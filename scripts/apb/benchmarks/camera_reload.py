"""测试项：重载相机 (camera_reload)。

复刻 ATC'26 A2 论文 Appendix A.1 的 Shared Benchmark Workload：
  依次启动 N 个 app（每个做代表性动作后切后台，部分保活音频/导航）→
  最后启动相机制造内存压力峰值。

测量 5 个指标（论文 A.1）：
  1. 整轮 MemAvailable 均值/最低值（内存压力曲线）
  2. direct reclaim 次数（内核回收压力，需 trace）
  3. 相机启动延迟（camera launch latency，am start -W + trace）
  4. 相机启动后存活的后台 app 数（keep-alive after camera）
  5. 各 app 冷/热启动时间（可选，startup benchmark 已覆盖）

规格见 references/benchmark-specs.md。
"""
from __future__ import annotations

import time

from .. import config
from ..device import Device
from ..trace_capture import capture


def _detect_camera(dev: Device) -> str:
    """探测设备上实际存在的相机包名（精确匹配，避免 cameraextensions 误判）。"""
    rc, out = dev.shell("pm", "list", "packages", timeout=15)
    installed = set()
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("package:"):
            installed.add(line[len("package:"):])
    for pkg in config.CAMERA_PACKAGES:
        if pkg in installed:
            return pkg
    return "com.android.camera"  # 兜底


def _memavailable(dev: Device) -> int | None:
    """读当前 MemAvailable (KB)。"""
    rc, out = dev.shell("cat", "/proc/meminfo", timeout=10)
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.startswith("MemAvailable:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def _app_action(dev: Device, pkg: str, duration: int, try_scroll: bool = True) -> None:
    """每个 app 启动后的代表性动作。

    优先等 app 到前台并滚动；若被其他 app 抢前台（荣耀等 ROM 有激进保活），
    则跳过滚动只等待——内存压力主要靠进程启动产生，不依赖手势。
    try_scroll=False 时只等待不滚动（camera 阶段用）。
    """
    pid = dev.app_wait(pkg, front=True, timeout=6)
    on_front = False
    if pid:
        on_front = True
    else:
        cur = dev.app_current()
        on_front = bool(cur and cur.get("package") == pkg)
    if not on_front:
        print(f"    ⚠ {pkg} 未到前台，仅等待（不滚动，避免误触桌面）")
        time.sleep(duration)
        return
    time.sleep(1)
    dev.dismiss_popups()
    if not try_scroll:
        time.sleep(max(0, duration - 1))
        return
    end = time.time() + duration
    while time.time() < end:
        dev.swipe_up(scale=0.6)
        time.sleep(0.6)


def run(dev: Device, args, env: dict, app_list: list[str]) -> list[dict]:
    """跑 camera_reload benchmark。

    app_list 来源：优先 --app-list（逗号分隔）；否则用 A2 论文 23-app 列表（仅取设备已装的）。
    """
    use_duration = getattr(args, "camera_use_duration", None) or config.DEFAULT_CAMERA_USE_DURATION
    interval = getattr(args, "camera_interval", None) or config.DEFAULT_CAMERA_INTERVAL
    camera_repeat = getattr(args, "camera_repeat", 3) or 3

    # 构建本轮要跑的 app 序列
    if args.app_list:
        target_apps = [a.strip() for a in args.app_list.split(",") if a.strip()]
    else:
        # 论文 23-app 列表，过滤出设备已装的
        a2_pkgs = [p[0] for p in config.A2_BENCHMARK_APPS]
        rc, out = dev.shell("pm", "list", "packages", timeout=20)
        installed = set()
        for line in (out or "").splitlines():
            if line.startswith("package:"):
                installed.add(line.split(":", 1)[1].strip())
        target_apps = [p for p in a2_pkgs if p in installed]
        if len(target_apps) < 3:
            print(f"[camera_reload] 设备上论文 app 装得太少（{len(target_apps)} 个），改用传入/默认 app_list")
            target_apps = [a for a in app_list if a in installed][:8]

    camera_pkg = _detect_camera(dev)
    # camera_repeat 语义：完整加压流程（N app → 相机×1）重复的轮数（论文 500 轮）
    rounds = camera_repeat
    # 估算单轮时长用于 perfetto duration（N app × use_duration + 相机 ~6s + 余量）
    per_round_s = len(target_apps) * (use_duration + interval + 2) + 8
    print(f"\n[camera_reload] 完整加压流程重复 {rounds} 轮")
    print(f"  每轮：{len(target_apps)} 个 app 依次启动(前台{use_duration}s/间隔{interval}s) → 最后启动相机 {camera_pkg} 一次")
    print(f"  论文 A.1：连开多 app + 相机制造内存压力峰值，整个流程重复测系统持续压力下表现")

    dev.unlock()
    dev.kill_all(target_apps + [camera_pkg])
    time.sleep(2)

    mem_curve = []           # 全程 MemAvailable 采样
    launch_times = []        # 每个 app 每轮的启动时间
    camera_results = []      # 每轮相机的指标
    started_apps = list(target_apps)

    # trace 覆盖整个多轮测试
    total_dur = max(per_round_s * rounds, 20)
    with capture(dev, camera_pkg, duration_s=total_dur, run=args.run,
                 name="camera", env=env, include_sched=True):
        for rd in range(rounds):
            print(f"\n  ── 第 {rd+1}/{rounds} 轮 ──")
            # ── 阶段A：依次启动所有 app（轮间不清后台，压力累积，同论文）──
            for i, pkg in enumerate(target_apps):
                step_idx = rd * len(target_apps) + i
                mb = _memavailable(dev)
                if mb is not None:
                    mem_curve.append({"round": rd+1, "step": step_idx,
                                      "phase": "pre_launch", "app": pkg, "memavailable_kb": mb})
                res = dev.am_start_w(pkg)
                dev.app_start(pkg)  # u2 兜底切前台
                launch_times.append({"round": rd+1, "app": pkg,
                                     "wait_time_ms": res.get("wait_time"),
                                     "state": res.get("launch_state")})
                _app_action(dev, pkg, use_duration)
                dev.home()
                time.sleep(interval)
                mb = _memavailable(dev)
                if mb is not None:
                    mem_curve.append({"round": rd+1, "step": step_idx,
                                      "phase": "post_bg", "app": pkg, "memavailable_kb": mb})

            # ── 阶段B：相机启动前存活数（每轮记录）──
            pre_cam_cached = dev.cached_apps(set(started_apps))
            pre_cam_mem = _memavailable(dev)

            # ── 阶段C：启动相机一次（本轮压力峰值）──
            dev.force_stop(camera_pkg)
            time.sleep(1)
            res = dev.am_start_w(camera_pkg)
            dev.app_start(camera_pkg)
            time.sleep(3)  # 等相机预览稳定，触发内存压力
            wt = res.get("wait_time")
            post_cam_cached = dev.cached_apps(set(started_apps))
            post_cam_mem = _memavailable(dev)
            camera_results.append({
                "round": rd + 1,
                "camera_wait_time_ms": wt,
                "camera_state": res.get("launch_state"),
                "pre_survived_count": len(pre_cam_cached),
                "survived_apps": sorted(post_cam_cached),
                "survived_count": len(post_cam_cached),
                "pre_memavailable_kb": pre_cam_mem,
                "memavailable_kb": post_cam_mem,
            })
            print(f"  轮 {rd+1}: 相机启动={wt}ms | 相机前存活 {len(pre_cam_cached)} → "
                  f"相机后存活 {len(post_cam_cached)} app | MemAvail "
                  f"{round(pre_cam_mem/1024) if pre_cam_mem else '?'}→"
                  f"{round(post_cam_mem/1024) if post_cam_mem else '?'}MB")
            dev.home()
            time.sleep(1)

    # 统计
    mem_values = [m["memavailable_kb"] for m in mem_curve if m.get("memavailable_kb") is not None]
    run_dir = config.TRACE_DIR / args.run
    camera_trace = run_dir / "camera.perfetto-trace"
    if not camera_trace.exists():
        camera_trace = run_dir / "camera.ftrace"
    summary = {
        "app": "__camera_reload_summary__",
        "app_list": target_apps,
        "camera_pkg": camera_pkg,
        "rounds": rounds,
        "trace": str(camera_trace) if camera_trace.exists() else None,
        "mem_curve": mem_curve,
        "mem_stats": {
            "mean_kb": round(sum(mem_values) / len(mem_values), 1) if mem_values else None,
            "min_kb": min(mem_values) if mem_values else None,
            "max_kb": max(mem_values) if mem_values else None,
        },
        "camera_results": camera_results,
        "launch_times": launch_times,
        # 关键对比指标（供 report/compare 用）：多轮相机的统计
        "camera_launch_ms_mean": (
            round(sum(c["camera_wait_time_ms"] for c in camera_results
                      if c["camera_wait_time_ms"] is not None) / len(camera_results), 1)
            if any(c["camera_wait_time_ms"] is not None for c in camera_results) else None),
        "survived_after_camera_mean": (
            round(sum(c["survived_count"] for c in camera_results) / len(camera_results), 1)
            if camera_results else None),
        "memavailable_min_kb": min(mem_values) if mem_values else None,
    }
    return [summary]
