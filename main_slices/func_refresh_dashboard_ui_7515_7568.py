async def refresh_dashboard_ui():
    try:
        # 如果仪表盘还没打开（引用是空的），直接跳过
        if not DASHBOARD_REFS.get('servers'): return

        # ✨ 直接调用通用计算函数，确保与 API 逻辑绝对一致
        data = calculate_dashboard_data()
        if not data: return

        # --- 更新 UI 文字 ---
        if DASHBOARD_REFS.get('servers'): DASHBOARD_REFS['servers'].set_text(data['servers'])
        if DASHBOARD_REFS.get('nodes'): DASHBOARD_REFS['nodes'].set_text(data['nodes'])
        if DASHBOARD_REFS.get('traffic'): DASHBOARD_REFS['traffic'].set_text(data['traffic'])
        if DASHBOARD_REFS.get('subs'): DASHBOARD_REFS['subs'].set_text(data['subs'])

        # --- 更新 柱状图 ---
        if DASHBOARD_REFS.get('bar_chart'):
            DASHBOARD_REFS['bar_chart'].options['xAxis']['data'] = data['bar_chart']['names']
            DASHBOARD_REFS['bar_chart'].options['series'][0]['data'] = data['bar_chart']['values']
            DASHBOARD_REFS['bar_chart'].update()

        # --- 更新 饼图 ---
        if DASHBOARD_REFS.get('pie_chart'):
            DASHBOARD_REFS['pie_chart'].options['series'][0]['data'] = data['pie_chart']
            DASHBOARD_REFS['pie_chart'].update()

        # --- 更新地图数据 ---
        globe_data_list = []
        seen_locations = set()
        for s in SERVERS_CACHE:
            lat, lon = None, None
            if 'lat' in s and 'lon' in s: lat, lon = s['lat'], s['lon']
            else:
                coords = get_coords_from_name(s.get('name', ''))
                if coords: lat, lon = coords[0], coords[1]
            
            if lat is not None and lon is not None:
                coord_key = (round(lat, 2), round(lon, 2))
                if coord_key not in seen_locations:
                    seen_locations.add(coord_key)
                    flag_only = "📍"
                    try:
                        full_group = detect_country_group(s.get('name', ''), s)
                        flag_only = full_group.split(' ')[0]
                    except: pass
                    globe_data_list.append({'lat': lat, 'lon': lon, 'name': flag_only})
        
        if CURRENT_VIEW_STATE.get('scope') == 'DASHBOARD':
            import json
            json_data = json.dumps(globe_data_list, ensure_ascii=False)
            ui.run_javascript(f'if(window.updateDashboardMap) window.updateDashboardMap({json_data});')

    except Exception as e:
        logger.error(f"UI 更新失败: {e}")