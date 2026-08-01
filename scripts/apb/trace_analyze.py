"""阶段 2 — 解析 trace，计算指标。

用 subprocess 调 trace_processor_shell 跑 SQL（输出 JSON），再用 metrics.py 计算。
结果写到 out/results/<run>.json。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from . import config, metrics, setup_env


# ── trace_processor_shell 调用 ─────────────────────────────────────
def _tps_path(env: dict) -> str:
    p = env.get("trace_processor_shell") or ""
    if not p:
        # 兜底：现找
        import shutil
        p = shutil.which("trace_processor_shell") or shutil.which("trace_processor_shell.exe") or ""
    if not p:
        raise RuntimeError("trace_processor_shell 未找到，请先运行: python -m apb setup")
    return p


def query_sql(trace_path: str, sql: str, env: dict, timeout: int = 180,
              quiet: bool = False) -> list[dict]:
    """调 trace_processor_shell 跑一条 SQL，返回行列表（每行 dict）。

    用临时文件传 SQL（避免 shell 转义问题），读默认 CSV 输出并解析。
    quiet=True 时不打印错误（用于可选的补充查询）。
    """
    tps = _tps_path(env)
    # SQL 写到 Windows 可读路径（不用 /tmp，避免 MSYS 路径问题）
    import os
    sql_file = os.path.join(tempfile.gettempdir(), "apb_query.sql")
    with open(sql_file, "w", encoding="utf-8") as f:
        f.write(sql)
    cmd_env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    try:
        p = subprocess.run([tps, "query", "-f", sql_file, trace_path],
                           capture_output=True, text=True, timeout=timeout, env=cmd_env)
    finally:
        try:
            Path(sql_file).unlink(missing_ok=True)
        except OSError:
            pass

    if p.returncode != 0:
        if not quiet:
            print(f"[analyze] trace_processor_shell 失败 (rc={p.returncode}): {p.stderr[:300]}")
        return []
    return _parse_tps_csv(p.stdout)


def run_metric(trace_path: str, metric: str, env: dict, timeout: int = 180) -> dict:
    """跑一个 android_* metric，返回解析后的 JSON dict。best-effort。"""
    tps = _tps_path(env)
    cmd = [tps, "--run-metrics", metric, "--metrics-output=json", trace_path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        print(f"[analyze] metric {metric} 异常: {e}")
        return {}
    if p.returncode != 0:
        print(f"[analyze] metric {metric} 失败: {p.stderr[:200]}")
        return {}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}


def _parse_tps_csv(stdout: str) -> list[dict]:
    """解析 trace_processor_shell query 的默认 CSV 输出。

    格式：
        column 0 = <h0>          ← 表头声明行（可多行）
        column 1 = <h1>
        "<h0>","<h1>"            ← CSV 表头行
        "v00","v01"              ← 数据行
        [xxx] query.cc:...        ← 日志行（以 [ 开头）跳过
    """
    if not stdout.strip():
        return []
    import csv as _csv
    import io
    # 只保留非日志、非 "column N =" 声明的行
    lines = []
    for ln in stdout.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("["):           # 日志
            continue
        if s.startswith("column "):     # 表头声明
            continue
        lines.append(ln)
    if not lines:
        return []
    reader = _csv.reader(io.StringIO("\n".join(lines)))
    rows = list(reader)
    if len(rows) < 2:
        return []
    headers = rows[0]
    result = []
    for r in rows[1:]:
        result.append({headers[i]: (r[i] if i < len(r) else None) for i in range(len(headers))})
    return result


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
