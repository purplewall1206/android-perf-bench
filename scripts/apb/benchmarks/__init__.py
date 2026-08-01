"""benchmarks 包：startup / jank_fps / cache_mem / cpu。

每个 benchmark 暴露 run(dev, args, env, app_list) -> list[dict]，
返回的 item 列表会被 trace_capture._save_raw 落盘为 .raw.json。
"""
