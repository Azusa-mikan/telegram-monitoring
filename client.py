import sys
import socketio
import asyncio
import threading
import win32con
import win32com.client
import win32gui
import winreg
from winrt.windows.ui.notifications import ToastActivatedEventArgs
from winrt.windows.foundation import IPropertyValue
from ctypes import CFUNCTYPE, c_void_p, c_int
import pythoncom
from ctypes import windll
import yaml
from pydantic import BaseModel, ValidationError
import mss
from io import BytesIO
from win11toast import toast_async as toast
import win11toast as win11toast_module
import psutil
import platform
from PIL import Image
import time
import colorlog
import logging

i18n_zh = {
    "window_change": "前台窗口: {}",
    "invalid_gpu": "无效的GPU信息: {}",
    "battery_not_found": "未找到电池",
    "connect_server": "已连接到服务器",
    "server_toast": "服务器通知: {}",
    "server_toast_reply": "服务器通知（需要回复）: {}",
    "client_toast_reply": "客户端回复: {}",
    "image_too_big": (
        "图片过大 {} MB,\n"
        "使用 JPEG"
        ),
    "created_image": "已创建图片大小: {} MB",
    "cpu_name": "CPU名称: {}",
    "cpu_speed": "CPU频率: {}",
    "cpu_cores": "CPU核心数: {}",
    "cpu_threads": "CPU线程数: {}",
    "cpu_usage": "CPU占用率: {}",
    "total_memory": "总内存: {} MB",
    "available_memory": "可用内存: {} MB",
    "uptime": "系统运行时间: {}",
    "gpu_info": "GPU信息: {}",
    "battery_percent": "电池电量: {}",
    "is_charging": "是否充电: {}",
    "no_users": "暂无用户，无法发送消息",
    "list_users": "输入 .list 选择用户",
    "select_user": "请输入用户ID (第一列): ",
    "list_no_user": "此用户未在用户列表中",
    "chat_in_user": "正在与 {} 对话",
    "failed_select_user": "请输入正确的用户ID",
    "send_failed": "消息发送失败, 服务器返回错误: {}",
    "baned_user": "用户 {} 封禁了你的机器人, 你无法再与该用户对话",
    "enter_continue": "按回车键继续...",
    "disconnect_server": "已断开来自服务器的连接: {}",
    "connect_error_view": (
        "连接服务器失败: {}\n"
        "请查看服务器日志"
        ),
    "connect_error": "连接服务器失败: {}",
    "not_supported": "不支持非Windows系统"
}

i18n_en = {
    "window_change": "Foreground window: {}",
    "invalid_gpu": "Invalid GPU info: {}",
    "battery_not_found": "Battery not found",
    "connect_server": "Connected to server",
    "server_toast": "Server toast: {}",
    "server_toast_reply": "Server toast (need reply): {}",
    "client_toast_reply": "Client reply: {}",
    "image_too_big": (
        "Image size {} MB is too big,\n"
        "use JPEG"
        ),
    "created_image": "Created image size: {} MB",
    "cpu_name": "CPU name: {}",
    "cpu_speed": "CPU speed: {}",
    "cpu_cores": "CPU cores: {}",
    "cpu_threads": "CPU threads: {}",
    "cpu_usage": "CPU usage: {}",
    "total_memory": "Total memory: {} MB",
    "available_memory": "Available memory: {} MB",
    "uptime": "Uptime: {}",
    "gpu_info": "GPU info: {}",
    "battery_percent": "Battery percent: {}",
    "is_charging": "Is charging: {}",
    "no_users": "No users, can't send message",
    "list_users": "Input .list to select user",
    "select_user": "Please input user ID (first column): ",
    "list_no_user": "This user is not in user list",
    "chat_in_user": "Chatting with {} ",
    "failed_select_user": "Please input correct user ID",
    "send_failed": "Send message failed, server return error: {}",
    "baned_user": "User {} banned your bot, can't chat with them",
    "enter_continue": "Press Enter to continue...",
    "disconnect_server": "Disconnected from server: {}",
    "connect_error_view": (
        "Connect server failed: {}\n"
        "Please check server log"
        ),
    "connect_error": "Connect server failed: {}",
    "not_supported": "Not supported on non-Windows system"
}

class I18n(BaseModel):
    window_change: str
    invalid_gpu: str
    battery_not_found: str
    connect_server: str
    server_toast: str
    server_toast_reply: str
    client_toast_reply: str
    image_too_big: str
    created_image: str
    cpu_name: str
    cpu_speed: str
    cpu_cores: str
    cpu_threads: str
    cpu_usage: str
    total_memory: str
    available_memory: str
    uptime: str
    gpu_info: str
    battery_percent: str
    is_charging: str
    no_users: str
    list_users: str
    select_user: str
    list_no_user: str
    chat_in_user: str
    failed_select_user: str
    send_failed: str
    baned_user: str
    enter_continue: str
    disconnect_server: str
    connect_error_view: str
    connect_error: str
    not_supported: str

handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    fmt="%(asctime)s - %(log_color)s%(levelname)s%(reset)s - %(message)s",
    datefmt="%H:%M:%S",
    log_colors={
        "DEBUG": "cyan",
        "INFO": "green",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "red,bg_white",
    },
))

config_path = "client_config.yaml"
window_get_started = False
input_loop: asyncio.Task | None = None
default_config: dict[str, str | bool | list[str]] = {
    "lang": "zh-CN",
    "log_level": "INFO",
    "server_url": "http://localhost:5000",
    "chat_mode": False,
    "token": "",
    "pass_window": ["任务切换", "新通知"]
}
log_level_dict = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
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
        config = yaml.safe_load(f)
        try:
            config = Config.model_validate(config, extra="forbid")
        except ValidationError as e:
            logging.critical(f"config validation error: {e}\nuse default config")
            config = Config.model_validate(default_config, extra="forbid")
except FileNotFoundError:
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(default_config, f, indent=2, default_flow_style=False, allow_unicode=True)
    logging.info("config file not found, created default config")
    sys.exit(0)

i18n_dict = {
    "zh": i18n_zh,
    "zh-cn": i18n_zh,
    "zh_cn": i18n_zh,
    "en-us": i18n_en,
    "en_us": i18n_en,
    "en": i18n_en,
}

logger = logging.getLogger(__name__)
logging.basicConfig(handlers=[handler], level=log_level_dict[config.log_level])

if config.log_level == "DEBUG":
    sio = socketio.AsyncClient(
        reconnection=False,
        engineio_logger=logger,
        logger=logger,  # type: ignore
        handle_sigint=False
    )
else:
    sio = socketio.AsyncClient(reconnection=False)

try:
    itr = I18n.model_validate(i18n_dict[config.lang.lower()], extra="forbid")
except KeyError:
    logging.warning(f"lang {config.lang} not supported, use default lang en-us")
    itr = I18n.model_validate(i18n_en, extra="forbid")

async def get_window_title():
    global window_get_started
    if window_get_started:
        return
    window_get_started = True
    loop = asyncio.get_running_loop()

    def _window_get_event():
        @CFUNCTYPE(None, c_void_p, c_int, c_int, c_int, c_int, c_int, c_int)
        def win_event_handler(hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
            if event in [
                win32con.EVENT_SYSTEM_FOREGROUND,
                win32con.EVENT_SYSTEM_MINIMIZEEND
            ]:
                title, switch_window_time = win32gui.GetWindowText(hwnd), int(time.time())
                if not title or title in config.pass_window:
                    return
                logging.debug(itr.window_change.format(f"{title} {switch_window_time}"))
                if not sio.connected:
                    return
                asyncio.run_coroutine_threadsafe(sio.emit("window_change", (title, switch_window_time)), loop)

        hook = windll.user32.SetWinEventHook(
            win32con.EVENT_SYSTEM_FOREGROUND,
            win32con.EVENT_SYSTEM_MINIMIZEEND,
            0,
            win_event_handler,
            0,
            0,
            win32con.WINEVENT_OUTOFCONTEXT
        )
        pythoncom.PumpMessages()

    threading.Thread(target=_window_get_event, daemon=True).start()


def _activated_args_patch(_, event):
    e = ToastActivatedEventArgs._from(event) # type: ignore
    vs = e.user_input
    try:
        names = list(vs.keys())
    except Exception:
        names = []
    user_input = {}
    for name in names:
        user_input[name] = IPropertyValue._from(vs.lookup(name)).get_string() # type: ignore
    return {'arguments': e.arguments, 'user_input': user_input}

win11toast_module.activated_args = _activated_args_patch

def _get_cpu_name() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0") as k:
            name, _ = winreg.QueryValueEx(k, "ProcessorNameString")
            if name:
                return name.strip()
    except Exception:
        pass
    return platform.processor() or "Unknown CPU"

def _get_gpu_info() -> list[dict[str, str | int | None]]:
    """获取GPU信息"""
    try:
        pythoncom.CoInitialize()
        try:
            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
            items = wmi.ExecQuery("SELECT Name, AdapterRAM, CurrentRefreshRate, VideoModeDescription FROM Win32_VideoController")
            import re
            def _to_int(v) -> int | None:
                if v is None:
                    return None
                try:
                    return int(v)
                except Exception:
                    if isinstance(v, str):
                        s = "".join(ch for ch in v if ch.isdigit())
                        return int(s) if s else None
                    return None
            res: list[dict[str, str | int | None]] = []
            for i in items:
                try:
                    gpu_name = str(getattr(i, "Name", "") or "")
                    mem = getattr(i, "AdapterRAM", None)
                    mem_mb = int(mem / (1024 * 1024)) if mem is not None else None
                    vm_raw = getattr(i, "VideoModeDescription", None)
                    vm_s = str(vm_raw) if vm_raw is not None else ""
                    m = re.search(r"(\d+)\s*[xX×]\s*(\d+)", vm_s)
                    rr = _to_int(getattr(i, "CurrentRefreshRate", None)) or 0
                    video_mode = f"{m.group(1)}x{m.group(2)}@{rr}Hz" if m else None
                    if gpu_name == "" or not mem:
                        logging.warning(itr.invalid_gpu.format(f"{gpu_name} {mem_mb} {video_mode}"))
                        continue
                    res.append({
                        "name": gpu_name,
                        "memory_mb": mem_mb,
                        "video_mode": video_mode,
                    })
                except Exception:
                    pass
            return res
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    except Exception:
        return []

def _get_battery_info() -> tuple[int, bool]:
    """
    获取电池信息：电量百分比及是否充电
    返回 (电量百分比, 是否充电)
    """
    battery = psutil.sensors_battery()
    if battery is None:
        # 无电池设备
        logging.warning(itr.battery_not_found)
        return (0, False)
    return int(battery.percent), battery.power_plugged

@sio.event
async def connect() -> bool:
    logging.info(itr.connect_server)
    return True

@sio.event
async def client_toast(data: dict):
    logging.debug(itr.server_toast.format(data))
    await toast(**data)

@sio.event
async def client_toast_reply(data: dict) -> str:
    logging.debug(itr.server_toast_reply.format(data))
    text: str = ""
    def on_click(args):
        nonlocal text
        text = (
            args.get("user_input", {}).get("Reply", "")
            or args.get("user_input", {}).get("回复消息", "")
            or ""
        )
    await toast(on_click=on_click, **data)
    logging.debug(itr.client_toast_reply.format(text))
    return text

def _make_screenshot_bytes() -> bytes:
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", shot.size, shot.rgb)
        b = BytesIO()
        img.save(b, format="PNG")
        data = b.getvalue()
        max_size = 10 * 1024 * 1024
        if len(data) > max_size:
            logging.warning(itr.image_too_big.format(len(data) / (1024 * 1024)).split("\n")[0])
            logging.warning(itr.image_too_big.split("\n")[1])
            quality = 95
            b = BytesIO()
            img.save(b, format="JPEG", quality=quality, optimize=True, progressive=True)
            data = b.getvalue()
            while len(data) > max_size and quality >= 70:
                quality -= 5
                b = BytesIO()
                img.save(b, format="JPEG", quality=quality, optimize=True, progressive=True)
                data = b.getvalue()
            if len(data) > max_size:
                w, h = img.size
                while len(data) > max_size and min(w, h) > 480:
                    w = int(w * 0.85)
                    h = int(h * 0.85)
                    resized = img.resize((w, h), resample=Image.Resampling.LANCZOS)
                    b = BytesIO()
                    resized.save(b, format="JPEG", quality=max(50, quality), optimize=True, progressive=True)
                    data = b.getvalue()
    return data

@sio.event
async def screenshot() -> bytes:
    photo_data = await asyncio.to_thread(_make_screenshot_bytes)
    logging.debug(itr.created_image.format(f"{len(photo_data) / (1024 * 1024):.2f}"))
    return photo_data

@sio.event
async def client_toast_on_click(data: dict) -> bytes:
    photo_data = b""
    def on_click(args):
        nonlocal photo_data
        photo_data = _make_screenshot_bytes()
        logging.debug(itr.created_image.format(f"{len(photo_data) / (1024 * 1024):.2f}"))
        return photo_data
    await toast(on_click=on_click, **data)
    return photo_data

@sio.event
async def get_hard_info() -> dict[
    str,
    dict[
        str,
        str | int | bool | list[
            dict[
                str, str | int | None
            ]
        ]
    ]]:
    """
    获取硬件信息
    """
    def _get_system_info() -> tuple[
        str,          # cpu_name
        str,          # cpu_speed
        int,          # cpu_cores
        int,          # cpu_threads
        float,          # cpu_usage
        int,          # total_mb
        int,          # available_mb
        int,          # battery_percent
        bool,         # is_charging
        int,          # uptime
        list[dict[str, str | int | None]]  # gpu_info
    ]:
        cpu_name = _get_cpu_name()
        logging.debug(itr.cpu_name.format(cpu_name))
        freq = psutil.cpu_freq()
        cpu_speed = f"{((freq.max or freq.current) / 1000):.2f} GHz" if freq else "Unknown"
        logging.debug(itr.cpu_speed.format(cpu_speed))
        cpu_cores = psutil.cpu_count(logical=False) or 0
        cpu_threads = psutil.cpu_count(logical=True) or 0
        cpu_usage = psutil.cpu_percent()
        logging.debug(itr.cpu_cores.format(cpu_cores))
        logging.debug(itr.cpu_threads.format(cpu_threads))
        logging.debug(itr.cpu_usage.format(cpu_usage))
        sysmem = psutil.virtual_memory()
        total_mb = int(sysmem.total / (1024 * 1024))
        available_mb = int(sysmem.available / (1024 * 1024))
        logging.debug(itr.total_memory.format(total_mb))
        logging.debug(itr.available_memory.format(available_mb))
        uptime = int(time.time() - psutil.boot_time())
        logging.debug(itr.uptime.format(uptime))
        gpu_info = _get_gpu_info()
        logging.debug(itr.gpu_info.format(gpu_info))
        battery_percent, is_charging = _get_battery_info()
        logging.debug(itr.battery_percent.format(battery_percent))
        logging.debug(itr.is_charging.format(is_charging))
        return cpu_name, cpu_speed, cpu_cores, cpu_threads, cpu_usage, total_mb, available_mb, battery_percent, is_charging, uptime, gpu_info
    
    cpu_name, cpu_speed, cpu_cores, cpu_threads, cpu_usage, total_mb, available_mb, battery_percent, is_charging, uptime, gpu_info = (
        await asyncio.to_thread(_get_system_info)
    )
    final = {
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
        "gpu_info": gpu_info
    }
    return final

@sio.event
async def get_user_msg(name: str, msg: str):
    if config.chat_mode:
        sys.stdout.write("\r" + " " * 100 + "\r")
        print(f"[{name}]: {msg}")
        sys.stdout.write("> ")
        sys.stdout.flush()
    else:
        logging.debug(f"Received message from {name}: {msg}")

@sio.event
async def disconnect(reason: str) -> bool:
    logging.info(itr.disconnect_server.format(reason))
    return False

async def ainput(prompt: str = "") -> str:
    """
    网上找的邪门办法
    在异步循环中调用同步的强阻塞的input函数
    丢线程池，应该是没有问题的
    https://blog.51cto.com/u_16213433/11756308
    测试了一下，在等待输入状态时调用 cancel 时能够立马抛出 CancelledError 异常而无需回车
    缺点是 CancelledError 后需要键入回车才能进行下一次输入
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)

async def input_msg():
    while True:
        if not sio.connected:
            await asyncio.sleep(1)
            continue
        user_list: list[tuple[int, str, str]] | None = await sio.call("get_user_list")
        if not user_list:
            print(itr.no_users)
            print(itr.list_users)
            while sio.connected:
                msg = await ainput("> ")
                if not msg:
                    continue
                if msg in [".list"]:
                    break
            continue
        print(itr.list_users)
        for user_id, username, fullname in user_list:
            print(f"{user_id} - {fullname} {username}")
        try:
            user_id = int(await ainput(itr.select_user))
            if user_id not in [user[0] for user in user_list]:
                print(itr.list_no_user)
                continue
            print(itr.chat_in_user.format(user_id))
            print(itr.list_users)
        except ValueError:
            print(itr.failed_select_user)
            continue
        while sio.connected:
            msg = await ainput("> ")
            if not msg:
                continue
            if msg in [".list"]:
                break
            success, err_msg = await sio.call("send_telegram_message", (user_id, msg)) # type: ignore
            if success:
                continue
            else:
                print(itr.send_failed.format(err_msg))
                if "bot was blocked by the user" in err_msg:
                    print(itr.baned_user.format(user_id))
                    break

async def main():
    await get_window_title()
    try:
        await sio.connect(config.server_url, auth={"token": config.token})
    except Exception as e:
        logging.critical(itr.connect_error_view.format(e).split("\n")[0])
        logging.critical(itr.connect_error_view.split("\n")[1])
        return

    if config.chat_mode:
        asyncio.create_task(input_msg())

    try:
        await sio.wait()
    finally:
        await sio.disconnect()

    while True:
        try:
            await sio.connect(config.server_url, auth={"token": config.token})

            if config.chat_mode:
                sys.stdout.write(itr.enter_continue)
                sys.stdout.flush()

            await sio.wait()
        except Exception as e:
            logging.error(itr.connect_error.format(e))
            await asyncio.sleep(3)
        finally:
            await sio.disconnect()

if __name__ == "__main__":
    try:
        if psutil.WINDOWS:
            asyncio.run(main())
        else:
            logging.critical(itr.not_supported)
            pass
    except KeyboardInterrupt:
        logging.debug("KeyboardInterrupt received, exiting...")
    except asyncio.CancelledError:
        logging.debug("Main loop cancelled")
    except RuntimeError as e:
        if str(e) == "Event loop is closed":
            pass
        else:
            raise e
    except Exception as e:
        logging.critical(itr.connect_error.format(e))
