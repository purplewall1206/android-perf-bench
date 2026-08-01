"""android-perf-bench 全局配置：路径、app 列表、阈值、perfetto release 版本。"""
from pathlib import Path

# ── 路径 ────────────────────────────────────────────────────────────
# scripts/ 目录（即本文件所在 apb 包的上两级）
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SKILL_DIR = SCRIPTS_DIR.parent
TEMPLATES_DIR = SKILL_DIR / "templates"
REFERENCES_DIR = SKILL_DIR / "references"

# 输出根目录（默认在 scripts/out/，可被环境变量 APB_OUT 覆盖）
OUT_DIR = Path(__file__).resolve().parent.parent / "out"
TRACE_DIR = OUT_DIR / "traces"
RESULT_DIR = OUT_DIR / "results"
REPORT_DIR = OUT_DIR / "report"
BIN_DIR = SCRIPTS_DIR / ".bin"          # trace_processor_shell 存放
ENV_JSON = OUT_DIR / "env.json"         # setup 阶段写入的设备能力摘要

# ── perfetto trace_processor_shell ─────────────────────────────────
PERFETTO_VERSION = "v57.2"
PERFETTO_RELEASE_BASE = "https://github.com/google/perfetto/releases/download"
# (平台标识, 期望 zip 文件名, 解压后二进制名)
PERFETTO_ASSETS = {
    "windows-amd64": ("windows-amd64.zip", "trace_processor_shell.exe"),
    "linux-amd64":   ("linux-amd64.zip",   "trace_processor_shell"),
    "mac-amd64":     ("mac-amd64.zip",     "trace_processor_shell"),
    "mac-arm64":     ("mac-arm64.zip",     "trace_processor_shell"),
}

# ── 指标阈值（复刻 Fleet）────────────────────────────────────────
JANK_THRESHOLD_MS = 16.7       # 60Hz 一帧；相邻 doFrame 间隔超过此值计为 jank
GC_THREAD_NAME = "HeapTaskDaemon"

# ── 默认 app 列表（复刻 Fleet 18 个，可被 CLI --app-list 覆盖）──────
DEFAULT_APP_LIST = [
    "com.twitter.android",
    "com.facebook.katana",
    "com.instagram.android",
    "org.telegram.messenger",
    "jp.naver.line.android",
    "com.linkedin.android",
    "com.google.android.youtube",
    "com.ss.android.ugc.aweme",
    "tv.twitch.android.app",
    "com.wemesh.android",
    "sg.bigo.live",
    "com.spotify.music",
    "com.amazon.mShop.android.shopping",
    "com.google.android.apps.maps",
    "com.android.chrome",
    "org.mozilla.firefox",
    "com.rovio.angrybirds",
    "com.king.candycrushsaga",
]

# ── 工作负载默认参数 ────────────────────────────────────────────────
DEFAULT_STARTUP_REPEAT = 10
DEFAULT_SCROLL_DURATION = 30       # 秒
DEFAULT_USE_DURATION = 30          # cache benchmark 每 app 前台秒数
DEFAULT_COOLDOWN = 3               # 秒
DEFAULT_BUFFER_KB = 262144         # perfetto buffer (256MB，RING_BUFFER 模式不丢数据)
DEFAULT_TRACE_EXTRA_DURATION_MS = 5000  # trace 比 workload 多录的余量(ms)


def ensure_dirs() -> None:
    """创建所有输出目录。各阶段入口都会调用。"""
    for d in (OUT_DIR, TRACE_DIR, RESULT_DIR, REPORT_DIR, BIN_DIR):
        d.mkdir(parents=True, exist_ok=True)
