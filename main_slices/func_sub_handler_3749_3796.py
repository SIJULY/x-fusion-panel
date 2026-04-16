async def sub_handler(token: str, request: Request):
    sub = next((s for s in SUBS_CACHE if s['token'] == token), None)
    if not sub: return Response("Invalid Token", 404)
    
    links = []
    
    # 1. 构建快速查找字典 (Map)
    # 格式: { 'url|id': (node_data, server_host) }
    node_lookup = {}
    
    for srv in SERVERS_CACHE:
        # 获取 Host
        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url)
            host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except: host = raw_url

        # 收集面板节点
        panel_nodes = NODES_DATA.get(srv['url'], []) or []
        for n in panel_nodes:
            key = f"{srv['url']}|{n['id']}"
            node_lookup[key] = (n, host)
            
        # 收集自定义节点
        custom_nodes = srv.get('custom_nodes', []) or []
        for n in custom_nodes:
            key = f"{srv['url']}|{n['id']}"
            node_lookup[key] = (n, host)

    # 2. 按照订阅中保存的顺序生成链接
    # sub['nodes'] 是你在管理面板里排好序的 ID 列表
    ordered_ids = sub.get('nodes', [])
    
    for key in ordered_ids:
        if key in node_lookup:
            node, host = node_lookup[key]
            
            # A. 优先使用原始链接
            if node.get('_raw_link'):
                links.append(node['_raw_link'])
            # B. 生成标准链接
            else:
                l = generate_node_link(node, host)
                if l: links.append(l)
                    
    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")