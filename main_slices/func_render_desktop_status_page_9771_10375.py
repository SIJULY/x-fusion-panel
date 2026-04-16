async def render_desktop_status_page():
    global CURRENT_PROBE_TAB
    
    # 1. 启用 Dark Mode
    dark_mode = ui.dark_mode()
    if app.storage.user.get('is_dark') is None:
        app.storage.user['is_dark'] = True
    dark_mode.value = app.storage.user.get('is_dark')

    # 2. 资源注入
    ui.add_head_html('<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>')
    ui.add_head_html('<link href="https://use.fontawesome.com/releases/v6.4.0/css/all.css" rel="stylesheet">')
    
    # [CSS 样式注入] 集成 Twemoji 字体修复 Win 系统国旗显示 
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
        <style>
            @font-face {
                font-family: 'Twemoji Country Flags';
                src: url('https://cdn.jsdelivr.net/npm/country-flag-emoji-polyfill@0.1/dist/TwemojiCountryFlags.woff2') format('woff2');
                unicode-range: U+1F1E6-1F1FF, U+1F3F4, U+E0062-E007F;
            }
            body { 
                margin: 0; 
                font-family: "Twemoji Country Flags", "Noto Color Emoji", "Segoe UI Emoji", "Noto Sans SC", sans-serif; 
                transition: background-color 0.3s ease; 
            }
            body:not(.body--dark) { background: linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%); }
            body.body--dark { background-color: #0b1121; }
            .status-card { transition: all 0.3s ease; border-radius: 16px; }
            body:not(.body--dark) .status-card { background: rgba(255, 255, 255, 0.95); border: 1px solid rgba(255, 255, 255, 0.8); box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1); color: #1e293b; }
            body.body--dark .status-card { background: #1e293b; border: 1px solid rgba(255,255,255,0.05); box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3); color: #e2e8f0; }
            .status-card:hover { transform: translateY(-3px); }
            .offline-card { border-color: rgba(239, 68, 68, 0.6) !important; }
            body.body--dark .offline-card { background-image: repeating-linear-gradient(45deg, rgba(239, 68, 68, 0.05) 0px, rgba(239, 68, 68, 0.05) 10px, transparent 10px, transparent 20px) !important; }
            body:not(.body--dark) .offline-card { background: rgba(254, 226, 226, 0.95) !important; }
            .scrollbar-hide::-webkit-scrollbar { display: none; }
            .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
            .prog-bar { transition: width 0.5s ease-out; }
            #public-map-container { contain: strict; transform: translateZ(0); will-change: transform; z-index: 0; }
        </style>
    ''')
    
    RENDERED_CARDS = {} 
    tab_container = None
    grid_container = None
    header_refs = {}
    pie_chart_ref = None
    pagination_ref = None 
    local_ui_version = GLOBAL_UI_VERSION
    
    # 状态管理
    page_state = {
        'page': 1,
        'group': 'ALL'
    }

    def get_probe_groups():
        groups_list = ['ALL']
        customs = ADMIN_CONFIG.get('probe_custom_groups', [])
        groups_list.extend(customs) 
        return groups_list
    
    def fmt_capacity(b):
        if b is None: return "0 B"
        try:
            if isinstance(b, str):
                import re
                nums = re.findall(r"[-+]?\d*\.\d+|\d+", b)
                val = float(nums[0]) if nums else 0
            else:
                val = float(b)
            if val > 1024 * 1024:
                if val < 1024**3: return f"{val/1024**2:.1f} MB"
                return f"{val/1024**3:.1f} GB"
            if val > 0: return f"{val:.1f} GB"
            return "0 B"
        except: return str(b)

    def fmt_traffic(b): return f"{round(b/1024**3, 1)}G" if b > 1024**3 else f"{int(b/1024**2)}M"
    def fmt_speed(b): return f"{int(b)} B" if b < 1024 else (f"{int(b/1024)} K" if b < 1024**2 else f"{int(b/1024**2)} M")

    try:
        chart_data, pie_data, region_count, region_stats_json, centroids_json = prepare_map_data()
    except Exception as e:
        chart_data = '{"cities": [], "flags": [], "regions": []}'
        pie_data = []; region_count = 0; region_stats_json = "{}"; centroids_json = "{}"

    # ================= UI 布局 =================
    with ui.element('div').classes('fixed top-0 left-0 w-full h-[35vh] min-h-[300px] max-h-[500px] z-0').style('z-index: 0; contain: size layout paint;'): 
        ui.html('<div id="public-map-container" style="width:100%; height:100%;"></div>', sanitize=False).classes('w-full h-full')

    with ui.column().classes('w-full h-screen p-0 gap-0 overflow-hidden flex flex-col absolute top-0 left-0 pointer-events-none'):
        with ui.element('div').classes('w-full h-[35vh] min-h-[300px] max-h-[500px] relative p-0 shrink-0 pointer-events-none'):
            with ui.row().classes('absolute top-6 left-8 right-8 z-50 justify-between items-start pointer-events-auto'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('public', color='blue').classes('text-3xl drop-shadow-[0_0_10px_rgba(59,130,246,0.8)]')
                        ui.label('X-Fusion Status').classes('text-2xl font-black text-slate-800 dark:text-white drop-shadow-md')
                    with ui.row().classes('gap-4 text-sm font-bold font-mono pl-1'):
                        with ui.row().classes('items-center gap-1'):
                            ui.element('div').classes('w-2 h-2 rounded-full bg-green-500 shadow-[0_0_5px_rgba(34,197,94,0.8)]')
                            header_refs['online_count'] = ui.label('在线: --').classes('text-slate-600 dark:text-slate-300 drop-shadow-sm')
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('language').classes('text-blue-500 dark:text-blue-400 text-xs drop-shadow-sm')
                            header_refs['region_count'] = ui.label(f'分布区域: {region_count}').classes('text-slate-600 dark:text-slate-300 drop-shadow-sm')
                with ui.row().classes('items-center gap-2'):
                    def toggle_dark():
                        dark_mode.value = not dark_mode.value
                        app.storage.user['is_dark'] = dark_mode.value
                        if pie_chart_ref:
                            color = '#e2e8f0' if dark_mode.value else '#334155'
                            pie_chart_ref.options['legend']['textStyle']['color'] = color
                            pie_chart_ref.update()
                        ui.run_javascript(f'if(window.changeTheme) window.changeTheme({str(dark_mode.value).lower()});')
                    ui.button(icon='dark_mode', on_click=toggle_dark).props('flat round dense').classes('text-slate-700 dark:text-yellow-400 bg-white/50')
                    ui.button('后台管理', icon='login', on_click=lambda: ui.navigate.to('/login')).props('flat dense').classes('font-bold text-xs text-slate-700 dark:text-slate-300 bg-white/50 rounded px-2')
            with ui.element('div').classes('absolute left-4 bottom-4 z-40 pointer-events-auto'):
                text_color = '#e2e8f0' if dark_mode.value else '#334155'
                pie_chart_ref = ui.echart({'backgroundColor': 'transparent', 'tooltip': {'trigger': 'item'}, 'legend': {'bottom': '0%', 'left': 'center', 'icon': 'circle', 'itemGap': 15, 'textStyle': {'color': text_color, 'fontSize': 11}}, 'series': [{'type': 'pie', 'radius': ['35%', '60%'], 'center': ['50%', '35%'], 'avoidLabelOverlap': False, 'itemStyle': {'borderRadius': 4, 'borderColor': 'transparent', 'borderWidth': 2}, 'label': {'show': False}, 'emphasis': {'scale': True, 'scaleSize': 10, 'label': {'show': True, 'color': 'auto', 'fontWeight': 'bold'}, 'itemStyle': {'shadowBlur': 10, 'shadowOffsetX': 0, 'shadowColor': 'rgba(0, 0, 0, 0.5)'}}, 'data': pie_data}]}).classes('w-64 h-72')

        with ui.column().classes('w-full flex-grow relative gap-0 overflow-hidden flex flex-col bg-white/80 dark:bg-[#0f172a]/90 backdrop-blur-xl pointer-events-auto border-t border-white/10').style('z-index: 10; contain: content;'): 
            with ui.row().classes('w-full px-6 py-2 border-b border-gray-200/50 dark:border-gray-800 items-center shrink-0 justify-between'):
                with ui.element('div').classes('flex-grow overflow-x-auto whitespace-nowrap scrollbar-hide mr-4') as tab_container: pass 
                pagination_ref = ui.row().classes('items-center')

            with ui.scroll_area().classes('w-full flex-grow p-4 md:p-6'):
                grid_container = ui.grid().classes('w-full gap-4 md:gap-5 pb-20').style('grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))')

    # ================= 渲染逻辑 (含分页) =================
    
    def render_tabs():
        tab_container.clear()
        groups = get_probe_groups(); global CURRENT_PROBE_TAB 
        if CURRENT_PROBE_TAB not in groups: CURRENT_PROBE_TAB = 'ALL'
        page_state['group'] = CURRENT_PROBE_TAB
        
        with tab_container:
            with ui.tabs().props('dense no-caps align=left active-color=blue indicator-color=blue').classes('text-slate-600 dark:text-gray-500 bg-transparent') as tabs:
                ui.tab('ALL', label='全部').on('click', lambda: apply_filter('ALL'))
                for g in groups:
                    if g == 'ALL': continue
                    ui.tab(g).on('click', lambda _, g=g: apply_filter(g))
                tabs.set_value(CURRENT_PROBE_TAB)

    # ================= ✨✨✨ 优化后的卡片渲染与更新逻辑 ✨✨✨ =================

    # 1. 抽离出的通用 UI 更新函数 (用于：1.创建时立即回显缓存 2.定时任务更新)
    def update_card_ui(refs, status, static):
        if not status: return
        
        is_probe_online = (status.get('status') == 'online')
        
        if is_probe_online:
            refs['status_icon'].set_name('bolt'); refs['status_icon'].classes(replace='text-green-500', remove='text-gray-400 text-red-500 text-purple-400')
            refs['online_dot'].classes(replace='bg-green-500', remove='bg-gray-500 bg-red-500 bg-purple-500')
        else:
            if status.get('cpu_usage') is not None:
                refs['status_icon'].set_name('api'); refs['status_icon'].classes(replace='text-purple-400', remove='text-gray-400 text-red-500 text-green-500')
                refs['online_dot'].classes(replace='bg-purple-500', remove='bg-gray-500 bg-red-500 bg-green-500')
            else:
                refs['status_icon'].set_name('flash_off'); refs['status_icon'].classes(replace='text-red-500', remove='text-green-500 text-gray-400 text-purple-400')
                refs['online_dot'].classes(replace='bg-red-500', remove='bg-green-500 bg-orange-500 bg-purple-500')

        os_str = static.get('os', 'Linux')
        import re
        simple_os = re.sub(r' GNU/Linux', '', os_str, flags=re.I)
        refs['os_info'].set_text(f"{simple_os}")
        
        cores = status.get('cpu_cores')
        refs['summary_cores'].set_text(f"{cores} C" if cores else "N/A")
        refs['summary_ram'].set_text(fmt_capacity(status.get('mem_total', 0)))
        refs['summary_disk'].set_text(fmt_capacity(status.get('disk_total', 0)))
        
        refs['traf_up'].set_text(f"↑ {fmt_traffic(status.get('net_total_out', 0))}")
        refs['traf_down'].set_text(f"↓ {fmt_traffic(status.get('net_total_in', 0))}")

        cpu = float(status.get('cpu_usage', 0))
        refs['cpu_bar'].style(f'width: {cpu}%'); refs['cpu_pct'].set_text(f'{cpu:.1f}%')
        c_num = status.get('cpu_cores', 1); refs['cpu_sub'].set_text(f"{c_num} Cores")
        
        mem = float(status.get('mem_usage', 0))
        refs['mem_bar'].style(f'width: {mem}%'); refs['mem_pct'].set_text(f'{mem:.1f}%')
        mem_total = float(status.get('mem_total', 0))
        if mem_total > 0:
            mem_val_used = mem_total * (mem / 100.0)
            refs['mem_sub'].set_text(f"{fmt_capacity(mem_val_used)} / {fmt_capacity(mem_total)}")
        else: refs['mem_sub'].set_text(f"{mem:.1f}%")

        disk = float(status.get('disk_usage', 0))
        refs['disk_bar'].style(f'width: {disk}%'); refs['disk_pct'].set_text(f'{disk:.1f}%')
        disk_total = float(status.get('disk_total', 0))
        if disk_total > 0:
            disk_val_used = disk_total * (disk / 100.0)
            refs['disk_sub'].set_text(f"{fmt_capacity(disk_val_used)} / {fmt_capacity(disk_total)}")
        else: refs['disk_sub'].set_text(f"{disk:.1f}%")

        n_up = status.get('net_speed_out', 0); n_down = status.get('net_speed_in', 0)
        refs['net_up'].set_text(f"↑ {fmt_speed(n_up)}/s"); refs['net_down'].set_text(f"↓ {fmt_speed(n_down)}/s")

        up = str(status.get('uptime', '-'))
        colored_up = re.sub(r'(\d+)(\s*(?:days?|天))', r'<span class="text-green-500 font-bold text-sm">\1</span>\2', up, flags=re.IGNORECASE)
        refs['uptime'].set_content(colored_up)

# 2. 自动更新循环 
    async def card_autoupdate_loop(url):
        # 获取服务器配置
        current_server = next((s for s in SERVERS_CACHE if s['url'] == url), None)
        if not current_server: return

        # 判断是否安装了探针
        is_probe = current_server.get('probe_installed', False)

        # 🛑 如果没有安装探针，直接结束此协程！
        # 这样前端卡片就不会每分钟去骚扰后台了
        if not is_probe:
            return 

        # --- 首次启动延迟 ---
        await asyncio.sleep(random.uniform(0.5, 3.0))
        
        while True:
            # --- 基础检查 ---
            if url not in RENDERED_CARDS: break 
            if url not in [s['url'] for s in SERVERS_CACHE]: break
            
            item = RENDERED_CARDS.get(url)
            if not item: break 
            
            # 省流模式：标签页不可见时暂停
            if not item['card'].visible: 
                await asyncio.sleep(5.0) 
                continue 
                    
            # 执行获取数据
            current_server = next((s for s in SERVERS_CACHE if s['url'] == url), None)
            if current_server:
                res = None
                try: 
                    res = await asyncio.wait_for(get_server_status(current_server), timeout=5.0)
                except: res = None
                
                if res:
                    raw_cache = PROBE_DATA_CACHE.get(url, {})
                    static = raw_cache.get('static', {})
                    update_card_ui(item['refs'], res, static)
                    
                    is_online = (res.get('status') == 'online')
                    if is_online: item['card'].classes(remove='offline-card')
                    else: item['card'].classes(add='offline-card')

            # 探针刷新间隔
            await asyncio.sleep(random.uniform(2.0, 3.0))

    # 3. 创建卡片 (✨✨✨ 创建时立即回显 ✨✨✨)
    def create_server_card(s):
        url = s['url']; refs = {}
        
        cached_data = PROBE_DATA_CACHE.get(url, {})
        initial_status = None
        if cached_data:
            initial_status = cached_data.copy()
            if 'pings' not in initial_status: initial_status['pings'] = {}
        
        with grid_container:
            with ui.card().classes('status-card w-full p-4 md:p-5 flex flex-col gap-2 md:gap-3 relative overflow-hidden group').style('contain: content;') as card:
                refs['card'] = card
                with ui.row().classes('w-full items-center mb-1 gap-2 flex-nowrap'):
                    flag = "🏳️"; 
                    try: flag = detect_country_group(s['name'], s).split(' ')[0]
                    except: pass
                    ui.label(flag).classes('text-2xl md:text-3xl flex-shrink-0 leading-none') 
                    ui.label(s['name']).classes('text-base md:text-lg font-bold text-slate-800 dark:text-gray-100 truncate flex-grow min-w-0 cursor-pointer hover:text-blue-500 transition leading-tight').on('click', lambda _, s=s: open_pc_server_detail(s))
                    refs['status_icon'] = ui.icon('bolt').props('size=32px').classes('text-gray-400 flex-shrink-0')
                with ui.row().classes('w-full justify-between items-center px-1 mb-2'):
                    with ui.row().classes('items-center gap-1.5'):
                        ui.icon('dns').classes('text-xs text-gray-400'); ui.label('OS').classes('text-xs text-slate-500 dark:text-gray-400 font-bold')
                    with ui.row().classes('items-center gap-1.5'):
                        refs['os_icon'] = ui.icon('computer').classes('text-xs text-slate-400'); refs['os_info'] = ui.label('Loading...').classes('text-xs font-mono font-bold text-slate-700 dark:text-gray-300 whitespace-nowrap')
                ui.separator().classes('mb-3 opacity-50 dark:opacity-30')
                with ui.row().classes('w-full justify-between px-1 mb-1 md:mb-2'):
                    label_cls = 'text-xs font-mono text-slate-500 dark:text-gray-400 font-bold'
                    with ui.row().classes('items-center gap-1'): ui.icon('grid_view').classes('text-blue-500 dark:text-blue-400 text-xs'); refs['summary_cores'] = ui.label('--').classes(label_cls)
                    with ui.row().classes('items-center gap-1'): ui.icon('memory').classes('text-green-500 dark:text-green-400 text-xs'); refs['summary_ram'] = ui.label('--').classes(label_cls)
                    with ui.row().classes('items-center gap-1'): ui.icon('storage').classes('text-purple-500 dark:text-purple-400 text-xs'); refs['summary_disk'] = ui.label('--').classes(label_cls)
                with ui.column().classes('w-full gap-2 md:gap-3'):
                    def stat_row(label, color_cls, light_track_color):
                        with ui.column().classes('w-full gap-1'):
                            with ui.row().classes('w-full items-center justify-between'):
                                ui.label(label).classes('text-xs text-slate-500 dark:text-gray-500 font-bold w-8')
                                with ui.element('div').classes(f'flex-grow h-2 md:h-2.5 bg-{light_track_color} dark:bg-gray-700/50 rounded-full overflow-hidden mx-2 transition-colors'):
                                    bar = ui.element('div').classes(f'h-full {color_cls} prog-bar').style('width: 0%')
                                pct = ui.label('0%').classes('text-xs font-mono font-bold text-slate-700 dark:text-white w-8 text-right')
                            sub = ui.label('').classes('text-[10px] text-slate-400 dark:text-gray-500 font-mono text-right w-full pr-1')
                        return bar, pct, sub
                    refs['cpu_bar'], refs['cpu_pct'], refs['cpu_sub'] = stat_row('CPU', 'bg-blue-500', 'blue-100')
                    refs['mem_bar'], refs['mem_pct'], refs['mem_sub'] = stat_row('内存', 'bg-green-500', 'green-100')
                    refs['disk_bar'], refs['disk_pct'], refs['disk_sub'] = stat_row('硬盘', 'bg-purple-500', 'purple-100')
                ui.separator().classes('bg-slate-200 dark:bg-white/5 my-1')
                with ui.column().classes('w-full gap-1'):
                    label_sub_cls = 'text-xs text-slate-400 dark:text-gray-500'
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('网络').classes(label_sub_cls); 
                        with ui.row().classes('gap-2 font-mono whitespace-nowrap'): refs['net_up'] = ui.label('↑ 0B').classes('text-xs text-orange-500 dark:text-orange-400 font-bold'); refs['net_down'] = ui.label('↓ 0B').classes('text-xs text-green-600 dark:text-green-400 font-bold')
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('流量').classes(label_sub_cls)
                        with ui.row().classes('gap-2 font-mono whitespace-nowrap text-xs text-slate-600 dark:text-gray-300'): refs['traf_up'] = ui.label('↑ 0B'); refs['traf_down'] = ui.label('↓ 0B')
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('在线').classes(label_sub_cls)
                        with ui.row().classes('items-center gap-1'): refs['uptime'] = ui.html('--', sanitize=False).classes('text-xs font-mono text-slate-600 dark:text-gray-300 text-right'); refs['online_dot'] = ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-gray-400')

        # ✨✨✨ 立即应用缓存数据 (防止页面白屏闪烁) ✨✨✨
        if initial_status:
            static = cached_data.get('static', {})
            update_card_ui(refs, initial_status, static)
            is_cached_online = (initial_status.get('status') == 'online') or (initial_status.get('cpu_usage') is not None)
            if is_cached_online: card.classes(remove='offline-card')
            else: card.classes(add='offline-card')

        RENDERED_CARDS[url] = {'card': card, 'refs': refs, 'data': s}
        asyncio.create_task(card_autoupdate_loop(url))

    def apply_filter(group_name):
        global CURRENT_PROBE_TAB; CURRENT_PROBE_TAB = group_name
        page_state['group'] = group_name
        page_state['page'] = 1 
        render_grid_page()

    def change_page(new_page):
        page_state['page'] = new_page
        render_grid_page()

    # ================= 核心：分页渲染逻辑  =================
    def render_grid_page():
        grid_container.clear()
        pagination_ref.clear()
        RENDERED_CARDS.clear()

        group_name = page_state['group']
        filtered_servers = []
        try: sorted_all = sorted(SERVERS_CACHE, key=lambda x: x.get('name', ''))
        except: sorted_all = SERVERS_CACHE
        
        for s in sorted_all:
            if group_name == 'ALL' or (group_name in s.get('tags', [])):
                filtered_servers.append(s)

        PAGE_SIZE = 60
        total_items = len(filtered_servers)
        total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
        if page_state['page'] > total_pages: page_state['page'] = 1
        if page_state['page'] < 1: page_state['page'] = 1
        
        start_idx = (page_state['page'] - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_items = filtered_servers[start_idx:end_idx]

        if not current_page_items:
            with grid_container:
                ui.label('暂无服务器').classes('text-gray-500 dark:text-gray-400 col-span-full text-center mt-10')
        else:
            for s in current_page_items:
                create_server_card(s)

        if total_pages > 1:
            with pagination_ref:
                p = ui.pagination(1, total_pages, direction_links=True).props('dense color=blue outline rounded text-color=white active-color=blue active-text-color=white max-pages=7')
                p.value = page_state['page']
                p.on('update:model-value', lambda e: change_page(e.args))
                ui.label(f'共 {total_items} 台').classes('text-xs text-gray-400 ml-4 self-center')

    render_tabs()
    render_grid_page()
    
    # [JS 逻辑注入] 地图渲染 + 修复字体样式 + 调整悬浮窗宽度 + 区域高亮修复 
    ui.run_javascript(f'''
    (function() {{
        var mapData = {chart_data}; 
        window.regionStats = {region_stats_json}; 
        window.countryCentroids = {centroids_json}; 
        
        var defaultPt = [116.40, 39.90]; 
        var defaultZoom = 1.35; 
        var focusedZoom = 4.0; 
        var isZoomed = false; 
        var myChart = null;

        function tryIpLocation() {{
            fetch('https://ipapi.co/json/')
                .then(response => response.json())
                .then(data => {{
                    if(data.latitude && data.longitude) {{
                        defaultPt = [data.longitude, data.latitude];
                        if(!isZoomed && myChart) renderMap();
                    }}
                }})
                .catch(e => {{}});
        }}

        function checkAndRender() {{
            var chartDom = document.getElementById('public-map-container');
            if (!chartDom || typeof echarts === 'undefined') {{ setTimeout(checkAndRender, 100); return; }}
            
            fetch('/static/world.json').then(r => r.json()).then(w => {{
                echarts.registerMap('world', w); 
                myChart = echarts.init(chartDom); 
                window.publicMapChart = myChart; 
                
                if (navigator.geolocation) {{ 
                    navigator.geolocation.getCurrentPosition(
                        p => {{ 
                            defaultPt = [p.coords.longitude, p.coords.latitude]; 
                            if(!isZoomed) renderMap(); 
                        }},
                        e => {{ tryIpLocation(); }}
                    ); 
                }} else {{ tryIpLocation(); }}
                
                renderMap();
                
                function renderMap(center, zoomLevel, roamState) {{
                    var viewCenter = center || defaultPt;
                    var viewZoom = zoomLevel || defaultZoom;
                    var viewRoam = roamState !== undefined ? roamState : false;
                    var mapLeft = isZoomed ? 'center' : '55%'; 
                    var mapTop = '1%';

                    var lines = mapData.cities.map(pt => ({{ coords: [pt.value, defaultPt] }}));
                    
                    var isDark = document.body.classList.contains('body--dark');
                    var areaColor = isDark ? '#1B2631' : '#e0e7ff'; 
                    var borderColor = isDark ? '#404a59' : '#a5b4fc'; 
                    
                    // 双色主题定义
                    var ttBg = isDark ? 'rgba(23, 23, 23, 0.95)' : 'rgba(255, 255, 255, 0.95)';
                    var ttTextMain = isDark ? '#fff' : '#1e293b';
                    var ttTextSub = isDark ? 'rgba(255, 255, 255, 0.6)' : 'rgba(30, 41, 59, 0.6)';
                    var ttBorder = isDark ? '1px solid rgba(255,255,255,0.1)' : '1px solid #e2e8f0';

                    // 字体优化
                    var emojiFont = "'Twemoji Country Flags', 'Noto Sans SC', 'Roboto', 'Helvetica Neue', 'Arial', sans-serif";

                    // ✨✨✨ [核心修复]：构建高亮区域配置 ✨✨✨
                    var highlightFill = isDark ? 'rgba(37, 99, 235, 0.4)' : 'rgba(147, 197, 253, 0.5)'; // 蓝色半透明
                    var highlightStroke = isDark ? '#3b82f6' : '#2563eb'; // 边框颜色
                    
                    var activeRegions = mapData.regions || [];
                    var geoRegions = activeRegions.map(function(name) {{
                        return {{
                            name: name,
                            itemStyle: {{ 
                                areaColor: highlightFill, 
                                borderColor: highlightStroke,
                                borderWidth: 1.5,
                                opacity: 1
                            }},
                            emphasis: {{
                                itemStyle: {{
                                    areaColor: highlightFill,
                                    borderColor: '#60a5fa',
                                    borderWidth: 2
                                }}
                            }}
                        }};
                    }});

                    var option = {{
                        backgroundColor: 'transparent',
                        tooltip: {{
                            show: true, trigger: 'item', padding: 0, backgroundColor: 'transparent', borderColor: 'transparent',
                            formatter: function(params) {{
                                var searchKey = params.name;
                                if (params.data && params.data.country_key) searchKey = params.data.country_key;
                                var stats = window.regionStats[searchKey];
                                if (!stats) return; // 没有数据的区域不显示弹窗
                                
                                var serverListHtml = '';
                                var displayLimit = 5; 
                                var servers = stats.servers || []; 
                                
                                for (var i = 0; i < Math.min(servers.length, displayLimit); i++) {{
                                    var s = servers[i];
                                    var isOnline = s.status === 'online';
                                    var statusColor = isOnline ? '#22c55e' : '#ef4444'; 
                                    var statusText = isOnline ? '在线' : '离线';
                                    
                                    serverListHtml += `
                                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; line-height: 1.2;">
                                            <div style="display: flex; align-items: center; max-width: 170px;">
                                                <span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background-color: ${{statusColor}}; margin-right: 8px; flex-shrink: 0;"></span>
                                                <span style="font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${{s.name}}</span>
                                            </div>
                                            <span style="font-size: 12px; color: ${{ttTextSub}}; flex-shrink: 0; margin-left: 8px;">${{statusText}}</span>
                                        </div>
                                    `;
                                }}
                                
                                if (servers.length > displayLimit) {{
                                    serverListHtml += `<div style="font-size: 11px; color: ${{ttTextSub}}; margin-top: 8px; text-align: right; opacity: 0.8;">+${{servers.length - displayLimit}} 更多...</div>`;
                                }}
                                
                                return `<div style="background:${{ttBg}}; border:${{ttBorder}}; padding: 14px 16px; border-radius: 10px; color:${{ttTextMain}}; font-family: ${{emojiFont}}; box-shadow: 0 4px 16px rgba(0,0,0,0.3); min-width: 240px; max-width: 260px; pointer-events: none;">
                                    <div style="font-size: 16px; font-weight: 700; margin-bottom: 2px; display: flex; align-items: center; letter-spacing: 0.5px;">
                                        <span style="margin-right: 8px; font-size: 20px;">${{stats.flag}}</span>${{stats.cn}}
                                    </div>
                                    <div style="font-size: 12px; color: ${{ttTextSub}}; margin-bottom: 12px; font-weight: 400;">
                                        共 ${{stats.total}} 台服务器, ${{stats.online}} 台在线
                                    </div>
                                    <div style="border-top: 1px solid ${{isDark ? 'rgba(255,255,255,0.08)' : '#f1f5f9'}}; padding-top: 10px; margin-top: 4px;">
                                        ${{serverListHtml}}
                                    </div>
                                </div>`;
                            }}
                        }},
                        geo: {{
                            map: 'world', left: mapLeft, top: mapTop, roam: viewRoam, zoom: viewZoom, center: viewCenter,
                            aspectScale: 0.85, label: {{ show: false }},
                            itemStyle: {{ areaColor: areaColor, borderColor: borderColor, borderWidth: 1 }},
                            emphasis: {{ itemStyle: {{ areaColor: isDark ? '#1e3a8a' : '#bfdbfe' }} }},
                            
                            // 🛑 核心修复：注入区域高亮配置
                            regions: geoRegions 
                        }},
                        series: [
                            {{ type: 'lines', zlevel: 2, effect: {{ show: true, period: 4, trailLength: 0.5, color: '#00ffff', symbol: 'arrow', symbolSize: 6 }}, lineStyle: {{ color: '#00ffff', width: 0, curveness: 0.2, opacity: 0 }}, data: lines, silent: true }},
                            {{ type: 'effectScatter', coordinateSystem: 'geo', zlevel: 3, rippleEffect: {{ brushType: 'stroke', scale: 2.5 }}, itemStyle: {{ color: '#00ffff' }}, data: mapData.cities }},
                            
                            {{ 
                                type: 'scatter', coordinateSystem: 'geo', zlevel: 6, symbolSize: 0, 
                                label: {{ 
                                    show: true, position: 'top', formatter: '{{b}}', 
                                    color: isDark?'#fff':'#1e293b', fontSize: 16, offset: [0, -5],
                                    fontFamily: emojiFont 
                                }}, 
                                data: mapData.flags 
                            }},
                            
                            {{ type: 'effectScatter', coordinateSystem: 'geo', zlevel: 5, itemStyle: {{ color: '#f59e0b' }}, label: {{ show: true, position: 'bottom', formatter: 'My PC', color: '#f59e0b', fontWeight: 'bold' }}, data: [{{ value: defaultPt }}] }}
                        ]
                    }};
                    myChart.setOption(option, true);
                }}
                
                window.updatePublicMap = function(newData) {{ 
                    if (!newData) return; mapData = newData; 
                    renderMap(isZoomed ? myChart.getOption().geo[0].center : defaultPt, isZoomed ? myChart.getOption().geo[0].zoom : defaultZoom, isZoomed ? 'move' : false); 
                }};
                
                myChart.on('click', function(params) {{
                    var searchKey = params.name;
                    if (params.data && params.data.country_key) searchKey = params.data.country_key;
                    var targetCoord = window.countryCentroids[searchKey];
                    if (targetCoord) {{ isZoomed = true; renderMap(targetCoord, focusedZoom, 'move'); }}
                }});
                
                myChart.getZr().on('mousewheel', function() {{ if(isZoomed) {{ isZoomed = false; renderMap(defaultPt, defaultZoom, false); }} }});
                window.changeTheme = function(isDark) {{ renderMap(undefined, undefined, undefined); }}; 
                window.addEventListener('resize', () => myChart.resize());
            }});
        }}
        checkAndRender();
    }})();
    ''')
    # ================= 循环更新逻辑  =================
    async def loop_update():
        nonlocal local_ui_version
        try:
            # 1. 检查版本号，如果变动则重绘架构 
            if GLOBAL_UI_VERSION != local_ui_version:
                local_ui_version = GLOBAL_UI_VERSION
                render_tabs(); render_grid_page() 
                try: new_map, _, new_cnt, new_stats, new_centroids = prepare_map_data()
                except: new_map = "{}"; new_cnt = 0; new_stats = "{}"; new_centroids = "{}"
                if header_refs.get('region_count'): header_refs['region_count'].set_text(f'分布区域: {new_cnt}')
                ui.run_javascript(f'''if(window.updatePublicMap){{ window.regionStats = {new_stats}; window.countryCentroids = {new_centroids}; window.updatePublicMap({new_map}); }}''')
            
            # 2.实时统计在线数量
            real_online_count = 0
            now_ts = time.time()
            
            for s in SERVERS_CACHE:
                is_node_online = False
                
                # A. 优先检查探针心跳 (20秒内有更新算在线)
                probe_cache = PROBE_DATA_CACHE.get(s['url'])
                if probe_cache and (now_ts - probe_cache.get('last_updated', 0) < 20):
                    is_node_online = True
                
                # B. 兼容旧状态字段 (如果探针没在线，看下系统标记)
                elif s.get('_status') == 'online':
                    is_node_online = True
                
                if is_node_online:
                    real_online_count += 1

            # 3. 更新 UI 文字
            if header_refs.get('online_count'): 
                header_refs['online_count'].set_text(f'在线: {real_online_count}')
                
        except Exception as e: 
            pass # 忽略临时错误
            
        ui.timer(5.0, loop_update, once=True)

    ui.timer(0.1, loop_update, once=True)