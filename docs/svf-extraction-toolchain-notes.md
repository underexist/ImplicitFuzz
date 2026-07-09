# 静态分析抽取层 - 环境搭建排查笔记(2026-07-08)

## 背景

按照双语言架构方案(C++ 基于 SVF 做值流分析,Python 通过 subprocess 消费其
JSON Lines 输出),第一步是在本地(macOS arm64)搭建 SVF + LLVM 21 的最小
可运行环境,照抄 `svf-ex.cpp` 骨架跑通一个最小 driver。过程中在 macOS 的
动态链接机制上反复踩坑,最终决定把实际实现移到 Linux 服务器上进行。本文
记录排查过程和结论,避免以后重复踩同样的坑。

## 结论(TL;DR)

- SVF 支持作为库嵌入自定义 C++ 程序,`svf-ex.cpp`(`SVF/svf-llvm/tools/Example/svf-ex.cpp`)
  是官方范例 driver,可以直接照抄。
- SVF 针对 LLVM 21 从源码构建**在 macOS 上可以编译成功**,但运行时会遇到
  多个 macOS 动态链接器(dyld)特有的 ABI 问题,反复修复后仍剩一个"进程内
  同时加载两份不兼容 libc++ 导致段错误"的问题没有彻底解决。
- 这些问题大多是 macOS 的 two-level namespace 符号绑定机制导致的,在 Linux
  的 ELF/`ld.so` 体系下预期不会以同样形式出现,因此决定暂停 macOS 上的环境
  搭建,直接在目标 Linux 服务器上实现。

## 环境约束

- 机器:macOS arm64(Darwin 21.6.0),磁盘初始仅 26GB 可用(后清理到
  30GB+),没有 Docker。
- Apple 系统自带 clang 是 14.0.0(Xcode CommandLineTools),不满足 SVF
  需要的 LLVM 21。

## 排查过程与每一步的根因

### 1. `brew install llvm@21` 实际是从源码整体编译

`brew info llvm@21` 显示有 bottle,但实际执行 `brew install llvm@21` 后台
观察到大量 `clang++ -cc1` 编译 LLVM/Clang 自身源码的进程,15 分钟内把
26GB 磁盘吃到剩 20GB。判断是这台机器的操作系统版本/架构没有匹配的预编译
bottle,brew 静默回退到源码编译。

**处理**:杀掉 brew 进程,清理残留的 1.5GB 临时构建目录,改用 LLVM 官方
GitHub Release 的预编译包(`LLVM-21.1.8-macOS-ARM64.tar.xz`,来自
`llvm/llvm-project` release `llvmorg-21.1.8`),直接下载解压,不编译。

**下载过程本身也不顺利**:约 1.4GB 的包在这台机器上下载速度只有
100-300KB/s,且两次因 `HTTP/2` 流错误(curl exit 92)、连接被重置
(curl exit 35)而中断,最终用 `curl -L --http1.1 -C - --retry 10
--retry-all-errors` (HTTP/1.1 + 断点续传 + 自动重试)才完整下载成功。

### 2. SVF 的 `build.sh` 在 macOS 上会调用 `brew install llvm@${MajorLLVMVer}`

`build.sh` 的逻辑是:如果 `LLVM_DIR` 环境变量未设置且未提前指向存在的目录,
macOS 分支会自动执行 `brew install llvm@21`——即会重新触发上面的问题。

**处理**:提前 `export LLVM_DIR=<解压好的 LLVM 21 路径>`,让 `build.sh`
里 `if [[ ! -d "$LLVM_DIR" ]]` 判断为假,跳过整个下载/brew 分支。

### 3. `build.sh` 设置的全局 `DYLD_LIBRARY_PATH` 把系统自带的 `cmake` 自己弄崩了

`build.sh` 在执行 cmake 之前设置了:
```
export DYLD_LIBRARY_PATH=$LLVM_DIR/lib:$Z3_DIR/bin:$DYLD_LIBRARY_PATH
```
这导致 Homebrew 装的 `cmake` 本身在启动时被劫持去加载我们 LLVM 21 自带的
`libc++.1.0.dylib`,而不是它原本链接的系统 libc++,两者 ABI 不兼容,cmake
直接 `Abort trap: 6`(报错缺 `std::length_error` 析构符号)。

**处理**:不要全局设置 `DYLD_LIBRARY_PATH` 后再调用 cmake/编译工具;改为
手动复现 `build.sh` 的 cmake 配置/编译步骤,只在**运行编译产物**时才需要
考虑动态库搜索路径(且优先靠 rpath 而不是环境变量,见下文第 6 条)。

### 4. cmake 探测到的 C++ 编译器实际是 AppleClang,不是 LLVM 21

即使把 `$LLVM_DIR/bin` 加进 `PATH` 最前面,cmake 的编译器自动探测仍然选中
了 Xcode 自带的 AppleClang(`CMake C++ compiler: AppleClang`)。导致整个
SVF 代码库被 AppleClang 14 编译,但链接期又依赖 LLVM 21 的头文件/库,产生
一系列 ABI 不匹配的链接错误(`could not parse object file ... Opaque
pointers are only supported in -opaque-pointers mode`,以及一堆
`__cxa_*`/`__hash_memory` 符号缺失)。

**处理**:cmake 配置时显式指定
```
-DCMAKE_C_COMPILER=$LLVM_DIR/bin/clang
-DCMAKE_CXX_COMPILER=$LLVM_DIR/bin/clang++
```
不要依赖 PATH 顺序。`build.sh` 文件末尾其实留了一条注释提示了这个做法,
一开始没注意到。

### 5. LLVM 21 官方发行版把 `libc++` 和 `libc++abi` 拆成了两个独立的 dylib

修正编译器后,链接阶段依次缺:
- `std::__1::__hash_memory(...)`:存在于 LLVM 21 的 `libc++.dylib`,不存在
  于 macOS 系统的 `/usr/lib/libc++.dylib`(版本太老)。
  → 修法:链接参数加 `-L$LLVM_DIR/lib -Wl,-rpath,$LLVM_DIR/lib`,让链接器
  优先用 LLVM 21 自己的 `libc++`。
- `__cxa_throw`/`__cxa_rethrow`/`__cxa_demangle` 等符号:存在于 LLVM 21 的
  `libc++abi.dylib`,但默认链接不会自动带上它(macOS 系统的 libc++ 是"合并"
  过的单一库,包含这些符号;LLVM 官方发行版是拆开的两个库)。
  → 修法:链接参数显式加 `-lc++abi`。

最终把 SVF 完整编译成功(`ae`/`cfl`/`dvf`/`llvm2svf`/`mta`/`saber`/
`svf-ex`/`wpa` 全部产出)所用的完整 cmake 配置:
```bash
cmake -D CMAKE_BUILD_TYPE:STRING="Release" \
    -DSVF_ENABLE_ASSERTIONS:BOOL=true \
    -DBUILD_SHARED_LIBS=ON \
    -DCMAKE_MACOSX_RPATH=ON \
    -DCMAKE_C_COMPILER=${LLVM_DIR}/bin/clang \
    -DCMAKE_CXX_COMPILER=${LLVM_DIR}/bin/clang++ \
    -DCMAKE_EXE_LINKER_FLAGS="-L${LLVM_DIR}/lib -Wl,-rpath,${LLVM_DIR}/lib -lc++abi" \
    -DCMAKE_SHARED_LINKER_FLAGS="-L${LLVM_DIR}/lib -Wl,-rpath,${LLVM_DIR}/lib -lc++abi" \
    "-DCMAKE_BUILD_RPATH=@loader_path/../lib;@loader_path/../../z3.obj/bin;${LLVM_DIR}/lib" \
    "-DCMAKE_INSTALL_RPATH=@loader_path/../lib;@loader_path/../../../z3.obj/bin;${LLVM_DIR}/lib" \
    -S . -B Release-build
```
(中途也试过把链接器换成 LLVM 自带的 `ld64.lld`/`lld`——即 `-fuse-ld=lld`
——想绕开 macOS 系统 `ld` 对 opaque-pointer bitcode 的解析问题,但会引出
新的一批 libc++abi 符号缺失,反而更麻烦;最终还是用系统 `ld` + 上面这套
`-L`/`-Wl,-rpath`/`-lc++abi` 参数解决的。)

### 6. `libz3.dylib`(SVF 依赖的预编译 Z3)和自己编的代码用了不同的 libc++,运行时崩溃

SVF **编译链接**都成功后,实际**运行**`svf-ex`/`wpa` 处理一个 `.ll` 文件
时,统计信息打印到一半直接 `libc++abi: terminating`(无具体异常信息)。

用 `lldb` 抓到真实调用栈,定位到 `SVFStat::printStat()` 里一次普通的
`std::cout << number` 触发了 `std::locale::use_facet` 抛 `bad_cast`。
`otool -L` 排查发现:SVF 自己的二进制链接的是 LLVM 21 的
`libc++.1.dylib`,而 `libz3.dylib`(SVF-npm 提供的预编译包)链接的是
**macOS 系统的** `/usr/lib/libc++.1.dylib`——同一进程里加载了两份 ABI
不兼容的 C++ 运行时,`std::locale` 的 RTTI 身份对不上导致抛异常失败。

**尝试过的修法**:
- 用 `install_name_tool -change` 把 `libz3.dylib` 对 libc++ 的依赖改指向
  LLVM 21 那份 → 往前推进了一步,但暴露出 `std::length_error` 析构符号
  只存在于 LLVM 21 的 `libc++abi.dylib`,而 `libz3.dylib` 没有直接依赖
  `libc++abi.dylib`(Mach-O 的 two-level namespace 在链接期把每个符号绑定
  到固定的目标库,`install_name_tool` 没法事后改这个绑定关系)。
- 试过 `DYLD_FORCE_FLAT_NAMESPACE=1`(放开符号解析范围到进程内所有已加载
  库)→ 无效,因为这个错误是 dyld **加载期的硬绑定失败**,不是运行期的
  懒符号查找,flat namespace 管不到。
- 干脆把 Z3 4.15.4 从源码用同一个 LLVM 21 clang++ 重新编译一遍(复用上面
  第 5 条的链接参数)→ 编译链接都成功,产物的依赖关系变得干净(全部指向
  `@rpath/libc++abi.1.dylib`、`@rpath/libc++.1.dylib`)。
  - 替换过程中还踩了一个坑:这台机器 `PATH` 里的 `install_name_tool`/
    `otool` 实际解析到 Anaconda 自带的 `cctools-port` 版本,对新编译出来
    的 dylib 处理时被系统杀掉(exit 137),换成 Xcode 自带的
    `/usr/bin/install_name_tool`/`/usr/bin/otool` 才正常。
  - 改了 `libz3.dylib` 的 install name 后代码签名失效,Apple Silicon 上
    未签名的 dylib 无法加载,需要 `codesign -s - -f` 重新做 ad-hoc 签名。
- 换上重新编译的 Z3 后,原来的 `libc++abi: terminating` 异常确实消失了,
  但变成了另一种崩溃:**段错误**(SIGSEGV),崩溃点在**系统的**
  `libc++.1.dylib`(注意文件名里没有 LLVM 那份特有的 `.1.0` 版本号)的
  locale 内部代码里访问野指针——说明进程里**仍然**有第二份 libc++ 被
  加载,但检查了 `z3.obj/bin/`、`Release-build/lib/` 两个目录都没有发现
  多余的 libc++ 文件,还没来得及用 `DYLD_PRINT_LIBRARIES=1` 精确定位这
  第二份库到底从哪条路径加载进来——排查到这里决定暂停,转到 Linux 服务器
  上直接实现。

## 对 Linux 服务器实现的建议

1. **LLVM 21 优先找官方 GitHub Release 的预编译包**
   (`llvm/llvm-project` release `llvmorg-21.1.8`,Linux 资产名类似
   `LLVM-21.1.8-Linux-<arch>.tar.xz`),避免重蹈"包管理器静默从源码编译"
   的覆辙;如果发行版仓库(apt/yum)有对应的 llvm-21 包,直接装包管理器
   版本大概率更省心。
2. **SVF 编译时显式指定 `-DCMAKE_C_COMPILER`/`-DCMAKE_CXX_COMPILER`**
   指向选定的 LLVM 21 clang/clang++,不要依赖 `PATH` 顺序——这条是
   cmake 跨平台的通病,不是 macOS 专属,Linux 上同样建议这样做。
3. **Z3 建议用同一套 LLVM 21 clang++ 从源码编译**,而不是用预编译的
   `libz3` 包——目的是保证同一个进程里所有 C++ 组件用同一套 ABI 一致的
   运行时。这是通用的工程原则,不是绕开 macOS 问题的临时手段。
4. 以下这些是 **macOS dyld 特有**、预期在 Linux 上不会以同样形式出现,
   不需要带过去的坑:全局设置 `DYLD_LIBRARY_PATH` 污染无关进程、Mach-O
   two-level namespace 符号绑定、`cctools-port` 与系统 `otool`/
   `install_name_tool` 冲突、dylib 改名后需要重新 ad-hoc 签名。Linux 下
   `ld.so` 用的是更简单的符号解析模型(以及 `LD_LIBRARY_PATH`/rpath 的
   行为也更宽松),但仍然建议:
   - 全程只用一套工具链(同一个 LLVM 21 的 clang/clang++/ld)编译所有
     C++ 组件(SVF、Z3、以及我们自己的 driver),避免"部分组件用系统
     libstdc++/glibc、部分组件用自带运行时"的混用场景。
   - 如果最终选择动态链接,记得给自己的可执行文件设置正确的 `RPATH`/
     `RUNPATH`(而不是依赖调用者设置 `LD_LIBRARY_PATH`),减少环境依赖。

## 参考

- SVF 官方仓库:https://github.com/SVF-tools/SVF (克隆于 HEAD,构建于
  2026-07-08)
- SVF 官方"作为库嵌入"范例:https://github.com/SVF-tools/SVF-example ,
  以及仓库内 `svf-llvm/tools/Example/svf-ex.cpp`
- LLVM 21.1.8 Release:
  https://github.com/llvm/llvm-project/releases/tag/llvmorg-21.1.8
- Z3 4.15.4 源码:https://github.com/Z3Prover/z3 (tag `z3-4.15.4`)
