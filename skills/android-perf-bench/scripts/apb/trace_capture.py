"""阶段 1 — 抓 trace + 跑 workload。

根据 env.trace_backend 自适应：
  - perfetto：推送 textproto 配置，--background 录制，pull 回
  - atrace：async_start/async_stop 降级（无 FrameTimeline）
  - none：报错

提供 TraceContext 上下文管理器：进入开始录、退出停止并 pull。
各 benchmark 在 TraceContext 内跑 workload。
"""
from __future__ import annotations

import argparse
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from . import config, setup_env
from .device import Device


def _render_config(duration_ms: int, app_pkg: str, env: dict,
                   include_frametimeline: bool = True,
                   include_sched: bool = True) -> str:
    """渲染 templates/perfetto-config.textproto（全面数据源 + 三 buffer）。

    数据源名严格按 https://perfetto.dev/docs/data-sources/frametimeline ：
      android.surfaceflinger.frametimeline（注意无 s）
    三 buffer：[0] ftrace / [1] process_stats+packages+log / [2] FrameTimeline。
    """
    tmpl = (config.TEMPLATES_DIR / "perfetto-config.textproto").read_text(encoding="utf-8")
    # FrameTimeline 块（Android 12+，user 版可用），用 buffer 2 独立空间
    if include_frametimeline and env.get("frame_timeline_supported"):
        ft_block = """data_sources {
  config {
    name: "android.surfaceflinger.frametimeline"
    target_buffer: 2
  }
}"""
    else:
        ft_block = "# FrameTimeline: 不支持（需 Android 12+）"
    # atrace sched 已在模板固定行，include_sched=False 时留空（sched 事件仍在）
    atrace_sched = ""
    return (tmpl
            .replace("{{DURATION_MS}}", str(duration_ms))
            .replace("{{BUFFER_KB}}", str(config.DEFAULT_BUFFER_KB))
            .replace("{{APP_PKG}}", app_pkg)
            .replace("{{ATRACE_SCHED}}", atrace_sched)
            .replace("{{FRAMETIMELINE_BLOCK}}", ft_block))


@contextmanager
def capture(dev: Device, app_pkg: str, duration_s: int, run: str,
            name: str, env: dict, include_frametimeline: bool = True,
            include_sched: bool = True):
    """真正的录制上下文管理器。yield (trace_local_path_or_None, ok)。"""
    backend = env.get("trace_backend", "none")
    run_dir = config.TRACE_DIR / run
    run_dir.mkdir(parents=True, exist_ok=True)
    duration_ms = (duration_s + 5) * 1000  # 5s 余量
    ext = ".perfetto-trace" if backend == "perfetto" else ".ftrace"
    local_trace = run_dir / f"{name}{ext}"
    remote_trace = None
    started = False

    if backend == "none":
        print(f"[capture] ⚠ 无 trace 能力，跳过录制")
        yield None
        return

    try:
        if backend == "perfetto":
            # Android 12+ user 版：perfetto 只能读 /data/misc/perfetto-configs/
            cfg_remote = "/data/misc/perfetto-configs/apb_cfg.pbtxt"
            remote_trace = "/data/misc/perfetto-traces/apb_trace.perfetto-trace"
            cfg_text = _render_config(duration_ms, app_pkg, env, include_frametimeline, include_sched)
            cfg_local = run_dir / f"{name}.pbtxt"
            cfg_local.write_text(cfg_text, encoding="utf-8")
            dev.push(str(cfg_local), cfg_remote)
            dev.rm(remote_trace)
            rc = dev.perfetto_start(cfg_remote, remote_trace, txt=True)
            if rc != 0:
                yield None
                return
            started = True
            yield str(local_trace)
        else:  # atrace
            rc = dev.atrace_start(app_pkg, buf_kb=config.DEFAULT_BUFFER_KB // 2)
            if rc != 0:
                yield None
                return
            started = True
            remote_trace = "/data/local/tmp/apb_trace.ftrace"
            yield str(local_trace)
    finally:
        if not started:
            return
        try:
            if backend == "perfetto":
                # 等录制结束（--background 完成后进程退出）
                dev.perfetto_wait(remote_trace, timeout=duration_ms // 1000 + 60)
                if dev.file_exists(remote_trace):
                    dev.pull(remote_trace, str(local_trace))
                    print(f"[capture] ✓ trace -> {local_trace}")
                else:
                    print(f"[capture] ✗ 设备上无 trace 文件 {remote_trace}")
                    local_trace = None
            else:  # atrace
                dev.atrace_stop(remote_path=remote_trace)
                if dev.file_exists(remote_trace):
                    dev.pull(remote_trace, str(local_trace))
                    print(f"[capture] ✓ atrace -> {local_trace}")
                else:
                    print(f"[capture] ✗ atrace 无输出")
                    local_trace = None
        except Exception as e:
            print(f"[capture] 收尾异常: {e}")
            local_trace = None


# ── 通用辅助：后台预跑 N app 制造内存压力 ──────────────────────────
def warm_background_apps(dev: Device, app_list: list[str], n: int,
                         use_s: int = 8) -> None:
    if n <= 0:
        return
    chosen = app_list[:n]
    print(f"[capture] 后台预跑 {len(chosen)} 个 app 制造内存压力...")
    for pkg in chosen:
        if dev.app_start(pkg):
            time.sleep(use_s)
            dev.home()
            time.sleep(1)


# ── CLI ────────────────────────────────────────────────────────────
def main(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    env = setup_env.load_env()
    serial = args.serial or env.get("serial")
    dev = Device(serial)

    # 解析 app_list
    app_list = (args.app_list.split(",") if args.app_list
                else list(config.DEFAULT_APP_LIST))

    # 分派
    from .benchmarks import startup, jank_fps, camera_reload, keepalive
    raw = None
    if args.type in ("startup", "all"):
        raw = startup.run(dev, args, env, app_list)
        if args.type == "all" and raw:
            _save_raw(args.run, "startup", raw, env)
    if args.type in ("jank", "all"):
        raw = jank_fps.run(dev, args, env, app_list)
        if args.type == "all" and raw:
            _save_raw(args.run + "_jank", "jank", raw, env)
    if args.type in ("camera", "all"):
        raw = camera_reload.run(dev, args, env, app_list)
        if args.type == "all" and raw:
            _save_raw(args.run + "_camera", "camera", raw, env)
    if args.type in ("cache", "all"):
        # cache 已并入 keepalive（作为轻量预设：单轮 + scroll 模式 + per-app PSS）
        setattr(args, "_cache_preset", True)
        raw = keepalive.run(dev, args, env, app_list)
        if args.type == "all" and raw:
            _save_raw(args.run + "_cache", "keepalive", raw, env)
    if args.type in ("keepalive", "all"):
        raw = keepalive.run(dev, args, env, app_list)
        if args.type == "all" and raw:
            _save_raw(args.run + "_keepalive", "keepalive", raw, env)
    if args.type == "cpu":
        raw = jank_fps.run(dev, args, env, app_list, force_cpu=True)

    # 单类型（非 all）落盘 + 可选 analyze
    # 注：cache 已并入 keepalive，落盘/分析用 keepalive 类型
    if args.type in ("startup", "jank", "cache", "cpu", "camera", "keepalive") and raw:
        suffix = {"jank": "", "cpu": "_cpu", "cache": "_cache",
                  "startup": "", "camera": "_camera", "keepalive": "_keepalive"}[args.type]
        bench_type = "keepalive" if args.type == "cache" else args.type
        _save_raw(args.run + suffix, bench_type, raw, env)

    if not args.no_analyze and raw and args.type != "all":
        from . import trace_analyze
        suffix = {"jank": "", "cpu": "_cpu", "cache": "_cache",
                  "startup": "", "camera": "_camera", "keepalive": "_keepalive"}[args.type]
        trace_analyze.analyze_run(args.run + suffix, env)
    return 0


def _save_raw(run_name: str, bench_type: str, items: list, env: dict) -> None:
    raw = {"run": run_name, "type": bench_type,
           "device": {"model": env.get("model"), "brand": env.get("brand"),
                      "android_version": env.get("android_version"),
                      "sdk": env.get("sdk"), "abi": env.get("abi"),
                      "trace_backend": env.get("trace_backend")},
           "items": items}
    p = config.RESULT_DIR / f"{run_name}.raw.json"
    p.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[capture] raw -> {p}")
