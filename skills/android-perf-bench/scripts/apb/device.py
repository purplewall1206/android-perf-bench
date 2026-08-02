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
        """懒加载 uiautomator2 连接，掉线时自动重连。"""
        if self._u2 is None:
            self._u2 = self._connect_u2()
        else:
            # 健康检查：掉线（设备休眠/重启）则重连一次
            try:
                self._u2.info
            except Exception:
                self._u2 = self._connect_u2()
        return self._u2

    def _connect_u2(self):
        import uiautomator2 as u2
        conn = u2.connect(self.serial) if self.serial else u2.connect()
        conn.implicitly_wait(10.0)
        return conn

    # ── app 管理 ─────────────────────────────────────────────────
    def resolve_activity(self, pkg: str) -> Optional[str]:
        """解析 pkg 的 launcher activity（component），形如 'pkg/.MainActivity'。

        用 cmd package resolve-activity --brief，比 monkey 更可靠。
        失败返回 None（调用方应回退到 monkey 或纯包名）。
        """
        rc, out = self.shell("cmd", "package", "resolve-activity", "--brief", pkg, timeout=15)
        if rc != 0:
            return None
        # 输出最后一行是 component（pkg/activity）；排除纯包名行和提示行
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line and "/" in line and not line.startswith("-"):
                return line
        return None

    def am_start_w(self, pkg: str, activity: Optional[str] = None,
                   stop: bool = False) -> dict:
        """am start -W，解析返回 {status, launch_state, total_time, wait_time, complete}。

        activity 为 None 时自动 resolve_activity 解析 launcher activity（荣耀等 ROM
        仅传包名不可靠，必须带真实 component 才能正确拉到前台）。
        复刻 Fleet parsing_adb_am_result。
        """
        if activity is None:
            resolved = self.resolve_activity(pkg)
            if resolved and "/" in resolved:
                comp = resolved  # 已是 pkg/activity 形式
            else:
                comp = pkg  # 回退
        else:
            comp = f"{pkg}/{activity}"
        flags = ["-W"]
        if stop:
            flags.append("-S")
        rc, out = self.shell("am", "start", *flags, comp, timeout=60)
        res = {"status": "unknown", "launch_state": None,
               "total_time": None, "wait_time": None, "complete": None,
               "component": comp, "raw": out}
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

    def _app_label(self, pkg: str) -> Optional[str]:
        """查 app 的桌面显示名。先查 config 映射，再回退 PackageManager label。"""
        from . import config
        label = config.PACKAGE_DISPLAY_NAMES.get(pkg)
        if label:
            return label
        # PackageManager 查 label
        rc, out = self.shell("cmd", "package", "list", "packages", "-f", pkg, timeout=10)
        # dumpsys package 查 label 不可靠，用 pm dump 太重；这里直接返回 None 让调用方处理
        return None

    def launch_by_click(self, pkg: str, timeout: float = 8.0) -> bool:
        """点击启动：回桌面 → 进 app 抽屉 → 遍历所有屏找图标 → click。

        遍历策略：先在 app 抽屉里 scroll.to(text=label) 找；找不到则左右翻页。
        屏幕上没有该 app（未装/在文件夹内）时返回 False，调用方回退 adb。
        """
        d = self.u2()
        label = self._app_label(pkg)
        if not label:
            return False  # 无显示名，无法点击
        try:
            d.press("home"); time.sleep(0.6)
            # 上滑进 app 抽屉（荣耀/华为/小米通用）
            info = d.info
            w, h = info["displayWidth"], info["displayHeight"]
            d.swipe(w // 2, int(h * 0.9), w // 2, int(h * 0.1), 0.4); time.sleep(1.2)
            # 在抽屉的 scrollable 容器里滚动查找目标
            found = False
            try:
                d(scrollable=True).scroll.to(text=label)
                if d(text=label).exists:
                    found = True
            except Exception:
                pass
            # 若 scroll.to 没找到，左右翻页再找（部分 ROM 抽屉分页不分滚动）
            if not found:
                for _ in range(6):
                    if d(text=label).exists:
                        found = True
                        break
                    d.swipe(int(w * 0.8), h // 2, int(w * 0.2), h // 2, 0.3); time.sleep(0.5)
            if not found:
                return False
            el = d(text=label)
            if el.exists:
                el.click()
                # 等待 app 到前台
                end = time.time() + timeout
                while time.time() < end:
                    cur = self.app_current()
                    if cur.get("package") == pkg:
                        return True
                    time.sleep(0.3)
            return False
        except Exception as e:
            print(f"[device] launch_by_click({pkg}/{label}) 异常: {e}")
            return False

    def launch_app(self, pkg: str, method: str = "auto",
                   stop: bool = False, force_click: bool = False) -> dict:
        """统一启动入口，按策略选择 adb / 点击。

        method:
          - "adb"（默认推荐）：u2 app_start。进程会启动并占内存（即使被保活 app 抢前台，
            内存压力已产生——camera_reload/cache 测的是内存压力，不依赖 app 在前台）。
          - "click"：点击桌面/app抽屉图标启动（模拟真实用户点击，某些 ROM 对点击有优化）。
            先遍历 app 抽屉 scroll.to 找图标，找不到回退 adb。
          - "auto"：等价 adb（保活环境下点击不可靠，故 auto 直接用 adb）。
        返回 {method_used, on_front, pkg}。注：on_front 仅作参考，保活 app 抢前台时可能为 False
        但进程已启动、内存已占用。
        """
        result = {"pkg": pkg, "method_used": None, "on_front": False}
        d = self.u2()

        def _check_front() -> bool:
            return self.app_current().get("package") == pkg

        # 点击模式
        if method == "click" or force_click:
            if self.launch_by_click(pkg):
                result.update(method_used="click", on_front=True)
                return result
            # 点击失败回退 adb
            self.app_start(pkg, stop=stop)
            time.sleep(1.0)
            result["method_used"] = "adb(click回退)"
            result["on_front"] = _check_front()
            return result

        # adb 模式（默认/auto）
        self.app_start(pkg, stop=stop)
        time.sleep(1.0)
        result["method_used"] = "adb"
        result["on_front"] = _check_front()
        return result

    def clear_recent_apps(self, app_list: list[str] | None = None,
                          cleanup_cfg: dict | None = None) -> bool:
        """清理后台 app。优先点击"最近任务-清除全部"，不可用则回退 force-stop 批量杀。

        cleanup_cfg 来自 env['recent_cleanup']（setup 探测结果）。
        返回是否执行了清理。
        """
        cfg = cleanup_cfg or {}
        # 方式1：点击最近任务全清（探测可用时）
        if cfg.get("available"):
            try:
                d = self.u2()
                info = d.info
                w, h = info["displayWidth"], info["displayHeight"]
                method = cfg.get("method")
                if method == "keyevent_187":
                    d.shell("input keyevent 187")
                elif method == "keyevent_580":
                    d.shell("input keyevent 580")
                elif method == "swipe_gesture":
                    d.swipe(w // 2, h - 10, w // 2, int(h * 0.3), 0.6)
                time.sleep(1.5)
                # 找清除按钮点击
                import re
                clicked = False
                for t in cfg.get("clear_texts", []):
                    if d(text=t).click_exists(timeout=1.0):
                        clicked = True; break
                if not clicked:
                    clear_pat = re.compile(r"清除|清理|关闭全部|全部关闭|全部清除|close all|clear all|×|垃圾", re.I)
                    for el in d(textMatches=clear_pat):
                        try:
                            el.click(); clicked = True; break
                        except Exception:
                            continue
                time.sleep(1.0)
                d.press("home")
                if clicked:
                    return True
            except Exception as e:
                print(f"[device] 点击清理后台失败: {e}")

        # 方式2：回退 force-stop 批量杀（兜底）
        if app_list:
            print("[device] ⚠ 用 adb force-stop 批量杀后台兜底（不如系统清理彻底）")
            killed = 0
            rc, out = self.shell("dumpsys", "activity", "recents", timeout=15)
            bg_pkgs = set(app_list)
            if rc == 0:
                import re
                for m in re.finditer(r'#\d+\s+[A-Z]+\s+(\S+)/', out):
                    bg_pkgs.add(m.group(1))
            launcher_pkgs = {"com.android.systemui", "com.hihonor.android.launcher",
                             "com.huawei.android.launcher", "com.miui.home",
                             "com.coloros.launcher", "com.bbk.launcher2"}
            for pkg in bg_pkgs:
                if pkg and pkg not in launcher_pkgs:
                    self.force_stop(pkg)
                    killed += 1
            print(f"[device] force-stop 杀了约 {killed} 个后台 app")
            time.sleep(1.0)
            return True
        return False

    def app_wait(self, pkg: str, front: bool = True, timeout: float = 15.0) -> int:
        """等待 app 运行/到前台，返回 pid 或 0。"""
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

    def sample_proc_meminfo(self) -> dict:
        """采样 /proc/meminfo 的关键字段（KB）。轻量，每步可调。"""
        rc, out = self.shell("cat", "/proc/meminfo", timeout=10)
        res = {}
        if rc != 0:
            return res
        keys = ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached",
                "SwapCached", "Active", "Inactive", "SwapTotal", "SwapFree",
                "Dirty", "Writeback", "AnonPages", "Mapped", "Shmem",
                "Slab", "SReclaimable", "SUnreclaim", "CmaTotal", "CmaFree")
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].rstrip(":") in keys:
                try:
                    res[parts[0].rstrip(":").lower() + "_kb"] = int(parts[1])
                except ValueError:
                    pass
        return res

    def sample_proc_vmstat(self) -> dict:
        """采样 /proc/vmstat 的关键字段（内存回收/swap 活动）。"""
        rc, out = self.shell("cat", "/proc/vmstat", timeout=10)
        res = {}
        if rc != 0:
            return res
        # 关注 reclaim/swap/compact 相关计数器
        keys = ("pgmajfault", "pgpgin", "pgpgout", "pswpin", "pswpout",
                "pgsteal_kswapd", "pgsteal_direct", "pgscan_kswapd",
                "pgscan_direct", "pgrefill", "compact_stall",
                "oom_kill", "pgalloc_normal", "pgalloc_dma32")
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] in keys:
                try:
                    res[parts[0]] = int(parts[1])
                except ValueError:
                    pass
        return res

    def sample_dumpsys_meminfo_s(self) -> list[dict]:
        """dumpsys meminfo -S（summary 模式），返回每个进程的内存摘要。

        输出形如：
          PSS       Private    Private    Swap     Heap     Heap     Heap
          TOTAL     RSS        Dirty      PSS      ...
          123456    ...        com.example.app (pid-1234)
        每行一个进程，含 PSS/RSS 等。
        """
        rc, out = self.shell("dumpsys", "meminfo", "-S", timeout=20)
        procs = []
        if rc != 0:
            return procs
        # -S 模式："  944,344K: com.ss.android.ugc.aweme (pid 25109 / activities)"
        for line in out.splitlines():
            line = line.strip()
            # 匹配 "<PSS>K: <package> (pid ...)"
            m = re.match(r"([\d,]+)K:\s+(\S+)\s*\(pid", line)
            if m:
                try:
                    pss = int(m.group(1).replace(",", ""))
                except ValueError:
                    pss = 0
                procs.append({"package": m.group(2), "pss_kb": pss, "raw": line})
        return procs

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
