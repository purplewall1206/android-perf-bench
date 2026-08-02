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

# ── ATC'26 A2 论文 Appendix A.1 的 Shared Benchmark Workload ───────
# 23 个 app 依次启动（每个做代表性操作后切后台，部分保持后台音频/导航），
# 最后启动相机制造内存压力峰值。用于测：相机启动延迟、相机后存活 app 数、
# 整轮 MemAvailable、direct reclaim、各 app 冷/热启动时间。
# 包名按国内常见版填充，实际跑前可用 --app-list 覆盖为设备上已装的包。
A2_BENCHMARK_APPS = [
    # (package, 类别, 动作, 是否后台保活)
    ("com.android.weather",          "系统工具",   "启动加载首页",      False),
    ("com.android.contacts",         "系统工具",   "加载联系人",        False),
    ("com.android.mms",              "系统工具",   "加载会话",          False),
    ("com.huawei.appmarket",         "应用分发",   "加载精选页",        False),
    ("com.lvmama.android.travel",    "视频剪辑",   "加载编辑器",        False),  # CapCut 占位
    ("tv.danmaku.bili",              "在线视频",   "播放视频(后台音频)", True),
    ("com.tencent.qqlive",           "在线视频",   "播放视频(后台音频)", True),
    ("com.ss.android.article.news",  "新闻聚合",   "加载新闻流",        False),
    ("com.sankuai.meituan",          "本地生活",   "加载首页",          False),
    ("com.huawei.health",            "健康健身",   "加载仪表盘",        False),
    ("com.xunmeng.pinduoduo",        "电商",       "加载商品流",        False),
    ("com.xingin.xhs",               "社交电商",   "滚动信息流",        False),
    ("com.taobao.taobao",            "电商",       "加载商品流",        False),
    ("com.baidu.searchbox",          "搜索引擎",   "加载搜索页",        False),
    ("com.chaozh.iReaderFree",       "数字阅读",   "加载书架",          False),
    ("com.tencent.mobileqq",         "即时通讯",   "加载会话",          False),
    ("com.tencent.qqmusic",          "音乐流媒体", "播放歌曲(后台音乐)", True),
    ("com.eg.android.AlipayGphone",  "数字支付",   "加载首页",          False),
    ("com.autonavi.minimap",         "地图导航",   "开始导航(后台语音)", True),
    ("com.kuaishou.lite",            "短视频",     "滚动×3",            False),
    ("com.ss.android.ugc.aweme",     "短视频",     "滚动×3",            False),
    ("com.tencent.mm",               "即时通讯",   "加载会话",          False),
]

# 相机包名（按厂商适配，user 版多数走 com.android.camera）
CAMERA_PACKAGES = [
    "com.android.camera",
    "com.huawei.camera",
    "com.hihonor.camera",
    "com.miui.camera",
    "com.oppo.camera",
    "com.coloros.camera",
    "org.codeaurora.snapcam",
    "com.mediatek.camera",
]

# ── 工作负载默认参数 ────────────────────────────────────────────────
DEFAULT_STARTUP_REPEAT = 10
DEFAULT_SCROLL_DURATION = 30       # 秒
DEFAULT_USE_DURATION = 30          # cache benchmark 每 app 前台秒数
DEFAULT_COOLDOWN = 3               # 秒
DEFAULT_CAMERA_INTERVAL = 1        # camera_reload 每 app 启动间隔秒数（论文 A.1 用 1s）
DEFAULT_CAMERA_USE_DURATION = 5    # camera_reload 每 app 前台使用秒数（论文动作后即切后台）
DEFAULT_BUFFER_KB = 262144         # perfetto buffer (256MB，RING_BUFFER 模式不丢数据)
DEFAULT_TRACE_EXTRA_DURATION_MS = 5000  # trace 比 workload 多录的余量(ms)


def ensure_dirs() -> None:
    """创建所有输出目录。各阶段入口都会调用。"""
    for d in (OUT_DIR, TRACE_DIR, RESULT_DIR, REPORT_DIR, BIN_DIR):
        d.mkdir(parents=True, exist_ok=True)
