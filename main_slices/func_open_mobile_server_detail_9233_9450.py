def open_mobile_server_detail(server_conf):
    # 注入 CSS
    ui.add_head_html('''
        <style>
            .full-height-dialog { height: 85vh !important; max-height: 95vh !important; }
            @media (orientation: landscape) { .full-height-dialog { height: 95vh !important; } }
            .q-tabs__arrow { display: none !important; }
            .q-tabs__content { overflow: hidden !important; flex-wrap: nowrap !important; }
            .q-tab { cursor: pointer !important; min-height: 32px !important; }
            .q-tab__content { padding: 0 8px !important; }
            .detail-scroll-area, .detail-scroll-area .q-scrollarea__container, 
            .detail-scroll-area .q-scrollarea__content { width: 100% !important; max-width: 100% !important; }
            .q-dialog__inner--minimized > div { max-width: 95vw !important; }
            
            /* 卡片样式 (无缩放，仅变色) */
            .ping-card-base { border-width: 2px; border-style: solid; transition: all 0.3s; }
            .ping-card-inactive { border-color: transparent !important; opacity: 0.4; filter: grayscale(100%); }
        </style>
    ''')

    try:
        LABEL_STYLE = 'text-gray-500 font-bold text-[9px] md:text-[10px] uppercase tracking-wider' 
        VALUE_STYLE = 'text-gray-200 font-mono text-xs md:text-sm truncate font-bold'
        BORDER_STYLE = 'border border-white/10'
        CARD_BG = 'bg-[#1e293b]/50'
        
        # 状态管理
        visible_series = {0: True, 1: True, 2: True}
        is_smooth = {'value': False}

        with ui.dialog() as d, ui.card().classes(
            'p-0 overflow-hidden flex flex-col bg-[#0f172a] border border-slate-700 shadow-2xl full-height-dialog'
        ).style('width: 95vw; max-width: 900px; border-radius: 20px;'): 
            d.props('backdrop-filter="blur(10px)"')
            
            # --- 1. 顶部标题栏 ---
            with ui.row().classes('w-full items-center justify-between p-3 md:p-6 bg-[#1e293b] border-b border-slate-700 flex-shrink-0 flex-nowrap'):
                with ui.row().classes('items-center gap-3 overflow-hidden flex-nowrap'):
                    flag = "🏳️"
                    try: flag = detect_country_group(server_conf['name'], server_conf).split(' ')[0]
                    except: pass
                    ui.label(flag).classes('text-xl md:text-3xl flex-shrink-0') 
                    ui.label(server_conf['name']).classes('text-base md:text-lg font-black text-white truncate flex-grow')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=white')

            # --- 2. 内容滚动区 ---
            with ui.scroll_area().classes('w-full flex-grow detail-scroll-area'):
                with ui.column().classes('p-4 md:p-8 gap-4 w-full'):
                    refs = {} 
                    
                    # A. 系统信息模块
                    with ui.card().classes(f'w-full p-0 rounded-xl {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                        ui.label('系统信息').classes('text-[10px] font-black text-blue-500 m-3 mb-1 tracking-widest')
                        with ui.row().classes('w-full flex-wrap md:flex-nowrap items-stretch p-0'):
                            def info_row(label, key, value_cls=VALUE_STYLE):
                                with ui.row().classes('w-full items-center justify-between border-b border-white/5 pb-1.5 mb-1.5 last:border-0 last:mb-0'):
                                    ui.label(label).classes(LABEL_STYLE)
                                    refs[key] = ui.label('Loading...').classes(value_cls)
                            with ui.column().classes('w-full md:w-1/2 p-3 md:p-6 border-b md:border-b-0 md:border-r border-white/10 gap-1'):
                                info_row('CPU 型号', 'cpu_model'); info_row('操作系统', 'os')
                                info_row('内存', 'mem_detail'); info_row('总流量', 'traffic_detail')
                            with ui.column().classes('w-full md:w-1/2 p-3 md:p-6 gap-1'):
                                info_row('架构/虚拟', 'arch_virt')
                                info_row('硬盘', 'disk_detail')
                                info_row('实时网速', 'speed_detail', value_cls='text-blue-400 font-mono text-xs font-bold text-right')
                                info_row('系统负载', 'load')

                    # B. 三网延迟模块 (修复：点击仅变色，不移位)
                    with ui.card().classes(f'w-full p-3 rounded-xl {CARD_BG} {BORDER_STYLE}'):
                        ui.label('三网延迟 (点击切换)').classes('text-[10px] font-black text-purple-500 mb-2 tracking-widest')
                        with ui.grid().classes('w-full grid-cols-3 gap-2'):
                            
                            def toggle_series(idx, card_el, color_cls):
                                visible_series[idx] = not visible_series[idx]
                                if visible_series[idx]:
                                    # 选中：恢复颜色边框，移除透明边框和灰色滤镜
                                    card_el.classes(add=color_cls, remove='ping-card-inactive')
                                else:
                                    # 取消：添加透明边框和灰色滤镜，移除颜色边框
                                    card_el.classes(add='ping-card-inactive', remove=color_cls)
                                
                            def ping_box(name, color, key, idx):
                                color_border_cls = f'border-{color}-500' # 激活时的边框颜色
                                # 默认状态：激活
                                base_cls = f'bg-[#0f172a]/60 ping-card-base rounded-xl p-1.5 items-center flex flex-col cursor-pointer {color_border_cls}'
                                
                                with ui.element('div').classes(base_cls) as card:
                                    card.on('click', lambda _, i=idx, c=card, col=color_border_cls: toggle_series(i, c, col))
                                    ui.label(name).classes(f'text-{color}-400 font-bold text-[8px] whitespace-nowrap')
                                    refs[key] = ui.label('--').classes('text-white font-bold text-xs font-mono tracking-tighter')
                            
                            ping_box('电信', 'blue', 'ping_ct', 0)
                            ping_box('联通', 'orange', 'ping_cu', 1)
                            ping_box('移动', 'green', 'ping_cm', 2)

                    # C. 网络趋势模块
                    with ui.card().classes(f'w-full p-0 mb-2 rounded-xl {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                        
                        # 工具栏
                        with ui.row().classes('w-full justify-between items-center p-3 border-b border-white/5'):
                            with ui.row().classes('items-center gap-2'):
                                ui.label('网络趋势').classes('text-[10px] font-black text-teal-500 tracking-widest')
                                # 平滑开关
                                with ui.row().classes('items-center gap-1 cursor-pointer bg-white/5 px-2 py-0.5 rounded-full').on('click', lambda: [smooth_sw.set_value(not smooth_sw.value)]):
                                    smooth_sw = ui.switch().props('dense size=xs color=teal').classes('scale-75')
                                    ui.label('平滑').classes('text-[9px] text-gray-400 select-none')
                                    smooth_sw.on_value_change(lambda e: is_smooth.update({'value': e.value}))

                            with ui.tabs().props('dense no-caps hide-arrows active-color=blue-400 indicator-color=transparent').classes('bg-white/5 rounded-lg p-0.5') as chart_tabs:
                                t_1h = ui.tab('1h', label='1小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                                t_3h = ui.tab('3h', label='3小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                                t_6h = ui.tab('6h', label='6小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                            chart_tabs.set_value('1h')

                        # EWMA 算法
                        def calculate_ewma(data, alpha=0.3):
                            if not data: return []
                            result = [data[0]]
                            for i in range(1, len(data)):
                                result.append(alpha * data[i] + (1 - alpha) * result[-1])
                            return [int(x) for x in result]

                        chart = ui.echart({
                            'backgroundColor': 'transparent',
                            'color': ['#3b82f6', '#f97316', '#22c55e'], 
                            'legend': { 'show': False },
                            'tooltip': {
                                'trigger': 'axis',
                                'backgroundColor': 'rgba(15, 23, 42, 0.9)',
                                'borderColor': '#334155',
                                'textStyle': {'color': '#f1f5f9', 'fontSize': 10},
                                'axisPointer': {'type': 'line', 'lineStyle': {'color': '#94a3b8', 'width': 1, 'type': 'dashed'}},
                                'formatter': '{b}<br/>{a0}: {c0}ms<br/>{a1}: {c1}ms<br/>{a2}: {c2}ms'
                            },
                            'dataZoom': [
                                {'type': 'inside', 'xAxisIndex': 0, 'zoomLock': False}
                            ],
                            'grid': { 'left': '2%', 'right': '4%', 'bottom': '5%', 'top': '10%', 'containLabel': True },
                            'xAxis': { 'type': 'category', 'boundaryGap': False, 'data': [], 'axisLabel': { 'fontSize': 8, 'color': '#64748b' } },
                            'yAxis': { 'type': 'value', 'splitLine': { 'lineStyle': { 'color': 'rgba(255,255,255,0.05)' } }, 'axisLabel': { 'fontSize': 8, 'color': '#64748b' } },
                            'series': [
                                {'name': '电信', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}},
                                {'name': '联通', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}},
                                {'name': '移动', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}}
                            ]
                        }).classes('w-full h-64 md:h-72')

                async def update_dark_detail():
                    if not d.value: return
                    try:
                        status = await get_server_status(server_conf)
                        if not status: return
                        raw_cache = PROBE_DATA_CACHE.get(server_conf['url'], {})
                        static = raw_cache.get('static', {})
                        
                        refs['cpu_model'].set_text(status.get('cpu_model', static.get('cpu_model', 'Generic CPU')))
                        refs['os'].set_text(static.get('os', 'Linux'))
                        refs['mem_detail'].set_text(f"{int(status.get('mem_usage', 0))}% / {status.get('mem_total', 0)}G")
                        refs['arch_virt'].set_text(f"{static.get('arch', 'x64')} / {static.get('virt', 'kvm')}")
                        refs['disk_detail'].set_text(f"{int(status.get('disk_usage', 0))}% / {status.get('disk_total', 0)}G")
                        
                        def fmt_b(b): return format_bytes(b)
                        refs['traffic_detail'].set_text(f"↑{fmt_b(status.get('net_total_out', 0))} ↓{fmt_b(status.get('net_total_in', 0))}")
                        refs['speed_detail'].set_text(f"↑{fmt_b(status.get('net_speed_out', 0))}/s ↓{fmt_b(status.get('net_speed_in', 0))}/s")
                        refs['load'].set_text(str(status.get('load_1', 0)))
                        
                        pings = status.get('pings', {})
                        def fmt_p(v): return str(v) if v > 0 else "N/A"
                        refs['ping_ct'].set_text(fmt_p(pings.get('电信', -1)))
                        refs['ping_cu'].set_text(fmt_p(pings.get('联通', -1)))
                        refs['ping_cm'].set_text(fmt_p(pings.get('移动', -1)))

                        history_data = PING_TREND_CACHE.get(server_conf['url'], [])
                        if history_data:
                            import time
                            current_mode = chart_tabs.value
                            if current_mode == '1h': duration = 3600
                            elif current_mode == '3h': duration = 10800
                            elif current_mode == '6h': duration = 21600 
                            else: duration = 3600
                            
                            cutoff = time.time() - duration
                            sliced = [p for p in history_data if p['ts'] > cutoff]
                            
                            if sliced:
                                raw_ct = [p['ct'] for p in sliced]
                                raw_cu = [p['cu'] for p in sliced]
                                raw_cm = [p['cm'] for p in sliced]
                                times = [p['time_str'] for p in sliced]

                                if is_smooth['value']:
                                    final_ct = calculate_ewma(raw_ct)
                                    final_cu = calculate_ewma(raw_cu)
                                    final_cm = calculate_ewma(raw_cm)
                                else:
                                    final_ct, final_cu, final_cm = raw_ct, raw_cu, raw_cm

                                chart.options['xAxis']['data'] = times
                                chart.options['series'][0]['data'] = final_ct if visible_series[0] else []
                                chart.options['series'][1]['data'] = final_cu if visible_series[1] else []
                                chart.options['series'][2]['data'] = final_cm if visible_series[2] else []
                                
                                chart.update()
                    except: pass

                chart_tabs.on_value_change(update_dark_detail)

            # 3. 底部状态栏
            with ui.row().classes('w-full justify-center p-2 bg-[#0f172a] border-t border-white/5 flex-shrink-0'):
                ui.label(f"已运行: {PROBE_DATA_CACHE.get(server_conf['url'], {}).get('uptime', '-') or '-'}").classes('text-[10px] text-gray-500 font-mono')

        d.open()
        asyncio.create_task(update_dark_detail())
        timer = ui.timer(2.0, update_dark_detail)
        d.on('hide', lambda: timer.cancel())

    except Exception as e:
        print(f"Mobile Detail error: {e}")