async def short_sub_handler(target: str, token: str, request: Request):
    try:
        sub_obj = next((s for s in SUBS_CACHE if s['token'] == token), None)
        if not sub_obj: return Response("Subscription Not Found", 404)
        
        # -------------------------------------------------------------
        # 策略 A: 针对 Surge -> Python 原生生成 (严格顺序版)
        # -------------------------------------------------------------
        if target == 'surge':
            links = []
            
            # 1. 构建查找字典
            node_lookup = {}
            for srv in SERVERS_CACHE:
                # 解析 Host
                raw_url = srv['url']
                try:
                    if '://' not in raw_url: raw_url = f'http://{raw_url}'
                    parsed = urlparse(raw_url)
                    host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
                except: host = raw_url
                
                # 收集所有节点
                all_nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
                for n in all_nodes:
                    key = f"{srv['url']}|{n['id']}"
                    node_lookup[key] = (n, host)

            # 2. 按顺序生成配置
            ordered_ids = sub_obj.get('nodes', [])
            
            for key in ordered_ids:
                if key in node_lookup:
                    node, host = node_lookup[key]
                    # 生成 Surge 配置行
                    line = generate_detail_config(node, host)
                    if line and not line.startswith('//') and not line.startswith('None'):
                        links.append(line)
                            
            return Response("\n".join(links), media_type="text/plain; charset=utf-8")

        # -------------------------------------------------------------
        # 策略 B: Clash / 其他 -> SubConverter
        # -------------------------------------------------------------
        # SubConverter 会读取上一步 sub_handler 生成的原始订阅
        # 只要 sub_handler 是有序的，SubConverter 输出也就是有序的
        
        custom_base = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
        if custom_base: 
            base_url = custom_base
        else:
            host = request.headers.get('host')
            scheme = request.url.scheme
            base_url = f"{scheme}://{host}"
            
        internal_api = f"{base_url}/sub/{token}"
        opt = sub_obj.get('options', {})
        
        params = {
            "target": target, "url": internal_api, 
            "insert": "false", "list": "true", "ver": "4",
            "emoji": str(opt.get('emoji', True)).lower(), 
            "udp": str(opt.get('udp', True)).lower(),
            "tfo": str(opt.get('tfo', False)).lower(), 
            "scv": str(opt.get('skip_cert', True)).lower(),
            "fdn": "false", # 强制不过滤域名
            "sort": "false", # ✨✨✨ 关键：告诉 SubConverter 不要再次排序，保持原样
        }
        
        # 处理正则过滤 (保持原样)
        regions = opt.get('regions', [])
        includes = []
        if opt.get('include_regex'): includes.append(opt['include_regex'])
        if regions:
            region_keywords = []
            for r in regions:
                parts = r.split(' '); k = parts[1] if len(parts)>1 else r
                region_keywords.append(k)
                for c, v in AUTO_COUNTRY_MAP.items(): 
                    if v == r and len(c) == 2: region_keywords.append(c)
            if region_keywords: includes.append(f"({'|'.join(region_keywords)})")
        
        if includes: params['include'] = "|".join(includes)
        if opt.get('exclude_regex'): params['exclude'] = opt['exclude_regex']
        
        ren_pat = opt.get('rename_pattern', '')
        if ren_pat: params['rename'] = f"{ren_pat}@{opt.get('rename_replacement', '')}"

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