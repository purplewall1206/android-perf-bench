"""apb CLI 入口。

子命令：
  setup    阶段0：探测设备 + 下载 trace_processor_shell + 检查依赖
  capture  阶段1：抓 trace + 跑 workload
  analyze  阶段2：解析 trace，计算指标
  report   阶段3：生成 CSV/JSON + Markdown + HTML + 图
  run      capture + analyze 串联（report 需单独跑以对比多 run）

用法: python -m apb <subcommand> [options]
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="apb",
        description="android-perf-bench: Android 性能测试 harness",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ── setup ──────────────────────────────────────────────────────
    p_setup = sub.add_parser("setup", help="探测设备 + 下载 trace_processor_shell + 检查依赖")
    p_setup.add_argument("--serial", default=None, help="指定设备 serial（多设备时）")
    p_setup.add_argument("--install-deps", action="store_true", help="自动 pip install 缺失的 Python 包")
    p_setup.add_argument("--init-u2", action="store_true", help="推送 uiautomator2 agent 到设备")

    # ── capture ────────────────────────────────────────────────────
    p_cap = sub.add_parser("capture", help="抓 trace + 跑 workload")
    p_cap.add_argument("--type", required=True,
                       choices=["startup", "jank", "cache", "cpu", "camera", "all"],
                       help="测试类型")
    p_cap.add_argument("--app", default=None, help="目标 app 包名（startup/jank/cpu）")
    p_cap.add_argument("--app-list", default=None, help="逗号分隔的包名列表（cache）")
    p_cap.add_argument("--run", default="baseline", help="本次 run 的名字（如 baseline/variant）")
    # startup
    p_cap.add_argument("--repeat", type=int, default=None, help="启动重复次数（startup）")
    p_cap.add_argument("--launch-type", default="cold", choices=["cold", "hot", "both"], help="启动类型")
    # jank / cpu
    p_cap.add_argument("--scroll-duration", type=int, default=None, help="滚动秒数（jank/cpu）")
    p_cap.add_argument("--scroll-mode", default="swipe", choices=["swipe", "fling"])
    p_cap.add_argument("--scroll-scale", type=float, default=0.8)
    p_cap.add_argument("--warmup", type=int, default=5, help="启动后等待稳定秒数")
    # cache
    p_cap.add_argument("--use-duration", type=int, default=None, help="每 app 前台秒数（cache）")
    # camera_reload
    p_cap.add_argument("--camera-repeat", type=int, default=3, help="完整加压流程（N app→相机×1）重复轮数（camera，论文 A.1 跑 500 轮）")
    p_cap.add_argument("--camera-use-duration", type=int, default=None, help="camera 模式每 app 前台秒数")
    p_cap.add_argument("--camera-interval", type=int, default=None, help="camera 模式每 app 启动间隔秒数")
    # 通用
    p_cap.add_argument("--background-apps", type=int, default=0, help="后台预跑 N 个 app 制造内存压力")
    p_cap.add_argument("--serial", default=None)
    p_cap.add_argument("--no-analyze", action="store_true", help="只抓 trace，不自动解析")

    # ── analyze ────────────────────────────────────────────────────
    p_an = sub.add_parser("analyze", help="解析 trace，计算指标")
    p_an.add_argument("--run", default="baseline", help="要解析的 run 名")
    p_an.add_argument("--trace", default=None, help="直接指定单个 trace 文件路径")

    # ── report ─────────────────────────────────────────────────────
    p_rep = sub.add_parser("report", help="生成对比报告")
    p_rep.add_argument("--runs", required=True, help="逗号分隔的 run json，如 baseline.json,variant.json")
    p_rep.add_argument("--baseline", default=None, help="基线 run 名（默认第一个）")

    # ── run (capture+analyze 串联) ─────────────────────────────────
    p_run = sub.add_parser("run", help="capture + analyze 串联（report 需单独跑）")
    p_run.add_argument("--type", required=True, choices=["startup", "jank", "cache", "cpu", "camera", "all"])
    p_run.add_argument("--app", default=None)
    p_run.add_argument("--app-list", default=None)
    p_run.add_argument("--run", default="baseline")
    p_run.add_argument("--repeat", type=int, default=None)
    p_run.add_argument("--launch-type", default="cold", choices=["cold", "hot", "both"])
    p_run.add_argument("--scroll-duration", type=int, default=None)
    p_run.add_argument("--scroll-mode", default="swipe")
    p_run.add_argument("--scroll-scale", type=float, default=0.8)
    p_run.add_argument("--warmup", type=int, default=5)
    p_run.add_argument("--use-duration", type=int, default=None)
    p_run.add_argument("--background-apps", type=int, default=0)
    p_run.add_argument("--camera-repeat", type=int, default=3)
    p_run.add_argument("--camera-use-duration", type=int, default=None)
    p_run.add_argument("--camera-interval", type=int, default=None)
    p_run.add_argument("--serial", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "setup":
        from . import setup_env
        return setup_env.main(serial=args.serial,
                              install_deps=args.install_deps,
                              init_u2=args.init_u2)

    if args.cmd == "capture":
        from . import trace_capture
        return trace_capture.main(args)

    if args.cmd == "analyze":
        from . import trace_analyze
        return trace_analyze.main(args)

    if args.cmd == "report":
        from . import report
        return report.main(args)

    if args.cmd == "run":
        from . import trace_capture, trace_analyze
        rc = trace_capture.main(args)
        if rc != 0:
            return rc
        return trace_analyze.main(args)

    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
