def get_manager(server_conf):
    # --- 优先级 1：SSH / Root 探针模式 (上帝模式) ---
    # 只要检测到安装了探针且配置了 SSH Host，无论有没有 API 账号，都优先走 SSH 通道。
    # 优点：操作数据库更稳，无需担心 API 端口被封或 API 特征检测。
    if server_conf.get('probe_installed') and server_conf.get('ssh_host'):
        url = server_conf.get('url')
        # 使用特殊前缀作为 key 缓存 SSH 管理器实例
        mgr_key = f"ssh_{url}"
        if mgr_key not in managers:
            managers[mgr_key] = SSHXUIManager(server_conf)
        return managers[mgr_key]

    # --- 优先级 2：标准 API 模式 ---
    # 只有当 SSH 不可用（未配置探针/SSH）时，才尝试使用 API 账号登录。
    url = server_conf.get('url')
    if url and server_conf.get('user') and server_conf.get('pass'):
        if url not in managers:
            managers[url] = XUIManager(url, server_conf['user'], server_conf['pass'], server_conf.get('prefix'))
        return managers[url]
    
    # --- 兜底 ---
    raise Exception("无法创建管理器：未配置 SSH 且缺少 X-UI 账号信息")