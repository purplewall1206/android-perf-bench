---
description: 跑 Android 性能测试并生成对比报告（启动时间/帧率jank/缓存/重载相机）。
argument-hint: "[测试类型和参数，如 'chrome 冷启动' 或 '测一下抖音的帧率']"
skills: android-perf-bench
allowed-tools: Bash, Read, Write
---

使用 `android-perf-bench` skill 完成用户的性能测试请求：

$ARGUMENTS

按 skill 的四阶段工作流执行：
1. **setup**（首次）：`python -m apb setup` 探测设备、下载 trace_processor_shell、装依赖、扫描 app、探测后台清理方式
2. **capture**：`python -m apb capture --type <startup|jank|cache|camera> ...` 抓 trace + 跑 workload
3. **analyze**：`python -m apb analyze --run <name>` 解析 trace 计算指标（capture 默认自动接 analyze）
4. **report**：`python -m apb report --runs baseline.json,variant.json` 生成对比报告

命令在 `skills/android-perf-bench/scripts/` 下运行（`cd` 到该目录或用 `python -m apb`）。

若用户没指定测试类型，根据意图推断：提"启动/打开速度"→ startup；提"帧率/卡顿/流畅/jank"→ jank；提"内存/能开几个/缓存"→ cache；提"相机/拍照/压力"→ camera。
若用户要对比，跑两次用不同 `--run` 名（如 baseline / variant），再 report。
