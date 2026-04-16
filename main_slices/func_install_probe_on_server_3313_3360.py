async def install_probe_on_server(server_conf):
    name = server_conf.get('name', 'Unknown')
    auth_type = server_conf.get('ssh_auth_type', '全局密钥')
    if auth_type == '独立密码' and not server_conf.get('ssh_password'): return False
    if auth_type == '独立密钥' and not server_conf.get('ssh_key'): return False
    
    my_token = ADMIN_CONFIG.get('probe_token', 'default_token')
    
    # 1. 获取主控端地址
    manager_url = ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080') 
    
    # 2. 获取自定义测速点 (如果没有设置，使用默认值)
    ping_ct = ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68') # 电信
    ping_cu = ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')  # 联通
    ping_cm = ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')  # 移动

    # 3. 替换脚本中的变量
    real_script = PROBE_INSTALL_SCRIPT \
        .replace("__MANAGER_URL__", manager_url) \
        .replace("__TOKEN__", my_token) \
        .replace("__SERVER_URL__", server_conf['url']) \
        .replace("__PING_CT__", ping_ct) \
        .replace("__PING_CU__", ping_cu) \
        .replace("__PING_CM__", ping_cm)

    # 4. 执行安装 (保持原有 Paramiko 逻辑)
    def _do_install():
        client = None
        try:
            client, msg = get_ssh_client_sync(server_conf)
            if not client: return False, f"SSH连接失败: {msg}"
            stdin, stdout, stderr = client.exec_command(real_script, timeout=60)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status == 0: return True, "Agent 安装成功并启动"
            return False, f"安装脚本错误 (Exit {exit_status})"
        except Exception as e:
            return False, f"异常: {str(e)}"
        finally:
            if client: client.close()

    success, msg = await run.io_bound(_do_install)
    if success:
        server_conf['probe_installed'] = True
        await save_servers()
        logger.info(f"✅ [Push Agent] {name} 部署成功")
    else:
        logger.warning(f"⚠️ [Push Agent] {name} 部署失败: {msg}")
    return success