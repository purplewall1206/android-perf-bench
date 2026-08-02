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

# ── 包名 → 桌面显示名（点击启动方式用）。覆盖测试范围内的常见 app ──
# 点击启动：回桌面 → 进 app 抽屉 → scroll.to(text=显示名) → click
# 缺失的包名，点击方式会先尝试用 PackageManager 查 label，查不到才回退 adb。
PACKAGE_DISPLAY_NAMES = {
    # 第三方
    "com.ss.android.ugc.aweme": "抖音",
    "com.ss.android.article.news": "今日头条",
    "com.kuaishou.lite": "快手极速版",
    "com.kuaishou.nebula": "快手极速版",
    "com.tencent.mm": "微信",
    "com.tencent.mobileqq": "QQ",
    "com.tencent.qqlive": "腾讯视频",
    "com.tencent.qqmusic": "QQ音乐",
    "tv.danmaku.bili": "哔哩哔哩",
    "com.xingin.xhs": "小红书",
    "com.taobao.taobao": "淘宝",
    "com.xunmeng.pinduoduo": "拼多多",
    "com.sankuai.meituan": "美团",
    "com.eg.android.AlipayGphone": "支付宝",
    "com.autonavi.minimap": "高德地图",
    "com.baidu.searchbox": "百度",
    "com.dragon.read": "番茄免费小说",
    "com.zhihu.android": "知乎",
    "com.jingdong.app.mall": "京东",
    "com.achievo.vipshop": "唯品会",
    "com.netease.cloudmusic": "网易云音乐",
    "com.sdu.didi.psnger": "滴滴出行",
    "ctrip.android.view": "携程旅行",
    "com.tencent.wetype": "讯飞输入法",
    "com.baidu.input_hihonor": "百度输入法",
    "com.android.chrome": "Chrome",
    "com.mi.globalbrowser": "浏览器",
    # 荣耀/华为第一方（显示名可能因版本微调，点击会先查 label）
    "com.hihonor.camera": "相机",
    "com.huawei.camera": "相机",
    "com.android.camera": "相机",
    "com.miui.camera": "相机",
    "com.hihonor.appmarket": "应用市场",
    "com.huawei.appmarket": "应用市场",
    "com.hihonor.health": "运动健康",
    "com.huawei.health": "运动健康",
    "com.hihonor.android.totemweather": "天气",
    "com.huawei.android.totemweather": "天气",
    "com.android.contacts": "联系人",
    "com.android.mms": "信息",
    "com.android.settings": "设置",
    "com.hihonor.android.launcher": "桌面",
}
# 第一方 app 的通用显示名（按槽位，用于点击启动的回退显示名）
SLOT_DISPLAY_NAMES = {
    "camera": "相机", "health": "运动健康", "appmarket": "应用市场",
    "weather": "天气", "contacts": "联系人", "mms": "信息",
}

# ── 第一方 app 包名：按厂商区分（相机/健康/应用商店/天气/联系人/短信/桌面）──
# 不同 ROM 的第一方 app 包名不同，用"功能槽位 → 各厂商候选包名"映射，
# setup 阶段自动探测设备品牌选对应包名。
FIRST_PARTY_APPS = {
    "camera": {  # 相机（camera_reload 必需）
        "HONOR": ["com.hihonor.camera"],
        "HUAWEI": ["com.huawei.camera"],
        "XIAOMI": ["com.android.camera", "com.miui.camera"],
        "REDMI":  ["com.android.camera", "com.miui.camera"],
        "OPPO":   ["com.android.camera", "com.oppo.camera", "com.coloros.camera"],
        "ONEPLUS":["com.android.camera", "com.oneplus.camera"],
        "VIVO":   ["com.android.camera", "com.vivo.camera"],
        "SAMSUNG":["com.sec.android.app.camera"],
        "GOOGLE": ["com.google.android.GoogleCamera"],
        "_fallback": ["com.android.camera", "org.codeaurora.snapcam", "com.mediatek.camera"],
    },
    "health": {  # 运动/健康（论文 A.1 用到）
        "HONOR": ["com.hihonor.health", "com.huawei.health"],
        "HUAWEI": ["com.huawei.health"],
        "XIAOMI": ["com.xiaomi.health", "com.mi.health"],
        "REDMI":  ["com.xiaomi.health", "com.mi.health"],
        "OPPO":   ["com.coloros.health", "com.heytap.health"],
        "ONEPLUS":["com.heytap.health"],
        "VIVO":   ["com.vivo.health"],
        "SAMSUNG":["com.samsung.android.app.shealth"],
        "GOOGLE": ["com.google.android.apps.fitness"],
        "_fallback": [],
    },
    "appmarket": {  # 应用商店（论文 A.1 用到 AppGallery）
        "HONOR": ["com.hihonor.appmarket", "com.huawei.appmarket"],
        "HUAWEI": ["com.huawei.appmarket"],
        "XIAOMI": ["com.xiaomi.market"],
        "REDMI":  ["com.xiaomi.market"],
        "OPPO":   ["com.heytap.market", "com.coloros.market"],
        "ONEPLUS":["com.heytap.market"],
        "VIVO":   ["com.vivo.appstore"],
        "SAMSUNG":["com.sec.android.app.samsungapps"],
        "GOOGLE": ["com.android.vending"],
        "_fallback": [],
    },
    "weather": {
        "HONOR": ["com.hihonor.android.totemweather", "com.huawei.android.totemweather"],
        "HUAWEI": ["com.huawei.android.totemweather"],
        "XIAOMI": ["com.miui.weather2"],
        "REDMI":  ["com.miui.weather2"],
        "OPPO":   ["com.coloros.weather2"],
        "ONEPLUS":["com.coloros.weather2"],
        "VIVO":   ["com.android.weather"],
        "SAMSUNG":["com.samsung.android.weather"],
        "GOOGLE": [],
        "_fallback": ["com.android.weather"],
    },
    "contacts": {  # AOSP 标准多数通用
        "_fallback": ["com.android.contacts"],
        "SAMSUNG": ["com.samsung.android.app.contacts"],
        "XIAOMI": ["com.android.contacts", "com.miui.contacts"],
        "REDMI":  ["com.android.contacts"],
    },
    "mms": {
        "_fallback": ["com.android.mms"],
        "SAMSUNG": ["com.samsung.android.messaging"],
        "HONOR": ["com.hihonor.mms", "com.android.mms"],
        "HUAWEI": ["com.huawei.mms", "com.android.mms"],
    },
    "launcher": {  # 桌面（点击启动 app 时回到桌面）
        "HONOR": ["com.hihonor.android.launcher"],
        "HUAWEI": ["com.huawei.android.launcher"],
        "XIAOMI": ["com.miui.home"],
        "REDMI":  ["com.miui.home"],
        "OPPO":   ["com.coloros.launcher"],
        "ONEPLUS":["net.oneplus.launcher", "com.coloros.launcher"],
        "VIVO":   ["com.bbk.launcher2"],
        "SAMSUNG":["com.sec.android.app.launcher"],
        "GOOGLE": ["com.google.android.apps.nexuslauncher"],
        "_fallback": ["com.android.launcher3"],
    },
}


def first_party_candidates(slot: str, brand: str) -> list[str]:
    """取某功能槽位在指定品牌下的候选包名列表（含 fallback）。"""
    table = FIRST_PARTY_APPS.get(slot, {})
    candidates = []
    if brand:
        candidates.extend(table.get(brand.upper(), []))
    candidates.extend(table.get("_fallback", []))
    # 去重保序
    seen = set()
    out = []
    for p in candidates:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


# ── ATC'26 A2 论文 Appendix A.1 的 Shared Benchmark Workload ───────
# 23 个 app 依次启动（每个做代表性操作后切后台，部分保持后台音频/导航），
# 最后启动相机制造内存压力峰值。用于测：相机启动延迟、相机后存活 app 数、
# 整轮 MemAvailable、direct reclaim、各 app 冷/热启动时间。
# 第三方 app 包名跨厂商通用；第一方 app（健康/应用商店）用 first_party_candidates 动态解析。
A2_THIRD_PARTY_APPS = [
    # (package, 类别, 动作, 是否后台保活)  —— 第三方 app，包名通用
    ("tv.danmaku.bili",              "在线视频",   "播放视频(后台音频)", True),
    ("com.tencent.qqlive",           "在线视频",   "播放视频(后台音频)", True),
    ("com.ss.android.article.news",  "新闻聚合",   "加载新闻流",        False),
    ("com.sankuai.meituan",          "本地生活",   "加载首页",          False),
    ("com.xunmeng.pinduoduo",        "电商",       "加载商品流",        False),
    ("com.xingin.xhs",               "社交电商",   "滚动信息流",        False),
    ("com.taobao.taobao",            "电商",       "加载商品流",        False),
    ("com.baidu.searchbox",          "搜索引擎",   "加载搜索页",        False),
    ("com.tencent.mobileqq",         "即时通讯",   "加载会话",          False),
    ("com.tencent.qqmusic",          "音乐流媒体", "播放歌曲(后台音乐)", True),
    ("com.eg.android.AlipayGphone",  "数字支付",   "加载首页",          False),
    ("com.autonavi.minimap",         "地图导航",   "开始导航(后台语音)", True),
    ("com.kuaishou.lite",            "短视频",     "滚动×3",            False),
    ("com.ss.android.ugc.aweme",     "短视频",     "滚动×3",            False),
    ("com.tencent.mm",               "即时通讯",   "加载会话",          False),
]
# 第一方槽位（论文 A.1 用到，需按品牌解析）
A2_FIRST_PARTY_SLOTS = ["weather", "contacts", "mms", "appmarket", "health"]


def build_a2_app_list(brand: str) -> list[str]:
    """构建论文 A.1 的 app 列表：第一方(按品牌) + 第三方。"""
    pkgs = []
    for slot in A2_FIRST_PARTY_SLOTS:
        pkgs.extend(first_party_candidates(slot, brand))
    pkgs.extend(p for p, *_ in A2_THIRD_PARTY_APPS)
    return pkgs


# 相机包名候选（兼容旧代码；新代码用 first_party_candidates("camera", brand)）
CAMERA_PACKAGES = first_party_candidates("camera", "")  # 含全厂商 fallback

# ── 工作负载默认参数 ────────────────────────────────────────────────
DEFAULT_STARTUP_REPEAT = 10
DEFAULT_SCROLL_DURATION = 30       # 秒
DEFAULT_USE_DURATION = 30          # cache benchmark 每 app 前台秒数
DEFAULT_COOLDOWN = 3               # 秒
DEFAULT_CAMERA_INTERVAL = 1        # camera_reload 每 app 启动间隔秒数（论文 A.1 用 1s）
DEFAULT_CAMERA_USE_DURATION = 5    # camera_reload 每 app 前台使用秒数（论文动作后即切后台）
DEFAULT_BUFFER_KB = 262144         # perfetto buffer (256MB，RING_BUFFER 模式不丢数据)
DEFAULT_TRACE_EXTRA_DURATION_MS = 5000  # trace 比 workload 多录的余量(ms)

# ── keepalive（保活压力测试）默认参数 ──────────────────────────────
DEFAULT_KA_FOREGROUND = 30         # 每 app 前台秒数
DEFAULT_KA_BACKGROUND_WAIT = 10    # 进入后台后等待秒数（再启动下一个）
DEFAULT_KA_ROUNDS = 1              # 重复轮数
DEFAULT_KA_TARGET_COUNT = 50       # 目标 app 数（实际取设备已装交集）

# keepalive 候选 app 池（国内常见，跑时取设备已装交集，凑到目标数量）
# 含第三方 + 第一方，覆盖社交/视频/电商/工具/资讯等，制造充分的保活压力
KEEPALIVE_APP_POOL = [
    # 社交/通讯
    "com.tencent.mm", "com.tencent.mobileqq", "com.ss.android.ugc.aweme",
    "com.xingin.xhs", "com.smile.gifmaker", "com.sina.weibo",
    "com.zhihu.android", "com.linkedin.android",
    # 视频/直播
    "tv.danmaku.bili", "com.tencent.qqlive", "com.ss.android.article.news",
    "com.kuaishou.lite", "com.netease.cloudmusic", "com.tencent.qqmusic",
    "com.kugou.android", "com.duowan.kiwi",
    # 电商
    "com.taobao.taobao", "com.xunmeng.pinduoduo", "com.jingdong.app.mall",
    "com.eg.android.AlipayGphone", "com.achievo.vipshop", "com.suning.mobile.ebuy",
    "com.xunmeng.pinduoduo", "com.tmall.wireless",
    # 本地/出行/地图
    "com.sankuai.meituan", "com.sdu.didi.psnger", "com.autonavi.minimap",
    "com.baidu.BaiduMap", "ctrip.android.view", "com.Qunar",
    "com.taobao.trip",
    # 资讯/阅读/工具
    "com.baidu.searchbox", "com.dragon.read", "com.chaozh.iReaderFree",
    "com.ifeng.news2", "com.sohu.newsclient", "com.netease.news",
    "com.baidu.tieba", "com.wondertek.paper",
    # 浏览器/输入法/系统
    "com.android.chrome", "com.mi.globalbrowser", "com.baidu.input_hihonor",
    "com.tencent.wetype", "com.android.settings", "com.android.contacts",
    "com.android.mms", "com.hihonor.android.totemweather",
    # 第一方（按品牌补充，setup 探测后动态加）
]


def ensure_dirs() -> None:
    """创建所有输出目录。各阶段入口都会调用。"""
    for d in (OUT_DIR, TRACE_DIR, RESULT_DIR, REPORT_DIR, BIN_DIR):
        d.mkdir(parents=True, exist_ok=True)
