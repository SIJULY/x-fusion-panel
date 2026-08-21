import httpx
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

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.error(f"❌ TG 发送失败: {e}")
