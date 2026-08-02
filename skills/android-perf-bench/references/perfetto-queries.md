# Perfetto SQL 查询手册 (perfetto-queries.md)

本套件 `trace_analyze.py` 调用 `trace_processor_shell` 执行的所有 SQL 查询。所有查询已在 Fleet notebook 验证过或来自 perfetto 官方标准库。

## trace_processor_shell 调用方式

```bash
# 跑 SQL 文件，输出 JSON
trace_processor_shell query -f queries.sql <trace.perfetto-trace> --json

# 跑单条 SQL
trace_processor_shell query <trace.perfetto-trace> "SELECT ..."

# 跑 metric（启动/jank/fps）
trace_processor_shell --run-metrics android_startup,android_jank,android_fps --metrics-output=json <trace>
```

Python 层用 `subprocess` 调用，解析 stdout JSON 的 `queryResult` 里的 rows。

---

## 1. App 启动时间

### 1a. am start -W 解析（不需 trace，直接解析 stdout）
```
Status: ok
Activity: com.example/.MainActivity
ThisTime: 1234
TotalTime: 1300
WaitTime: 1310
Complete: true
```
取 `TotalTime`、`WaitTime`，`LaunchState` 来自另一行（`am start -W` 在新版 Android 输出 `LaunchState: COLD`）。

### 1b. android_startup metric（从 trace）
```sql
INCLUDE PERFETTO MODULE android.startup.startups;
INCLUDE PERFETTO MODULE android.startup.time_to_display;

SELECT
  startup_id,
  package,
  startup_type,            -- cold / warm / hot
  dur,                      -- 启动总时长 ns（intent_received → first frame）
  time_to_initial_display AS ttid,   -- ns
  time_to_full_display     AS ttfd   -- ns（可能 NULL）
FROM android_startups
WHERE package = '<package>';
```

### 1c. 主线程调度状态分解
```sql
INCLUDE PERFETTO MODULE android.startup.startups;
INCLUDE PERFETTO MODULE android.startup.timestamps;

SELECT
  s.package,
  s.startup_type,
  s.running_dur AS running_ns,
  s.runnable_dur AS runnable_ns,
  s.uninterruptible_sleep_dur AS uninterruptible_ns
FROM android_startups s
WHERE s.package = '<package>';
```

> 字段名以实际 trace_processor 版本为准，若上述 view 不存在，用 metric：`trace_processor_shell --run-metrics android_startup <trace>` 输出的 protobuf/json 含 `android_startup.startup[*].{to_first_frame, time_to_full_display, dur}` 及主线程 `main_thread.{running_dur_ns, runnable_dur_ns, uninterruptible_sleep_dur_ns}`。

---

## 2. Jank / FPS（Fleet 方法：Choreographer#doFrame）

### 2a. 取 doFrame 时间戳（Fleet notebook 原始查询）
```sql
SELECT slice_id, track_id, ts, dur, slice.name AS slice_name, process.name AS process_name
FROM slice
JOIN thread_track ON slice.track_id = thread_track.id
JOIN thread USING(utid)
JOIN process USING(upid)
WHERE process.name = '<package>'
  AND slice.name = 'Choreographer#doFrame'
ORDER BY ts;
```
Python 后处理：
```python
ts_arr = sorted([row['ts'] for row in rows])
deltas = [ts_arr[i] - ts_arr[i-1] for i in range(1, len(ts_arr))]
jank_num = sum(1 for d in deltas if d/1e6 > 16.7)   # 阈值 16.7ms = 60Hz
jank_ratio = jank_num / len(deltas)
fps = len(ts_arr) / ((ts_arr[-1] - ts_arr[0]) / 1e9)
```

### 2b. FrameTimeline jank_type 分解（Android 12+，增强）
```sql
SELECT
  ts, dur,
  jank_type,
  on_time_finish,
  present_type,
  layer_name,
  process.name AS process_name
FROM actual_frame_timeline_slice
LEFT JOIN process USING(upid)
WHERE process.name = '<package>';
```
`jank_type` 取值：`None` / `AppDeadlineMissed` / `BufferStuffing` / `SurfaceFlingerCpuDeadlineMissed` / `SurfaceFlingerGpuDeadlineMissed` / `DisplayHAL` / `PredictionError` / `Unknown`

### 2c. 备用：android_jank / android_fps metric
```bash
trace_processor_shell --run-metrics android_jank,android_fps --metrics-output=json <trace>
```

---

## 3. CPU 开销分解（可选）

```sql
SELECT process.name AS process, thread.name AS thread, sum(dur) AS cpu_dur
FROM sched
INNER JOIN thread USING(utid)
INNER JOIN process USING(upid)
GROUP BY utid
ORDER BY cpu_dur DESC;
```
Python 归类：`thread == 'HeapTaskDaemon'` → gc，同 process 其余 → mutator。

---

## 4. 缓存 / 内存

无需 trace，解析 `adb shell dumpsys meminfo`：
- 顶层 `Total RAM:` / `Free RAM:` / `Used RAM:` 行
- 每行首 token 若在 app_list → 该 app 仍缓存
- 单 app 详情：`dumpsys meminfo <pkg>` 取 `RSS` / `Java Heap` / `Native Heap`

可选 mem trace metric：
```bash
trace_processor_shell --run-metrics android_mem --metrics-output=json <trace>
```

---

## trace 抓取配置要点

`templates/perfetto-config.textproto` 关键数据源：
- `linux.ftrace`：`sched/sched_switch`、`sched_waking` + atrace_categories `gfx/view/am/wm/ss/input` + `atrace_apps: "<pkg>"`
- `android.surfaceflinger.frametimeline`：FrameTimeline（jank_type 必备，Android 12+，**注意无 s**，user 版可用）
- `linux.process_stats`：`scan_all_processes_on_start` + `record_thread_names`
- buffer 用默认 RING_BUFFER（不丢数据），size_kb ≥ 256MB

降级（atrace，user 设备）：
```bash
adb shell atrace --async_start -b 32768 -a <pkg> gfx view am wm ss sched input
# ... 跑 workload ...
adb shell atrace --async_stop -o /data/local/tmp/trace.ftrace
adb pull /data/local/tmp/trace.ftrace
```
atrace 降级时无 FrameTimeline，jank 只能用 doFrame 阈值法。
