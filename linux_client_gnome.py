"""
Linux (GNOME Shell 45+ / Wayland) 前台窗口监控实现

GNOME（Wayland）不允许外部程序直接读取前台窗口：内置的
org.gnome.Shell.Introspect 与 org.gnome.Shell.Screenshot 都只对两个
portal 开放（DBusSenderChecker 白名单），org.gnome.Shell.Eval 也默认禁用。

因此本客户端依赖随项目提供的 GNOME Shell 扩展
（gnome_extension/telegram-monitoring@azusa-mikan）：扩展在 Shell 进程内
监听焦点窗口，并通过自建 D-Bus 对象对外暴露焦点变化与截图能力。

    bus name : org.gnome.Shell.Extensions.TelegramMonitoring
    path     : /org/gnome/Shell/Extensions/TelegramMonitoring
    iface    : org.gnome.Shell.Extensions.TelegramMonitoring
      Get() -> (s)                  当前窗口 JSON
      Screenshot(s path) -> (bs)    截图写入 path（PNG）
      FocusChanged(s)               焦点/标题变化时推送窗口 JSON

对应 KDE Plasma 版本见 linux_client_kde.py。
"""

import asyncio
import json
import logging
import threading
from pathlib import Path
from collections.abc import Callable

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

# 必须在创建任何 SessionBus 之前设置默认主循环，
# 否则 dbus-python 无法处理异步调用/信号/导出对象。
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

logger = logging.getLogger(__name__)

# 随项目提供的 GNOME Shell 扩展所暴露的 D-Bus 服务
_DBUS_SERVICE = "org.gnome.Shell.Extensions.TelegramMonitoring"
_DBUS_PATH = "/org/gnome/Shell/Extensions/TelegramMonitoring"
_DBUS_INTERFACE = "org.gnome.Shell.Extensions.TelegramMonitoring"

# 扩展 UUID 与安装位置（仅用于提示信息）
_EXT_UUID = "telegram-monitoring@azusa-mikan"


class GnomeWindowWatcher:
    """
    GNOME Shell 扩展驱动的前台窗口监控。

    用法::

        watcher = GnomeWindowWatcher(on_window_changed=my_callback)
        watcher.start()   # 非阻塞，内部起 GLib 主循环

    ``on_window_changed`` 会在焦点窗口（或焦点窗口标题）变化时被调用，
    传入窗口标题。回调发生在 GLib 线程中，跨线程操作 asyncio 需自行
    用 ``asyncio.run_coroutine_threadsafe`` 转交。
    """

    def __init__(self, on_window_changed: Callable[[str], None]):
        self._on_window_changed = on_window_changed
        self._loop_thread: threading.Thread | None = None
        self._glib_loop: GLib.MainLoop | None = None
        self._bus: dbus.Bus | None = None
        self._iface: dbus.Interface | None = None
        self._signal_match = None

    def start(self) -> None:
        """连接扩展的 D-Bus 服务并启动事件循环（非阻塞）"""
        if self._glib_loop is not None:
            # 已在运行，避免重复订阅
            return

        self._bus = dbus.SessionBus(mainloop=dbus.mainloop.glib.DBusGMainLoop())
        self._iface = dbus.Interface(
            self._bus.get_object(_DBUS_SERVICE, _DBUS_PATH),
            _DBUS_INTERFACE,
        )

        # 订阅焦点变化。不限定 sender，这样 gnome-shell 重启、
        # 扩展重新导出对象后信号仍然可达。
        self._signal_match = self._bus.add_signal_receiver(
            self._on_focus_changed,
            signal_name="FocusChanged",
            dbus_interface=_DBUS_INTERFACE,
            path=_DBUS_PATH,
        )

        # 起一个线程跑 GLib 主循环，负责接收扩展推送的信号
        self._glib_loop = GLib.MainLoop()
        self._loop_thread = threading.Thread(
            target=self._glib_loop.run, daemon=True, name="glib-dbus"
        )
        self._loop_thread.start()

        # 启动时主动取一次当前窗口（在 GLib 线程里执行）
        GLib.idle_add(self._report_current)

        logger.info("GNOME window watcher started")

    def _parse_title(self, payload: str) -> str:
        """扩展推送的是 JSON；解析失败时退化为把整串当标题。"""
        try:
            return str(json.loads(payload).get("title") or "")
        except Exception:
            return payload

    def _on_focus_changed(self, payload: str) -> None:
        logger.debug(f"扩展焦点变化: {payload}")
        self._on_window_changed(self._parse_title(payload))

    def _report_current(self) -> bool:
        """主动查询一次当前窗口（返回 False 以便 GLib 移除该 idle 源）"""
        if self._iface is None:
            return False
        try:
            payload = self._iface.Get()
        except Exception as e:
            logger.debug(f"查询当前窗口失败: {e}")
            return False
        logger.debug(f"当前窗口: {payload}")
        self._on_window_changed(self._parse_title(payload))
        return False

    def stop(self) -> None:
        """停止事件循环（扩展由 GNOME Shell 自行管理，无需在此卸载）"""
        if self._bus is not None and self._signal_match is not None:
            try:
                self._bus.remove_signal_receiver(self._signal_match)
            except Exception as e:
                logger.debug(f"取消信号订阅失败: {e}")
            self._signal_match = None
        if self._glib_loop is not None:
            self._glib_loop.quit()
        logger.info("GNOME window watcher stopped")


def is_supported() -> bool:
    """判断当前环境是否支持（GNOME 且随项目的扩展已加载）"""
    import os

    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "gnome" not in desktop:
        return False
    try:
        bus = dbus.SessionBus(mainloop=dbus.mainloop.glib.DBusGMainLoop())
        return bool(bus.name_has_owner(_DBUS_SERVICE))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 截图：通过随项目提供的 GNOME 扩展（在 Shell 进程内调用 Shell.Screenshot，
# 不受 GNOME 内置截图 D-Bus 的 portal 白名单限制）
# ---------------------------------------------------------------------------

def _extension_screenshot(path: str) -> tuple[bool, str]:
    """调用扩展的 Screenshot(path) 方法，把 PNG 写入 path。"""
    import subprocess

    try:
        proc = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", _DBUS_SERVICE,
                "--object-path", _DBUS_PATH,
                "--method", f"{_DBUS_INTERFACE}.Screenshot",
                path,
            ],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return False, str(e)
    if proc.returncode != 0:
        return False, proc.stderr.strip()
    out = proc.stdout.strip()
    return out.startswith("(true"), out


def take_screenshot() -> bytes:
    """
    用 GNOME 扩展截取整个桌面，返回 PNG 字节。

    若图片超过 10MB（服务端限制），依次降质/缩放为 JPEG。
    与 client.py 的 _make_screenshot_bytes 行为保持一致。
    """
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "shot.png"
        ok, detail = _extension_screenshot(str(out))
        if not ok:
            raise RuntimeError(f"GNOME 扩展截图失败: {detail}")
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
    """
    用 Mutter 的 DisplayConfig 解析当前显示模式，如 '2560x1440@144Hz'。

    替代 KDE 版的 kscreen-doctor：GetCurrentState 返回每个显示器支持的模式，
    其中带 'is-current' 属性的即为当前模式。
    """
    import dbus

    try:
        bus = dbus.SessionBus()
        obj = bus.get_object(
            "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig"
        )
        iface = dbus.Interface(obj, "org.gnome.Mutter.DisplayConfig")
        _serial, monitors, _logical, _props = iface.GetCurrentState()
    except Exception as e:
        logger.debug(f"读取 Mutter DisplayConfig 失败: {e}")
        return None

    for monitor in monitors:
        for mode in monitor[1]:
            # mode = (id, width, height, refresh, preferred_scale, scales, props)
            if mode[6].get("is-current"):
                w, h, rr = int(mode[1]), int(mode[2]), mode[3]
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
# 以下是 GNOME 客户端主程序（复用与 client.py 相同的配置文件 client_config.yaml）
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
            "当前环境不受支持：需要 GNOME，且已安装并启用随项目的 Shell 扩展 "
            f"{_EXT_UUID}。\n"
            "安装：\n"
            "  cp -r gnome_extension/* ~/.local/share/gnome-shell/extensions/\n"
            f"  gnome-extensions enable {_EXT_UUID}\n"
            "（GNOME Wayland 下新装的扩展需重新登录后才生效）"
        )
        sys.exit(1)

    # handle_sigint=False：不让 socketio/engineio 接管 SIGINT。
    # 它们收到 SIGINT 会 cancel 掉当前任务并 stop 事件循环，导致
    # asyncio.run() 抛出 CancelledError、按 Ctrl+C 时打印整段 traceback。
    # 交给下面 main() 末尾的 KeyboardInterrupt 处理即可。
    sio = socketio.AsyncClient(
        reconnection=False,
        handle_sigint=False,
        **({"logger": log, "engineio_logger": log}
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

    watcher = GnomeWindowWatcher(on_window_changed=on_window_changed)

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
        while True:
            try:
                if sio.connected:
                    await sio.disconnect()
                await sio.connect(config.server_url, auth={"token": config.token})
                await sio.wait()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"连接服务器失败: {e}")
                await asyncio.sleep(3)
            finally:
                try:
                    if sio.connected:
                        await sio.disconnect()
                except Exception:
                    pass

    # watcher 进程内只启动一次，不随 socketio 重连而重启
    watcher.start()
    try:
        # 必须只建一个事件循环：socketio.AsyncClient 的内部原语
        # 绑定在创建它的事件循环上，跨 asyncio.run() 复用会报
        # "bound to a different event loop" / "not in a disconnected state"，
        # 导致断线后再也无法重连。重连循环因此放在循环内部。
        asyncio.run(run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        # 正常退出：Ctrl+C 或事件循环被取消，不应打印 traceback
        log.info("正在退出...")
    finally:
        watcher.stop()


if __name__ == "__main__":
    main()
