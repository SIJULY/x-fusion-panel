def check_auth(request: Request):
    """
    检查用户是否已登录，且会话版本是否有效
    """
    # 1. 基础认证：检查 Cookie 里有没有 authenticated 标记
    if not app.storage.user.get('authenticated', False):
        return False
    
    # 2. 全局会话版本校验 (实现一键踢人核心逻辑)
    # 获取当前系统要求的全局版本号 (如 v1)
    current_global_ver = ADMIN_CONFIG.get('session_version', 'init')
    # 获取用户 Cookie 里的版本号
    user_ver = app.storage.user.get('session_version', '')
    
    # 如果版本不匹配 (比如管理员刚刚重置了密钥)，视为未登录
    if current_global_ver != user_ver:
        return False
        
    return True