async def load_dashboard_stats():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'DASHBOARD'
    CURRENT_VIEW_STATE['data'] = None
    
    await asyncio.sleep(0.1)
    content_container.clear()
    content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
    
    # 1. 计算初始统计数据
    init_data = calculate_dashboard_data()
    if not init_data:
        init_data = {
            "servers": "0/0", "nodes": "0", "traffic": "0 GB", "subs": "0",
            "bar_chart": {"names": [], "values": []}, "pie_chart": []
        }

    # 强制重算区域数据 (Top 5 + 其他)
    # 这是页面加载时显示的正确数据
    group_buckets = {}
    for s in SERVERS_CACHE:
        # 优先使用保存的分组，如果是特殊分组则重新检测
        g_name = s.get('group')
        if not g_name or g_name in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区']:
            g_name = detect_country_group(s.get('name', ''))
        
        if g_name not in group_buckets: group_buckets[g_name] = 0
        group_buckets[g_name] += 1
    
    # 转为列表并排序
    all_regions = [{'name': k, 'value': v} for k, v in group_buckets.items()]
    all_regions.sort(key=lambda x: x['value'], reverse=True)
    
    # 只取前 5 名，剩下的合并为 "🏳️ 其他地区"
    if len(all_regions) > 5:
        top_5 = all_regions[:5]
        others_count = sum(item['value'] for item in all_regions[5:])
        top_5.append({'name': '🏳️ 其他地区', 'value': others_count})
        pie_data_final = top_5
    else:
        pie_data_final = all_regions

    # 覆盖 init_data，确保初始显示正确
    init_data['pie_chart'] = pie_data_final

    with content_container:
        ui.run_javascript("""
        if (window.dashInterval) clearInterval(window.dashInterval);
        window.dashInterval = setInterval(async () => {
            if (document.hidden) return;
            try {
                const res = await fetch('/api/dashboard/live_data');
                if (!res.ok) return;
                const data = await res.json();
                if (data.error) return;

                // 1. 刷新顶部数字 (保留)
                const ids = ['stat-servers', 'stat-nodes', 'stat-traffic', 'stat-subs'];
                const keys = ['servers', 'nodes', 'traffic', 'subs'];
                ids.forEach((id, i) => {
                    const el = document.getElementById(id);
                    if (el) el.innerText = data[keys[i]];
                });

                // 2. 刷新柱状图 (流量是实时变的，必须保留)
                const barDom = document.getElementById('chart-bar');
                if (barDom) {
                    const chart = echarts.getInstanceByDom(barDom);
                    if (chart) {
                        chart.setOption({
                            xAxis: { data: data.bar_chart.names },
                            series: [{ data: data.bar_chart.values }]
                        });
                    }
                }
                
                // ✂️ [已彻底删除] 饼图更新逻辑
                // 这里原本有 update chart-pie 的代码，现在删掉了。
                // 无论后台发来什么数据，饼图永远保持 Python 刚开始画的样子。
                
            } catch (e) {}
        }, 3000);
        """)

        ui.label('系统概览').classes('text-3xl font-bold mb-4 text-slate-200 tracking-tight')
        
        # === A. 顶部统计卡片 (渐变色保持不变，文字适配) ===
        with ui.row().classes('w-full gap-4 mb-6 items-stretch'):
            def create_stat_card(ref_key, dom_id, title, sub_text, icon, gradient, init_val):
                with ui.card().classes(f'flex-1 p-4 shadow-lg border border-white/10 text-white {gradient} rounded-xl relative overflow-hidden'):
                    ui.element('div').classes('absolute -right-4 -top-4 w-24 h-24 bg-white opacity-10 rounded-full blur-xl')
                    with ui.row().classes('items-center justify-between w-full relative z-10'):
                        with ui.column().classes('gap-0'):
                            ui.label(title).classes('opacity-90 text-[11px] font-bold uppercase tracking-wider')
                            DASHBOARD_REFS[ref_key] = ui.label(init_val).props(f'id={dom_id}').classes('text-3xl font-black tracking-tight my-1 drop-shadow-md')
                            ui.label(sub_text).classes('opacity-70 text-[10px] font-medium')
                        ui.icon(icon).classes('text-4xl opacity-80 drop-shadow-sm')

            create_stat_card('servers', 'stat-servers', '在线服务器', 'Online / Total', 'dns', 'bg-gradient-to-br from-blue-600 to-indigo-800', init_data['servers'])
            create_stat_card('nodes', 'stat-nodes', '节点总数', 'Active Nodes', 'hub', 'bg-gradient-to-br from-purple-600 to-fuchsia-800', init_data['nodes'])
            create_stat_card('traffic', 'stat-traffic', '总流量消耗', 'Total Usage', 'bolt', 'bg-gradient-to-br from-emerald-600 to-teal-800', init_data['traffic'])
            create_stat_card('subs', 'stat-subs', '订阅配置', 'Subscriptions', 'rss_feed', 'bg-gradient-to-br from-orange-500 to-red-700', init_data['subs'])

        # === B. 图表区域 (修改背景为深色) ===
        with ui.row().classes('w-full gap-6 mb-6 flex-wrap xl:flex-nowrap items-stretch'):
            # 深色卡片样式
            chart_card_cls = 'w-full p-5 shadow-xl border border-slate-700 rounded-xl bg-[#1e293b] flex flex-col'
            
            # 流量排行
            with ui.card().classes(f'xl:w-2/3 {chart_card_cls}'):
                with ui.row().classes('w-full justify-between items-center mb-4 border-b border-slate-700 pb-2'):
                    ui.label('📊 服务器流量排行 (GB)').classes('text-base font-bold text-slate-200')
                    with ui.row().classes('items-center gap-1 px-2 py-0.5 bg-green-900/30 rounded-full border border-green-500/30'):
                        ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse')
                        ui.label('Live').classes('text-[10px] font-bold text-green-400')
                
                # ECharts 配置：坐标轴颜色改为灰色，以适应深底
                DASHBOARD_REFS['bar_chart'] = ui.echart({
                    'tooltip': {'trigger': 'axis'},
                    'grid': {'left': '2%', 'right': '3%', 'bottom': '2%', 'top': '10%', 'containLabel': True},
                    'xAxis': {'type': 'category', 'data': init_data['bar_chart']['names'], 'axisLabel': {'interval': 0, 'rotate': 30, 'color': '#94a3b8', 'fontSize': 10}}, # text-slate-400
                    'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'type': 'dashed', 'color': '#334155'}}, 'axisLabel': {'color': '#94a3b8'}},
                    'series': [{'type': 'bar', 'data': init_data['bar_chart']['values'], 'barWidth': '40%', 'itemStyle': {'borderRadius': [3, 3, 0, 0], 'color': '#6366f1'}}]
                }).classes('w-full h-64').props('id=chart-bar')

            # 区域分布
            with ui.card().classes(f'xl:w-1/3 {chart_card_cls}'):
                ui.label('🌏 服务器分布').classes('text-base font-bold text-slate-200 mb-4 border-b border-slate-700 pb-2')
                color_palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#6366f1', '#ec4899', '#14b8a6', '#f97316']
                
                DASHBOARD_REFS['pie_chart'] = ui.echart({
                    'tooltip': {'trigger': 'item', 'formatter': '{b}: <br/><b>{c} 台</b> ({d}%)'},
                    'legend': {'bottom': '0%', 'left': 'center', 'icon': 'circle', 'itemGap': 10, 'textStyle': {'color': '#e2e8f0', 'fontSize': 11}}, # text-slate-200
                    'color': color_palette,
                    'series': [{
                        'name': '服务器分布', 
                        'type': 'pie', 
                        'radius': ['45%', '75%'],
                        'center': ['50%', '45%'],
                        'avoidLabelOverlap': False,
                        'itemStyle': {'borderRadius': 4, 'borderColor': '#1e293b', 'borderWidth': 2}, # 边框颜色配合背景
                        'label': { 'show': False, 'position': 'center' },
                        'emphasis': {'label': {'show': True, 'fontSize': 16, 'fontWeight': 'bold', 'color': '#fff'}, 'scale': True, 'scaleSize': 5},
                        'labelLine': { 'show': False },
                        'data': init_data['pie_chart']
                    }]
                }).classes('w-full h-64').props('id=chart-pie')

        # === C. 底部地图区域 ===
        with ui.row().classes('w-full gap-6 mb-6'):
            with ui.card().classes('w-full p-0 shadow-md border-none rounded-xl bg-slate-900 overflow-hidden relative'):
                with ui.row().classes('w-full px-6 py-3 bg-slate-800/50 border-b border-gray-700 justify-between items-center z-10 relative'):
                    with ui.row().classes('gap-2 items-center'):
                        ui.icon('public', color='blue-4').classes('text-xl')
                        ui.label('全球节点实景 (Global View)').classes('text-base font-bold text-white')
                    DASHBOARD_REFS['map_info'] = ui.label('Live Rendering').classes('text-[10px] text-gray-400')

                # 1. 准备旧版简单数据
                globe_data_list = []
                seen_locations = set()
                total_server_count = len(SERVERS_CACHE)
                
                for s in SERVERS_CACHE:
                    lat, lon = None, None
                    if 'lat' in s: lat, lon = s['lat'], s['lon']
                    else:
                        c = get_coords_from_name(s.get('name', ''))
                        if c: lat, lon = c[0], c[1]
                    if lat:
                        k = (round(lat,2), round(lon,2))
                        if k not in seen_locations:
                            seen_locations.add(k)
                            flag = "📍"
                            try: flag = detect_country_group(s['name']).split(' ')[0]
                            except: pass
                            globe_data_list.append({'lat': lat, 'lon': lon, 'name': flag})

                import json
                json_data = json.dumps(globe_data_list, ensure_ascii=False)
                
                # 2. 渲染容器
                ui.html(GLOBE_STRUCTURE, sanitize=False).classes('w-full h-[650px] overflow-hidden')
                
                # 3. 注入数据和 JS
                ui.run_javascript(f'window.DASHBOARD_DATA = {json_data};')
                ui.run_javascript(GLOBE_JS_LOGIC)