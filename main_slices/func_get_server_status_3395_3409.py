async def get_server_status(server_conf):
    raw_url = server_conf['url']
    
    # 只有当服务器安装了 Python 探针脚本，才从缓存读取数据
    if server_conf.get('probe_installed', False) or raw_url in PROBE_DATA_CACHE:
        cache = PROBE_DATA_CACHE.get(raw_url)
        if cache:
            # 检查数据新鲜度 (15秒超时)
            if time.time() - cache.get('last_updated', 0) < 15:
                return cache 
            else:
                return {'status': 'offline', 'msg': '探针离线 (超时)'}
    
    # 🛑 对于 X-UI 面板账号，直接返回离线，不尝试登录
    return {'status': 'offline', 'msg': '未安装探针'}