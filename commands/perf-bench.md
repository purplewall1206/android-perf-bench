---
description: 跑 Android 性能测试并生成对比报告（启动速度/帧率jank/重载相机/保活模型）。
argument-hint: "[测试类型和参数，如 'chrome 冷启动' 或 '测抖音帧率' 或 '保活测试']"
skills: android-perf-bench
allowed-tools: Bash, Read, Write
---

使用 `android-perf-bench` skill 完成用户的性能测试请求：

$ARGUMENTS

按 skill 的四阶段工作流执行。命令在 `${CLAUDE_PLUGIN_ROOT}/skills/android-perf-bench/scripts/` 下：

```bash
cd "${CLAUDE_PLUGIN_ROOT}/skills/android-perf-bench/scripts"
```

1. **setup**（首次）：`python -m apb setup` 探测设备、下载 trace_processor_shell、装依赖、扫描 app、探测后台清理方式
2. **capture**：`python -m apb capture --type <startup|jank|camera|keepalive> ...` 抓 trace + 跑 workload
3. **analyze**：`python -m apb analyze --run <name>` 解析 trace 计算指标（capture 默认自动接 analyze）
4. **report**：`python -m apb report --runs baseline.json,variant.json` 生成对比报告

**4 个测试类型**（根据用户意图推断）：
- 提"启动/打开速度/冷启动/热启动" → `startup`
- 提"帧率/卡顿/流畅/jank/fps" → `jank`
- 提"相机/拍照/压力/相机重载" → `camera`
- 提"保活/能开几个/内存压力/存活" → `keepalive`

**统一范式**：每个测试按轮次走"小刷子清后台→加压→抓trace→启动目标→回桌面"。
若用户要对比，跑两次用不同 `--run` 名（如 baseline / variant），再 report。
