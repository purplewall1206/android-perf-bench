"""测试项 2：帧率与 Jank。对应 Fleet Exp-4 (Fig 14)。
测试项 4（可选）：CPU 开销分解。对应 Fleet Exp-4 CPU。

按轮次的完整流程（每轮 repeat，默认 3 轮）：
  ① 小刷子清后台
  ② warm_background_apps 加压
  ③ capture（启 perfetto）
  ④ app_start 目标（不 stop=True）→ 前台校验 → warmup → 滚动 workload
每轮一个 trace，analyze 聚合多轮 FPS/jank 统计。
force_cpu=True 时走 cpu 路径（加 sched）。
"""
from __future__ import annotations

import time

from .. import config
from ..device import Device
from ..trace_capture import capture, warm_background_apps

DEFAULT_JANK_REPEAT = 3


def run(dev: Device, args, env: dict, app_list: list[str],
        force_cpu: bool = False) -> list[dict]:
    pkg = args.app
    if not pkg:
        raise SystemExit("jank/cpu 需要 --app <package>")
    duration = args.scroll_duration or config.DEFAULT_SCROLL_DURATION
    warmup = args.warmup
    scale = args.scroll_scale
    mode = args.scroll_mode
    repeat = getattr(args, "repeat", None) or DEFAULT_JANK_REPEAT
    cleanup_cfg = env.get("recent_cleanup", {})

    bench_name = "cpu" if force_cpu else "jank"
    print(f"\n[{bench_name}] {pkg} · 滚动 {duration}s × {repeat} 轮 "
          f"(mode={mode}, scale={scale})")

    dev.unlock()
    traces = []

    for i in range(repeat):
        print(f"  ── 第 {i+1}/{repeat} 轮 ──")
        # ① 小刷子清后台
        dev.clear_recent_apps(app_list=app_list + [pkg], cleanup_cfg=cleanup_cfg)
        time.sleep(1)
        # ② 加压（不 force-stop 加压 app）
        if args.background_apps:
            warm_background_apps(dev, app_list, args.background_apps, use_s=5)
        # ③ 抓 trace + ④ 启动目标 + 滚动
        with capture(dev, pkg, duration_s=duration + warmup + 5, run=args.run,
                     name=f"{bench_name}_{i}", env=env, include_sched=True):
            dev.app_start(pkg)  # 不传 stop=True（让 app 自然状态）
            dev.app_wait(pkg, front=True, timeout=20)
            # 前台校验（被抢前台则纠正）
            cur = dev.app_current()
            if cur and cur.get("package") != pkg:
                print(f"    ⚠ 前台是 {cur.get('package')} 而非 {pkg}，重新拉起")
                dev.app_start(pkg)
                dev.app_wait(pkg, front=True, timeout=15)
            time.sleep(warmup)
            dev.dismiss_popups()
            print(f"    滚动中... ({duration}s)")
            end = time.time() + duration
            while time.time() < end:
                if mode == "fling":
                    dev.swipe_ext("up", scale=scale)
                else:
                    dev.swipe_up(scale=scale)
                time.sleep(0.5)
            dev.dismiss_popups()
        # 本轮结束回桌面，再进下一轮（真实用户切换节奏）
        dev.home()
        time.sleep(1)

        run_dir = config.TRACE_DIR / args.run
        trace = run_dir / f"{bench_name}_{i}.perfetto-trace"
        if not trace.exists():
            trace = run_dir / f"{bench_name}_{i}.ftrace"
        if trace.exists():
            traces.append(str(trace))

    return [{
        "app": pkg,
        "scroll_duration": duration,
        "scroll_mode": mode,
        "repeat": repeat,
        "traces": traces,           # 多轮 trace 列表
        "trace": traces[-1] if traces else None,  # 兼容：最后一个
    }]
