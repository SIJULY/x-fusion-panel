async def save_servers(): 
    global GLOBAL_UI_VERSION # ✨ 关键：引入全局版本变量
    
    # 执行保存
    await safe_save(CONFIG_FILE, SERVERS_CACHE)
    
    # ✨ 关键：更新版本号，通知前台 /status 页面进行结构重绘
    GLOBAL_UI_VERSION = time.time() 
    
    # 触发后台仪表盘数据的静默刷新
    await refresh_dashboard_ui()