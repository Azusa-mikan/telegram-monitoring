"""
Linux (KDE Plasma 6 Wayland/X11) 前台窗口监控实现

通过 KWin 的脚本接口 (org.kde.KWin /Scripting) 动态加载一段常驻 JS 脚本，
脚本监听 workspace.windowActivated 信号，窗口切换时通过 callDBus 回调
到本进程注册的 D-Bus 服务，实现事件驱动的前台窗口获取。

原理与 kdotool 类似，但这里是常驻脚本 + 事件回调，而非每次调用都加载。
KWin 脚本 API: https://develop.kde.org/docs/plasma/kwin/api/
"""

import asyncio
import logging
import threading
from pathlib import Path
from collections.abc import Callable

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# 必须在创建任何 SessionBus 之前设置默认主循环，
# 否则 dbus-python 无法处理异步调用/信号/导出对象。
# 见 https://dbus.freedesktop.org/doc/dbus-python/
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

logger = logging.getLogger(__name__)

# 我们注册的 D-Bus 服务信息
_DBUS_SERVICE = "org.kde.telegrammonitoring.Client"
_DBUS_PATH = "/Window"
_DBUS_INTERFACE = "org.kde.telegrammonitoring.Window"

# KWin 脚本接口（注意：接口名是 org.kde.kwin，小写 kwin，D-Bus 接口名大小写敏感）
_KWIN_SERVICE = "org.kde.KWin"
_KWIN_SCRIPTING_PATH = "/Scripting"
_KWIN_SCRIPTING_IFACE = "org.kde.kwin.Scripting"
_KWIN_SCRIPT_NAME = "telegram_monitoring_window_watcher"

# 看门狗检查间隔（秒）
_WATCHDOG_INTERVAL = 30

# 常驻 KWin 脚本内容：监听窗口激活，回调到我们的 D-Bus 服务
_KWIN_SCRIPT = """
function _tm_emit(client) {
    if (!client) {
        return;
    }
    // 直接取 caption（即窗口原标题）
    var title = client.caption;
    if (!title) {
        return;
    }
    callDBus(
        "%(service)s",
        "%(path)s",
        "%(iface)s",
        "window_changed",
        title
    );
}

workspace.windowActivated.connect(_tm_emit);

// 脚本加载时，若已有活动窗口，先上报一次
if (workspace.activeWindow) {
    _tm_emit(workspace.activeWindow);
}
""" % {
    "service": _DBUS_SERVICE,
    "path": _DBUS_PATH,
    "iface": _DBUS_INTERFACE,
}


class _WindowService(dbus.service.Object):
    """接收 KWin 脚本推送的窗口变化"""

    def __init__(self, bus: dbus.Bus, on_window_changed: Callable[[str], None]):
        super().__init__(bus, _DBUS_PATH)
        self._on_window_changed = on_window_changed

    @dbus.service.method(_DBUS_INTERFACE, in_signature="s", out_signature="")
    def window_changed(self, title: str) -> None:
        logger.debug(f"KWin window_changed: {title}")
        self._on_window_changed(title)


class KWinWindowWatcher:
    """
    KDE Plasma 下的事件驱动前台窗口监控。

    用法::

        watcher = KWinWindowWatcher(on_window_changed=my_callback)
        watcher.start()   # 非阻塞，内部起 GLib 主循环

    ``on_window_changed`` 会在窗口切换时被调用，传入窗口标题。
    因为回调发生在 GLib 线程中，跨线程操作 asyncio 需自行
    用 ``asyncio.run_coroutine_threadsafe`` 转交。
    """

    def __init__(self, on_window_changed: Callable[[str], None]):
        self._on_window_changed = on_window_changed
        self._script_id: int | None = None
        self._loop_thread: threading.Thread | None = None
        self._glib_loop: GLib.MainLoop | None = None
        self._script_file: Path | None = None
        self._bus: dbus.Bus | None = None
        self._service: _WindowService | None = None
        self._bus_name: dbus.service.BusName | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """注册 D-Bus 服务、加载 KWin 脚本并启动事件循环（非阻塞）"""
        if self._glib_loop is not None:
            # 已在运行，避免重连时重复加载脚本
            return
        # 用 GLib 主循环托管 D-Bus 连接（默认主循环已在模块加载时设置）
        self._bus = dbus.SessionBus(mainloop=dbus.mainloop.glib.DBusGMainLoop())
        self._service = _WindowService(self._bus, self._on_window_changed)
        # 必须保留 BusName 引用，否则对象被回收后服务名会注销
        self._bus_name = dbus.service.BusName(_DBUS_SERVICE, self._bus, do_not_queue=True)

        # 起一个线程跑 GLib 主循环，负责接收 KWin 的回调
        self._glib_loop = GLib.MainLoop()
        self._loop_thread = threading.Thread(
            target=self._glib_loop.run, daemon=True, name="glib-dbus"
        )
        self._loop_thread.start()

        # 加载 KWin 脚本
        self._load_kwin_script()

        # 启动看门狗，定期检查脚本存活
        self._watchdog_thread = threading.Thread(
            target=self._watchdog, daemon=True, name="kwin-watchdog"
        )
        self._watchdog_thread.start()

        logger.info("KWin window watcher started")

    def _load_kwin_script(self) -> None:
        """把脚本写入文件并加载进 KWin，然后启动"""
        script_dir = Path.home() / ".cache" / "telegram_monitoring"
        script_dir.mkdir(parents=True, exist_ok=True)
        self._script_file = script_dir / f"{_KWIN_SCRIPT_NAME}.js"
        self._script_file.write_text(_KWIN_SCRIPT, encoding="utf-8")

        # loadScript 有两个重载（单参 s 和双参 ss），dbus-python 的透明代理
        # 只按反射到的单个签名校验，因此用 call_blocking 显式声明 ss。
        # 带上 pluginName 后，才能用 isScriptLoaded/unloadScript 按名字管理。
        self._script_id = int(self._bus.call_blocking(
            _KWIN_SERVICE,
            _KWIN_SCRIPTING_PATH,
            _KWIN_SCRIPTING_IFACE,
            "loadScript",
            "ss",
            (str(self._script_file), _KWIN_SCRIPT_NAME),
        ))
        self._bus.call_blocking(
            _KWIN_SERVICE,
            _KWIN_SCRIPTING_PATH,
            _KWIN_SCRIPTING_IFACE,
            "start",
            "",
            (),
        )
        logger.debug(f"KWin script loaded, id={self._script_id}")

    def _is_script_loaded(self) -> bool:
        """检查 KWin 脚本是否仍在运行（KWin 重启后会变为 False）"""
        try:
            scripting = dbus.Interface(
                self._bus.get_object(_KWIN_SERVICE, _KWIN_SCRIPTING_PATH),
                _KWIN_SCRIPTING_IFACE,
            )
            return bool(scripting.isScriptLoaded(_KWIN_SCRIPT_NAME))
        except Exception as e:
            logger.debug(f"检查 KWin 脚本状态失败: {e}")
            return False

    def _watchdog(self) -> None:
        """
        看门狗线程：定期检查脚本存活，失效则重载。
        覆盖 KWin 崩溃/重启导致动态脚本丢失的情况。
        """
        import time as _time

        while not self._stop_event.is_set():
            _time.sleep(_WATCHDOG_INTERVAL)
            if self._stop_event.is_set():
                break
            if not self._is_script_loaded():
                logger.warning("KWin 脚本已失效，尝试重新加载...")
                try:
                    self._load_kwin_script()
                    logger.info("KWin 脚本已重新加载")
                except Exception as e:
                    logger.error(f"重新加载 KWin 脚本失败: {e}")

    def stop(self) -> None:
        """停止看门狗与事件循环，并卸载 KWin 脚本"""
        self._stop_event.set()
        try:
            self._bus.call_blocking(
                _KWIN_SERVICE,
                _KWIN_SCRIPTING_PATH,
                _KWIN_SCRIPTING_IFACE,
                "unloadScript",
                "s",
                (_KWIN_SCRIPT_NAME,),
            )
        except Exception as e:
            logger.debug(f"卸载 KWin 脚本失败: {e}")
        if self._glib_loop is not None:
            self._glib_loop.quit()
        logger.info("KWin window watcher stopped")


def is_supported() -> bool:
    """判断当前环境是否支持（KDE Plasma 且 KWin D-Bus 可用）"""
    import os

    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" not in desktop and "plasma" not in desktop:
        return False
    try:
        bus = dbus.SessionBus(mainloop=dbus.mainloop.glib.DBusGMainLoop())
        bus.get_object(_KWIN_SERVICE, _KWIN_SCRIPTING_PATH)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 截图：通过 Spectacle CLI（KDE 自带，Wayland 下无需授权框）
# ---------------------------------------------------------------------------

def take_screenshot() -> bytes:
    """
    用 spectacle 后台模式截取整个桌面，返回 PNG 字节。

    若图片超过 10MB（服务端限制），依次降质/缩放为 JPEG。
    与 client.py 的 _make_screenshot_bytes 行为保持一致。
    """
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "shot.png"
        subprocess.run(
            ["spectacle", "-b", "-n", "-f", "-o", str(out)],
            check=True,
            timeout=15,
            capture_output=True,
        )
        data = out.read_bytes()

    max_size = 10 * 1024 * 1024
    if len(data) <= max_size:
        return data

    # 超过 10MB，转为 JPEG 并逐步降质/缩放
    from io import BytesIO
    from PIL import Image

    logger.warning(f"图片过大 {len(data) / (1024 * 1024):.2f} MB，使用 JPEG")
    img = Image.open(BytesIO(data)).convert("RGB")
    quality = 95
    while True:
        b = BytesIO()
        img.save(b, format="JPEG", quality=quality, optimize=True, progressive=True)
        data = b.getvalue()
        if len(data) <= max_size or quality <= 70:
            break
        quality -= 5
    w, h = img.size
    while len(data) > max_size and min(w, h) > 480:
        w, h = int(w * 0.85), int(h * 0.85)
        resized = img.resize((w, h), resample=Image.Resampling.LANCZOS)
        b = BytesIO()
        resized.save(b, format="JPEG", quality=max(50, quality), optimize=True, progressive=True)
        data = b.getvalue()
    return data


# ---------------------------------------------------------------------------
# 通知：通过 notify-send
# 参数语义见 /home/aaccgg/文档/docs/notify-send.md
# ---------------------------------------------------------------------------

def _notify_send(title: str, body: str, action: str | None = None, timeout_s: float = 12.0) -> tuple[bool, str]:
    """
    发送桌面通知。

    action 非空时附加该 `-A` 动作并隐含 --wait，等待用户操作：
      - 用户点击动作：stdout 输出动作名，返回 (True, 动作名)
      - 通知被关闭 / 超时：返回 (False, "")
    成功与否以退出码判定（不解析受 locale 影响的 stderr）。

    参数语义见 /home/aaccgg/文档/docs/notify-send.md
    """
    import subprocess

    cmd = ["notify-send", "-a", "Telegram Monitoring", title, body]
    if action is not None:
        # 通知在等待窗口结束时自动消失，避免一直挂在屏幕上
        cmd += ["-A", action, "-t", str(int(timeout_s * 1000))]

    try:
        # 极端情况下 notify-send 会阻塞等待激活，必须设超时
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # 未操作，视为未同意
        logger.debug("notify-send 超时（用户未操作）")
        return False, ""
    if proc.returncode != 0:
        logger.error(f"notify-send 失败: {proc.returncode} {proc.stderr.strip()}")
        return False, ""
    result = proc.stdout.strip()
    # 需要动作时，stdout 为空表示用户未点击（通知过期或关闭）
    if action is not None and not result:
        return False, ""
    return True, result


# ---------------------------------------------------------------------------
# 硬件信息：psutil 为主，GPU 尽力探测
# ---------------------------------------------------------------------------

def _get_cpu_name() -> str:
    import platform
    # 优先从 /proc/cpuinfo 的 model name 取（比 platform.processor 准确）
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    if name:
                        return name
    except Exception:
        pass
    return platform.processor() or "Unknown CPU"


def _pci_id_to_name(vendor_id: str, device_id: str) -> str | None:
    """用 /usr/share/hwdata/pci.ids 查设备名（返回该 vendor 段下的 device 名）"""
    from pathlib import Path

    for p in (Path("/usr/share/hwdata/pci.ids"), Path("/usr/share/misc/pci.ids")):
        if not p.exists():
            continue
        try:
            in_vendor = False
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip() or line.startswith("#"):
                        continue
                    if not line.startswith("\t"):
                        # 顶格的 vendor 行
                        in_vendor = line[:4].lower() == vendor_id.lower()
                        continue
                    if in_vendor and line.startswith("\t") and not line.startswith("\t\t"):
                        tok = line.strip().split(None, 1)
                        if tok and tok[0].lower() == device_id.lower():
                            return tok[1] if len(tok) > 1 else None
        except Exception:
            continue
    return None


def _get_video_mode() -> str | None:
    """用 kscreen-doctor 解析当前显示模式，如 '2560x1440@144Hz'"""
    import subprocess
    import re

    try:
        out = subprocess.run(
            ["kscreen-doctor", "-o"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return None

    # 当前模式带 "*" 后缀，如 "3:2560x1440@144.00*"
    for line in out.splitlines():
        if "Modes:" in line or "*" in line:
            m = re.search(r"(\d+)x(\d+)@([\d.]+)\*", line)
            if m:
                w, h, rr = m.group(1), m.group(2), m.group(3)
                return f"{w}x{h}@{round(float(rr))}Hz"
    return None


def _get_gpu_info() -> list[dict[str, str | int | None]]:
    """探测 GPU 名称、显存与显示模式"""
    import subprocess
    import re
    from pathlib import Path

    video_mode = _get_video_mode()
    res: list[dict[str, str | int | None]] = []

    # 用 lspci -nn 拿到 [vendor:device]，再查 pci.ids 得到设备名
    try:
        out = subprocess.run(
            ["lspci", "-nn"], capture_output=True, text=True, timeout=5
        ).stdout
        for line in out.splitlines():
            if not re.search(
                r"\b(VGA compatible controller|3D controller|Display controller)\b", line
            ):
                continue
            m = re.search(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]", line)
            name = None
            if m:
                name = _pci_id_to_name(m.group(1), m.group(2))
            if not name:
                # 退回 lspci 原文（去掉 "(rev xx)" 噪声）
                name = re.sub(r"\s*\(rev [0-9a-fA-F]+\)\s*$", "", line.split(":", 2)[-1].strip())

            memory_mb: int | None = None
            # 用驱动暴露的 vram 总量
            slot = line.split()[0]  # 如 63:00.0
            for card in Path("/sys/class/drm").glob("card[0-9]*"):
                try:
                    slot_file = card / "device" / "uevent"
                    if not slot_file.exists():
                        continue
                    uevent = slot_file.read_text()
                    if f"PCI_SLOT_NAME=0000:{slot}" not in uevent:
                        continue
                    vram = card / "device" / "mem_info_vram_total"
                    if vram.exists():
                        memory_mb = int(vram.read_text().strip()) // (1024 * 1024)
                    break
                except Exception:
                    continue

            res.append({"name": name, "memory_mb": memory_mb, "video_mode": video_mode})
    except Exception:
        pass

    # lspci 不可用则退回 /sys/class/drm 的驱动名
    if not res:
        try:
            for card in Path("/sys/class/drm").glob("card[0-9]*"):
                driver = card / "device" / "driver"
                if driver.exists():
                    res.append({
                        "name": driver.resolve().name,
                        "memory_mb": None,
                        "video_mode": video_mode,
                    })
        except Exception:
            pass

    return res


def get_hard_info() -> dict:
    """采集硬件信息，字段与 client.py 的 get_hard_info 一致"""
    import time
    import psutil

    cpu_name = _get_cpu_name()
    freq = psutil.cpu_freq()
    cpu_speed = f"{((freq.max or freq.current) / 1000):.2f} GHz" if freq else "Unknown"
    cpu_cores = psutil.cpu_count(logical=False) or 0
    cpu_threads = psutil.cpu_count(logical=True) or 0
    cpu_usage = psutil.cpu_percent()

    sysmem = psutil.virtual_memory()
    total_mb = int(sysmem.total / (1024 * 1024))
    available_mb = int(sysmem.available / (1024 * 1024))

    battery = psutil.sensors_battery()
    if battery is None:
        logger.warning("未找到电池")
        battery_percent, is_charging = 0, False
    else:
        battery_percent, is_charging = int(battery.percent), bool(battery.power_plugged)

    uptime = int(time.time() - psutil.boot_time())

    return {
        "cpu_info": {
            "name": cpu_name,
            "base_speed": cpu_speed,
            "cores": cpu_cores,
            "threads": cpu_threads,
            "usage": cpu_usage,
        },
        "memory": {
            "total_mb": total_mb,
            "available_mb": available_mb,
        },
        "battery": {
            "percent": battery_percent,
            "is_charging": is_charging,
        },
        "uptime": uptime,
        "gpu_info": _get_gpu_info(),
    }


# ---------------------------------------------------------------------------
# 以下是 Linux 客户端主程序（复用与 client.py 相同的配置文件 client_config.yaml）
# ---------------------------------------------------------------------------

def main() -> None:
    import sys
    import time
    import yaml
    import socketio
    import colorlog
    from pathlib import Path
    from pydantic import BaseModel, ValidationError

    # ---- 日志 ----
    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        fmt="%(asctime)s - %(name)s - %(log_color)s%(levelname)s%(reset)s - %(message)s",
        datefmt="%H:%M:%S",
        log_colors={
            "DEBUG": "cyan", "INFO": "green", "WARNING": "yellow",
            "ERROR": "red", "CRITICAL": "red,bg_white",
        },
    ))

    # ---- 配置（与 client.py 同格式）----
    config_path = Path("client_config.yaml")
    default_config: dict = {
        "lang": "zh-CN",
        "log_level": "INFO",
        "server_url": "http://localhost:5000",
        "chat_mode": False,
        "token": "",
        "pass_window": ["任务切换", "新通知"],
    }
    log_level_dict = {
        "DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING,
        "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL,
    }

    class Config(BaseModel):
        lang: str
        log_level: str
        server_url: str
        chat_mode: bool
        token: str
        pass_window: list[str]

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = Config.model_validate(yaml.safe_load(f), extra="forbid")
    except FileNotFoundError:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(default_config, f, indent=2, default_flow_style=False, allow_unicode=True)
        logging.basicConfig(handlers=[handler], level=logging.INFO)
        logging.info("config file not found, created default config")
        sys.exit(0)
    except ValidationError as e:
        logging.basicConfig(handlers=[handler], level=logging.INFO)
        logging.critical(f"config validation error: {e}\nuse default config")
        config = Config.model_validate(default_config, extra="forbid")

    logging.basicConfig(handlers=[handler], level=log_level_dict[config.log_level])
    log = logging.getLogger("linux_client")

    if not is_supported():
        log.critical(
            "当前环境不受支持：需要 KDE Plasma（KWin 的 D-Bus 脚本接口可用）。"
            "其他桌面环境（GNOME/Hyprland 等）暂不支持。"
        )
        sys.exit(1)

    sio = socketio.AsyncClient(
        reconnection=False,
        **({"logger": log, "engineio_logger": log, "handle_sigint": False}
           if config.log_level == "DEBUG" else {}),
    )

    # 窗口事件发生在 GLib 线程，需要转交回 asyncio 事件循环
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    def on_window_changed(title: str) -> None:
        if not title or title in config.pass_window:
            return
        switch_window_time = int(time.time())
        log.debug(f"前台窗口: {title} {switch_window_time}")
        loop = loop_holder.get("loop")
        if loop is None or not sio.connected:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                sio.emit("window_change", (title, switch_window_time)), loop
            )
        except RuntimeError:
            # 重连期间旧事件循环可能已关闭，忽略
            pass

    watcher = KWinWindowWatcher(on_window_changed=on_window_changed)

    @sio.event
    async def connect() -> bool:
        log.info("已连接到服务器")
        return True

    @sio.event
    async def disconnect(reason: str) -> bool:
        log.info(f"已断开来自服务器的连接: {reason}")
        return False

    @sio.event
    async def screenshot() -> bytes:
        """服务端请求截图，返回 PNG/JPEG 字节"""
        data = await asyncio.to_thread(take_screenshot)
        log.debug(f"已创建图片大小: {len(data) / (1024 * 1024):.2f} MB")
        return data

    @sio.event
    async def client_toast(data: dict) -> None:
        """服务端请求弹通知（仅提示）"""
        title = data.get("title", "")
        body = data.get("body", "")
        log.debug(f"服务器通知: {data}")
        await asyncio.to_thread(_notify_send, title, body, None)

    @sio.on("get_hard_info")
    async def _handle_get_hard_info() -> dict:
        """服务端请求硬件信息"""
        return await asyncio.to_thread(get_hard_info)

    @sio.event
    async def client_toast_on_click(data: dict) -> bytes:
        """
        服务端请求截图，需客户端当前使用者点击"允许"后才执行。

        安全：非允许列表用户发起截图时，必须经客户端使用者同意。
        这里弹出一条带"允许"动作的通知并等待点击，只有用户点击
        "允许"才截图；未点击（关闭通知 / 超时）返回空字节，不截图。
        """
        title = data.get("title", "")
        body = data.get("body", "")
        log.debug(f"服务器截图请求（等待用户允许）: {data}")

        # 服务端 client_screenshot_on_click 的超时是 10 秒，
        # 这里给用户约 8 秒的点击窗口，避免服务端先超时。
        # -A 的 NAME 设为 "allow"，用户点击后 stdout 返回 "allow"。
        clicked, action = await asyncio.to_thread(
            _notify_send, title, body, "allow=允许", 8.0
        )
        if not clicked or action != "allow":
            log.info("用户未允许截图，已取消")
            return b""

        data_bytes = await asyncio.to_thread(take_screenshot)
        log.debug(f"已创建图片大小: {len(data_bytes) / (1024 * 1024):.2f} MB")
        return data_bytes

    @sio.event
    async def get_user_msg(name: str, msg: str) -> None:
        """服务端转发用户消息"""
        if config.chat_mode:
            import sys as _sys
            _sys.stdout.write("\r" + " " * 100 + "\r")
            print(f"[{name}]: {msg}")
            _sys.stdout.write("> ")
            _sys.stdout.flush()
        else:
            log.debug(f"收到来自 {name} 的消息: {msg}")

    async def run() -> None:
        loop_holder["loop"] = asyncio.get_running_loop()
        await sio.connect(config.server_url, auth={"token": config.token})
        try:
            await sio.wait()
        finally:
            try:
                await sio.disconnect()
            except Exception:
                pass

    # watcher 进程内只启动一次，不随 socketio 重连而重启
    watcher.start()
    try:
        while True:
            try:
                asyncio.run(run())
            except KeyboardInterrupt:
                log.debug("收到 KeyboardInterrupt，退出...")
                break
            except asyncio.CancelledError:
                log.debug("任务被取消，退出...")
                break
            except Exception as e:
                log.error(f"连接服务器失败: {e}")
                time.sleep(3)
    except KeyboardInterrupt:
        log.debug("收到 KeyboardInterrupt，退出...")
    finally:
        watcher.stop()


if __name__ == "__main__":
    main()

