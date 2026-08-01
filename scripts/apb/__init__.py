"""android-perf-bench (apb) — Android 性能测试 harness。

子模块：
  setup_env     阶段0：探测设备 + 下载 trace_processor_shell + 装依赖
  device        设备抽象（adbutils + uiautomator2）
  trace_capture 阶段1：perfetto/atrace 自适应抓取
  trace_analyze 阶段2：trace_processor_shell 跑 SQL/metrics
  metrics       指标计算（复刻 Fleet 公式）
  benchmarks    startup / jank_fps / cache_mem / cpu
  compare       基线 vs 方案对比
  report        阶段3：CSV/JSON + Markdown + HTML + 图
"""
__version__ = "0.1.0"
