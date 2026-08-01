# android-perf-bench

一个测量 **Android 手机性能**并生成"基线 vs 方案"对比报告的 ZCode/Claude skill harness。测量方法学复刻自 **[Fleet (ASPLOS'24)](https://github.com/jiachengh/Fleet)**，但通用化为对任意已连接手机做性能基线测试——**不需要刷自编 AOSP、不需要 root**。

已在 **荣耀 BKQ-AN00 (Android 16, user 版, 无 root)** 上端到端实测验证。

## 能测什么

| 测试项 | 测什么 | 关键指标 | 对应 Fleet |
|---|---|---|---|
| `startup` | App 冷/热/温启动耗时 | TTID, TTFD, WaitTime, TotalTime, CDF | Exp-3 (Fig 13) |
| `jank` | 前台滚动帧率稳定性与掉帧 | FPS, jank ratio, jank_type 分解 | Exp-4 (Fig 14) |
| `cache` | 内存缓存容量（连开多 app 后剩几个） | 每步缓存数序列, 峰值缓存数, 每 app RSS/Heap | Exp-1 (Fig 11c) |
| `cpu`（可选） | CPU 开销，分离 mutator/GC 线程 | app/mutator/gc runtime | Exp-4 CPU |

## 工作流（四阶段）

```bash
cd scripts

# 阶段 0：探测设备能力 + 下载 trace_processor_shell + 装依赖 + 推 u2 agent
python -m apb setup --install-deps --init-u2

# 阶段 1：抓 perfetto trace + 跑 workload
python -m apb capture --type startup --app com.android.chrome --repeat 10 --run baseline
python -m apb capture --type jank    --app com.android.chrome --scroll-duration 30 --run baseline
python -m apb capture --type cache   --app-list "pkg1,pkg2,pkg3" --run baseline

# 阶段 2：用 trace_processor_shell 解析 trace，计算指标
python -m apb analyze --run baseline

# 阶段 3：生成对比报告（CSV/JSON + Markdown + HTML + 图）
python -m apb report --runs baseline.json,variant.json --baseline baseline
```

`capture` 默认会自动接 `analyze`；也可用 `python -m apb run` 串联。

报告输出到 `scripts/out/report/`：`report.md`（对比表格）+ `report.html` + `*.csv` + `*.png`（柱状/CDF/折线）。

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
        └── benchmarks/            # startup / jank_fps / cache_mem (+ cpu)
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

## 与 Fleet 的关系

Fleet 测量的是它自研 ART/Kernel（GC-Swap 协同设计）的收益，需刷自编 AOSP 镜像、Pixel 3 + root。本套件**复用其测量方法学**（trace 抓取、perfetto SQL、指标计算公式、CDF/柱状对比可视化），但**通用化**为任意手机的基线测试，对比维度换成"不同手机 / 不同系统配置 / 不同 app 版本"。Fleet 的 Exp-0（私有日志验证）和 Exp-2（需 debug 编译的 GC working set）不在本套件范围。

## 许可

MIT
