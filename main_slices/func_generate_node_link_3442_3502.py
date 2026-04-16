def generate_node_link(node, server_host):
    try:
        # 清洗 server_host，只保留纯 IP/域名 
        clean_host = server_host
        # 1. 去掉协议头 (http:// 或 https://)
        if '://' in clean_host:
            clean_host = clean_host.split('://')[-1]
        # 2. 去掉端口 (例如 :54321)
        # 注意：排除 IPv6 ([...]) 的情况，这里简单处理 IPv4 和域名
        if ':' in clean_host and not clean_host.startswith('['):
            clean_host = clean_host.split(':')[0]

        p = node['protocol']; remark = node['remark']; port = node['port']
        # 使用清洗后的 clean_host 作为默认地址
        add = node.get('listen') or clean_host
        
        s = json.loads(node['settings']) if isinstance(node['settings'], str) else node['settings']
        st = json.loads(node['streamSettings']) if isinstance(node['streamSettings'], str) else node['streamSettings']
        net = st.get('network', 'tcp'); tls = st.get('security', 'none'); path = ""; host = ""
        
        if net == 'ws': 
            path = st.get('wsSettings',{}).get('path','/')
            host = st.get('wsSettings',{}).get('headers',{}).get('Host','')
        elif net == 'grpc': 
            path = st.get('grpcSettings',{}).get('serviceName','')
        
        if p == 'vmess':
            # 构建标准的 v2 VMess json
            v = {
                "v": "2",
                "ps": remark,
                "add": add,      # 这里现在是纯 IP 了
                "port": port,    # 这里的端口才是节点端口 (如 14789)
                "id": s['clients'][0]['id'],
                "aid": "0",
                "scy": "auto",
                "net": net,
                "type": "none",
                "host": host,
                "path": path,
                "tls": tls
            }
            return "vmess://" + safe_base64(json.dumps(v))
            
        elif p == 'vless':
            params = f"type={net}&security={tls}"
            if path: params += f"&path={path}" if net != 'grpc' else f"&serviceName={path}"
            if host: params += f"&host={host}"
            return f"vless://{s['clients'][0]['id']}@{add}:{port}?{params}#{remark}"
            
        elif p == 'trojan': 
            return f"trojan://{s['clients'][0]['password']}@{add}:{port}?type={net}&security={tls}#{remark}"
            
        elif p == 'shadowsocks': 
            cred = f"{s['method']}:{s['password']}"
            return f"ss://{safe_base64(cred)}@{add}:{port}#{remark}"
            
    except Exception as e: 
        # print(f"Generate Link Error: {e}")
        return ""
    return ""