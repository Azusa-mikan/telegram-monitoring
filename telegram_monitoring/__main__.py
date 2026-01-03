import argparse
import uvicorn
from telegram_monitoring.src.socket_route import app
from telegram_monitoring.src.config import config

def main():
    parser = argparse.ArgumentParser(description="Telegram Monitoring Bot")
    parser.add_argument("--nobot", action="store_true", help="Disable Telegram bot, only run http and socketio server")
    args = parser.parse_args()
    
    app.state.need_start_bot = not args.nobot

    uvicorn.run(app, host=config.bind, port=config.port, log_config=None)

if __name__ == "__main__":
    main()