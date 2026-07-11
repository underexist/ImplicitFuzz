//===- implicitfuzz-extract.cpp -- Minimal SVF driver for ImplicitFuzz -----===//
//
// Based on SVF/svf-llvm/tools/Example/svf-ex.cpp
//
//===----------------------------------------------------------------------===//

#include "AE/Core/AbstractState.h"
#include "Graphs/ICFGNode.h"
#include "Graphs/SVFG.h"
#include "SVF-LLVM/LLVMModule.h"
#include "SVF-LLVM/LLVMUtil.h"
#include "SVF-LLVM/SVFIRBuilder.h"
#include "Util/CommandLine.h"
#include "Util/Options.h"
#include "WPA/Andersen.h"

#include "llvm/IR/Argument.h"
#include "llvm/IR/DataLayout.h"
#include "llvm/IR/DebugInfo.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/IR/Instruction.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/Operator.h"
#include "llvm/BinaryFormat/Dwarf.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <regex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

using namespace llvm;
using namespace std;
using namespace SVF;

static const Option<std::string> JsonlOut(
    "jsonl-out",
    "Path for JSONL facts output",
    "implicitfuzz-facts.jsonl");

static const Option<bool> EmitStructLayout(
    "emit-struct-layout",
    "Emit struct_layout_fact rows (offset->member) from DWARF; off by default",
    false);

static const Option<std::string> PrimitiveSummaryPath(
    "primitive-summary",
    "Path to primitive_summary.json",
    "primitive_summary.json");

struct NodeBinding
{
    std::optional<NodeID> svfNodeId;
    std::optional<NodeID> icfgNodeId;
};

struct GepStepRaw
{
    std::string sourceElementType;
    std::string resultType;
    std::vector<std::string> indicesRaw;
    std::string sourceLocationJson;
};

struct RunMetadata
{
    std::string kernelVersion = "userland";
    std::string llvmVersion = "21.1.8";
    std::string optLevel = "-O0";
    std::string bcUnit;
};

struct SchemaConfidence
{
    std::string label;
    double score;
};

struct AccessPathInfo
{
    std::string numericKind;
    std::vector<long long> numeric;
    std::vector<GepStepRaw> gepSteps;
    std::vector<std::string> symbolic;
    std::optional<std::string> symbolicPath;
    std::optional<std::string> fieldType;
    std::optional<std::string> fieldIrType;
    std::string primaryProvenance;
    std::string accessPathRecovery;
    SchemaConfidence confidence;
    std::string baseObjectScope = "synthetic";
    std::string baseObjectValue = "minimal_stub";
    bool hasAliasCandidate = false;
    std::string aliasObjectScope;
    std::string aliasObjectValue;
    std::vector<std::string> pointsToLabels;
};

static std::string stringArrayJson(const std::vector<std::string>& values);
static std::string intArrayJson(const std::vector<long long>& values);
static AccessPathInfo wholeObjectAccessPathInfo();

static std::string jsonEscape(const std::string& in)
{
    std::string out;
    out.reserve(in.size() + 16);
    for (char c : in)
    {
        switch (c)
        {
        case '"':
            out += "\\\"";
            break;
        case '\\':
            out += "\\\\";
            break;
        case '\n':
            out += "\\n";
            break;
        case '\r':
            out += "\\r";
            break;
        case '\t':
            out += "\\t";
            break;
        default:
            out += c;
            break;
        }
    }
    return out;
}

static std::string llvmValueToString(const Value* value)
{
    std::string repr;
    raw_string_ostream oss(repr);
    value->print(oss);
    oss.flush();
    return repr;
}

static std::string llvmTypeToString(const Type* type)
{
    std::string repr;
    raw_string_ostream oss(repr);
    type->print(oss);
    oss.flush();
    return repr;
}

static const Value* stripCastsOnly(const Value* value)
{
    const Value* cur = value;
    while (true)
    {
        const auto* op = SVFUtil::dyn_cast<Operator>(cur);
        if (!op)
            return cur;
        const unsigned opcode = op->getOpcode();
        if (opcode == Instruction::BitCast || opcode == Instruction::AddrSpaceCast)
        {
            cur = op->getOperand(0);
            continue;
        }
        return cur;
    }
}

static std::string sourceLocSchemaJson(const Instruction& inst)
{
    std::string spelling = "unknown:0";
    std::string expansion = "unknown:0";
    if (const DebugLoc& dl = inst.getDebugLoc())
    {
        std::string file = dl->getFilename().str();
        std::string dir = dl->getDirectory().str();
        if (!dir.empty() && !file.empty() && file.front() != '/')
            file = dir + "/" + file;
        spelling = file + ":" + std::to_string(dl.getLine());
        expansion = spelling;
    }

    std::ostringstream oss;
    oss << "{\"spelling\":\"" << jsonEscape(spelling) << "\","
        << "\"expansion\":\"" << jsonEscape(expansion) << "\","
        << "\"inlined_at\":[]}";
    return oss.str();
}

static std::string sourceExpansionKey(const Instruction& inst)
{
    if (const DebugLoc& dl = inst.getDebugLoc())
    {
        std::string file = dl->getFilename().str();
        std::string dir = dl->getDirectory().str();
        if (!dir.empty() && !file.empty() && file.front() != '/')
            file = dir + "/" + file;
        return file + ":" + std::to_string(dl.getLine());
    }
    return "unknown:0";
}

static SchemaConfidence schemaConfidenceFromScore(double score)
{
    if (score >= 0.95)
        return {"high", score};
    if (score >= 0.85)
        return {"medium_high", score};
    if (score >= 0.75)
        return {"medium", score};
    if (score >= 0.65)
        return {"medium_low", score};
    return {"low", score};
}

static std::string provenanceArrayJson(const std::vector<std::string>& items)
{
    return stringArrayJson(items);
}

static std::string nodeIdString(const std::optional<NodeID>& nodeId)
{
    if (!nodeId.has_value())
        return "unknown";
    return std::to_string(nodeId.value());
}

static std::string accessPathNumericString(const std::vector<long long>& numeric)
{
    if (numeric.empty())
        return "";
    return intArrayJson(numeric);
}

static std::string accessPathKey(const AccessPathInfo& info)
{
    if (info.symbolicPath.has_value() && !info.symbolicPath->empty())
        return info.symbolicPath.value();
    const std::string numeric = accessPathNumericString(info.numeric);
    if (!numeric.empty())
        return numeric;
    return "whole_object";
}

static std::string buildCrossOptKey(const std::string& expansion,
                                    const AccessPathInfo& info,
                                    const std::string& function,
                                    const std::string& semanticOp)
{
    return expansion + ":" + accessPathKey(info) + ":" + function + ":" + semanticOp;
}

static std::string callChainHash(const std::string& caller,
                                 const std::string& callee,
                                 const std::string& instructionId)
{
    return caller + "->" + callee + "@" + instructionId;
}

static std::string commonEnvelopeJson(const RunMetadata& runMeta,
                                       const std::string& function,
                                       const Instruction& inst,
                                       const std::string& primaryProvenance,
                                       const std::vector<std::string>& provenance,
                                       const SchemaConfidence& confidence)
{
    std::ostringstream oss;
    oss << "\"schema_version\":\"1.0.0\","
        << "\"kernel_version\":\"" << jsonEscape(runMeta.kernelVersion) << "\","
        << "\"llvm_version\":\"" << jsonEscape(runMeta.llvmVersion) << "\","
        << "\"opt_level\":\"" << jsonEscape(runMeta.optLevel) << "\","
        << "\"bc_unit\":\"" << jsonEscape(runMeta.bcUnit) << "\","
        << "\"function\":\"" << jsonEscape(function) << "\","
        << "\"source_location\":" << sourceLocSchemaJson(inst) << ","
        << "\"primary_provenance\":\"" << primaryProvenance << "\","
        << "\"provenance\":" << provenanceArrayJson(provenance) << ","
        << "\"confidence\":\"" << confidence.label << "\","
        << "\"confidence_score\":" << confidence.score;
    return oss.str();
}

// Envelope for facts with no instruction anchor (e.g. entry_fact read from a
// module-level const dispatch table). source_location is null (schema allows).
static std::string commonEnvelopeNoInstJson(const RunMetadata& runMeta,
                                            const std::string& function,
                                            const std::string& primaryProvenance,
                                            const std::vector<std::string>& provenance,
                                            const SchemaConfidence& confidence)
{
    std::ostringstream oss;
    oss << "\"schema_version\":\"1.0.0\","
        << "\"kernel_version\":\"" << jsonEscape(runMeta.kernelVersion) << "\","
        << "\"llvm_version\":\"" << jsonEscape(runMeta.llvmVersion) << "\","
        << "\"opt_level\":\"" << jsonEscape(runMeta.optLevel) << "\","
        << "\"bc_unit\":\"" << jsonEscape(runMeta.bcUnit) << "\","
        << "\"function\":\"" << jsonEscape(function) << "\","
        << "\"source_location\":null,"
        << "\"primary_provenance\":\"" << primaryProvenance << "\","
        << "\"provenance\":" << provenanceArrayJson(provenance) << ","
        << "\"confidence\":\"" << confidence.label << "\","
        << "\"confidence_score\":" << confidence.score;
    return oss.str();
}

static std::string sourceLocJson(const Instruction& inst)
{
    if (const DebugLoc& dl = inst.getDebugLoc())
    {
        std::string file = dl->getFilename().str();
        std::string dir = dl->getDirectory().str();
        if (!dir.empty() && !file.empty() && file.front() != '/')
            file = dir + "/" + file;

        std::ostringstream oss;
        oss << "{\"file\":\"" << jsonEscape(file) << "\",\"line\":" << dl.getLine()
            << ",\"column\":" << dl.getCol() << "}";
        return oss.str();
    }
    return "{\"file\":\"unknown\",\"line\":0,\"column\":0}";
}

static std::string nullableNodeIdJson(const std::optional<NodeID>& nodeId)
{
    if (!nodeId.has_value())
        return "null";
    return std::to_string(nodeId.value());
}

static NodeBinding resolveNodeBinding(const Instruction& inst, LLVMModuleSet* llvmMS)
{
    NodeBinding binding;

    if (llvmMS->hasICFGNode(&inst))
        binding.icfgNodeId = llvmMS->getICFGNode(&inst)->getId();

    if (llvmMS->hasValueNode(&inst))
        binding.svfNodeId = llvmMS->getValueNode(&inst);

    return binding;
}

static std::string nodeBindingJson(const NodeBinding& binding)
{
    std::ostringstream oss;
    oss << "\"svf_node_id\":" << nullableNodeIdJson(binding.svfNodeId) << ","
        << "\"icfg_node_id\":" << nullableNodeIdJson(binding.icfgNodeId);
    return oss.str();
}

static std::string instructionId(const Function& func, const Instruction& inst,
                                 uint64_t ordinal)
{
    std::ostringstream oss;
    oss << func.getName().str() << "#" << ordinal << "#"
        << reinterpret_cast<uintptr_t>(&inst);
    return oss.str();
}

static bool matchesNamePattern(const std::string& pattern, const std::string& callee)
{
    if (pattern.empty())
        return false;
    if (pattern.size() > 1 && pattern.back() == '*')
        return callee.rfind(pattern.substr(0, pattern.size() - 1), 0) == 0;
    if (pattern.size() > 1 && pattern.front() == '*')
    {
        const std::string suffix = pattern.substr(1);
        return callee.size() >= suffix.size() &&
               callee.compare(callee.size() - suffix.size(), std::string::npos,
                              suffix) == 0;
    }
    return pattern == callee;
}

struct PrimitiveSummaryEntry
{
    std::string namePattern;
    std::string semanticOp;
    std::string confidence;
    std::vector<std::string> effects;
};

static bool entryHasEffect(const PrimitiveSummaryEntry& entry, const char* effect)
{
    return std::find(entry.effects.begin(), entry.effects.end(), effect) !=
           entry.effects.end();
}

class PrimitiveSummaryIndex
{
public:
    bool loadFromJson(const std::string& path)
    {
        entries_.clear();
        auto bufferOrErr = llvm::MemoryBuffer::getFile(path);
        if (!bufferOrErr)
            return false;

        llvm::Expected<llvm::json::Value> parsed =
            llvm::json::parse(bufferOrErr.get()->getBuffer());
        if (!parsed)
            return false;

        const auto* arr = parsed->getAsArray();
        if (!arr)
            return false;

        for (const llvm::json::Value& item : *arr)
        {
            const auto* obj = item.getAsObject();
            if (!obj)
                continue;
            PrimitiveSummaryEntry entry;
            if (const auto pattern = obj->getString("name_pattern"))
                entry.namePattern = pattern->str();
            if (const auto semantic = obj->getString("semantic_op"))
                entry.semanticOp = semantic->str();
            if (const auto confidence = obj->getString("confidence"))
                entry.confidence = confidence->str();
            if (const auto* effects = obj->getArray("effects"))
            {
                for (const llvm::json::Value& effectVal : *effects)
                {
                    if (const auto effect = effectVal.getAsString())
                        entry.effects.push_back(effect->str());
                }
            }
            if (!entry.namePattern.empty() && !entry.semanticOp.empty())
                entries_.push_back(std::move(entry));
        }
        return !entries_.empty();
    }

    std::optional<PrimitiveSummaryEntry> match(const std::string& callee) const
    {
        for (const PrimitiveSummaryEntry& entry : entries_)
        {
            if (matchesNamePattern(entry.namePattern, callee))
                return entry;
        }
        return std::nullopt;
    }

    size_t size() const { return entries_.size(); }

private:
    std::vector<PrimitiveSummaryEntry> entries_;
};

static std::optional<std::pair<std::string, std::string>>
primitiveSemanticAccess(const PrimitiveSummaryEntry& entry)
{
    if (entry.semanticOp == "alloc")
        return std::make_pair("alloc", "call_alloc");
    if (entry.semanticOp == "free")
        return std::make_pair("free", "call_free");
    if (entry.semanticOp == "free_async")
        return std::make_pair("free_async", "call_free");
    if (entryHasEffect(entry, "copy_from_user"))
        return std::make_pair("read", "usercopy");
    if (entryHasEffect(entry, "copy_to_user"))
        return std::make_pair("write", "usercopy");
    if (entry.semanticOp == "retain")
        return std::make_pair("retain", "call_arg");
    if (entry.semanticOp == "release")
        return std::make_pair("release", "call_arg");
    if (entry.semanticOp == "read")
        return std::make_pair("read", "call_arg");
    return std::nullopt;
}

static AccessPathInfo primitiveAccessPathInfo(const std::string& confidenceLabel)
{
    AccessPathInfo info = wholeObjectAccessPathInfo();
    info.primaryProvenance = "summary";
    info.accessPathRecovery = "numeric_only";
    double score = 0.9;
    if (confidenceLabel == "high")
        score = 0.95;
    else if (confidenceLabel == "medium")
        score = 0.85;
    info.confidence = schemaConfidenceFromScore(score);
    return info;
}

struct SummaryDetail
{
    std::string matchKind;
    std::string wrapperSource;
    std::string primitiveSource;
};

static std::string summaryDetailJson(const SummaryDetail& detail)
{
    std::ostringstream oss;
    oss << "\"summary_detail\":{"
        << "\"match_kind\":\"" << jsonEscape(detail.matchKind) << "\","
        << "\"wrapper_source\":\"" << jsonEscape(detail.wrapperSource) << "\","
        << "\"primitive_source\":\"" << jsonEscape(detail.primitiveSource)
        << "\"}";
    return oss.str();
}

static AccessPathInfo wrapperPropagationAccessPathInfo()
{
    AccessPathInfo info = wholeObjectAccessPathInfo();
    info.primaryProvenance = "summary";
    info.accessPathRecovery = "numeric_only";
    info.confidence = schemaConfidenceFromScore(0.75);
    return info;
}

static const Value* peelCasts(const Value* value)
{
    return stripCastsOnly(value);
}

static const Value* traceLocalSlotValue(const Value* value)
{
    const Value* peeled = peelCasts(value);
    const auto* load = SVFUtil::dyn_cast<LoadInst>(peeled);
    if (!load)
        return peeled;

    const auto* alloca = SVFUtil::dyn_cast<AllocaInst>(load->getPointerOperand());
    if (!alloca)
        return peeled;

    for (const User* user : alloca->users())
    {
        const auto* store = SVFUtil::dyn_cast<StoreInst>(user);
        if (!store || store->getPointerOperand() != alloca)
            continue;
        return peelCasts(store->getValueOperand());
    }
    return peeled;
}

static bool isAllocPrimitiveCallee(const PrimitiveSummaryIndex& primitiveIndex,
                                   const std::string& calleeName)
{
    const std::optional<PrimitiveSummaryEntry> entry = primitiveIndex.match(calleeName);
    return entry.has_value() && entry->semanticOp == "alloc";
}

static bool isFreePrimitiveCallee(const PrimitiveSummaryIndex& primitiveIndex,
                                  const std::string& calleeName)
{
    const std::optional<PrimitiveSummaryEntry> entry = primitiveIndex.match(calleeName);
    return entry.has_value() && entry->semanticOp == "free";
}

struct AllocWrapperInfo
{
    std::string primitiveSource;
};

struct FreeWrapperInfo
{
    unsigned freedParamIndex = 0;
    std::string primitiveSource;
};

class WrapperSummaryIndex
{
public:
    void build(LLVMModuleSet* llvmMS, const PrimitiveSummaryIndex& primitiveIndex)
    {
        allocWrappers_.clear();
        freeWrappers_.clear();

        bool changed = true;
        while (changed)
        {
            changed = false;
            for (Module& mod : llvmMS->getLLVMModules())
            {
                for (Function& func : mod)
                {
                    if (func.isDeclaration())
                        continue;

                    const std::string funcName = func.getName().str();
                    if (!allocWrappers_.count(funcName))
                    {
                        if (const std::optional<std::string> primitiveSource =
                                detectAllocWrapper(func, primitiveIndex))
                        {
                            allocWrappers_[funcName] = {*primitiveSource};
                            changed = true;
                        }
                    }

                    if (!freeWrappers_.count(funcName))
                    {
                        if (const std::optional<FreeWrapperInfo> info =
                                detectFreeWrapper(func, primitiveIndex))
                        {
                            freeWrappers_[funcName] = *info;
                            changed = true;
                        }
                    }
                }
            }
        }
    }

    bool isAllocWrapper(const std::string& name) const
    {
        return allocWrappers_.count(name) > 0;
    }

    std::optional<AllocWrapperInfo> allocWrapper(const std::string& name) const
    {
        const auto it = allocWrappers_.find(name);
        if (it == allocWrappers_.end())
            return std::nullopt;
        return it->second;
    }

    std::optional<FreeWrapperInfo> freeWrapper(const std::string& name) const
    {
        const auto it = freeWrappers_.find(name);
        if (it == freeWrappers_.end())
            return std::nullopt;
        return it->second;
    }

    size_t allocCount() const { return allocWrappers_.size(); }
    size_t freeCount() const { return freeWrappers_.size(); }

private:
    std::optional<std::string>
    resolveAllocCallSource(const CallBase& call,
                           const PrimitiveSummaryIndex& primitiveIndex) const
    {
        const Function* callee = call.getCalledFunction();
        if (!callee)
            return std::nullopt;

        const std::string calleeName = callee->getName().str();
        if (isAllocPrimitiveCallee(primitiveIndex, calleeName))
            return calleeName;

        const auto it = allocWrappers_.find(calleeName);
        if (it != allocWrappers_.end())
            return it->second.primitiveSource;

        return std::nullopt;
    }

    std::optional<std::string>
    detectAllocWrapper(const Function& func,
                       const PrimitiveSummaryIndex& primitiveIndex) const
    {
        for (const BasicBlock& bb : func)
        {
            for (const Instruction& inst : bb)
            {
                const auto* ret = SVFUtil::dyn_cast<ReturnInst>(&inst);
                if (!ret || !ret->getReturnValue())
                    continue;

                const Value* traced = traceLocalSlotValue(ret->getReturnValue());
                const auto* call = SVFUtil::dyn_cast<CallBase>(traced);
                if (!call)
                    continue;

                if (const std::optional<std::string> primitiveSource =
                        resolveAllocCallSource(*call, primitiveIndex))
                    return primitiveSource;
            }
        }
        return std::nullopt;
    }

    std::optional<FreeWrapperInfo>
    detectFreeWrapper(const Function& func,
                      const PrimitiveSummaryIndex& primitiveIndex) const
    {
        for (const BasicBlock& bb : func)
        {
            for (const Instruction& inst : bb)
            {
                const auto* call = SVFUtil::dyn_cast<CallBase>(&inst);
                if (!call)
                    continue;

                const Function* callee = call->getCalledFunction();
                if (!callee)
                    continue;

                const std::string calleeName = callee->getName().str();
                std::optional<std::string> primitiveSource;
                if (isFreePrimitiveCallee(primitiveIndex, calleeName))
                    primitiveSource = calleeName;
                else
                {
                    const auto it = freeWrappers_.find(calleeName);
                    if (it != freeWrappers_.end())
                        primitiveSource = it->second.primitiveSource;
                }
                if (!primitiveSource.has_value())
                    continue;

                for (unsigned argIndex = 0; argIndex < call->arg_size(); ++argIndex)
                {
                    const Value* traced =
                        traceLocalSlotValue(call->getArgOperand(argIndex));
                    const auto* formal = SVFUtil::dyn_cast<Argument>(traced);
                    if (!formal || formal->getParent() != &func)
                        continue;

                    return FreeWrapperInfo{formal->getArgNo(), *primitiveSource};
                }
            }
        }
        return std::nullopt;
    }

    std::unordered_map<std::string, AllocWrapperInfo> allocWrappers_;
    std::unordered_map<std::string, FreeWrapperInfo> freeWrappers_;
};

static constexpr unsigned kPointerTraceMaxDepth = 8;

struct BaseObjectInfo
{
    std::string scope = "synthetic";
    std::string value = "minimal_stub";
};

static std::string addrBasedObjectId(const Function& func, const Value* value)
{
    std::ostringstream oss;
    oss << func.getName().str() << "#addr" << reinterpret_cast<uintptr_t>(value);
    return oss.str();
}

// Unlike traceLocalSlotValue (used for wrapper detection, where picking any
// one store is an acceptable approximation), base object identity requires
// an unambiguous slot: if a local variable's stack slot is written by more
// than one store (e.g. a loop induction variable such as `head = head->next`),
// the "value stored here" question does not have a single answer, so we
// deliberately refuse to hop through it rather than pick an arbitrary one.
static const Value* traceUniqueStoreSlot(const LoadInst* load)
{
    const auto* alloca = SVFUtil::dyn_cast<AllocaInst>(load->getPointerOperand());
    if (!alloca)
        return load;

    const StoreInst* uniqueStore = nullptr;
    for (const User* user : alloca->users())
    {
        const auto* store = SVFUtil::dyn_cast<StoreInst>(user);
        if (!store || store->getPointerOperand() != alloca)
            continue;
        if (uniqueStore)
            return load;
        uniqueStore = store;
    }
    if (!uniqueStore)
        return load;
    return stripCastsOnly(uniqueStore->getValueOperand());
}

// Direct (tier-1) base object resolution: walk the pointer chain through GEPs
// and unambiguous local-slot hops to a global, formal parameter, stack
// allocation, or an allocation-primitive/wrapper call site. Pointers that
// bottom out in something else (e.g. a value loaded from another object's
// field, or a slot with more than one store) are left for a future SVF
// points-to based tier and reported as synthetic here.
// Classifies a *root* value (already peeled of casts/GEPs/local-slot hops)
// into a base_object. Shared by tier 1 (walks to this root itself) and tier
// 2 (walks to this root via SVF's points-to target, reverse-mapped back to
// an LLVM Value) so that the same underlying object always gets the same
// label regardless of which tier found it -- otherwise accesses reached via
// a direct root walk vs. via points-to on an indirect pointer would produce
// different base_object strings for the same real object and silently fail
// to group together downstream.
static BaseObjectInfo classifyRootValue(const Value* cur,
                                        const PrimitiveSummaryIndex& primitiveIndex,
                                        const WrapperSummaryIndex& wrapperIndex)
{
    cur = stripCastsOnly(cur);

    if (const auto* gv = SVFUtil::dyn_cast<GlobalVariable>(cur))
        return {"global", gv->getName().str()};

    if (const auto* arg = SVFUtil::dyn_cast<Argument>(cur))
    {
        std::ostringstream oss;
        oss << arg->getParent()->getName().str() << ":" << arg->getArgNo();
        return {"formal_param", oss.str()};
    }

    if (const auto* alloca = SVFUtil::dyn_cast<AllocaInst>(cur))
        return {"allocation_site", addrBasedObjectId(*alloca->getFunction(), alloca)};

    if (const auto* call = SVFUtil::dyn_cast<CallBase>(cur))
    {
        const Function* callee = call->getCalledFunction();
        if (callee)
        {
            const std::string calleeName = callee->getName().str();
            if (isAllocPrimitiveCallee(primitiveIndex, calleeName) ||
                wrapperIndex.isAllocWrapper(calleeName))
            {
                return {"allocation_site",
                        addrBasedObjectId(*call->getFunction(), call)};
            }
        }
    }

    return {"synthetic", "minimal_stub"};
}

static BaseObjectInfo resolveBaseObjectDirect(const Value* ptr,
                                              const PrimitiveSummaryIndex& primitiveIndex,
                                              const WrapperSummaryIndex& wrapperIndex)
{
    const Value* cur = ptr;
    for (unsigned hop = 0; hop < kPointerTraceMaxDepth; ++hop)
    {
        cur = stripCastsOnly(cur);
        if (const auto* gep = SVFUtil::dyn_cast<GEPOperator>(cur))
        {
            cur = gep->getPointerOperand();
            continue;
        }
        if (const auto* load = SVFUtil::dyn_cast<LoadInst>(cur))
        {
            const Value* traced = traceUniqueStoreSlot(load);
            if (traced != load)
            {
                cur = traced;
                continue;
            }
        }
        break;
    }
    return classifyRootValue(cur, primitiveIndex, wrapperIndex);
}

// Tier-2 base object resolution via SVF Andersen points-to. Only invoked when
// tier 1 leaves a pointer as "formal_param" or "synthetic" -- per the v3
// object_id design (docs/静态抽取层技术路线_v3.md §5.6), those two scopes are
// relational evidence only and must be refined toward a concrete
// allocation_site/global before they can support an identity claim.
// GepObjVar points-to targets are reduced to their BaseObjVar: field
// sensitivity does not matter for "same object" identity.
static const BaseObjVar* reduceToBaseObjVar(const SVFVar* gnode)
{
    if (const auto* baseObj = SVFUtil::dyn_cast<BaseObjVar>(gnode))
        return baseObj;
    if (const auto* gepObj = SVFUtil::dyn_cast<GepObjVar>(gnode))
        return gepObj->getBaseObj();
    return nullptr;
}

// Prefer reverse-mapping the SVF object back to its LLVM Value and running it
// through the same classifyRootValue tier-1 uses, so a heap/stack/global
// object reached via points-to gets *the same* base_object string as when
// tier 1 reaches it directly -- otherwise the two tiers would silently
// disagree on the label for the same real object. Falls back to an
// SVF-node-based label (still stable within this run, just not shared with
// tier 1's addressing scheme) only when the reverse mapping is unavailable.
static BaseObjectInfo svfObjectToBaseObject(const BaseObjVar& obj, LLVMModuleSet* llvmMS,
                                            const PrimitiveSummaryIndex& primitiveIndex,
                                            const WrapperSummaryIndex& wrapperIndex)
{
    // LLVMModuleSet::getLLVMValue() is only assert-guarded (compiled out in
    // this Release build) against a missing reverse mapping; hasLLVMValue()
    // is the real, non-assert check and must be used first -- not every SVF
    // object (e.g. synthetic/extapi-modeled objects) has a backing LLVM
    // Value, and calling getLLVMValue() on one is undefined behavior
    // (segfaulted on io_uring.c, which has SVF object kinds the smaller
    // golden TUs never exercised).
    if (llvmMS->hasLLVMValue(&obj))
    {
        if (const Value* llvmValue = llvmMS->getLLVMValue(&obj))
        {
            const BaseObjectInfo classified =
                classifyRootValue(llvmValue, primitiveIndex, wrapperIndex);
            if (classified.scope != "synthetic")
                return classified;
        }
    }

    if (obj.isGlobalObj())
        return {"global", obj.getValueName()};
    const ICFGNode* icfgNode = obj.getICFGNode();
    const std::string locSuffix = icfgNode ? "@" + icfgNode->getSourceLoc() : "";
    return {"allocation_site", "svf_obj" + std::to_string(obj.getId()) + locSuffix};
}

// Self-describing "scope:value" label for alias_fact.points_to_set entries
// (points_to_set is schema'd as a plain string array, no nested object).
static std::string svfObjectLabel(const BaseObjVar& obj, LLVMModuleSet* llvmMS,
                                  const PrimitiveSummaryIndex& primitiveIndex,
                                  const WrapperSummaryIndex& wrapperIndex)
{
    const BaseObjectInfo info =
        svfObjectToBaseObject(obj, llvmMS, primitiveIndex, wrapperIndex);
    return info.scope + ":" + info.value;
}

struct PointsToResolution
{
    bool hasSingleConcreteObject = false;
    BaseObjectInfo singleObject;
    std::vector<std::string> concreteObjectLabels;
};

static PointsToResolution resolveBaseObjectViaPointsTo(const Value* ptr, Andersen* ander,
                                                        SVFIR* pag, LLVMModuleSet* llvmMS,
                                                        const PrimitiveSummaryIndex& primitiveIndex,
                                                        const WrapperSummaryIndex& wrapperIndex)
{
    PointsToResolution result;
    if (!ander || !pag || !llvmMS || !llvmMS->hasValueNode(ptr))
        return result;

    const NodeID nodeId = llvmMS->getValueNode(ptr);
    const PointsTo& pts = ander->getPts(nodeId);

    std::vector<const BaseObjVar*> concreteObjects;
    for (NodeID objId : pts)
    {
        const SVFVar* gnode = pag->getGNode(objId);
        const BaseObjVar* baseObj = reduceToBaseObjVar(gnode);
        if (!baseObj)
            continue;
        if (baseObj->isBlackHoleObj())
        {
            // A blackhole target means "could be anything": not a useful
            // identity signal, so treat the whole points-to set as unresolved.
            return PointsToResolution{};
        }
        concreteObjects.push_back(baseObj);
    }

    if (concreteObjects.empty())
        return result;

    for (const BaseObjVar* obj : concreteObjects)
        result.concreteObjectLabels.push_back(
            svfObjectLabel(*obj, llvmMS, primitiveIndex, wrapperIndex));

    if (concreteObjects.size() == 1)
    {
        result.hasSingleConcreteObject = true;
        result.singleObject = svfObjectToBaseObject(*concreteObjects.front(), llvmMS,
                                                     primitiveIndex, wrapperIndex);
    }
    return result;
}

class DwarfStructIndex
{
public:
    void indexModule(const Module& mod)
    {
        byName_.clear();
        DebugInfoFinder finder;
        finder.processModule(mod);
        for (DIType* diType : finder.types())
        {
            const auto* composite = SVFUtil::dyn_cast<DICompositeType>(diType);
            if (!composite)
                continue;
            const unsigned tag = composite->getTag();
            if (tag != dwarf::DW_TAG_structure_type && tag != dwarf::DW_TAG_class_type)
                continue;
            const std::string name = composite->getName().str();
            if (!name.empty())
                byName_[name] = composite;
        }
    }

    const DICompositeType* lookup(const std::string& name) const
    {
        const auto it = byName_.find(name);
        if (it == byName_.end())
            return nullptr;
        return it->second;
    }

    const std::unordered_map<std::string, const DICompositeType*>& structs() const
    {
        return byName_;
    }

private:
    std::unordered_map<std::string, const DICompositeType*> byName_;
};

static std::string structTypeBaseName(const StructType* structTy)
{
    if (!structTy || !structTy->hasName())
        return "";
    std::string name = structTy->getName().str();
    if (name.rfind("struct.", 0) == 0)
        return name.substr(7);
    if (name.rfind("class.", 0) == 0)
        return name.substr(6);
    return name;
}

struct DwarfMemberInfo
{
    std::string name;
    std::string sourceType;
};

static std::string renderDwarfTypeName(const DIType* type);

static std::string normalizeQualified(const std::string& qualifier,
                                      const std::string& base)
{
    if (base.empty() || base == "unknown")
        return base;
    return qualifier + " " + base;
}

static std::string renderCompositeTypeName(const DICompositeType* composite)
{
    const std::string rawName = composite->getName().str();
    if (rawName.empty())
        return "unknown";

    const unsigned tag = composite->getTag();
    if (tag == dwarf::DW_TAG_structure_type)
        return "struct " + rawName;
    if (tag == dwarf::DW_TAG_union_type)
        return "union " + rawName;
    if (tag == dwarf::DW_TAG_class_type)
        return "class " + rawName;
    return rawName;
}

static std::string renderDwarfTypeName(const DIType* type)
{
    if (!type)
        return "unknown";

    if (const auto* basic = SVFUtil::dyn_cast<DIBasicType>(type))
    {
        const std::string name = basic->getName().str();
        return name.empty() ? "unknown" : name;
    }

    if (const auto* composite = SVFUtil::dyn_cast<DICompositeType>(type))
        return renderCompositeTypeName(composite);

    if (const auto* derived = SVFUtil::dyn_cast<DIDerivedType>(type))
    {
        const DIType* base = derived->getBaseType();
        const std::string baseName = renderDwarfTypeName(base);
        switch (derived->getTag())
        {
        case dwarf::DW_TAG_pointer_type:
            if (baseName == "unknown")
                return "void *";
            return baseName + " *";
        case dwarf::DW_TAG_const_type:
            return normalizeQualified("const", baseName);
        case dwarf::DW_TAG_volatile_type:
            return normalizeQualified("volatile", baseName);
        case dwarf::DW_TAG_restrict_type:
            return normalizeQualified("restrict", baseName);
        case dwarf::DW_TAG_typedef:
            // B2 uses a stable expanded view to avoid typedef ambiguity.
            return baseName;
        case dwarf::DW_TAG_member:
            return baseName;
        default:
            if (!baseName.empty() && baseName != "unknown")
                return baseName;
            break;
        }

        const std::string name = derived->getName().str();
        if (!name.empty())
            return name;
    }

    const std::string fallbackName = type->getName().str();
    return fallbackName.empty() ? "unknown" : fallbackName;
}

static std::optional<DwarfMemberInfo> dwarfMemberInfo(const DICompositeType* composite,
                                                      unsigned fieldIdx)
{
    if (!composite)
        return std::nullopt;

    unsigned memberIdx = 0;
    for (Metadata* element : composite->getElements())
    {
        const auto* derived = SVFUtil::dyn_cast<DIDerivedType>(element);
        if (!derived || derived->getTag() != dwarf::DW_TAG_member)
            continue;
        if (memberIdx == fieldIdx)
        {
            DwarfMemberInfo info;
            info.name = derived->getName().str();
            info.sourceType = renderDwarfTypeName(derived->getBaseType());
            return info;
        }
        ++memberIdx;
    }
    return std::nullopt;
}

static std::string renderDwarfTypeNameKeepTypedef(const DIType* type)
{
    if (!type)
        return "unknown";

    if (const auto* derived = SVFUtil::dyn_cast<DIDerivedType>(type))
    {
        if (derived->getTag() == dwarf::DW_TAG_typedef)
        {
            const std::string typedefName = derived->getName().str();
            if (!typedefName.empty())
                return typedefName;
        }
    }
    return renderDwarfTypeName(type);
}

static const DIType* stripDwarfQualifiers(const DIType* type)
{
    const DIType* cur = type;
    while (const auto* derived = dyn_cast_or_null<DIDerivedType>(cur))
    {
        switch (derived->getTag())
        {
        case dwarf::DW_TAG_const_type:
        case dwarf::DW_TAG_volatile_type:
        case dwarf::DW_TAG_restrict_type:
        case dwarf::DW_TAG_typedef:
            cur = derived->getBaseType();
            continue;
        default:
            return cur;
        }
    }
    return cur;
}

static const DICompositeType* pointeeCompositeType(const DIType* type)
{
    const DIType* cur = stripDwarfQualifiers(type);
    const auto* ptr = dyn_cast_or_null<DIDerivedType>(cur);
    if (!ptr || ptr->getTag() != dwarf::DW_TAG_pointer_type)
        return nullptr;
    const DIType* pointee = stripDwarfQualifiers(ptr->getBaseType());
    return dyn_cast_or_null<DICompositeType>(pointee);
}

static std::optional<std::string> sourceLineText(const Instruction& inst)
{
    const DebugLoc& dl = inst.getDebugLoc();
    if (!dl)
        return std::nullopt;

    std::string file = dl->getFilename().str();
    std::string dir = dl->getDirectory().str();
    if (!dir.empty() && !file.empty() && file.front() != '/')
        file = dir + "/" + file;
    if (file.empty())
        return std::nullopt;

    static std::unordered_map<std::string, std::vector<std::string>> fileCache;
    auto it = fileCache.find(file);
    if (it == fileCache.end())
    {
        std::ifstream in(file);
        if (!in.is_open())
            return std::nullopt;
        std::vector<std::string> lines;
        std::string line;
        while (std::getline(in, line))
            lines.push_back(std::move(line));
        it = fileCache.emplace(file, std::move(lines)).first;
    }

    const unsigned lineNo = dl.getLine();
    if (lineNo == 0 || lineNo > it->second.size())
        return std::nullopt;
    return it->second[lineNo - 1];
}

static std::optional<std::string> extractArrowBaseVar(const Instruction& inst)
{
    const std::optional<std::string> lineText = sourceLineText(inst);
    if (!lineText.has_value())
        return std::nullopt;
    static const std::regex kArrowBase(R"(([A-Za-z_][A-Za-z0-9_]*)\s*->)");
    std::smatch m;
    if (!std::regex_search(*lineText, m, kArrowBase) || m.size() < 2)
        return std::nullopt;
    return m[1].str();
}

static std::optional<std::pair<const DICompositeType*, std::string>>
resolveCompositeFromLocalContext(const Instruction& inst)
{
    const std::optional<std::string> baseVar = extractArrowBaseVar(inst);
    if (!baseVar.has_value())
        return std::nullopt;

    const DILocalScope* scope = nullptr;
    if (const DebugLoc& dl = inst.getDebugLoc())
        scope = dyn_cast_or_null<DILocalScope>(dl->getScope());
    if (!scope)
        return std::nullopt;
    const DISubprogram* subprogram = scope->getSubprogram();
    if (!subprogram)
        return std::nullopt;

    const DICompositeType* uniqueComposite = nullptr;
    for (Metadata* md : subprogram->getRetainedNodes())
    {
        const auto* local = dyn_cast_or_null<DILocalVariable>(md);
        if (!local || local->getName() != *baseVar)
            continue;
        const DICompositeType* composite = pointeeCompositeType(local->getType());
        if (!composite)
            continue;
        if (!uniqueComposite)
            uniqueComposite = composite;
        else if (uniqueComposite != composite)
            return std::nullopt;
    }
    if (!uniqueComposite)
        return std::nullopt;
    return std::make_pair(uniqueComposite, *baseVar);
}

static std::optional<DwarfMemberInfo> dwarfMemberInfoByByteOffset(
    const DICompositeType* composite, uint64_t byteOffset)
{
    if (!composite)
        return std::nullopt;

    std::optional<DwarfMemberInfo> unique;
    for (Metadata* element : composite->getElements())
    {
        const auto* member = dyn_cast_or_null<DIDerivedType>(element);
        if (!member || member->getTag() != dwarf::DW_TAG_member)
            continue;
        if ((member->getOffsetInBits() / 8) != byteOffset)
            continue;

        DwarfMemberInfo info;
        info.name = member->getName().str();
        info.sourceType = renderDwarfTypeNameKeepTypedef(member->getBaseType());
        if (!unique.has_value())
        {
            unique = std::move(info);
        }
        else
        {
            return std::nullopt;
        }
    }
    return unique;
}

static bool resolveByteOffsetGepSymbolic(const GEPOperator* gep,
                                         const Instruction& inst,
                                         const std::vector<long long>& numeric,
                                         std::vector<std::string>& symbolic,
                                         std::string& rootStructName,
                                         std::string& leafFieldType,
                                         std::string& leafFieldIrType)
{
    if (!gep || numeric.size() != 1 || numeric.front() < 0)
        return false;
    const Type* srcElemTy = gep->getSourceElementType();
    if (!srcElemTy || !srcElemTy->isIntegerTy(8))
        return false;

    const std::optional<std::pair<const DICompositeType*, std::string>> context =
        resolveCompositeFromLocalContext(inst);
    if (!context.has_value())
        return false;
    const DICompositeType* composite = context->first;

    const uint64_t byteOffset = static_cast<uint64_t>(numeric.front());
    const std::optional<DwarfMemberInfo> member =
        dwarfMemberInfoByByteOffset(composite, byteOffset);
    if (!member.has_value() || member->name.empty())
        return false;

    rootStructName = composite->getName().str();
    if (rootStructName.empty())
        return false;
    symbolic.clear();
    symbolic.push_back(member->name);
    leafFieldType = member->sourceType;
    leafFieldIrType = "i8_byte_offset";
    return true;
}

static std::string simplifiedFieldTypeName(const Type* type)
{
    if (!type)
        return "unknown";
    if (type->isPointerTy())
        return "ptr";
    if (type->isIntegerTy(1))
        return "bool";
    if (type->isIntegerTy())
        return "int";
    if (type->isFloatingPointTy())
        return "float";
    return llvmTypeToString(type);
}

static void collectGepSteps(const Value* ptr, std::vector<GepStepRaw>& steps);
static const Value* resolvePointerToGep(const Value* value, unsigned depth,
                                        std::unordered_set<const Value*>& visiting);

static bool mergeUniqueCandidate(const Value*& candidate, const Value* next)
{
    if (!next)
        return true;
    if (!candidate)
    {
        candidate = next;
        return true;
    }
    return candidate == next;
}

static const Value* resolvePhiToGep(const PHINode* phi, unsigned depth,
                                    std::unordered_set<const Value*>& visiting)
{
    const Value* candidate = nullptr;
    for (const Value* incoming : phi->incoming_values())
    {
        const Value* resolved = resolvePointerToGep(incoming, depth - 1, visiting);
        if (!mergeUniqueCandidate(candidate, resolved))
            return nullptr;
    }
    return candidate;
}

static const Value* resolveSelectToGep(const SelectInst* sel, unsigned depth,
                                       std::unordered_set<const Value*>& visiting)
{
    const Value* candidate = nullptr;
    const Value* t = resolvePointerToGep(sel->getTrueValue(), depth - 1, visiting);
    if (!mergeUniqueCandidate(candidate, t))
        return nullptr;
    const Value* f = resolvePointerToGep(sel->getFalseValue(), depth - 1, visiting);
    if (!mergeUniqueCandidate(candidate, f))
        return nullptr;
    return candidate;
}

static const Value* resolveStackSlotToGep(const AllocaInst* slot, unsigned depth,
                                          std::unordered_set<const Value*>& visiting)
{
    const Value* candidate = nullptr;
    for (const User* user : slot->users())
    {
        const auto* store = SVFUtil::dyn_cast<StoreInst>(user);
        if (!store)
            continue;
        if (stripCastsOnly(store->getPointerOperand()) != slot)
            continue;
        const Value* resolved =
            resolvePointerToGep(store->getValueOperand(), depth - 1, visiting);
        if (!mergeUniqueCandidate(candidate, resolved))
            return nullptr;
    }
    return candidate;
}

static const Value* resolvePointerToGep(const Value* value, unsigned depth,
                                        std::unordered_set<const Value*>& visiting)
{
    if (!value || depth == 0)
        return nullptr;

    const Value* stripped = stripCastsOnly(value);
    if (const auto* gep = SVFUtil::dyn_cast<GEPOperator>(stripped))
        return gep;

    if (!stripped->getType()->isPointerTy())
        return nullptr;
    if (!visiting.insert(stripped).second)
        return nullptr;

    const Value* resolved = nullptr;
    if (const auto* phi = SVFUtil::dyn_cast<PHINode>(stripped))
    {
        resolved = resolvePhiToGep(phi, depth, visiting);
    }
    else if (const auto* sel = SVFUtil::dyn_cast<SelectInst>(stripped))
    {
        resolved = resolveSelectToGep(sel, depth, visiting);
    }
    else if (const auto* load = SVFUtil::dyn_cast<LoadInst>(stripped))
    {
        const Value* addr = stripCastsOnly(load->getPointerOperand());
        if (const auto* slot = SVFUtil::dyn_cast<AllocaInst>(addr))
            resolved = resolveStackSlotToGep(slot, depth, visiting);
    }

    visiting.erase(stripped);
    return resolved;
}

static bool resolveStructGepSymbolic(const GEPOperator* gep,
                                   const DwarfStructIndex& dwarfIndex,
                                   std::vector<std::string>& symbolic,
                                   std::string& rootStructName,
                                   std::string& leafFieldType,
                                   std::string& leafFieldIrType)
{
    if (!gep)
        return false;

    Type* currentTy = gep->getSourceElementType();
    symbolic.clear();
    rootStructName.clear();
    leafFieldType.clear();
    leafFieldIrType.clear();

    const unsigned numIndices = gep->getNumIndices();
    for (unsigned i = 0; i < numIndices; ++i)
    {
        const Value* idxVal = gep->getOperand(i + 1);
        const auto* idxConst = SVFUtil::dyn_cast<ConstantInt>(idxVal);
        if (!idxConst)
        {
            // Kernel -O2 often keeps struct-typed GEP with a variable array index
            // followed by a constant field index (e.g. hbs[i].list).
            if (const auto* structTy = SVFUtil::dyn_cast<StructType>(currentTy))
            {
                if (rootStructName.empty())
                    rootStructName = structTypeBaseName(structTy);
                continue;
            }
            if (const auto* arrayTy = SVFUtil::dyn_cast<ArrayType>(currentTy))
            {
                currentTy = arrayTy->getElementType();
                continue;
            }
            if (const auto* vectorTy = SVFUtil::dyn_cast<VectorType>(currentTy))
            {
                currentTy = vectorTy->getElementType();
                continue;
            }
            return false;
        }

        const uint64_t index = idxConst->getZExtValue();
        if (const auto* structTy = SVFUtil::dyn_cast<StructType>(currentTy))
        {
            if (rootStructName.empty())
                rootStructName = structTypeBaseName(structTy);

            const bool hasMore = i + 1 < numIndices;
            if (index == 0 && hasMore)
                continue;

            if (index >= structTy->getNumElements())
                return false;

            const std::optional<DwarfMemberInfo> memberInfo =
                dwarfMemberInfo(dwarfIndex.lookup(rootStructName),
                                static_cast<unsigned>(index));
            if (!memberInfo.has_value() || memberInfo->name.empty())
                return false;

            symbolic.push_back(memberInfo->name);
            currentTy = structTy->getElementType(static_cast<unsigned>(index));
            leafFieldType = memberInfo->sourceType;
            leafFieldIrType = simplifiedFieldTypeName(currentTy);
        }
        else if (const auto* arrayTy = SVFUtil::dyn_cast<ArrayType>(currentTy))
        {
            currentTy = arrayTy->getElementType();
        }
        else if (const auto* vectorTy = SVFUtil::dyn_cast<VectorType>(currentTy))
        {
            currentTy = vectorTy->getElementType();
        }
        else
        {
            return false;
        }
    }

    return !symbolic.empty() && !rootStructName.empty();
}

static AccessPathInfo buildAccessPathInfo(const Value* ptr,
                                          const Instruction& inst,
                                          const DwarfStructIndex& dwarfIndex,
                                          const PrimitiveSummaryIndex& primitiveIndex,
                                          const WrapperSummaryIndex& wrapperIndex,
                                          Andersen* ander,
                                          SVFIR* pag,
                                          LLVMModuleSet* llvmMS)
{
    AccessPathInfo info;
    info.primaryProvenance = "numeric_fallback";
    info.accessPathRecovery = "numeric_only";
    info.confidence = schemaConfidenceFromScore(0.9);

    const BaseObjectInfo baseObject =
        resolveBaseObjectDirect(ptr, primitiveIndex, wrapperIndex);
    info.baseObjectScope = baseObject.scope;
    info.baseObjectValue = baseObject.value;

    // Tier 2: formal_param/synthetic are relational evidence only (v3 §5.6);
    // try to refine them toward a concrete allocation_site/global via SVF
    // Andersen points-to before falling back to leaving them as-is.
    if (baseObject.scope == "formal_param" || baseObject.scope == "synthetic")
    {
        const PointsToResolution ptsResult = resolveBaseObjectViaPointsTo(
            ptr, ander, pag, llvmMS, primitiveIndex, wrapperIndex);
        if (ptsResult.hasSingleConcreteObject)
        {
            info.baseObjectScope = ptsResult.singleObject.scope;
            info.baseObjectValue = ptsResult.singleObject.value;
        }
        else if (ptsResult.concreteObjectLabels.size() > 1)
        {
            info.hasAliasCandidate = true;
            info.aliasObjectScope = baseObject.scope;
            info.aliasObjectValue = baseObject.value;
            info.pointsToLabels = ptsResult.concreteObjectLabels;
        }
    }

    const Value* resolvedPtr = [&]() -> const Value* {
        std::unordered_set<const Value*> visiting;
        const Value* traced =
            resolvePointerToGep(ptr, kPointerTraceMaxDepth, visiting);
        return traced ? traced : ptr;
    }();

    const Value* stripped = stripCastsOnly(resolvedPtr);
    const GEPOperator* gep = SVFUtil::dyn_cast<GEPOperator>(stripped);

    collectGepSteps(resolvedPtr, info.gepSteps);

    if (!gep)
    {
        info.numericKind = "whole_object";
        return info;
    }

    bool allConst = true;
    for (auto it = gep->idx_begin(); it != gep->idx_end(); ++it)
    {
        const Value* idx = *it;
        if (const ConstantInt* ci = SVFUtil::dyn_cast<ConstantInt>(idx))
            info.numeric.push_back(ci->getSExtValue());
        else
            allConst = false;
    }
    info.numericKind = allConst ? "gep_offsets" : "unknown";

    std::vector<std::string> symbolic;
    std::string rootStructName;
    std::string leafFieldType;
    std::string leafFieldIrType;
    if (resolveStructGepSymbolic(gep, dwarfIndex, symbolic, rootStructName,
                                 leafFieldType, leafFieldIrType))
    {
        info.symbolic = std::move(symbolic);
        info.symbolicPath = rootStructName + "." + info.symbolic.back();
        info.fieldType = leafFieldType;
        info.fieldIrType = leafFieldIrType;
        info.primaryProvenance = "dwarf";
        info.accessPathRecovery = "dwarf";
        info.confidence = schemaConfidenceFromScore(0.95);
        if (!info.numeric.empty())
            info.numericKind = "gep_offsets";
    }
    else if (resolveByteOffsetGepSymbolic(gep, inst, info.numeric, symbolic,
                                          rootStructName, leafFieldType,
                                          leafFieldIrType))
    {
        info.symbolic = std::move(symbolic);
        info.symbolicPath = rootStructName + "." + info.symbolic.back();
        info.fieldType = leafFieldType;
        info.fieldIrType = leafFieldIrType;
        info.primaryProvenance = "dwarf";
        info.accessPathRecovery = "dwarf";
        info.confidence = schemaConfidenceFromScore(0.95);
    }

    return info;
}

static void collectGepSteps(const Value* ptr, std::vector<GepStepRaw>& steps)
{
    const Value* stripped = stripCastsOnly(ptr);
    const GEPOperator* gep = SVFUtil::dyn_cast<GEPOperator>(stripped);
    if (!gep)
        return;

    collectGepSteps(gep->getPointerOperand(), steps);

    GepStepRaw step;
    step.sourceElementType = llvmTypeToString(gep->getSourceElementType());
    step.resultType = llvmTypeToString(gep->getType());
    for (auto it = gep->idx_begin(); it != gep->idx_end(); ++it)
        step.indicesRaw.push_back(llvmValueToString(*it));

    if (const Instruction* gepInst = SVFUtil::dyn_cast<Instruction>(gep))
        step.sourceLocationJson = sourceLocJson(*gepInst);
    else
        step.sourceLocationJson = "{\"file\":\"unknown\",\"line\":0,\"column\":0}";

    steps.push_back(std::move(step));
}

static std::string gepRawJson(const std::vector<GepStepRaw>& steps)
{
    if (steps.empty())
        return "null";

    std::ostringstream oss;
    oss << "{\"steps\":[";
    for (size_t i = 0; i < steps.size(); ++i)
    {
        if (i)
            oss << ",";
        const GepStepRaw& step = steps[i];
        oss << "{\"source_element_type\":\"" << jsonEscape(step.sourceElementType)
            << "\",\"result_type\":\"" << jsonEscape(step.resultType)
            << "\",\"indices_raw\":[";
        for (size_t j = 0; j < step.indicesRaw.size(); ++j)
        {
            if (j)
                oss << ",";
            oss << "\"" << jsonEscape(step.indicesRaw[j]) << "\"";
        }
        oss << "],\"source_location\":" << step.sourceLocationJson << "}";
    }
    oss << "]}";
    return oss.str();
}

static std::string nullableStringJson(const std::optional<std::string>& value)
{
    if (!value.has_value())
        return "null";
    return "\"" + jsonEscape(value.value()) + "\"";
}

static std::string stringArrayJson(const std::vector<std::string>& values)
{
    std::ostringstream oss;
    oss << "[";
    for (size_t i = 0; i < values.size(); ++i)
    {
        if (i)
            oss << ",";
        oss << "\"" << jsonEscape(values[i]) << "\"";
    }
    oss << "]";
    return oss.str();
}

static std::string intArrayJson(const std::vector<long long>& values)
{
    std::ostringstream oss;
    oss << "[";
    for (size_t i = 0; i < values.size(); ++i)
    {
        if (i)
            oss << ",";
        oss << values[i];
    }
    oss << "]";
    return oss.str();
}

static std::string accessPathDetailJson(const AccessPathInfo& info)
{
    std::ostringstream oss;
    oss << "{\"numeric_kind\":\"" << info.numericKind << "\","
        << "\"indices\":" << intArrayJson(info.numeric) << ","
        << "\"numeric\":" << intArrayJson(info.numeric) << ","
        << "\"symbolic\":" << stringArrayJson(info.symbolic) << ","
        << "\"symbolic_path\":" << nullableStringJson(info.symbolicPath) << ","
        << "\"field_type\":" << nullableStringJson(info.fieldType) << ","
        << "\"field_ir_type\":" << nullableStringJson(info.fieldIrType) << ","
        << "\"gep_raw\":" << gepRawJson(info.gepSteps) << "}";
    return oss.str();
}

static AccessPathInfo wholeObjectAccessPathInfo()
{
    AccessPathInfo info;
    info.numericKind = "whole_object";
    info.primaryProvenance = "numeric_fallback";
    info.accessPathRecovery = "numeric_only";
    info.confidence = schemaConfidenceFromScore(0.9);
    return info;
}

static void writeCallFact(std::ofstream& out, const RunMetadata& runMeta,
                          const Function& func, const Instruction& inst,
                          uint64_t ordinal, const std::string& callee,
                          const NodeBinding& binding)
{
    const std::string funcName = func.getName().str();
    const std::string instId = instructionId(func, inst, ordinal);
    const SchemaConfidence confidence = schemaConfidenceFromScore(0.95);
    const std::vector<std::string> provenance = {"svf"};

    out << "{"
        << "\"fact_type\":\"call_fact\","
        << commonEnvelopeJson(runMeta, funcName, inst, "svf", provenance, confidence) << ","
        << "\"instruction_id\":\"" << jsonEscape(instId) << "\","
        << "\"svf_node_id\":\"" << jsonEscape(nodeIdString(binding.svfNodeId)) << "\","
        << "\"icfg_node_id\":" << nullableNodeIdJson(binding.icfgNodeId) << ","
        << "\"caller\":\"" << jsonEscape(funcName) << "\","
        << "\"callee\":\"" << jsonEscape(callee) << "\","
        << "\"call_site\":\"" << jsonEscape(instId) << "\","
        << "\"is_indirect\":false,"
        << "\"resolution_method\":\"direct\","
        << "\"call_chain_hash\":\"" << jsonEscape(callChainHash(funcName, callee, instId))
        << "\"}\n";
}

// Indirect calls (through a function pointer -- op-table dispatch, callbacks,
// etc.) previously produced no call_fact at all, since the direct-call path
// only fires when getCalledFunction() is non-null. This is a real gap for
// kernel code: io_uring's core dispatch (io_issue_sqe -> io_op_defs[...].issue)
// is exactly this pattern. Resolution is attempted via SVF's own indirect
// call graph (Andersen points-to on the called operand, already computed for
// the whole TU) rather than re-deriving it here, so "resolved" here means
// exactly what SVF's own getNumOfResolvedIndCallEdge() counts.
static bool writeIndirectCallFact(std::ofstream& out, const RunMetadata& runMeta,
                                  const Function& func, const Instruction& inst,
                                  uint64_t ordinal, const NodeBinding& binding,
                                  CallGraph* callgraph, LLVMModuleSet* llvmMS)
{
    const std::string funcName = func.getName().str();
    const std::string instId = instructionId(func, inst, ordinal);
    const std::vector<std::string> provenance = {"svf"};

    std::vector<std::string> candidates;
    const ICFGNode* icfgNode =
        llvmMS->hasICFGNode(&inst) ? llvmMS->getICFGNode(&inst) : nullptr;
    const CallICFGNode* callNode =
        icfgNode ? SVFUtil::dyn_cast<CallICFGNode>(icfgNode) : nullptr;
    if (callNode && callgraph->hasIndCSCallees(callNode))
    {
        for (const FunObjVar* candidate : callgraph->getIndCSCallees(callNode))
        {
            // See svfObjectToBaseObject: getLLVMValue() is only
            // assert-guarded, so hasLLVMValue() must be checked first.
            if (llvmMS->hasLLVMValue(candidate))
            {
                if (const Value* llvmValue = llvmMS->getLLVMValue(candidate))
                {
                    if (const auto* fn = SVFUtil::dyn_cast<Function>(llvmValue))
                        candidates.push_back(fn->getName().str());
                }
            }
        }
    }
    std::sort(candidates.begin(), candidates.end());

    // Schema requires a non-null "callee" string even when unresolved;
    // callee_candidates (possibly empty) carries the full resolved set.
    const std::string calleeField =
        candidates.empty() ? "<indirect_unresolved>" : candidates.front();
    const SchemaConfidence confidence =
        candidates.empty() ? schemaConfidenceFromScore(0.5)
                           : schemaConfidenceFromScore(0.75);

    out << "{"
        << "\"fact_type\":\"call_fact\","
        << commonEnvelopeJson(runMeta, funcName, inst, "svf", provenance, confidence) << ","
        << "\"instruction_id\":\"" << jsonEscape(instId) << "\","
        << "\"svf_node_id\":\"" << jsonEscape(nodeIdString(binding.svfNodeId)) << "\","
        << "\"icfg_node_id\":" << nullableNodeIdJson(binding.icfgNodeId) << ","
        << "\"caller\":\"" << jsonEscape(funcName) << "\","
        << "\"callee\":\"" << jsonEscape(calleeField) << "\","
        << "\"call_site\":\"" << jsonEscape(instId) << "\","
        << "\"is_indirect\":true,"
        << "\"resolution_method\":\"pta\","
        << "\"callee_candidates\":" << stringArrayJson(candidates) << ","
        << "\"call_chain_hash\":\"" << jsonEscape(callChainHash(funcName, calleeField, instId))
        << "\"}\n";
    return !candidates.empty();
}

static void writeAccessFact(std::ofstream& out, const RunMetadata& runMeta,
                            const Function& func, const Instruction& inst,
                            uint64_t ordinal, const std::string& semanticOp,
                            const std::string& accessKind,
                            const AccessPathInfo& accessPath,
                            const NodeBinding& binding,
                            const std::optional<SummaryDetail>& summaryDetail =
                                std::nullopt)
{
    const std::string funcName = func.getName().str();
    const std::string instId = instructionId(func, inst, ordinal);
    const std::string expansion = sourceExpansionKey(inst);
    const std::string crossOptKey =
        buildCrossOptKey(expansion, accessPath, funcName, semanticOp);
    const std::vector<std::string> provenance =
        accessPath.primaryProvenance == "dwarf"
            ? std::vector<std::string>{"svf", "dwarf"}
            : accessPath.primaryProvenance == "summary"
                  ? std::vector<std::string>{"svf", "summary"}
                  : std::vector<std::string>{"svf", "numeric_fallback"};

    out << "{"
        << "\"fact_type\":\"access_fact\","
        << commonEnvelopeJson(runMeta, funcName, inst, accessPath.primaryProvenance,
                              provenance, accessPath.confidence) << ","
        << "\"instruction_id\":\"" << jsonEscape(instId) << "\","
        << "\"svf_node_id\":\"" << jsonEscape(nodeIdString(binding.svfNodeId)) << "\","
        << "\"icfg_node_id\":" << nullableNodeIdJson(binding.icfgNodeId) << ","
        << "\"semantic_op\":\"" << semanticOp << "\","
        << "\"access_kind\":\"" << accessKind << "\","
        << "\"base_object\":{\"object_scope\":\"" << jsonEscape(accessPath.baseObjectScope)
        << "\",\"value\":\"" << jsonEscape(accessPath.baseObjectValue) << "\"},"
        << "\"object_scope\":\"" << jsonEscape(accessPath.baseObjectScope) << "\","
        << "\"numeric_kind\":\"" << accessPath.numericKind << "\","
        << "\"access_path_symbolic\":" << nullableStringJson(accessPath.symbolicPath) << ","
        << "\"access_path_numeric\":"
        << (accessPath.numeric.empty()
                ? "null"
                : "\"" + jsonEscape(accessPathNumericString(accessPath.numeric)) + "\"")
        << ","
        << "\"field_type\":" << nullableStringJson(accessPath.fieldType) << ","
        << "\"access_path_recovery\":\"" << accessPath.accessPathRecovery << "\","
        << "\"cross_opt_key\":\"" << jsonEscape(crossOptKey) << "\","
        << "\"access_path\":" << accessPathDetailJson(accessPath);
    if (summaryDetail.has_value())
        out << "," << summaryDetailJson(summaryDetail.value());
    out << "}\n";
}

// Emitted alongside an access_fact when SVF Andersen points-to found more
// than one concrete candidate object for a pointer whose direct (tier-1)
// base_object stayed formal_param/synthetic. Records the full points-to set
// as evidence for later identity closure (v3 §5.7: closure is computed by
// the evidence store, not fully resolved here).
static void writeAliasFact(std::ofstream& out, const RunMetadata& runMeta,
                           const Function& func, const Instruction& inst,
                           const std::string& objectScope, const std::string& objectValue,
                           const std::vector<std::string>& pointsToSet,
                           const std::string& relationEvidence)
{
    const std::string funcName = func.getName().str();
    const SchemaConfidence confidence = schemaConfidenceFromScore(0.75);
    const std::vector<std::string> provenance = {"svf"};

    out << "{"
        << "\"fact_type\":\"alias_fact\","
        << commonEnvelopeJson(runMeta, funcName, inst, "svf", provenance, confidence) << ","
        << "\"object_id\":{\"object_scope\":\"" << jsonEscape(objectScope)
        << "\",\"value\":\"" << jsonEscape(objectValue) << "\"},"
        << "\"points_to_set\":" << stringArrayJson(pointsToSet) << ","
        << "\"relation_evidence\":\"" << jsonEscape(relationEvidence) << "\","
        << "\"pta_kind\":\"andersen\""
        << "}\n";
}

static void writeEntryFact(std::ofstream& out, const RunMetadata& runMeta,
                           const std::string& tableName, const std::string& entrySymbol,
                           const std::string& role, long long index,
                           const std::string& opcodeName)
{
    const SchemaConfidence confidence = schemaConfidenceFromScore(0.95);
    const std::vector<std::string> provenance = {"dwarf"};
    out << "{"
        << "\"fact_type\":\"entry_fact\","
        << commonEnvelopeNoInstJson(runMeta, tableName, "dwarf", provenance, confidence) << ","
        << "\"entry_kind\":\"op_dispatch\","
        << "\"entry_symbol\":\"" << jsonEscape(entrySymbol) << "\","
        << "\"associated_syscall\":"
        << (opcodeName.empty() ? "null" : "\"" + jsonEscape(opcodeName) + "\"") << ","
        << "\"dispatch_table\":\"" << jsonEscape(tableName) << "\","
        << "\"dispatch_index\":" << index << ","
        << "\"dispatch_role\":\"" << jsonEscape(role) << "\""
        << "}\n";
}

// Recursively emit struct_layout_fact rows for one composite's members at
// absolute byte offsets. Anonymous (nameless) struct/union members -- e.g.
// io_uring's ____cacheline_aligned_in_smp anon groups -- are recursed into so
// their leaf fields (like io_ring_ctx.nr_user_files) surface with absolute
// offsets rather than being skipped. Recursion terminates: only by-value
// nested composites recurse (pointers are DIDerivedType, not composites).
static void emitStructLayoutMembers(std::ofstream& out, const RunMetadata& runMeta,
                                    const SchemaConfidence& confidence,
                                    const std::vector<std::string>& provenance,
                                    const std::string& structName,
                                    const DICompositeType* composite,
                                    uint64_t baseByteOffset,
                                    uint64_t& structLayoutFactCount)
{
    for (Metadata* element : composite->getElements())
    {
        const auto* member = dyn_cast_or_null<DIDerivedType>(element);
        if (!member || member->getTag() != dwarf::DW_TAG_member)
            continue;
        const uint64_t byteOffset = baseByteOffset + member->getOffsetInBits() / 8;
        const std::string memberName = member->getName().str();
        const DIType* baseType = member->getBaseType();
        const DIType* stripped = stripDwarfQualifiers(baseType);
        const auto* nested = dyn_cast_or_null<DICompositeType>(stripped);
        const bool nestedAggregate =
            nested && (nested->getTag() == dwarf::DW_TAG_structure_type ||
                       nested->getTag() == dwarf::DW_TAG_union_type);
        if (memberName.empty())
        {
            if (nestedAggregate)
                emitStructLayoutMembers(out, runMeta, confidence, provenance, structName,
                                        nested, byteOffset, structLayoutFactCount);
            continue;
        }
        const std::string memberType = renderDwarfTypeNameKeepTypedef(baseType);
        out << "{"
            << "\"fact_type\":\"struct_layout_fact\","
            << commonEnvelopeNoInstJson(runMeta, structName, "dwarf", provenance,
                                        confidence)
            << ","
            << "\"struct_name\":\"" << jsonEscape(structName) << "\","
            << "\"member_name\":\"" << jsonEscape(memberName) << "\","
            << "\"byte_offset\":" << byteOffset << ","
            << "\"member_type\":\"" << jsonEscape(memberType) << "\""
            << "}\n";
        ++structLayoutFactCount;
    }
}

// Emit struct_layout_fact rows (offset->member) from DWARF for every indexed
// struct. Read-only DWARF traversal over the DebugInfoFinder-built index --
// deliberately off the SVF points-to / getLLVMValue() hot path. Gated by the
// opt-in -emit-struct-layout flag so default runs (regression) are unchanged.
static void writeStructLayoutFacts(std::ofstream& out, const RunMetadata& runMeta,
                                   const DwarfStructIndex& dwarfIndex,
                                   uint64_t& structLayoutFactCount)
{
    const SchemaConfidence confidence = schemaConfidenceFromScore(0.98);
    const std::vector<std::string> provenance = {"dwarf"};
    for (const auto& entry : dwarfIndex.structs())
    {
        const std::string& structName = entry.first;
        const DICompositeType* composite = entry.second;
        if (!composite || structName.empty())
            continue;
        emitStructLayoutMembers(out, runMeta, confidence, provenance, structName,
                                composite, 0, structLayoutFactCount);
    }
}

static const Function* asFunctionTarget(const Constant* c)
{
    if (!c)
        return nullptr;
    return SVFUtil::dyn_cast<Function>(c->stripPointerCasts());
}

// The array element type of a const dispatch table is often an anonymous/
// literal LLVM struct (no `%struct.name`), so the struct name -- and thus the
// DWARF member names for role labeling -- can't come from the LLVM type. Get
// the element struct's DICompositeType from the global variable's own DWARF
// debug info instead: global var type is `[N x elem]`, whose base type is the
// element struct composite.
static const DICompositeType* dispatchElementComposite(const GlobalVariable& gv)
{
    SmallVector<DIGlobalVariableExpression*, 1> gves;
    gv.getDebugInfo(gves);
    for (DIGlobalVariableExpression* gve : gves)
    {
        const DIGlobalVariable* var = gve->getVariable();
        if (!var)
            continue;
        const auto* arrTy = dyn_cast_or_null<DICompositeType>(var->getType());
        if (!arrTy || arrTy->getTag() != dwarf::DW_TAG_array_type)
            continue;
        // Array element base type is often const-qualified (`const struct T[]`);
        // strip const/volatile before casting to the element composite.
        const DIType* base = stripDwarfQualifiers(arrTy->getBaseType());
        if (const auto* st = dyn_cast_or_null<DICompositeType>(base))
            return st;
    }
    return nullptr;
}

// Read constant function-pointer dispatch tables (e.g. io_uring's io_op_defs)
// and emit one entry_fact per (element index = opcode, function-pointer field)
// with a real Function target. This resolves the opcode->handler mapping that
// is otherwise an indirect call unresolvable per-TU (io_op_defs is `external`
// in io_uring.c; here in opdef.c its constant initializer is fully readable).
// Field roles (issue/prep/cleanup/...) come from DWARF by byte offset, so
// struct padding/bitfields do not misalign them.
static void scanDispatchTables(std::ofstream& out, const RunMetadata& runMeta,
                               Module& mod, const DwarfStructIndex& dwarfIndex,
                               uint64_t& entryFactCount)
{
    const DataLayout& dl = mod.getDataLayout();
    for (GlobalVariable& gv : mod.globals())
    {
        if (!gv.isConstant() || !gv.hasInitializer())
            continue;
        const auto* arr = SVFUtil::dyn_cast<ConstantArray>(gv.getInitializer());
        if (!arr)
            continue;
        auto* elemStruct = SVFUtil::dyn_cast<StructType>(arr->getType()->getElementType());
        if (!elemStruct)
            continue;
        const std::string tableName = gv.getName().str();
        const DICompositeType* composite = dispatchElementComposite(gv);
        if (!composite)
            composite = dwarfIndex.lookup(structTypeBaseName(elemStruct));
        const StructLayout* layout = dl.getStructLayout(elemStruct);

        for (unsigned e = 0; e < arr->getNumOperands(); ++e)
        {
            const auto* elem = SVFUtil::dyn_cast<ConstantStruct>(arr->getOperand(e));
            if (!elem)
                continue;
            for (unsigned f = 0; f < elem->getNumOperands(); ++f)
            {
                const Function* fn = asFunctionTarget(elem->getOperand(f));
                if (!fn)
                    continue;
                const uint64_t byteOffset = layout->getElementOffset(f);
                std::string role;
                if (composite)
                {
                    const std::optional<DwarfMemberInfo> member =
                        dwarfMemberInfoByByteOffset(composite, byteOffset);
                    if (member.has_value())
                        role = member->name;
                }
                if (role.empty())
                    role = "byte" + std::to_string(byteOffset);
                writeEntryFact(out, runMeta, tableName, fn->getName().str(), role,
                               (long long)e, /*opcodeName=*/"");
                ++entryFactCount;
            }
        }
    }
}

// Branch-local backward slice: collect LoadInsts feeding a branch/switch
// condition, within a bounded depth over data operands, stopping at PHIs to
// stay local (a full cross-block slice is future work per v3 §5.8). The loads
// tie the branch to the object fields whose state gates it.
static void collectConditionLoads(const Value* cond, unsigned depth,
                                  std::unordered_set<const Value*>& seen,
                                  std::vector<const LoadInst*>& loads)
{
    if (depth == 0 || !cond || !seen.insert(cond).second)
        return;
    if (const auto* li = SVFUtil::dyn_cast<LoadInst>(cond))
    {
        loads.push_back(li);
        return;
    }
    const auto* op = SVFUtil::dyn_cast<Instruction>(cond);
    if (!op || SVFUtil::isa<PHINode>(op))
        return;
    for (const Use& u : op->operands())
        collectConditionLoads(u.get(), depth - 1, seen, loads);
}

static std::vector<std::string> successorLabels(const Instruction& term)
{
    std::vector<std::string> labels;
    if (!term.isTerminator())
        return labels;
    for (unsigned i = 0; i < term.getNumSuccessors(); ++i)
    {
        const BasicBlock* succ = term.getSuccessor(i);
        std::string name = succ && succ->hasName() ? succ->getName().str() : "";
        labels.push_back("succ" + std::to_string(i) + (name.empty() ? "" : ":" + name));
    }
    return labels;
}

static std::string truncated(const std::string& s, size_t n)
{
    return s.size() <= n ? s : s.substr(0, n) + "...";
}

static void writeBranchFact(std::ofstream& out, const RunMetadata& runMeta,
                            const Function& func, const Instruction& inst, uint64_t ordinal,
                            const Value* cond,
                            const std::unordered_map<const Instruction*, std::string>& instIdMap)
{
    const std::string funcName = func.getName().str();
    const std::string branchId = instructionId(func, inst, ordinal);

    std::unordered_set<const Value*> seen;
    std::vector<const LoadInst*> loads;
    collectConditionLoads(cond, kPointerTraceMaxDepth, seen, loads);

    std::vector<std::string> relatedLoads;
    std::string firstLoadLoc;
    for (const LoadInst* li : loads)
    {
        const auto it = instIdMap.find(li);
        if (it != instIdMap.end())
            relatedLoads.push_back(it->second);
        if (firstLoadLoc.empty())
            firstLoadLoc = sourceExpansionKey(*li);
    }
    const std::string branchLoc = sourceExpansionKey(inst);
    const std::string sliceRange =
        (firstLoadLoc.empty() ? branchLoc : firstLoadLoc) + ".." + branchLoc;

    const SchemaConfidence confidence = schemaConfidenceFromScore(0.9);
    const std::vector<std::string> provenance = {"svf"};
    out << "{"
        << "\"fact_type\":\"branch_fact\","
        << commonEnvelopeJson(runMeta, funcName, inst, "svf", provenance, confidence) << ","
        << "\"branch_instruction_id\":\"" << jsonEscape(branchId) << "\","
        << "\"condition_value\":\"" << jsonEscape(truncated(llvmValueToString(cond), 200)) << "\","
        << "\"control_deps\":" << stringArrayJson(successorLabels(inst)) << ","
        << "\"related_loads\":" << stringArrayJson(relatedLoads) << ","
        << "\"slice_range\":\"" << jsonEscape(sliceRange) << "\""
        << "}\n";
}

int main(int argc, char** argv)
{
    std::vector<std::string> moduleNameVec =
        OptionBase::parseOptions(argc, argv, "ImplicitFuzz SVF extraction driver",
                                 "[options] <input-bitcode...>");

    if (moduleNameVec.empty())
    {
        std::cerr << "No input bitcode files specified.\n";
        return 1;
    }

    std::cout << "[implicitfuzz-extract] loading " << moduleNameVec.size()
              << " module(s)\n";

    LLVMModuleSet::preProcessBCs(moduleNameVec);
    LLVMModuleSet::buildSVFModule(moduleNameVec);

    SVFIRBuilder builder;
    SVFIR* pag = builder.build();

    Andersen* ander = AndersenWaveDiff::createAndersenWaveDiff(pag);
    CallGraph* callgraph = ander->getCallGraph();
    ICFG* icfg = pag->getICFG();
    (void)icfg;

    VFG* vfg = new VFG(callgraph);

    SVFGBuilder svfBuilder;
    SVFG* svfg = svfBuilder.buildFullSVFG(ander);

    const std::string jsonlPath = JsonlOut();
    std::ofstream out(jsonlPath);
    if (!out.is_open())
    {
        std::cerr << "Failed to open JSONL output: " << jsonlPath << "\n";
        return 1;
    }

    RunMetadata runMeta;
    runMeta.bcUnit = std::filesystem::path(moduleNameVec.front()).filename().string();

    LLVMModuleSet* llvmMS = LLVMModuleSet::getLLVMModuleSet();
    DwarfStructIndex dwarfIndex;
    for (Module& mod : llvmMS->getLLVMModules())
        dwarfIndex.indexModule(mod);

    PrimitiveSummaryIndex primitiveIndex;
    const std::string summaryPath = PrimitiveSummaryPath();
    if (!primitiveIndex.loadFromJson(summaryPath))
    {
        std::cerr << "[implicitfuzz-extract] warning: failed to load primitive "
                     "summary: "
                  << summaryPath << "\n";
    }
    else
    {
        std::cout << "[implicitfuzz-extract] loaded primitive summary: "
                  << primitiveIndex.size() << " entries from " << summaryPath
                  << "\n";
    }

    uint64_t factCount = 0;
    uint64_t primitiveAllocFacts = 0;
    uint64_t primitiveFreeFacts = 0;
    uint64_t primitiveFreeAsyncFacts = 0;
    uint64_t primitiveRetainFacts = 0;
    uint64_t primitiveReleaseFacts = 0;
    uint64_t primitiveUsercopyFacts = 0;
    uint64_t primitiveOtherFacts = 0;
    uint64_t wrapperAllocFacts = 0;
    uint64_t wrapperFreeFacts = 0;
    uint64_t aliasFactCount = 0;
    uint64_t indirectCallFacts = 0;
    uint64_t indirectCallResolvedFacts = 0;
    uint64_t entryFactCount = 0;
    uint64_t branchFactCount = 0;

    for (Module& mod : llvmMS->getLLVMModules())
        scanDispatchTables(out, runMeta, mod, dwarfIndex, entryFactCount);
    factCount += entryFactCount;

    uint64_t structLayoutFactCount = 0;
    if (EmitStructLayout())
        writeStructLayoutFacts(out, runMeta, dwarfIndex, structLayoutFactCount);
    factCount += structLayoutFactCount;

    WrapperSummaryIndex wrapperIndex;
    if (primitiveIndex.size() > 0)
    {
        wrapperIndex.build(llvmMS, primitiveIndex);
        std::cout << "[implicitfuzz-extract] wrapper summary: alloc_wrappers="
                  << wrapperIndex.allocCount()
                  << " free_wrappers=" << wrapperIndex.freeCount() << "\n";
    }

    for (Module& mod : llvmMS->getLLVMModules())
    {
        for (Function& func : mod)
        {
            if (func.isDeclaration())
                continue;
            uint64_t ordinal = 0;
            // Instruction -> instruction_id, so branch_fact.related_loads can
            // reference the same ids that load access_facts carry. Loads feed
            // conditions from dominating positions, so they are mapped before
            // their branch is reached in this single forward pass.
            std::unordered_map<const Instruction*, std::string> instIdMap;
            for (BasicBlock& bb : func)
            {
                for (Instruction& inst : bb)
                {
                    ++ordinal;
                    instIdMap[&inst] = instructionId(func, inst, ordinal);
                    const NodeBinding binding = resolveNodeBinding(inst, llvmMS);

                    if (const CallBase* cb = SVFUtil::dyn_cast<CallBase>(&inst))
                    {
                        const Function* callee = cb->getCalledFunction();
                        if (callee)
                        {
                            const std::string calleeName = callee->getName().str();
                            writeCallFact(out, runMeta, func, inst, ordinal, calleeName,
                                          binding);
                            ++factCount;

                            const std::optional<PrimitiveSummaryEntry> primitive =
                                primitiveIndex.match(calleeName);
                            if (primitive.has_value())
                            {
                                const std::optional<std::pair<std::string, std::string>>
                                    semanticAccess =
                                        primitiveSemanticAccess(primitive.value());
                                if (semanticAccess.has_value())
                                {
                                    writeAccessFact(
                                        out, runMeta, func, inst, ordinal,
                                        semanticAccess->first, semanticAccess->second,
                                        primitiveAccessPathInfo(primitive->confidence),
                                        binding);
                                    ++factCount;
                                    if (semanticAccess->first == "alloc")
                                        ++primitiveAllocFacts;
                                    else if (semanticAccess->first == "free")
                                        ++primitiveFreeFacts;
                                    else if (semanticAccess->first == "free_async")
                                        ++primitiveFreeAsyncFacts;
                                    else if (semanticAccess->first == "retain")
                                        ++primitiveRetainFacts;
                                    else if (semanticAccess->first == "release")
                                        ++primitiveReleaseFacts;
                                    else if (semanticAccess->second == "usercopy")
                                        ++primitiveUsercopyFacts;
                                    else
                                        ++primitiveOtherFacts;
                                }
                            }
                            else if (const std::optional<AllocWrapperInfo> allocWrapper =
                                         wrapperIndex.allocWrapper(calleeName))
                            {
                                const SummaryDetail detail{
                                    "wrapper_propagation", calleeName,
                                    allocWrapper->primitiveSource};
                                writeAccessFact(out, runMeta, func, inst, ordinal, "alloc",
                                                "call_alloc",
                                                wrapperPropagationAccessPathInfo(), binding,
                                                detail);
                                ++factCount;
                                ++wrapperAllocFacts;
                            }
                            else if (const std::optional<FreeWrapperInfo> freeWrapper =
                                         wrapperIndex.freeWrapper(calleeName))
                            {
                                (void)freeWrapper;
                                const SummaryDetail detail{
                                    "wrapper_propagation", calleeName,
                                    freeWrapper->primitiveSource};
                                writeAccessFact(out, runMeta, func, inst, ordinal, "free",
                                                "call_free",
                                                wrapperPropagationAccessPathInfo(), binding,
                                                detail);
                                ++factCount;
                                ++wrapperFreeFacts;
                            }
                        }
                        else if (!cb->isInlineAsm())
                        {
                            const bool resolved = writeIndirectCallFact(
                                out, runMeta, func, inst, ordinal, binding, callgraph, llvmMS);
                            ++factCount;
                            ++indirectCallFacts;
                            if (resolved)
                                ++indirectCallResolvedFacts;
                        }
                    }

                    if (const LoadInst* li = SVFUtil::dyn_cast<LoadInst>(&inst))
                    {
                        const AccessPathInfo accessPath =
                            buildAccessPathInfo(li->getPointerOperand(), inst, dwarfIndex,
                                     primitiveIndex, wrapperIndex, ander, pag, llvmMS);
                        writeAccessFact(out, runMeta, func, inst, ordinal, "read",
                                        "direct_load", accessPath, binding);
                        ++factCount;
                        if (accessPath.hasAliasCandidate)
                        {
                            writeAliasFact(out, runMeta, func, inst, accessPath.aliasObjectScope,
                                          accessPath.aliasObjectValue, accessPath.pointsToLabels,
                                          instructionId(func, inst, ordinal));
                            ++factCount;
                            ++aliasFactCount;
                        }
                    }
                    else if (const StoreInst* si = SVFUtil::dyn_cast<StoreInst>(&inst))
                    {
                        const AccessPathInfo accessPath =
                            buildAccessPathInfo(si->getPointerOperand(), inst, dwarfIndex,
                                     primitiveIndex, wrapperIndex, ander, pag, llvmMS);
                        writeAccessFact(out, runMeta, func, inst, ordinal, "write",
                                        "direct_store", accessPath, binding);
                        ++factCount;
                        if (accessPath.hasAliasCandidate)
                        {
                            writeAliasFact(out, runMeta, func, inst, accessPath.aliasObjectScope,
                                          accessPath.aliasObjectValue, accessPath.pointsToLabels,
                                          instructionId(func, inst, ordinal));
                            ++factCount;
                            ++aliasFactCount;
                        }
                    }
                    else if (const BranchInst* br = SVFUtil::dyn_cast<BranchInst>(&inst))
                    {
                        if (br->isConditional())
                        {
                            writeBranchFact(out, runMeta, func, inst, ordinal,
                                            br->getCondition(), instIdMap);
                            ++factCount;
                            ++branchFactCount;
                        }
                    }
                    else if (const SwitchInst* sw = SVFUtil::dyn_cast<SwitchInst>(&inst))
                    {
                        writeBranchFact(out, runMeta, func, inst, ordinal,
                                        sw->getCondition(), instIdMap);
                        ++factCount;
                        ++branchFactCount;
                    }
                }
            }
        }
    }
    out.close();

    std::cout << "[implicitfuzz-extract] SVFIR vars: " << pag->getSVFVarNum()
              << ", SVFG nodes: " << svfg->getTotalNodeNum()
              << ", resolved indirect call edges: "
              << callgraph->getNumOfResolvedIndCallEdge()
              << "\n";
    std::cout << "[implicitfuzz-extract] wrote " << factCount << " facts to "
              << jsonlPath << "\n";
    std::cout << "[implicitfuzz-extract] primitive summary hits: alloc="
              << primitiveAllocFacts << " free=" << primitiveFreeFacts
              << " free_async=" << primitiveFreeAsyncFacts
              << " retain=" << primitiveRetainFacts
              << " release=" << primitiveReleaseFacts
              << " usercopy=" << primitiveUsercopyFacts
              << " other=" << primitiveOtherFacts << "\n";
    std::cout << "[implicitfuzz-extract] wrapper propagation hits: alloc="
              << wrapperAllocFacts << " free=" << wrapperFreeFacts << "\n";
    std::cout << "[implicitfuzz-extract] alias_fact (andersen points-to, "
                 "multi-object) hits: "
              << aliasFactCount << "\n";
    std::cout << "[implicitfuzz-extract] indirect call_fact: " << indirectCallFacts
              << " (resolved to >=1 candidate: " << indirectCallResolvedFacts << ")\n";
    std::cout << "[implicitfuzz-extract] entry_fact (const dispatch table) hits: "
              << entryFactCount << "\n";
    std::cout << "[implicitfuzz-extract] struct_layout_fact (opt-in): "
              << structLayoutFactCount << "\n";
    std::cout << "[implicitfuzz-extract] branch_fact (conditional/switch) hits: "
              << branchFactCount << "\n";

    delete vfg;
    AndersenWaveDiff::releaseAndersenWaveDiff();
    SVFIR::releaseSVFIR();

    LLVMModuleSet::getLLVMModuleSet()->dumpModulesToFile(".svf.bc");
    SVF::LLVMModuleSet::releaseLLVMModuleSet();
#if LLVM_VERSION_MAJOR < 21
    llvm::llvm_shutdown();
#endif

    std::cout << "[implicitfuzz-extract] done\n";
    return 0;
}
