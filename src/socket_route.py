import socketio
import quart
import json
import time
from collections import deque
from pydantic import BaseModel, ValidationError
import hmac
from .config import config
from .log import socketio_log
from .i18n import itr

app = quart.Quart(__name__)

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

final_app = socketio.ASGIApp(sio, app)

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

async def emit_window_change(emit: bool) -> str | None:
    if not now_window_list:
        return

    now_window, switch_window_time = now_window_list[-1]
    window_list: list[dict[str, str | int]] = [
        {"title": window, "switch_window_time": time}
        for window, time in now_window_list
    ]

    data: str = json.dumps({
        "now_window": now_window,
        "switch_window_time": switch_window_time,
        "window_list": window_list
    },
    ensure_ascii=False,
    indent=2,
    )
    if emit and listen_client_sid:
        await sio.emit("get_window", data, to=listen_client_sid)
        socketio_log.debug(f"Emitted window change to listen client {listen_client_sid}")

    return data

async def emit_phone_app(emit: bool) -> str | None:
    if not phone_now_app:
        return

    data: str = json.dumps(phone_now_app, ensure_ascii=False, indent=2)

    if emit and listen_client_sid:
        await sio.emit("get_app", data, to=listen_client_sid)
        socketio_log.debug(f"Emitted phone app to listen client {listen_client_sid}")

    return data

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

@app.route("/", methods=["GET"])
async def _index() -> quart.Response:
    return quart.Response(
        response="Copyright (C) 2026 Azusa-Mikan\n",
        headers={
            "X-Copyright": "Copyright (C) 2026 Azusa-Mikan"
        },
        content_type="text/plain"
    )

@app.route("/now_window", methods=["GET"])
async def _get_now_window() -> quart.Response:
    token = quart.request.headers.get("Authorization", "").replace("Bearer ", "")
    auth_token_byte = token.encode("utf-8")
    config_token_byte = config.token.encode("utf-8")
    if not hmac.compare_digest(auth_token_byte, config_token_byte):
        return quart.Response(status=401)

    data: str | None = await emit_window_change(False)
    if data is None:
        return quart.Response(status=204)

    return quart.Response(data, status=200, mimetype="application/json")

@app.route("/now_app", methods=["GET"])
async def _get_now_app() -> quart.Response:
    token = quart.request.headers.get("Authorization", "").replace("Bearer ", "")
    auth_token_byte = token.encode("utf-8")
    config_token_byte = config.token.encode("utf-8")
    if not hmac.compare_digest(auth_token_byte, config_token_byte):
        return quart.Response(status=401)

    data: str | None = await emit_phone_app(False)
    if data is None:
        return quart.Response(status=204)

    return quart.Response(data, status=200, mimetype="application/json")

@app.route("/phone_webhook", methods=["POST"])
async def _push_phone_now_app() -> quart.Response:
    token = quart.request.headers.get("Authorization", "").replace("Bearer ", "")
    auth_token_byte = token.encode("utf-8")
    config_token_byte = config.token.encode("utf-8")
    if not hmac.compare_digest(auth_token_byte, config_token_byte):
        return quart.Response(status=401)

    phone_now_app = await quart.request.get_json()
    socketio_log.debug(f"Received phone json: \n{phone_now_app}")
    try:
        phone_app = PhoneWebhook.model_validate(phone_now_app, extra="forbid")
    except ValidationError as e:
        socketio_log.error(f"Error in phone_webhook: {e}")
        return quart.Response(status=400)

    if not now_app_list or phone_app.name != now_app_list[-1][0]:
        now_app_list.append((phone_app.name, int(time.time())))
        if len(now_app_list) > config.max_window:
            now_app_list.popleft()

    if phone_app.status == "屏幕关闭":
        phone_now_app["name"] = "手机已熄屏"

    if phone_app.power_status == "打开":
        phone_now_app["power_status"] = itr.telegram.battery_charging_charge
    else:
        phone_now_app["power_status"] = itr.telegram.battery_charging_not

    phone_now_app["switch_app_time"] = int(time.time())
    phone_now_app["app_list"] = list(now_app_list)

    await emit_phone_app(True)
    socketio_log.debug(f"Received phone now app from client: {phone_now_app}")
    return quart.Response(status=200)
    

async def get_now_window() -> list[tuple[str, int]]:
    return list(now_window_list)

def get_client_sid() -> str:
    return client_sid

async def get_phone_now_app() -> dict[str, str | int | list[tuple[str, int]]]:
    return phone_now_app