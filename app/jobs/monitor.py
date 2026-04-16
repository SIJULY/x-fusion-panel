import asyncio
import time

from app.api.notifications import send_telegram_message
from app.core.logging import logger
from app.core.state import ADMIN_CONFIG, ALERT_CACHE, FAILURE_COUNTS, SERVERS_CACHE
from app.services.probe import get_server_status


async def job_monitor_status():
    """
    监控任务：每分钟检查一次服务器状态
    优化：将并发数从 5 提升至 50，以支持 1000 台服务器在 30-40秒内完成轮询
    修正：彻底跳过未安装探针的 X-UI 面板机器
    """
    sema = asyncio.Semaphore(50)
    failure_threshold = 3
    current_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())

    async def _check_single_server(srv):
        if not srv.get('probe_installed', False):
            return

        async with sema:
            await asyncio.sleep(0.01)
            res = await get_server_status(srv)
            name = srv.get('name', 'Unknown')
            url = srv['url']

            if not ADMIN_CONFIG.get('tg_bot_token'):
                return

            display_ip = url.split('://')[-1].split(':')[0]
            is_physically_online = isinstance(res, dict) and res.get('status') == 'online'

            if is_physically_online:
                FAILURE_COUNTS[url] = 0
                if ALERT_CACHE.get(url) == 'offline':
                    msg = (
                        f"🟢 **恢复：服务器已上线**\n\n"
                        f"🖥️ **名称**: `{name}`\n"
                        f"🔗 **地址**: `{display_ip}`\n"
                        f"🕒 **时间**: `{current_time}`"
                    )
                    logger.info(f"🔔 [恢复] {name} 已上线")
                    asyncio.create_task(send_telegram_message(msg))
                    ALERT_CACHE[url] = 'online'
            else:
                current_count = FAILURE_COUNTS.get(url, 0) + 1
                FAILURE_COUNTS[url] = current_count
                if current_count >= failure_threshold and ALERT_CACHE.get(url) != 'offline':
                    msg = (
                        f"🔴 **警告：服务器离线**\n\n"
                        f"🖥️ **名称**: `{name}`\n"
                        f"🔗 **地址**: `{display_ip}`\n"
                        f"🕒 **时间**: `{current_time}`\n"
                        f"⚠️ **提示**: 连续监测，无法连接"
                    )
                    logger.warning(f"🔔 [报警] {name} 确认离线 (重试{current_count}次)")
                    asyncio.create_task(send_telegram_message(msg))
                    ALERT_CACHE[url] = 'offline'

    tasks = [_check_single_server(s) for s in SERVERS_CACHE]
    await asyncio.gather(*tasks)
