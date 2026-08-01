"""设备抽象层：封装 adbutils（adb 命令）+ uiautomator2（UI 自动化）。

benchmarks 通过 Device 实例操作手机，不直接碰 adb/u2，便于隔离与测试。
所有方法对失败容错（返回 None/False 而非抛异常），workload 才能稳定。
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
import os
from typing import Optional

from . import config


def _adb_env() -> dict:
    """构造 subprocess 环境，禁用 Git Bash/MSYS 的路径转换。

    MSYS 会把 /data/local/tmp 这类以 / 开头的参数转成 Windows 路径
    (C:/Program Files/Git/data/...)，导致 adb pull/push/shell 远程路径失效。
    MSYS_NO_PATHCONV=1 全局禁用；MSYS2_ARG_CONV_EXCL='*' 作为双保险。
    """
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    env["MSYS2_ARG_CONV_EXCL"] = "*"
    return env


def _adb(*args: str, serial: Optional[str] = None, timeout: int = 60,
         capture: bool = True) -> tuple[int, str]:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    try:
        p = subprocess.run(cmd, capture_output=capture, text=True,
                           timeout=timeout, env=_adb_env())
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


class Device:
    """对一台已连接手机的抽象。"""

    def __init__(self, serial: Optional[str] = None):
        self.serial = serial
        self._u2 = None  # 懒加载

    # ── adb 原始 ─────────────────────────────────────────────────
    def shell(self, *args: str, timeout: int = 60) -> tuple[int, str]:
        return _adb("shell", *args, serial=self.serial, timeout=timeout)

    def shell_split(self, cmd: str, timeout: int = 60) -> tuple[int, str]:
        """整条 shell 命令字符串（含管道/重定向）。"""
        return _adb("shell", cmd, serial=self.serial, timeout=timeout)

    def pull(self, remote: str, local: str, timeout: int = 300) -> int:
        return _adb("pull", remote, local, serial=self.serial, timeout=timeout)[0]

    def push(self, local: str, remote: str, timeout: int = 120) -> int:
        return _adb("push", local, remote, serial=self.serial, timeout=timeout)[0]

    def getprop(self, prop: str) -> str:
        rc, out = self.shell("getprop", prop)
        return out.strip() if rc == 0 else ""

    # ── uiautomator2 ─────────────────────────────────────────────
    def u2(self):
        """懒加载 uiautomator2 连接。"""
        if self._u2 is None:
            import uiautomator2 as u2
            self._u2 = u2.connect(self.serial) if self.serial else u2.connect()
            self._u2.implicitly_wait(10.0)
        return self._u2

    # ── app 管理 ─────────────────────────────────────────────────
    def am_start_w(self, pkg: str, activity: Optional[str] = None,
                   stop: bool = False) -> dict:
        """am start -W，解析返回 {status, launch_state, total_time, wait_time, complete}。

        复刻 Fleet parsing_adb_am_result。老版 Android 可能无 LaunchState 行。
        """
        comp = f"{pkg}/{activity}" if activity else pkg
        flags = ["-W"]
        if stop:
            flags.append("-S")
        rc, out = self.shell("am", "start", *flags, comp, timeout=60)
        res = {"status": "unknown", "launch_state": None,
               "total_time": None, "wait_time": None, "complete": None,
               "raw": out}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Status:"):
                res["status"] = line.split(":", 1)[1].strip()
            elif line.startswith("LaunchState:"):
                res["launch_state"] = line.split(":", 1)[1].strip()
            elif line.startswith("ThisTime:"):
                try: res["total_time"] = int(line.split(":", 1)[1].strip())
                except ValueError: pass
            elif line.startswith("TotalTime:"):
                try: res["total_time"] = int(line.split(":", 1)[1].strip())
                except ValueError: pass
            elif line.startswith("WaitTime:"):
                try: res["wait_time"] = int(line.split(":", 1)[1].strip())
                except ValueError: pass
            elif line.startswith("Complete:"):
                res["complete"] = line.split(":", 1)[1].strip()
        return res

    def app_start(self, pkg: str, stop: bool = False) -> bool:
        try:
            self.u2().app_start(pkg, stop=stop)
            return True
        except Exception as e:
            print(f"[device] app_start({pkg}) 失败: {e}")
            return False

    def app_wait(self, pkg: str, front: bool = True, timeout: float = 15.0) -> int:
        try:
            return self.u2().app_wait(pkg, front=front, timeout=timeout)
        except Exception:
            return 0

    def app_current(self) -> dict:
        """返回当前前台 app {package, activity, pid}。"""
        try:
            return self.u2().app_current()
        except Exception:
            return {}

    def force_stop(self, pkg: str) -> None:
        self.shell("am", "force-stop", pkg)

    def pm_clear(self, pkg: str) -> bool:
        rc, _ = self.shell("pm", "clear", pkg, timeout=30)
        return rc == 0

    def kill_all(self, app_list: list[str]) -> None:
        for pkg in app_list:
            self.force_stop(pkg)

    # ── 交互 ─────────────────────────────────────────────────────
    def press(self, key: str) -> None:
        try:
            self.u2().press(key)
        except Exception:
            self.shell("input", "keyevent", f"KEYCODE_{key.upper()}")

    def home(self) -> None:
        self.press("home")

    def swipe_up(self, scale: float = 0.8, duration: float = 0.4) -> None:
        """向上滑动（模拟列表向上滚）。复刻 Fleet action-touchscreen-swipe-up。"""
        try:
            d = self.u2()
            info = d.info
            w, h = info["displayWidth"], info["displayHeight"]
            d.swipe(w // 2, int(h * 0.8), w // 2, int(h * (0.8 - scale)), duration)
        except Exception as e:
            print(f"[device] swipe_up 失败: {e}")

    def swipe_ext(self, direction: str, scale: float = 0.8) -> None:
        try:
            self.u2().swipe_ext(direction, scale=scale)
        except Exception as e:
            # 回退到 swipe_up
            self.swipe_up(scale)

    def unlock(self) -> None:
        try:
            self.u2().screen_on()
            time.sleep(0.3)
            self.u2().unlock()
        except Exception:
            pass

    def dismiss_popups(self, timeout: float = 3.0) -> None:
        """尝试关闭常见弹窗（同意/跳过/允许/取消/我知道了）。best-effort。"""
        try:
            d = self.u2()
            for text in ["同意", "允许", "始终允许", "跳过", "我知道了", "确定",
                         "AGREE", "ALLOW", "SKIP", "OK", "GOT IT", "Accept", "Continue",
                         "知道了", "关闭", "暂不", "以后再说"]:
                if d(text=text).click_exists(timeout=0.3):
                    time.sleep(0.3)
        except Exception:
            pass

    # ── 系统 / 内存 ──────────────────────────────────────────────
    def dumpsys_meminfo(self, pkg: Optional[str] = None) -> str:
        args = ["dumpsys", "meminfo"]
        if pkg:
            args.append(pkg)
        rc, out = self.shell(*args, timeout=30)
        return out if rc == 0 else ""

    def cached_apps(self, app_list: list[str]) -> set[str]:
        """从 dumpsys meminfo 扫描仍存在的 app。复刻 Fleet check_cached_apps。"""
        out = self.dumpsys_meminfo()
        found = set()
        for line in out.splitlines():
            for tok in line.split():
                if tok in app_list:
                    found.add(tok)
        return found

    def parse_meminfo_total(self) -> dict:
        """解析 dumpsys meminfo 顶层的 Total/Free/Used RAM。"""
        out = self.dumpsys_meminfo()
        res = {}
        for line in out.splitlines():
            m = re.match(r"\s*(Total|Free|Used)\s*RAM:\s*([\d,]+)\s*K?B?", line)
            if m:
                try:
                    res[m.group(1).lower() + "_ram_kb"] = int(m.group(2).replace(",", ""))
                except ValueError:
                    pass
        return res

    def parse_meminfo_app(self, pkg: str) -> dict:
        """解析单个 app 的 RSS / Java Heap / Native Heap。best-effort。"""
        out = self.dumpsys_meminfo(pkg)
        res = {}
        for line in out.splitlines():
            m = re.match(r"\s*(RSS|TOTAL)\s*:?\s*([\d,]+)", line)
            if m and "rss" not in res:
                try: res["rss_kb"] = int(m.group(2).replace(",", ""))
                except ValueError: pass
            m2 = re.match(r"\s*Java Heap:\s*([\d,]+)", line)
            if m2:
                try: res["java_heap_kb"] = int(m2.group(1).replace(",", ""))
                except ValueError: pass
            m3 = re.match(r"\s*Native Heap:\s*([\d,]+)", line)
            if m3:
                try: res["native_heap_kb"] = int(m3.group(1).replace(",", ""))
                except ValueError: pass
        return res

    # ── perfetto / atrace 控制 ───────────────────────────────────
    def perfetto_start(self, config_remote: str, out_remote: str,
                       txt: bool = True, timeout: int = 10) -> int:
        flags = ["-c", config_remote]
        if txt:
            flags.append("--txt")
        flags += ["-o", out_remote, "--background"]
        rc, out = self.shell("perfetto", *flags, timeout=timeout)
        if rc != 0:
            print(f"[device] perfetto 启动失败 (rc={rc}): {out.strip()[:200]}")
        return rc

    def perfetto_wait(self, out_remote: str, timeout: int = 300) -> bool:
        """等 perfetto 进程结束（--background 模式下录制完成后进程自动退出）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            rc, out = self.shell("pidof", "perfetto", timeout=10)
            if rc != 0 or not out.strip():  # pidof 无输出=进程不存在
                return True
            time.sleep(1)
        return False

    def atrace_start(self, pkg: str, buf_kb: int = 32768,
                     categories: str = "gfx view am wm ss sched input") -> int:
        cmd = f"atrace --async_start -b {buf_kb} -a {pkg} {categories}"
        rc, out = self.shell_split(cmd, timeout=15)
        if rc != 0:
            print(f"[device] atrace 启动失败 (rc={rc}): {out.strip()[:200]}")
        return rc

    def atrace_stop(self, remote_path: str = "/data/local/tmp/apb_trace.ftrace",
                    timeout: int = 60) -> bool:
        rc, out = self.shell_split(f"atrace --async_stop -o {remote_path}", timeout=timeout)
        return rc == 0

    def file_exists(self, remote: str) -> bool:
        rc, _ = self.shell("test", "-f", remote, timeout=10)
        return rc == 0

    def rm(self, remote: str) -> None:
        self.shell("rm", "-f", remote, timeout=10)
