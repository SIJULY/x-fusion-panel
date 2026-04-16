def open_quick_group_create_dialog(callback=None):
    selection_map = {s['url']: False for s in SERVERS_CACHE}
    ui_rows = {} 

    # 弹窗背景: bg-[#1e293b], 边框: border-slate-700
    with ui.dialog() as d, ui.card().classes('w-full max-w-lg h-[85vh] flex flex-col p-0 bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # 1. 顶部区域
        with ui.column().classes('w-full p-4 border-b border-slate-700 bg-[#0f172a] gap-3 flex-shrink-0'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('新建分组 (标签模式)').classes('text-lg font-bold text-slate-200')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
            
            # 输入框
            name_input = ui.input('分组名称', placeholder='例如: 甲骨文云').props('outlined dense dark').classes('w-full')
            search_input = ui.input(placeholder='🔍 搜索筛选服务器...').props('outlined dense clearable dark').classes('w-full')
            
            def on_search(e):
                keyword = str(e.value).lower().strip()
                for url, item in ui_rows.items():
                    is_match = keyword in item['search_text']
                    item['row'].set_visibility(is_match)
            search_input.on_value_change(on_search)

        # 2. 中间：列表
        with ui.column().classes('w-full flex-grow overflow-hidden relative bg-[#1e293b]'):
            # 工具栏
            with ui.row().classes('w-full p-2 bg-[#172033] justify-between items-center border-b border-slate-700 flex-shrink-0'):
                ui.label('勾选加入该组:').classes('text-xs font-bold text-slate-400 ml-2')
                with ui.row().classes('gap-1'):
                    ui.button('全选', on_click=lambda: toggle_visible(True)).props('flat dense size=xs color=blue')
                    ui.button('清空', on_click=lambda: toggle_visible(False)).props('flat dense size=xs color=grey')

            with ui.scroll_area().classes('w-full flex-grow p-2'):
                with ui.column().classes('w-full gap-1'):
                    try: sorted_srv = sorted(SERVERS_CACHE, key=lambda x: str(x.get('name', '')))
                    except: sorted_srv = SERVERS_CACHE
                    
                    for s in sorted_srv:
                        search_key = f"{s['name']} {s['url']}".lower()
                        
                        # 列表项：悬浮变亮
                        with ui.row().classes('w-full items-center p-2 hover:bg-slate-700 rounded border border-transparent transition cursor-pointer group') as row:
                            chk = ui.checkbox(value=False).props('dense dark color=blue')
                            chk.on('click.stop', lambda: None)
                            chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                            row.on('click', lambda _, c=chk: c.set_value(not c.value))

                            ui.label(s['name']).classes('text-sm font-bold text-slate-300 ml-2 truncate flex-grow select-none group-hover:text-white')
                            
                            detected = "未知"
                            try: detected = detect_country_group(s['name'], s)
                            except: pass
                            ui.label(detected).classes('text-xs text-slate-500 font-mono')
                        
                        ui_rows[s['url']] = {'row': row, 'chk': chk, 'search_text': search_key}

            def toggle_visible(state):
                count = 0
                for item in ui_rows.values():
                    if item['row'].visible:
                        item['chk'].value = state; count += 1
                if state and count > 0: safe_notify(f"已选中 {count} 个", "positive")

        # 3. 底部
        async def save():
            new_name = name_input.value.strip()
            if not new_name: return safe_notify('名称不能为空', 'warning')
            existing = set(ADMIN_CONFIG.get('custom_groups', []))
            if new_name in existing: return safe_notify('分组已存在', 'warning')
            if 'custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['custom_groups'] = []
            ADMIN_CONFIG['custom_groups'].append(new_name)
            await save_admin_config()
            
            count = 0
            for s in SERVERS_CACHE:
                if selection_map.get(s['url'], False):
                    if 'tags' not in s: s['tags'] = []
                    if new_name not in s['tags']: s['tags'].append(new_name); count += 1
                    if s.get('group') == new_name: s['group'] = detect_country_group(s['name'], None)

            if count > 0: await save_servers()
            render_sidebar_content.refresh()
            safe_notify(f'✅ 分组 "{new_name}" 创建成功', 'positive')
            d.close()
            if callback: await callback(new_name)

        with ui.row().classes('w-full p-4 border-t border-slate-700 bg-[#0f172a] justify-end gap-2 flex-shrink-0'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('创建并保存', on_click=save).classes('bg-blue-600 text-white shadow-md')
    d.open()