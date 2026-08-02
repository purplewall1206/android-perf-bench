"""测试项 1：App 启动时间（冷/热）。对应 Fleet Exp-3 (Fig 13)。

按轮次的完整流程（每轮 repeat）：
  ① 小刷子清后台（clear_recent_apps，setup 探测的）
  ② cold: force_stop 目标 app（制造进程不存在的 cold 基线）+ warm_background_apps 加压
     hot:  目标 app 先跑前台再切后台缓存 + warm_background_apps 加压
  ③ capture（启 perfetto）
  ④ am_start_w 目标 + app_wait
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
    cleanup_cfg = env.get("recent_cleanup", {})
    items = []

    dev.unlock()

    for lt in (("cold", "hot") if launch_type == "both" else (launch_type,)):
        print(f"\n[startup] {pkg} · {lt}启动 · 重复 {repeat} 轮（每轮：小刷子→加压→trace→启动）")
        am_results = []

        for i in range(repeat):
            # ── 每轮完整流程 ──
            # ① 小刷子清后台
            dev.clear_recent_apps(app_list=app_list + [pkg], cleanup_cfg=cleanup_cfg)
            time.sleep(1)

            # ② 制造启动基线 + 加压
            if lt == "cold":
                # cold: 目标进程不存在（先小刷子已清，再 force_stop 目标确保进程不在）
                dev.force_stop(pkg)
                time.sleep(cooldown)
            else:  # hot: 目标 app 先跑前台再切后台缓存（不 force_stop）
                dev.app_start(pkg)
                time.sleep(10)
                dev.home()
                time.sleep(cooldown)
            # 加压（不 force-stop 加压 app，让厂商自然保活）
            if args.background_apps:
                warm_background_apps(dev, app_list, args.background_apps)

            # ③ 抓 trace（perfetto）
            # ④ 启动目标
            with capture(dev, pkg, duration_s=10, run=args.run,
                         name=f"{lt}_{i}", env=env, include_sched=True):
                res = dev.am_start_w(pkg)
                am_results.append(res)
                dev.app_wait(pkg, front=True, timeout=10)
                time.sleep(1)
            print(f"  [{i+1}/{repeat}] {lt} wait={res.get('wait_time')}ms "
                  f"state={res.get('launch_state')}")
            # 本轮结束回桌面，再进下一轮（真实用户切换节奏）
            dev.home()
            time.sleep(1)

        # 保留最后一次 trace 做分析样本
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
