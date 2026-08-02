"""测试项 3：缓存容量/内存压力。对应 Fleet Exp-1 (Fig 11c)。

无需 trace，直接 dumpsys meminfo 扫包名。规格见 references/benchmark-specs.md。
"""
from __future__ import annotations

import time

from .. import config
from ..device import Device


def run(dev: Device, args, env: dict, app_list: list[str]) -> list[dict]:
    use_duration = args.use_duration or config.DEFAULT_USE_DURATION

    print(f"\n[cache] 连续启动 {len(app_list)} 个 app，每 app 前台 {use_duration}s")
    dev.unlock()

    # 清空后台（点击最近任务全清优先，force-stop 兜底）
    print("[cache] 清理后台...")
    dev.clear_recent_apps(app_list=app_list, cleanup_cfg=env.get("recent_cleanup", {}))
    time.sleep(2)

    cached_numbers = []
    mem_per_app = []
    started = []

    for i, pkg in enumerate(app_list):
        print(f"  [{i+1}/{len(app_list)}] 启动 {pkg}")
        if not dev.app_start(pkg):
            print(f"    启动失败，跳过")
            continue
        dev.app_wait(pkg, front=True, timeout=15)
        time.sleep(2)
        dev.dismiss_popups()

        # 前台使用（滚动）
        end = time.time() + use_duration
        while time.time() < end:
            dev.swipe_up(scale=0.7)
            time.sleep(0.8)

        dev.home()
        time.sleep(1)
        started.append(pkg)

        # 每步统计缓存数（复刻 Fleet check_cached_apps）
        cached = dev.cached_apps(app_list)
        cached_numbers.append(len(cached))
        print(f"    缓存数 = {len(cached)}  ({sorted(cached)})")

        # 顺便记单个 app 内存
        mem = dev.parse_meminfo_app(pkg)
        if mem:
            mem["app"] = pkg
            mem_per_app.append(mem)

    sys_mem = dev.parse_meminfo_total()

    return [{
        "app": "__cache_summary__",
        "app_list": app_list,
        "cached_numbers": cached_numbers,
        "started_apps": started,
        "mem_per_app": mem_per_app,
        "system_mem": sys_mem,
    }]
