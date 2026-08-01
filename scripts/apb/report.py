"""阶段 3 — 生成报告：CSV/JSON + Markdown + HTML + 图。

读多个 <run>.json，用 compare.py 整理成表格，渲染 jinja2 模板，
matplotlib 出图（柱状/CDF/折线），全部写到 out/report/。
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from . import compare, config


def _load_run(name: str) -> dict:
    p = config.RESULT_DIR / f"{name}.json"
    if not p.exists():
        # 尝试带后缀
        for suffix in ("_jank", "_cache", "_cpu"):
            alt = config.RESULT_DIR / f"{name}{suffix}.json"
            if alt.exists():
                return json.loads(alt.read_text(encoding="utf-8"))
        raise SystemExit(f"未找到 run 结果 {p}（请先 capture+analyze）")
    return json.loads(p.read_text(encoding="utf-8"))


def _group_by_type(runs: dict[str, dict]) -> dict[str, dict[str, dict]]:
    """按 benchmark type 分组：{type: {run_name: run_data}}。
    cache 的 run 文件名带 _cache 后缀，jank 不带；统一识别。"""
    groups: dict[str, dict[str, dict]] = {}
    for rn, data in runs.items():
        t = data.get("type", "unknown")
        groups.setdefault(t, {})[rn] = data
    return groups


def _plot_startup_cdf(runs: dict[str, dict], out_path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    fig, ax = plt.subplots(figsize=(7, 4))
    plotted = False
    for rn, data in runs.items():
        for item in data.get("items", []):
            waits = [r.get("wait_time_ms") for r in item.get("analysis", {}).get("am_runs", [])
                     if r.get("wait_time_ms") is not None]
            if not waits:
                continue
            sw = sorted(waits)
            n = len(sw)
            ax.plot(sw, [(i + 1) / n for i in range(n)],
                    label=f"{rn}/{item.get('launch_type')}")
            plotted = True
    if not plotted:
        plt.close(fig)
        return False
    ax.set_xlabel("WaitTime (ms)")
    ax.set_ylabel("CDF")
    ax.set_title("App Startup Time CDF")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return True


def _plot_jank_bars(runs: dict[str, dict], out_path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return False
    apps = sorted({item.get("app") for data in runs.values()
                   for item in data.get("items", []) if item.get("app")})
    run_names = list(runs.keys())
    if not apps or not run_names:
        return False
    fps = {rn: [] for rn in run_names}
    for rn, data in runs.items():
        for app in apps:
            v = None
            for item in data.get("items", []):
                if item.get("app") == app:
                    v = item.get("analysis", {}).get("primary", {}).get("fps")
                    break
            fps[rn].append(v or 0)

    x = np.arange(len(apps))
    w = 0.8 / len(run_names)
    fig, ax = plt.subplots(figsize=(max(7, len(apps) * 0.6), 4))
    for i, rn in enumerate(run_names):
        ax.bar(x + i * w, fps[rn], w, label=rn)
    ax.set_xticks(x + w * (len(run_names) - 1) / 2)
    ax.set_xticklabels([a.split(".")[-1] for a in apps], rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("FPS")
    ax.set_title("FPS Comparison")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return True


def _plot_cache_lines(runs: dict[str, dict], out_path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    fig, ax = plt.subplots(figsize=(7, 4))
    plotted = False
    for rn, data in runs.items():
        for item in data.get("items", []):
            nums = item.get("cached_numbers")
            if not nums:
                continue
            ax.plot(range(1, len(nums) + 1), nums, marker="o", label=rn)
            plotted = True
    if not plotted:
        plt.close(fig)
        return False
    ax.set_xlabel("已启动 app 数")
    ax.set_ylabel("仍缓存的 app 数")
    ax.set_title("Cached Apps Capacity")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return True


def _write_csv(table: dict, out_path: Path) -> None:
    cols = table["columns"]
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for row in table["rows"]:
            w.writerow([row.get(c, "") for c in cols])


def main(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    run_files = [r.strip() for r in args.runs.split(",") if r.strip()]
    # 去掉可能的 .json 后缀
    run_names = [r[:-5] if r.endswith(".json") else r for r in run_files]
    runs = {rn: _load_run(rn) for rn in run_names}
    baseline = args.baseline or run_names[0]

    groups = _group_by_type(runs)

    benchmarks = []
    # 设备信息取第一个 run
    first_dev = next(iter(runs.values())).get("device", {})

    md_t = (config.TEMPLATES_DIR / "report.md.j2").read_text(encoding="utf-8")
    html_t = (config.TEMPLATES_DIR / "report.html.j2").read_text(encoding="utf-8")
    try:
        from jinja2 import Template
        md_tpl, html_tpl = Template(md_t), Template(html_t)
    except ImportError:
        print("[report] jinja2 未安装，跳过模板渲染。先 pip install jinja2")
        return 1

    # 各 type 出表 + 图
    if "startup" in groups:
        tbl = compare.startup_table(groups["startup"], baseline)
        img = "startup_cdf.png" if _plot_startup_cdf(groups["startup"], config.REPORT_DIR / "startup_cdf.png") else None
        benchmarks.append({"title": "App 启动时间", "columns": tbl["columns"],
                           "rows": tbl["rows"], "image": img})
        _write_csv(tbl, config.REPORT_DIR / "startup.csv")

    if "jank" in groups:
        tbl = compare.jank_table(groups["jank"], baseline)
        img = "jank_bars.png" if _plot_jank_bars(groups["jank"], config.REPORT_DIR / "jank_bars.png") else None
        benchmarks.append({"title": "帧率与 Jank", "columns": tbl["columns"],
                           "rows": tbl["rows"], "image": img})
        _write_csv(tbl, config.REPORT_DIR / "jank.csv")

    if "cache" in groups:
        tbl = compare.cache_table(groups["cache"], baseline)
        img = "cache_lines.png" if _plot_cache_lines(groups["cache"], config.REPORT_DIR / "cache_lines.png") else None
        benchmarks.append({"title": "缓存容量/内存", "columns": tbl["columns"],
                           "rows": tbl["rows"], "image": img,
                           "summary": tbl.get("summary")})
        _write_csv(tbl, config.REPORT_DIR / "cache.csv")

    # 全量 JSON
    (config.REPORT_DIR / "results.json").write_text(
        json.dumps({rn: runs[rn] for rn in run_names}, indent=2, ensure_ascii=False),
        encoding="utf-8")

    ctx = {
        "device": {
            "model": first_dev.get("model"), "brand": first_dev.get("brand"),
            "android_version": first_dev.get("android_version"),
            "sdk": first_dev.get("sdk"), "abi": first_dev.get("abi"),
            "root_status": baseline,  # 占位，下方覆盖
            "trace_backend": first_dev.get("trace_backend"),
            "frame_timeline": "?",
        },
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "benchmarks": benchmarks,
    }
    # 补 root/frame_timeline（从 env.json 若存在）
    try:
        from . import setup_env
        env = setup_env.load_env()
        ctx["device"]["root_status"] = "root/userdebug" if env.get("is_root") else "user (无root)"
        ctx["device"]["frame_timeline"] = "支持" if env.get("frame_timeline_supported") else "不支持"
    except Exception:
        pass

    (config.REPORT_DIR / "report.md").write_text(md_tpl.render(**ctx), encoding="utf-8")
    (config.REPORT_DIR / "report.html").write_text(html_tpl.render(**ctx), encoding="utf-8")
    print(f"[report] ✓ {config.REPORT_DIR / 'report.md'}")
    print(f"[report] ✓ {config.REPORT_DIR / 'report.html'}")
    print(f"[report] ✓ {config.REPORT_DIR / 'results.json'}")
    csv_files = sorted(config.REPORT_DIR.glob("*.csv"))
    print(f"[report] CSV: {', '.join(str(f) for f in csv_files)}")
    return 0
