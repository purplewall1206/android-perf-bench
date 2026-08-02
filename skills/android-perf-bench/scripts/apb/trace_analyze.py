"""阶段 2 — 解析 trace，计算指标。

用 perfetto python 库（TraceProcessor API）跑 SQL，再用 metrics.py 计算。
结果写到 out/results/<run>.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from . import config, metrics, setup_env


# ── perfetto TraceProcessor（python 库）─────────────────────────────
_tp_cache: dict[str, object] = {}  # trace_path → TraceProcessor 实例（同一 trace 复用）


def _get_tp(trace_path: str, env: dict):
    """获取/复用 TraceProcessor 实例。用 bin_path 指定本地 trace_processor_shell，避免自动下载。"""
    if trace_path in _tp_cache:
        return _tp_cache[trace_path]
    from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig
    bin_path = env.get("trace_processor_shell") or ""
    cfg = TraceProcessorConfig(bin_path=bin_path) if bin_path else TraceProcessorConfig()
    tp = TraceProcessor(trace=trace_path, config=cfg)
    _tp_cache[trace_path] = tp
    return tp


def query_sql(trace_path: str, sql: str, env: dict, timeout: int = 180,
              quiet: bool = False) -> list[dict]:
    """用 perfetto TraceProcessor 跑 SQL，返回行列表（每行 dict，列名→值）。

    同一 trace 复用 tp 实例（性能）。失败返回空列表。
    """
    try:
        tp = _get_tp(trace_path, env)
        df = tp.query(sql).as_pandas_dataframe()
        # 转 list[dict]，NaN→None
        return [{k: (None if (v != v) else v) for k, v in row.items()}
                for row in df.to_dict("records")]
    except Exception as e:
        if not quiet:
            print(f"[analyze] query 失败: {str(e)[:300]}")
        return []


# ── 各实验的 SQL（来自 references/perfetto-queries.md）─────────────
SQL_DOFRAME = """
SELECT ts, dur, slice.name AS slice_name, process.name AS process_name
FROM slice
JOIN thread_track ON slice.track_id = thread_track.id
JOIN thread USING(utid)
JOIN process USING(upid)
WHERE slice.name = 'Choreographer#doFrame' AND process.name = '{pkg}'
ORDER BY ts;
"""

SQL_FRAMETIMELINE = """
SELECT ts, dur, jank_type, on_time_finish, present_type, layer_name
FROM actual_frame_timeline_slice;
"""

# direct reclaim 次数（论文 A.1 指标 2，需 trace 开启 vmscan ftrace 事件；user 版可能无此事件→返回 0）
SQL_DIRECT_RECLAIM = """
SELECT count(*) AS direct_reclaim_count
FROM counters
WHERE name = 'vmscan/direct_reclaim_begin';
"""

# 相机进程在 trace 里的最早 slice ts（近似 apk 启动时刻，作为 first buffer 起点）
SQL_PROCESS_FIRST_TS = """
SELECT min(ts) AS ts
FROM slice
JOIN thread_track ON slice.track_id = thread_track.id
JOIN thread USING(utid)
JOIN process USING(upid)
WHERE process.name = '{pkg}';
"""

SQL_CPU = """
SELECT process.name AS process, thread.name AS thread, sum(dur) AS cpu_dur
FROM sched
INNER JOIN thread USING(utid)
INNER JOIN process USING(upid)
GROUP BY utid
ORDER BY cpu_dur DESC;
"""

SQL_STARTUP = """
INCLUDE PERFETTO MODULE android.startup.startups;
INCLUDE PERFETTO MODULE android.startup.time_to_display;
SELECT startup_id, package, startup_type, dur,
       time_to_initial_display AS ttid,
       time_to_full_display AS ttfd
FROM android_startups
WHERE package = '{pkg}';
"""


def _short(pkg: str) -> str:
    return pkg.split(".")[-1] if pkg else ""


# ── 分析单个 trace ─────────────────────────────────────────────────
def analyze_startup_trace(trace_path: str, pkg: str, am_results: list[dict], env: dict) -> dict:
    """启动时间分析：am start -W 结果 + trace 的 android_startup。"""
    out = {"app": pkg, "am_runs": [], "am_stats": {}, "trace": None}
    waits = []
    for am in am_results:
        sm = metrics.startup_from_am(am)
        out["am_runs"].append(sm)
        if sm["wait_time_ms"] is not None:
            waits.append(sm["wait_time_ms"])
    out["am_stats"]["wait_time_ms"] = metrics._stats(waits)
    out["am_stats"]["total_time_ms"] = metrics._stats(
        [r["total_time_ms"] for r in out["am_runs"] if r["total_time_ms"] is not None])

    # trace 侧（若有）
    try:
        rows = query_sql(trace_path, SQL_STARTUP.format(pkg=pkg), env, quiet=True)
        if rows:
            out["trace"] = metrics.startup_from_metric(rows)
    except Exception as e:
        out["trace_error"] = str(e)
    return out


def analyze_jank_trace(trace_path: str, pkg: str, env: dict) -> dict:
    """jank/FPS 分析：FrameTimeline 为主（Android 12+，user 版可用），doFrame 阈值法为备选。"""
    out = {"app": pkg}
    # 主路径：FrameTimeline
    ft_rows = query_sql(trace_path, SQL_FRAMETIMELINE, env)
    out["frametimeline"] = metrics.jank_fps_from_frametimeline(ft_rows, pkg)
    # 备选路径：doFrame 阈值法（需 app debuggable 或 root，多数 user 版抓不到）
    rows = query_sql(trace_path, SQL_DOFRAME.format(pkg=pkg), env)
    out["doframe"] = metrics.jank_fps_from_doframe_rows(rows)
    # 选定主结果（供 report 使用）：优先 FrameTimeline，否则 doFrame
    if out["frametimeline"].get("available"):
        out["primary"] = {k: out["frametimeline"][k] for k in ("fps", "jank_ratio", "frame_count", "jank_count", "method")}
        out["primary"]["jank_type_breakdown"] = out["frametimeline"].get("jank_type_breakdown", {})
    elif out["doframe"].get("frame_count", 0) >= 2:
        df = out["doframe"]
        out["primary"] = {"fps": df["fps"], "jank_ratio": df["jank_ratio"],
                          "frame_count": df["frame_count"], "jank_count": df["jank_count"],
                          "method": df["method"]}
    else:
        out["primary"] = {"available": False, "reason": "no usable jank data (FrameTimeline 空 + doFrame 空)"}
    return out


def analyze_cpu_trace(trace_path: str, pkg: str, n_apps: int, env: dict) -> dict:
    rows = query_sql(trace_path, SQL_CPU, env)
    return {"app": pkg, **metrics.cpu_breakdown(rows, pkg, n_apps)}


def analyze_camera_trace(trace_path: str, env: dict, camera_pkg: str = "") -> dict:
    """从相机阶段的 trace 提取：direct reclaim + 相机 first full buffer。

    多数指标（am 测的启动延迟、存活数、MemAvailable）已在 capture 阶段算好，
    trace 补充：
      - direct reclaim 次数（vmscan ftrace，论文 A.1 指标）
      - 相机 first full buffer（apk 启动 → SurfaceFlinger 收到相机预览首帧）
    """
    out = {}
    # direct reclaim
    try:
        rows = query_sql(trace_path, SQL_DIRECT_RECLAIM, env, quiet=True)
        if rows:
            out["direct_reclaim_count"] = int(rows[0].get("direct_reclaim_count") or 0)
    except Exception:
        pass

    # 相机 first full buffer：需 camera_pkg 定位启动起点 + FrameTimeline 找首帧
    if camera_pkg:
        try:
            # 起点：相机进程在 trace 里的第一个 slice ts（近似 apk 启动时刻）
            launch_rows = query_sql(trace_path,
                SQL_PROCESS_FIRST_TS.format(pkg=camera_pkg), env, quiet=True)
            launch_ts = int(launch_rows[0]["ts"]) if launch_rows and launch_rows[0].get("ts") else 0
            if launch_ts:
                ft_rows = query_sql(trace_path, SQL_FRAMETIMELINE, env, quiet=True)
                fb = metrics.camera_first_buffer(ft_rows, camera_pkg, launch_ts)
                if fb.get("found"):
                    out["first_full_buffer_ms"] = fb["first_buffer_ms"]
                    out["first_full_buffer_layer"] = fb["layer_name"]
        except Exception as e:
            out["first_buffer_error"] = str(e)[:200]
    return out


# ── run 级汇总 ─────────────────────────────────────────────────────
def analyze_run(run_name: str, env: dict) -> dict:
    """读取 out/results/<run>.raw.json（capture 阶段产出），补充 trace 分析，写出 <run>.json。"""
    raw_path = config.RESULT_DIR / f"{run_name}.raw.json"
    if not raw_path.exists():
        print(f"[analyze] 未找到 {raw_path}，请先运行 capture")
        return {}
    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    bench_type = raw.get("type")
    result = {"run": run_name, "type": bench_type, "device": raw.get("device"),
              "items": raw.get("items", [])}

    if bench_type == "startup":
        for item in result["items"]:
            tr = item.get("trace")
            if tr and Path(tr).exists():
                item["analysis"] = analyze_startup_trace(
                    tr, item["app"], item.get("am_results", []), env)
    elif bench_type in ("jank", "cpu"):
        for item in result["items"]:
            tr = item.get("trace")
            if tr and Path(tr).exists():
                if bench_type == "jank":
                    item["analysis"] = analyze_jank_trace(tr, item["app"], env)
                else:
                    item["analysis"] = analyze_cpu_trace(
                        tr, item["app"], raw.get("n_apps", 1), env)
    elif bench_type == "cache":
        # cache 无 trace，metrics 已在 capture 阶段算好
        for item in result["items"]:
            item.setdefault("analysis",
                            metrics.cache_summary(item.get("cached_numbers", [])))
    elif bench_type == "camera":
        # camera 主要指标在 capture 阶段已算好；trace 补充 direct reclaim + first full buffer
        for item in result["items"]:
            analysis = {k: v for k, v in item.items()
                        if k in ("mem_stats", "pre_camera", "camera_results",
                                 "camera_launch_ms_mean", "survived_after_camera_mean",
                                 "memavailable_min_kb", "launch_times")}
            tr = item.get("trace")
            cam_pkg = item.get("camera_pkg", "")
            if tr and Path(tr).exists():
                analysis.update(analyze_camera_trace(tr, env, cam_pkg))
            item["analysis"] = analysis
    elif bench_type == "keepalive":
        # keepalive 指标在 capture 阶段已算好（samples + launch_records + mem/alive 统计）
        for item in result["items"]:
            item["analysis"] = {k: v for k, v in item.items()
                                if k in ("params", "samples", "launch_records",
                                         "mem_stats", "alive_stats",
                                         "memavailable_min_kb", "alive_max")}

    out_path = config.RESULT_DIR / f"{run_name}.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[analyze] ✓ {out_path}")
    return result


# ── CLI ────────────────────────────────────────────────────────────
def main(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    env = setup_env.load_env()
    if args.trace:
        # 单 trace 模式：自动判定 jank（最通用）
        pkg = args.run
        print(f"[analyze] 单 trace 模式：作为 jank 分析 {args.trace}")
        res = {"run": args.run, "type": "jank",
               "items": [{"app": "unknown", "trace": args.trace,
                          "analysis": analyze_jank_trace(args.trace, "unknown", env)}]}
        out_path = config.RESULT_DIR / f"{args.run}.json"
        out_path.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[analyze] ✓ {out_path}")
        return 0
    analyze_run(args.run, env)
    return 0
