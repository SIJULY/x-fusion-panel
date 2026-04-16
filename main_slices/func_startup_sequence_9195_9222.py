async def startup_sequence():
    global PROCESS_POOL
    # ✨ 初始化进程池 (4核) - 专门处理 Ping 等 CPU/阻塞任务
    PROCESS_POOL = ProcessPoolExecutor(max_workers=4)
    logger.info("🚀 进程池已启动 (ProcessPoolExecutor)")

    # ✨ 添加定时任务
    # 1. 流量同步 (3小时一次)
    scheduler.add_job(job_sync_all_traffic, 'interval', hours=24, id='traffic_sync', replace_existing=True, max_instances=1)
    
    # 2. 服务器状态监控与报警 (120秒一次) ✨✨✨
    scheduler.add_job(job_monitor_status, 'interval', seconds=120, id='status_monitor', replace_existing=True, max_instances=1)
    
    scheduler.start()
    logger.info("🕒 APScheduler 定时任务已启动")

    # ✨ 开机立即执行一次 (作为初始化)
    asyncio.create_task(job_sync_all_traffic())
    asyncio.create_task(job_check_geo_ip())
    
    # 首次运行填充状态缓存，避免刚开机就疯狂报警
    async def init_alert_cache():
        await asyncio.sleep(5) # 等待几秒让系统稳一下
        if ADMIN_CONFIG.get('tg_bot_token'):
            logger.info("🛡️ 正在初始化监控状态缓存...")
            await job_monitor_status()
            
    asyncio.create_task(init_alert_cache())