def parse_vless_link_to_node(link, remark_override=None):
    """将 vless:// 链接解析为面板节点格式的字典"""
    try:
        if not link.startswith("vless://"): return None
        
        # 局部引入依赖，防止报错
        import urllib.parse
        
        # 1. 基础解析：移除协议头
        main_part = link.replace("vless://", "")
        
        # 处理 fragment (#备注)
        remark = "XHTTP-Reality"
        if "#" in main_part:
            main_part, remark = main_part.split("#", 1)
            remark = urllib.parse.unquote(remark)
        
        # 如果传入了强制备注（用户输入的），覆盖原备注
        if remark_override: 
            remark = remark_override

        # 处理 query parameters (?)
        params = {}
        if "?" in main_part:
            main_part, query_str = main_part.split("?", 1)
            params = dict(urllib.parse.parse_qsl(query_str))
        
        # 处理 user@host:port
        if "@" in main_part:
            user_info, host_port = main_part.split("@", 1)
            uuid = user_info
        else:
            return None # 格式不正确

        if ":" in host_port:
            # 使用 rsplit 确保正确处理 host:port
            host, port = host_port.rsplit(":", 1)
        else:
            host = host_port
            port = 443

        # ================= 更新原始链接中的备注 =================
        final_link = link
        if remark_override:
            # 1. 如果原链接里有 #，先去掉旧的
            if "#" in final_link:
                final_link = final_link.split("#")[0]
            # 2. 拼接新的备注 (进行 URL 编码)
            final_link = f"{final_link}#{urllib.parse.quote(remark)}"
        # ==========================================================

        # 2. 构建符合 Panel 格式的 Node 字典
        node = {
            "id": uuid, 
            "remark": remark,
            "port": int(port),
            "protocol": "vless",
            "settings": {
                "clients": [{"id": uuid, "flow": params.get("flow", "")}],
                "decryption": "none"
            },
            "streamSettings": {
                "network": params.get("type", "tcp"),
                "security": params.get("security", "none"),
                "xhttpSettings": {
                    "path": params.get("path", ""),
                    "mode": params.get("mode", "auto"),
                    "host": params.get("host", "")
                },
                "realitySettings": {
                    "serverName": params.get("sni", ""),
                    "shortId": params.get("sid", ""), 
                    "publicKey": params.get("pbk", "") 
                }
            },
            "enable": True,
            "_is_custom": True, 
            "_raw_link": final_link  # 使用更新后的链接
        }
        return node

    except Exception as e:
        # 必须要有 except 块来捕获潜在错误
        print(f"[Error] 解析 VLESS 链接失败: {e}")
        return None