# 依赖的 App 清单

本测试套件的 `cache` / `camera` / `startup` / `jank` 测试会启动若干 app 制造负载。运行前请确认设备上已安装所需 app，缺失的需先安装（否则该 app 会被自动跳过，影响内存压力测试的负载量）。

> 💡 运行 `python -m apb setup` 会**自动扫描设备已装 app**，并提示哪些测试范围的 app 还没装。

## 第一方 app（按手机品牌不同，自动适配）

这些 app 的包名因厂商而异，套件会根据 `ro.product.brand` 自动选对应包名。

| 功能 | 用途 | 缺失影响 |
|---|---|---|
| **相机** | camera_reload 测试的核心（最后启动制造内存压力） | camera_reload 无法运行 |
| 健康 | 论文 A.1 workload 的一项 | 该槽位跳过 |
| 应用商店 | 论文 A.1 workload 的一项 | 该槽位跳过 |
| 天气/联系人/短信 | 论文 A.1 workload 的系统工具类 | 该槽位跳过 |
| 桌面(launcher) | 点击启动 app 方式需要 | 回退到 adb 启动 |

套件内置了 HONOR/HUAWEI/XIAOMI/REDMI/OPPO/ONEPLUS/VIVO/SAMSUNG/GOOGLE 的第一方包名映射。如果你的品牌不在列，setup 会提示，可在 `scripts/apb/config.py` 的 `FIRST_PARTY_APPS` 里补充。

## 第三方 app（包名跨厂商通用，需自行安装）

`camera_reload` 和 `cache` 默认用论文 ATC'26 A.1 Appendix A.1 的 app 列表，建议装以下（国内应用商店均可下载）：

| App | 包名 | 论文 A.1 用途 |
|---|---|---|
| 抖音 | `com.ss.android.ugc.aweme` | 短视频滚动 |
| 快手极速版 | `com.kuaishou.lite` | 短视频滚动 |
| 微信 | `com.tencent.mm` | 即时通讯 |
| QQ | `com.tencent.mobileqq` | 即时通讯 |
| 哔哩哔哩 | `tv.danmaku.bili` | 在线视频(后台音频) |
| 腾讯视频 | `com.tencent.qqlive` | 在线视频(后台音频) |
| QQ音乐 | `com.tencent.qqmusic` | 音乐(后台) |
| 今日头条 | `com.ss.android.article.news` | 新闻流 |
| 小红书 | `com.xingin.xhs` | 社交流滚动 |
| 淘宝 | `com.taobao.taobao` | 电商 |
| 拼多多 | `com.xunmeng.pinduoduo` | 电商 |
| 美团 | `com.sankuai.meituan` | 本地生活 |
| 支付宝 | `com.eg.android.AlipayGphone` | 数字支付 |
| 高德地图 | `com.autonavi.minimap` | 导航(后台语音) |
| 百度 | `com.baidu.searchbox` | 搜索 |

**最小测试集**：装 5-8 个即可跑通完整流程（内存压力随 app 数增加）。

## 自定义 app 列表

跑测试时可用 `--app-list` 覆盖默认列表，用设备上已装的任意 app：
```bash
python -m apb capture --type camera --app-list "com.tencent.mm,com.ss.android.ugc.aweme,com.xingin.xhs" --run baseline
```

## 测试 app 是否可被启动

```bash
adb shell am start -W <包名>/.MainActivity   # 看是否 Status: ok
```
套件的 `device.py` 已封装 `resolve_activity` 自动解析 launcher activity，通常无需手动指定 activity。
