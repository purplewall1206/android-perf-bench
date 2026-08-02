# Fleet 方法论笔记（ASPLOS'24）

本套件的测量方法学复刻自 Fleet 论文。本文件记录其原始 5 个实验、论文图表映射、关键公式，作为通用化套件的理论依据。

- **论文**：*More Apps, Faster Hot-Launch on Mobile Devices via Fore/Background-aware GC-Swap Co-design* (ASPLOS'24, DOI 10.1145/3620666.3651377)
- **代码**：https://github.com/jiachengh/Fleet
- **核心**：ART + Linux Kernel 协同设计（BGC 后台对象 GC + RGS 运行时引导 swap），目标"更多缓存 app"+"更快热启动"。报告 1.59× 更快热启动、1.21× 更多缓存 app。

## Fleet 五个实验 ↔ 论文图

| 实验 | 图 | 测什么 | 测量手段 | 本套件对应 |
|---|---|---|---|---|
| Exp-0 | Fig 6b/9/10 | 功能验证（RGS BFS/region、BGC card table） | `adb logcat \| grep jiacheng` 私有日志 | ❌ 不含（依赖私有日志字段） |
| Exp-1 | Fig 11c | App 缓存容量 | `dumpsys meminfo` 扫包名 | ✅ cache benchmark |
| Exp-2 | Fig 12a/12b | GC working set | 日志关键字 `GcWs=`/`MutatorWS=` | ❌ 不含（需 debug 编译） |
| Exp-3 | Fig 13a–n | 热启动时间分布 + heap 比例 vs 加速比 | `am start -W` 的 WaitTime/LaunchState | ✅ startup benchmark |
| Exp-4 | Fig 14 | 运行时性能：jank/FPS/CPU overhead | perfetto trace + trace_processor SQL | ✅ jank + cpu benchmark |

## 关键公式（已在 metrics.py 复刻）

### Jank / FPS（Exp-4，来自 `exp-runtime-performance.ipynb`）
```sql
SELECT slice_id, track_id, ts, dur, slice.name, process.name
FROM slice
JOIN thread_track ON slice.track_id = thread_track.id
JOIN thread USING(utid)
JOIN process USING(upid)
WHERE process.name = '<package>' AND slice.name = 'Choreographer#doFrame'
ORDER BY ts;
```
- 取所有 doFrame 的 `ts`，排序
- 帧间隔 `delta[i] = ts[i] - ts[i-1]`（单位 ns）
- **jank 阈值**：`delta/1e6 > 16.7`（即 60Hz 一帧 16.7ms）→ 计为 jank
- **jank ratio** = jank 帧数 / 总间隔数
- **FPS** = 帧数 / ((末 ts - 首 ts)/1e9)

### CPU 开销 mutator/GC 分解（Exp-4，`AnalysisCPU`）
```sql
SELECT process.name AS process, thread.name AS thread, sum(dur) AS cpu_dur
FROM sched
INNER JOIN thread USING(utid)
INNER JOIN process USING(upid)
GROUP BY utid
ORDER BY cpu_dur DESC;
```
- `thread.name == 'HeapTaskDaemon'` → GC 线程
- 其余属于该 process 的 → mutator
- 归一化：`app_runtime / all_runtime × len(app_list)`

### 启动时间（Exp-3，`parsing_adb_am_result`）
`am start -W` 输出三行解析：
- `Status: ok`
- `LaunchState: HOT|COLD|WARM`
- `WaitTime: <ms>`
归类到 hot/cold/other 三个列表。

### 缓存容量（Exp-1，`check_cached_apps`）
解析 `adb shell dumpsys meminfo`，对每行 split 空格，若 token 在 `app_list` 中则计入缓存集合。

## Fleet 设备与系统（仅供参考，本套件不要求）

- 设备：Pixel 3 (blueline)，AOSP android-10，kernel 4.9
- 三套对比镜像：Fleet / Original Android / Marvin
- 通用修改：bypass security checks + 禁用 lmkd
- 18 个被测 app（Twitter/Facebook/Instagram/.../CandyCrush）

## Exp-4 真实参考数值（来自 plot notebook，可作 sanity check）

FPS 示例（单位 fps，三系统对比）：

| App | Android | Marvin | Fleet |
|---|---|---|---|
| Twitter | 43.88 | 39.73 | 42.37 |
| Facebook | 48.51 | 38.84 | 49.33 |
| Chrome | 42.67 | 33.88 | 48.39 |
| ... | ... | ... | ... |

本套件复刻这套"CDF + 三列柱状对比"可视化（`compare.py` + `report.py`），对比维度换成任意 run（基线/方案/...）。

## Exp-2 GC Working Set（本套件不含，记录供参考）

需重编译 Fleet 开 `JIACHENG_DEBUG`。日志字段：
- `GcWs=`：GC 工作集对象数
- `MutatorWs=`：mutator 工作集对象数
- `card_scan_num2`：BGC 扫描的 card 数

时间线：前台 180s → 后台 300s → 前台 120s，按 app tag 切分日志，取后台段（marker `Runphases() End * GetGcCause()= RelocateHotness *` 到 `UpdateProcessState() jank_perceptible=1`）算 GcWs 均值/std。

参考结果：BGC 使 GC 工作集从 ~718k 降到 ~104k 对象（约 7×）。
