import asyncio
import random
import time

from app.core.logging import logger
from app.core.state import ADMIN_CONFIG, SERVERS_CACHE
from app.services.xui_fetch import fetch_inbounds_safe
from app.storage.repositories import save_admin_config, save_nodes_cache
from app.ui.components.dashboard import refresh_dashboard_ui


async def job_sync_all_traffic():
    logger.info('🕒 [智能同步] 检查同步任务进度...')

    target_duration = 84600
    start_ts = ADMIN_CONFIG.get('sync_job_start', 0)
    current_idx = ADMIN_CONFIG.get('sync_job_index', 0)
    now = time.time()

    if (now - start_ts > 86400) or start_ts == 0 or current_idx >= len(SERVERS_CACHE):
        logger.info('🔄 [智能同步] 启动新一轮 24h 周期任务')
        start_ts = now
        current_idx = 0
        ADMIN_CONFIG['sync_job_start'] = start_ts
        ADMIN_CONFIG['sync_job_index'] = 0
        await save_admin_config()
    else:
        logger.info(f'♻️ [智能同步] 恢复进度: 第 {current_idx + 1} 台')

    i = current_idx

    while True:
        current_total = len(SERVERS_CACHE)
        if i >= current_total:
            break

        try:
            server = SERVERS_CACHE[i]
            if server.get('probe_installed', False):
                i += 1
                ADMIN_CONFIG['sync_job_index'] = i
                if i % 10 == 0:
                    await save_admin_config()
                await asyncio.sleep(0.05)
                continue

            loop_step_start = time.time()
            await fetch_inbounds_safe(server, force_refresh=True, sync_name=False)

            progress = (i + 1) / current_total
            logger.info(f"⏳ [API轮询] {server.get('name')} 同步完成 ({progress:.1%})")

            ADMIN_CONFIG['sync_job_index'] = i + 1
            await save_admin_config()

            remaining_items = current_total - (i + 1)
            if remaining_items > 0:
                elapsed_time = time.time() - start_ts
                time_left = target_duration - elapsed_time

                if time_left <= 0:
                    sleep_seconds = 1
                else:
                    base_interval = time_left / remaining_items
                    sleep_seconds = base_interval * random.uniform(0.9, 1.1)
                    cost_time = time.time() - loop_step_start
                    sleep_seconds = max(1, sleep_seconds - cost_time)

                logger.info(f'💤 API 轮询休眠: {int(sleep_seconds)}秒...')
                await asyncio.sleep(sleep_seconds)

        except Exception as e:
            logger.warning(f"⚠️ 同步异常: {server.get('name')} - {e}")
            await asyncio.sleep(10)

        i += 1

    await save_nodes_cache()
    await refresh_dashboard_ui()
    logger.info('✅ [智能同步] 本轮任务全部完成')
