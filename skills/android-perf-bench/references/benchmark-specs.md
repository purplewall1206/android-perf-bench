# 测试项详细规格 (benchmark-specs.md)

每个 benchmark 的**精确规格**：① 测试内容 ② 预制环境（含可配参数）③ 分析的数据 ④ CLI 参数。调参前必读。

---

## 测试项 1：App 启动时间 (`startup`)

**对应 Fleet**：Exp-3 (Figure 13a–n)

### ① 测试内容
测量 app 从启动到首帧（可显示 / 完全显示）的耗时，区分：
- **冷启动 (cold)**：进程不存在，需 fork zygote + 加载 class + inflate layout
- **热启动 (hot)**：进程在后台缓存，仅恢复 Activity
- **温启动 (warm)**：进程存在但 Activity 被回收

### ② 预制环境
| 参数 | 默认 | 说明 |
|---|---|---|
| `--app` | 必填 | 目标 app 包名（如 `com.android.chrome`） |
| `--repeat` | 10 | 每个 app 重复启动次数 |
| `--launch-type` | cold | `cold` / `hot` / `both` |
| `--background-apps` | 0 | 启动前在后台预跑 N 个 app 制造内存压力（复刻 Fleet `start_all_apps`）。**大内存设备建议设 8-10**（见 keepalive 对照表），不加压测不出压力下启动表现；未设置时 benchmark 会按内存给提示 |
| `--clear-data` | true(cold) | 冷启动前 `pm clear` 清数据 |
| `--cooldown` | 3s | 两次启动间冷却 |

**冷启动流程**：`pm clear <pkg>` → `am force-stop` → （可选）后台预跑 N app → 起 perfetto trace → `am start -W` → 等首帧 → 停 trace
**热启动流程**：`app_start` 跑前台 10s → `press home` 切后台缓存 → 等 `cooldown` → 起 trace → `am start -W` → 停 trace

### ③ 分析的数据（每 app 一组）
| 指标 | 来源 | 单位 |
|---|---|---|
| `WaitTime` / `TotalTime` | `am start -W` stdout | ms |
| `LaunchState` | `am start -W` stdout | COLD/HOT/WARM |
| `time_to_initial_display` (TTID) | android_startup metric | ns→ms |
| `time_to_full_display` (TTFD) | android_startup metric | ns→ms（可能为 null） |
| `dur` | android_startup | ns→ms |
| `running_dur_ns` / `runnable_dur_ns` / `uninterruptible_sleep_dur_ns` | android_startup 主线程调度分解 | ns→ms |
| 统计 | mean/std/min/max/p50/p95 | — |

**可视化**：CDF 图（复刻 Fleet Fig 13a–l）、按 app 柱状图

### ④ 报告列
`app | 启动类型 | TTID均值 | TTFD均值 | WaitTime均值 | [vs基线差值] | [加速比]`

### CLI
```bash
python -m apb capture --type startup --app com.android.chrome --repeat 10 --launch-type cold --run baseline
python -m apb capture --type startup --app com.android.chrome --launch-type both --background-apps 5 --run variant
```

---

## 测试项 2：帧率与 Jank (`jank`)

**对应 Fleet**：Exp-4 jank/fps (Figure 14)

### ① 测试内容
app 前台持续滚动时的帧率稳定性与掉帧情况。

### ② 预制环境
| 参数 | 默认 | 说明 |
|---|---|---|
| `--app` | 必填 | 目标 app |
| `--scroll-duration` | 30 | 滚动总秒数 |
| `--scroll-mode` | swipe | `swipe`（向上滑）/ `fling`（快速滚动） |
| `--scroll-scale` | 0.8 | 滑动幅度（屏宽比例） |
| `--background-apps` | 0 | 后台预跑 N app 抢内存（大内存设备建议 8-10，同 startup） |
| `--warmup` | 5s | 启动后等待稳定时间（等 idle + 关弹窗） |

**流程**：`app_start(pkg, stop=True)` 冷启动 → 等 idle + `watch_context` 关弹窗 → （可选）后台预跑 N app → 起 trace → `swipe_ext("up", scale)` 循环 `scroll_duration` 秒 → 停 trace

### ③ 分析的数据（每 app 一组）
| 指标 | 公式/来源 |
|---|---|
| **FPS** | 帧数 / ((末 ts - 首 ts)/1e9) |
| **jank ratio** | 相邻 `Choreographer#doFrame` 间隔 > 16.7ms 的帧数 / 总帧数 |
| **jank_type 分解**（Android 12+） | FrameTimeline：AppDeadlineMissed / BufferStuffing / SurfaceFlingerDeadlineMissed / PredictionError 各占比 |
| 帧间隔分布 | min / p50 / p95 / max (ms) |
| 统计 | mean FPS、mean jank ratio |

**注意**：jank_type 分解需 FrameTimeline（Android 12+，perfetto 数据源 `android.surfaceflinger.frametimelines`）。user 非 root 设备若用 atrace 降级，则无此分解，仅保留 doFrame 阈值法。

### ④ 报告列
`app | FPS均值 | jank ratio% | 帧间隔p95(ms) | 主要jank_type | [vs基线]`

### CLI
```bash
python -m apb capture --type jank --app com.android.chrome --scroll-duration 30 --run baseline
python -m apb capture --type jank --app com.twitter.android --scroll-duration 60 --background-apps 5 --run pressure
```

---

## 测试项 3：缓存容量 / 内存压力 (`cache`) — 已并入 keepalive

> **`cache` 已合并进 `keepalive`**（见下方测试项 4）。`--type cache` 现在是 keepalive 的轻量预设：
> 单轮 + scroll 模式（前台滚动而非等待）+ 每步采 per-app PSS。
>
> 原 cache 测的"缓存数序列"= keepalive 的 `alive_count` 时序；"峰值缓存数"= keepalive 的 `alive_max`。
>
> ```bash
> # 原 cache 行为 = 现在：
> python -m apb capture --type cache --app-list "p1,p2,p3" --use-duration 30 --run baseline
> # 等价于 keepalive 单轮 scroll：
> python -m apb capture --type keepalive --ka-workload scroll --ka-rounds 1 --app-list "p1,p2,p3" --run baseline
> ```

---

## 测试项 4（可选）：CPU 开销分解 (`cpu`)

**对应 Fleet**：Exp-4 CPU

### ① 测试内容
app 运行时 CPU 耗时，分离 mutator（应用逻辑）与 GC 线程。

### ② 预制环境
同 jank（前台滚动），trace 配置加 `sched/sched_switch`。默认**不启用**，需 `--enable-cpu` 或直接 `--type cpu`。

### ③ 分析的数据
按线程聚合 `sched` slice 时长：
- `thread.name == 'HeapTaskDaemon'` → GC
- 其余属该 process → mutator
- 归一化：`app_runtime / all_runtime × len(app_list)`

返回：`app_runtime, app_mutator_runtime, app_gc_runtime, all_runtime, all_mutator_runtime, all_gc_runtime`（秒）

### CLI
```bash
python -m apb capture --type cpu --app com.android.chrome --scroll-duration 30 --run baseline
```

---

## 测试项：重载相机 (camera_reload)

**对应论文**：ATC'26 A2 论文 Appendix A.1 (Shared Benchmark Workload)

### ① 测试内容
复刻 A2 论文的工业级内存压力基准（论文叫 "app-launch stress"，跑了 500 轮）：**完整加压流程 = 依次启动 N 个 app（每个做代表性动作后切后台）→ 最后启动相机一次制造内存压力峰值**；这个完整流程重复多轮，轮间不清后台（压力累积），统计系统在持续压力下的 keep-alive、相机启动、MemAvailable 表现。

### ② 预制环境
| 参数 | 默认 | 说明 |
|---|---|---|
| `--app-list` | 论文 23-app（取设备已装交集） | 逗号分隔的包名列表 |
| `--camera-use-duration` | 5 | 每个 app 前台使用秒数 |
| `--camera-interval` | 1 | 每个 app 启动间隔秒数（论文 A.1 用 1s） |
| `--camera-repeat` | 3 | **完整加压流程（N app → 相机×1）重复的轮数**（论文跑 500 轮） |

**单轮流程**（论文 A.1）：
1. 对 app_list 每个 app：`am start -W`（记启动时间）+ u2 兜底切前台 → 滚动使用 `camera_use_duration` 秒 → home 切后台 → 每步采样 `/proc/meminfo` 的 MemAvailable
2. 所有 app 启动后：统计存活数 + MemAvailable（相机前）
3. **启动相机一次**（本轮压力峰值）：`force-stop camera` → `am start -W camera`（记启动延迟）→ 等预览稳定触发内存压力 → 统计存活数 + MemAvailable（相机后）

整个单轮流程重复 `camera_repeat` 轮，**轮间不清后台**（压力累积，同论文 "without killing background"）。

**内存×app 数提示**：默认用论文 23-app 列表（取已装交集）。大内存设备（12G/16G）若已装 app 数低于 `recommended_app_count`（见 keepalive 测试项的对照表），benchmark 会提示用 `--app-list` 指定更多 app 充分加压——加压不足时相机制造的峰值压力不够，测不出系统在极限下的 keep-alive 差异。

**说明**：app 启动用 `resolve-activity` 解析真实 launcher activity + u2 `app_start` 兜底确保到前台（荣耀等 ROM 仅 `am start` 可能不切前台）。若 app 被其他保活 app 抢前台，跳过手势只等待——内存压力主要靠进程启动产生，不依赖手势。

### ③ 分析的数据（论文 A.1 的 5 个指标）
| 指标 | 来源 |
|---|---|
| 整轮 MemAvailable 均值/最低值 | `/proc/meminfo` 采样序列 |
| direct reclaim 次数 | trace 的 `vmscan/direct_reclaim_begin`（需开 vmscan ftrace；user 版可能无此事件→0） |
| **相机启动延迟** | `am start -W` 的 WaitTime（ms） |
| **相机启动后存活的后台 app 数** | `dumpsys meminfo` 扫包名 |
| 各 app 冷/热启动时间 | `am start -W` 的 WaitTime + LaunchState |

**可视化**：MemAvailable 随启动步数的折线图（论文 Figure 1 风格）

### ④ 报告列
`指标 | 各 run 的值 | vs 基线`（相机启动延迟 / 相机后存活数 / 最低 MemAvailable）

### CLI
```bash
# 默认：论文 23-app（自动取设备已装交集）+ 自动探测相机包名 + 完整流程跑 3 轮
python -m apb capture --type camera --run baseline

# 指定 app 列表 + 完整加压流程跑 10 轮（更稳定的统计）
python -m apb capture --type camera \
  --app-list "com.tencent.mm,com.ss.android.ugc.aweme,com.xingin.xhs" \
  --camera-repeat 10 --run baseline
```

### 与 Fleet Exp-1 (cache) 的区别
两者都"连开多 app 测缓存"，但 camera_reload 的**关键差异是最后启动相机制造内存压力峰值**——相机是内存消耗大户（预览缓冲、ISP），能逼出系统在极端压力下的 keep-alive 和启动表现，这是论文 A.1 的核心场景。

---

## 测试项：保活压力测试 (keepalive)

### ① 测试内容
N 个 app 启动多轮，每个 app 前台运行 → 进后台 → 等待 → 启动下一个。每步密集采样系统资源和存活 app 数，观察系统在大量 app 保活压力下的内存管理能力——哪些 app 被回收、回收压力（vmstat）、内存水位（meminfo）、各进程 PSS（dumpsys meminfo -S）。

**加压 app 数按设备内存自动推荐**（setup 探测 MemTotal，大内存设备需更多 app 才能压满制造真实压力）：

| 设备内存 | 推荐加压 app 数 |
|---|---|
| 4 GB | 8 |
| 6 GB | 12 |
| 8 GB | 18 |
| 12 GB | 30 |
| 16 GB | 35 |

经验值：每 app 保活约占 200-400MB（后台 RSS + 缓存），系统保留 3-4GB 给 kernel/system，剩余决定能压多少。候选池 68 个国内常见 app，跑时取设备已装交集；已装不足推荐数时 setup 会提示安装（见 DEPENDENCIES.md）。

### ② 预制环境
| 参数 | 默认 | 说明 |
|---|---|---|
| `--app-list` | 候选池取设备已装交集（68 个候选） | 逗号分隔包名；不传则用 KEEPALIVE_APP_POOL + 第一方槽位 |
| `--ka-target-count` | **按内存推荐**（见上表） | 目标 app 数（从已装交集取前 N 个）；显式指定则覆盖推荐 |
| `--ka-foreground` | 30 | 每 app 前台秒数 |
| `--ka-background-wait` | 10 | 进后台后等待秒数（再启动下一个） |
| `--ka-rounds` | 1 | 重复轮数 |
| `--ka-workload` | idle | idle(前台等待) / scroll(前台滚动,原 cache 行为) |

**单 app 流程**：启动 → 前台 `ka-foreground` 秒（idle 等待或 scroll 滚动）→ press home 进桌面 → **采样** → 等 `ka-background-wait` 秒 → **采样** → 下一个 app。

### ③ 分析的数据（每步 3 个采样点）
| 指标 | 来源 | 采样点 |
|---|---|---|
| **MemAvailable / Cached / Swap / AnonPages 等** | `/proc/meminfo` | pre_launch / post_home / post_bg_wait |
| **pgmajfault / pswpin / pswpout / pgscan / oom_kill** | `/proc/vmstat` | 同上 |
| **每进程 PSS** | `dumpsys meminfo -S` | 同上 |
| **存活后台 app 数** | `dumpsys meminfo` 扫包名 | 同上 |

**统计**：MemAvailable 均值/最低/最高、存活数 均值/峰值。
**可视化**：MemAvailable + 存活 app 数随启动步数变化（双 Y 轴折线图）。

### ④ 报告列
`指标 | 各 run 值 | vs 基线`（最低 MemAvailable / 峰值存活数 / 平均存活数）

### CLI
```bash
# 默认：候选池取设备已装 app（按内存推荐数，16G→35/12G→30/8G→18），前台 30s + 后台 10s，1 轮
python -m apb capture --type keepalive --run baseline

# 自定义：指定 8 个 app，前台 20s，后台 5s，跑 3 轮
python -m apb capture --type keepalive \
  --app-list "com.tencent.mm,com.ss.android.ugc.aweme,..." \
  --ka-foreground 20 --ka-background-wait 5 --ka-rounds 3 --run baseline
```

