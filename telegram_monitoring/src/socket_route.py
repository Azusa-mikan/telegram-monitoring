import socketio
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Depends, Response
import json
import time
from collections import deque
from pydantic import BaseModel
import hmac
from telegram_monitoring.src.config import config
from telegram_monitoring.src.log import socketio_log
from telegram_monitoring.src.i18n import itr

@asynccontextmanager
async def lifespan(app: FastAPI):
    if getattr(app.state, "need_start_bot", False):
        from telegram_monitoring.src.sql import init_db, close_con
        from telegram_monitoring.src.telegram import bot, commands
        await init_db()
        await bot.set_my_commands(commands)
        asyncio.create_task(bot.polling())

    try:
        yield
    finally:
        if getattr(app.state, "need_start_bot", False):
            from telegram_monitoring.src.sql import close_con
            await close_con()

app = FastAPI(lifespan=lifespan)

if config.log_level == "DEBUG":
    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins="*",
        max_http_buffer_size=10*1024*1024, # 10MB，因为 Telegram 限制了上传图片大小（此项目使用字节流传输）
        logger=socketio_log, # type: ignore
        engineio_logger=socketio_log,
    )
else:
    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins="*",
        max_http_buffer_size=10*1024*1024
    )

app.mount("/socket.io", socketio.ASGIApp(sio)) # type: ignore

client_sid: str = ""
listen_client_sid: str = ""
now_window_list: deque[tuple[str, int]] = deque()
phone_now_app: dict = {}
now_app_list: deque[tuple[str, int]] = deque()

class PhoneWebhook(BaseModel):
    name: str
    status: str
    battery: int
    power_status: str
    device_info: str
    android_version: str
    uptime: str

@sio.event
async def connect(sid: str, environ: dict, auth: dict[str, str]) -> bool:
    global client_sid, listen_client_sid
    async def emit_disconnect():
        await sio.disconnect(sid)
        socketio_log.warning(itr.socketio.no_auth_token.format(sid=sid))
        socketio_log.warning(itr.socketio.auth_token_help)

    # 优先从 auth 字典里取，没有再从 environ 里取
    client_type = (
        (auth or {}).get("type")
        or environ.get("HTTP_TYPE", "")
        or ("listen_client" if "type=listen_client" in environ.get("QUERY_STRING", "") else "")
    )

    # 如果已有 client_sid 且新连接不是 listen_client，则拒绝
    if client_sid and client_type != "listen_client":
        socketio_log.warning(itr.socketio.reject_connect.format(sid1=sid, sid2=client_sid))
        return False
    # 如果已有 listen_client_sid 且新连接是 listen_client，也拒绝
    if listen_client_sid and client_type == "listen_client":
        socketio_log.warning(itr.socketio.reject_connect.format(sid1=sid, sid2=listen_client_sid))
        return False

    if client_type == "listen_client":
        socketio_log.info(itr.socketio.type_client.format(sid=sid))
        listen_client_sid = sid
    else:
        client_sid = sid

    try:
        auth_token = (auth or {}).get("token") or environ.get("HTTP_TOKEN", "")
    except AttributeError:
        if f"token={config.token}" in environ.get("QUERY_STRING", ""):
            socketio_log.warning(itr.socketio.warning_connect.format(sid=sid).split("\n")[0])
            socketio_log.warning(itr.socketio.warning_connect.split("\n")[1])
            return True
        else:
            await emit_disconnect()
            return False

    auth_token_byte = auth_token.encode("utf-8")
    config_token_byte = config.token.encode("utf-8")
    if not hmac.compare_digest(auth_token_byte, config_token_byte):
        await emit_disconnect()
        return False

    socketio_log.info(itr.socketio.connected.format(sid=sid))
    return True

@sio.event
async def disconnect(sid: str):
    global client_sid, listen_client_sid
    if sid == client_sid:
        client_sid = ""
    elif sid == listen_client_sid:
        listen_client_sid = ""
    now_window_list.clear()
    socketio_log.info(itr.socketio.disconnected.format(sid=sid))

async def emit_window_change(emit: bool) -> tuple[str, dict]:
    if not now_window_list:
        return "", {}

    now_window, switch_window_time = now_window_list[-1]
    window_list: list[dict[str, str | int]] = [
        {"title": window, "switch_window_time": time}
        for window, time in now_window_list
    ]

    data_raw: dict= {
        "now_window": now_window,
        "switch_window_time": switch_window_time,
        "window_list": window_list
    }
    data = json.dumps(data_raw, ensure_ascii=False, indent=2)
    if emit and listen_client_sid:
        await sio.emit("get_window", data, to=listen_client_sid)
        socketio_log.debug(f"Emitted window change to listen client {listen_client_sid}")

    return data, data_raw

async def emit_phone_app(emit: bool) -> tuple[str, dict]:
    if not phone_now_app:
        return "", {}

    data: str = json.dumps(phone_now_app, ensure_ascii=False, indent=2)
    data_raw = phone_now_app

    if emit and listen_client_sid:
        await sio.emit("get_app", data, to=listen_client_sid)
        socketio_log.debug(f"Emitted phone app to listen client {listen_client_sid}")

    return data, data_raw

@sio.event
async def window_change(sid: str, title: str, switch_window_time: int):
    global now_window_list
    if not now_window_list:
        window_title = ""
    else:
        window_title, _ = now_window_list[-1]

    if window_title == title:
        return

    now_window_list.append((title, switch_window_time))
    if len(now_window_list) > config.max_window:
        now_window_list.popleft()

    socketio_log.debug(f"Received window info from client {sid}: {title} {switch_window_time}")
    await emit_window_change(True)

async def verify_token(authorization: str = Header(...)) -> None:
    """验证 token"""
    token = authorization.replace("Bearer ", "")
    auth_token_byte = token.encode("utf-8")
    config_token_byte = config.token.encode("utf-8")
    if not hmac.compare_digest(auth_token_byte, config_token_byte):
        raise HTTPException(status_code=401, detail="Unauthorized")
    

@app.get("/")
async def _index() -> Response:
    return Response(
        content="Copyright (C) 2026 Azusa-Mikan\n",
        headers={
            "X-Copyright": "Copyright (C) 2026 Azusa-Mikan"
        },
        media_type="text/plain"
    )

@app.get("/now_window", dependencies=[Depends(verify_token)], response_model=None)
async def _get_now_window() -> dict | Response:
    """获取当前窗口"""
    data, data_raw = await emit_window_change(False)
    if not data:
        return Response(status_code=204)

    return data_raw

@app.get("/now_app", dependencies=[Depends(verify_token)], response_model=None)
async def _get_now_app() -> dict | Response:
    """获取当前应用"""

    data, data_raw = await emit_phone_app(False)
    if not data:
        return Response(status_code=204)

    return data_raw

@app.post("/phone_webhook", dependencies=[Depends(verify_token)])
async def _push_phone_now_app(request: PhoneWebhook) -> Response:
    """接收手机应用变化"""

    socketio_log.debug(f"Received phone json: \n{request.model_dump(mode='json')}")

    if not now_app_list or request.name != now_app_list[-1][0]:
        now_app_list.append((request.name, int(time.time())))
        if len(now_app_list) > config.max_window:
            now_app_list.popleft()

    if request.status == "屏幕关闭":
        phone_now_app["name"] = "手机已熄屏"

    if request.power_status == "打开":
        phone_now_app["power_status"] = itr.telegram.battery_charging_charge
    else:
        phone_now_app["power_status"] = itr.telegram.battery_charging_not

    phone_now_app["switch_app_time"] = int(time.time())
    phone_now_app["app_list"] = list(now_app_list)

    await emit_phone_app(True)
    socketio_log.debug(f"Received phone now app from client: {phone_now_app}")
    return Response(status_code=200)
    

async def get_now_window() -> list[tuple[str, int]]:
    return list(now_window_list)

def get_client_sid() -> str:
    return client_sid

async def get_phone_now_app() -> dict[str, str | int | list[tuple[str, int]]]:
    return phone_now_app