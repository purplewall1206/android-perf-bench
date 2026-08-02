# android-perf-bench

一个测量 **Android 手机性能**并生成"基线 vs 方案"对比报告的 ZCode/Claude skill harness。测量方法学复刻自 **[Fleet (ASPLOS'24)](https://github.com/jiachengh/Fleet)**，但通用化为对任意已连接手机做性能基线测试——**不需要刷自编 AOSP、不需要 root**。

已在 **荣耀 BKQ-AN00 (Android 16, user 版, 无 root)** 上端到端实测验证。

## 能测什么

| 测试项 | 测什么 | 关键指标 | 对应论文 |
|---|---|---|---|
| `startup` | App 冷/热/温启动耗时 | TTID, TTFD, WaitTime, TotalTime, CDF | Fleet Exp-3 |
| `jank` | 前台滚动帧率稳定性与掉帧 | FPS, jank ratio, jank_type 分解（FrameTimeline，user 版可用） | Fleet Exp-4 |
| `cache` | 内存缓存容量（连开多 app 后剩几个） | 每步缓存数序列, 峰值缓存数, 每 app RSS/Heap | Fleet Exp-1 |
| `camera` | 重载相机（内存压力峰值） | 相机启动延迟, 相机后存活 app 数, MemAvailable 曲线 | ATC'26 A2 (App A.1) |
| `cpu`（可选） | CPU 开销，分离 mutator/GC 线程 | app/mutator/gc runtime | Fleet Exp-4 |

## 工作流（四阶段）

```bash
cd scripts

# 阶段 0：探测设备能力 + 下载 trace_processor_shell + 装依赖 + 推 u2 agent
python -m apb setup --install-deps --init-u2

# 阶段 1：抓 perfetto trace + 跑 workload
python -m apb capture --type startup --app com.android.chrome --repeat 10 --run baseline
python -m apb capture --type jank    --app com.chrome --scroll-duration 30 --run baseline
python -m apb capture --type cache   --app-list "pkg1,pkg2,pkg3" --run baseline
python -m apb capture --type camera  --run baseline   # 论文 A.1：N app → 最后重载相机

# 阶段 2：用 trace_processor_shell 解析 trace，计算指标
python -m apb analyze --run baseline

# 阶段 3：生成对比报告（CSV/JSON + Markdown + HTML + 图）

# 阶段 2：用 trace_processor_shell 解析 trace，计算指标
python -m apb analyze --run baseline

# 阶段 3：生成对比报告（CSV/JSON + Markdown + HTML + 图）
python -m apb report --runs baseline.json,variant.json --baseline baseline
```

`capture` 默认会自动接 `analyze`；也可用 `python -m apb run` 串联。

报告输出到 `scripts/out/report/`：`report.md`（对比表格）+ `report.html` + `*.csv` + `*.png`（柱状/CDF/折线）。

## 灵活配置测试负载

测试的"负载"由一组 CLI 参数控制。**核心思路：跑两次（baseline / variant），对比报告自动算差值。** 变化任何一个负载参数构成新的 run，就能对比"加压 vs 不加压""长滚动 vs 短滚动""多 app vs 少 app"。

### 负载参数总表

| 参数 | 作用 | 默认 | 影响哪个测试 |
|---|---|---|---|
| `--app <pkg>` | 单 app 测试目标 | 必填 | startup / jank / cpu |
| `--app-list "p1,p2,..."` | 多 app 列表 | 论文/Fleet 列表 | cache / camera |
| `--repeat N` | 启动重复次数（统计样本量） | 10 | startup |
| `--launch-type cold\|hot\|both` | 启动类型 | cold | startup |
| `--scroll-duration S` | 滚动秒数（jank 采样窗口） | 30 | jank / cpu |
| `--scroll-mode swipe\|fling` | 滚动方式 | swipe | jank / cpu |
| `--scroll-scale 0~1` | 滑动幅度（屏宽比例） | 0.8 | jank / cpu |
| `--warmup S` | 启动后等稳定秒数（关弹窗） | 5 | jank / cpu |
| `--use-duration S` | cache 每 app 前台秒数 | 30 | cache |
| `--camera-use-duration S` | camera 每 app 前台秒数 | 5 | camera |
| `--camera-repeat N` | **完整加压流程（N app → 相机×1）重复轮数**（论文跑 500 轮） | 3 | camera |
| **`--background-apps N`** | **后台预跑 N 个 app 制造内存压力** | 0 | startup / jank |
| `--run <name>` | 本次 run 名（baseline/variant） | baseline | 全部 |

### 典型负载场景

**场景 1：测压力下的启动/帧率**（`--background-apps`）
```bash
# 无压力基线：Chrome 冷启动
python -m apb capture --type startup --app com.android.chrome --repeat 10 --run baseline
# 加压：后台先开 5 个 app 抢内存，再启动 Chrome
python -m apb capture --type startup --app com.android.chrome --repeat 10 --background-apps 5 --run pressure
python -m apb report --runs baseline.json,pressure.json --baseline baseline
```

**场景 2：测滚动时长对帧率的影响**（`--scroll-duration`）
```bash
python -m apb capture --type jank --app com.android.chrome --scroll-duration 10  --run short
python -m apb capture --type jank --app com.android.chrome --scroll-duration 120 --run long
```

**场景 3：测缓存容量**（`--app-list` + `--use-duration`）
```bash
# 连开 8 个 app，每个用 30s，看最终缓存几个
python -m apb capture --type cache --app-list "p1,p2,p3,p4,p5,p6,p7,p8" --use-duration 30 --run baseline
```

**场景 4：重载相机（ATC'26 A2 论文 A.1，完整加压流程多轮）**
```bash
# 默认：论文 23-app（自动取设备已装交集）+ 自动探测相机 + 完整流程（N app→相机×1）跑 3 轮
python -m apb capture --type camera --run baseline
# 自定义：5 个 app + 完整加压流程跑 10 轮（轮间不清后台，压力累积，统计更稳）
python -m apb capture --type camera --app-list "p1,p2,p3,p4,p5" --camera-repeat 10 --run baseline
```

**场景 5：对比不同 app 的性能**
```bash
python -m apb capture --type jank --app com.tencent.mm     --run wechat
python -m apb capture --type jank --app com.ss.android.ugc.aweme --run douyin
python -m apb report --runs wechat.json,douyin.json  # 多 run 自动对比
```

### 一键全量测试
```bash
python -m apb run --type all --app com.android.chrome    # startup + jank + cache + camera
```

### 注意事项（荣耀/华为等 MagicOS）
- **app 启动用 u2 兜底**：`resolve-activity` 解析真实 launcher activity 后 `am start -W` 拿启动耗时，再用 u2 `app_start` 确保切到前台（仅 `am start` 可能不切前台）
- **后台保活 app 干扰**：测试前建议 `am force-stop` 掉知乎/番茄等爱抢前台的 app；若 app 未到前台，benchmark 会自动跳过手势避免误触桌面
- 相机包名自动探测（`com.hihonor.camera` / `com.huawei.camera` / `com.android.camera` 等）

## 关键技术决策

### jank 分析用 FrameTimeline，不用 atrace
**SurfaceFlinger FrameTimeline**（`actual_frame_timeline_slice`）作为 jank 主路径——它在 **Android 12+ 的 user 版（无 root）** 设备上完全可用，不依赖 app 的 atrace 标签（`Choreographer#doFrame` 那种，非 debuggable app 抓不到）。`Choreographer#doFrame` 阈值法作为备选。

> 这是本套件相对 Fleet 原项目的关键适配：Fleet 用 root/userdebug 设备抓 atrace_apps；本套件让 user 版手机也能做完整 jank 分析。

参考：[perfetto FrameTimeline 文档](https://perfetto.dev/docs/data-sources/frametimeline)、[trace-config-proto](https://perfetto.dev/docs/reference/trace-config-proto)。

### perfetto 配置要点
- 数据源名 `android.surfaceflinger.frametimeline`（**注意无 s**，写错则数据源不生效）
- 双 buffer 设计：FrameTimeline + process_stats 单独用 buffer 1，避免被海量 `sched_switch` 事件（buffer 0）覆盖
- 配置必须放 `/data/misc/perfetto-configs/`（Android 12+ user 版 perfetto 只读此目录），输出放 `/data/misc/perfetto-traces/`
- `--background` 模式录制，录制完成后进程自动退出

## 设备能力自适应

`apb setup` 自动探测并选择 trace 后端：

| 条件 | 后端 | 说明 |
|---|---|---|
| perfetto 可用 且 (root 或 Android 12+) | **perfetto** | 完整能力（含 FrameTimeline） |
| 否则，atrace 可用 | atrace 降级 | jank 仅 doFrame 阈值法（需 app debuggable），无 FrameTimeline |
| 都不可用 | none | 报错 |

## 目录结构

```
android-perf-bench/
├── SKILL.md                       # ZCode skill 入口（四阶段流程 + 触发描述）
├── README.md
├── references/                    # 渐进式披露文档
│   ├── fleet-methodology.md       # Fleet 5 实验方法学 + 论文图映射
│   ├── benchmark-specs.md         # 每个测试项详细规格（测什么/预制环境/分析数据/CLI 参数）
│   ├── perfetto-queries.md        # 全部 SQL 查询手册
│   └── troubleshooting.md         # 排错（root/驱动/FrameTimeline/u2）
├── templates/
│   ├── perfetto-config.textproto  # perfetto trace 配置（双 buffer，参数化）
│   ├── report.md.j2 / .html.j2    # 报告模板
└── scripts/
    ├── requirements.txt
    ├── run.py                     # 便捷入口
    └── apb/                       # python -m apb
        ├── __main__.py            # CLI: setup/capture/analyze/report/run
        ├── setup_env.py           # 阶段0：设备探测 + trace_processor_shell + 依赖
        ├── device.py              # adbutils + uiautomator2 封装
        ├── trace_capture.py       # 阶段1：perfetto/atrace 自适应抓取
        ├── trace_analyze.py       # 阶段2：trace_processor_shell 跑 SQL
        ├── metrics.py             # 指标计算（复刻 Fleet 公式）
        ├── compare.py             # 基线 vs 方案对比
        ├── report.py              # 阶段3：CSV/JSON/MD/HTML/图
        └── benchmarks/            # startup / jank_fps / cache_mem / camera_reload (+ cpu)
```

## 安装与依赖

### 主机
- Python 3.10+
- adb（Android platform-tools）在 PATH
- `pip install -r scripts/requirements.txt`（uiautomator2, perfetto, adbutils, pandas, matplotlib, jinja2）

trace_processor_shell 由 `apb setup` 自动从 [perfetto releases](https://github.com/google/perfetto/releases) 下载（按平台选 windows/linux/mac-amd64/arm64）。

### 设备
- Android 10+（FrameTimeline 需 12+）
- 开启 **USB 调试**（荣耀/华为还需开"仅充电模式下允许 ADB 调试"+ 装 Honor Suite/HiSuite 驱动）
- user 版即可（无 root 要求）；root/userdebug 可解锁更多 atrace 类别

## 实测示例（荣耀 BKQ-AN00, Android 16, user 版）

Settings app 滚动 12s 的 jank 分析：
- FPS = 97.5，jank ratio = 47.1%
- jank_type 分解：None 41.3%、Buffer Stuffing 58.3%、App Deadline Missed 0.2%、SurfaceFlinger Scheduling 0.1%、Prediction Error 0.1%

Chrome 冷启动 5 次：
- WaitTime 均值 205ms（std 6ms，min 196，max 212，p95 212）

重载相机（camera_reload，3 个 app → 相机×2，复刻 ATC'26 A2 论文 A.1）：
- 相机启动延迟：均值 233.5ms（263ms / 204ms）
- 相机启动后存活后台 app：3 个（全部存活）
- 相机制造的内存压力：相机前 MemAvailable 3.8GB → 相机后 2.8-3.1GB（约 1GB 压力峰值）

## 与 Fleet 的关系

Fleet 测量的是它自研 ART/Kernel（GC-Swap 协同设计）的收益，需刷自编 AOSP 镜像、Pixel 3 + root。本套件**复用其测量方法学**（trace 抓取、perfetto SQL、指标计算公式、CDF/柱状对比可视化），但**通用化**为任意手机的基线测试，对比维度换成"不同手机 / 不同系统配置 / 不同 app 版本"。Fleet 的 Exp-0（私有日志验证）和 Exp-2（需 debug 编译的 GC working set）不在本套件范围。

## 许可

MIT
