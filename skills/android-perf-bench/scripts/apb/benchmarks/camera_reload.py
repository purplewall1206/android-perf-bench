"""测试项：重载相机 (camera_reload)。复刻 ATC'26 A2 论文 Appendix A.1。

按轮次的完整流程（每轮 rounds）：
  ① 小刷子清后台（clear_recent_apps，setup 探测）
  ② 启 perfetto（覆盖本轮 N app 加压 + 相机启动，记录全程内存曲线）
  ③ 启动 N 个 app 加压（不 force-stop，让厂商自然保活）
  ④ 启动相机（不 force-stop，靠小刷子已清 + 系统自然状态）
每轮一个 trace，analyze 从 perfetto 提取 MemAvailable 曲线/存活数/direct reclaim/相机 first buffer。

注意：proc 信息（MemAvailable/存活数）全部从 perfetto trace 解析，不单独抓（perfetto 已含
process_stats + packages_list）。相机启动延迟 = apk 启动 → first full buffer。
"""
from __future__ import annotations

import time

from .. import config
from ..device import Device
from ..trace_capture import capture


def _detect_camera(dev: Device, env: dict) -> str:
    """探测设备上实际存在的相机包名。优先用 env['camera_pkg']，否则按品牌精确匹配。"""
    if env.get("camera_pkg"):
        return env["camera_pkg"]
    rc, out = dev.shell("pm", "list", "packages", timeout=15)
    installed = set()
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("package:"):
            installed.add(line[len("package:"):])
    brand = env.get("brand", "")
    for pkg in config.first_party_candidates("camera", brand):
        if pkg in installed:
            return pkg
    for pkg in config.CAMERA_PACKAGES:
        if pkg in installed:
            return pkg
    return "com.android.camera"


def _app_action(dev: Device, pkg: str, duration: int) -> None:
    """app 启动后等待进程稳定（占内存）。不做手势——保活 app 抢前台时手势会在错误界面操作。"""
    time.sleep(duration)


def run(dev: Device, args, env: dict, app_list: list[str]) -> list[dict]:
    use_duration = getattr(args, "camera_use_duration", None) or config.DEFAULT_CAMERA_USE_DURATION
    interval = getattr(args, "camera_interval", None) or config.DEFAULT_CAMERA_INTERVAL
    rounds = camera_repeat = getattr(args, "camera_repeat", 3) or 3
    launch_method = getattr(args, "launch_method", "auto")
    cleanup_cfg = env.get("recent_cleanup", {})

    # 构建 app 列表
    if args.app_list:
        target_apps = [a.strip() for a in args.app_list.split(",") if a.strip()]
    else:
        a2_pkgs = config.build_a2_app_list(env.get("brand", ""))
        rc, out = dev.shell("pm", "list", "packages", timeout=20)
        installed = set()
        for line in (out or "").splitlines():
            if line.startswith("package:"):
                installed.add(line.split(":", 1)[1].strip())
        target_apps = [p for p in a2_pkgs if p in installed]
        if len(target_apps) < 3:
            target_apps = [a for a in app_list if a in installed][:8]

    camera_pkg = _detect_camera(dev, env)
    # 内存提示
    mem_gb = env.get("memtotal_gb")
    if mem_gb and not args.app_list and len(target_apps) < config.recommended_app_count(env.get("memtotal_kb")):
        print(f"[camera_reload] 提示：设备 {mem_gb}GB，当前仅 {len(target_apps)} 个加压 app，"
              f"建议 --app-list 指定更多（推荐 {config.recommended_app_count(env.get('memtotal_kb'))} 个）")

    print(f"\n[camera_reload] {len(target_apps)} 个 app × {rounds} 轮（每轮：小刷子→trace→N app加压→相机）")
    print(f"  相机: {camera_pkg}，proc 信息全从 perfetto trace 解析")

    dev.unlock()
    camera_results = []

    for rd in range(rounds):
        print(f"\n  ── 第 {rd+1}/{rounds} 轮 ──")
        # ① 小刷子清后台（不 kill_all，让 recent 小刷子 + 系统自然）
        dev.clear_recent_apps(app_list=target_apps + [camera_pkg], cleanup_cfg=cleanup_cfg)
        time.sleep(1)

        # ②③④ 启 perfetto（覆盖全程）→ N app 加压 → 相机
        per_round_s = len(target_apps) * (use_duration + interval + 2) + 10
        with capture(dev, camera_pkg, duration_s=per_round_s, run=args.run,
                     name=f"camera_r{rd}", env=env, include_sched=True):
            # ③ 启动 N 个 app 加压（不 force-stop，自然保活）
            for i, pkg in enumerate(target_apps):
                am_res = dev.am_start_w(pkg)
                dev.launch_app(pkg, method=launch_method)
                _app_action(dev, pkg, use_duration)
                dev.home()
                time.sleep(interval)

            # ④ 启动相机（本轮压力峰值；不 force_stop，靠小刷子已清 + 自然状态）
            am_res = dev.am_start_w(camera_pkg)
            dev.launch_app(camera_pkg, method=launch_method)
            time.sleep(4)  # 等相机预览稳定，触发内存压力 + first buffer

        # 记录本轮（相机启动时间来自 am；MemAvailable/存活数/direct reclaim/first buffer 由 analyze 从 trace 提取）
        trace = config.TRACE_DIR / args.run / f"camera_r{rd}.perfetto-trace"
        if not trace.exists():
            trace = config.TRACE_DIR / args.run / f"camera_r{rd}.ftrace"
        camera_results.append({
            "round": rd + 1,
            "camera_pkg": camera_pkg,
            "camera_wait_time_ms": am_res.get("wait_time"),
            "camera_state": am_res.get("launch_state"),
            "trace": str(trace) if trace.exists() else None,
        })
        print(f"  轮 {rd+1}: 相机启动={am_res.get('wait_time')}ms (MemAvailable/存活数/reclaim 见 trace 分析)")
        dev.home()
        time.sleep(1)

    summary = {
        "app": "__camera_reload_summary__",
        "app_list": target_apps,
        "camera_pkg": camera_pkg,
        "rounds": rounds,
        "camera_results": camera_results,
        # 相机启动均值（am 测的；first buffer 由 analyze 补充）
        "camera_launch_ms_mean": (
            round(sum(c["camera_wait_time_ms"] for c in camera_results
                      if c["camera_wait_time_ms"] is not None) / len(camera_results), 1)
            if any(c["camera_wait_time_ms"] is not None for c in camera_results) else None),
    }
    return [summary]
