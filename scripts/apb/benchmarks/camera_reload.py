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
    print(f"\n[camera_reload] {len(target_apps)} 个 app 依次启动 → 最后重载相机 {camera_pkg} × {camera_repeat}")
    print(f"  (每 app 前台 {use_duration}s，间隔 {interval}s)")

    dev.unlock()
    dev.kill_all(target_apps + [camera_pkg])
    time.sleep(2)

    # 整轮 MemAvailable 采样
    mem_curve = []
    started_apps = []
    launch_times = []  # 每个 app 的 am start WaitTime

    for i, pkg in enumerate(target_apps):
        # 采样内存（启动前）
        mb = _memavailable(dev)
        if mb is not None:
            mem_curve.append({"step": i, "phase": "pre_launch", "app": pkg, "memavailable_kb": mb})

        # am start -W 记录启动耗时（拿数据），再用 u2 app_start 兜底确保到前台
        # （荣耀 MagicOS 等仅 am start 不一定能切前台，需 u2 的启动方式）
        res = dev.am_start_w(pkg)
        wt = res.get("wait_time")
        dev.app_start(pkg)  # u2 兜底切前台
        launch_times.append({"app": pkg, "wait_time_ms": wt, "state": res.get("launch_state")})

        _app_action(dev, pkg, use_duration)
        dev.home()
        started_apps.append(pkg)
        time.sleep(interval)

        # 采样内存（启动后/切后台后）
        mb = _memavailable(dev)
        if mb is not None:
            mem_curve.append({"step": i, "phase": "post_bg", "app": pkg, "memavailable_kb": mb})

    # 所有 app 启动完，相机启动前的存活数 + MemAvailable
    pre_camera_cached = dev.cached_apps(set(started_apps))
    pre_camera_mem = _memavailable(dev)
    print(f"  相机启动前：缓存 {len(pre_camera_cached)} app，MemAvailable={pre_camera_mem} KB")

    # ── 重载相机阶段：启动相机制造内存压力峰值 ──
    camera_results = []
    with capture(dev, camera_pkg, duration_s=10 * camera_repeat, run=args.run,
                 name="camera", env=env, include_sched=True):
        for r in range(camera_repeat):
            dev.force_stop(camera_pkg)
            time.sleep(1)
            res = dev.am_start_w(camera_pkg)  # 记录相机启动耗时
            dev.app_start(camera_pkg)          # u2 兜底确保相机到前台
            time.sleep(3)  # 等相机预览稳定，触发内存压力
            wt = res.get("wait_time")
            post_cam_cached = dev.cached_apps(set(started_apps))
            post_cam_mem = _memavailable(dev)
            camera_results.append({
                "round": r + 1,
                "camera_wait_time_ms": wt,
                "camera_state": res.get("launch_state"),
                "survived_apps": sorted(post_cam_cached),
                "survived_count": len(post_cam_cached),
                "memavailable_kb": post_cam_mem,
            })
            print(f"  相机 [{r+1}/{camera_repeat}] 启动={wt}ms "
                  f"存活={len(post_cam_cached)} app MemAvail={post_cam_mem}KB")
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
        "trace": str(camera_trace) if camera_trace.exists() else None,
        "mem_curve": mem_curve,
        "mem_stats": {
            "mean_kb": round(sum(mem_values) / len(mem_values), 1) if mem_values else None,
            "min_kb": min(mem_values) if mem_values else None,
            "max_kb": max(mem_values) if mem_values else None,
        },
        "pre_camera": {
            "survived_count": len(pre_camera_cached),
            "memavailable_kb": pre_camera_mem,
        },
        "camera_results": camera_results,
        "launch_times": launch_times,
        # 关键对比指标（供 report/compare 用）
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
