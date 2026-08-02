---
name: android-perf-bench
description: 测试 Android 手机性能并生成基线对比报告。每当用户提到测手机性能、App 启动时间、帧率/FPS/jank/卡顿、内存/缓存容量、抓 trace、perfetto/trace_processor 解析、手机性能基线测试，或想对比不同系统配置/app 版本/governor 的性能差异时使用——即使用户没明说"性能测试"。通过 adb + uiautomator2 自动化跑 workload、抓 perfetto trace、用 trace_processor_shell 解析、生成 Markdown/HTML/CSV 报告。
---

# Android 性能测试套件 (android-perf-bench)

一个测量 Android 手机性能并生成"基线 vs 方案"对比报告的 harness。方法学复刻自 **Fleet (ASPLOS'24)**，但通用化：不刷自编 AOSP，对任意已连接手机做性能基线测试。

## 何时使用

用户想做以下任意一件事时加载本 skill：
- "测一下这台手机的性能" / "跑个性能基线"
- "测 App 启动时间 / 冷启动 / 热启动"
- "测帧率 / FPS / 卡顿 / jank"
- "测内存 / 能缓存几个 app"
- "抓个 trace" / "用 perfetto 分析"
- "对比开启某功能前后/不同 app 版本的性能差异"

## 核心工作流（四阶段）

所有命令在 `skills/android-perf-bench/scripts/` 下运行。前置：把该目录加入 PATH 或用 `python -m apb`（需先 `cd` 到 `scripts/` 或设置 PYTHONPATH）。

### 阶段 0 — 引导设置（首次必跑）
```bash
cd skills/android-perf-bench/scripts
python -m apb setup
```
探测已连接手机的型号/Android 版本/root 状态/perfetto·atrace 可用性/FrameTimeline 支持；自动下载对应平台的 `trace_processor_shell`；安装 Python 依赖；推送 uiautomator2 agent。结果写入 `out/env.json`，后续阶段自动读取以自适应抓取路径。

若无设备连接，给出明确的连接引导而不是崩溃。

### 阶段 1 — 抓 trace + 跑 workload
```bash
python -m apb capture --type startup --app com.example.app --repeat 10 --run baseline
python -m apb capture --type jank    --app com.example.app --scroll-duration 30 --run baseline
python -m apb capture --type cache   --app-list "com.a,com.b,com.c" --use-duration 30 --run baseline
```
按设备能力自动选 perfetto（root/userdebug）或 atrace（user 降级）。trace 落到 `out/traces/<run>/`。

### 阶段 2 — 解析 trace
```bash
python -m apb analyze --run baseline
```
调 `trace_processor_shell` 跑 SQL/metrics，计算指标（TTID/TTFD、FPS、jank ratio、缓存数等，公式复刻 Fleet）。结果写到 `out/results/<run>.json`。

### 阶段 3 — 报告与对比
```bash
python -m apb report --runs baseline.json,variant.json
```
生成 `out/report/`：`results.csv` + `results.json` + `report.md`（每实验一张表，含"vs 基线"差值/加速比列）+ `report.html`（内嵌柱状图/CDF/折线 PNG）。

## 一键全流程
```bash
python -m apb run --type startup --app com.example.app --repeat 10  # capture+analyze
python -m apb run --type all --app com.example.app                  # 三类实验全跑
```

## 何时读哪个 reference

- **references/fleet-methodology.md** — 想理解 Fleet 原始 5 个实验、论文图表映射、为什么这么测时读
- **references/benchmark-specs.md** — 想知道每个测试项的**精确规格**（测什么/预制环境参数/最后分析哪些数据/CLI 参数）时读；调参前必读
- **references/perfetto-queries.md** — 想看/改 SQL 查询、加新指标、理解 trace_processor_shell 用法时读
- **references/troubleshooting.md** — trace 抓不到、root 权限问题、FrameTimeline 不支持、uiautomator2 连不上等排错

## 测试项速览

| 测试项 | 测什么 | 关键指标 | 对应 Fleet |
|---|---|---|---|
| startup | App 冷/热启动耗时 | TTID, TTFD, WaitTime, CDF | Exp-3 (Fig 13) |
| jank | 前台滚动帧率稳定性 | FPS, jank ratio, jank_type 分解（FrameTimeline，user 版可用） | Exp-4 (Fig 14) |
| cache | 内存缓存容量 | 每步缓存数序列, 峰值缓存数 | Exp-1 (Fig 11c) |
| camera | 重载相机（内存压力） | 相机启动延迟, 相机后存活 app 数, MemAvailable 曲线 | ATC'26 A2 (App A.1) |
| cpu（可选） | CPU 开销 mutator/GC 分解 | app/mutator/gc runtime | Exp-4 CPU |

**关键技术决策**：jank 分析用 SurfaceFlinger **FrameTimeline**（`actual_frame_timeline_slice`）作为主路径——它在 Android 12+ 的 **user 版（无 root）** 设备上完全可用，不依赖 app 的 atrace 标签。`Choreographer#doFrame` 阈值法作为备选（需 app debuggable 或 root）。已在荣耀 BKQ-AN00 (Android 16, user 版) 实测验证。

详细规格见 `references/benchmark-specs.md`。每个测试项都支持 `--background-apps N` 在后台预跑 N 个 app 制造内存压力。

## 与 Fleet 的关系

Fleet 测量的是它自研 ART/Kernel（GC-Swap 协同设计）的收益，需要刷自编 AOSP。本套件**复用其测量方法学**（trace 抓取方式、perfetto SQL、指标计算公式、CDF/柱状对比可视化），但**通用化**为任意手机的基线测试，对比维度换成"不同手机/不同系统配置/不同 app 版本"等。需要 root 之外的深度内核指标（Fleet Exp-0/Exp-2 的私有日志）不在本套件范围。

## 默认 app 列表

`apb/config.py` 内置 Fleet 的 18 个 app 包名（Twitter/Facebook/Instagram/YouTube/Chrome 等）作为默认。可覆盖：`--app-list "pkg1,pkg2"` 或改 config.py。
