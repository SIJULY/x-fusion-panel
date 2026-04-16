def generate_detail_config(node, server_host):
    try:
        # 1. 基础信息清洗
        clean_host = server_host.replace('http://', '').replace('https://', '')
        if ':' in clean_host and not clean_host.startswith('['):
            clean_host = clean_host.split(':')[0]

        remark = node.get('remark', 'Unnamed').replace(',', '_').replace('=', '_').strip()
        address = node.get('listen') or clean_host
        port = node['port'] # 数据库里存的是 "20000-50000"
        
        # === A. 自定义节点 (Snell / Hy2 / XHTTP) ===
        if node.get('_is_custom'):
            raw_link = node.get('_raw_link', '')
            
            # [新增] Snell 解析逻辑
            if raw_link.startswith('snell://'):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(raw_link)
                psk = parsed.username
                s_host = parsed.hostname or address 
                s_port = parsed.port or port
                
                params = parse_qs(parsed.query)
                version = params.get('version', ['4'])[0]
                
                return f"{remark} = snell, {s_host}, {s_port}, psk={psk}, version={version}, tfo=true, reuse=true"

            # [原有] Hy2 解析逻辑
            elif raw_link.startswith('hy2://'):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(raw_link)
                password = parsed.username
                h_host = parsed.hostname or address 
                
                # ✨✨✨ 修复核心：优先使用范围字符串 ✨✨✨
                if str(port) and '-' in str(port):
                    h_port = port
                else:
                    h_port = parsed.port or port
                
                params = parse_qs(parsed.query)
                sni = params.get('sni', [''])[0] or params.get('peer', [''])[0]
                
                line = f"{remark} = hysteria2, {h_host}, {h_port}, password={password}"
                if sni: line += f", sni={sni}"
                line += ", skip-cert-verify=true, download-bandwidth=500, udp-relay=true"
                return line
                
            elif raw_link.startswith('vless://'):
                 return f"// Surge 暂未原生支持 XHTTP: {remark}"

        # === B. 面板标准节点 (VMess / Trojan) ===
        protocol = node['protocol']
        settings = json.loads(node['settings']) if isinstance(node['settings'], str) else node['settings']
        stream = json.loads(node['streamSettings']) if isinstance(node['streamSettings'], str) else node['streamSettings']
        net = stream.get('network', 'tcp')
        security = stream.get('security', 'none')
        tls = (security == 'tls') or (security == 'reality')
        
        if protocol == 'vmess':
            uuid = settings['clients'][0]['id']
            line = f"{remark} = vmess, {address}, {port}, username={uuid}"
            if net == 'ws':
                ws_set = stream.get('wsSettings', {})
                path = ws_set.get('path', '/')
                panel_host = ws_set.get('headers', {}).get('Host', '')
                sni = ""
                if tls:
                    tls_set = stream.get('tlsSettings', {})
                    sni = tls_set.get('serverName', '')
                line += f", ws=true, ws-path={path}"
                if tls and sni: pass 
                elif panel_host: line += f", ws-headers=Host:{panel_host}"
            if tls:
                line += ", tls=true"
                tls_set = stream.get('tlsSettings', {})
                sni = tls_set.get('serverName', '')
                if sni: line += f", sni={sni}"
                line += ", skip-cert-verify=true"
            line += ", tfo=true, udp-relay=true"
            return line

        elif protocol == 'trojan':
            password = settings['clients'][0]['password']
            line = f"{remark} = trojan, {address}, {port}, password={password}"
            if tls:
                line += ", tls=true"
                sni = stream.get('tlsSettings', {}).get('serverName', '')
                if sni: line += f", sni={sni}"
                line += ", skip-cert-verify=true"
            line += ", tfo=true, udp-relay=true"
            return line

    except Exception as e:
        return f"// Config Error: {str(e)}"
    
    return ""