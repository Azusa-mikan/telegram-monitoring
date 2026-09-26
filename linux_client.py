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
        logger.info("KWin window watcher started")

    def _load_kwin_script(self) -> None:
        """把脚本写入文件并加载进 KWin，然后启动"""
        script_dir = Path.home() / ".cache" / "telegram_monitoring"
        script_dir.mkdir(parents=True, exist_ok=True)
        self._script_file = script_dir / f"{_KWIN_SCRIPT_NAME}.js"
        self._script_file.write_text(_KWIN_SCRIPT, encoding="utf-8")

        kwin = self._bus.get_object(_KWIN_SERVICE, _KWIN_SCRIPTING_PATH)
        scripting = dbus.Interface(kwin, _KWIN_SCRIPTING_IFACE)

        # 单参重载：只传脚本路径，返回 int（脚本 id）
        self._script_id = int(scripting.loadScript(str(self._script_file)))
        # start() 启动所有已加载但未运行的脚本
        scripting.start()
        logger.debug(f"KWin script loaded, id={self._script_id}")

    def stop(self) -> None:
        """停止事件循环"""
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

    async def run() -> None:
        loop_holder["loop"] = asyncio.get_running_loop()
        await sio.connect(config.server_url, auth={"token": config.token})
        try:
            await sio.wait()
        finally:
            await sio.disconnect()

    # watcher 进程内只启动一次，不随 socketio 重连而重启
    watcher.start()
    try:
        while True:
            try:
                asyncio.run(run())
            except KeyboardInterrupt:
                log.debug("KeyboardInterrupt received, exiting...")
                break
            except Exception as e:
                log.error(f"连接服务器失败: {e}")
                time.sleep(3)
    finally:
        watcher.stop()


if __name__ == "__main__":
    main()

