async def short_group_handler(target: str, group_b64: str, request: Request):
    try:
        group_name = decode_base64_safe(group_b64)
        if not group_name: return Response("Invalid Group Name", 400)

        # -------------------------------------------------------------
        # 策略 A: 针对 Surge / Loon -> 使用 Python 原生生成 (解决 Hy2 无法转换 + VMess 格式问题)
        # -------------------------------------------------------------
        if target == 'surge':
            links = []
            
            # 1. 筛选服务器
            target_servers = [
                s for s in SERVERS_CACHE 
                if s.get('group', '默认分组') == group_name or group_name in s.get('tags', [])
            ]
            
            # 2. 遍历服务器生成配置
            for srv in target_servers:
                panel_nodes = NODES_DATA.get(srv['url'], []) or []
                custom_nodes = srv.get('custom_nodes', []) or []
                
                # 获取干净的 Host
                raw_url = srv['url']
                try:
                    if '://' not in raw_url: raw_url = f'http://{raw_url}'
                    parsed = urlparse(raw_url)
                    host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
                except: host = raw_url

                # 合并处理面板节点和自定义节点
                for n in (panel_nodes + custom_nodes):
                    if n.get('enable'):
                        # 调用我们修复后的 generate_detail_config
                        line = generate_detail_config(n, host)
                        if line and not line.startswith('//') and not line.startswith('None'):
                            links.append(line)
            
            if not links:
                return Response(f"// Group [{group_name}] is empty", media_type="text/plain; charset=utf-8")
                
            return Response("\n".join(links), media_type="text/plain; charset=utf-8")

        # -------------------------------------------------------------
        # 策略 B: 针对 Clash / 其他 -> 继续使用 SubConverter
        # (注意：SubConverter 可能依然无法解析 Hy2，但能正常解析 VMess)
        # -------------------------------------------------------------
        custom_base = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
        if custom_base: 
            base_url = custom_base
        else:
            host = request.headers.get('host')
            scheme = request.url.scheme
            base_url = f"{scheme}://{host}"

        internal_api = f"{base_url}/sub/group/{group_b64}"
        
        # 关键参数：scv=true (跳过证书验证), udp=true
        params = { 
            "target": target, 
            "url": internal_api, 
            "insert": "false", 
            "list": "true", 
            "ver": "4", 
            "udp": "true", 
            "scv": "true" 
        }
        
        converter_api = "http://subconverter:25500/sub"

        def _fetch_sync():
            try: return requests.get(converter_api, params=params, timeout=10)
            except: return None

        response = await run.io_bound(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            return Response(f"SubConverter Error (Code: {getattr(response, 'status_code', 'Unk')})", status_code=502)

    except Exception as e: return Response(f"Error: {str(e)}", status_code=500)