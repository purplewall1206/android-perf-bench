"""基线 vs 方案对比：把多个 run 的结果整理成表格友好的结构。

输入：report.py 读多个 <run>.json，按 type 分组，对每个指标算
  - 各 run 的统计值（mean/p50/p95...）
  - vs 基线的差值 / 改善百分比 / 加速比
"""
from __future__ import annotations

from typing import Optional


def _fmt(v, places: int = 2) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{places}f}"
    return str(v)


def startup_table(runs: dict[str, dict], baseline: str) -> dict:
    """runs: {run_name: analyze_run 结果}（仅 type==startup 的）。
    返回 {columns, rows}，每行一个 (app, launch_type) 组合。"""
    # 收集所有 (app, launch_type)
    keys = []
    seen = set()
    for run_data in runs.values():
        for item in run_data.get("items", []):
            k = (item.get("app"), item.get("launch_type"))
            if k not in seen:
                seen.add(k)
                keys.append(k)

    run_names = list(runs.keys())
    columns = ["app", "launch_type"] + [f"{rn}\nWaitTime(ms)" for rn in run_names]
    if len(run_names) > 1:
        columns += [f"vs {baseline}\nΔ(ms)", f"vs {baseline}\n改善%"]

    rows = []
    for app, lt in keys:
        row = {"app": app or "-", "launch_type": lt or "-"}
        base_wait = None
        for rn in run_names:
            data = runs[rn]
            wait = None
            for item in data.get("items", []):
                if item.get("app") == app and item.get("launch_type") == lt:
                    st = item.get("analysis", {}).get("am_stats", {}).get("wait_time_ms", {})
                    wait = st.get("mean")
                    break
            row[f"{rn}\nWaitTime(ms)"] = _fmt(wait)
            if rn == baseline:
                base_wait = wait
        if len(run_names) > 1 and base_wait not in (None, 0):
            # 取最后一个 run 做对比
            last = run_names[-1]
            last_wait = None
            for item in runs[last].get("items", []):
                if item.get("app") == app and item.get("launch_type") == lt:
                    last_wait = item.get("analysis", {}).get("am_stats", {}).get("wait_time_ms", {}).get("mean")
                    break
            if last_wait is not None:
                delta = last_wait - base_wait
                improve = -delta / base_wait * 100  # 负 delta = 更快 = 正改善
                row[f"vs {baseline}\nΔ(ms)"] = _fmt(delta)
                row[f"vs {baseline}\n改善%"] = _fmt(improve)
            else:
                row[f"vs {baseline}\nΔ(ms)"] = "-"
                row[f"vs {baseline}\n改善%"] = "-"
        rows.append(row)
    return {"columns": columns, "rows": rows}


def jank_table(runs: dict[str, dict], baseline: str) -> dict:
    run_names = list(runs.keys())
    columns = ["app"] + sum(
        ([f"{rn}\nFPS", f"{rn}\nJank%"] for rn in run_names), [])
    if len(run_names) > 1:
        columns += [f"vs {baseline}\nFPS Δ", f"vs {baseline}\nJank Δ"]

    # 收集 app
    apps = []
    seen = set()
    for run_data in runs.values():
        for item in run_data.get("items", []):
            a = item.get("app")
            if a and a not in seen:
                seen.add(a)
                apps.append(a)

    rows = []
    for app in apps:
        row = {"app": app}
        base_fps = base_jank = None
        for rn in run_names:
            data = runs[rn]
            fps = jank = None
            for item in data.get("items", []):
                if item.get("app") == app:
                    pr = item.get("analysis", {}).get("primary", {})
                    fps = pr.get("fps")
                    jank = pr.get("jank_ratio")
                    if jank is not None:
                        jank *= 100
                    break
            row[f"{rn}\nFPS"] = _fmt(fps)
            row[f"{rn}\nJank%"] = _fmt(jank)
            if rn == baseline:
                base_fps, base_jank = fps, jank
        if len(run_names) > 1:
            last = run_names[-1]
            lf = lj = None
            for item in runs[last].get("items", []):
                if item.get("app") == app:
                    pr = item.get("analysis", {}).get("primary", {})
                    lf = pr.get("fps")
                    lj = pr.get("jank_ratio")
                    break
            row[f"vs {baseline}\nFPS Δ"] = _fmt((lf - base_fps) if lf is not None and base_fps is not None else None)
            row[f"vs {baseline}\nJank Δ"] = _fmt(
                (lj * 100 - base_jank) if lj is not None and base_jank is not None else None,
                places=2)
        rows.append(row)
    return {"columns": columns, "rows": rows}


def keepalive_table(runs: dict[str, dict], baseline: str) -> dict:
    """keepalive 对比表：最低 MemAvailable / 峰值存活数 / 平均存活数。"""
    run_names = list(runs.keys())
    columns = ["指标"] + run_names
    if len(run_names) > 1:
        columns += [f"vs {baseline}"]

    def _get(run_data: dict, *keys):
        for item in run_data.get("items", []):
            an = item.get("analysis") or item
            for k in keys:
                if k in an:
                    return an[k]
        return None

    rows = []
    pairs = [
        ("最低 MemAvailable (MB)", "memavailable_min_kb", True),   # KB→MB
        ("峰值存活 app 数", "alive_max", False),
        ("平均存活 app 数", None, False),
    ]
    for label, key, to_mb in pairs:
        row = {"指标": label}
        base_val = None
        for rn in run_names:
            if key:
                v = _get(runs[rn], key)
                if to_mb and v:
                    v = round(v / 1024, 1)
            else:
                st = _get(runs[rn], "alive_stats")
                v = st.get("mean") if st else None
            row[rn] = _fmt(v, places=1)
            if rn == baseline:
                base_val = v
        if len(run_names) > 1:
            last = run_names[-1]
            if key:
                lv = _get(runs[last], key)
                if to_mb and lv:
                    lv = round(lv / 1024, 1)
            else:
                lst = _get(runs[last], "alive_stats")
                lv = lst.get("mean") if lst else None
            row[f"vs {baseline}"] = _fmt(round(lv - base_val, 1), places=1) if (lv is not None and base_val is not None) else "-"
        rows.append(row)
    return {"columns": columns, "rows": rows}


def camera_table(runs: dict[str, dict], baseline: str) -> dict:
    """camera_reload 对比表：相机启动延迟 / 相机后存活数 / 最低 MemAvailable。

    每个 run 一个 item（__camera_reload_summary__），从 analysis 读三个关键指标。
    """
    run_names = list(runs.keys())
    columns = ["指标"] + run_names
    if len(run_names) > 1:
        columns += [f"vs {baseline}"]

    def _get(run_data: dict, key: str):
        for item in run_data.get("items", []):
            an = item.get("analysis") or item
            if key in an:
                return an[key]
        return None

    rows = []
    metrics_pairs = [
        ("相机启动延迟 (ms)", "camera_launch_ms_mean"),
        ("相机后存活 app 数", "survived_after_camera_mean"),
        ("最低 MemAvailable (MB)", None),  # 特殊处理 KB→MB
    ]
    for label, key in metrics_pairs:
        row = {"指标": label}
        base_val = None
        for rn in run_names:
            if key:
                v = _get(runs[rn], key)
            else:
                kb = _get(runs[rn], "memavailable_min_kb")
                v = round(kb / 1024, 1) if kb else None
            row[rn] = _fmt(v, places=1)
            if rn == baseline:
                base_val = v
        if len(run_names) > 1 and base_val is not None:
            last = run_names[-1]
            if key:
                lv = _get(runs[last], key)
            else:
                lkb = _get(runs[last], "memavailable_min_kb")
                lv = round(lkb / 1024, 1) if lkb else None
            if lv is not None and base_val != 0:
                # 启动延迟/存活数：差值；MemAvailable：差值
                row[f"vs {baseline}"] = _fmt(round(lv - base_val, 1), places=1)
            else:
                row[f"vs {baseline}"] = "-"
        elif len(run_names) > 1:
            row[f"vs {baseline}"] = "-"
        rows.append(row)
    return {"columns": columns, "rows": rows}


def cache_table(runs: dict[str, dict], baseline: str) -> dict:
    run_names = list(runs.keys())
    # cache 每个 run 只有一个 item（__cache_summary__），取 cached_numbers 序列
    columns = ["step"] + [f"{rn}\n缓存数" for rn in run_names]
    if len(run_names) > 1:
        columns += [f"vs {baseline}\nΔ"]
    # 对齐到最长序列
    series = {}
    for rn in run_names:
        items = runs[rn].get("items", [])
        if items:
            series[rn] = items[0].get("cached_numbers", [])
        else:
            series[rn] = []
    max_len = max((len(v) for v in series.values()), default=0)
    base_series = series.get(baseline, [])
    rows = []
    for i in range(max_len):
        row = {"step": f"启动第{i+1}个"}
        for rn in run_names:
            v = series[rn][i] if i < len(series[rn]) else None
            row[f"{rn}\n缓存数"] = v if v is not None else "-"
        if len(run_names) > 1:
            b = base_series[i] if i < len(base_series) else None
            last = series.get(run_names[-1], [])
            l = last[i] if i < len(last) else None
            row[f"vs {baseline}\nΔ"] = (l - b) if (l is not None and b is not None) else "-"
        rows.append(row)
    # 汇总行
    summary_row = {"step": "峰值缓存数"}
    peaks = {rn: max(s) if s else 0 for rn, s in series.items()}
    for rn in run_names:
        summary_row[f"{rn}\n缓存数"] = peaks[rn]
    if len(run_names) > 1:
        summary_row[f"vs {baseline}\nΔ"] = peaks.get(run_names[-1], 0) - peaks.get(baseline, 0)
    return {"columns": columns, "rows": rows, "summary": f"峰值缓存数: " + ", ".join(f"{rn}={peaks[rn]}" for rn in run_names)}
