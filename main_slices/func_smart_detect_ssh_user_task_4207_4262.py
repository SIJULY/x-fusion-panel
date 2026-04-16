async def smart_detect_ssh_user_task(server_conf):
    """
    后台任务：尝试使用不同的用户名 (ubuntu -> root) 连接 SSH。
    连接成功后：
    1. 更新配置并保存。
    2. 自动触发探针安装。
    """
    # 待测试的用户名列表 (优先尝试 ubuntu，失败则尝试 root)
    # 你可以在这里添加更多，比如 'ec2-user', 'debian', 'opc'
    candidates = ['root', 'ubuntu']
    
    ip = server_conf['url'].split('://')[-1].split(':')[0]
    original_user = server_conf.get('ssh_user', '')
    
    logger.info(f"🕵️‍♂️ [智能探测] 开始探测 {server_conf['name']} ({ip}) 的 SSH 用户名...")

    found_user = None

    for user in candidates:
        # 1. 临时修改配置中的用户名
        server_conf['ssh_user'] = user
        
        # 2. 尝试连接 (复用现有的连接函数，自带全局密钥逻辑)
        # 注意：get_ssh_client_sync 内部有 5秒 超时，适合做探测
        client, msg = await run.io_bound(get_ssh_client_sync, server_conf)
        
        if client:
            # ✅ 连接成功！
            client.close()
            found_user = user
            logger.info(f"✅ [智能探测] 成功匹配用户名: {user}")
            break
        else:
            logger.warning(f"⚠️ [智能探测] 用户名 '{user}' 连接失败，尝试下一个...")

    # 3. 处理探测结果
    if found_user:
        # 保存正确的用户名
        server_conf['ssh_user'] = found_user
        # 标记探测成功，防止后续逻辑误判
        server_conf['_ssh_verified'] = True 
        await save_servers()
        
        # 🎉 探测成功后，立即触发探针安装 (如果开启了探针功能)
        if ADMIN_CONFIG.get('probe_enabled', False):
            logger.info(f"🚀 [自动部署] SSH 验证通过，开始安装探针...")
            # 稍作延迟，等待 SSH 服务稳定
            await asyncio.sleep(2) 
            await install_probe_on_server(server_conf)
            
    else:
        # ❌ 全部失败，恢复回默认 (或者保留最后一个尝试失败的)
        logger.error(f"❌ [智能探测] {server_conf['name']} 所有用户名均尝试失败 (请检查安全组或密钥)")
        # 可选：恢复为 root 或者保持原状
        if original_user: server_conf['ssh_user'] = original_user
        await save_servers()