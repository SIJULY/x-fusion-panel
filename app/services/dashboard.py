import json

from app.core.logging import logger
from app.core.state import NODES_DATA, PROBE_DATA_CACHE, SERVERS_CACHE, SUBS_CACHE
from app.utils.geo import detect_country_group, get_coords_from_name, get_flag_for_country


def prepare_map_data():
    try:
        city_points_map = {}
        flag_points_map = {}
        unique_deployed_countries = set()
        region_stats = {}
        active_regions_for_highlight = set()

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

        MAP_NAME_ALIASES = {
            'United States': ['United States of America', 'USA'],
            'United Kingdom': ['United Kingdom', 'UK', 'Great Britain'],
            'China': ["People's Republic of China"],
            'Russia': ['Russian Federation'],
            'South Korea': ['Korea', 'Republic of Korea'],
            'Vietnam': ['Viet Nam']
        }

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

        temp_stats_storage = {}

        for s in snapshot:
            s_name = s.get('name', '')

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
                except:
                    pass

            try:
                country_counter[detect_country_group(s_name, s)] += 1
            except:
                pass

            lat, lon = None, None
            for city_key, (c_lat, c_lon) in CITY_COORDS_FIX.items():
                if city_key.lower() in s_name.lower():
                    lat, lon = c_lat, c_lon
                    break
            if not lat:
                if 'lat' in s and 'lon' in s:
                    lat, lon = s['lat'], s['lon']
                else:
                    coords = get_coords_from_name(s_name)
                    if coords:
                        lat, lon = coords[0], coords[1]

            if lat and lon and map_name_standard:
                coord_key = f"{lat},{lon}"
                if coord_key not in city_points_map:
                    city_points_map[coord_key] = {'name': s_name, 'value': [lon, lat], 'country_key': map_name_standard}

                if flag_icon != "📍" and flag_icon not in flag_points_map:
                    flag_points_map[flag_icon] = {'name': flag_icon, 'value': [lon, lat], 'country_key': map_name_standard}

            if map_name_standard:
                unique_deployed_countries.add(map_name_standard)

                if map_name_standard not in temp_stats_storage:
                    cn_name = map_name_standard
                    try:
                        full_g = detect_country_group(s_name, s)
                        if full_g and ' ' in full_g:
                            cn_name = full_g.split(' ')[1]
                    except:
                        pass

                    temp_stats_storage[map_name_standard] = {
                        'flag': flag_icon, 'cn': cn_name,
                        'total': 0, 'online': 0, 'servers': []
                    }

                rs = temp_stats_storage[map_name_standard]
                rs['total'] += 1

                is_on = False

                probe_cache = PROBE_DATA_CACHE.get(s['url'])
                if probe_cache:
                    if now_ts - probe_cache.get('last_updated', 0) < 20:
                        is_on = True

                if not is_on and s.get('_status') == 'online':
                    is_on = True

                if is_on:
                    rs['online'] += 1

                rs['servers'].append({
                    'name': s_name,
                    'status': 'online' if is_on else 'offline'
                })

                if map_name_standard not in COUNTRY_CENTROIDS and lat and lon:
                    COUNTRY_CENTROIDS[map_name_standard] = [lon, lat]

        for std_name, stats in temp_stats_storage.items():
            stats['servers'].sort(key=lambda x: 0 if x['status'] == 'online' else 1)
            region_stats[std_name] = stats
            active_regions_for_highlight.add(std_name)

            if std_name in MAP_NAME_ALIASES:
                for alias in MAP_NAME_ALIASES[std_name]:
                    region_stats[alias] = stats
                    active_regions_for_highlight.add(alias)

        pie_data = []
        if country_counter:
            sorted_counts = country_counter.most_common(5)
            for k, v in sorted_counts:
                pie_data.append({'name': f"{k} ({v})", 'value': v})
            others = sum(country_counter.values()) - sum(x[1] for x in sorted_counts)
            if others > 0:
                pie_data.append({'name': f"🏳️ 其他 ({others})", 'value': others})
        else:
            pie_data.append({'name': '暂无数据', 'value': 0})

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
        import traceback
        traceback.print_exc()
        return (json.dumps({'cities': [], 'flags': [], 'regions': []}), [], 0, "{}", "{}")


def get_dashboard_live_data():
    data = calculate_dashboard_data()
    return data if data else {"error": "Calculation failed"}


def calculate_dashboard_data():
    """
    计算并返回当前所有面板数据。
    逻辑调整：优先使用 Root 探针的流量和状态，没有探针才使用 X-UI 数据。
    """
    try:
        total_servers = len(SERVERS_CACHE)
        logger.info(f"[DashboardCalc] start | total_servers={total_servers} nodes_cache_keys={len(NODES_DATA)} probe_cache_keys={len(PROBE_DATA_CACHE)} subs={len(SUBS_CACHE)}")
        online_servers = 0
        total_nodes = 0
        total_traffic_bytes = 0

        server_traffic_map = {}
        from collections import Counter
        country_counter = Counter()

        import time
        now_ts = time.time()

        for s in SERVERS_CACHE:
            res = NODES_DATA.get(s['url'], []) or []
            custom = s.get('custom_nodes', []) or []
            probe_data = PROBE_DATA_CACHE.get(s['url'])

            name = s.get('name', '未命名')

            try:
                region_str = detect_country_group(name, s)
                if not region_str or region_str.strip() == "🏳️":
                    region_str = "🏳️ 未知区域"
            except:
                region_str = "🏳️ 未知区域"
            country_counter[region_str] += 1

            srv_traffic = 0
            use_probe_traffic = False

            if s.get('probe_installed') and probe_data:
                t_in = probe_data.get('net_total_in', 0)
                t_out = probe_data.get('net_total_out', 0)
                if t_in > 0 or t_out > 0:
                    srv_traffic = t_in + t_out
                    use_probe_traffic = True

            if not use_probe_traffic and res:
                for n in res:
                    srv_traffic += int(n.get('up', 0)) + int(n.get('down', 0))

            total_traffic_bytes += srv_traffic
            server_traffic_map[name] = srv_traffic

            is_online = False

            if s.get('probe_installed') and probe_data:
                if now_ts - probe_data.get('last_updated', 0) < 60:
                    is_online = True

            if not is_online:
                if res or s.get('_status') == 'online':
                    is_online = True

            if is_online:
                online_servers += 1

            if res:
                total_nodes += len(res)
            if custom:
                total_nodes += len(custom)

        sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:15]
        bar_names = [x[0] for x in sorted_traffic]
        bar_values = [round(x[1] / (1024**3), 2) for x in sorted_traffic]

        chart_data = []
        sorted_regions = country_counter.most_common()

        if len(sorted_regions) > 5:
            top_5 = sorted_regions[:5]
            others_count = sum(item[1] for item in sorted_regions[5:])
            for k, v in top_5:
                chart_data.append({'name': f"{k} ({v})", 'value': v})
            if others_count > 0:
                chart_data.append({'name': f"🏳️ 其他 ({others_count})", 'value': others_count})
        else:
            for k, v in sorted_regions:
                chart_data.append({'name': f"{k} ({v})", 'value': v})

        if not chart_data:
            chart_data = [{'name': '暂无数据', 'value': 0}]

        result = {
            "servers": f"{online_servers}/{total_servers}",
            "nodes": str(total_nodes),
            "traffic": f"{total_traffic_bytes / (1024**3):.2f} GB",
            "subs": str(len(SUBS_CACHE)),
            "bar_chart": {"names": bar_names, "values": bar_values},
            "pie_chart": chart_data
        }
        logger.info(f"[DashboardCalc] result={result}")
        return result
    except Exception as e:
        print(f"Error calculating dashboard data: {e}")
        import traceback
        traceback.print_exc()
        return None
