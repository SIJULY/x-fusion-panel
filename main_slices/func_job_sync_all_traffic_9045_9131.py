async def job_sync_all_traffic():
    logger.info("🕒 [智能同步] 检查同步任务进度...")
    
    # 目标周期：23.5 小时
    TARGET_DURATION = 84600 
    
    start_ts = ADMIN_CONFIG.get('sync_job_start', 0)
    current_idx = ADMIN_CONFIG.get('sync_job_index', 0)
    now = time.time()

    # 重置逻辑
    if (now - start_ts > 86400) or start_ts == 0 or current_idx >= len(SERVERS_CACHE):
        logger.info("🔄 [智能同步] 启动新一轮 24h 周期任务")
        start_ts = now
        current_idx = 0
        ADMIN_CONFIG['sync_job_start'] = start_ts
        ADMIN_CONFIG['sync_job_index'] = 0
        await save_admin_config()
    else:
        logger.info(f"♻️ [智能同步] 恢复进度: 第 {current_idx+1} 台")

    i = current_idx
    
    while True:
        current_total = len(SERVERS_CACHE)
        if i >= current_total: break
            
        try:
            server = SERVERS_CACHE[i]
            
            # 如果是探针/双模，直接跳过轮询与休眠 
            if server.get('probe_installed', False):
                # 仅在调试时或者是第一台时打印一下，防止日志刷屏
                # logger.info(f"⏩ [跳过轮询] {server.get('name')} (探针实时推送)")
                
                # 依然保存进度，防止重启后回滚
                i += 1
                ADMIN_CONFIG['sync_job_index'] = i
                
                # 每处理 10 个探针保存一次磁盘，减少 IO
                if i % 10 == 0: await save_admin_config()
                
                # 极速通过，仅做极短休眠防止 CPU 独占
                await asyncio.sleep(0.05)
                continue
            
            # === 下面是仅针对【纯 API 模式】的逻辑 ===
            loop_step_start = time.time()
            
            # 执行主动拉取
            await fetch_inbounds_safe(server, force_refresh=True, sync_name=False)
            
            progress = (i + 1) / current_total
            logger.info(f"⏳ [API轮询] {server.get('name')} 同步完成 ({progress:.1%})")

            # 保存进度
            ADMIN_CONFIG['sync_job_index'] = i + 1
            await save_admin_config()

            # 动态计算休眠 (仅针对需要轮询的机器)
            remaining_items = current_total - (i + 1)
            if remaining_items > 0:
                elapsed_time = time.time() - start_ts
                time_left = TARGET_DURATION - elapsed_time
                
                if time_left <= 0:
                    sleep_seconds = 1
                else:
                    # 重新计算剩余列表中，可能还有多少个 API 机器？
                    # 这里简单处理，假设剩下的都是 API 机器来计算间隔，保证 24小时 兜底
                    base_interval = time_left / remaining_items
                    sleep_seconds = base_interval * random.uniform(0.9, 1.1)
                    cost_time = time.time() - loop_step_start
                    sleep_seconds = max(1, sleep_seconds - cost_time)

                logger.info(f"💤 API 轮询休眠: {int(sleep_seconds)}秒...")
                await asyncio.sleep(sleep_seconds)
                
        except Exception as e:
            logger.warning(f"⚠️ 同步异常: {server.get('name')} - {e}")
            await asyncio.sleep(10)

        i += 1

    await save_nodes_cache()
    await refresh_dashboard_ui()
    logger.info("✅ [智能同步] 本轮任务全部完成")