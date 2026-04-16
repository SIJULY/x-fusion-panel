async def save_admin_config(): 
    global GLOBAL_UI_VERSION # ✨ 关键：引入全局版本变量
    
    # 执行保存
    await safe_save(ADMIN_CONFIG_FILE, ADMIN_CONFIG)
    
    # ✨ 关键：更新版本号，通知前台 /status 页面进行结构重绘 (例如分组变化)
    GLOBAL_UI_VERSION = time.time()