# LLM 受限判读 可行性 spike — findings

**Date:** 2026-07-11
**Branch:** `phase2b-evidence-identity`
**目的:** 在建 LLM pilot 前,量一下"LLM 在代码切片+事实账约束下推状态门控谓词"的真实可行性,据此定 LLM 角色、以及抽取器符号化升级是否必须前置。
**判读员:** Claude Opus 4.8(feasibility 阶段自任 judge,不接外部 API)。
**样本:** 旗舰 READ_FIXED fixed-file 门控(最难的 param-align 跨对象类,purpose 立论例)。n=1 深案例 + 对照定性。

---

## 1. 输入(真实,来自服务器)

**代码切片(读侧门控)—— `io_file_get_fixed` (linux-6.1 io_uring/io_uring.c):**
```c
struct io_ring_ctx *ctx = req->ctx;
if (unlikely((unsigned int)fd >= ctx->nr_user_files))   // 门控条件
    goto out;
fd = array_index_nospec(fd, ctx->nr_user_files);
file_ptr = io_fixed_file_slot(&ctx->file_table, fd)->file_ptr;  // 读 file_table[fd]
```

**代码切片(写侧)—— `io_sqe_files_register` (rsrc.c:937):**
```c
if (!io_alloc_file_tables(&ctx->file_table, nr_args)) {...}   // 分配 file_table
...
ctx->nr_user_files = nr;   // 注册的文件数(= nr_args)
```

**事实账(真实 access_fact,rsrc TU):**
| 字段 | 账里形态 | 位置 |
|---|---|---|
| `io_ring_ctx.file_data`(注册表数据) | **symbolic** write | io_sqe_files_register rsrc.c:963 |
| `io_ring_ctx.nr_user_files` | **numeric-only** write,`num:io_ring_ctx@[160]`(sym=None) | rsrc.c:967(register)/799/801(unregister) |
| `io_ring_ctx.file_alloc_start/end` | symbolic write | filetable.h:71/72 |
| `io_file_table.alloc_hint/bitmap` | symbolic | filetable.h |

---

## 2. 判读员输出(结构化谓词候选)

到达 fixed-file 深层分支(req->file 为合法已注册文件)的准入条件,拆为三类、每项标对象/来源/置信:

| 类 | 谓词 | 所属对象 | 来源 | 置信 |
|---|---|---|---|---|
| **premise** | 已执行 `io_uring_register(IORING_REGISTER_FILES, nr)` → `ctx` 的注册表已建立 | io_ring_ctx | 切片(io_alloc_file_tables/file_data 写)+ 账(file_data write @rsrc.c:963) | high |
| **activation** | `fd < ctx->nr_user_files` | io_ring_ctx | 切片(io_uring.c 门控行) | high |
| **param-align** | 提交 op 的 `sqe->fd`(fixed-file 索引)须 `< nr`,其中 `nr` = 前序 register 的 `nr_args` | io_kiocb(提交) ↔ io_ring_ctx(前序 register 写入) | 切片(register `ctx->nr_user_files = nr`)+ 跨调用对齐推断 | medium_high |

判读员**未编造**账/切片外的字段(fd、nr_user_files、file_table 均在给定切片中真实出现);允许"不确定"退路——param-align 标 medium_high 而非 high,因 `nr` 与 `sqe->fd` 的绑定是跨调用语义推断而非单切片可见。

## 3. 三道校验(对真实事实账)

1. **字段存在性对账:**
   - `io_ring_ctx.file_data`(premise 依据)→ 账中有 **symbolic** write @rsrc.c:963 → **确认**(按名直接对上)。
   - `io_ring_ctx.nr_user_files`(activation + param-align 依据)→ 账中**无 symbolic 记录**;仅有 `num:io_ring_ctx@[160]` write。**按名对账失败** → 纯符号 reconciliation 会把这个**正确**的谓词误判为不可验证/幻觉(false flag)。
   - → 关键发现:旗舰 param-align 谓词卡在"字段按名对不上 numeric-only 记录"这一步,而非 LLM 推错。
2. **schema 合法:** 三类谓词 + 对象标注 + 来源 + 置信,结构合法。**通过。**
3. **可合成性:** 可合成为"先 `register(FILES, nr)`、再令 read 的 fixed-file 索引 < nr"的参数设置。**通过。**

## 4. 结论

**方法可行性:正面。** LLM 在给定切片+账约束下,正确推出了旗舰门控的完整谓词,**包括最难的 param-align 跨调用对齐**(sqe->fd ↔ 前序 register 的 nr_args),无切片外幻觉,且能对不确定项主动降置信。这支持"LLM 作受限判读员"的路线在最难子任务上成立。

**真正的瓶颈不在 LLM,在"字段存在性对账"对 numeric-only 字段的处理。** 旗舰的 `nr_user_files` 在账里是 `num:io_ring_ctx@[160]`;纯按名对账会误杀正确谓词。有两条解:
- **(A) 抽取器符号化升级(C++,原计划):** 把 nr_user_files 就地符号化。风险高(段错误敏感的热路径)。
- **(B) reconciliation 侧 name→(struct,offset) 解析器(Python,推荐):** 账保持 numeric,校验器把 `ctx->nr_user_files` 经 DWARF 解析为 `(io_ring_ctx, offset 160)`,再匹配该偏移的 numeric access_fact。**更省、更安全**——不碰抽取器热路径,复用抽取器已有的 DwarfStructIndex,只需把 io_ring_ctx 的 offset→member 布局作为**一次性 side-table** 导出(安全的 DWARF dump,非逐访问内联)。

**布局来源坑:** 必须用**被分析的 6.1 树自己的 DWARF**(.bc 带 -g)。运行时 BTF 把 nr_user_files 放在 byte 120,而 6.1 账里是 byte 160——不同内核版本布局不同,运行时 BTF 不能当 6.1 的偏移 oracle。

**对"抽取器升级是否必须前置"的回答:不必。** 方案 (B) 用一个 Python 侧偏移解析器 + 一次性 struct-layout side-table,即可让旗舰 param-align 谓词通过校验,规避了对段错误敏感抽取器的大改。这把原"高风险 C++ 重写"降级为"安全的 DWARF 布局导出 + Python 解析"。

## 5. 局限与下一步

- **局限:** n=1 深案例,且判读员被喂了精确切片(grounding 偏乐观);真实 pilot 的风险在**切片选取**与**更大样本的精确率/召回**。对照类(io_kiocb.flags 等 symbolic premise 门控)按名对账可直接确认,较易,非瓶颈。
- **下一步(建 LLM pilot 时):**
  1. 先做 reconciliation 侧 name→offset 解析器 + struct-layout side-table(方案 B),解锁 numeric-only 字段的对账;
  2. 备 ~10–20 个人工标注门控(含若干 param-align)作评测集,量三道校验后的精确率/召回率;
  3. 判读员先用本模型跑,量准后再定 LLM 主力/辅助与是否需外部 API 批量化。
