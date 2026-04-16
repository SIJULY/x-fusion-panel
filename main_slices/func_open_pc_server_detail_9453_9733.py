def open_pc_server_detail(server_conf):
    try:
        # 1. 获取当前主题状态
        is_dark = app.storage.user.get('is_dark', True)
        
        # 2. 定义双模样式 
        LABEL_STYLE = 'text-slate-500 dark:text-gray-400 text-sm font-medium'
        VALUE_STYLE = 'text-[#1e293b] dark:text-gray-200 font-mono text-sm font-bold'
        SECTION_TITLE = 'text-[#1e293b] dark:text-gray-200 text-base font-black mb-4 flex items-center gap-2'
        DIALOG_BG = 'bg-white/85 backdrop-blur-xl dark:bg-[#0d1117] dark:backdrop-blur-none'
        CARD_BG   = 'bg-white/60 dark:bg-[#161b22]' 
        BORDER_STYLE = 'border border-white/50 dark:border-[#30363d]'
        SHADOW_STYLE = 'shadow-[0_8px_32px_0_rgba(31,38,135,0.15)] dark:shadow-2xl'
        TRACK_COLOR = 'blue-1' if not is_dark else 'grey-9'

        visible_series = {0: True, 1: True, 2: True}
        is_smooth = {'value': False}

        # 智能容量格式化
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
            except:
                return str(b)

        ui.add_head_html('''
            <style>
                .ping-card-base { border-width: 2px; border-style: solid; transition: all 0.3s; }
                .ping-card-inactive { border-color: transparent !important; opacity: 0.4; filter: grayscale(100%); }
            </style>
        ''')
        
        with ui.dialog() as d, ui.card().classes(f'p-0 overflow-hidden flex flex-col {DIALOG_BG} {SHADOW_STYLE}').style('width: 1000px; max-width: 95vw; border-radius: 12px;'):
            
            # --- 标题栏 ---
            with ui.row().classes(f'w-full items-center justify-between p-4 {CARD_BG} border-b border-white/50 dark:border-[#30363d] flex-shrink-0'):
                with ui.row().classes('items-center gap-3'):
                    flag = "🏳️"
                    try: flag = detect_country_group(server_conf['name'], server_conf).split(' ')[0]
                    except: pass
                    ui.label(flag).classes('text-2xl')
                    ui.label(server_conf['name']).classes(f'text-lg font-bold text-[#1e293b] dark:text-white')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey-5')

            # --- 内容区 ---
            with ui.scroll_area().classes('w-full flex-grow p-6').style('height: 65vh;'):
                refs = {}
                
                # 第一行：左右分栏
                with ui.row().classes('w-full gap-6 no-wrap items-stretch'):
                    # 左侧：资源
                    with ui.column().classes(f'flex-1 p-5 rounded-xl {CARD_BG} {BORDER_STYLE} justify-between'):
                        ui.label('资源使用情况').classes(SECTION_TITLE)
                        
                        def progress_block(label, key, icon, color_class):
                            with ui.column().classes('w-full gap-1'):
                                with ui.row().classes('w-full justify-between items-end'):
                                    with ui.row().classes('items-center gap-2'):
                                        ui.icon(icon).classes('text-gray-400 dark:text-gray-500 text-xs'); ui.label(label).classes(LABEL_STYLE)
                                    refs[f'{key}_pct'] = ui.label('0.0%').classes('text-gray-500 dark:text-gray-400 text-xs font-mono')
                                refs[f'{key}_bar'] = ui.linear_progress(value=0, show_value=False).props(f'color={color_class} track-color={TRACK_COLOR}').classes('h-1.5 rounded-full')
                                with ui.row().classes('w-full justify-end'):
                                    # ✨ 修改默认占位符，不再显示 "-- / --"
                                    refs[f'{key}_val'] = ui.label('--').classes('text-[11px] text-gray-500 font-mono mt-1')
                        
                        progress_block('CPU', 'cpu', 'settings_suggest', 'blue-5')
                        progress_block('RAM', 'mem', 'memory', 'green-5')
                        progress_block('DISK', 'disk', 'storage', 'purple-5')

                    # 右侧：系统
                    with ui.column().classes(f'w-[400px] p-5 rounded-xl {CARD_BG} {BORDER_STYLE} justify-between'):
                        ui.label('系统资讯').classes(SECTION_TITLE)
                        def info_line(label, icon, key):
                            with ui.row().classes('w-full items-center justify-between py-3 border-b border-white/50 dark:border-[#30363d] last:border-0'):
                                with ui.row().classes('items-center gap-2'):
                                    ui.icon(icon).classes('text-gray-400 dark:text-gray-500 text-sm'); ui.label(label).classes(LABEL_STYLE)
                                refs[key] = ui.label('Loading...').classes(VALUE_STYLE)
                        info_line('作业系统', 'laptop_windows', 'os')
                        info_line('架构', 'developer_board', 'arch')
                        info_line('虚拟化', 'cloud_queue', 'virt')
                        info_line('在线时长', 'timer', 'uptime')

                # 第二行：延迟卡片
                with ui.row().classes('w-full gap-4 mt-6'):
                    def toggle_series(idx, card_el, color_cls):
                        visible_series[idx] = not visible_series[idx]
                        if visible_series[idx]:
                            card_el.classes(add=color_cls, remove='ping-card-inactive')
                        else:
                            card_el.classes(add='ping-card-inactive', remove=color_cls)

                    def ping_card(name, color, key, idx):
                        color_border_cls = f'border-{color}-500'
                        base_cls = f'flex-1 p-4 rounded-xl {CARD_BG} ping-card-base cursor-pointer {color_border_cls}'
                        with ui.element('div').classes(base_cls) as card:
                            card.on('click', lambda _, i=idx, c=card, col=color_border_cls: toggle_series(i, c, col))
                            with ui.row().classes('w-full justify-between items-center mb-1'):
                                ui.label(name).classes(f'text-{color}-500 text-xs font-bold')
                            with ui.row().classes('items-baseline gap-1'):
                                refs[f'{key}_cur'] = ui.label('--').classes(f'text-2xl font-black font-mono text-[#1e293b] dark:text-white')
                                ui.label('ms').classes('text-gray-500 text-[10px]')
                    ping_card('电信', 'blue', 'ping_ct', 0)
                    ping_card('联通', 'orange', 'ping_cu', 1)
                    ping_card('移动', 'green', 'ping_cm', 2)

                # 第三行：趋势图
                with ui.column().classes(f'w-full mt-6 p-5 rounded-xl {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                    with ui.row().classes('w-full justify-between items-center mb-4'):
                        with ui.row().classes('items-center gap-4'):
                            ui.label('网络质量趋势').classes(f'text-sm font-bold text-[#1e293b] dark:text-gray-200')
                            switch_bg = 'bg-blue-50/50 dark:bg-[#0d1117]'
                            with ui.row().classes(f'items-center gap-2 cursor-pointer {switch_bg} px-3 py-1 rounded-full border border-white/50 dark:border-[#30363d]').on('click', lambda: smooth_sw.set_value(not smooth_sw.value)):
                                smooth_sw = ui.switch().props('dense size=sm color=blue')
                                ui.label('平滑曲线').classes('text-xs text-slate-500 dark:text-gray-400 select-none')
                                smooth_sw.on_value_change(lambda e: is_smooth.update({'value': e.value}))
                        tab_bg = 'bg-blue-50/50 dark:bg-[#0d1117]'
                        with ui.tabs().props('dense no-caps indicator-color=blue active-color=blue').classes(f'{tab_bg} rounded-lg p-1') as chart_tabs:
                            tab_cls = 'px-4 text-xs text-slate-500 dark:text-gray-400'
                            ui.tab('1h', label='1小时').classes(tab_cls)
                            ui.tab('3h', label='3小时').classes(tab_cls)
                            ui.tab('6h', label='6小时').classes(tab_cls)
                        chart_tabs.set_value('1h')

                    def calculate_ewma(data, alpha=0.3):
                        if not data: return []
                        result = [data[0]]
                        for i in range(1, len(data)):
                            result.append(alpha * data[i] + (1 - alpha) * result[-1])
                        return [int(x) for x in result]

                    chart_text = '#64748b' if not is_dark else '#94a3b8'
                    split_line = '#e2e8f0' if not is_dark else '#30363d'
                    tooltip_bg = 'rgba(255, 255, 255, 0.95)' if not is_dark else 'rgba(13, 17, 23, 0.95)'
                    tooltip_border = '#cbd5e1' if not is_dark else '#30363d'
                    tooltip_text = '#334155' if not is_dark else '#e6edf3'

                    chart = ui.echart({
                        'backgroundColor': 'transparent', 
                        'color': ['#3b82f6', '#f97316', '#22c55e'], 
                        'legend': { 'show': False },
                        'tooltip': {
                            'trigger': 'axis', 'backgroundColor': tooltip_bg, 'borderColor': tooltip_border, 'textStyle': {'color': tooltip_text},
                            'axisPointer': {'type': 'line', 'lineStyle': {'color': '#8b949e', 'type': 'dashed'}},
                            'formatter': '{b}<br/>{a0}: {c0}ms<br/>{a1}: {c1}ms<br/>{a2}: {c2}ms'
                        },
                        'dataZoom': [{'type': 'inside', 'xAxisIndex': 0, 'zoomLock': False}],
                        'grid': { 'left': '1%', 'right': '1%', 'bottom': '5%', 'top': '15%', 'containLabel': True },
                        'xAxis': { 'type': 'category', 'boundaryGap': False, 'axisLabel': { 'color': chart_text } },
                        'yAxis': { 'type': 'value', 'splitLine': { 'lineStyle': { 'color': split_line } }, 'axisLabel': { 'color': chart_text } },
                        'series': [
                            {'name': '电信', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}},
                            {'name': '联通', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}},
                            {'name': '移动', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}}
                        ]
                    }).classes('w-full h-64')

                async def update_dark_detail():
                    if not d.value: return
                    try:
                        status = await get_server_status(server_conf)
                        raw_cache = PROBE_DATA_CACHE.get(server_conf['url'], {})
                        static = raw_cache.get('static', {})

                        # ✨✨✨ CPU 更新逻辑：百分比 + 核心数 ✨✨✨
                        cpu_val = float(status.get('cpu_usage', 0))
                        refs['cpu_pct'].set_text(f"{cpu_val:.1f}%") 
                        refs['cpu_bar'].set_value(cpu_val / 100)
                        
                        # ✨ 核心修复：强制获取并显示核心数 (格式如 "2 C")
                        c_cores = status.get('cpu_cores')
                        if not c_cores:
                            c_cores = static.get('cpu_cores') # 备用：从静态缓存读取
                        
                        if c_cores:
                            refs['cpu_val'].set_text(f"{c_cores} C")
                        else:
                            refs['cpu_val'].set_text("--")

                        # ✨✨✨ 内存 百分比 + 容量 ✨✨✨
                        mem_p = float(status.get('mem_usage', 0))
                        refs['mem_pct'].set_text(f"{mem_p:.1f}%") 
                        refs['mem_bar'].set_value(mem_p / 100)
                        
                        mem_t_raw = status.get('mem_total', 0)
                        total_str = fmt_capacity(mem_t_raw)
                        used_str = "--"
                        if status.get('mem_used'):
                            used_str = fmt_capacity(status.get('mem_used'))
                        else:
                            # 估算已用
                            try:
                                val_t = float(re.findall(r"[-+]?\d*\.\d+|\d+", str(mem_t_raw))[0]) if isinstance(mem_t_raw, str) else float(mem_t_raw)
                                numeric_used = val_t * (mem_p / 100.0)
                                used_str = fmt_capacity(numeric_used)
                            except: pass
                        refs['mem_val'].set_text(f"{used_str} / {total_str}")

                        # ✨✨✨ 硬盘 百分比 + 容量 ✨✨✨
                        disk_p = float(status.get('disk_usage', 0))
                        refs['disk_pct'].set_text(f"{disk_p:.1f}%")
                        refs['disk_bar'].set_value(disk_p / 100)
                        
                        disk_t_raw = status.get('disk_total', 0)
                        disk_total_str = fmt_capacity(disk_t_raw)
                        disk_used_str = "--"
                        if status.get('disk_used'):
                            disk_used_str = fmt_capacity(status.get('disk_used'))
                        else:
                            # 估算已用
                            try:
                                val_d = float(re.findall(r"[-+]?\d*\.\d+|\d+", str(disk_t_raw))[0]) if isinstance(disk_t_raw, str) else float(disk_t_raw)
                                numeric_disk_used = val_d * (disk_p / 100.0)
                                disk_used_str = fmt_capacity(numeric_disk_used)
                            except: pass
                        refs['disk_val'].set_text(f"{disk_used_str} / {disk_total_str}")

                        # 系统信息
                        raw_arch = static.get('arch', '').lower()
                        display_arch = "AMD" if "x86" in raw_arch or "amd" in raw_arch else "ARM" if "arm" in raw_arch or "aarch" in raw_arch else raw_arch.upper()
                        refs['os'].set_text(static.get('os', 'Linux')); refs['arch'].set_text(display_arch); refs['virt'].set_text(static.get('virt', 'kvm'))
                        
                        uptime_str = str(status.get('uptime', '-')).replace('up ', '').replace('days', '天').replace('hours', '时').replace('minutes', '分')
                        refs['uptime'].set_text(uptime_str); refs['uptime'].classes('text-green-500')

                        # 延迟
                        pings = status.get('pings', {})
                        refs['ping_ct_cur'].set_text(str(pings.get('电信', 'N/A')))
                        refs['ping_cu_cur'].set_text(str(pings.get('联通', 'N/A')))
                        refs['ping_cm_cur'].set_text(str(pings.get('移动', 'N/A')))

                        # 图表
                        history_data = PING_TREND_CACHE.get(server_conf['url'], [])
                        if history_data:
                            import time
                            current_mode = chart_tabs.value
                            duration = 3600
                            if current_mode == '3h': duration = 10800
                            elif current_mode == '6h': duration = 21600 
                            
                            cutoff = time.time() - duration
                            sliced = [p for p in history_data if p['ts'] > cutoff]
                            if sliced:
                                raw_ct = [p['ct'] for p in sliced]
                                raw_cu = [p['cu'] for p in sliced]
                                raw_cm = [p['cm'] for p in sliced]
                                times = [p['time_str'] for p in sliced]
                                
                                final_ct = calculate_ewma(raw_ct) if is_smooth['value'] else raw_ct
                                final_cu = calculate_ewma(raw_cu) if is_smooth['value'] else raw_cu
                                final_cm = calculate_ewma(raw_cm) if is_smooth['value'] else raw_cm
                                
                                chart.options['xAxis']['data'] = times
                                chart.options['series'][0]['data'] = final_ct if visible_series[0] else []
                                chart.options['series'][1]['data'] = final_cu if visible_series[1] else []
                                chart.options['series'][2]['data'] = final_cm if visible_series[2] else []
                                chart.update()
                    except: pass

                chart_tabs.on_value_change(update_dark_detail)

            # --- 底部 ---
            with ui.row().classes(f'w-full justify-center p-2 {CARD_BG} border-t border-white/50 dark:border-[#30363d]'):
                ui.label('Powered by X-Fusion Monitor').classes('text-[10px] text-gray-500 dark:text-gray-600 font-mono italic')

        d.open()
        asyncio.create_task(update_dark_detail())
        timer = ui.timer(2.0, update_dark_detail)
        d.on('hide', lambda: timer.cancel())
    except Exception as e:
        print(f"PC Detail Error: {e}")