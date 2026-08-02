"""指标计算（复刻 Fleet 公式）。

所有计算纯函数：输入原始行（dict 列表），输出指标 dict。
trace_analyze.py 调 trace_processor_shell 拿到行后，调这里的函数算指标。
"""
from __future__ import annotations

import statistics
from typing import Iterable

from . import config


def _stats(values: Iterable[float]) -> dict:
    """mean/std/min/max/p50/p95/count。空序列返回全 None。"""
    v = [x for x in values if x is not None]
    if not v:
        return {"mean": None, "std": None, "min": None, "max": None,
                "p50": None, "p95": None, "count": 0}
    sv = sorted(v)
    n = len(sv)
    return {
        "mean": statistics.mean(v),
        "std": statistics.pstdev(v) if n > 1 else 0.0,
        "min": sv[0],
        "max": sv[-1],
        "p50": sv[n // 2],
        "p95": sv[min(n - 1, int(n * 0.95))],
        "count": n,
    }


def ns_to_ms(x):
    return x / 1e6 if x is not None else None


# ── Jank / FPS（Fleet exp-runtime-performance.ipynb）──────────────
def jank_fps_from_doframe_rows(rows: list[dict]) -> dict:
    """rows: trace_processor 查询 Choreographer#doFrame 的结果，含 ts(nanoseconds)。"""
    ts_arr = sorted(int(r["ts"]) for r in rows if r.get("ts") is not None)
    if len(ts_arr) < 2:
        return {"fps": None, "jank_ratio": None, "frame_count": len(ts_arr),
                "frame_intervals_ms": _stats([]), "jank_count": 0,
                "method": "doframe_threshold"}
    deltas_ns = [ts_arr[i] - ts_arr[i - 1] for i in range(1, len(ts_arr))]
    deltas_ms = [d / 1e6 for d in deltas_ns]
    jank_count = sum(1 for d_ms in deltas_ms if d_ms > config.JANK_THRESHOLD_MS)
    total_s = (ts_arr[-1] - ts_arr[0]) / 1e9
    fps = (len(ts_arr) - 1) / total_s if total_s > 0 else None
    return {
        "fps": fps,
        "jank_ratio": jank_count / len(deltas_ms),
        "jank_count": jank_count,
        "frame_count": len(ts_arr),
        "frame_intervals_ms": _stats(deltas_ms),
        "method": "doframe_threshold",
        "threshold_ms": config.JANK_THRESHOLD_MS,
    }


def jank_type_breakdown(rows: list[dict]) -> dict:
    """rows: actual_frame_timeline_slice 查询结果，含 jank_type。"""
    total = 0
    counts: dict[str, int] = {}
    for r in rows:
        jt = (r.get("jank_type") or "None").strip()
        total += 1
        counts[jt] = counts.get(jt, 0) + 1
    if total == 0:
        return {"total": 0, "breakdown": {}}
    return {
        "total": total,
        "breakdown": {k: {"count": v, "ratio": v / total} for k, v in counts.items()},
    }


def jank_fps_from_frametimeline(rows: list[dict], target_pkg: str) -> dict:
    """从 actual_frame_timeline_slice 计算 jank/FPS（推荐主路径，Android 12+，user 版可用）。

    rows 需含 ts, dur, jank_type, layer_name（可选 on_time_finish/present_type）。
    过滤逻辑：只取 layer_name 含 target_pkg 的 app surface 帧（排除 SF display 帧）。
    - jank_ratio = jank_type != 'None' 的帧数 / 总帧数
    - FPS = 帧数 / ((末ts - 首ts)/1e9)
    """
    if not target_pkg:
        app_rows = rows
    else:
        # layer_name 形如 "TX - com.android.chrome/ChromeChildSurface#339272"
        app_rows = [r for r in rows
                    if target_pkg in (r.get("layer_name") or "")]
    if not app_rows:
        return {"available": False, "reason": "no app frames in FrameTimeline"}

    ts_arr = sorted(int(r["ts"]) for r in app_rows if r.get("ts") is not None)
    jank_count = sum(1 for r in app_rows
                     if (r.get("jank_type") or "None").strip() != "None")
    total_s = (ts_arr[-1] - ts_arr[0]) / 1e9 if len(ts_arr) >= 2 else 0
    fps = (len(ts_arr) - 1) / total_s if total_s > 0 else None
    return {
        "available": True,
        "method": "frametimeline",
        "frame_count": len(app_rows),
        "jank_count": jank_count,
        "jank_ratio": jank_count / len(app_rows),
        "fps": fps,
        "jank_type_breakdown": jank_type_breakdown(app_rows)["breakdown"],
    }


# ── 相机启动：apk → first full buffer ─────────────────────────────
# 相机启动不同于通用 app（需硬件返帧）。测量点：
#   起点：相机 apk 启动（am start 触发的 intent received）
#   终点：first full buffer —— SurfaceFlinger 收到相机预览的第一个完整 buffer
# 在 FrameTimeline 里，相机预览的 layer_name 通常含相机包名（如 com.hihonor.camera）。
# 注意：不能用 "SurfaceView" 作关键字——普通 app 也用 SurfaceView（游戏/视频），
# 会误匹配到前一个 app 的 layer。只匹配相机包名 + 相机特有的 layer 标识。
CAMERA_LAYER_KEYWORDS = ("camera", "Camera")


def camera_first_buffer(frametimeline_rows: list[dict], camera_pkg: str,
                        launch_ts_ns: int) -> dict:
    """从 FrameTimeline 找相机启动后的第一个 full buffer 帧。

    frametimeline_rows: actual_frame_timeline_slice 查询结果（含 ts/layer_name/jank_type）。
    camera_pkg: 相机包名（用于过滤 layer_name，如 com.hihonor.camera）。
    launch_ts_ns: 相机启动起点的 ts（ns），first buffer 必须 > 此值。
    返回 {first_buffer_ts_ns, first_buffer_ms, found, layer_name}。
    first_buffer_ms = (first_buffer_ts - launch_ts) / 1e6。
    """
    # 过滤相机相关 layer（包名匹配 或 SurfaceView 关键字）
    def _is_camera_layer(name: str) -> bool:
        name = name or ""
        if camera_pkg and camera_pkg in name:
            return True
        return any(kw.lower() in name.lower() for kw in CAMERA_LAYER_KEYWORDS)

    candidates = []
    for r in frametimeline_rows:
        ts = r.get("ts")
        layer = r.get("layer_name") or ""
        if ts is None:
            continue
        try:
            ts_int = int(ts)
        except (TypeError, ValueError):
            continue
        if ts_int <= launch_ts_ns:
            continue
        if _is_camera_layer(layer):
            candidates.append((ts_int, layer))
    if not candidates:
        return {"found": False, "reason": "no camera layer frames after launch"}
    candidates.sort(key=lambda x: x[0])
    first_ts, first_layer = candidates[0]
    return {
        "found": True,
        "first_buffer_ts_ns": first_ts,
        "first_buffer_ms": round((first_ts - launch_ts_ns) / 1e6, 1),
        "layer_name": first_layer,
    }


# ── 启动时间 ─────────────────────────────────────────────────────
def startup_from_am(am_result: dict) -> dict:
    """从 am start -W 解析结果抽取。"""
    return {
        "status": am_result.get("status"),
        "launch_state": am_result.get("launch_state"),
        "total_time_ms": am_result.get("total_time"),
        "wait_time_ms": am_result.get("wait_time"),
        "complete": am_result.get("complete"),
    }


def startup_from_metric(metric_rows: list[dict]) -> dict:
    """从 android_startup metric/sql 行抽取 TTID/TTFD/dur。

    兼容多种字段名（startup_type 或 type，ttid 或 time_to_initial_display）。
    """
    if not metric_rows:
        return {}
    r = metric_rows[-1]  # 取最后一条（最近的启动）
    def _get(*keys):
        for k in keys:
            if k in r and r[k] is not None:
                return r[k]
        return None
    dur = _get("dur")
    ttid = _get("ttid", "time_to_initial_display")
    ttfd = _get("ttfd", "time_to_full_display")
    return {
        "startup_type": _get("startup_type", "type"),
        "dur_ms": ns_to_ms(dur),
        "ttid_ms": ns_to_ms(ttid),
        "ttfd_ms": ns_to_ms(ttfd),
        "package": _get("package", "process_name"),
    }


# ── CPU mutator/GC 分解（Fleet AnalysisCPU）──────────────────────
def cpu_breakdown(rows: list[dict], target_pkg: str, n_apps: int = 1) -> dict:
    """rows: 按线程聚合的 sched slice (process, thread, cpu_dur_ns)。"""
    all_runtime = 0.0
    app_runtime = 0.0
    app_mutator = 0.0
    app_gc = 0.0
    all_gc = 0.0
    all_mutator = 0.0
    for r in rows:
        proc = (r.get("process") or "").strip()
        thr = (r.get("thread") or "").strip()
        dur = float(r.get("cpu_dur") or 0)
        all_runtime += dur
        if proc == target_pkg:
            app_runtime += dur
            if thr == config.GC_THREAD_NAME:
                app_gc += dur
            else:
                app_mutator += dur
        if thr == config.GC_THREAD_NAME:
            all_gc += dur
        elif proc:
            all_mutator += dur
    norm = (lambda x: x / all_runtime * n_apps) if all_runtime > 0 else (lambda x: 0.0)
    return {
        "app_runtime_s": app_runtime / 1e9,
        "app_mutator_s": app_mutator / 1e9,
        "app_gc_s": app_gc / 1e9,
        "all_runtime_s": all_runtime / 1e9,
        "all_mutator_s": all_mutator / 1e9,
        "all_gc_s": all_gc / 1e9,
        "app_cpu_normalized": norm(app_runtime),
        "app_mutator_cpu_normalized": norm(app_mutator),
        "app_gc_cpu_normalized": norm(app_gc),
    }


# ── 缓存容量（Fleet check_cached_apps）───────────────────────────
def cache_summary(cached_numbers: list[int]) -> dict:
    return {
        "cached_numbers": cached_numbers,
        "final": cached_numbers[-1] if cached_numbers else 0,
        "peak": max(cached_numbers) if cached_numbers else 0,
        "steps": len(cached_numbers),
    }


def cdf(values: list[float]) -> list[tuple[float, float]]:
    """CDF：排序后 (value, cumulative_probability)。复刻 Fleet notebook cdf()。"""
    sv = sorted(v for v in values if v is not None)
    n = len(sv)
    if n == 0:
        return []
    return [(sv[i], (i + 1) / n) for i in range(n)]
