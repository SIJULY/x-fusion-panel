import asyncio
import random
import re
import time

from fastapi import Request
from nicegui import app, ui

import app.core.state as state
from app.services.dashboard import prepare_map_data
from app.services.probe import get_server_status
from app.utils.formatters import format_bytes
from app.utils.geo import detect_country_group


def is_mobile_device(request: Request) -> bool:
    """通过 User-Agent 判断是否为移动设备"""
    user_agent = request.headers.get('user-agent', '').lower()
    mobile_keywords = [
        'android', 'iphone', 'ipad', 'iemobile',
        'opera mini', 'mobile', 'harmonyos'
    ]
    return any(keyword in user_agent for keyword in mobile_keywords)


CURRENT_PROBE_TAB = state.CURRENT_PROBE_TAB


async def status_page_router(request: Request):
    """
    路由分发器：
    1. 检测设备类型
    2. 手机端调用 render_mobile_status_page()
    3. 电脑端调用 render_desktop_status_page()
    """
    if is_mobile_device(request):
        # 针对手机进行极简渲染，防止硬件加速导致的浏览器崩溃
        await render_mobile_status_page()
    else:
        # 恢复 V30 版本的酷炫地图大屏显示
        await render_desktop_status_page()


def open_mobile_server_detail(server_conf):
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
            .ping-card-base { border-width: 2px; border-style: solid; transition: all 0.3s; }
            .ping-card-inactive { border-color: transparent !important; opacity: 0.4; filter: grayscale(100%); }
        </style>
    ''')

    try:
        LABEL_STYLE = 'text-gray-500 font-bold text-[9px] md:text-[10px] uppercase tracking-wider'
        VALUE_STYLE = 'text-gray-200 font-mono text-xs md:text-sm truncate font-bold'
        BORDER_STYLE = 'border border-white/10'
        CARD_BG = 'bg-[#1e293b]/50'

        visible_series = {0: True, 1: True, 2: True}
        is_smooth = {'value': False}

        with ui.dialog() as d, ui.card().classes(
            'p-0 overflow-hidden flex flex-col bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)] full-height-dialog'
        ).style('width: 95vw; max-width: 900px; border-radius: 8px;'):
            d.props('backdrop-filter="blur(10px)"')

            with ui.row().classes('w-full items-center justify-between p-3 md:p-6 bg-gradient-to-r from-[#0a1526] to-[#050a14] border-b border-[#1e3a5f]/60 flex-shrink-0 flex-nowrap'):
                with ui.row().classes('items-center gap-3 overflow-hidden flex-nowrap'):
                    flag = '🏳️'
                    try:
                        flag = detect_country_group(server_conf['name'], server_conf).split(' ')[0]
                    except:
                        pass
                    ui.label(flag).classes('text-xl md:text-3xl flex-shrink-0')
                    ui.label(server_conf['name']).classes('text-base md:text-lg font-black text-white truncate flex-grow')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=white').classes('text-slate-300 hover:text-cyan-300 hover:bg-cyan-950/30')


            with ui.scroll_area().classes('w-full flex-grow detail-scroll-area'):
                with ui.column().classes('p-4 md:p-8 gap-4 w-full'):
                    refs = {}

                    with ui.card().classes(f'w-full p-0 rounded-sm {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                        ui.label('系统信息').classes('text-[10px] font-black text-cyan-400 m-3 mb-1 tracking-widest')
                        with ui.row().classes('w-full flex-wrap md:flex-nowrap items-stretch p-0'):
                            def info_row(label, key, value_cls=VALUE_STYLE):
                                with ui.row().classes('w-full items-center justify-between border-b border-white/5 pb-1.5 mb-1.5 last:border-0 last:mb-0'):
                                    ui.label(label).classes(LABEL_STYLE)
                                    refs[key] = ui.label('Loading...').classes(value_cls)
                            with ui.column().classes('w-full md:w-1/2 p-3 md:p-6 border-b md:border-b-0 md:border-r border-white/10 gap-1'):
                                info_row('CPU 型号', 'cpu_model')
                                info_row('操作系统', 'os')
                                info_row('内存', 'mem_detail')
                                info_row('总流量', 'traffic_detail')
                            with ui.column().classes('w-full md:w-1/2 p-3 md:p-6 gap-1'):
                                info_row('架构/虚拟', 'arch_virt')
                                info_row('硬盘', 'disk_detail')
                                info_row('实时网速', 'speed_detail', value_cls='text-blue-400 font-mono text-xs font-bold text-right')
                                info_row('系统负载', 'load')

                    with ui.card().classes(f'w-full p-3 rounded-sm {CARD_BG} {BORDER_STYLE}'):
                        ui.label('三网延迟 (点击切换)').classes('text-[10px] font-black text-fuchsia-400 mb-2 tracking-widest')
                        with ui.grid().classes('w-full grid-cols-3 gap-2'):
                            def toggle_series(idx, card_el, color_cls):
                                visible_series[idx] = not visible_series[idx]
                                if visible_series[idx]:
                                    card_el.classes(add=color_cls, remove='ping-card-inactive')
                                else:
                                    card_el.classes(add='ping-card-inactive', remove=color_cls)

                            def ping_box(name, color, key, idx):
                                color_border_cls = f'border-{color}-500'
                                base_cls = f'bg-[#050b14]/80 ping-card-base rounded-sm p-1.5 items-center flex flex-col cursor-pointer {color_border_cls} border border-[#1e3a5f]/35'
                                with ui.element('div').classes(base_cls) as card:
                                    card.on('click', lambda _, i=idx, c=card, col=color_border_cls: toggle_series(i, c, col))
                                    ui.label(name).classes(f'text-{color}-400 font-bold text-[8px] whitespace-nowrap')
                                    refs[key] = ui.label('--').classes('text-white font-bold text-xs font-mono tracking-tighter')

                            ping_box('电信', 'blue', 'ping_ct', 0)
                            ping_box('联通', 'orange', 'ping_cu', 1)
                            ping_box('移动', 'green', 'ping_cm', 2)

                    with ui.card().classes(f'w-full p-0 mb-2 rounded-sm {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                        with ui.row().classes('w-full justify-between items-center p-3 border-b border-[#1e3a5f]/35'):
                            with ui.row().classes('items-center gap-2'):
                                ui.label('网络趋势').classes('text-[10px] font-black text-cyan-400 tracking-widest')
                                with ui.row().classes('items-center gap-1 cursor-pointer bg-cyan-950/20 px-2 py-0.5 rounded-sm border border-[#1e3a5f]/35').on('click', lambda: [smooth_sw.set_value(not smooth_sw.value)]):
                                    smooth_sw = ui.switch().props('dense size=xs color=teal').classes('scale-75')
                                    ui.label('平滑').classes('text-[9px] text-gray-400 select-none')
                                    smooth_sw.on_value_change(lambda e: is_smooth.update({'value': e.value}))

                            with ui.tabs().props('dense no-caps hide-arrows active-color=cyan-4 indicator-color=transparent').classes('bg-cyan-950/20 rounded-sm p-0.5 border border-[#1e3a5f]/35') as chart_tabs:

                                ui.tab('1h', label='1小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                                ui.tab('3h', label='3小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                                ui.tab('6h', label='6小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                            chart_tabs.set_value('1h')

                        def calculate_ewma(data, alpha=0.3):
                            if not data:
                                return []
                            result = [data[0]]
                            for i in range(1, len(data)):
                                result.append(alpha * data[i] + (1 - alpha) * result[-1])
                            return [int(x) for x in result]

                        chart = ui.echart({
                            'backgroundColor': 'transparent',
                            'color': ['#3b82f6', '#f97316', '#22c55e'],
                            'legend': {'show': False},
                            'tooltip': {
                                'trigger': 'axis',
                                'backgroundColor': 'rgba(15, 23, 42, 0.9)',
                                'borderColor': '#334155',
                                'textStyle': {'color': '#f1f5f9', 'fontSize': 10},
                                'axisPointer': {'type': 'line', 'lineStyle': {'color': '#94a3b8', 'width': 1, 'type': 'dashed'}},
                                'formatter': '{b}<br/>{a0}: {c0}ms<br/>{a1}: {c1}ms<br/>{a2}: {c2}ms'
                            },
                            'dataZoom': [{'type': 'inside', 'xAxisIndex': 0, 'zoomLock': False}],
                            'grid': {'left': '2%', 'right': '4%', 'bottom': '5%', 'top': '10%', 'containLabel': True},
                            'xAxis': {'type': 'category', 'boundaryGap': False, 'data': [], 'axisLabel': {'fontSize': 8, 'color': '#64748b'}},
                            'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'color': 'rgba(255,255,255,0.05)'}}, 'axisLabel': {'fontSize': 8, 'color': '#64748b'}},
                            'series': [
                                {'name': '电信', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}},
                                {'name': '联通', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}},
                                {'name': '移动', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}}
                            ]
                        }).classes('w-full h-64 md:h-72')

                async def update_dark_detail():
                    if not d.value:
                        return
                    try:
                        status = await get_server_status(server_conf)
                        if not status:
                            return
                        raw_cache = state.PROBE_DATA_CACHE.get(server_conf['url'], {})
                        static = raw_cache.get('static', {})

                        refs['cpu_model'].set_text(status.get('cpu_model', static.get('cpu_model', 'Generic CPU')))
                        refs['os'].set_text(static.get('os', 'Linux'))
                        refs['mem_detail'].set_text(f"{int(status.get('mem_usage', 0))}% / {status.get('mem_total', 0)}G")
                        refs['arch_virt'].set_text(f"{static.get('arch', 'x64')} / {static.get('virt', 'kvm')}")
                        refs['disk_detail'].set_text(f"{int(status.get('disk_usage', 0))}% / {status.get('disk_total', 0)}G")
                        refs['traffic_detail'].set_text(f"↑{format_bytes(status.get('net_total_out', 0))} ↓{format_bytes(status.get('net_total_in', 0))}")
                        refs['speed_detail'].set_text(f"↑{format_bytes(status.get('net_speed_out', 0))}/s ↓{format_bytes(status.get('net_speed_in', 0))}/s")
                        refs['load'].set_text(str(status.get('load_1', 0)))

                        pings = status.get('pings', {})
                        refs['ping_ct'].set_text(str(pings.get('电信', -1)) if pings.get('电信', -1) > 0 else 'N/A')
                        refs['ping_cu'].set_text(str(pings.get('联通', -1)) if pings.get('联通', -1) > 0 else 'N/A')
                        refs['ping_cm'].set_text(str(pings.get('移动', -1)) if pings.get('移动', -1) > 0 else 'N/A')

                        history_data = state.PING_TREND_CACHE.get(server_conf['url'], [])
                        if history_data:
                            current_mode = chart_tabs.value
                            duration = 3600 if current_mode == '1h' else 10800 if current_mode == '3h' else 21600
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
                    except:
                        pass

                chart_tabs.on_value_change(update_dark_detail)

            with ui.row().classes('w-full justify-center p-2 bg-[#030712] border-t border-[#1e3a5f]/35 flex-shrink-0'):
                ui.label(f"已运行: {state.PROBE_DATA_CACHE.get(server_conf['url'], {}).get('uptime', '-') or '-'}").classes('text-[10px] text-cyan-500/70 font-mono font-bold')

        d.open()
        asyncio.create_task(update_dark_detail())
        timer = ui.timer(2.0, update_dark_detail)
        d.on('hide', lambda: timer.cancel())
    except Exception as e:
        print(f"Mobile Detail error: {e}")


def open_pc_server_detail(server_conf):
    try:
        is_dark = app.storage.user.get('is_dark', True)
        LABEL_STYLE = 'text-slate-500 dark:text-slate-400 text-sm font-bold tracking-wide'
        VALUE_STYLE = 'text-[#1e293b] dark:text-slate-100 font-mono text-sm font-black'
        SECTION_TITLE = 'text-[#1e293b] dark:text-slate-100 text-base font-black mb-4 flex items-center gap-2 tracking-wide'
        DIALOG_BG = 'bg-white/90 backdrop-blur-xl dark:bg-[#070b14] dark:backdrop-blur-none'
        CARD_BG = 'bg-white/70 dark:bg-[#030712]'
        BORDER_STYLE = 'border border-white/50 dark:border-[#1e3a5f]/45'
        SHADOW_STYLE = 'shadow-[0_8px_32px_0_rgba(31,38,135,0.15)] dark:shadow-[0_18px_48px_rgba(0,0,0,0.78)]'
        TRACK_COLOR = 'blue-1' if not is_dark else 'blue-10'

        visible_series = {0: True, 1: True, 2: True}
        is_smooth = {'value': False}

        def fmt_capacity(b):
            if b is None:
                return '0 B'
            try:
                if isinstance(b, str):
                    nums = re.findall(r'[-+]?\d*\.\d+|\d+', b)
                    val = float(nums[0]) if nums else 0
                else:
                    val = float(b)
                if val > 1024 * 1024:
                    if val < 1024**3:
                        return f'{val/1024**2:.1f} MB'
                    return f'{val/1024**3:.1f} GB'
                if val > 0:
                    return f'{val:.1f} GB'
                return '0 B'
            except:
                return str(b)

        ui.add_head_html('''
            <style>
                .ping-card-base { border-width: 2px; border-style: solid; transition: all 0.3s; }
                .ping-card-inactive { border-color: transparent !important; opacity: 0.4; filter: grayscale(100%); }
            </style>
        ''')

        with ui.dialog() as d, ui.card().classes(f'p-0 overflow-hidden flex flex-col {DIALOG_BG} {SHADOW_STYLE} dark:border dark:border-[#1e3a5f]/55').style('width: 1000px; max-width: 95vw; border-radius: 8px;'):
            with ui.row().classes(f'w-full items-center justify-between p-4 {CARD_BG} border-b border-white/50 dark:border-[#1e3a5f]/60 flex-shrink-0 dark:bg-gradient-to-r dark:from-[#0a1526] dark:to-[#050a14]'):
                with ui.row().classes('items-center gap-3'):
                    flag = '🏳️'
                    try:
                        flag = detect_country_group(server_conf['name'], server_conf).split(' ')[0]
                    except:
                        pass
                    ui.label(flag).classes('text-2xl')
                    ui.label(server_conf['name']).classes('text-lg font-black text-[#1e293b] dark:text-slate-100 tracking-wide')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey-5').classes('dark:text-slate-400 dark:hover:text-cyan-300 dark:hover:bg-cyan-950/30')

            with ui.scroll_area().classes('w-full flex-grow p-6').style('height: 65vh;'):
                refs = {}

                with ui.row().classes('w-full gap-6 no-wrap items-stretch'):
                    with ui.column().classes(f'flex-1 p-5 rounded-sm {CARD_BG} {BORDER_STYLE} justify-between dark:shadow-[0_0_12px_rgba(0,0,0,0.24)]'):
                        ui.label('资源使用情况').classes(SECTION_TITLE)

                        def progress_block(label, key, icon, color_class):
                            with ui.column().classes('w-full gap-1'):
                                with ui.row().classes('w-full justify-between items-end'):
                                    with ui.row().classes('items-center gap-2'):
                                        ui.icon(icon).classes('text-gray-400 dark:text-gray-500 text-xs')
                                        ui.label(label).classes(LABEL_STYLE)
                                    refs[f'{key}_pct'] = ui.label('0.0%').classes('text-gray-500 dark:text-gray-400 text-xs font-mono')
                                refs[f'{key}_bar'] = ui.linear_progress(value=0, show_value=False).props(f'color={color_class} track-color={TRACK_COLOR}').classes('h-1.5 rounded-full')
                                with ui.row().classes('w-full justify-end'):
                                    refs[f'{key}_val'] = ui.label('--').classes('text-[11px] text-gray-500 font-mono mt-1')

                        progress_block('CPU', 'cpu', 'settings_suggest', 'blue-5')
                        progress_block('RAM', 'mem', 'memory', 'green-5')
                        progress_block('DISK', 'disk', 'storage', 'purple-5')

                    with ui.column().classes(f'w-[400px] p-5 rounded-sm {CARD_BG} {BORDER_STYLE} justify-between dark:shadow-[0_0_12px_rgba(0,0,0,0.24)]'):
                        ui.label('系统资讯').classes(SECTION_TITLE)

                        def info_line(label, icon, key):
                            with ui.row().classes('w-full items-center justify-between py-3 border-b border-white/50 dark:border-[#1e3a5f]/35 last:border-0'):
                                with ui.row().classes('items-center gap-2'):
                                    ui.icon(icon).classes('text-gray-400 dark:text-gray-500 text-sm')
                                    ui.label(label).classes(LABEL_STYLE)
                                refs[key] = ui.label('Loading...').classes(VALUE_STYLE)

                        info_line('作业系统', 'laptop_windows', 'os')
                        info_line('架构', 'developer_board', 'arch')
                        info_line('虚拟化', 'cloud_queue', 'virt')
                        info_line('在线时长', 'timer', 'uptime')

                with ui.row().classes('w-full gap-4 mt-6'):
                    def toggle_series(idx, card_el, color_cls):
                        visible_series[idx] = not visible_series[idx]
                        if visible_series[idx]:
                            card_el.classes(add=color_cls, remove='ping-card-inactive')
                        else:
                            card_el.classes(add='ping-card-inactive', remove=color_cls)

                    def ping_card(name, color, key, idx):
                        color_border_cls = f'border-{color}-500'
                        base_cls = f'flex-1 p-4 rounded-sm {CARD_BG} ping-card-base cursor-pointer {color_border_cls} dark:shadow-[0_0_10px_rgba(0,0,0,0.18)]'
                        with ui.element('div').classes(base_cls) as card:
                            card.on('click', lambda _, i=idx, c=card, col=color_border_cls: toggle_series(i, c, col))
                            with ui.row().classes('w-full justify-between items-center mb-1'):
                                ui.label(name).classes(f'text-{color}-500 text-xs font-bold')
                            with ui.row().classes('items-baseline gap-1'):
                                refs[f'{key}_cur'] = ui.label('--').classes('text-2xl font-black font-mono text-[#1e293b] dark:text-white')
                                ui.label('ms').classes('text-gray-500 text-[10px]')

                    ping_card('电信', 'blue', 'ping_ct', 0)
                    ping_card('联通', 'orange', 'ping_cu', 1)
                    ping_card('移动', 'green', 'ping_cm', 2)

                with ui.column().classes(f'w-full mt-6 p-5 rounded-sm {CARD_BG} {BORDER_STYLE} overflow-hidden dark:shadow-[0_0_12px_rgba(0,0,0,0.24)]'):
                    with ui.row().classes('w-full justify-between items-center mb-4'):
                        with ui.row().classes('items-center gap-4'):
                            ui.label('网络质量趋势').classes('text-sm font-black text-[#1e293b] dark:text-slate-100 tracking-wide')
                            switch_bg = 'bg-blue-50/50 dark:bg-[#050b14]'
                            with ui.row().classes(f'items-center gap-2 cursor-pointer {switch_bg} px-3 py-1 rounded-sm border border-white/50 dark:border-[#1e3a5f]/45').on('click', lambda: smooth_sw.set_value(not smooth_sw.value)):
                                smooth_sw = ui.switch().props('dense size=sm color=blue')
                                ui.label('平滑曲线').classes('text-xs text-slate-500 dark:text-gray-400 select-none')
                                smooth_sw.on_value_change(lambda e: is_smooth.update({'value': e.value}))
                        tab_bg = 'bg-blue-50/50 dark:bg-[#050b14]'
                        with ui.tabs().props('dense no-caps indicator-color=cyan active-color=cyan').classes(f'{tab_bg} rounded-sm p-1 border border-white/40 dark:border-[#1e3a5f]/45') as chart_tabs:
                            tab_cls = 'px-4 text-xs text-slate-500 dark:text-gray-400'
                            ui.tab('1h', label='1小时').classes(tab_cls)
                            ui.tab('3h', label='3小时').classes(tab_cls)
                            ui.tab('6h', label='6小时').classes(tab_cls)
                        chart_tabs.set_value('1h')

                    def calculate_ewma(data, alpha=0.3):
                        if not data:
                            return []
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
                        'legend': {'show': False},
                        'tooltip': {
                            'trigger': 'axis', 'backgroundColor': tooltip_bg, 'borderColor': tooltip_border, 'textStyle': {'color': tooltip_text},
                            'axisPointer': {'type': 'line', 'lineStyle': {'color': '#8b949e', 'type': 'dashed'}},
                            'formatter': '{b}<br/>{a0}: {c0}ms<br/>{a1}: {c1}ms<br/>{a2}: {c2}ms'
                        },
                        'dataZoom': [{'type': 'inside', 'xAxisIndex': 0, 'zoomLock': False}],
                        'grid': {'left': '1%', 'right': '1%', 'bottom': '5%', 'top': '15%', 'containLabel': True},
                        'xAxis': {'type': 'category', 'boundaryGap': False, 'axisLabel': {'color': chart_text}},
                        'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'color': split_line}}, 'axisLabel': {'color': chart_text}},
                        'series': [
                            {'name': '电信', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}},
                            {'name': '联通', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}},
                            {'name': '移动', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}}
                        ]
                    }).classes('w-full h-64')

                async def update_dark_detail():
                    if not d.value:
                        return
                    try:
                        status = await get_server_status(server_conf)
                        raw_cache = state.PROBE_DATA_CACHE.get(server_conf['url'], {})
                        static = raw_cache.get('static', {})

                        cpu_val = float(status.get('cpu_usage', 0))
                        refs['cpu_pct'].set_text(f'{cpu_val:.1f}%')
                        refs['cpu_bar'].set_value(cpu_val / 100)
                        c_cores = status.get('cpu_cores') or static.get('cpu_cores')
                        refs['cpu_val'].set_text(f'{c_cores} C' if c_cores else '--')

                        mem_p = float(status.get('mem_usage', 0))
                        refs['mem_pct'].set_text(f'{mem_p:.1f}%')
                        refs['mem_bar'].set_value(mem_p / 100)
                        mem_t_raw = status.get('mem_total', 0)
                        total_str = fmt_capacity(mem_t_raw)
                        used_str = '--'
                        if status.get('mem_used'):
                            used_str = fmt_capacity(status.get('mem_used'))
                        else:
                            try:
                                val_t = float(re.findall(r'[-+]?\d*\.\d+|\d+', str(mem_t_raw))[0]) if isinstance(mem_t_raw, str) else float(mem_t_raw)
                                used_str = fmt_capacity(val_t * (mem_p / 100.0))
                            except:
                                pass
                        refs['mem_val'].set_text(f'{used_str} / {total_str}')

                        disk_p = float(status.get('disk_usage', 0))
                        refs['disk_pct'].set_text(f'{disk_p:.1f}%')
                        refs['disk_bar'].set_value(disk_p / 100)
                        disk_t_raw = status.get('disk_total', 0)
                        disk_total_str = fmt_capacity(disk_t_raw)
                        disk_used_str = '--'
                        if status.get('disk_used'):
                            disk_used_str = fmt_capacity(status.get('disk_used'))
                        else:
                            try:
                                val_d = float(re.findall(r'[-+]?\d*\.\d+|\d+', str(disk_t_raw))[0]) if isinstance(disk_t_raw, str) else float(disk_t_raw)
                                disk_used_str = fmt_capacity(val_d * (disk_p / 100.0))
                            except:
                                pass
                        refs['disk_val'].set_text(f'{disk_used_str} / {disk_total_str}')

                        raw_arch = static.get('arch', '').lower()
                        display_arch = 'AMD' if 'x86' in raw_arch or 'amd' in raw_arch else 'ARM' if 'arm' in raw_arch or 'aarch' in raw_arch else raw_arch.upper()
                        refs['os'].set_text(static.get('os', 'Linux'))
                        refs['arch'].set_text(display_arch)
                        refs['virt'].set_text(static.get('virt', 'kvm'))
                        uptime_str = str(status.get('uptime', '-')).replace('up ', '').replace('days', '天').replace('hours', '时').replace('minutes', '分')
                        refs['uptime'].set_text(uptime_str)
                        refs['uptime'].classes('text-green-500')

                        pings = status.get('pings', {})
                        refs['ping_ct_cur'].set_text(str(pings.get('电信', 'N/A')))
                        refs['ping_cu_cur'].set_text(str(pings.get('联通', 'N/A')))
                        refs['ping_cm_cur'].set_text(str(pings.get('移动', 'N/A')))

                        history_data = state.PING_TREND_CACHE.get(server_conf['url'], [])
                        if history_data:
                            current_mode = chart_tabs.value
                            duration = 3600 if current_mode == '1h' else 10800 if current_mode == '3h' else 21600
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
                    except:
                        pass

                chart_tabs.on_value_change(update_dark_detail)

            with ui.row().classes(f'w-full justify-center p-2 {CARD_BG} border-t border-white/50 dark:border-[#1e3a5f]/45 dark:bg-[#030712]'):
                ui.label('Powered by X-Fusion Monitor').classes('text-[10px] text-gray-500 dark:text-cyan-500/70 font-mono italic font-bold')

        d.open()
        asyncio.create_task(update_dark_detail())
        timer = ui.timer(2.0, update_dark_detail)
        d.on('hide', lambda: timer.cancel())
    except Exception as e:
        print(f'PC Detail Error: {e}')


async def render_desktop_status_page():
    global CURRENT_PROBE_TAB
    CURRENT_PROBE_TAB = state.CURRENT_PROBE_TAB

    # 1. 启用 Dark Mode
    dark_mode = ui.dark_mode()
    if app.storage.user.get('is_dark') is None:
        app.storage.user['is_dark'] = True
    dark_mode.value = app.storage.user.get('is_dark')

    ui.add_head_html('<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>')
    ui.add_head_html('<link href="https://use.fontawesome.com/releases/v6.4.0/css/all.css" rel="stylesheet">')
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
            body:not(.body--dark) { background: linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%); }
            body.body--dark { background-color: #030712; }
            .status-card { transition: all 0.3s ease; border-radius: 8px; }
            body:not(.body--dark) .status-card { background: rgba(255, 255, 255, 0.95); border: 1px solid rgba(191, 219, 254, 0.8); box-shadow: 0 8px 24px 0 rgba(30, 58, 95, 0.12); color: #1e293b; }
            body.body--dark .status-card { background: #070b14; border: 1px solid rgba(30,58,95,0.55); box-shadow: 0 0 16px rgba(0, 0, 0, 0.28); color: #e2e8f0; }
            .status-card:hover { transform: translateY(-3px); }
            .offline-card { border-color: rgba(244, 63, 94, 0.6) !important; }
            body.body--dark .offline-card { background-image: repeating-linear-gradient(45deg, rgba(244, 63, 94, 0.05) 0px, rgba(244, 63, 94, 0.05) 10px, transparent 10px, transparent 20px) !important; }

            body:not(.body--dark) .offline-card { background: rgba(254, 226, 226, 0.95) !important; }
            .scrollbar-hide::-webkit-scrollbar { display: none; }
            .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
            .prog-bar { transition: width 0.5s ease-out; }
            #public-map-container { contain: strict; transform: translateZ(0); will-change: transform; z-index: 0; }
        </style>
    ''')

    rendered_cards = {}
    tab_container = None
    grid_container = None
    header_refs = {}
    pie_chart_ref = None
    pagination_ref = None
    view_mode_btn = None
    list_header = None
    local_ui_version = state.GLOBAL_UI_VERSION
    page_state = {'page': 1, 'group': 'ALL'}

    view_mode = app.storage.user.get('status_view_mode', 'grid') if app.storage.user else 'grid'

    def toggle_view_mode():
        nonlocal view_mode
        view_mode = 'list' if view_mode == 'grid' else 'grid'
        if app.storage.user:
            app.storage.user['status_view_mode'] = view_mode
        if view_mode_btn:
            view_mode_btn.props(f'icon={"grid_view" if view_mode == "list" else "view_list"}')
        render_grid_page()

    def get_probe_groups():
        groups_list = ['ALL']
        groups_list.extend(state.ADMIN_CONFIG.get('probe_custom_groups', []))
        return groups_list

    def fmt_capacity(b):
        if b is None:
            return '0 B'
        try:
            if isinstance(b, str):
                nums = re.findall(r'[-+]?\d*\.\d+|\d+', b)
                val = float(nums[0]) if nums else 0
            else:
                val = float(b)
            if val > 1024 * 1024:
                if val < 1024**3:
                    return f'{val/1024**2:.1f} MB'
                return f'{val/1024**3:.1f} GB'
            if val > 0:
                return f'{val:.1f} GB'
            return '0 B'
        except:
            return str(b)

    def fmt_traffic(b):
        return f'{round(b/1024**3, 1)}G' if b > 1024**3 else f'{int(b/1024**2)}M'

    def fmt_speed(b):
        return f'{int(b)} B' if b < 1024 else (f'{int(b/1024)} K' if b < 1024**2 else f'{int(b/1024**2)} M')

    try:
        chart_data, pie_data, region_count, region_stats_json, centroids_json = prepare_map_data()
    except Exception:
        chart_data = '{"cities": [], "flags": [], "regions": []}'
        pie_data, region_count, region_stats_json, centroids_json = [], 0, '{}', '{}'

    with ui.element('div').classes('fixed top-0 left-0 w-full h-[35vh] min-h-[300px] max-h-[500px] z-0').style('z-index: 0; contain: size layout paint;'):
        ui.html('<div id="public-map-container" style="width:100%; height:100%;"></div>', sanitize=False).classes('w-full h-full')

    with ui.column().classes('w-full h-screen p-0 gap-0 overflow-hidden flex flex-col absolute top-0 left-0 pointer-events-none'):
        with ui.element('div').classes('w-full h-[35vh] min-h-[300px] max-h-[500px] relative p-0 shrink-0 pointer-events-none'):
            with ui.row().classes('absolute top-6 left-8 right-8 z-50 justify-between items-start pointer-events-auto'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('public', color='blue').classes('text-3xl text-cyan-400 drop-shadow-[0_0_10px_rgba(34,211,238,0.65)]')
                        ui.label('X-Fusion Status').classes('text-2xl font-black text-slate-800 dark:text-slate-100 drop-shadow-md tracking-wide')
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
                    ui.button(icon='dark_mode', on_click=toggle_dark).props('flat round dense').classes('text-slate-700 dark:text-cyan-300 bg-white/50 dark:bg-[#070b14]/80 border border-white/40 dark:border-[#1e3a5f]/45')
                    ui.button('后台管理', icon='login', on_click=lambda: ui.navigate.to('/login')).props('flat dense').classes('font-black text-xs text-slate-700 dark:text-slate-300 bg-white/50 dark:bg-[#070b14]/80 border border-white/40 dark:border-[#1e3a5f]/45 rounded-sm px-2')
            with ui.element('div').classes('absolute left-4 bottom-4 z-40 pointer-events-auto'):
                text_color = '#e2e8f0' if dark_mode.value else '#334155'
                pie_chart_ref = ui.echart({'backgroundColor': 'transparent', 'tooltip': {'trigger': 'item'}, 'legend': {'bottom': '0%', 'left': 'center', 'icon': 'circle', 'itemGap': 15, 'textStyle': {'color': text_color, 'fontSize': 11}}, 'series': [{'type': 'pie', 'radius': ['35%', '60%'], 'center': ['50%', '35%'], 'avoidLabelOverlap': False, 'itemStyle': {'borderRadius': 4, 'borderColor': 'transparent', 'borderWidth': 2}, 'label': {'show': False}, 'emphasis': {'scale': True, 'scaleSize': 10, 'label': {'show': True, 'color': 'auto', 'fontWeight': 'bold'}, 'itemStyle': {'shadowBlur': 10, 'shadowOffsetX': 0, 'shadowColor': 'rgba(0, 0, 0, 0.5)'}}, 'data': pie_data}]}).classes('w-64 h-72')

        with ui.column().classes('w-full flex-grow relative gap-0 overflow-hidden flex flex-col bg-white/80 dark:bg-[#030712]/92 backdrop-blur-xl pointer-events-auto border-t border-white/10 dark:border-[#1e3a5f]/45').style('z-index: 10; contain: content;'):
            with ui.row().classes('w-full px-6 py-2 border-b border-gray-200/50 dark:border-[#1e3a5f]/45 items-center shrink-0 justify-between bg-white/20 dark:bg-[#070b14]/80'):
                with ui.element('div').classes('flex-grow overflow-x-auto whitespace-nowrap scrollbar-hide mr-4') as tab_container:
                    pass
                with ui.row().classes('items-center gap-2 shrink-0'):
                    view_mode_btn = ui.button(icon='grid_view' if view_mode == 'list' else 'view_list', on_click=toggle_view_mode).props('flat dense').classes('text-slate-500 dark:text-slate-400')
                    pagination_ref = ui.row().classes('items-center')

            with ui.scroll_area().classes('w-full flex-grow p-4 md:p-6'):
                list_header = ui.row().classes('w-full px-4 mb-2 text-xs font-bold text-slate-500 dark:text-slate-400 flex-nowrap items-center hidden gap-2')
                with list_header:
                    ui.label('状态').classes('w-10 text-center shrink-0')
                    ui.label('系统').classes('w-10 text-center shrink-0')
                    ui.label('节点').classes('flex-[2] min-w-[120px] shrink-0')
                    ui.label('标签').classes('w-16 shrink-0 text-center')
                    ui.label('运行时间').classes('w-24 shrink-0 text-center')
                    ui.label('CPU').classes('flex-1 min-w-[80px] shrink-0')
                    ui.label('内存').classes('flex-[1.2] min-w-[100px] shrink-0')
                    ui.label('硬盘').classes('flex-[1.2] min-w-[100px] shrink-0')
                    ui.label('流量').classes('flex-1 min-w-[80px] shrink-0 text-right')
                    ui.label('速率').classes('flex-1 min-w-[80px] shrink-0 text-right')

                grid_container = ui.element('div').classes('w-full pb-20')

    def render_tabs():
        tab_container.clear()
        groups = get_probe_groups(); global CURRENT_PROBE_TAB
        if CURRENT_PROBE_TAB not in groups:
            CURRENT_PROBE_TAB = 'ALL'
            state.CURRENT_PROBE_TAB = 'ALL'
        page_state['group'] = CURRENT_PROBE_TAB
        with tab_container:
            with ui.tabs().props('dense no-caps align=left active-color=cyan-4 indicator-color=cyan-4').classes('text-slate-600 dark:text-slate-400 bg-transparent') as tabs:
                ui.tab('ALL', label='全部').on('click', lambda: apply_filter('ALL'))
                for g in groups:
                    if g == 'ALL':
                        continue
                    ui.tab(g).on('click', lambda _, g=g: apply_filter(g))
                tabs.set_value(CURRENT_PROBE_TAB)

    def update_card_ui(refs, status, static):
        if not status:
            return
        is_probe_online = status.get('status') == 'online'
        if is_probe_online:
            refs['status_icon'].set_name('bolt')
            refs['status_icon'].classes(replace='text-green-500', remove='text-gray-400 text-red-500 text-purple-400')
            refs['online_dot'].classes(replace='bg-green-500', remove='bg-gray-500 bg-red-500 bg-purple-500')
        else:
            if status.get('cpu_usage') is not None:
                refs['status_icon'].set_name('api')
                refs['status_icon'].classes(replace='text-purple-400', remove='text-gray-400 text-red-500 text-green-500')
                refs['online_dot'].classes(replace='bg-purple-500', remove='bg-gray-500 bg-red-500 bg-green-500')
            else:
                refs['status_icon'].set_name('flash_off')
                refs['status_icon'].classes(replace='text-red-500', remove='text-green-500 text-gray-400 text-purple-400')
                refs['online_dot'].classes(replace='bg-red-500', remove='bg-green-500 bg-orange-500 bg-purple-500')

        refs['os_info'].set_text(re.sub(r' GNU/Linux', '', static.get('os', 'Linux'), flags=re.I))
        cores = status.get('cpu_cores')
        refs['summary_cores'].set_text(f'{cores} C' if cores else 'N/A')
        refs['summary_ram'].set_text(fmt_capacity(status.get('mem_total', 0)))
        refs['summary_disk'].set_text(fmt_capacity(status.get('disk_total', 0)))
        refs['traf_up'].set_text(f"↑ {fmt_traffic(status.get('net_total_out', 0))}")
        refs['traf_down'].set_text(f"↓ {fmt_traffic(status.get('net_total_in', 0))}")

        cpu = float(status.get('cpu_usage', 0))
        refs['cpu_bar'].style(f'width: {cpu}%')
        refs['cpu_pct'].set_text(f'{cpu:.1f}%')
        refs['cpu_sub'].set_text(f"{status.get('cpu_cores', 1)} Cores")

        mem = float(status.get('mem_usage', 0))
        refs['mem_bar'].style(f'width: {mem}%')
        refs['mem_pct'].set_text(f'{mem:.1f}%')
        mem_total = float(status.get('mem_total', 0))
        refs['mem_sub'].set_text(f"{fmt_capacity(mem_total * (mem / 100.0))} / {fmt_capacity(mem_total)}" if mem_total > 0 else f'{mem:.1f}%')

        disk = float(status.get('disk_usage', 0))
        refs['disk_bar'].style(f'width: {disk}%')
        refs['disk_pct'].set_text(f'{disk:.1f}%')
        disk_total = float(status.get('disk_total', 0))
        refs['disk_sub'].set_text(f"{fmt_capacity(disk_total * (disk / 100.0))} / {fmt_capacity(disk_total)}" if disk_total > 0 else f'{disk:.1f}%')

        refs['net_up'].set_text(f"↑ {fmt_speed(status.get('net_speed_out', 0))}/s")
        refs['net_down'].set_text(f"↓ {fmt_speed(status.get('net_speed_in', 0))}/s")
        up = str(status.get('uptime', '-'))
        colored_up = re.sub(r'(\d+)(\s*(?:days?|天))', r'<span class="text-green-500 font-bold text-sm">\1</span>\2', up, flags=re.IGNORECASE)
        refs['uptime'].set_content(colored_up)

    async def card_autoupdate_loop(url):
        current_server = next((s for s in state.SERVERS_CACHE if s['url'] == url), None)
        if not current_server or not current_server.get('probe_installed', False):
            return
        await asyncio.sleep(random.uniform(0.5, 3.0))
        while True:
            if url not in rendered_cards or url not in [s['url'] for s in state.SERVERS_CACHE]:
                break
            item = rendered_cards.get(url)
            if not item:
                break
            if not item['card'].visible:
                await asyncio.sleep(5.0)
                continue
            current_server = next((s for s in state.SERVERS_CACHE if s['url'] == url), None)
            if current_server:
                try:
                    res = await asyncio.wait_for(get_server_status(current_server), timeout=5.0)
                except:
                    res = None
                if res:
                    static = state.PROBE_DATA_CACHE.get(url, {}).get('static', {})
                    update_card_ui(item['refs'], res, static)
                    if res.get('status') == 'online':
                        item['card'].classes(remove='offline-card')
                    else:
                        item['card'].classes(add='offline-card')
            await asyncio.sleep(random.uniform(2.0, 3.0))

    def create_server_card(s):
        url = s['url']
        refs = {}
        cached_data = state.PROBE_DATA_CACHE.get(url, {})
        initial_status = cached_data.copy() if cached_data else None
        if initial_status and 'pings' not in initial_status:
            initial_status['pings'] = {}

        with grid_container:
            if view_mode == 'list':
                with ui.card().classes('status-card w-full p-2 px-4 flex flex-row items-center relative overflow-hidden group hover:bg-white dark:hover:bg-[#070b14]/80 flex-nowrap gap-2').style('contain: content; border-radius: 6px;') as card:
                    refs['card'] = card
                    ui.element('div').classes('absolute inset-0 hidden dark:block bg-[linear-gradient(135deg,rgba(34,211,238,0.02),transparent_35%)] pointer-events-none')

                    with ui.column().classes('w-10 items-center justify-center shrink-0'):
                        refs['online_dot'] = ui.element('div').classes('w-2 h-2 rounded-full bg-gray-400 shadow-sm')
                        refs['status_icon'] = ui.icon('bolt').classes('hidden')

                    with ui.column().classes('w-10 items-center justify-center shrink-0'):
                        refs['os_icon'] = ui.icon('computer').classes('text-lg text-slate-400')
                        refs['os_info'] = ui.label('Loading...').classes('hidden')

                    with ui.row().classes('flex-[2] min-w-[120px] shrink-0 items-center gap-2 truncate cursor-pointer').on('click', lambda _, s=s: open_pc_server_detail(s)):
                        flag = '🏳️'
                        try:
                            flag = detect_country_group(s['name'], s).split(' ')[0]
                        except:
                            pass
                        ui.label(flag).classes('text-xl shrink-0')
                        ui.label(s['name']).classes('text-[14px] font-black text-slate-800 dark:text-slate-100 truncate hover:text-cyan-500 transition')

                    with ui.column().classes('w-16 shrink-0 text-center items-center justify-center'):
                        tags_list = s.get('tags', [])
                        tags_str = tags_list[0] if tags_list else '--'
                        ui.label(tags_str).classes('text-[11px] text-slate-500 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded truncate max-w-[55px]')

                    with ui.column().classes('w-24 shrink-0 items-center justify-center'):
                        refs['uptime'] = ui.html('--', sanitize=False).classes('text-xs font-mono text-slate-600 dark:text-gray-400')
                        refs['summary_cores'] = ui.label('--').classes('hidden')
                        refs['summary_ram'] = ui.label('--').classes('hidden')
                        refs['summary_disk'] = ui.label('--').classes('hidden')

                    def list_stat_col(width_cls, color_cls, bg_cls):
                        with ui.column().classes(f'{width_cls} shrink-0 pr-4'):
                            with ui.row().classes('w-full justify-between items-end mb-[2px] flex-nowrap'):
                                pct = ui.label('0%').classes('text-xs font-mono font-bold text-slate-700 dark:text-gray-300')
                                sub = ui.label('').classes('text-[11px] text-slate-400 truncate')
                            with ui.element('div').classes(f'w-full h-1.5 {bg_cls} rounded-full overflow-hidden'):
                                bar = ui.element('div').classes(f'h-full {color_cls} prog-bar').style('width: 0%')
                        return bar, pct, sub

                    refs['cpu_bar'], refs['cpu_pct'], refs['cpu_sub'] = list_stat_col('flex-1 min-w-[80px]', 'bg-blue-500', 'bg-blue-100 dark:bg-gray-700/50')
                    refs['mem_bar'], refs['mem_pct'], refs['mem_sub'] = list_stat_col('flex-[1.2] min-w-[100px]', 'bg-green-500', 'bg-green-100 dark:bg-gray-700/50')
                    refs['disk_bar'], refs['disk_pct'], refs['disk_sub'] = list_stat_col('flex-[1.2] min-w-[100px]', 'bg-purple-500', 'bg-purple-100 dark:bg-gray-700/50')

                    with ui.column().classes('flex-1 min-w-[80px] shrink-0 items-end justify-center font-mono text-xs text-slate-600 dark:text-gray-400 whitespace-nowrap'):
                        refs['traf_up'] = ui.label('↑ 0B')
                        refs['traf_down'] = ui.label('↓ 0B')

                    with ui.column().classes('flex-1 min-w-[80px] shrink-0 items-end justify-center font-mono text-xs whitespace-nowrap'):
                        refs['net_up'] = ui.label('↑ 0B/s').classes('text-orange-500 dark:text-orange-400 font-bold')
                        refs['net_down'] = ui.label('↓ 0B/s').classes('text-green-600 dark:text-green-400 font-bold')
            else:
                with ui.card().classes('status-card w-full p-4 md:p-5 flex flex-col gap-2 md:gap-3 relative overflow-hidden group').style('contain: content;') as card:
                    ui.element('div').classes('absolute inset-0 hidden dark:block bg-[linear-gradient(135deg,rgba(34,211,238,0.03),transparent_45%)] pointer-events-none')
                    refs['card'] = card
                    with ui.row().classes('w-full items-center mb-1 gap-2 flex-nowrap'):
                        flag = '🏳️'
                        try:
                            flag = detect_country_group(s['name'], s).split(' ')[0]
                        except:
                            pass
                        ui.label(flag).classes('text-2xl md:text-3xl flex-shrink-0 leading-none')
                        ui.label(s['name']).classes('text-base md:text-lg font-black text-slate-800 dark:text-slate-100 truncate flex-grow min-w-0 cursor-pointer hover:text-cyan-500 dark:hover:text-cyan-300 transition leading-tight').on('click', lambda _, s=s: open_pc_server_detail(s))
                        refs['status_icon'] = ui.icon('bolt').props('size=32px').classes('text-gray-400 flex-shrink-0')
                    with ui.row().classes('w-full justify-between items-center px-1 mb-2'):
                        with ui.row().classes('items-center gap-1.5'):
                            ui.icon('dns').classes('text-xs text-gray-400')
                            ui.label('OS').classes('text-xs text-slate-500 dark:text-gray-400 font-bold')
                        with ui.row().classes('items-center gap-1.5'):
                            refs['os_icon'] = ui.icon('computer').classes('text-xs text-slate-400')
                            refs['os_info'] = ui.label('Loading...').classes('text-xs font-mono font-bold text-slate-700 dark:text-gray-300 whitespace-nowrap')
                    ui.separator().classes('mb-3 opacity-50 dark:opacity-30')
                    with ui.row().classes('w-full justify-between px-1 mb-1 md:mb-2'):
                        label_cls = 'text-xs font-mono text-slate-500 dark:text-gray-400 font-bold'
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('grid_view').classes('text-blue-500 dark:text-blue-400 text-xs')
                            refs['summary_cores'] = ui.label('--').classes(label_cls)
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('memory').classes('text-green-500 dark:text-green-400 text-xs')
                            refs['summary_ram'] = ui.label('--').classes(label_cls)
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('storage').classes('text-purple-500 dark:text-purple-400 text-xs')
                            refs['summary_disk'] = ui.label('--').classes(label_cls)
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
                            ui.label('网络').classes(label_sub_cls)
                            with ui.row().classes('gap-2 font-mono whitespace-nowrap'):
                                refs['net_up'] = ui.label('↑ 0B').classes('text-xs text-orange-500 dark:text-orange-400 font-bold')
                                refs['net_down'] = ui.label('↓ 0B').classes('text-xs text-green-600 dark:text-green-400 font-bold')
                        with ui.row().classes('w-full justify-between items-center no-wrap'):
                            ui.label('流量').classes(label_sub_cls)
                            with ui.row().classes('gap-2 font-mono whitespace-nowrap text-xs text-slate-600 dark:text-gray-300'):
                                refs['traf_up'] = ui.label('↑ 0B')
                                refs['traf_down'] = ui.label('↓ 0B')
                        with ui.row().classes('w-full justify-between items-center no-wrap'):
                            ui.label('在线').classes(label_sub_cls)
                            with ui.row().classes('items-center gap-1'):
                                refs['uptime'] = ui.html('--', sanitize=False).classes('text-xs font-mono text-slate-600 dark:text-gray-300 text-right')
                                refs['online_dot'] = ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-gray-400')

        if initial_status:
            update_card_ui(refs, initial_status, cached_data.get('static', {}))
            if initial_status.get('status') == 'online' or initial_status.get('cpu_usage') is not None:
                card.classes(remove='offline-card')
            else:
                card.classes(add='offline-card')

        rendered_cards[url] = {'card': card, 'refs': refs, 'data': s}
        asyncio.create_task(card_autoupdate_loop(url))

    def apply_filter(group_name):
        global CURRENT_PROBE_TAB
        CURRENT_PROBE_TAB = group_name
        state.CURRENT_PROBE_TAB = group_name
        page_state['group'] = group_name
        page_state['page'] = 1
        render_grid_page()

    def change_page(new_page):
        page_state['page'] = new_page
        render_grid_page()

    def render_grid_page():
        grid_container.clear()
        pagination_ref.clear()
        rendered_cards.clear()
        group_name = page_state['group']
        try:
            sorted_all = sorted(state.SERVERS_CACHE, key=lambda x: x.get('name', ''))
        except:
            sorted_all = state.SERVERS_CACHE
        filtered_servers = [s for s in sorted_all if group_name == 'ALL' or group_name in s.get('tags', [])]

        if view_mode == 'grid':
            list_header.classes(add='hidden', remove='flex')
            grid_container.classes(add='grid gap-4 md:gap-5', remove='flex flex-col gap-1.5').style('grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))')
        else:
            list_header.classes(add='flex', remove='hidden')
            grid_container.classes(add='flex flex-col gap-1.5', remove='grid gap-4 md:gap-5').style('grid-template-columns: none')

        page_size = 60
        total_items = len(filtered_servers)
        total_pages = (total_items + page_size - 1) // page_size
        if page_state['page'] > total_pages:
            page_state['page'] = 1
        if page_state['page'] < 1:
            page_state['page'] = 1
        start_idx = (page_state['page'] - 1) * page_size
        current_page_items = filtered_servers[start_idx:start_idx + page_size]

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
        function checkAndRender() {{
            var chartDom = document.getElementById('public-map-container');
            if (!chartDom || typeof echarts === 'undefined') {{ setTimeout(checkAndRender, 100); return; }}
            fetch('/static/world.json').then(r => r.json()).then(w => {{
                echarts.registerMap('world', w);
                myChart = echarts.init(chartDom);
                window.publicMapChart = myChart;
                if (navigator.geolocation) {{
                    navigator.geolocation.getCurrentPosition(
                        p => {{ defaultPt = [p.coords.longitude, p.coords.latitude]; if(!isZoomed) renderMap(); }},
                        e => {{}},
                        {{ enableHighAccuracy: false, timeout: 2500, maximumAge: 600000 }}
                    );
                }}
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
                    var ttBg = isDark ? 'rgba(23, 23, 23, 0.95)' : 'rgba(255, 255, 255, 0.95)';
                    var ttTextMain = isDark ? '#fff' : '#1e293b';
                    var ttTextSub = isDark ? 'rgba(255, 255, 255, 0.6)' : 'rgba(30, 41, 59, 0.6)';
                    var ttBorder = isDark ? '1px solid rgba(255,255,255,0.1)' : '1px solid #e2e8f0';
                    var emojiFont = "'Twemoji Country Flags', 'Noto Sans SC', 'Roboto', 'Helvetica Neue', 'Arial', sans-serif";
                    var highlightFill = isDark ? 'rgba(37, 99, 235, 0.4)' : 'rgba(147, 197, 253, 0.5)';
                    var highlightStroke = isDark ? '#3b82f6' : '#2563eb';
                    var geoRegions = (mapData.regions || []).map(function(name) {{
                        return {{name: name, itemStyle: {{areaColor: highlightFill, borderColor: highlightStroke, borderWidth: 1.5, opacity: 1}}, emphasis: {{itemStyle: {{areaColor: highlightFill, borderColor: '#60a5fa', borderWidth: 2}}}}}};
                    }});
                    var option = {{
                        backgroundColor: 'transparent',
                        tooltip: {{
                            show: true, trigger: 'item', padding: 0, backgroundColor: 'transparent', borderColor: 'transparent',
                            formatter: function(params) {{
                                var searchKey = params.data && params.data.country_key ? params.data.country_key : params.name;
                                var stats = window.regionStats[searchKey];
                                if (!stats) return;
                                var serverListHtml = '';
                                var displayLimit = 5;
                                var servers = stats.servers || [];
                                for (var i = 0; i < Math.min(servers.length, displayLimit); i++) {{
                                    var s = servers[i];
                                    var isOnline = s.status === 'online';
                                    var statusColor = isOnline ? '#22c55e' : '#ef4444';
                                    var statusText = isOnline ? '在线' : '离线';
                                    serverListHtml += `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;line-height:1.2;"><div style="display:flex;align-items:center;max-width:170px;"><span style="display:inline-block;width:6px;height:6px;border-radius:50%;background-color:${{statusColor}};margin-right:8px;flex-shrink:0;"></span><span style="font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${{s.name}}</span></div><span style="font-size:12px;color:${{ttTextSub}};flex-shrink:0;margin-left:8px;">${{statusText}}</span></div>`;
                                }}
                                if (servers.length > displayLimit) {{
                                    serverListHtml += `<div style="font-size:11px;color:${{ttTextSub}};margin-top:8px;text-align:right;opacity:0.8;">+${{servers.length - displayLimit}} 更多...</div>`;
                                }}
                                return `<div style="background:${{ttBg}}; border:${{ttBorder}}; padding:14px 16px; border-radius:10px; color:${{ttTextMain}}; font-family:${{emojiFont}}; box-shadow:0 4px 16px rgba(0,0,0,0.3); min-width:240px; max-width:260px; pointer-events:none;"><div style="font-size:16px; font-weight:700; margin-bottom:2px; display:flex; align-items:center; letter-spacing:0.5px;"><span style="margin-right:8px; font-size:20px;">${{stats.flag}}</span>${{stats.cn}}</div><div style="font-size:12px; color:${{ttTextSub}}; margin-bottom:12px; font-weight:400;">共 ${{stats.total}} 台服务器, ${{stats.online}} 台在线</div><div style="border-top:1px solid ${{isDark ? 'rgba(255,255,255,0.08)' : '#f1f5f9'}}; padding-top:10px; margin-top:4px;">${{serverListHtml}}</div></div>`;
                            }}
                        }},
                        geo: {{map: 'world', left: mapLeft, top: mapTop, roam: viewRoam, zoom: viewZoom, center: viewCenter, aspectScale: 0.85, label: {{show: false}}, itemStyle: {{areaColor: areaColor, borderColor: borderColor, borderWidth: 1}}, emphasis: {{itemStyle: {{areaColor: isDark ? '#1e3a8a' : '#bfdbfe'}}}}, regions: geoRegions}},
                        series: [
                            {{type: 'lines', zlevel: 2, effect: {{show: true, period: 4, trailLength: 0.5, color: '#00ffff', symbol: 'arrow', symbolSize: 6}}, lineStyle: {{color: '#00ffff', width: 0, curveness: 0.2, opacity: 0}}, data: lines, silent: true}},
                            {{type: 'effectScatter', coordinateSystem: 'geo', zlevel: 3, rippleEffect: {{brushType: 'stroke', scale: 2.5}}, itemStyle: {{color: '#00ffff'}}, data: mapData.cities}},
                            {{type: 'scatter', coordinateSystem: 'geo', zlevel: 6, symbolSize: 0, label: {{show: true, position: 'top', formatter: '{{b}}', color: isDark?'#fff':'#1e293b', fontSize: 16, offset: [0, -5], fontFamily: emojiFont}}, data: mapData.flags}},
                            {{type: 'effectScatter', coordinateSystem: 'geo', zlevel: 5, itemStyle: {{color: '#f59e0b'}}, label: {{show: true, position: 'bottom', formatter: 'My PC', color: '#f59e0b', fontWeight: 'bold'}}, data: [{{ value: defaultPt }}]}}
                        ]
                    }};
                    myChart.setOption(option, true);
                }}
                window.updatePublicMap = function(newData) {{
                    if (!newData) return;
                    mapData = newData;
                    renderMap(isZoomed ? myChart.getOption().geo[0].center : defaultPt, isZoomed ? myChart.getOption().geo[0].zoom : defaultZoom, isZoomed ? 'move' : false);
                }};
                myChart.on('click', function(params) {{
                    var searchKey = params.data && params.data.country_key ? params.data.country_key : params.name;
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

    async def loop_update():
        nonlocal local_ui_version
        try:
            if state.GLOBAL_UI_VERSION != local_ui_version:
                local_ui_version = state.GLOBAL_UI_VERSION
                render_tabs()
                render_grid_page()
                try:
                    new_map, _, new_cnt, new_stats, new_centroids = prepare_map_data()
                except:
                    new_map, new_cnt, new_stats, new_centroids = '{}', 0, '{}', '{}'
                if header_refs.get('region_count'):
                    header_refs['region_count'].set_text(f'分布区域: {new_cnt}')
                ui.run_javascript(f'''if(window.updatePublicMap){{ window.regionStats = {new_stats}; window.countryCentroids = {new_centroids}; window.updatePublicMap({new_map}); }}''')

            real_online_count = 0
            now_ts = time.time()
            for s in state.SERVERS_CACHE:
                probe_cache = state.PROBE_DATA_CACHE.get(s['url'])
                is_node_online = bool(probe_cache and (now_ts - probe_cache.get('last_updated', 0) < 20)) or s.get('_status') == 'online'
                if is_node_online:
                    real_online_count += 1
            if header_refs.get('online_count'):
                header_refs['online_count'].set_text(f'在线: {real_online_count}')
        except:
            pass
        ui.timer(5.0, loop_update, once=True)

    ui.timer(0.1, loop_update, once=True)


async def render_mobile_status_page():
    global CURRENT_PROBE_TAB
    CURRENT_PROBE_TAB = state.CURRENT_PROBE_TAB
    # 用于存储 UI 组件引用的字典，实现局部刷新
    mobile_refs = {}
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
        <style>
            body { background-color: #030712; color: #ffffff; margin: 0; padding: 0; overflow-x: hidden; }
            .mobile-header { background: #070b14; border-bottom: 1px solid rgba(30,58,95,0.6); position: sticky; top: 0; z-index: 100; padding: 12px 16px; }
            .mobile-card-container { display: flex; flex-direction: column; align-items: center; width: 100%; padding: 12px 0; }
            .mobile-card { background: #070b14; border-radius: 8px; padding: 18px; border: 1px solid rgba(30,58,95,0.55); width: calc(100% - 24px); margin-bottom: 16px; box-sizing: border-box; box-shadow: 0 0 14px rgba(0,0,0,0.22); }
            .inner-module { background: #0a1120; border-radius: 8px; padding: 12px; height: 95px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(30,58,95,0.35); }
            .stat-header { display: flex; justify-content: space-between; align-items: center; }
            .stat-label-box { display: flex; align-items: center; gap: 4px; }
            .stat-icon { font-size: 14px !important; color: #888; }
            .stat-label { color: #888; font-size: 11px; font-weight: bold; }
            .stat-value { color: #fff; font-size: 17px; font-weight: 800; font-family: monospace; }
            .bar-bg { height: 5px; background: #050b14; border-radius: 3px; overflow: hidden; margin: 2px 0; border: 1px solid rgba(30,58,95,0.25); }
            .bar-fill-cpu { height: 100%; background: #3b82f6; transition: width 0.6s; box-shadow: 0 0 5px #3b82f6; }
            .bar-fill-mem { height: 100%; background: #22c55e; transition: width 0.6s; box-shadow: 0 0 5px #22c55e; }
            .bar-fill-disk { height: 100%; background: #a855f7; }
            .stat-subtext { color: #555; font-size: 10px; font-family: monospace; font-weight: bold; }
            .speed-up { color: #22c55e; font-weight: bold; font-size: 11px; }
            .speed-down { color: #3b82f6; font-weight: bold; font-size: 11px; }
            .scrollbar-hide::-webkit-scrollbar { display: none; }
        </style>
    ''')

    with ui.column().classes('mobile-header w-full gap-1'):
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('X-Fusion Status').classes('text-lg font-black text-cyan-400 tracking-wide')
            ui.button(icon='login', on_click=lambda: ui.navigate.to('/login')).props('flat dense color=grey-5').classes('text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30')
        online_count = len([s for s in state.SERVERS_CACHE if s.get('_status') == 'online'])
        ui.label(f'🟢 {online_count} ONLINE / {len(state.SERVERS_CACHE)} TOTAL').classes('text-[10px] font-black text-cyan-500/70 tracking-widest')

    with ui.row().classes('w-full px-2 py-1 bg-[#030712] border-b border-[#1e3a5f]/45 overflow-x-auto whitespace-nowrap scrollbar-hide'):
        groups = ['ALL'] + state.ADMIN_CONFIG.get('probe_custom_groups', [])
        with ui.tabs().props('dense no-caps active-color=cyan-4 indicator-color=cyan-4').classes('text-slate-500') as tabs:
            for g in groups:
                ui.tab(g, label='全部' if g == 'ALL' else g).on('click', lambda _, group=g: update_mobile_tab(group))
            tabs.set_value(CURRENT_PROBE_TAB)

    list_container = ui.column().classes('mobile-card-container')

    async def render_list(target_group):
        list_container.clear()
        mobile_refs.clear()
        filtered = [s for s in state.SERVERS_CACHE if target_group == 'ALL' or target_group in s.get('tags', [])]
        filtered.sort(key=lambda x: (0 if x.get('_status') == 'online' else 1, x.get('name', '')))
        with list_container:
            for s in filtered:
                status = state.PROBE_DATA_CACHE.get(s['url'], {})
                is_online = s.get('_status') == 'online'
                srv_ref = {}
                with ui.column().classes('mobile-card').on('click', lambda _, srv=s: open_mobile_server_detail(srv)):
                    with ui.row().classes('items-center gap-3 mb-3'):
                        flag = '🏳️'
                        try:
                            flag = detect_country_group(s['name'], s).split(' ')[0]
                        except:
                            pass
                        ui.label(flag).classes('text-3xl')
                        ui.label(s['name']).classes('text-base font-bold truncate').style('max-width:200px')

                    with ui.grid().classes('w-full grid-cols-2 gap-3'):
                        cpu = status.get('cpu_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">settings_suggest</span><span class="stat-label">CPU</span></div>', sanitize=False)
                                srv_ref['cpu_text'] = ui.label(f'{cpu}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                srv_ref['cpu_bar'] = ui.element('div').classes('bar-fill-cpu').style(f'width: {cpu}%')
                            ui.label(f"{status.get('cpu_cores', 1)} Cores").classes('stat-subtext')

                        mem_p = status.get('mem_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">memory</span><span class="stat-label">RAM</span></div>', sanitize=False)
                                srv_ref['mem_text'] = ui.label(f'{int(mem_p)}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                srv_ref['mem_bar'] = ui.element('div').classes('bar-fill-mem').style(f'width: {mem_p}%')
                            srv_ref['mem_detail'] = ui.label('-- / --').classes('stat-subtext')

                        disk_p = status.get('disk_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">storage</span><span class="stat-label">DISK</span></div>', sanitize=False)
                                ui.label(f'{int(disk_p)}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                ui.element('div').classes('bar-fill-disk').style(f'width: {disk_p}%')
                            ui.label(f"{status.get('disk_total', 0)}G Total").classes('stat-subtext')

                        with ui.element('div').classes('inner-module'):
                            ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">swap_calls</span><span class="stat-label">SPEED</span></div>', sanitize=False)
                            with ui.column().classes('w-full gap-0'):
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('↑').classes('speed-up')
                                    srv_ref['net_up'] = ui.label('--').classes('text-[12px] font-mono font-bold')
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('↓').classes('speed-down')
                                    srv_ref['net_down'] = ui.label('--').classes('text-[12px] font-mono font-bold')

                    with ui.row().classes('w-full justify-between mt-3 pt-2 border-t border-[#1e3a5f]/35 items-center'):
                        srv_ref['uptime'] = ui.label('在线时长：--').classes('text-[10px] font-bold text-green-500 font-mono')
                        with ui.row().classes('items-center gap-2'):
                            srv_ref['load'] = ui.label(f"⚡ {status.get('load_1', '0.0')}").classes('text-[10px] text-gray-400 font-bold')
                            ui.label('ACTIVE' if is_online else 'DOWN').classes(f"text-[10px] font-black {'text-green-500' if is_online else 'text-red-400'}")
                mobile_refs[s['url']] = srv_ref

    def fmt_m_speed(b):
        if b < 1024:
            return f'{int(b)}B'
        return f'{int(b/1024)}K' if b < 1024**2 else f'{round(b/1024**2,1)}M'

    async def mobile_sync_loop():
        for url, refs in mobile_refs.items():
            status = state.PROBE_DATA_CACHE.get(url, {})
            if not status:
                continue
            refs['net_up'].set_text(f"{fmt_m_speed(status.get('net_speed_out', 0))}/s")
            refs['net_down'].set_text(f"{fmt_m_speed(status.get('net_speed_in', 0))}/s")
            cpu = status.get('cpu_usage', 0)
            mem_p = status.get('mem_usage', 0)
            refs['cpu_text'].set_text(f'{cpu}%')
            refs['cpu_bar'].style(f'width: {cpu}%')
            refs['mem_text'].set_text(f'{int(mem_p)}%')
            refs['mem_bar'].style(f'width: {mem_p}%')
            mem_t = status.get('mem_total', 0)
            mem_u = round(float(mem_t or 0) * (float(mem_p or 0) / 100), 2)
            refs['mem_detail'].set_text(f'{mem_u}G / {mem_t}G')
            raw_uptime = str(status.get('uptime', '-'))
            formatted_uptime = raw_uptime.replace('up ', '').replace(' days, ', '天 ').replace(' day, ', '天 ')
            if ':' in formatted_uptime:
                parts = formatted_uptime.split(' ')
                time_parts = parts[-1].split(':')
                formatted_uptime = f"{''.join(parts[:-1])}{time_parts[0]}时 {time_parts[1]}分"
            refs['uptime'].set_text(f'在线时长：{formatted_uptime}')
            refs['load'].set_text(f"⚡ {status.get('load_1', '0.0')}")

    async def update_mobile_tab(val):
        global CURRENT_PROBE_TAB
        CURRENT_PROBE_TAB = val
        state.CURRENT_PROBE_TAB = val
        await render_list(val)

    await render_list(CURRENT_PROBE_TAB)
    ui.timer(5.0, mobile_sync_loop)
