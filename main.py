import asyncio
import argparse
import uvicorn
from src.socket_route import app, final_app
from src.config import config
from src.sql import init_db, close_con

need_start_bot = False

@app.before_serving
async def start_bot():
    if not need_start_bot:
        return
    from src.telegram import bot, commands
    await init_db()
    await bot.set_my_commands(commands)
    asyncio.create_task(bot.polling())

@app.after_serving
async def close_db():
    if not need_start_bot:
        return
    await close_con()

def main():
    global need_start_bot
    parser = argparse.ArgumentParser(description="Telegram Monitoring Bot")
    parser.add_argument("--nobot", action="store_true", help="Disable Telegram bot, only run http and socketio server")
    args = parser.parse_args()
    if not args.nobot:
        need_start_bot = True

    uvicorn.run(final_app, host=config.bind, port=config.port, log_config=None)

if __name__ == "__main__":
    main()