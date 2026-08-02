# 排错手册 (troubleshooting.md)

## 设备未发现
`apb setup` 报 "no device"：
1. `adb devices` 确认是否列出。空列表 → 检查 USB 连接、驱动（Windows 可能需 OEM USB driver）、开发者选项→USB 调试是否开。
2. `unauthorized` → 手机上点"允许 USB 调试"对话框；或 `adb kill-server && adb start-server`。
3. 多设备：`apb setup --serial <serial>` 指定。

## trace 抓不到 / perfetto 命令失败
1. `ro.build.type == user` 且无 root → perfetto 抓 atrace_apps 受限。`apb capture` 自动降级到 atrace。
2. `adb shell perfetto` 报 command not found → Android < 10 或被裁剪。用 atrace 降级路径。
3. perfetto 输出 trace 为空 → 检查配置 buffer 大小、duration、atrace_apps 是否含目标包名；`adb shell dumpsys package <pkg>` 确认 app 可被调试（`atrace -a` 需 app debuggable 或系统 app，root 可绕过）。
4. `perfetto -c ... --txt` 报错 → 老版 perfetto 不支持 `--txt`，改用二进制 proto 或升级设备。

## FrameTimeline 不可用 / jank_type 缺失
- FrameTimeline 需 **Android 12+** + perfetto 数据源 `android.surfaceflinger.frametimelines`。
- atrace 降级路径**无** FrameTimeline。此时 jank 仅用 `Choreographer#doFrame` 阈值法（>16.7ms）。
- `apb setup` 输出的设备摘要会标明 `frame_timeline: supported|unsupported`。

## Choreographer#doFrame 查询为空
- 该 slice 由 atrace `view` 类别产生。确认 trace 配置含 `atrace_categories: "view"` 且 `atrace_apps` 含目标包名。
- app 不渲染（纯 SurfaceView，如游戏/视频播放器）可能无 doFrame → 改用 FrameTimeline 或 gfxinfo。
- 备用：`adb shell dumpsys gfxinfo <pkg>` 的 frame stats。

## uiautomator2 连不上 / atx-agent 失败
1. `python -m uiautomator2 init` 重新推送 agent。
2. 端口冲突：`adb forward tcp:7912 tcp:7912` 手动；或 `u2.connect(serial)` 指定。
3. `DeviceError` → 重启 adb：`adb kill-server; adb start-server`，重新 init。
4. MIUI/EMUI 等需在开发者选项开"USB 调试（安全设置）"允许模拟点击/手势。

## am start -W 无 LaunchState 行
老版 Android（< 10）`am start -W` 不输出 `LaunchState`。此时用 trace 的 `android_startup` metric 推断 startup_type，或仅记录 WaitTime/TotalTime。

## trace_processor_shell 下载失败
- GitHub release 在某些网络受限。镜像/代理后重试。
- 备用：`pip install perfetto`，Python 包首次用 `TraceProcessor(trace=...)` 时会自动下载二进制到缓存目录。
- 验证：`trace_processor_shell --version`。

## dumpsys meminfo 解析不到 app
- app 已被杀（不在缓存）。这是 cache benchmark 的正常现象（缓存数下降）。
- 包名带进程名后缀（`:process`）→ 用 package 前缀匹配。

## 报告图表中文乱码
matplotlib 默认字体无中文。`report.py` 已设 `rcParams['font.sans-serif']` 包含常见中文字体；若无，图表改英文标签（已默认英文）。

## Windows 路径问题
- adb forward / perfetto 路径用正斜杠或双反斜杠。
- `trace_processor_shell.exe` 在 Windows 需 `.exe` 后缀，`setup_env.py` 已按平台处理。

## 权限不足（force-stop / pm clear 失败）
- `pm clear` 需 app 不在前台或 root。先 `am force-stop` 再 clear。
- 某些厂商 ROM 限制 `pm clear` 系统包。改用 `am force-stop` + 等 lmkd 自然清理模拟冷启动。
