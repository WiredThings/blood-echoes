# 正常 CMake layout target 的完整封口

2026-10-26，`tools/build_metadata_layout.py` 新增正常 target 收集入口，旧 `--component-plan` 及原模板构建入口保留。它不编译 renderer／FEX，不重放历史 compile template；消费的是当前正常 CMake 已实际构建的 `metadata-layout`。

```sh
python3 "$runtime_source/tools/build_metadata_layout.py" \
  --project "$private_project" \
  --renderer-build "$renderer_build" \
  --source-root "$runtime_source/vendor/metal_api/shadps4" \
  --output "$private_project/evidence/layout-collection-new"
```

输出必须是明确 project 的 `evidence/` 下全新目录。输入目录／文件仍受 canonical、owner、regular、bounded、nonlinked 检查；未修改任何运行时目录准入。

正常入口先核对当前 CMake cache、File API target、单一 ARM64、唯一原 emitter 源／对象／实际二进制、编译字段、生成的完整 Profile 字段表。它把 `build.ninja` 原字节复制到新输出目录，仅换 primary manifest 文件名，通过原 build cwd 的 Ninja `-n metadata-layout` 检查 command log／dependency database／目标依赖，要求 `no work to do`。没有删除或改写规则，没有执行构建；这样避免默认 glob 检查只显示“重跑 CMake”而遮住目标实际是否待编译。CMake／源／对象／二进制身份在收集前后重新核对，改变则拒绝完成。

原构建入口和新收集入口共用 `seal_layout`：

- 实际二进制必须是 thin ARM64，`otool -L` 只允许系统库；以清洁工具环境执行，保留内存／锁屏／超时保护。
- 填入当前 12 项 canonical 源摘要、实际 binary SHA、完整 Profile 字段，以及 `renderer_loaded=false`／`cached_code_registered=false`。
- 调用实际 native Writer 生成 plain、fetch、motion、geometry、graphics、compute、profile-padding 七种合成 fixture，核对文件／返回 envelope／SHA，并交原 canonical Python decoder 验证。Profile-padding 本身是两个裸 Profile；只补原严格 profile reader 所要求的合成 envelope，不放宽 reader。
- 最终 `layout.json` 必须通过原 `load_canonical_layout`，才返回完成结果。失败不得发布成功的 `result.json`，也不能将 raw emitter JSON 直接交给 component。

有界实跑证据：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/normal-layout-sealed-1793024421953187000/`。约 0.38 秒完成；layout SHA 为 `e33fe51b6c284ddac449880f85864cbada76eebb76d2594f1cc5a6c6cc4d5f9c`，七项实际 fixture 与严格 loader 均通过，实际加载边仅 libc++／System。此前 raw emitter 的 215 个原生 ABI 字段现在具备完整封口，而不是借用旧 layout 的来源字段。

十二项短作者反例也通过：错误架构、第三方／相对加载边、缺 Profile 字段、fixture envelope 错误、中途 binary／源变化、最终 strict-loader 拒绝、CMake artifact 越界和链接等。它们使用不执行 placeholder 的假 runner，与实际 native 收集证据分开记录；记录位于 `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/normal-layout-controls-1793024421812740000/result.json`。此工作不代表完整 renderer／CPU 构建、native 运行 ABI 或最终安装包已经验收。
