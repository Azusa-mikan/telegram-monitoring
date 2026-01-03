from pydantic import BaseModel, ValidationError, ConfigDict
import yaml
import sys
import secrets
from pathlib import Path

default_config = {
    "lang": "zh_CN",
    "log_level": "INFO",
    "bind": "0.0.0.0",
    "port": 5000,
    "token": secrets.token_urlsafe(32),
    "max_window": 5,
    "telegram": {
        "token": "",
        "admins": [],
        "screenshot": {
            "allow": True,
            "delete_time": 3
        }
    }
}

class ScreenshotConfig(BaseModel):
    allow: bool
    delete_time: int
    model_config = ConfigDict(extra="forbid")

class TelegramConfig(BaseModel):
    token: str
    admins: list[int]
    screenshot: ScreenshotConfig
    model_config = ConfigDict(extra="forbid")

class Config(BaseModel):
    lang: str
    log_level: str
    bind: str
    port: int
    token: str
    max_window: int
    telegram: TelegramConfig
    model_config = ConfigDict(extra="forbid")

def load_config(config_path = Path(__file__).parents[2] / "config.yaml") -> Config:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            try:
                config = Config.model_validate(config)
            except ValidationError:
                print("config file validation failed")
                sys.exit(1)
    except FileNotFoundError:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(default_config, f, default_flow_style=False, allow_unicode=True)
            print("config file created, please edit it")
            print(f"Token: {default_config['token']}")
            sys.exit(0)
    return config

config = load_config()