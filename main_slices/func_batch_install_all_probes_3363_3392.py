async def batch_install_all_probes():
    if not SERVERS_CACHE:
        safe_notify("没有服务器可安装", "warning")
        return

    safe_notify(f"正在后台为 {len(SERVERS_CACHE)} 台服务器安装/更新探针...", "ongoing")
    
    # ✨ 限制并发数：同时只允许 10 台服务器进行 SSH 连接，防止卡死
    sema = asyncio.Semaphore(10)

    async def _worker(server_conf):
        name = server_conf.get('name', 'Unknown')
        async with sema:
            # 1. 打印开始日志
            logger.info(f"🚀 [AutoInstall] {name} 开始安装...")
            
            # 2. 执行安装 (复用已有的单台安装函数)
            success = await install_probe_on_server(server_conf)
            
            # 3. 这里的日志会在 install_probe_on_server 内部打印，或者我们可以补充
            # (原函数 install_probe_on_server 内部已经有成功/失败的日志了)

    # 创建任务列表
    tasks = [_worker(s) for s in SERVERS_CACHE]
    
    # 并发执行
    if tasks:
        await asyncio.gather(*tasks)
    
    safe_notify("✅ 所有探针安装/更新任务已完成", "positive")