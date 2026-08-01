"""阶段 0 — 环境引导与设备能力探测。

`python -m apb setup` 做四件事：
  1. 检测 adb / 已连接设备
  2. 探测设备能力（型号、Android 版本、root/userdebug、perfetto·atrace、FrameTimeline）
  3. 下载对应平台的 trace_processor_shell（若未安装）
  4. 提示安装 Python 依赖；可选推送 uiautomator2 agent

结果写入 config.ENV_JSON，后续阶段（capture/analyze）读取以自适应。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from typing import Optional

from . import config


# ── 小工具 ──────────────────────────────────────────────────────────
def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    """运行命令，返回 (returncode, stdout+stderr 合并)。失败不抛。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout: {' '.join(cmd)}"
    except Exception as e:  # pragma: no cover
        return 1, str(e)


def _adb(*args: str, serial: Optional[str] = None, timeout: int = 30) -> tuple[int, str]:
    """运行 adb 命令。"""
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    return _run(cmd, timeout=timeout)


def _getprop(prop: str, serial: Optional[str] = None) -> str:
    rc, out = _adb("shell", "getprop", prop, serial=serial)
    return out.strip() if rc == 0 else ""


# ── 1. adb 与设备检测 ───────────────────────────────────────────────
def check_adb() -> tuple[bool, str]:
    rc, out = _run(["adb", "version"])
    ok = rc == 0 and "version" in out.lower()
    return ok, out.strip().splitlines()[0] if out else "adb 未找到"


def list_devices() -> list[str]:
    """返回已授权设备的 serial 列表（过滤掉 unauthorized/offline）。"""
    rc, out = _adb("devices")
    serials = []
    for line in (out or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


# ── 2. 设备能力探测 ─────────────────────────────────────────────────
def probe_device(serial: str) -> dict:
    """返回设备能力字典。"""
    model = _getprop("ro.product.model", serial) or "unknown"
    brand = _getprop("ro.product.brand", serial) or "unknown"
    manufacturer = _getprop("ro.product.manufacturer", serial) or brand
    android_ver = _getprop("ro.build.version.release", serial) or "?"
    sdk = _getprop("ro.build.version.sdk", serial) or "?"
    abi = _getprop("ro.product.cpu.abi", serial) or "?"
    build_type = _getprop("ro.build.type", serial) or "unknown"
    debuggable = _getprop("ro.debuggable", serial)

    # root 判定：build_type==userdebug 或 su 可用
    has_su = _adb("shell", "which", "su", serial=serial)[0] == 0
    is_root = build_type in ("userdebug", "eng") or has_su
    # 进一步验证 su 实际可用
    if has_su and build_type == "user":
        rc, _ = _adb("shell", "su", "-c", "id", serial=serial, timeout=10)
        if rc != 0:
            has_su = False
            is_root = False

    # perfetto / atrace 可用性
    perfetto_path = _adb("shell", "which", "perfetto", serial=serial)[1].strip()
    atrace_path = _adb("shell", "which", "atrace", serial=serial)[1].strip()
    rc, atrace_cats = _adb("shell", "atrace", "--list_categories", serial=serial, timeout=15)
    has_atrace_view = "view" in atrace_cats
    has_atrace_gfx = "gfx" in atrace_cats

    # FrameTimeline：Android 12+ (SDK 31)
    try:
        sdk_int = int(sdk)
    except ValueError:
        sdk_int = 0
    frame_timeline = sdk_int >= 31

    # 选择 trace 后端：
    #   - perfetto 可用 且 (root/userdebug 或 Android 12+) → perfetto（FrameTimeline 需它）
    #   - 否则 atrace 降级（无 FrameTimeline/jank_type 分解）
    # 注：Android 12+ user 版 perfetto 可用，但配置须放 /data/misc/perfetto-configs/（见 trace_capture.py）
    if perfetto_path and (is_root or sdk_int >= 31):
        trace_backend = "perfetto"
    elif atrace_path:
        trace_backend = "atrace"
    else:
        trace_backend = "none"

    # 主机信息
    info = {
        "serial": serial,
        "model": model,
        "brand": manufacturer,
        "android_version": android_ver,
        "sdk": sdk,
        "sdk_int": sdk_int,
        "abi": abi,
        "build_type": build_type,
        "debuggable": debuggable,
        "is_root": is_root,
        "has_su": has_su,
        "perfetto_path": perfetto_path,
        "atrace_path": atrace_path,
        "atrace_has_view": has_atrace_view,
        "atrace_has_gfx": has_atrace_gfx,
        "frame_timeline_supported": frame_timeline,
        "trace_backend": trace_backend,
    }
    return info


# ── 3. trace_processor_shell 下载 ──────────────────────────────────
def _host_asset() -> tuple[str, str]:
    """返回 (平台标识, 期望 zip 文件名)。"""
    plat = sys.platform
    mach = platform.machine().lower()
    if plat == "win32":
        key = "windows-amd64"
    elif plat == "linux":
        key = "linux-amd64"
    elif plat == "darwin":
        key = "mac-arm64" if mach in ("arm64", "aarch64") else "mac-amd64"
    else:  # 兜底
        key = "linux-amd64"
    zip_name, bin_name = config.PERFETTO_ASSETS[key]
    return key, zip_name


def ensure_trace_processor_shell() -> str:
    """确保 trace_processor_shell 已下载，返回其路径。已存在则跳过。"""
    config.ensure_dirs()
    key, _ = _host_asset()
    bin_name = config.PERFETTO_ASSETS[key][1]
    bin_path = config.BIN_DIR / bin_name
    if bin_path.exists():
        return str(bin_path)

    # 找系统中已存在的
    found = shutil.which("trace_processor_shell") or shutil.which("trace_processor_shell.exe")
    if found:
        return found

    zip_name = config.PERFETTO_ASSETS[key][0]
    url = f"{config.PERFETTO_RELEASE_BASE}/{config.PERFETTO_VERSION}/{zip_name}"
    zip_path = config.BIN_DIR / zip_name
    print(f"[setup] 下载 trace_processor_shell: {url}")
    try:
        urllib.request.urlretrieve(url, zip_path)
    except Exception as e:
        print(f"[setup] ✗ 下载失败: {e}")
        print(f"        请手动下载 {url}，解压后将 {bin_name} 放到 {config.BIN_DIR}")
        return ""

    # 解压（zip 内有 windows-amd64/ 子目录，只提取目标 exe 到 .bin 根）
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.namelist():
                if info.endswith("/") or info in ("__MACOSX",):
                    continue
                name = Path(info).name
                if name == bin_name:
                    target = config.BIN_DIR / bin_name
                    with z.open(info) as src, open(target, "wb") as dst:
                        dst.write(src.read())
                    break
    except Exception as e:
        print(f"[setup] ✗ 解压失败: {e}")
        return ""
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass

    # 非 Windows 设可执行
    if sys.platform != "win32" and bin_path.exists():
        bin_path.chmod(0o755)

    if bin_path.exists():
        print(f"[setup] ✓ trace_processor_shell -> {bin_path}")
        return str(bin_path)
    return ""


# ── 4. Python 依赖检查 ─────────────────────────────────────────────
def check_python_deps() -> dict:
    """检查关键 Python 包是否已装。返回 {包名: bool}。"""
    deps = {}
    for mod, pkg in [("uiautomator2", "uiautomator2"),
                     ("perfetto", "perfetto"),
                     ("adbutils", "adbutils"),
                     ("pandas", "pandas"),
                     ("matplotlib", "matplotlib"),
                     ("jinja2", "jinja2")]:
        try:
            __import__(mod)
            deps[pkg] = True
        except ImportError:
            deps[pkg] = False
    return deps


def install_python_deps() -> None:
    """pip install -r requirements.txt。"""
    req = config.SCRIPTS_DIR / "requirements.txt"
    print(f"[setup] pip install -r {req}")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)], check=False)


# ── 主入口 ──────────────────────────────────────────────────────────
def main(serial: Optional[str] = None,
         install_deps: bool = False,
         init_u2: bool = False) -> int:
    config.ensure_dirs()

    # adb
    adb_ok, adb_ver = check_adb()
    print(f"[setup] adb: {adb_ver}" if adb_ok else "[setup] ✗ adb 未找到，请先安装 Android platform-tools 并加入 PATH")
    if not adb_ok:
        return 1

    # 设备
    devices = list_devices()
    if not devices:
        print("[setup] ✗ 未发现已授权设备。请：")
        print("        1) 用 USB 连接手机并开启开发者选项 → USB 调试")
        print("        2) 在手机弹窗点'允许 USB 调试'")
        print("        3) 若显示 unauthorized，运行 adb kill-server && adb start-server")
        return 2

    if serial is None:
        serial = devices[0]
    elif serial not in devices:
        print(f"[setup] ✗ 指定的 serial={serial} 不在设备列表 {devices}")
        return 2
    print(f"[setup] 使用设备: {serial}")

    # 探测
    info = probe_device(serial)
    print()
    print("═" * 60)
    print(" 设备能力摘要")
    print("═" * 60)
    print(f"  型号        : {info['brand']} {info['model']}")
    print(f"  Android     : {info['android_version']} (SDK {info['sdk']}, {info['abi']})")
    print(f"  Build type  : {info['build_type']}  (root: {info['is_root']}, su: {info['has_su']})")
    print(f"  perfetto    : {info['perfetto_path'] or '不可用'}")
    print(f"  atrace      : {info['atrace_path'] or '不可用'} (view:{info['atrace_has_view']}, gfx:{info['atrace_has_gfx']})")
    print(f"  FrameTimeline: {'支持' if info['frame_timeline_supported'] else '不支持 (需 Android 12+)'}")
    print(f"  Trace 后端  : {info['trace_backend']}")
    if info['trace_backend'] == 'atrace':
        print("  ⚠ 使用 atrace 降级：jank 将仅用 doFrame 阈值法，无 jank_type 分解")
    print("═" * 60)
    print()

    # trace_processor_shell
    tps_path = ensure_trace_processor_shell()
    info["trace_processor_shell"] = tps_path

    # Python 依赖
    deps = check_python_deps()
    missing = [k for k, v in deps.items() if not v]
    if missing:
        print(f"[setup] 缺少 Python 包: {', '.join(missing)}")
        if install_deps:
            install_python_deps()
        else:
            print(f"        运行: python -m apb setup --install-deps")
            print(f"        或手动: {sys.executable} -m pip install -r {config.SCRIPTS_DIR / 'requirements.txt'}")
    else:
        print("[setup] ✓ Python 依赖齐全")
    info["python_deps"] = deps

    # uiautomator2 agent
    if init_u2 and deps.get("uiautomator2"):
        print("[setup] 推送 uiautomator2 agent (atx-agent)...")
        subprocess.run([sys.executable, "-m", "uiautomator2", "init", serial], check=False)

    # 写 env.json
    info["host"] = {"platform": sys.platform, "machine": platform.machine()}
    config.ENV_JSON.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[setup] ✓ 设备能力已写入 {config.ENV_JSON}")
    print("[setup] 完成。下一步: python -m apb capture --type startup --app <pkg> --run baseline")
    return 0


def load_env() -> dict:
    """读取 setup 写入的 env.json。后续阶段用。"""
    if not config.ENV_JSON.exists():
        raise FileNotFoundError(
            f"未找到 {config.ENV_JSON}，请先运行: python -m apb setup"
        )
    return json.loads(config.ENV_JSON.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
