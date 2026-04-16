async def render_mobile_status_page():
    global CURRENT_PROBE_TAB
    # 用于存储 UI 组件引用的字典，实现局部刷新
    mobile_refs = {}

    # 1. 注入复刻样式的 CSS
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
        <style>
            body { background-color: #0d0d0d; color: #ffffff; margin: 0; padding: 0; overflow-x: hidden; }
            .mobile-header { background: #1a1a1a; border-bottom: 1px solid #333; position: sticky; top: 0; z-index: 100; padding: 12px 16px; }
            .mobile-card-container { display: flex; flex-direction: column; align-items: center; width: 100%; padding: 12px 0; }
            .mobile-card { 
                background: #1a1a1a; border-radius: 16px; padding: 18px; border: 1px solid #333;
                width: calc(100% - 24px); margin-bottom: 16px; box-sizing: border-box;
            }
            .inner-module {
                background: #242424; border-radius: 12px; padding: 12px; height: 95px;
                display: flex; flex-direction: column; justify-content: space-between;
            }
            .stat-header { display: flex; justify-content: space-between; align-items: center; }
            .stat-label-box { display: flex; align-items: center; gap: 4px; }
            .stat-icon { font-size: 14px !important; color: #888; }
            .stat-label { color: #888; font-size: 11px; font-weight: bold; }
            .stat-value { color: #fff; font-size: 17px; font-weight: 800; font-family: monospace; }
            .bar-bg { height: 5px; background: #333; border-radius: 3px; overflow: hidden; margin: 2px 0; }
            .bar-fill-cpu { height: 100%; background: #3b82f6; transition: width 0.6s; box-shadow: 0 0 5px #3b82f6; }
            .bar-fill-mem { height: 100%; background: #22c55e; transition: width 0.6s; box-shadow: 0 0 5px #22c55e; }
            .bar-fill-disk { height: 100%; background: #a855f7; }
            .stat-subtext { color: #555; font-size: 10px; font-family: monospace; font-weight: bold; }
            .speed-up { color: #22c55e; font-weight: bold; font-size: 11px; }
            .speed-down { color: #3b82f6; font-weight: bold; font-size: 11px; }
            .scrollbar-hide::-webkit-scrollbar { display: none; }
        </style>
    ''')

    # --- 2. 顶部与标签栏 ---
    with ui.column().classes('mobile-header w-full gap-1'):
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('X-Fusion Status').classes('text-lg font-black text-blue-400')
            ui.button(icon='login', on_click=lambda: ui.navigate.to('/login')).props('flat dense color=grey-5')
        online_count = len([s for s in SERVERS_CACHE if s.get('_status') == 'online'])
        ui.label(f'🟢 {online_count} ONLINE / {len(SERVERS_CACHE)} TOTAL').classes('text-[10px] font-bold text-gray-500 tracking-widest')

    with ui.row().classes('w-full px-2 py-1 bg-[#0d0d0d] border-b border-[#333] overflow-x-auto whitespace-nowrap scrollbar-hide'):
        groups = ['ALL'] + ADMIN_CONFIG.get('probe_custom_groups', [])
        with ui.tabs().props('dense no-caps active-color=blue-400 indicator-color=blue-400').classes('text-gray-500') as tabs:
            for g in groups:
                ui.tab(g, label='全部' if g=='ALL' else g).on('click', lambda _, group=g: update_mobile_tab(group))
            tabs.set_value(CURRENT_PROBE_TAB)

    list_container = ui.column().classes('mobile-card-container')

    # --- 3. 渲染函数 ---
    async def render_list(target_group):
        list_container.clear()
        mobile_refs.clear()
        
        filtered = [s for s in SERVERS_CACHE if target_group == 'ALL' or target_group in s.get('tags', [])]
        filtered.sort(key=lambda x: (0 if x.get('_status')=='online' else 1, x.get('name', '')))

        with list_container:
            for s in filtered:
                status = PROBE_DATA_CACHE.get(s['url'], {})
                static = status.get('static', {})
                is_online = s.get('_status') == 'online'
                srv_ref = {}
                
                with ui.column().classes('mobile-card').on('click', lambda _, srv=s: open_mobile_server_detail(srv)):
                    # 标题与描述
                    with ui.row().classes('items-center gap-3 mb-3'):
                        flag = "🏳️"
                        try: flag = detect_country_group(s['name'], s).split(' ')[0]
                        except: pass
                        ui.label(flag).classes('text-3xl')
                        ui.label(s['name']).classes('text-base font-bold truncate').style('max-width:200px')

                    # 2x2 宫格布局
                    with ui.grid().classes('w-full grid-cols-2 gap-3'):
                        # CPU 模块
                        cpu = status.get('cpu_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">settings_suggest</span><span class="stat-label">CPU</span></div>', sanitize=False)
                                srv_ref['cpu_text'] = ui.label(f'{cpu}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                srv_ref['cpu_bar'] = ui.element('div').classes('bar-fill-cpu').style(f'width: {cpu}%')
                            ui.label(f"{status.get('cpu_cores', 1)} Cores").classes('stat-subtext')

                        # RAM 模块
                        mem_p = status.get('mem_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">memory</span><span class="stat-label">RAM</span></div>', sanitize=False)
                                srv_ref['mem_text'] = ui.label(f'{int(mem_p)}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                srv_ref['mem_bar'] = ui.element('div').classes('bar-fill-mem').style(f'width: {mem_p}%')
                            srv_ref['mem_detail'] = ui.label('-- / --').classes('stat-subtext')

                        # DISK 模块
                        disk_p = status.get('disk_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">storage</span><span class="stat-label">DISK</span></div>', sanitize=False)
                                ui.label(f'{int(disk_p)}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                ui.element('div').classes('bar-fill-disk').style(f'width: {disk_p}%')
                            ui.label(f"{status.get('disk_total', 0)}G Total").classes('stat-subtext')

                        # SPEED 模块
                        with ui.element('div').classes('inner-module'):
                            ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">swap_calls</span><span class="stat-label">SPEED</span></div>', sanitize=False)
                            with ui.column().classes('w-full gap-0'):
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('↑').classes('speed-up')
                                    srv_ref['net_up'] = ui.label('--').classes('text-[12px] font-mono font-bold')
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('↓').classes('speed-down')
                                    srv_ref['net_down'] = ui.label('--').classes('text-[12px] font-mono font-bold')

                    # 底部状态
                    with ui.row().classes('w-full justify-between mt-3 pt-2 border-t border-[#333] items-center'):
                        # 修改点：左侧显示绿色加粗的在线时长
                        srv_ref['uptime'] = ui.label("在线时长：--").classes('text-[10px] font-bold text-green-500 font-mono')
                        with ui.row().classes('items-center gap-2'):
                            # 修改点：闪电图标引用 srv_ref['load']，动态展示 load_1 数据
                            srv_ref['load'] = ui.label(f"⚡ {status.get('load_1', '0.0')}").classes('text-[10px] text-gray-400 font-bold')
                            ui.label('ACTIVE' if is_online else 'DOWN').classes(f'text-[10px] font-black {"text-green-500" if is_online else "text-red-400"}')
                
                mobile_refs[s['url']] = srv_ref

    # --- 4. 实时同步逻辑 ---
    def fmt_speed(b):
        if b < 1024: return f"{int(b)}B"
        return f"{int(b/1024)}K" if b < 1024**2 else f"{round(b/1024**2,1)}M"

    async def mobile_sync_loop():
        for url, refs in mobile_refs.items():
            status = PROBE_DATA_CACHE.get(url, {})
            if not status: continue
            
            # 更新网速
            refs['net_up'].set_text(f"{fmt_speed(status.get('net_speed_out', 0))}/s")
            refs['net_down'].set_text(f"{fmt_speed(status.get('net_speed_in', 0))}/s")
            
            # 更新 CPU & RAM
            cpu = status.get('cpu_usage', 0)
            mem_p = status.get('mem_usage', 0)
            refs['cpu_text'].set_text(f"{cpu}%")
            refs['cpu_bar'].style(f"width: {cpu}%")
            refs['mem_text'].set_text(f"{int(mem_p)}%")
            refs['mem_bar'].style(f"width: {mem_p}%")
            
            # 内存详情
            mem_t = status.get('mem_total', 0)
            mem_u = round(float(mem_t or 0) * (float(mem_p or 0)/100), 2)
            refs['mem_detail'].set_text(f"{mem_u}G / {mem_t}G")
            
            # Uptime 格式化处理：将 "up 81 days, 11:08" 转换为 "在线时长：81天 11时 8分"
            raw_uptime = str(status.get('uptime', '-'))
            formatted_uptime = raw_uptime.replace('up ', '').replace(' days, ', '天 ').replace(' day, ', '天 ')
            if ':' in formatted_uptime:
                parts = formatted_uptime.split(' ')
                time_parts = parts[-1].split(':')
                h = time_parts[0]
                m = time_parts[1]
                # 重新拼接
                prefix = "".join(parts[:-1])
                formatted_uptime = f"{prefix}{h}时 {m}分"
            
            refs['uptime'].set_text(f"在线时长：{formatted_uptime}")
            
            # Load 更新：显示实时负载数据
            refs['load'].set_text(f"⚡ {status.get('load_1', '0.0')}")

    async def update_mobile_tab(val):
        global CURRENT_PROBE_TAB
        CURRENT_PROBE_TAB = val
        await render_list(val)

    await render_list(CURRENT_PROBE_TAB)
    ui.timer(5.0, mobile_sync_loop)