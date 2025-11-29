from pydantic import BaseModel, ValidationError
from .socket_route import sio, get_client_sid
from .sql import *
from .log import socketio_log
from .i18n import itr

__all__ = [
    "client_toast",
    "client_toast_with_input",
    "client_screenshot",
    "client_get_hard_info"
]

class cpu_info(BaseModel):
    name: str
    base_speed: str
    cores: int
    threads: int
    usage: float

class memoryinfo(BaseModel):
    total_mb: int
    available_mb: int

class batteryinfo(BaseModel):
    percent: int
    is_charging: bool

class client_hardwareinfo(BaseModel):
    cpu_info: cpu_info
    memory: memoryinfo
    battery: batteryinfo
    uptime: int
    gpu_info: list[dict[str, str | int | None]]

async def client_toast(title: str, body: str):
    client_sid: str = get_client_sid()
    await sio.emit(
        "client_toast",
        data={"title": title, "body": body},
        to=client_sid
        )

async def client_toast_with_input(title: str, body: str) -> str | None:
    client_sid: str = get_client_sid()
    data: dict[str, str | dict[str, str]] = {
        "title": title,
        "body": body,
        "input": f'{itr.socketio.reply}',
        "button": {
            'activationType': 'protocol', 
            'arguments': 'http:',
            'content': f'{itr.socketio.send}',
            'hint-inputId': 'reply'
        }
    }
    try:
        reply_text: str | None = await sio.call(
            "client_toast_reply",
            data=data,
            to=client_sid,
            timeout=10
            )
        socketio_log.debug(f"Received reply from client {client_sid}: {reply_text}")
        if reply_text is None:
            return None
        return reply_text
    except Exception:
        return None

async def client_screenshot_on_click(userfullname: str, userid: int) -> bytes:
    client_sid: str = get_client_sid()
    data: dict[str, str] = {
        "title": itr.socketio.please_screenshot.format(user=f"{userfullname}({userid})"),
        "body": itr.socketio.click_allow
    }
    try:
        photo_data: bytes | None = await sio.call(
            "client_toast_on_click",
            data=data,
            to=client_sid,
            timeout=10  # 适配 Windows 通知超时时间
        )
    except Exception as e:
        socketio_log.error(f"Error in client_toast_on_click: {e}")
        return b""
    if photo_data is None:
        socketio_log.debug(f"Received empty screenshot from client {client_sid}")
        return b""
    socketio_log.debug(f"Received screenshot from client {client_sid}")
    return photo_data

async def client_screenshot(userfullname: str, userid: int) -> tuple[bytes, bool]:
    client_sid: str = get_client_sid()
    allow: bool = await check_allow_user_db(userid)
    if not allow:
        socketio_log.debug(f"User {userfullname}({userid}) is not allowed user")
        png_byte: bytes = await client_screenshot_on_click(userfullname, userid)
        return png_byte, allow
    await client_toast(
        itr.socketio.is_screenshot,
        itr.socketio.screenshot_from.format(user=f"{userfullname}({userid})")
        )
    socketio_log.debug(f"User {userfullname}({userid}) is allowed user")
    try:
        png_bytes: bytes | None = await sio.call("screenshot", to=client_sid, timeout=5)
        if png_bytes is None:
            socketio_log.debug(f"Received empty screenshot from client {client_sid}")
            return b"", allow
        socketio_log.debug(f"Received screenshot from client {client_sid}")
        return png_bytes, allow
    except Exception as e:
        socketio_log.error(f"Error in client_screenshot: {e}")
        return b"", allow

async def client_get_hard_info() -> dict | None:
    client_sid: str = get_client_sid()
    try:
        socketio_log.debug(f"Already get hard info from client {client_sid}")
        hard_info: dict | None = await sio.call("get_hard_info", to=client_sid, timeout=5)
        if hard_info is None:
            socketio_log.debug(f"Received empty hard info from client {client_sid}")
            return None
        try:
            client_hardwareinfo(**hard_info)
        except ValidationError as e:
            socketio_log.error(f"Error in client_get_hard_info: {e}")
            return None
    except Exception as e:
        socketio_log.error(f"Error in client_get_hard_info: {e}")
        return None
    socketio_log.debug(f"Received hard info from client {client_sid}: {hard_info}")
    return hard_info

@sio.event
async def get_user_list(sid) -> list[tuple[int, str, str]]:
    socketio_log.debug(f"Received get_user_list request from client {sid}")
    return await get_all_user_db()
