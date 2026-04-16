async def probe_push_data(request: Request):
    try:
        data = await request.json()
        token = data.get('token')
        server_url = data.get('server_url')
        
        # 1. 校验 Token
        correct_token = ADMIN_CONFIG.get('probe_token')
        if not token or token != correct_token:
            return Response("Invalid Token", 403)
            
        # 2. 查找服务器 (精准匹配 -> IP匹配)
        target_server = next((s for s in SERVERS_CACHE if s['url'] == server_url), None)
        if not target_server:
            try:
                push_ip = server_url.split('://')[-1].split(':')[0]
                for s in SERVERS_CACHE:
                    cache_ip = s['url'].split('://')[-1].split(':')[0]
                    if cache_ip == push_ip:
                        target_server = s
                        break
            except: pass

        if target_server:
            # 激活探针状态
            if not target_server.get('probe_installed'):
                 target_server['probe_installed'] = True
            
            # 3. 写入基础监控数据缓存
            data['status'] = 'online'
            data['last_updated'] = time.time()
            PROBE_DATA_CACHE[target_server['url']] = data
            
            # ✨✨✨ 核心逻辑：处理 X-UI 数据 & 自动命名 ✨✨✨
            if 'xui_data' in data and isinstance(data['xui_data'], list):
                # 解析节点
                raw_nodes = data['xui_data']
                parsed_nodes = []
                for n in raw_nodes:
                    try:
                        if isinstance(n.get('settings'), str): 
                            n['settings'] = json.loads(n['settings'])
                        if isinstance(n.get('streamSettings'), str): 
                            n['streamSettings'] = json.loads(n['streamSettings'])
                        parsed_nodes.append(n)
                    except: 
                        parsed_nodes.append(n)
                
                # 更新节点缓存
                NODES_DATA[target_server['url']] = parsed_nodes
                target_server['_status'] = 'online'

                # 🟢自动同步名称逻辑 (当端口不通时依赖此逻辑)
                # 只有当有节点，且当前名字看起来像默认IP时，才尝试修改
                if parsed_nodes:
                    first_remark = parsed_nodes[0].get('remark', '').strip()
                    current_name = target_server.get('name', '').strip()
                    
                    # 简单的判断：如果名字里没有这个备注
                    if first_remark and (first_remark not in current_name):
                        
                        # ：先检查备注里是否自带了国旗 
                        has_own_flag = False
                        # 遍历全局配置中的所有已知国旗
                        for v in AUTO_COUNTRY_MAP.values():
                            known_flag = v.split(' ')[0] # 提取 "🇺🇸"
                            if known_flag in first_remark:
                                has_own_flag = True
                                break
                        
                        if has_own_flag:
                            # 情况 A：备注自带国旗 (如 "Oracle|🇺🇸凤凰城") -> 直接用，不加前缀
                            new_name_candidate = first_remark
                        else:
                            # 情况 B：备注没国旗 -> 尝试继承旧国旗或查询 GeoIP 加上
                            flag = "🏳️"
                            # 1. 尝试沿用当前名字里的国旗
                            if ' ' in current_name:
                                parts = current_name.split(' ', 1)
                                if len(parts[0]) < 10: 
                                    flag = parts[0]
                            else:
                                # 2. 尝试重新获取国旗 (GeoIP)
                                try:
                                    ip_key = target_server['url'].split('://')[-1].split(':')[0]
                                    geo_info = IP_GEO_CACHE.get(ip_key)
                                    if geo_info: 
                                        flag = get_flag_for_country(geo_info[2]).split(' ')[0]
                                except: pass

                            new_name_candidate = f"{flag} {first_remark}"
                        
                        # 执行改名并保存
                        if target_server['name'] != new_name_candidate:
                            target_server['name'] = new_name_candidate
                            asyncio.create_task(save_servers())
                            logger.info(f"🏷️ [探针同步] 根据节点备注自动改名: {new_name_candidate}")

            # 记录历史
            record_ping_history(target_server['url'], data.get('pings', {}))
            
        return Response("OK", 200)
    except Exception as e:
        return Response("Error", 500)