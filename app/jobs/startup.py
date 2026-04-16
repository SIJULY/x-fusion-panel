import asyncio
from concurrent.futures import ProcessPoolExecutor

from app.core.logging import logger, scheduler
from app.core.state import ADMIN_CONFIG
from app.jobs.geoip import job_check_geo_ip
from app.jobs.monitor import job_monitor_status
from app.jobs.traffic import job_sync_all_traffic


async def startup_sequence():
    from app.core import state

    state.PROCESS_POOL = ProcessPoolExecutor(max_workers=4)
    logger.info('🚀 进程池已启动 (ProcessPoolExecutor)')

    scheduler.add_job(job_sync_all_traffic, 'interval', hours=24, id='traffic_sync', replace_existing=True, max_instances=1)
    scheduler.add_job(job_monitor_status, 'interval', seconds=120, id='status_monitor', replace_existing=True, max_instances=1)
    scheduler.start()
    logger.info('🕒 APScheduler 定时任务已启动')

    asyncio.create_task(job_sync_all_traffic())
    asyncio.create_task(job_check_geo_ip())

    async def init_alert_cache():
        await asyncio.sleep(5)
        if ADMIN_CONFIG.get('tg_bot_token'):
            logger.info('🛡️ 正在初始化监控状态缓存...')
            await job_monitor_status()

    asyncio.create_task(init_alert_cache())
