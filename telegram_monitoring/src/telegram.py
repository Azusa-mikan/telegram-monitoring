import time
from telebot.async_telebot import AsyncTeleBot
from telebot.util import extract_arguments
from telebot import types
from asyncio import sleep
from collections import deque
from telegram_markdown_converter import convert_markdown
import sys
from telegram_monitoring.src.config import config
from telegram_monitoring.src.log import telegram_log
from telegram_monitoring.src.socket_route import sio, get_now_window, get_client_sid, get_phone_now_app
from telegram_monitoring.src.socket_command import *
from telegram_monitoring.src.sql import *
from telegram_monitoring.src.i18n import itr

_times = deque()

patterns: list[str] = []
with open('prohibited_words.txt', encoding='utf-8') as f:
    for line in f:
        patterns.append(line.rstrip('\n'))

try:
    bot = AsyncTeleBot(config.telegram.token)
except Exception:
    telegram_log.error(itr.telegram.token_empty)
    sys.exit(1)

commands = [
    types.BotCommand("/start", itr.telegram.command_start),
    types.BotCommand("/ping", itr.telegram.command_ping),
    types.BotCommand("/screenshot", itr.telegram.command_screenshot),
    types.BotCommand("/info", itr.telegram.command_info),
    types.BotCommand("/phone_info", itr.telegram.command_phone_info),
    types.BotCommand("/ban", itr.telegram.command_ban),
    types.BotCommand("/unban", itr.telegram.command_unban),
    types.BotCommand("/add", itr.telegram.command_add),
    types.BotCommand("/list", itr.telegram.command_list),
    types.BotCommand("/allowlist", itr.telegram.command_allowlist),
    types.BotCommand("/banlist", itr.telegram.command_banlist),
    types.BotCommand("/del", itr.telegram.command_del),
    types.BotCommand("/help", itr.telegram.command_help),
]

async def flood_message() -> int:
    """返回4秒内消息数量"""
    WINDOW = 4.0

    now = time.time()
    _times.append(now)

    # 移除过期的时间
    while _times[0] < now - WINDOW:
        _times.popleft()
    
    # 返回当前时间内的消息数量
    return len(_times)

async def judge_should_handle(message) -> bool:
    """判断是否应该处理该消息"""
    if message.from_user.is_bot:
        return False
    me = await bot.get_me()
    if message.chat.type == "private":
        return True
    if message.chat.type in ("group", "supergroup"):
        if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == me.id:
            return True
        if message.entities:
            for e in message.entities:
                if e.type == "bot_command" and e.offset == 0:
                    cmd = message.text[:e.length]
                    if "@" in cmd:
                        mention = cmd.split("@", 1)[1]
                        if (me.username or "").lower() == mention.lower():
                            return True
                    return False
        return False
    return False

def admin(func):
    """仅允许管理员使用的装饰器"""
    async def wrapper(message):
        user_id = message.from_user.id
        if user_id not in config.telegram.admins:
            await bot.send_message(
                message.chat.id,
                itr.telegram.no_admin
            )
            return
        return await func(message)
    return wrapper

def user(func):
    """仅允许注册用户使用的装饰器，若用户被拉黑则无法使用，管理员除外"""
    async def wrapper(message):
        if not await judge_should_handle(message):
            return
        user_id = message.from_user.id
        if user_id in config.telegram.admins:
            return await func(message)
        if await check_ban_user_db(user_id):
            await bot.send_message(
                message.chat.id,
                itr.telegram.banned_user
            )
            return
        user_name = await get_user_db(user_id)
        if user_name is None:
            await bot.send_message(
                message.chat.id,
                itr.telegram.no_register
            )
            return
        return await func(message)
    return wrapper

def should_handle(func):
    """判断是否应该处理该消息的装饰器"""
    async def wrapper(message):
        if not await judge_should_handle(message):
            return
        return await func(message)
    return wrapper

@bot.message_handler(commands=["start"])
@should_handle # 仅 start、help 命令使用此装饰器
async def start(message):
    """注册用户"""
    user_id: int = message.from_user.id
    user_name: str = "@" + message.from_user.username or ""
    full_name: str = message.from_user.full_name
    if not await add_user_db(user_id, user_name, full_name):
        await bot.send_message(
            message.chat.id,
            itr.telegram.register_user_already
        )
        return
    await bot.send_message(
        message.chat.id,
        itr.telegram.register_user.format(user=full_name)
    )

@bot.message_handler(commands=["help"])
@should_handle # 仅 start、help 命令使用此装饰器
async def help(message):
    """显示帮助信息"""
    help_msg = itr.telegram.help
    await bot.send_message(message.chat.id, help_msg)

@bot.message_handler(commands=["ping"])
@user
async def get_window(message):
    """获取当前客户端信息"""
    now_window_list = await get_now_window()
    phone_app = await get_phone_now_app()
    phone_now_app = phone_app["name"] if phone_app else itr.telegram.no_app
    app_list: list[tuple[str, int]] = (
        phone_app["app_list"]
        if phone_app and isinstance(phone_app["app_list"], list) else []
    )
    now_window: str = now_window_list[-1][0] if now_window_list else itr.telegram.no_client
    now_time = int(time.time())
    def _fmt_delta(dt: int) -> str:
        if dt < 60:
            return itr.telegram.second.format(time=max(1, dt))
        return itr.telegram.minute.format(time=dt // 60)
    final_msg = itr.telegram.window_msg.format(title=now_window, phone_app=phone_now_app)
    for title, switch_window_time in now_window_list:
        final_msg = "".join([
            final_msg,
            f"> {title} - {_fmt_delta(now_time - switch_window_time)}\n"
        ])

    final_msg = "".join([
        final_msg,
        itr.telegram.phone_window_msg
    ])
    for title, switch_app_time in app_list:
        final_msg = "".join([
            final_msg,
            f"> {title} - {_fmt_delta(now_time - switch_app_time)}\n"
        ])

    final_msg = convert_markdown(final_msg)
    await bot.send_message(message.chat.id, final_msg, parse_mode="MarkdownV2")

@bot.message_handler(commands=["screenshot"])
@user
async def screenshot(message):
    """获取当前窗口截图"""
    if not config.telegram.screenshot.allow:
        await bot.send_message(message.chat.id, itr.telegram.forbidden_screenshot)
        return
    client_sid = get_client_sid()
    if not client_sid:
        await bot.send_message(message.chat.id, itr.telegram.no_client)
        return
    wait_msg = await bot.send_message(message.chat.id, itr.telegram.wait_screenshot)
    img_bytes, allow = await client_screenshot(message.from_user.full_name, message.from_user.id)
    if not allow and not img_bytes:
        await bot.delete_message(message.chat.id, wait_msg.message_id)
        await bot.send_message(message.chat.id, itr.telegram.no_screenshot)
        return
    elif not img_bytes:
        await bot.delete_message(message.chat.id, wait_msg.message_id)
        await bot.send_message(message.chat.id, itr.telegram.client_timeout)
        return
    telegram_log.debug(f"Screenshot size to send: {len(img_bytes) / (1024 * 1024):.2f} MB")
    await bot.delete_message(message.chat.id, wait_msg.message_id)
    photo = await bot.send_photo(message.chat.id, img_bytes, protect_content=True)
    delete_time = config.telegram.screenshot.delete_time
    if delete_time > 0:
        await sleep(delete_time)
        await bot.delete_message(message.chat.id, photo.message_id)

@bot.message_handler(commands=["info"])
@user
async def hard_info(message):
    """获取客户端硬件信息"""
    counter: int = 0
    client_sid = get_client_sid()
    if not client_sid:
        await bot.send_message(message.chat.id, itr.telegram.no_client)
        return

    hard_info = await client_get_hard_info()
    if not hard_info:
        await bot.send_message(message.chat.id, itr.telegram.client_timeout)
        return

    cpu_info: dict[str, str | int | float] = hard_info["cpu_info"]
    gpu_info: list[dict[str, str | int | None]] = hard_info["gpu_info"]
    mem_info: dict[str, int] = hard_info["memory"]
    battery_info: dict[int, bool] = hard_info["battery"]
    uptime: int = hard_info["uptime"]
    # 将unix时间戳格式化为 时:分:秒
    hours, rem = divmod(uptime, 3600)
    minutes, seconds = divmod(rem, 60)
    uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    if battery_info["percent"] == 0: # type: ignore
        battery_charging = itr.telegram.battery_charging_direct

    if battery_info["percent"] > 95 and battery_info["is_charging"]:  # type: ignore
        battery_charging = itr.telegram.battery_charging_smart
    elif battery_info["is_charging"]:  # type: ignore
        battery_charging = itr.telegram.battery_charging_charge
    else:
        battery_charging = itr.telegram.battery_charging_not

    final_msg = itr.telegram.hardware_info.format(
        name=cpu_info["name"],
        base_speed=cpu_info["base_speed"],
        cores=cpu_info["cores"],
        threads=cpu_info["threads"],
        usage=cpu_info["usage"],
        total_mb=mem_info["total_mb"],
        available_mb=mem_info["available_mb"],
        battery_percent=battery_info["percent"], # type: ignore
        is_charging=battery_charging,
        uptime=uptime_str,
    )
    for gpu in gpu_info:
        counter += 1
        final_msg = "\n".join([
            final_msg,
            itr.telegram.gpu_info.format(
                counter=counter,
                name=gpu["name"],
                memory=gpu["memory_mb"],
                mode=gpu["video_mode"],
            ),
        ])

    final_msg = convert_markdown(final_msg)
    await bot.send_message(message.chat.id, final_msg, parse_mode="MarkdownV2")

@bot.message_handler(commands=["phone_info"])
@user
async def phone_info(message):
    """获取手机信息"""
    phone_now_app = await get_phone_now_app()
    if not phone_now_app:
        await bot.send_message(message.chat.id, itr.telegram.no_app)
        return
    final_msg = itr.telegram.phone_info.format(
        status=phone_now_app["status"],
        battery=phone_now_app["battery"],
        power_status=phone_now_app["power_status"],
        device_info=phone_now_app["device_info"],
        android_version=phone_now_app["android_version"],
        uptime=phone_now_app["uptime"]
    )
    await bot.send_message(message.chat.id, final_msg)

@bot.message_handler(commands=["add"])
@admin
async def add_allow_user(message):
    """添加允许用户"""
    args = extract_arguments(message.text)
    if not args:
        await bot.send_message(message.chat.id, itr.telegram.input_user_id)
        return
    try:
        user_id = int(args)
    except ValueError:
        await bot.send_message(message.chat.id, itr.telegram.input_user_id_error)
        return
    if not await add_allow_user_db(user_id):
        await bot.send_message(message.chat.id, itr.telegram.user_no_register.format(user=user_id))
        return
    await bot.send_message(message.chat.id, itr.telegram.add_user_success.format(user=user_id))

@bot.message_handler(commands=["list"])
@admin
async def list_user(message):
    """列出所有允许用户"""
    users: list[tuple[int, str, str]] = await get_all_user_db()
    if not users:
        await bot.send_message(message.chat.id, itr.telegram.no_user)
        return
    
    final_msg = itr.telegram.list_user
    final_msg = "".join(
        [
            final_msg,
            # 使用 * 号将列表解包为独立的字符串元素，供 "".join 拼接
            *[
                f"\n{full_name} - {username or user_id}"
                for user_id, username, full_name in users
            ]
        ]
    )
    await bot.send_message(message.chat.id, final_msg)

@bot.message_handler(commands=["del"])
@admin
async def del_allow_user(message):
    """删除允许用户"""
    args = extract_arguments(message.text)
    if not args:
        await bot.send_message(message.chat.id, itr.telegram.input_user_id)
        return
    try:
        user_id = int(args)
    except ValueError:
        await bot.send_message(message.chat.id, itr.telegram.input_user_id_error)
        return
    if not await del_allow_user_db(user_id):
        await bot.send_message(message.chat.id, itr.telegram.user_no_register.format(user=user_id))
        return
    await bot.send_message(message.chat.id, itr.telegram.del_user_success.format(user=user_id))

@bot.message_handler(commands=["ban"])
@admin
async def ban_user(message):
    """添加封禁用户"""
    args = extract_arguments(message.text)
    if not args:
        await bot.send_message(message.chat.id, itr.telegram.input_user_id)
        return
    try:
        user_id = int(args)
    except ValueError:
        await bot.send_message(message.chat.id, itr.telegram.input_user_id_error)
        return
    if not await add_ban_user_db(user_id):
        await bot.send_message(message.chat.id, itr.telegram.user_no_register.format(user=user_id))
        return
    await bot.send_message(message.chat.id, itr.telegram.ban_user_success.format(user=user_id))

@bot.message_handler(commands=["allowlist"])
@admin
async def list_allow_user(message):
    """列出所有允许用户"""
    users: list[tuple[int, str, str]] = await list_allow_user_db()
    if not users:
        await bot.send_message(message.chat.id, itr.telegram.no_user)
        return
    final_msg = itr.telegram.list_allow_user
    final_msg = "".join(
        [
            final_msg,
            # 使用 * 号将列表解包为独立的字符串元素，供 "".join 拼接
            *[
                f"\n{full_name} - {username or user_id}"
                for user_id, username, full_name in users
            ]
        ]
    )
    await bot.send_message(message.chat.id, final_msg)

@bot.message_handler(commands=["banlist"])
@admin
async def list_ban_user(message):
    """列出所有封禁用户"""
    users: list[tuple[int, str, str]] = await list_ban_user_db()
    if not users:
        await bot.send_message(message.chat.id, itr.telegram.no_user)
        return
    
    final_msg = itr.telegram.list_ban_user
    final_msg = "".join(
        [
            final_msg,
            # 使用 * 号将列表解包为独立的字符串元素，供 "".join 拼接
            *[
                f"\n{full_name} - {username or user_id}"
                for user_id, username, full_name in users
            ]
        ]
    )
    await bot.send_message(message.chat.id, final_msg)

@bot.message_handler(commands=["unban"])
@admin
async def unban_user(message):
    """删除封禁用户"""
    args = extract_arguments(message.text)
    if not args:
        await bot.send_message(message.chat.id, itr.telegram.input_user_id)
        return
    try:
        user_id = int(args)
    except ValueError:
        await bot.send_message(message.chat.id, itr.telegram.input_user_id_error)
        return
    if not await del_ban_user_db(user_id):
        await bot.send_message(message.chat.id, itr.telegram.user_no_register.format(user=user_id))
        return
    await bot.send_message(message.chat.id, itr.telegram.unban_user_success.format(user=user_id))

@bot.message_handler(func=lambda message: True)
@user
async def all_msg(message):
    """监听私聊所有消息以及群聊回复机器人"""
    # 判断是否是刷屏
    msg_count = await flood_message()
    telegram_log.debug(f"User {message.from_user.id} sent {msg_count} messages")
    if msg_count > 10:
        await bot.send_message(message.chat.id, itr.telegram.flood_message)
        await add_ban_user_db(message.from_user.id)
        return
    if msg_count >= 7:
        await bot.send_message(message.chat.id, itr.telegram.ready_flood_message)
        return

    if any(pattern in message.text for pattern in patterns):
        await bot.send_message(message.chat.id, itr.telegram.ban_success)
        await add_ban_user_db(message.from_user.id)
        return

    userfullname = message.from_user.full_name
    username = "@" + message.from_user.username or ""
    await update_user_db(message.from_user.id, username, userfullname)
    msg = message.text
    await sio.emit("get_user_msg", (userfullname, msg))
    reply_msg = await client_toast_with_input(userfullname, msg)
    if not reply_msg:
        return
    await bot.send_message(message.chat.id, reply_to_message_id=message.message_id, text=reply_msg)

@sio.event
async def send_telegram_message(sid: str, user_id: int, message: str) -> tuple[bool, str]:
    """向指定用户发送消息"""
    telegram_log.debug(f"Received message from {sid} to user {user_id}: {message}")
    try:
        await bot.send_message(user_id, message)
    except Exception as e:
        telegram_log.error(f"Failed to send message to user {user_id}: {e}")
        if "bot was blocked by the user" in str(e):
            await del_user_db(user_id)
        return False, str(e)
    return True, ""

telegram_log.info(itr.telegram.command_register_success.format(commands=len(commands)))
telegram_log.info(itr.telegram.started)