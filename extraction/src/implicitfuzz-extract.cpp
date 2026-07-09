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
static constexpr unsigned kPointerTraceMaxDepth = 8;

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
                                          const DwarfStructIndex& dwarfIndex)
{
    AccessPathInfo info;
    info.primaryProvenance = "numeric_fallback";
    info.accessPathRecovery = "numeric_only";
    info.confidence = schemaConfidenceFromScore(0.9);

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
        << "\"base_object\":{\"object_scope\":\"synthetic\",\"value\":\"minimal_stub\"},"
        << "\"object_scope\":\"synthetic\","
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
            for (BasicBlock& bb : func)
            {
                for (Instruction& inst : bb)
                {
                    ++ordinal;
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
                    }

                    if (const LoadInst* li = SVFUtil::dyn_cast<LoadInst>(&inst))
                    {
                        const AccessPathInfo accessPath =
                            buildAccessPathInfo(li->getPointerOperand(), inst, dwarfIndex);
                        writeAccessFact(out, runMeta, func, inst, ordinal, "read",
                                        "direct_load", accessPath, binding);
                        ++factCount;
                    }
                    else if (const StoreInst* si = SVFUtil::dyn_cast<StoreInst>(&inst))
                    {
                        const AccessPathInfo accessPath =
                            buildAccessPathInfo(si->getPointerOperand(), inst, dwarfIndex);
                        writeAccessFact(out, runMeta, func, inst, ordinal, "write",
                                        "direct_store", accessPath, binding);
                        ++factCount;
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
