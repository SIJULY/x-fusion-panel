async def fetch_inbounds_safe(server_conf, force_refresh=False, sync_name=False):
    url = server_conf['url']
    
    # 探针机器处理：除非强制刷新，否则直接信任推送的缓存
    if server_conf.get('probe_installed', False) and not force_refresh:
        return NODES_DATA.get(url, [])
    
    # 如果不是强制刷新且已有数据，直接返回
    if not force_refresh and url in NODES_DATA and NODES_DATA[url]: 
        return NODES_DATA[url]
    
    async with SYNC_SEMAPHORE:
        try:
            mgr = get_manager(server_conf)
            # 增加超时判断
            inbounds = await asyncio.wait_for(run_in_bg_executor(mgr.get_inbounds), timeout=15)
            
            if inbounds is not None:
                NODES_DATA[url] = inbounds
                server_conf['_status'] = 'online'
                # ... (保持原有的同步名称逻辑)
                return inbounds
            
            # --- 关键修复：同步失败时，不要设置为空列表，保留之前的缓存 ---
            # 仅在完全没有旧数据时才标记离线
            if url not in NODES_DATA:
                NODES_DATA[url] = []
                server_conf['_status'] = 'offline'
            return NODES_DATA.get(url, [])
            
        except Exception as e:
            logger.warning(f"⚠️ {server_conf.get('name')} 同步跳过: {e}")
            # 发生异常时保留现场，不更新 _status 为 offline，防止误报
            return NODES_DATA.get(url, [])