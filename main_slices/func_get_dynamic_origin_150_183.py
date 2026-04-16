def get_dynamic_origin():
    """
    智能侦测当前面板的真实访问地址（适配开源分发）。
    侦测优先级：
    1. 用户在后台手动设置的 `manager_base_url`
    2. Cloudflare / Nginx 传递的真实协议和域名 (X-Forwarded-Proto / Host)
    3. 默认的 Request Host
    """
    # 1. 优先读取数据库里的配置（如果有）
    saved_url = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
    if saved_url and not ('127.0.0.1' in saved_url or 'localhost' in saved_url):
        # 顺便排除一下旧的硬编码域名，防止残留
        if 'sijuly.nyc.mn' not in saved_url:
            return saved_url

    # 2. 从当前 HTTP 请求中动态提取 (穿透反代)
    try:
        from nicegui import ui
        # 获取当前客户端的原始请求
        req = ui.context.client.request
        
        # 尝试获取经过 Nginx/CF 转发的真实域名和协议
        real_host = req.headers.get('X-Forwarded-Host') or req.headers.get('host')
        real_proto = req.headers.get('X-Forwarded-Proto') or req.url.scheme
        
        if real_host:
            # 自动构建当前网址
            detected_url = f"{real_proto}://{real_host}"
            return detected_url
    except Exception:
        pass

    # 3. 终极兜底（提示性占位，绝对不使用特定域名）
    return "http://{YOUR-DOMAIN-OR-IP}"