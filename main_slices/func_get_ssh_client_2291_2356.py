def get_ssh_client(server_data):
    """建立 SSH 连接"""
    import paramiko # 确保导入
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # 解析 IP
    raw_url = server_data['url']
    if '://' in raw_url: host = raw_url.split('://')[-1].split(':')[0]
    else: host = raw_url.split(':')[0]
    
    # 优先使用 ssh_host
    if server_data.get('ssh_host'): host = server_data['ssh_host']
    
    port = int(server_data.get('ssh_port') or 22)
    user = server_data.get('ssh_user') or 'root'
    
    # 获取认证类型
    auth_type = server_data.get('ssh_auth_type', '全局密钥').strip()
    
    print(f"🔌 [SSH Debug] 连接目标: {host}, 用户: {user}, 认证方式: [{auth_type}]", flush=True)
    
    try:
        if auth_type == '独立密码':
            pwd = server_data.get('ssh_password', '')
            if not pwd: raise Exception("选择了独立密码，但密码为空")
            
            # ✨ 强制只用密码，不找密钥，不找Agent
            client.connect(host, port, username=user, password=pwd, timeout=5, 
                           look_for_keys=False, allow_agent=False)
                           
        elif auth_type == '独立密钥':
            key_content = server_data.get('ssh_key', '')
            if not key_content: raise Exception("选择了独立密钥，但密钥为空")
            
            key_file = io.StringIO(key_content)
            try: pkey = paramiko.RSAKey.from_private_key(key_file)
            except: 
                key_file.seek(0)
                try: pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except: raise Exception("无法识别的私钥格式")
            
            # ✨✨✨ [此处已修改] 同样强制禁止 Agent 和本地其他密钥 ✨✨✨
            client.connect(host, port, username=user, pkey=pkey, timeout=5,
                           look_for_keys=False, allow_agent=False)
            
        else: # 默认：全局密钥
            g_key = load_global_key()
            if not g_key: raise Exception("全局密钥未配置")
            
            key_file = io.StringIO(g_key)
            try: pkey = paramiko.RSAKey.from_private_key(key_file)
            except: 
                key_file.seek(0)
                try: pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except: raise Exception("全局密钥格式无法识别")
            
            # 全局密钥也加上限制，防止它私自去读你电脑本身的 id_rsa
            client.connect(host, port, username=user, pkey=pkey, timeout=5,
                           look_for_keys=False, allow_agent=False)
            
        return client, f"✅ 已连接 {user}@{host}"
        
    except Exception as e:
        return None, f"❌ 连接失败: {str(e)}"