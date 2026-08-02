"""测试项 1：App 启动时间（冷/热）。对应 Fleet Exp-3 (Fig 13)。

规格见 references/benchmark-specs.md。
"""
from __future__ import annotations

import time

from .. import config
from ..device import Device
from ..trace_capture import capture, warm_background_apps


def run(dev: Device, args, env: dict, app_list: list[str]) -> list[dict]:
    pkg = args.app
    if not pkg:
        raise SystemExit("startup 需要 --app <package>")
    repeat = args.repeat or config.DEFAULT_STARTUP_REPEAT
    launch_type = args.launch_type  # cold / hot / both
    cooldown = config.DEFAULT_COOLDOWN
    items = []

    dev.unlock()

    for lt in (("cold", "hot") if launch_type == "both" else (launch_type,)):
        print(f"\n[startup] {pkg} · {lt}启动 · 重复 {repeat} 次")
        if args.background_apps:
            warm_background_apps(dev, app_list, args.background_apps)

        am_results = []
        for i in range(repeat):
            if lt == "cold":
                if getattr(args, "clear_data", True):
                    dev.pm_clear(pkg)
                dev.force_stop(pkg)
                time.sleep(cooldown)
            else:  # hot
                dev.app_start(pkg)
                time.sleep(10)  # 前台用 10s
                dev.home()
                time.sleep(cooldown)

            # 录 trace + am start -W（trace 覆盖整个启动过程）
            with capture(dev, pkg, duration_s=10, run=args.run,
                         name=f"{lt}_{i}", env=env,
                         include_sched=True):
                res = dev.am_start_w(pkg)
                am_results.append(res)
                # 等首帧稳定
                dev.app_wait(pkg, front=True, timeout=10)
                time.sleep(1)
            print(f"  [{i+1}/{repeat}] {lt} wait={res.get('wait_time')}ms "
                  f"state={res.get('launch_state')}")

        trace = None
        # 启动测试我们存最后一次 trace 做分析样本（多次启动各存一份更佳，
        # 但为简洁，此处保留最后一次的 trace 路径供 analyze）
        run_dir = config.TRACE_DIR / args.run
        last = run_dir / f"{lt}_{repeat-1}.perfetto-trace"
        if not last.exists():
            last = run_dir / f"{lt}_{repeat-1}.ftrace"
        trace = str(last) if last.exists() else None

        items.append({
            "app": pkg,
            "launch_type": lt,
            "repeat": repeat,
            "am_results": am_results,
            "trace": trace,
        })

    return items
