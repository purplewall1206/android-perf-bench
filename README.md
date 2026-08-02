# android-perf-bench

测量 **Android 手机性能**并生成"基线 vs 方案"对比报告的 **Claude Code plugin**。通过 adb + perfetto 自动化抓 trace、解析、生成 Markdown/HTML/CSV 报告。方法学复刻自 [Fleet (ASPLOS'24)](https://github.com/jiachengh/Fleet) 与 [ATC'26 A2](https://github.com/jiachengh/Fleet)，通用化为对任意已连接手机做基线测试——**不需要刷机、不需要 root**。

已在 **荣耀 BKQ-AN00 (Android 16, user 版, 无 root)** 上端到端实测验证。

---

## 4 个实验类型

| 实验 | 测什么 | 加压方式 | 预制（默认） | 一般测几组 | 核心指标 |
|---|---|---|---|---|---|
| **① 启动速度** `startup` | 单 app 冷/热/温启动 | 可选 `--background-apps N` 后台预跑 | 1 app × `--repeat 10` | 每 app 10 次 | WaitTime/TTID/TTFD + CDF |
| **② Jank** `jank` | 单 app 前台滚动帧率 | 可选 `--background-apps N` | 1 app × `--scroll-duration 30s` | 1 app × 30s | FPS + jank ratio + jank_type 分解 |
| **③ 相机重载** `camera` | 相机启动（最后启动） | N app 加压 → 最后相机 | N app × `--camera-repeat 3` 轮 | N app × 3 轮 | 相机启动延迟(apk→first buffer) + 相机后存活数 + MemAvailable |
| **④ 保活模型** `keepalive` | 系统保活能力 | N app 连续启动本身即加压 | N app(≤50) × `--ka-rounds 1`，每 app 前台30s+后台10s | N app × 1~3 轮 | 存活数曲线 + MemAvailable/vmstat/dumpsys 时序 + 每 app PSS |

> `cache` 已并入 keepalive（`--type cache` = keepalive 单轮 scroll 预设）。`cpu` 可选。

## 统一方法论

**所有测试遵循同一范式**：前序 app 加压（制造内存压力）→ 到达测试目标 → 测目标指标 + 宏观系统指标。

```
[前序 app 加压] ──→ [测试目标] ──→ [宏观指标采样]
   startup/jank: 可选 background-apps        am start -W / 滚动      MemAvailable/vmstat/存活数
   camera:       N app 依次启动                相机启动(最后)           + direct reclaim
   keepalive:    N app 连续启动(本身即加压)    每步采样                  + 每 app PSS
```

**宏观指标（所有实验都关注）**：MemAvailable 水位、vmstat 回收压力（pgmajfault/pswpin/pswpout/pgscan_direct/oom_kill）、存活后台 app 数。

## 快速开始

```bash
cd skills/android-perf-bench/scripts

# 1. 首次：探测设备 + 装工具 + 扫描 app
python -m apb setup --install-deps

# 2. 跑一个实验（以 jank 为例）
python -m apb capture --type jank --app com.android.chrome --run baseline

# 3. 对比两次（改个参数再跑一次）
python -m apb capture --type jank --app com.android.chrome --scroll-duration 60 --run variant
python -m apb report --runs baseline.json,variant.json
```

报告输出到 `out/report/`：`report.md`（对比表格）+ `report.html` + `*.csv` + `*.png`。

---

## 每个实验的详细规格

### ① 启动速度 `startup`

**测什么**：app 从启动到首帧（可显示/完全显示）的耗时，区分冷启动（进程不存在）、热启动（后台缓存）、温启动。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--app <pkg>` | 必填 | 目标 app |
| `--repeat N` | 10 | 重复启动次数（统计样本） |
| `--launch-type` | cold | cold / hot / both |
| `--background-apps N` | 0 | 启动前后台预跑 N 个 app 加压 |
| `--clear-data` | true(cold) | 冷启动前 pm clear |

**分析的指标**：WaitTime/TotalTime（am start -W）、TTID/TTFD（perfetto android_startup）、主线程调度分解（running/runnable/uninterruptible）。统计 mean/p50/p95 + CDF 图。

```bash
python -m apb capture --type startup --app com.android.chrome --repeat 10 --run baseline
# 加压对比：后台先开 5 个 app
python -m apb capture --type startup --app com.android.chrome --repeat 10 --background-apps 5 --run pressure
```

### ② Jank `jank`

**测什么**：app 前台滚动时的帧率稳定性与掉帧。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--app <pkg>` | 必填 | 目标 app |
| `--scroll-duration S` | 30 | 滚动秒数 |
| `--scroll-mode` | swipe | swipe / fling |
| `--background-apps N` | 0 | 后台加压 |

**分析的指标**：FPS、jank ratio、jank_type 分解（FrameTimeline：AppDeadlineMissed/BufferStuffing/SurfaceFlingerDeadlineMissed/DisplayHAL/PredictionError）、帧间隔分布。用 SurfaceFlinger FrameTimeline（Android 12+ user 版可用，**不需要 root**）。

```bash
python -m apb capture --type jank --app com.android.chrome --scroll-duration 30 --run baseline
```

### ③ 相机重载 `camera`

**测什么**：复刻 [ATC'26 A2 论文 Appendix A.1](https://github.com/jiachengh/Fleet)。N 个 app 依次启动加压 → **最后启动相机**制造内存压力峰值。完整加压流程（N app → 相机×1）重复多轮，轮间不清后台（压力累积）。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--app-list` | A2 论文 23-app（取已装交集） | 逗号分隔包名 |
| `--camera-repeat N` | 3 | 完整加压流程重复轮数（论文跑 500） |
| `--camera-use-duration S` | 5 | 每 app 前台秒数 |

**分析的指标**：相机启动延迟（apk 启动 → first full buffer，硬件返帧）、相机后存活 app 数、整轮 MemAvailable 曲线（min/mean）、direct reclaim 次数。相机包名按厂商自动探测（HONOR/HUAWEI/XIAOMI/...）。

```bash
python -m apb capture --type camera --run baseline
# 自定义 app 列表 + 跑 10 轮
python -m apb capture --type camera --app-list "p1,p2,p3" --camera-repeat 10 --run baseline
```

### ④ 保活模型 `keepalive`

**测什么**：N 个 app 连续启动（每 app 前台→后台→下一个），每步密集采样系统资源，观察系统在大量 app 保活压力下的内存管理——哪些 app 被回收、回收压力、各进程 PSS。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--app-list` | 候选池取已装（≤50） | 逗号分隔包名 |
| `--ka-target-count N` | 50 | 目标 app 数 |
| `--ka-foreground S` | 30 | 每 app 前台秒数 |
| `--ka-background-wait S` | 10 | 进后台后等待秒数 |
| `--ka-rounds N` | 1 | 重复轮数 |
| `--ka-workload` | idle | idle(等待) / scroll(滚动,原 cache) |

**分析的指标**：存活 app 数曲线（峰值/均值）、MemAvailable 时序、vmstat（pgmajfault/pswpin/pswpout/pgscan/oom_kill）、每 app PSS（dumpsys meminfo -S）。每步 3 个采样点（启动前/进桌面后/等待后）。

```bash
# 默认：候选池取设备已装 app（≤50），前台30s+后台10s
python -m apb capture --type keepalive --run baseline
# cache 预设（单轮滚动，原 cache_mem 行为）
python -m apb capture --type cache --app-list "p1,p2,p3" --run baseline
```

---

## 灵活配置负载

变化任一参数构成新 run，对比报告自动算差值：

| 场景 | 命令 |
|---|---|
| **加压对比启动** | `--background-apps 5`（后台预跑 5 app 抢内存） |
| **滚动时长对比 jank** | `--scroll-duration 10` vs `120` |
| **app 数量对比保活** | `--ka-target-count 10` vs `50` |
| **多轮对比保活** | `--ka-rounds 1` vs `3` |
| **多 app 对比** | 跑多个 `--run`（不同 app），`report --runs a.json,b.json` |
| **一键全量** | `python -m apb run --type all --app com.android.chrome` |

---

## perfetto trace 配置（全面数据源）

trace config 模板在 `templates/perfetto-config.textproto`，覆盖：

| 数据源 | 内容 | 用途 |
|---|---|---|
| `linux.ftrace` | sched_switch/waking、power/cpu_frequency、**vmscan/\***（direct reclaim）、compaction、binder | CPU 调度、内存回收压力 |
| `linux.ftrace` atrace | sched/gfx/view/am/wm/ss/input/audio/**video/camera**/res/dalvik/bionic | 全 atrace 类别（含 camera/video 硬件层） |
| `android.surfaceflinger.frametimeline` | actual/expected frame | jank + 相机 first buffer |
| `linux.process_stats` | scan_all_processes + **proc_stats_poll_ms:5000** | 进程信息 + 周期采样 |
| `android.packages_list` | 包列表 | 进程↔包名映射 |
| `android.log` | logcat（INFO+） | lmkd/OOM/相机错误日志 |

三 buffer 防覆盖：[0] ftrace（sched 海量）/ [1] process_stats+log / [2] FrameTimeline。配置放 `/data/misc/perfetto-configs/`（Android 12+ user 版 perfetto 只读此目录）。

trace 解析用 **perfetto python 库**（`TraceProcessor` API），非 subprocess。

---

## 安装

### 作为 Claude Code plugin
```bash
claude plugin marketplace add purplewall1206/android-perf-bench
claude plugin install android-perf-bench
# 或 clone 后 cd 到仓库目录
```
获得：skill（自动触发）、命令 `/android-perf-bench:perf-bench`、subagent `trace-analyst`、hooks（capture 前检查设备）。

### 独立 Python 工具
```bash
git clone https://github.com/purplewall1206/android-perf-bench
cd android-perf-bench/skills/android-perf-bench/scripts
pip install -r requirements.txt   # uiautomator2, perfetto, adbutils, pandas, matplotlib, jinja2
python -m apb setup               # 探测设备 + 下载 trace_processor_shell
```

**主机要求**：Python 3.10+、adb（platform-tools）在 PATH。
**设备要求**：Android 10+（FrameTimeline 需 12+）、开启 USB 调试。user 版即可（无 root 要求）。

依赖的 app 清单见 `DEPENDENCIES.md`（第一方按厂商自动适配，第三方列出包名供安装）。

---

## 目录结构

```
android-perf-bench/                  ← Claude Code plugin 根
├── .claude-plugin/plugin.json       # manifest
├── commands/perf-bench.md           # 斜杠命令
├── agents/trace-analyst.md          # trace 解析 subagent
├── hooks/                           # capture 前检查设备
├── skills/android-perf-bench/       # 核心 skill
│   ├── SKILL.md
│   ├── references/                  # fleet-methodology / benchmark-specs / perfetto-queries / troubleshooting
│   ├── templates/                   # perfetto-config.textproto（全面数据源）+ 报告模板
│   └── scripts/
│       └── apb/                     # python -m apb
│           ├── __main__.py          # CLI: setup/capture/analyze/report/run
│           ├── setup_env.py         # 设备探测 + trace_processor_shell + app扫描 + 后台清理探测
│           ├── device.py            # adb+u2（启动/采样/清理/手势）
│           ├── trace_capture.py     # perfetto 全面数据源抓取
│           ├── trace_analyze.py     # perfetto python 库解析
│           ├── metrics.py           # 指标计算（FrameTimeline/jank/相机first buffer/CPU）
│           ├── compare.py / report.py
│           └── benchmarks/          # startup / jank_fps / camera_reload / keepalive (+cpu)
├── DEPENDENCIES.md  README.md  LICENSE
```

## 许可

MIT
