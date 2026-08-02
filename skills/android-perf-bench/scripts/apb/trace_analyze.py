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
              quiet: bool = False, raise_on_error: bool = False) -> list[dict]:
    """用 perfetto TraceProcessor 跑 SQL，返回行列表（每行 dict，列名→值）。

    同一 trace 复用 tp 实例（性能）。默认失败返回空列表；raise_on_error=True 时抛出，
    供调用方区分"查询报错"与"查询成功但无数据"（避免 trace 分析静默变 None）。
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
        if raise_on_error:
            raise
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

# MemAvailable 时序曲线（从 process_stats 的 meminfo counters；替代 camera_reload 删掉的 _memavailable）
# counters 表的 meminfo 字段：MemAvailable 等，由 traced_probes 周期采样写入
SQL_MEMAVAILABLE_CURVE = """
SELECT ts, value AS memavailable_kb
FROM counters
WHERE name = 'MemAvailable'
ORDER BY ts;
"""

# 本轮 trace 期间存活的目标 app 进程数（从 process 表，替代删掉的 cached_apps）
SQL_ALIVE_APPS = """
SELECT COUNT(DISTINCT name) AS alive_count
FROM process
WHERE name IN ({pkgs});
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
SELECT s.startup_id, s.package, s.startup_type, s.dur,
       t.time_to_initial_display AS ttid,
       t.time_to_full_display AS ttfd
FROM android_startups s
LEFT JOIN android_startup_time_to_display t USING (startup_id)
WHERE s.package = '{pkg}'
ORDER BY s.ts;
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

    # trace 侧（若有）。raise_on_error 区分"SQL 报错"（如 perfetto 版本 schema 不兼容）
    # 与"查询成功但 trace 里没有启动事件"，前者显式记 trace_error，不静默吞掉。
    try:
        rows = query_sql(trace_path, SQL_STARTUP.format(pkg=pkg), env,
                         quiet=True, raise_on_error=True)
        if rows:
            out["trace"] = metrics.startup_from_metric(rows)
    except Exception as e:
        out["trace"] = None
        out["trace_error"] = str(e)[:300]
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


def analyze_camera_trace(trace_path: str, env: dict, camera_pkg: str = "",
                         target_apps: list[str] | None = None) -> dict:
    """从单轮 camera trace 提取：direct reclaim + 相机 first buffer + MemAvailable 曲线 + 存活数。

    camera_reload 已删掉单独 proc 采样（_memavailable/cached_apps），全部改从 perfetto 提取。
    """
    out = {}
    # direct reclaim
    try:
        rows = query_sql(trace_path, SQL_DIRECT_RECLAIM, env, quiet=True)
        if rows:
            out["direct_reclaim_count"] = int(rows[0].get("direct_reclaim_count") or 0)
    except Exception:
        pass

    # MemAvailable 曲线（从 process_stats counters）
    try:
        ma_rows = query_sql(trace_path, SQL_MEMAVAILABLE_CURVE, env, quiet=True)
        if ma_rows:
            ma_values = [int(r["memavailable_kb"]) for r in ma_rows
                         if r.get("memavailable_kb") is not None]
            if ma_values:
                out["memavailable_curve"] = [{"ts": r.get("ts"), "kb": int(r["memavailable_kb"])}
                                             for r in ma_rows]
                out["memavailable_min_kb"] = min(ma_values)
                out["memavailable_mean_kb"] = round(sum(ma_values) / len(ma_values), 1)
    except Exception:
        pass

    # 相机后存活的目标 app 数（从 process 表）
    if target_apps:
        try:
            pkgs = ",".join(f"'{p}'" for p in target_apps)
            alive_rows = query_sql(trace_path, SQL_ALIVE_APPS.format(pkgs=pkgs), env, quiet=True)
            if alive_rows:
                out["alive_apps_count"] = int(alive_rows[0].get("alive_count") or 0)
        except Exception:
            pass

    # 相机 first full buffer
    if camera_pkg:
        try:
            launch_rows = query_sql(trace_path,
                SQL_PROCESS_FIRST_TS.format(pkg=camera_pkg), env, quiet=True)
            launch_ts = int(launch_rows[0]["ts"]) if launch_rows and launch_rows[0].get("ts") else 0
            if launch_ts:
                ft_rows = query_sql(trace_path, SQL_FRAMETIMELINE, env, quiet=True)
                fb = metrics.camera_first_buffer(ft_rows, camera_pkg, launch_ts)
                if fb.get("found"):
                    out["first_full_buffer_ms"] = fb["first_full_buffer_ms"]
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
            if bench_type == "jank":
                # jank 多轮：分析每轮 trace，聚合 FPS/jank 统计
                traces = item.get("traces") or ([item["trace"]] if item.get("trace") else [])
                per_round, all_fps, all_jank = [], [], []
                for idx, tr in enumerate(traces):
                    if tr and Path(tr).exists():
                        an = analyze_jank_trace(tr, item["app"], env)
                        an["round"] = idx + 1
                        per_round.append(an)
                        p = an.get("primary", {})
                        if p.get("fps") is not None:
                            all_fps.append(p["fps"])
                        if p.get("jank_ratio") is not None:
                            all_jank.append(p["jank_ratio"])
                item["analysis"] = {
                    "per_round": per_round,
                    "primary": per_round[-1]["primary"] if per_round else {},
                    "fps_mean": round(sum(all_fps)/len(all_fps), 1) if all_fps else None,
                    "jank_ratio_mean": round(sum(all_jank)/len(all_jank), 3) if all_jank else None,
                }
            else:
                tr = item.get("trace")
                if tr and Path(tr).exists():
                    item["analysis"] = analyze_cpu_trace(
                        tr, item["app"], raw.get("n_apps", 1), env)
    elif bench_type == "cache":
        # cache 无 trace，metrics 已在 capture 阶段算好
        for item in result["items"]:
            item.setdefault("analysis",
                            metrics.cache_summary(item.get("cached_numbers", [])))
    elif bench_type == "camera":
        # camera 每轮一个 trace；遍历 camera_results 分析每轮
        for item in result["items"]:
            target_apps = item.get("app_list", [])
            cam_pkg = item.get("camera_pkg", "")
            per_round = []
            for cr in item.get("camera_results", []):
                tr = cr.get("trace")
                rd_analysis = {"round": cr.get("round"),
                               "camera_wait_time_ms": cr.get("camera_wait_time_ms")}
                if tr and Path(tr).exists():
                    rd_analysis.update(analyze_camera_trace(tr, env, cam_pkg, target_apps))
                per_round.append(rd_analysis)
            item["analysis"] = {
                "camera_launch_ms_mean": item.get("camera_launch_ms_mean"),
                "per_round": per_round,
                # 聚合：first buffer 均值、MemAvailable 最低、存活均值
                "first_full_buffer_ms_mean": (
                    round(sum(r["first_full_buffer_ms"] for r in per_round
                              if r.get("first_full_buffer_ms") is not None) / len(per_round), 1)
                    if any(r.get("first_full_buffer_ms") is not None for r in per_round) else None),
                "memavailable_min_kb": min(
                    (r["memavailable_min_kb"] for r in per_round
                     if r.get("memavailable_min_kb") is not None), default=None),
                "alive_apps_mean": (
                    round(sum(r["alive_apps_count"] for r in per_round
                              if r.get("alive_apps_count") is not None) / len(per_round), 1)
                    if any(r.get("alive_apps_count") is not None for r in per_round) else None),
            }
    elif bench_type == "keepalive":
        # keepalive 不录 perfetto；统计值在 capture 算好，原始时序在 proc_file
        for item in result["items"]:
            item["analysis"] = {k: v for k, v in item.items()
                                if k in ("params", "launch_records",
                                         "mem_stats", "alive_stats",
                                         "memavailable_min_kb", "alive_max", "proc_file")}

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
