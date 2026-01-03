import colorlog
import logging
from telegram_monitoring.src.config import config

log_level_dist = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    fmt="%(asctime)s - %(name)s - %(log_color)s%(levelname)s%(reset)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={
        "DEBUG": "cyan",
        "INFO": "green",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "red,bg_white",
    },
))
logging.basicConfig(handlers=[handler], level=log_level_dist[config.log_level])
logging.getLogger("uvicorn.error").name = "fastapi"
logging.getLogger("uvicorn.access").name = "fastapi"
logging.getLogger("aiosqlite").name = "sql"

logging.getLogger("TeleBot").setLevel(logging.ERROR)

telegram_log = logging.getLogger("telegram")
socketio_log = logging.getLogger("socketio")
sql_log = logging.getLogger("sql")
i18n_log = logging.getLogger("i18n")