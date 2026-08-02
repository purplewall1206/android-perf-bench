#!/usr/bin/env sh
# PreToolUse hook: 当 Bash 命令含 "apb capture" 或 "apb run" 时，检查 adb 设备已连接。
# 设备未连接时输出提示到 stderr（Claude Code 会把它作为反馈），不阻断（exit 0）。
# 命令 JSON 通过 stdin 传入（Claude Code hooks 协议）。
input="$(cat)"
cmd="$(printf '%s' "$input" | grep -o '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1)"

# 只在 apb capture / apb run 命令时检查
if ! printf '%s' "$cmd" | grep -qE 'apb (capture|run)'; then
    exit 0
fi

# 检查 adb 设备（MSYS_NO_PATHCONV 避免路径转换）
if ! MSYS_NO_PATHCONV=1 adb devices 2>/dev/null | grep -qE 'device$'; then
    echo "[android-perf-bench] ⚠ 未检测到已连接的 Android 设备。请确认手机已通过 USB 连接、开启 USB 调试并授权。" >&2
    echo "[android-perf-bench] 运行 'adb devices' 查看；参考 troubleshooting.md 排查连接问题。" >&2
fi
exit 0
