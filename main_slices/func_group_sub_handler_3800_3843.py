async def group_sub_handler(group_b64: str, request: Request):
    group_name = decode_base64_safe(group_b64)
    if not group_name: return Response("Invalid Group Name", 400)
    
    links = []
    
    # 筛选符合分组的服务器
    target_servers = [
        s for s in SERVERS_CACHE 
        if s.get('group', '默认分组') == group_name or group_name in s.get('tags', [])
    ]
    
    logger.info(f"正在生成分组订阅: [{group_name}]，匹配到 {len(target_servers)} 个服务器")

    for srv in target_servers:
        # 1. 获取面板节点
        panel_nodes = NODES_DATA.get(srv['url'], []) or []
        # 2. 获取自定义节点
        custom_nodes = srv.get('custom_nodes', []) or []
        # === 合并 ===
        all_nodes = panel_nodes + custom_nodes
        
        if not all_nodes: continue
        
        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url); host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except: host = raw_url
        
        for n in all_nodes:
            if n.get('enable'): 
                # A. 优先使用原始链接
                if n.get('_raw_link'):
                    links.append(n['_raw_link'])
                # B. 生成面板节点链接
                else:
                    l = generate_node_link(n, host)
                    if l: links.append(l)
    
    if not links:
        return Response(f"// Group [{group_name}] is empty or not found", media_type="text/plain; charset=utf-8")
        
    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")