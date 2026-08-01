"""测试项 2：帧率与 Jank。对应 Fleet Exp-4 (Fig 14)。
测试项 4（可选）：CPU 开销分解。对应 Fleet Exp-4 CPU。

force_cpu=True 时走 cpu 路径（加 sched）。
规格见 references/benchmark-specs.md。
"""
from __future__ import annotations

import time

from .. import config
from ..device import Device
from ..trace_capture import capture, warm_background_apps


def run(dev: Device, args, env: dict, app_list: list[str],
        force_cpu: bool = False) -> list[dict]:
    pkg = args.app
    if not pkg:
        raise SystemExit("jank/cpu 需要 --app <package>")
    duration = args.scroll_duration or config.DEFAULT_SCROLL_DURATION
    warmup = args.warmup
    scale = args.scroll_scale
    mode = args.scroll_mode

    bench_name = "cpu" if force_cpu else "jank"
    print(f"\n[{bench_name}] {pkg} · 滚动 {duration}s (mode={mode}, scale={scale})")

    dev.unlock()

    # 冷启动 + 等稳定 + 关弹窗
    dev.app_start(pkg, stop=True)
    dev.app_wait(pkg, front=True, timeout=20)

    # 关键：校验目标 app 真的到了前台（荣耀等 ROM 启动管理激进，可能被抢前台）
    cur = dev.app_current()
    if cur and cur.get("package") != pkg:
        print(f"  ⚠ 前台是 {cur.get('package')} 而非 {pkg}，重新拉起")
        dev.force_stop(cur.get("package")) if cur.get("package") else None
        dev.app_start(pkg)
        dev.app_wait(pkg, front=True, timeout=15)
    time.sleep(warmup)
    dev.dismiss_popups()

    # 后台预跑制造内存压力
    if args.background_apps:
        warm_background_apps(dev, app_list, args.background_apps, use_s=5)
        # 回到目标 app 前台
        dev.app_start(pkg)
        dev.app_wait(pkg, front=True, timeout=10)
        time.sleep(1)

    # 录 trace + 滚动 workload
    include_sched = True  # jank 也带 sched（CPU 分析可复用）
    with capture(dev, pkg, duration_s=duration, run=args.run,
                 name=bench_name, env=env, include_sched=include_sched):
        print(f"  滚动中... ({duration}s)")
        end = time.time() + duration
        while time.time() < end:
            if mode == "fling":
                dev.swipe_ext("up", scale=scale)
            else:
                dev.swipe_up(scale=scale)
            time.sleep(0.5)
        dev.dismiss_popups()

    run_dir = config.TRACE_DIR / args.run
    trace = run_dir / f"{bench_name}.perfetto-trace"
    if not trace.exists():
        trace = run_dir / f"{bench_name}.ftrace"

    return [{
        "app": pkg,
        "scroll_duration": duration,
        "scroll_mode": mode,
        "trace": str(trace) if trace.exists() else None,
    }]
