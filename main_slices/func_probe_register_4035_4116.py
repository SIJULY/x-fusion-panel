async def probe_register(request: Request):
    try:
        data = await request.json()
        
        # 1. 安全校验
        submitted_token = data.get('token')
        correct_token = ADMIN_CONFIG.get('probe_token')
        
        if not submitted_token or submitted_token != correct_token:
            return Response(json.dumps({"success": False, "msg": "Token 错误"}), status_code=403)

        # 2. 获取客户端真实 IP
        client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
        
        # 3. ✨✨✨ 智能查重逻辑 (核心修改) ✨✨✨
        target_server = None
        
        # 策略 A: 直接字符串匹配 (命中纯 IP 注册的情况)
        for s in SERVERS_CACHE:
            if client_ip in s['url']:
                target_server = s
                break
        
        # 策略 B: 如果没找到，尝试 DNS 反向解析 (命中域名注册的情况)
        if not target_server:
            logger.info(f"🔍 [探针注册] IP {client_ip} 未直接匹配，尝试解析现有域名...")
            for s in SERVERS_CACHE:
                try:
                    # 提取缓存中的 Host (可能是域名)
                    cached_host = s['url'].split('://')[-1].split(':')[0]
                    
                    # 跳过已经是 IP 的
                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", cached_host): continue
                    
                    # 解析域名为 IP (使用 run.io_bound 防止阻塞)
                    resolved_ip = await run.io_bound(socket.gethostbyname, cached_host)
                    
                    if resolved_ip == client_ip:
                        target_server = s
                        logger.info(f"✅ [探针注册] 域名 {cached_host} 解析为 {client_ip}，匹配成功！")
                        break
                except: pass

        # 4. 逻辑分支
        if target_server:
            # === 情况 1: 已存在，仅激活探针 ===
            if not target_server.get('probe_installed'):
                target_server['probe_installed'] = True
                await save_servers() # 保存状态
                await refresh_dashboard_ui() # 刷新UI
            
            return Response(json.dumps({"success": True, "msg": "已合并现有服务器"}), status_code=200)

        else:
            # === 情况 2: 完全陌生的机器，新建 ===
            # (之前的创建逻辑保持不变)
            new_server = {
                'name': f"🏳️ {client_ip}", 
                'group': '自动注册',
                'url': f"http://{client_ip}:54321",
                'user': 'admin',
                'pass': 'admin',
                'ssh_auth_type': '全局密钥',
                'probe_installed': True,
                '_status': 'online'
            }
            SERVERS_CACHE.append(new_server)
            await save_servers()
            
            # 触发强制重命名
            asyncio.create_task(force_geoip_naming_task(new_server))
            
            await refresh_dashboard_ui()
            try: render_sidebar_content.refresh()
            except: pass
            
            logger.info(f"✨ [主动注册] 新服务器上线: {client_ip}")
            return Response(json.dumps({"success": True, "msg": "注册成功"}), status_code=200)

    except Exception as e:
        logger.error(f"❌ 注册接口异常: {e}")
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500)