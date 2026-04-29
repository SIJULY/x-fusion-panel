import requests
from nicegui import run

from app.core.logging import logger
from app.core.state import ADMIN_CONFIG


async def send_telegram_message(text):
    """发送 Telegram 消息"""
    token = ADMIN_CONFIG.get('tg_bot_token')
    chat_id = ADMIN_CONFIG.get('tg_chat_id')

    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }

    def _do_req():
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"❌ TG 发送失败: {e}")

    await run.io_bound(_do_req)
