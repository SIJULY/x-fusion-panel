def calculate_dashboard_data():
    """
    计算并返回当前所有面板数据。
    逻辑调整：优先使用 Root 探针的流量和状态，没有探针才使用 X-UI 数据。
    """
    try:
        total_servers = len(SERVERS_CACHE)
        online_servers = 0
        total_nodes = 0
        total_traffic_bytes = 0
        
        server_traffic_map = {}
        from collections import Counter
        country_counter = Counter()
        
        import time
        now_ts = time.time()

        for s in SERVERS_CACHE:
            # 1. 获取基础数据
            res = NODES_DATA.get(s['url'], []) or []     # X-UI 节点数据
            custom = s.get('custom_nodes', []) or []     # 自定义节点
            probe_data = PROBE_DATA_CACHE.get(s['url'])  # 探针数据
            
            name = s.get('name', '未命名')
            
            # --- 统计区域 ---
            try:
                region_str = detect_country_group(name, s)
                if not region_str or region_str.strip() == "🏳️": region_str = "🏳️ 未知区域"
            except: region_str = "🏳️ 未知区域"
            country_counter[region_str] += 1

            # --- A. 计算流量 (优先探针) ---
            srv_traffic = 0
            use_probe_traffic = False
            
            if s.get('probe_installed') and probe_data:
                # 优先：读取网卡总流量 (入站+出站)
                # 注意：这里假设探针返回的是累积总量
                t_in = probe_data.get('net_total_in', 0)
                t_out = probe_data.get('net_total_out', 0)
                if t_in > 0 or t_out > 0:
                    srv_traffic = t_in + t_out
                    use_probe_traffic = True
            
            # 兜底：如果没有探针数据，则累加 X-UI 节点流量
            if not use_probe_traffic and res:
                for n in res:
                    srv_traffic += int(n.get('up', 0)) + int(n.get('down', 0))

            total_traffic_bytes += srv_traffic
            server_traffic_map[name] = srv_traffic

            # --- B. 判断在线状态 (优先探针心跳) ---
            is_online = False
            
            # 1. 探针判定 (心跳在 60秒内算在线)
            if s.get('probe_installed') and probe_data:
                if now_ts - probe_data.get('last_updated', 0) < 60:
                    is_online = True
            
            # 2. X-UI 判定 (如果探针没在线，看下 X-UI API 是否通了)
            if not is_online:
                # 如果缓存里有节点数据，或者状态标记为 online (由 fetch_inbounds_safe 设置)
                if res or s.get('_status') == 'online':
                    is_online = True
            
            if is_online:
                online_servers += 1

            # --- C. 统计节点数 (这个始终来自配置) ---
            if res: total_nodes += len(res)
            if custom: total_nodes += len(custom)

        # 构建图表数据
        sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:15]
        bar_names = [x[0] for x in sorted_traffic]
        bar_values = [round(x[1]/(1024**3), 2) for x in sorted_traffic]

        chart_data = []
        sorted_regions = country_counter.most_common()
        
        # 饼图逻辑 (Top 5 + 其他)
        if len(sorted_regions) > 5:
            top_5 = sorted_regions[:5]
            others_count = sum(item[1] for item in sorted_regions[5:])
            for k, v in top_5: chart_data.append({'name': f"{k} ({v})", 'value': v})
            if others_count > 0: chart_data.append({'name': f"🏳️ 其他 ({others_count})", 'value': others_count})
        else:
            for k, v in sorted_regions: chart_data.append({'name': f"{k} ({v})", 'value': v})

        if not chart_data: chart_data = [{'name': '暂无数据', 'value': 0}]

        return {
            "servers": f"{online_servers}/{total_servers}",
            "nodes": str(total_nodes),
            "traffic": f"{total_traffic_bytes/(1024**3):.2f} GB",
            "subs": str(len(SUBS_CACHE)),
            "bar_chart": {"names": bar_names, "values": bar_values},
            "pie_chart": chart_data
        }
    except Exception as e:
        print(f"Error calculating dashboard data: {e}")
        import traceback; traceback.print_exc()
        return None