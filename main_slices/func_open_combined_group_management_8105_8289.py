def open_combined_group_management(group_name):
    # 1. 准备数据结构
    ui_rows = {}
    
    # 弹窗容器: bg-[#1e293b], border-slate-700
    with ui.dialog() as d, ui.card().classes('w-[95vw] max-w-[600px] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # --- 标题栏 ---
        with ui.row().classes('w-full justify-between items-center p-4 bg-[#0f172a] border-b border-slate-700 flex-shrink-0'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('settings', color='primary').classes('text-xl')
                ui.label(f'管理分组: {group_name}').classes('text-lg font-bold text-slate-200')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        # --- 内容区域 ---
        with ui.column().classes('w-full flex-grow overflow-hidden p-0'):
            
            # --- A. 顶部设置区 ---
            with ui.column().classes('w-full p-4 border-b border-slate-700 bg-[#1e293b] gap-3 flex-shrink-0'):
                # 分组名称修改
                ui.label('分组名称').classes('text-xs font-bold text-slate-500 mb-[-5px]')
                name_input = ui.input(value=group_name).props('outlined dense dark').classes('w-full')
                
                # 搜索框
                ui.label('搜索筛选').classes('text-xs font-bold text-slate-500 mb-[-5px]')
                search_input = ui.input(placeholder='🔍 搜名称 / IP...').props('outlined dense clearable dark').classes('w-full')
                
                def on_search(e):
                    keyword = str(e.value).lower().strip()
                    for url, item in ui_rows.items():
                        is_match = keyword in item['search_text']
                        item['row'].set_visibility(is_match)
                
                search_input.on_value_change(on_search)

            # --- B. 成员选择区域 ---
            with ui.column().classes('w-full flex-grow overflow-hidden relative bg-[#0f172a]'):
                # 工具栏
                with ui.row().classes('w-full p-2 bg-[#172033] justify-between items-center border-b border-slate-700 flex-shrink-0'):
                    ui.label('成员选择:').classes('text-xs font-bold text-slate-400 ml-2')
                    with ui.row().classes('gap-1'):
                        ui.button('全选 (当前)', on_click=lambda: toggle_visible(True)).props('flat dense size=xs color=blue')
                        ui.button('清空', on_click=lambda: toggle_visible(False)).props('flat dense size=xs color=grey')

                with ui.scroll_area().classes('w-full flex-grow p-2'):
                    with ui.column().classes('w-full gap-1'):
                        
                        selection_map = {} 
                        
                        try: sorted_servers = sorted(SERVERS_CACHE, key=lambda x: str(x.get('name', '')))
                        except: sorted_servers = SERVERS_CACHE 

                        if not sorted_servers:
                            ui.label('暂无服务器数据').classes('w-full text-center text-slate-500 mt-4')

                        for s in sorted_servers:
                            tags = s.get('tags', [])
                            if not isinstance(tags, list): tags = []
                            is_in_group = group_name in tags
                            if s.get('group') == group_name: is_in_group = True
                            
                            selection_map[s['url']] = is_in_group
                            
                            ip_addr = s['url'].split('//')[-1].split(':')[0]
                            search_key = f"{s['name']} {ip_addr}".lower()

                            # 列表项渲染 (深色适配)
                            # 选中: bg-blue-900/30, 未选中: bg-[#1e293b]
                            bg_cls = 'bg-blue-900/30 border-blue-500/50' if is_in_group else 'bg-[#1e293b] border-slate-700'
                            
                            with ui.row().classes(f'w-full items-center p-2 hover:bg-slate-700 rounded border transition cursor-pointer {bg_cls}') as row:
                                chk = ui.checkbox(value=is_in_group).props('dense dark color=green')
                                
                                # 点击行触发勾选，点击checkbox不穿透
                                def toggle_row(c=chk, r=row, u=s['url']):
                                    c.set_value(not c.value)
                                    # 手动更新样式以获得即时反馈
                                    if c.value: r.classes(add='bg-blue-900/30 border-blue-500/50', remove='bg-[#1e293b] border-slate-700')
                                    else: r.classes(remove='bg-blue-900/30 border-blue-500/50', add='bg-[#1e293b] border-slate-700')

                                row.on('click', toggle_row)
                                chk.on('click.stop', lambda: None)
                                
                                chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                                
                                # 信息展示
                                with ui.column().classes('gap-0 ml-2 flex-grow overflow-hidden'):
                                    with ui.row().classes('items-center gap-2'):
                                        ui.label(s['name']).classes('text-sm font-bold truncate text-slate-300')
                                        
                                try:
                                    real_region = detect_country_group(s['name'], None)
                                    ui.label(real_region).classes('text-xs font-mono text-slate-500')
                                except: pass
                            
                            ui_rows[s['url']] = {
                                'row': row,
                                'chk': chk,
                                'search_text': search_key
                            }

                def toggle_visible(state):
                    count = 0
                    for item in ui_rows.values():
                        if item['row'].visible:
                            item['chk'].value = state
                            # 触发样式更新
                            if state: item['row'].classes(add='bg-blue-900/30 border-blue-500/50', remove='bg-[#1e293b] border-slate-700')
                            else: item['row'].classes(remove='bg-blue-900/30 border-blue-500/50', add='bg-[#1e293b] border-slate-700')
                            count += 1
                    if state and count > 0:
                        safe_notify(f"已选中当前显示的 {count} 个服务器", "positive")

        # 3. 底部按钮栏
        with ui.row().classes('w-full p-4 border-t border-slate-700 bg-[#0f172a] justify-between items-center flex-shrink-0'):
            
            # === 删除分组 ===
            async def delete_group():
                with ui.dialog() as confirm_d, ui.card().classes('bg-[#1e293b] border border-slate-700'):
                    ui.label(f'确定永久删除分组 "{group_name}"?').classes('font-bold text-red-500')
                    ui.label('服务器将保留，仅移除此标签。').classes('text-xs text-slate-400')
                    with ui.row().classes('w-full justify-end mt-4 gap-2'):
                        ui.button('取消', on_click=confirm_d.close).props('flat dense color=grey')
                        async def do_del():
                            if 'custom_groups' in ADMIN_CONFIG and group_name in ADMIN_CONFIG['custom_groups']:
                                ADMIN_CONFIG['custom_groups'].remove(group_name)
                            
                            for s in SERVERS_CACHE:
                                if 'tags' in s and group_name in s['tags']: s['tags'].remove(group_name)
                                if s.get('group') == group_name:
                                    try: s['group'] = detect_country_group(s['name'], None)
                                    except: s['group'] = '默认分组'

                            await save_admin_config(); await save_servers()
                            confirm_d.close(); d.close()
                            
                            render_sidebar_content.refresh()
                            if CURRENT_VIEW_STATE.get('scope') == 'TAG' and CURRENT_VIEW_STATE.get('data') == group_name:
                                await refresh_content('ALL')
                            else: safe_notify(f'分组 "{group_name}" 已删除', 'positive')
                                
                        ui.button('确认删除', color='red', on_click=do_del).props('unelevated')
                confirm_d.open()

            ui.button('删除分组', icon='delete', color='red', on_click=delete_group).props('flat')

            # === 保存修改 ===
            async def save_changes():
                new_name = name_input.value.strip()
                if not new_name: return safe_notify('分组名称不能为空', 'warning')
                
                # 1. 更新配置
                if new_name != group_name:
                    if 'custom_groups' in ADMIN_CONFIG:
                        if group_name in ADMIN_CONFIG['custom_groups']:
                            idx = ADMIN_CONFIG['custom_groups'].index(group_name)
                            ADMIN_CONFIG['custom_groups'][idx] = new_name
                        else:
                            ADMIN_CONFIG['custom_groups'].append(new_name)
                    await save_admin_config()

                # 2. 更新 Tags
                for s in SERVERS_CACHE:
                    if 'tags' not in s: s['tags'] = []
                    should_have_tag = selection_map.get(s['url'], False)
                    
                    if should_have_tag:
                        if new_name not in s['tags']: s['tags'].append(new_name)
                        if new_name != group_name and group_name in s['tags']: s['tags'].remove(group_name)
                    else:
                        if new_name in s['tags']: s['tags'].remove(new_name)
                        if group_name in s['tags']: s['tags'].remove(group_name)

                await save_servers()
                d.close()
                render_sidebar_content.refresh()
                
                if CURRENT_VIEW_STATE.get('scope') == 'TAG' and CURRENT_VIEW_STATE.get('data') == group_name:
                    await refresh_content('TAG', new_name, force_refresh=True)
                
                safe_notify('分组设置已保存', 'positive')

            ui.button('保存修改', icon='save', on_click=save_changes).classes('bg-blue-600 text-white shadow-lg')

    d.open()