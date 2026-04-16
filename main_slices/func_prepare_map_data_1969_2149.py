def prepare_map_data():
    try:
        city_points_map = {} 
        flag_points_map = {} 
        unique_deployed_countries = set() 
        region_stats = {} 
        active_regions_for_highlight = set()

        # 1. 国旗 -> 标准地图名映射 
        FLAG_TO_MAP_NAME = {
            '🇨🇳': 'China', '🇭🇰': 'China', '🇲🇴': 'China', '🇹🇼': 'China',
            '🇺🇸': 'United States', '🇨🇦': 'Canada', '🇲🇽': 'Mexico',
            '🇬🇧': 'United Kingdom', '🇩🇪': 'Germany', '🇫🇷': 'France', '🇳🇱': 'Netherlands',
            '🇷🇺': 'Russia', '🇯🇵': 'Japan', '🇰🇷': 'South Korea', '🇸🇬': 'Singapore',
            '🇮🇳': 'India', '🇦🇺': 'Australia', '🇧🇷': 'Brazil', '🇦🇷': 'Argentina',
            '🇹🇷': 'Turkey', '🇮🇹': 'Italy', '🇪🇸': 'Spain', '🇵🇹': 'Portugal',
            '🇨🇭': 'Switzerland', '🇸🇪': 'Sweden', '🇳🇴': 'Norway', '🇫🇮': 'Finland',
            '🇵🇱': 'Poland', '🇺🇦': 'Ukraine', '🇮🇪': 'Ireland', '🇦🇹': 'Austria',
            '🇧🇪': 'Belgium', '🇩🇰': 'Denmark', '🇨🇿': 'Czech Republic', '🇬🇷': 'Greece',
            '🇿🇦': 'South Africa', '🇪🇬': 'Egypt', '🇸🇦': 'Saudi Arabia', '🇦🇪': 'United Arab Emirates',
            '🇮🇱': 'Israel', '🇮🇷': 'Iran', '🇮🇩': 'Indonesia', '🇲🇾': 'Malaysia',
            '🇹🇭': 'Thailand', '🇻🇳': 'Vietnam', '🇵🇭': 'Philippines', '🇨🇱': 'Chile',
            '🇨🇴': 'Colombia', '🇵🇪': 'Peru'
        }

        # 2. 地图名别名库
        MAP_NAME_ALIASES = {
            'United States': ['United States of America', 'USA'],
            'United Kingdom': ['United Kingdom', 'UK', 'Great Britain'],
            'China': ['People\'s Republic of China'],
            'Russia': ['Russian Federation'],
            'South Korea': ['Korea', 'Republic of Korea'],
            'Vietnam': ['Viet Nam']
        }

        # 3. 中心点坐标库
        COUNTRY_CENTROIDS = {
            'China': [104.19, 35.86], 'United States': [-95.71, 37.09], 'United Kingdom': [-3.43, 55.37],
            'Germany': [10.45, 51.16], 'France': [2.21, 46.22], 'Netherlands': [5.29, 52.13],
            'Russia': [105.31, 61.52], 'Canada': [-106.34, 56.13], 'Brazil': [-51.92, -14.23],
            'Australia': [133.77, -25.27], 'India': [78.96, 20.59], 'Japan': [138.25, 36.20],
            'South Korea': [127.76, 35.90], 'Singapore': [103.81, 1.35], 'Turkey': [35.24, 38.96]
        }
        
        CITY_COORDS_FIX = { 
            'Dubai': (25.20, 55.27), 'Frankfurt': (50.11, 8.68), 'Amsterdam': (52.36, 4.90), 
            'San Jose': (37.33, -121.88), 'Phoenix': (33.44, -112.07), 'Tokyo': (35.68, 139.76),
            'Seoul': (37.56, 126.97), 'London': (51.50, -0.12), 'Singapore': (1.35, 103.81)
        }
        
        from collections import Counter
        country_counter = Counter()
        snapshot = list(SERVERS_CACHE)
        import time 
        now_ts = time.time()
        
        # 临时存储结构
        temp_stats_storage = {}

        for s in snapshot:
            s_name = s.get('name', '')
            
            # --- A. 确定国旗与标准名 ---
            flag_icon = "📍"
            map_name_standard = None
            
            for f, m_name in FLAG_TO_MAP_NAME.items():
                if f in s_name:
                    flag_icon = f
                    map_name_standard = m_name
                    break
            
            if not map_name_standard:
                try:
                    group_str = detect_country_group(s_name, s)
                    if group_str:
                        flag_part = group_str.split(' ')[0]
                        if flag_part in FLAG_TO_MAP_NAME:
                            flag_icon = flag_part
                            map_name_standard = FLAG_TO_MAP_NAME[flag_part]
                except: pass

            try: country_counter[detect_country_group(s_name, s)] += 1
            except: pass

            # --- B. 确定坐标 ---
            lat, lon = None, None
            for city_key, (c_lat, c_lon) in CITY_COORDS_FIX.items():
                if city_key.lower() in s_name.lower(): lat, lon = c_lat, c_lon; break
            if not lat:
                if 'lat' in s and 'lon' in s: lat, lon = s['lat'], s['lon']
                else: 
                    coords = get_coords_from_name(s_name)
                    if coords: lat, lon = coords[0], coords[1]
            
            # --- C. 生成数据点 ---
            if lat and lon and map_name_standard:
                coord_key = f"{lat},{lon}"
                if coord_key not in city_points_map: 
                    city_points_map[coord_key] = {'name': s_name, 'value': [lon, lat], 'country_key': map_name_standard}
                
                if flag_icon != "📍" and flag_icon not in flag_points_map:
                    flag_points_map[flag_icon] = {'name': flag_icon, 'value': [lon, lat], 'country_key': map_name_standard}

            # --- D. 聚合统计数据 (🛑 核心修复位置) ---
            if map_name_standard:
                unique_deployed_countries.add(map_name_standard)
                
                if map_name_standard not in temp_stats_storage:
                    cn_name = map_name_standard
                    try: 
                        full_g = detect_country_group(s_name, s)
                        if full_g and ' ' in full_g: cn_name = full_g.split(' ')[1]
                    except: pass

                    temp_stats_storage[map_name_standard] = {
                        'flag': flag_icon, 'cn': cn_name,
                        'total': 0, 'online': 0, 'servers': []
                    }
                
                rs = temp_stats_storage[map_name_standard]
                rs['total'] += 1
                
                # 🛑 优先检查探针缓存是否在线
                is_on = False
                
                # 1. 检查探针缓存
                probe_cache = PROBE_DATA_CACHE.get(s['url'])
                if probe_cache:
                    # 如果探针数据在 20秒内更新过，视为在线
                    if now_ts - probe_cache.get('last_updated', 0) < 20:
                        is_on = True
                
                # 2. 如果探针不在线，再检查旧的标记 (兼容其他节点)
                if not is_on and s.get('_status') == 'online':
                    is_on = True

                if is_on: rs['online'] += 1
                
                rs['servers'].append({
                    'name': s_name,
                    'status': 'online' if is_on else 'offline'
                })

                if map_name_standard not in COUNTRY_CENTROIDS and lat and lon:
                    COUNTRY_CENTROIDS[map_name_standard] = [lon, lat]

        # --- E. 数据后处理 ---
        for std_name, stats in temp_stats_storage.items():
            stats['servers'].sort(key=lambda x: 0 if x['status'] == 'online' else 1)
            region_stats[std_name] = stats
            active_regions_for_highlight.add(std_name)
            
            if std_name in MAP_NAME_ALIASES:
                for alias in MAP_NAME_ALIASES[std_name]:
                    region_stats[alias] = stats
                    active_regions_for_highlight.add(alias)

        # --- F. 生成饼图数据 ---
        pie_data = []
        if country_counter:
            sorted_counts = country_counter.most_common(5)
            for k, v in sorted_counts: pie_data.append({'name': f"{k} ({v})", 'value': v})
            others = sum(country_counter.values()) - sum(x[1] for x in sorted_counts)
            if others > 0: pie_data.append({'name': f"🏳️ 其他 ({others})", 'value': others})
        else: pie_data.append({'name': '暂无数据', 'value': 0})

        city_list = list(city_points_map.values())
        flag_list = list(flag_points_map.values())
        
        return (
            json.dumps({'cities': city_list, 'flags': flag_list, 'regions': list(active_regions_for_highlight)}, ensure_ascii=False), 
            pie_data, 
            len(unique_deployed_countries), 
            json.dumps(region_stats, ensure_ascii=False),
            json.dumps(COUNTRY_CENTROIDS, ensure_ascii=False)
        )
    except Exception as e:
        print(f"[ERROR] prepare_map_data failed: {e}")
        import traceback; traceback.print_exc()
        return (json.dumps({'cities': [], 'flags': [], 'regions': []}), [], 0, "{}", "{}")