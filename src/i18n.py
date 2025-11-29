import yaml
from pydantic import BaseModel, ValidationError, ConfigDict
from .config import config
from .log import i18n_log

default_en = {
    "socketio": {
        "please_screenshot": "{user} requested a screenshot",
        "click_allow": "Click Allow",
        "is_screenshot": "You have been screenshotted",
        "screenshot_from": "From {user}",
        "no_auth_token": "Connected client ({sid}) has no Token or Token error",
        "auth_token_help": "Please use the Token in the configuration file",
        "warning_connect": (
            "Client {sid} did not provide Token, but it was specified in the query string\n"
            "This is not recommended, please at least specify the token in the request header"
        ),
        "reject_connect": "Connection of client {sid1} rejected because already connected to client {sid2}",
        "type_client": "Client {sid} is a listener client",
        "reply": "Reply",
        "send": "Send",
        "connected": "Client {sid} connected",
        "disconnected": "Client {sid} disconnected"
    },
    "sqlite": {
        "connected": "Database connected",
        "add_user": "User {user} added to database",
        "update_user": "User {user} updated in database",
        "del_user": "User {user} removed from database",
        "add_allow_user": "Allowed user {user} added to database",
        "add_ban_user": "Banned user {user} added to database",
        "del_allow_user": "Allowed user {user} removed from database",
        "del_ban_user": "Banned user {user} removed from database",
        "close": "Database closed"
    },
    "telegram": {
        "token_empty": "Telegram bot token is empty",
        "command_start": "Register",
        "command_ping": "Get current client window info",
        "command_screenshot": "Request screenshot",
        "command_info": "Get client hardware info",
        "command_phone_info": "Get phone info",
        "command_ban": "Ban user",
        "command_unban": "Unban user",
        "command_add": "Add allowed user",
        "command_list": "List registered users",
        "command_allowlist": "List allowed users",
        "command_banlist": "List banned users",
        "command_del": "Remove allowed user",
        "command_help": "Show help info",
        "no_admin": "You do not have admin privileges",
        "banned_user": "You are banned and cannot use this bot",
        "no_register": "You are not registered, please use /start to register",
        "register_user": (
            "Welcome! {user}\n"
            "You have successfully registered."
        ),
        "register_user_already": "You are registered, please use /ping to get current client window info",
        "help": (
            "/start - Register user\n"
            "/ping - Get current client window info\n"
            "/screenshot - Request screenshot\n"
            "/info - Get client hardware info\n"
            "/phone_info - Get phone info\n"
            "/ban - Ban user\n"
            "/unban - Unban user\n"
            "/add - Add allowed user\n"
            "/list - List registered users\n"
            "/allowlist - List allowed users\n"
            "/banlist - List banned users\n"
            "/del - Remove allowed user\n"
            "/help - Show this message\n\n"
            "To notify the client, send a message to this bot. Only plain text messages are supported.\n"
            "Spamming or advertising behavior will be banned."
        ),
        "no_app": "None",
        "no_client": "Client not connected",
        "second": "{time} seconds ago",
        "minute": "{time} minutes ago",
        "window_msg": (
            "PC Current window: {title}\n"
            "Phone Current app: {phone_app}\n"
            "> History windows:\n"
        ),
        "phone_window_msg": (
            "> History apps:\n"
        ),
        "forbidden_screenshot": "Screenshot function disabled",
        "wait_screenshot": "Taking screenshot...",
        "no_screenshot": "Screenshot request not passed",
        "client_timeout": "Client timeout",
        "battery_charging_direct": "Direct Power",
        "battery_charging_smart": "Smart charging",
        "battery_charging_charge": "Charging",
        "battery_charging_not": "Not charging",
        "hardware_info": (
            "Client hardware info:\n"
            "CPU: {name}\n"
            "Base speed: {base_speed}\n"
            "Cores/Threads: {cores}/{threads}\n"
            "Current usage: {usage}%\n"
            "Total memory/available memory: {total_mb} MB / {available_mb} MB\n"
            "Battery: {battery_percent}% {is_charging}\n"
            "Uptime: {uptime}\n"
        ),
        "gpu_info": (
            "> GPU {counter}:\n"
            "> GPU: {name}\n"
            "> VRAM: {memory} MB\n"
            "> Display mode: {mode}\n"
        ),
        "phone_info": (
            "Phone info:\n"
            "Current status: {status}\n"
            "Battery: {battery}% {power_status}\n"
            "Device name: {device_info}\n"
            "Android version: {android_version}\n"
            "Uptime: {uptime}"
        ),
        "input_user_id": "Please enter user ID",
        "input_user_id_error": "Please enter a valid user ID",
        "no_user": "No user",
        "list_user": "Registered users:",
        "list_allow_user": "Allowed users:",
        "list_ban_user": "Banned users:",
        "user_no_register": "User {user} not registered",
        "add_user_success": "User {user} added to allow list",
        "del_user_success": "User {user} removed from allow list",
        "ban_user_success": "User {user} banned",
        "unban_user_success": "User {user} unbanned",
        "none_text": "Only text messages are supported",
        "ban_success": "You have been banned",
        "ready_flood_message": "Please do not send too many messages or you will be banned",
        "flood_message": "You have been banned for sending too many messages",
        "command_register_success": "Registered {commands} commands",
        "started": "Telegram bot started"
    }
}

class i18n_socketio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    please_screenshot: str
    click_allow: str
    is_screenshot: str
    screenshot_from: str
    no_auth_token: str
    auth_token_help: str
    warning_connect: str
    reject_connect: str
    type_client: str
    reply: str
    send: str
    connected: str
    disconnected: str

class i18n_sqlite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connected: str
    add_user: str
    update_user: str
    del_user: str
    add_allow_user: str
    add_ban_user: str
    del_allow_user: str
    del_ban_user: str
    close: str

class i18n_telegram(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token_empty: str
    command_start: str
    command_ping: str
    command_screenshot: str
    command_info: str
    command_phone_info: str
    command_ban: str
    command_unban: str
    command_add: str
    command_list: str
    command_allowlist: str
    command_banlist: str
    command_del: str
    command_help: str
    no_admin: str
    banned_user: str
    no_register: str
    register_user: str
    register_user_already: str
    help: str
    no_app: str
    no_client: str
    second: str
    minute: str
    window_msg: str
    phone_window_msg: str
    forbidden_screenshot: str
    wait_screenshot: str
    no_screenshot: str
    client_timeout: str
    battery_charging_direct: str
    battery_charging_smart: str
    battery_charging_charge: str
    battery_charging_not: str
    hardware_info: str
    gpu_info: str
    phone_info: str
    input_user_id: str
    input_user_id_error: str
    no_user: str
    list_user: str
    list_allow_user: str
    list_ban_user: str
    user_no_register: str
    add_user_success: str
    del_user_success: str
    ban_user_success: str
    unban_user_success: str
    none_text: str
    ban_success: str
    ready_flood_message: str
    flood_message: str
    command_register_success: str
    started: str

class I18n(BaseModel):
    model_config = ConfigDict(extra="forbid")
    socketio: i18n_socketio
    sqlite: i18n_sqlite
    telegram: i18n_telegram

def load_i18n(lang: str = config.lang) -> I18n:
    try:
        with open(f"src/i18n/{lang}.yaml", "r", encoding="utf-8") as f:
            try:
                _i18n = I18n.model_validate(yaml.safe_load(f))
                i18n_log.info(f"Using language {lang}")
                return _i18n
            except ValidationError as e:
                i18n_log.warning(f"Error loading {lang}.yaml: {e}")
                i18n_log.warning(f"Using Default Language")
                return I18n.model_validate(default_en)
    except FileNotFoundError:
        i18n_log.warning(f"Language file {lang}.yaml not found")
        i18n_log.warning(f"Using Default Language")
        return I18n.model_validate(default_en)

itr = load_i18n()
