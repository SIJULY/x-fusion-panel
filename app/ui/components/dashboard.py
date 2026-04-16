import asyncio
import json

from nicegui import app, ui

from app.core.logging import logger
from app.core.state import CURRENT_VIEW_STATE, DASHBOARD_REFS, SERVERS_CACHE
from app.services.dashboard import calculate_dashboard_data
from app.utils.geo import detect_country_group, get_coords_from_name


def build_globe_structure(is_dark: bool) -> str:
    container_bg = 'var(--xf-code-bg)'
    stats_text = 'var(--xf-text-strong)'
    stats_bg = 'color-mix(in srgb, var(--xf-panel-bg) 88%, transparent)'
    stats_border = 'var(--xf-card-border)'
    stats_span = 'var(--xf-accent)'
    return f"""
<style>
    #earth-container {{
        width: 100%;
        height: 100%;
        position: relative;
        overflow: hidden;
        border-radius: 12px;
        background-color: {container_bg};
    }}

    .earth-stats {{
        position: absolute;
        top: 20px;
        left: 20px;
        color: {stats_text};
        font-family: 'Consolas', monospace;
        font-size: 12px;
        z-index: 10;
        background: {stats_bg};
        padding: 10px 15px;
        border: 1px solid {stats_border};
        border-radius: 6px;
        backdrop-filter: blur(4px);
        pointer-events: none;
    }}
    .earth-stats span {{ color: {stats_span}; font-weight: bold; }}
</style>

<div id="earth-container">
    <div class="earth-stats">
        <div>ACTIVE NODES: <span id="node-count">0</span></div>
        <div>REGIONS: <span id="region-count">0</span></div>
    </div>
    <div id="earth-render-area" style="width:100%; height:100%;"></div>
</div>
"""


def build_globe_js_logic(is_dark: bool) -> str:
    return """
(function() {
    var container = document.getElementById('earth-render-area');
    if (!container) return;

    var serverData = window.DASHBOARD_DATA || [];
    var myLat = 39.9;
    var myLon = 116.4;
    var emojiFont = '"Twemoji Country Flags", "Noto Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", sans-serif';
    var nodeCountEl = document.getElementById('node-count');
    var regionCountEl = document.getElementById('region-count');

    function xfVar(name, fallback) {
        const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return v || fallback;
    }

    function getPalette() {
        return {
            background: 'transparent',
            geoArea: xfVar('--xf-map-geo-area', '#172033'),
            geoBorder: xfVar('--xf-map-geo-border', '#334155'),
            geoEmphasis: xfVar('--xf-map-geo-emphasis', '#1e293b'),
            highlightArea: xfVar('--xf-map-highlight-area', '#2563eb'),
            highlightBorder: xfVar('--xf-map-highlight-border', xfVar('--xf-accent', '#22d3ee')),
            scatter: xfVar('--xf-accent', '#22d3ee'),
            scatterShadow: xfVar('--xf-map-scatter-shadow', 'rgba(15,23,42,0.9)'),
            scatterLabel: xfVar('--xf-text-strong', '#e5eefc'),
            me: xfVar('--xf-map-me', '#facc15'),
            line: xfVar('--xf-map-line', '#22d3ee')
        };
    }

    function updateStats(data) {
        if (nodeCountEl) nodeCountEl.textContent = data.length;
        const uniqueRegions = new Set(data.map(s => s.name));
        if (regionCountEl) regionCountEl.textContent = uniqueRegions.size;
    }
    updateStats(serverData);

    var existing = echarts.getInstanceByDom(container);
    if (existing) existing.dispose();
    var myChart = echarts.init(container);
    window.dashboardMapChart = myChart;

    const searchKeys = {
        '🇺🇸': 'United States', '🇨🇳': 'China', '🇭🇰': 'China', '🇹🇼': 'China', '🇯🇵': 'Japan', '🇰🇷': 'Korea',
        '🇸🇬': 'Singapore', '🇬🇧': 'United Kingdom', '🇩🇪': 'Germany', '🇫🇷': 'France', '🇷🇺': 'Russia',
        '🇨🇦': 'Canada', '🇦🇺': 'Australia', '🇮🇳': 'India', '🇧🇷': 'Brazil'
    };

    function buildOption(mapGeoJSON, data, userLat, userLon) {
        const colors = getPalette();
        const mapFeatureNames = mapGeoJSON.features.map(f => f.properties.name);
        const activeMapNames = new Set();

        data.forEach(s => {
            let keyword = null;
            for (let key in searchKeys) {
                if (s.name && s.name.includes(key)) {
                    keyword = searchKeys[key];
                    break;
                }
            }
            if (keyword && mapFeatureNames.includes(keyword)) {
                activeMapNames.add(keyword);
            }
        });

        const highlightRegions = Array.from(activeMapNames).map(name => ({
            name: name,
            itemStyle: { areaColor: colors.highlightArea, borderColor: colors.highlightBorder, borderWidth: 1.5, opacity: 0.9 }
        }));

        const scatterData = data.map(s => ({
            name: s.name,
            value: [s.lon, s.lat],
            itemStyle: { color: colors.scatter }
        }));

        scatterData.push({
            name: 'ME',
            value: [userLon, userLat],
            itemStyle: { color: colors.me },
            symbolSize: 15,
            label: { show: true, position: 'top', formatter: 'My PC', color: colors.me }
        });

        const linesData = data.map(s => ({ coords: [[s.lon, s.lat], [userLon, userLat]] }));

        return {
            backgroundColor: colors.background,
            geo: {
                map: 'world',
                roam: false,
                zoom: 1.2,
                center: [15, 10],
                label: { show: false },
                itemStyle: { areaColor: colors.geoArea, borderColor: colors.geoBorder, borderWidth: 1 },
                emphasis: { itemStyle: { areaColor: colors.geoEmphasis }, label: { show: false } },
                regions: highlightRegions
            },
            series: [
                {
                    type: 'lines',
                    coordinateSystem: 'geo',
                    zlevel: 2,
                    effect: { show: true, period: 4, trailLength: 0.5, color: colors.line, symbol: 'arrow', symbolSize: 6 },
                    lineStyle: { color: colors.line, width: 1, opacity: 0, curveness: 0.2 },
                    data: linesData
                },
                {
                    type: 'scatter',
                    coordinateSystem: 'geo',
                    zlevel: 3,
                    symbol: 'circle',
                    symbolSize: 12,
                    itemStyle: { color: colors.scatter, shadowBlur: 10, shadowColor: colors.scatterShadow },
                    label: {
                        show: true,
                        position: 'right',
                        formatter: '{b}',
                        color: colors.scatterLabel,
                        fontSize: 16,
                        fontWeight: 'bold',
                        fontFamily: emojiFont
                    },
                    data: scatterData
                }
            ]
        };
    }

    function applyMap(data) {
        if (!window.cachedWorldJson || !myChart) return;
        updateStats(data);
        myChart.setOption(buildOption(window.cachedWorldJson, data, myLat, myLon), true);
    }

    window.updateDashboardMap = function(newData) {
        serverData = newData;
        applyMap(newData);
    };

    window.updateDashboardMapTheme = function() {
        applyMap(serverData);
    };

    if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
            function(position) {
                myLat = position.coords.latitude;
                myLon = position.coords.longitude;
                applyMap(serverData);
            },
            function() {},
            { enableHighAccuracy: false, timeout: 2500, maximumAge: 600000 }
        );
    }

    fetch('/static/world.json')
        .then(response => response.json())
        .then(worldJson => {
            echarts.registerMap('world', worldJson);
            window.cachedWorldJson = worldJson;
            applyMap(serverData);
            window.addEventListener('resize', () => myChart.resize());
            new ResizeObserver(() => myChart.resize()).observe(container);
        });
})();
"""


async def refresh_dashboard_ui():
    try:
        logger.info(f"[Dashboard] refresh_dashboard_ui called | scope={CURRENT_VIEW_STATE.get('scope')} servers_cache={len(SERVERS_CACHE)} refs_keys={list(DASHBOARD_REFS.keys())}")
        if not DASHBOARD_REFS.get('servers'):
            logger.info("[Dashboard] refresh_dashboard_ui skipped | servers ref missing")
            return

        data = calculate_dashboard_data()
        logger.info(f"[Dashboard] refresh_dashboard_ui data={data}")
        if not data:
            logger.info("[Dashboard] refresh_dashboard_ui skipped | data missing")
            return

        if DASHBOARD_REFS.get('servers'):
            DASHBOARD_REFS['servers'].set_text(data['servers'])
        if DASHBOARD_REFS.get('nodes'):
            DASHBOARD_REFS['nodes'].set_text(data['nodes'])
        if DASHBOARD_REFS.get('traffic'):
            DASHBOARD_REFS['traffic'].set_text(data['traffic'])
        if DASHBOARD_REFS.get('subs'):
            DASHBOARD_REFS['subs'].set_text(data['subs'])

        is_dark = bool(app.storage.user.get('is_dark', True))
        text_strong = '#f8fafc' if is_dark else '#111827'
        text_muted = '#94a3b8' if is_dark else '#4b5563'
        split_line = '#1e3a5f' if is_dark else '#d1d5db'
        pie_border = '#070b14' if is_dark else '#ffffff'

        if DASHBOARD_REFS.get('bar_chart'):
            DASHBOARD_REFS['bar_chart'].options['xAxis']['data'] = data['bar_chart']['names']
            DASHBOARD_REFS['bar_chart'].options['xAxis']['axisLabel']['color'] = text_strong
            DASHBOARD_REFS['bar_chart'].options['yAxis']['axisLabel']['color'] = text_muted
            DASHBOARD_REFS['bar_chart'].options['yAxis']['splitLine']['lineStyle']['color'] = split_line
            DASHBOARD_REFS['bar_chart'].options['series'][0]['data'] = data['bar_chart']['values']
            DASHBOARD_REFS['bar_chart'].update()

        if DASHBOARD_REFS.get('pie_chart'):
            DASHBOARD_REFS['pie_chart'].options['legend']['textStyle']['color'] = text_strong
            DASHBOARD_REFS['pie_chart'].options['series'][0]['itemStyle']['borderColor'] = pie_border
            DASHBOARD_REFS['pie_chart'].options['series'][0]['label']['color'] = text_strong
            DASHBOARD_REFS['pie_chart'].options['series'][0]['emphasis']['label']['color'] = text_strong
            DASHBOARD_REFS['pie_chart'].options['series'][0]['data'] = data['pie_chart']
            DASHBOARD_REFS['pie_chart'].update()

        globe_data_list = []
        seen_locations = set()
        for s in SERVERS_CACHE:
            lat, lon = None, None
            if 'lat' in s and 'lon' in s:
                lat, lon = s['lat'], s['lon']
            else:
                coords = get_coords_from_name(s.get('name', ''))
                if coords:
                    lat, lon = coords[0], coords[1]

            if lat is not None and lon is not None:
                coord_key = (round(lat, 2), round(lon, 2))
                if coord_key not in seen_locations:
                    seen_locations.add(coord_key)
                    flag_only = "📍"
                    try:
                        full_group = detect_country_group(s.get('name', ''), s)
                        flag_only = full_group.split(' ')[0]
                    except:
                        pass
                    globe_data_list.append({'lat': lat, 'lon': lon, 'name': flag_only})

        if CURRENT_VIEW_STATE.get('scope') == 'DASHBOARD':
            json_data = json.dumps(globe_data_list, ensure_ascii=False)
            ui.run_javascript(f'if(window.updateDashboardMap) window.updateDashboardMap({json_data}); if(window.updateDashboardMapTheme) window.updateDashboardMapTheme();')

    except Exception as e:
        logger.error(f"UI 更新失败: {e}")


async def load_dashboard_stats():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'DASHBOARD'
    CURRENT_VIEW_STATE['data'] = None
    CURRENT_VIEW_STATE['page'] = 1
    app.storage.user['last_view_scope'] = 'DASHBOARD'
    app.storage.user['last_view_data'] = None
    app.storage.user['last_view_page'] = 1
    logger.info(f"[Dashboard] load_dashboard_stats start | servers_cache={len(SERVERS_CACHE)} refs_before={list(DASHBOARD_REFS.keys())}")

    is_dark = bool(app.storage.user.get('is_dark', True))
    overview_wrap_cls = 'w-full items-center gap-3 border-b pb-3 mb-4'
    overview_icon_cls = 'w-11 h-11 rounded-sm flex items-center justify-center border relative overflow-hidden'
    overview_title_cls = 'text-3xl font-black tracking-wide'
    overview_sub_cls = 'text-xs font-black uppercase tracking-[0.25em]'
    stat_card_base = 'flex-1 p-4 rounded-sm relative overflow-hidden border'
    stat_title_cls = 'opacity-90 text-[11px] font-black uppercase tracking-[0.2em]'
    stat_value_cls = 'text-3xl font-black tracking-tight my-1'
    stat_subtext_cls = 'opacity-75 text-[10px] font-bold'
    stat_icon_cls = 'text-4xl opacity-80 drop-shadow-sm'
    chart_card_cls = 'w-full p-5 border rounded-sm flex flex-col'
    chart_header_cls = 'w-full justify-between items-center mb-4 border-b pb-2'
    chart_title_cls = 'text-base font-black tracking-wide'
    live_wrap_cls = 'items-center gap-1 px-2 py-0.5 rounded-sm border'
    live_text_cls = 'text-[10px] font-black tracking-wide'
    map_card_cls = 'w-full p-0 border rounded-sm overflow-hidden relative'
    map_header_cls = 'w-full px-6 py-3 border-b justify-between items-center z-10 relative'
    map_info_cls = 'text-[10px] font-bold tracking-wide'

    await asyncio.sleep(0.1)

    from app.ui.pages.content_router import content_container

    content_container.clear()
    content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
    content_container.style('background-color: var(--xf-bg-main);')

    init_data = calculate_dashboard_data()
    logger.info(f"[Dashboard] load_dashboard_stats init_data={init_data}")
    if not init_data:
        init_data = {
            "servers": "0/0", "nodes": "0", "traffic": "0 GB", "subs": "0",
            "bar_chart": {"names": [], "values": []}, "pie_chart": []
        }

    group_buckets = {}
    for s in SERVERS_CACHE:
        g_name = s.get('group')
        if not g_name or g_name in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区']:
            g_name = detect_country_group(s.get('name', ''))

        if g_name not in group_buckets:
            group_buckets[g_name] = 0
        group_buckets[g_name] += 1

    all_regions = [{'name': k, 'value': v} for k, v in group_buckets.items()]
    all_regions.sort(key=lambda x: x['value'], reverse=True)

    if len(all_regions) > 5:
        top_5 = all_regions[:5]
        others_count = sum(item['value'] for item in all_regions[5:])
        top_5.append({'name': '🏳️ 其他地区', 'value': others_count})
        pie_data_final = top_5
    else:
        pie_data_final = all_regions

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

                const ids = ['stat-servers', 'stat-nodes', 'stat-traffic', 'stat-subs'];
                const keys = ['servers', 'nodes', 'traffic', 'subs'];
                ids.forEach((id, i) => {
                    const el = document.getElementById(id);
                    if (el) el.innerText = data[keys[i]];
                });

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
            } catch (e) {}
        }, 3000);

        window.applyDashboardTheme = function() {
            const isDark = document.body.classList.contains('body--dark');
            const textStrong = isDark ? '#f8fafc' : '#111827';
            const textMuted = isDark ? '#94a3b8' : '#4b5563';
            const cardBorder = isDark ? '#334155' : '#d1d5db';
            const tooltipBg = isDark ? '#0f172a' : '#ffffff';
            const tooltipBorder = isDark ? '#334155' : '#cbd5e1';
            const panelBg = isDark ? '#0f172a' : '#ffffff';

            const barDom = document.getElementById('chart-bar');
            if (barDom) {
                const chart = echarts.getInstanceByDom(barDom);
                if (chart) {
                    chart.setOption({
                        textStyle: { color: textStrong },
                        tooltip: { backgroundColor: tooltipBg, borderColor: tooltipBorder, textStyle: { color: textStrong } },
                        xAxis: {
                            axisLine: { lineStyle: { color: cardBorder } },
                            axisLabel: { color: textStrong, margin: 14 }
                        },
                        yAxis: {
                            axisLabel: { color: textMuted },
                            splitLine: { lineStyle: { color: cardBorder } }
                        }
                    }, false, true);
                }
            }

            const pieDom = document.getElementById('chart-pie');
            if (pieDom) {
                const chart = echarts.getInstanceByDom(pieDom);
                if (chart) {
                    chart.setOption({
                        textStyle: { color: textStrong },
                        tooltip: { backgroundColor: tooltipBg, borderColor: tooltipBorder, textStyle: { color: textStrong } },
                        legend: { textStyle: { color: textStrong } },
                        series: [{
                            label: { color: textStrong },
                            itemStyle: { borderColor: panelBg },
                            emphasis: { label: { color: textStrong } }
                        }]
                    }, false, true);
                }
            }

            if (window.updateDashboardMapTheme) window.updateDashboardMapTheme();
        };
        """)

        with ui.row().classes(overview_wrap_cls).style('border-color: var(--xf-card-border);'):
            with ui.element('div').classes(overview_icon_cls).style('background: var(--xf-code-bg); border-color: var(--xf-card-border); color: var(--xf-accent); box-shadow: 0 4px 12px rgba(15,23,42,0.12);'):
                ui.element('div').classes('absolute inset-0').style('background: var(--xf-accent-soft);')
                ui.icon('dashboard').classes('text-[20px] drop-shadow-[0_0_5px_currentColor]')
            with ui.column().classes('gap-0'):
                ui.label('系统概览').classes(overview_title_cls).style('color: var(--xf-text-strong);')
                ui.label('Dashboard Overview').classes(overview_sub_cls).style('color: var(--xf-accent); opacity: 0.7;')

        with ui.row().classes('w-full gap-4 mb-6 items-stretch'):
            def create_stat_card(ref_key, dom_id, title, sub_text, icon, theme_key, init_val):
                badge_var = f'var(--xf-stat-{theme_key}-badge)'
                with ui.card().classes(stat_card_base).style(f'background: var(--xf-stat-{theme_key}-bg); border-color: color-mix(in srgb, {badge_var} 32%, var(--xf-card-border)); box-shadow: 0 14px 30px rgba(15,23,42,0.14);'):
                    ui.element('div').classes('absolute -right-8 -top-8 w-28 h-28 rounded-full blur-2xl').style(f'background: color-mix(in srgb, {badge_var} 28%, white 12%); opacity: 0.95;')
                    ui.element('div').classes('absolute inset-0 pointer-events-none').style('background: linear-gradient(135deg, rgba(255,255,255,0.18), transparent 50%);')
                    with ui.row().classes('items-center justify-between w-full relative z-10'):
                        with ui.column().classes('gap-0'):
                            with ui.row().classes('items-center gap-2 mb-1'):
                                ui.element('div').classes('h-2 w-2 rounded-full').style(f'background: {badge_var}; box-shadow: 0 0 10px {badge_var};')
                                ui.label(title).classes(stat_title_cls).style('color: var(--xf-stat-title);')
                            DASHBOARD_REFS[ref_key] = ui.label(init_val).props(f'id={dom_id}').classes(stat_value_cls).style('color: var(--xf-stat-value); text-shadow: 0 2px 12px rgba(15,23,42,0.18);')
                            ui.label(sub_text).classes(stat_subtext_cls).style('color: var(--xf-stat-sub);')
                        with ui.element('div').classes('w-14 h-14 rounded-2xl flex items-center justify-center border').style(f'background: var(--xf-stat-{theme_key}-icon-bg); border-color: color-mix(in srgb, {badge_var} 40%, transparent);'):
                            ui.icon(icon).classes(stat_icon_cls).style(f'color: {badge_var}; opacity: 1;')

            create_stat_card('servers', 'stat-servers', '在线服务器', 'Online / Total', 'dns', 'servers', init_data['servers'])
            create_stat_card('nodes', 'stat-nodes', '节点总数', 'Active Nodes', 'hub', 'nodes', init_data['nodes'])
            create_stat_card('traffic', 'stat-traffic', '总流量消耗', 'Total Usage', 'bolt', 'traffic', init_data['traffic'])
            create_stat_card('subs', 'stat-subs', '订阅配置', 'Subscriptions', 'rss_feed', 'subs', init_data['subs'])
            logger.info(f"[Dashboard] stat refs assigned | refs_now={list(DASHBOARD_REFS.keys())}")

        with ui.row().classes('w-full gap-6 mb-6 flex-wrap xl:flex-nowrap items-stretch'):
            with ui.card().classes(f'xl:w-2/3 {chart_card_cls}').style('background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 8px 24px rgba(15,23,42,0.10);'):
                with ui.row().classes(chart_header_cls).style('border-color: var(--xf-card-border);'):
                    ui.label('📊 服务器流量排行 (GB)').classes(chart_title_cls).style('color: var(--xf-text-strong);')
                    with ui.row().classes(live_wrap_cls).style('background: color-mix(in srgb, #22c55e 16%, transparent); border-color: color-mix(in srgb, #22c55e 35%, var(--xf-card-border));'):
                        ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse')
                        ui.label('Live').classes(live_text_cls).style('color: #22c55e;')

                DASHBOARD_REFS['bar_chart'] = ui.echart({
                    'textStyle': {'color': '#e5eefc' if is_dark else '#0f172a'},
                    'tooltip': {'trigger': 'axis', 'backgroundColor': '#0f172a' if is_dark else '#ffffff', 'borderColor': '#334155' if is_dark else '#cbd5e1', 'textStyle': {'color': '#e5eefc' if is_dark else '#0f172a'}},
                    'grid': {'left': '2%', 'right': '3%', 'bottom': '2%', 'top': '10%', 'containLabel': True},
                    'xAxis': {'type': 'category', 'data': init_data['bar_chart']['names'], 'axisLine': {'lineStyle': {'color': '#334155' if is_dark else '#d1d5db'}}, 'axisLabel': {'interval': 0, 'rotate': 30, 'margin': 14, 'color': '#f8fafc' if is_dark else '#111827', 'fontSize': 10}},
                    'yAxis': {'type': 'value', 'axisLine': {'show': False}, 'splitLine': {'lineStyle': {'type': 'dashed', 'color': '#1e3a5f' if is_dark else '#d1d5db'}}, 'axisLabel': {'color': '#94a3b8' if is_dark else '#4b5563'}},
                    'series': [{'type': 'bar', 'data': init_data['bar_chart']['values'], 'barWidth': '40%', 'itemStyle': {'borderRadius': [6, 6, 0, 0], 'color': '#06b6d4'}}]
                }).classes('w-full h-64').props('id=chart-bar')

            with ui.card().classes(f'xl:w-1/3 {chart_card_cls}').style('background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 8px 24px rgba(15,23,42,0.10);'):
                ui.label('🌏 服务器分布').classes('text-base font-black mb-4 pb-2 tracking-wide border-b').style('color: var(--xf-text-strong); border-color: var(--xf-card-border);')
                color_palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#6366f1', '#ec4899', '#14b8a6', '#f97316']

                DASHBOARD_REFS['pie_chart'] = ui.echart({
                    'textStyle': {'color': '#e5eefc' if is_dark else '#0f172a'},
                    'tooltip': {'trigger': 'item', 'formatter': '{b}: <br/><b>{c} 台</b> ({d}%)', 'backgroundColor': '#0f172a' if is_dark else '#ffffff', 'borderColor': '#334155' if is_dark else '#cbd5e1', 'textStyle': {'color': '#e5eefc' if is_dark else '#0f172a'}},
                    'legend': {'bottom': '0%', 'left': 'center', 'icon': 'circle', 'itemGap': 10, 'textStyle': {'color': '#f8fafc' if is_dark else '#111827', 'fontSize': 11}},
                    'color': color_palette,
                    'series': [{
                        'name': '服务器分布',
                        'type': 'pie',
                        'radius': ['45%', '75%'],
                        'center': ['50%', '45%'],
                        'avoidLabelOverlap': False,
                        'itemStyle': {'borderRadius': 4, 'borderColor': '#070b14' if is_dark else '#ffffff', 'borderWidth': 2},
                        'label': {'show': False, 'position': 'center', 'color': '#f8fafc' if is_dark else '#111827'},
                        'emphasis': {'label': {'show': True, 'fontSize': 16, 'fontWeight': 'bold', 'color': '#f8fafc' if is_dark else '#111827'}, 'scale': True, 'scaleSize': 5},
                        'labelLine': {'show': False},
                        'data': init_data['pie_chart']
                    }]
                }).classes('w-full h-64').props('id=chart-pie')

        with ui.row().classes('w-full gap-6 mb-6'):
            with ui.card().classes(map_card_cls).style('background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 8px 24px rgba(15,23,42,0.10);'):
                with ui.row().classes(map_header_cls).style('background: var(--xf-panel-bg); border-color: var(--xf-card-border);'):
                    with ui.row().classes('gap-2 items-center'):
                        ui.icon('public').classes('text-xl').style('color: var(--xf-accent);')
                        ui.label('全球节点实景 (Global View)').classes(chart_title_cls).style('color: var(--xf-text-strong);')
                    DASHBOARD_REFS['map_info'] = ui.label('Live Rendering').classes(map_info_cls).style('color: var(--xf-accent); opacity: 0.7;')

                globe_data_list = []
                seen_locations = set()
                total_server_count = len(SERVERS_CACHE)

                for s in SERVERS_CACHE:
                    lat, lon = None, None
                    if 'lat' in s:
                        lat, lon = s['lat'], s['lon']
                    else:
                        c = get_coords_from_name(s.get('name', ''))
                        if c:
                            lat, lon = c[0], c[1]
                    if lat:
                        k = (round(lat, 2), round(lon, 2))
                        if k not in seen_locations:
                            seen_locations.add(k)
                            flag = "📍"
                            try:
                                flag = detect_country_group(s['name']).split(' ')[0]
                            except:
                                pass
                            globe_data_list.append({'lat': lat, 'lon': lon, 'name': flag})

                json_data = json.dumps(globe_data_list, ensure_ascii=False)

                ui.html(build_globe_structure(is_dark), sanitize=False).classes('w-full h-[650px] overflow-hidden')
                ui.run_javascript(f'window.DASHBOARD_DATA = {json_data};')
                ui.run_javascript(build_globe_js_logic(is_dark))
                ui.run_javascript('setTimeout(() => { if(window.applyDashboardTheme) window.applyDashboardTheme(); }, 60)')
