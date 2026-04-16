async def refresh_content(scope='ALL', data=None, force_refresh=False, sync_name_action=False, page_num=1, manual_client=None):
    # 1. 上下文获取
    client = manual_client
    if not client:
        try: client = ui.context.client
        except: pass
    if not client: return

    with client:
        global CURRENT_VIEW_STATE, REFRESH_LOCKS, LAST_SYNC_MAP
        import time
        
        # 唯一标识 key (精确到页)
        cache_key = f"{scope}::{data}::P{page_num}"
        lock_key = cache_key
        
        now = time.time()
        last_sync = LAST_SYNC_MAP.get(cache_key, 0)
        
        # --- 预计算当前页的服务器成分 ---
        targets = get_targets_by_scope(scope, data)
        start_idx = (page_num - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_servers = targets[start_idx:end_idx] if targets else []
        
        has_probe = any(s.get('probe_installed') for s in current_page_servers)
        has_api_only = any(not s.get('probe_installed') for s in current_page_servers)
        
        # 2. 🛑 冷却逻辑与缓存渲染
        # 如果不是强制刷新，且满足以下任一条件则直接使用缓存渲染：
        # A. 在 30分钟 冷却时间内
        # B. 这一页全是已安装探针的机器（数据本身就是最新的）
        is_all_probe = has_probe and not has_api_only
        
        if not force_refresh and ((now - last_sync < SYNC_COOLDOWN) or is_all_probe):
            CURRENT_VIEW_STATE.update({'scope': scope, 'data': data, 'page': page_num, 'render_token': now})
            
            # 执行初次渲染（从内存加载）
            await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)
            
            # 智能提示语
            if is_all_probe:
                safe_notify("⚡ 实时数据 (探针推送)", "positive", timeout=1000)
            else:
                mins_ago = int((now - last_sync) / 60)
                safe_notify(f"🕒 缓存数据 ({mins_ago}分前)", "ongoing", timeout=1000)
            return

        # 3. 锁机制：防止同一个页面同时触发多个同步任务
        if lock_key in REFRESH_LOCKS:
             return

        # 4. 状态锁定：记录当前的 render_token，用于后台任务完成后核对
        CURRENT_VIEW_STATE.update({'scope': scope, 'data': data, 'page': page_num, 'render_token': now})
        
        # 5. 立即渲染一次（显示当前内存中的“旧”数据，保证点击瞬间不白屏）
        await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)

        if not current_page_servers: return

        # 6. 启动后台抓取任务
        async def _background_fetch(token_at_start):
            REFRESH_LOCKS.add(lock_key)
            try:
                # 过滤出真正需要从接口拉取的机器（非探针机器）
                sync_targets = [s for s in current_page_servers if not s.get('probe_installed')]
                
                if sync_targets:
                    if force_refresh:
                        safe_notify(f"🔄 正在同步 {len(sync_targets)} 台 API 节点...", "ongoing")
                    
                    # 执行并发抓取
                    tasks = [fetch_inbounds_safe(s, force_refresh=True, sync_name=sync_name_action) for s in sync_targets]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # --- ✨ 关键点：核对身份 ---
                    # 只有当抓取结束时，用户还没点别的页面（token没变），才触发重绘
                    if CURRENT_VIEW_STATE.get('render_token') == token_at_start:
                        with client:
                            await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)
                            # 记录同步成功的时间戳
                            LAST_SYNC_MAP[cache_key] = time.time()
                            if force_refresh: safe_notify("✅ 同步完成", "positive")
                            try: render_sidebar_content.refresh()
                            except: pass
                else:
                    # 全是探针机器，直接标记当前页已更新
                    LAST_SYNC_MAP[cache_key] = time.time()
            finally:
                REFRESH_LOCKS.discard(lock_key)
            
        asyncio.create_task(_background_fetch(now))