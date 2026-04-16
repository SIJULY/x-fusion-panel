async def job_monitor_status():
    """
    监控任务：每分钟检查一次服务器状态
    优化：将并发数从 5 提升至 50，以支持 1000 台服务器在 30-40秒内完成轮询
    修正：彻底跳过未安装探针的 X-UI 面板机器
    """
    # 50 并发
    sema = asyncio.Semaphore(50) 
    
    # 定义报警阈值：连续失败 3 次才报警
    FAILURE_THRESHOLD = 3 
    
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    async def _check_single_server(srv):
        # 🛑 如果未安装探针，直接跳过所有监控逻辑
        # 这样后台就不会去尝试获取这些机器的状态，也不会记录历史或报警
        if not srv.get('probe_installed', False):
            return

        async with sema:
            # 稍微让出一点 CPU 时间片，避免高并发瞬间卡顿 UI
            await asyncio.sleep(0.01) 
            
            res = await get_server_status(srv)
            name = srv.get('name', 'Unknown')
            url = srv['url']
            
            # 如果没配 TG，后面的报警逻辑就跳过
            if not ADMIN_CONFIG.get('tg_bot_token'): return

            # 清洗 IP，只显示纯 IP
            display_ip = url.split('://')[-1].split(':')[0]
            
            # 判断当前物理探测状态
            is_physically_online = False
            if isinstance(res, dict) and res.get('status') == 'online':
                is_physically_online = True
            
            # --- 核心防抖逻辑 ---
            if is_physically_online:
                # 1. 如果当前检测在线，直接重置失败计数器
                FAILURE_COUNTS[url] = 0
                
                # 2. 检查是否需要发“恢复通知”
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
                # 1. 如果当前检测离线，计数器 +1
                current_count = FAILURE_COUNTS.get(url, 0) + 1
                FAILURE_COUNTS[url] = current_count
                
                # 2. 只有计数器达到阈值，才报警
                if current_count >= FAILURE_THRESHOLD:
                    if ALERT_CACHE.get(url) != 'offline':
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

    # 创建所有任务并执行
    tasks = [_check_single_server(s) for s in SERVERS_CACHE]
    await asyncio.gather(*tasks)