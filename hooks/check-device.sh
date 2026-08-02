#!/usr/bin/env sh
# PreToolUse hook: 在跑 apb capture/run 前，检查 adb 设备已连接。
#
# Claude Code hooks 协议要点：
#   - stdin 收到 JSON（含 tool_name, tool_input.command 等）
#   - stdout 会被当 JSON 解析（严格 schema），故本脚本 stdout 保持空
#   - exit 0 = 放行，exit 2 = 阻断，其他非 0 = 错误
#   - 提示信息走 stderr（会作为反馈展示，不参与 JSON 解析）
#
# 本脚本只做"未连设备时提醒"，不阻断（exit 0），让 apb 自己处理错误。

# stdin 是工具调用的 JSON。用 grep 提取命令文本（避免依赖 jq）。
# 只在命令含 "apb" 时检查（避免每次 Bash 都跑 adb devices）。
input="$(cat)"
case "$input" in
  *apb*) ;;  # 含 apb，继续检查
  *) exit 0 ;;  # 不相关，直接放行
esac

# 检查 adb 设备（MSYS_NO_PATHCONV 避免 Git Bash 路径转换）
if command -v adb >/dev/null 2>&1; then
  if ! MSYS_NO_PATHCONV=1 adb devices 2>/dev/null | grep -q 'device$'; then
    echo "[android-perf-bench] ⚠ 未检测到已连接的 Android 设备。" >&2
    echo "  请确认手机已 USB 连接、开启 USB 调试并授权。运行 'adb devices' 查看。" >&2
    echo "  参考 references/troubleshooting.md 排查连接问题。" >&2
  fi
fi
# 始终放行（让 apb 自己报错处理），stdout 保持空
exit 0
