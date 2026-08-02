"""测试项：保活压力测试 (keepalive)。

50 个 app 启动 N 轮，每个 app：
  前台运行 foreground 秒 → press home 进后台 → 等待 background_wait 秒 → 启动下一个。
每启动完一个 app 进桌面后，采样：
  /proc/meminfo（MemAvailable/Cached/Swap 等）、
  /proc/vmstat（pgmajfault/pswpin/pswpout/pgscan 等）、
  dumpsys meminfo -S（每个进程 PSS）、
  存活的后台 app 数（dumpsys meminfo 扫包名）。

目的：测系统在大量 app 保活压力下的内存管理能力——哪些 app 被杀、内存回收压力、swap 活动。
"""
from __future__ import annotations

import time

from .. import config
from ..device import Device


def _sample(dev: Device, app_list: list[str], phase: str, app: str,
            step: int, rd: int, t0: float) -> dict:
    """采一个样本点：meminfo + vmstat + dumpsys -S + 存活数。"""
    sample = {
        "round": rd, "step": step, "phase": phase, "app": app,
        "elapsed_s": round(time.time() - t0, 1),
        "proc_meminfo": dev.sample_proc_meminfo(),
        "proc_vmstat": dev.sample_proc_vmstat(),
        "memavailable_kb": None,
        "alive_count": 0,
        "alive_apps": [],
    }
    mi = sample["proc_meminfo"]
    sample["memavailable_kb"] = mi.get("memavailable_kb")
    # 存活数（用 dumpsys meminfo 扫包名，best-effort，不阻塞）
    try:
        alive = dev.cached_apps(set(app_list))
        sample["alive_count"] = len(alive)
        sample["alive_apps"] = sorted(alive)
    except Exception:
        pass
    # 每 app 的 PSS（dumpsys meminfo -S，原 cache_mem 的 per-app 内存）
    try:
        procs = dev.sample_dumpsys_meminfo_s()
        # 过滤出 app_list 中的进程
        target_set = set(app_list)
        sample["app_pss"] = {p["package"]: p["pss_kb"]
                             for p in procs if p["package"] in target_set}
    except Exception:
        sample["app_pss"] = {}
        pass
    return sample


def run(dev: Device, args, env: dict, app_list: list[str]) -> list[dict]:
    foreground = getattr(args, "ka_foreground", None) or config.DEFAULT_KA_FOREGROUND
    bg_wait = getattr(args, "ka_background_wait", None) or config.DEFAULT_KA_BACKGROUND_WAIT
    rounds = getattr(args, "ka_rounds", None) or config.DEFAULT_KA_ROUNDS
    target = getattr(args, "ka_target_count", None)
    # 默认按设备内存推荐 app 数（16G→35, 12G→30, 8G→18...），可被 --ka-target-count 覆盖
    if not target:
        target = config.recommended_app_count(env.get("memtotal_kb"))
        print(f"[keepalive] 设备 {env.get('memtotal_gb','?')}GB → 推荐加压 {target} 个 app")
    launch_method = getattr(args, "launch_method", "auto")
    workload = getattr(args, "ka_workload", "idle")  # idle|scroll（scroll=原 cache 行为）
    # cache 预设：--type cache 时走轻量单轮滚动模式
    if getattr(args, "_cache_preset", False):
        workload = "scroll"
        rounds = 1

    # 构建 app 列表：优先 --app-list，否则用候选池取设备已装交集
    if args.app_list:
        target_apps = [a.strip() for a in args.app_list.split(",") if a.strip()]
    else:
        rc, out = dev.shell("pm", "list", "packages", timeout=20)
        installed = set()
        for line in (out or "").splitlines():
            if line.startswith("package:"):
                installed.add(line.split(":", 1)[1].strip())
        # 候选池 + 第一方槽位（按品牌）
        pool = list(config.KEEPALIVE_APP_POOL)
        brand = env.get("brand", "")
        for slot in ("camera", "health", "appmarket", "weather", "contacts", "mms"):
            pool.extend(config.first_party_candidates(slot, brand))
        # 去重保序
        seen = set()
        unique_pool = []
        for p in pool:
            if p not in seen:
                seen.add(p); unique_pool.append(p)
        target_apps = [p for p in unique_pool if p in installed][:target]
        if len(target_apps) < 3:
            print(f"[keepalive] 设备上候选 app 太少（{len(target_apps)}），改用传入 app_list")
            target_apps = [a for a in app_list if a in installed][:target]

    print(f"\n[keepalive] {len(target_apps)} 个 app × {rounds} 轮")
    print(f"  每个：前台 {foreground}s → 进后台等 {bg_wait}s → 下一个")
    print(f"  每步采样 /proc/meminfo + /proc/vmstat + dumpsys meminfo -S + 存活数")

    dev.unlock()
    # 测试前清后台（同 camera_reload）
    print("[keepalive] 清理后台...")
    dev.clear_recent_apps(app_list=target_apps, cleanup_cfg=env.get("recent_cleanup", {}))
    time.sleep(2)

    samples = []          # 所有采样点
    launch_records = []   # 每个 app 每轮的启动记录
    t0 = time.time()

    for rd in range(rounds):
        print(f"\n  ── 第 {rd+1}/{rounds} 轮 ──")
        for i, pkg in enumerate(target_apps):
            step = rd * len(target_apps) + i
            # 启动前采样
            samples.append(_sample(dev, target_apps, "pre_launch", pkg, step, rd + 1, t0))
            ma_pre = samples[-1]["memavailable_kb"]

            # 启动 app（am start -W 记时间 + launch_app 切前台）
            am_res = dev.am_start_w(pkg)
            lr = dev.launch_app(pkg, method=launch_method)
            launch_records.append({
                "round": rd + 1, "app": pkg,
                "wait_time_ms": am_res.get("wait_time"),
                "state": am_res.get("launch_state"),
                "launch_method": lr.get("method_used"),
                "on_front": lr.get("on_front"),
            })
            # 前台运行（workload=idle 纯等待；scroll 模拟用户滚动，原 cache 行为）
            if workload == "scroll":
                end_fg = time.time() + foreground
                while time.time() < end_fg:
                    dev.swipe_up(scale=0.7)
                    time.sleep(0.8)
            else:
                time.sleep(foreground)

            # press home 进桌面
            dev.home()
            # 进桌面后立即采样（关键观测点：app 刚切后台的系统状态）
            samples.append(_sample(dev, target_apps, "post_home", pkg, step, rd + 1, t0))
            ma_post = samples[-1]["memavailable_kb"]
            alive_post = samples[-1]["alive_count"]

            # 等待 background_wait 秒（让系统有机会回收）
            time.sleep(bg_wait)
            # 等待后再采样（看系统是否回收了该 app）
            samples.append(_sample(dev, target_apps, "post_bg_wait", pkg, step, rd + 1, t0))

            print(f"  [{step+1}/{len(target_apps)*rounds}] {pkg} "
                  f"启动={am_res.get('wait_time')}ms 前台存活={alive_post} "
                  f"MemAvail {round(ma_pre/1024) if ma_pre else '?'}→"
                  f"{round(ma_post/1024) if ma_post else '?'}MB")

    # 汇总
    mem_values = [s["memavailable_kb"] for s in samples if s.get("memavailable_kb") is not None]
    alive_values = [s["alive_count"] for s in samples]

    # proc 时序数据单独写文件（keepalive 不录 perfetto，proc 是唯一数据来源）
    import json
    config.ensure_dirs()
    proc_file = config.RESULT_DIR / f"{args.run}_keepalive_proc.json"
    proc_data = {
        "run": args.run, "type": "keepalive_proc",
        "params": {"foreground_s": foreground, "background_wait_s": bg_wait,
                   "target_count": target, "actual_count": len(target_apps),
                   "workload": workload, "rounds": rounds},
        "samples": samples,           # 完整时序：meminfo/vmstat/dumpsys-PSS/存活数
        "launch_records": launch_records,
    }
    proc_file.write_text(json.dumps(proc_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[keepalive] proc 时序数据 → {proc_file}")

    summary = {
        "app": "__keepalive_summary__",
        "app_list": target_apps,
        "rounds": rounds,
        "params": proc_data["params"],
        "proc_file": str(proc_file),   # 指向独立 proc 文件
        "launch_records": launch_records,
        # 只放统计值（原始时序在 proc_file）
        "mem_stats": {
            "mean_kb": round(sum(mem_values) / len(mem_values), 1) if mem_values else None,
            "min_kb": min(mem_values) if mem_values else None,
            "max_kb": max(mem_values) if mem_values else None,
        },
        "alive_stats": {
            "mean": round(sum(alive_values) / len(alive_values), 1) if alive_values else 0,
            "max": max(alive_values) if alive_values else 0,
        },
        "memavailable_min_kb": min(mem_values) if mem_values else None,
        "alive_max": max(alive_values) if alive_values else 0,
    }
    return [summary]
