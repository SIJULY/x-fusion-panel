async def on_server_click_handler(server):
    # 1. 获取当前视图状态
    current_scope = CURRENT_VIEW_STATE.get('scope')
    current_data = CURRENT_VIEW_STATE.get('data')
    
    # 2. 判断是否点击了当前正在显示的服务器 (通过 URL 唯一标识判断)
    is_same_server = False
    if current_scope == 'SINGLE' and current_data:
        try:
            # 比较 URL 是最稳妥的唯一性判断
            if current_data.get('url') == server.get('url'):
                is_same_server = True
        except: pass

    if is_same_server:
        # 🛑 核心逻辑：如果是同一台机器，阻止重绘页面！
        # 这样右下角的 SSH 窗口就不会被销毁，连接得以保持。
        
        # 可选：如果希望点击时顺便刷新一下节点列表（不影响 SSH），可以调用这个
        if REFRESH_CURRENT_NODES:
            res = REFRESH_CURRENT_NODES()
            if res and asyncio.iscoroutine(res):
                await res
        return 

    # 3. 如果是不同的服务器，才执行正常的切换逻辑 (重绘页面)
    await refresh_content('SINGLE', server)