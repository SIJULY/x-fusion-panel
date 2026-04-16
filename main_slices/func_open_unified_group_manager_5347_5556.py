def open_unified_group_manager(mode='manage'):
    if 'probe_custom_groups' not in ADMIN_CONFIG: 
        ADMIN_CONFIG['probe_custom_groups'] = []
    
    state = {
        'current_group': None,
        'selected_urls': set(),
        'checkboxes': {},
        'page': 1,
        'search_text': ''
    }

    view_list_container = None
    server_list_container = None
    title_input = None
    pagination_ref = None 

    # 弹窗容器: bg-[#1e293b] border-slate-700
    with ui.dialog() as d, ui.card().classes('w-full max-w-5xl h-[90vh] flex flex-col p-0 gap-0 bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # --- 1. 顶部：视图切换区 (bg-[#0f172a]) ---
        with ui.row().classes('w-full p-3 bg-[#0f172a] border-b border-slate-700 items-center gap-2 overflow-x-auto flex-shrink-0'):
            ui.label('视图列表:').classes('font-bold text-slate-400 mr-2 text-xs')
            ui.button('➕ 新建分组', on_click=lambda: load_group_data(None)).props('unelevated color=green text-color=white size=sm')
            ui.separator().props('vertical dark').classes('mx-2 h-6')
            view_list_container = ui.row().classes('gap-2 items-center flex-nowrap')
            ui.space()
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        # --- 2. 编辑区头部 (bg-[#1e293b]) ---
        with ui.row().classes('w-full p-4 bg-[#1e293b] border-b border-slate-700 items-center gap-4 flex-shrink-0 wrap'):
            title_input = ui.input('视图名称', placeholder='请输入分组名称...').props('outlined dense dark').classes('min-w-[200px] flex-grow font-bold')
            
            ui.input(placeholder='🔍 搜索服务器...', on_change=lambda e: update_search(e.value)).props('outlined dense dense dark').classes('w-48')

            with ui.row().classes('gap-2'):
                ui.button('全选本页', on_click=lambda: toggle_page_all(True)).props('flat dense size=sm color=blue')
                ui.button('清空本页', on_click=lambda: toggle_page_all(False)).props('flat dense size=sm color=grey')

        # --- 3. 服务器列表 (bg-[#0f172a]) ---
        with ui.scroll_area().classes('w-full flex-grow p-4 bg-[#0f172a]'):
            server_list_container = ui.column().classes('w-full gap-2')
            
        # --- 3.5 分页 (bg-[#1e293b]) ---
        with ui.row().classes('w-full p-2 justify-center bg-[#1e293b] border-t border-slate-700'):
            pagination_ref = ui.row() 

        # --- 4. 底部保存 (bg-[#0f172a]) ---
        with ui.row().classes('w-full p-4 bg-[#0f172a] border-t border-slate-700 justify-between items-center flex-shrink-0'):
            ui.button('删除此视图', icon='delete', color='red', on_click=lambda: delete_current_group()).props('flat')
            ui.button('保存当前配置', icon='save', on_click=lambda: save_current_group()).classes('bg-blue-600 text-white shadow-lg')

    # ================= 逻辑定义 =================

    def update_search(val):
        state['search_text'] = str(val).lower().strip()
        state['page'] = 1 
        render_servers()

    def render_views():
        view_list_container.clear()
        groups = ADMIN_CONFIG.get('probe_custom_groups', [])
        with view_list_container:
            for g in groups:
                is_active = (g == state['current_group'])
                # 按钮样式深色适配
                btn_props = 'unelevated color=blue' if is_active else 'outline color=grey-5 text-color=grey-4'
                ui.button(g, on_click=lambda _, name=g: load_group_data(name)).props(f'{btn_props} size=sm')

    def load_group_data(group_name):
        state['current_group'] = group_name
        state['page'] = 1
        state['selected_urls'] = set() 
        
        if group_name:
            for s in SERVERS_CACHE:
                if (group_name in s.get('tags', [])) or (s.get('group') == group_name):
                    state['selected_urls'].add(s['url'])
                    
        render_views()
        title_input.value = group_name if group_name else ''
        if not group_name: title_input.run_method('focus')
        render_servers()

    def render_servers():
        server_list_container.clear()
        pagination_ref.clear()
        state['checkboxes'] = {} 
        
        if not SERVERS_CACHE:
            with server_list_container: ui.label('暂无服务器').classes('text-center text-slate-500 mt-10 w-full')
            return

        all_srv = SERVERS_CACHE
        if state['search_text']:
            all_srv = [s for s in all_srv if state['search_text'] in s.get('name', '').lower() or state['search_text'] in s.get('url', '').lower()]
        
        try: sorted_servers = sorted(all_srv, key=lambda x: str(x.get('name', '')))
        except: sorted_servers = all_srv

        PAGE_SIZE = 48 
        total_items = len(sorted_servers)
        total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
        if state['page'] > total_pages: state['page'] = 1
        if state['page'] < 1: state['page'] = 1
        
        start_idx = (state['page'] - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_items = sorted_servers[start_idx:end_idx]

        with server_list_container:
            ui.label(f"共 {total_items} 台 (第 {state['page']}/{total_pages} 页)").classes('text-xs text-slate-400 mb-2')

            with ui.grid().classes('w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2'):
                for s in current_page_items:
                    url = s.get('url')
                    if not url: continue
                    is_checked = url in state['selected_urls']
                    
                    # 列表项背景：选中为深蓝，未选中为深灰
                    bg_cls = 'bg-blue-900/30 border-blue-500/50' if is_checked else 'bg-[#172033] border-slate-700'
                    
                    with ui.row().classes(f'items-center p-2 border rounded cursor-pointer hover:bg-slate-700 transition {bg_cls}') as row:
                        chk = ui.checkbox(value=is_checked).props('dense dark color=green')
                        state['checkboxes'][url] = chk
                        
                        def toggle_row(c=chk, r=row, u=url): 
                            c.value = not c.value
                            update_selection(u, c.value)
                            if c.value: r.classes(add='bg-blue-900/30 border-blue-500/50', remove='bg-[#172033] border-slate-700')
                            else: r.classes(remove='bg-blue-900/30 border-blue-500/50', add='bg-[#172033] border-slate-700')

                        row.on('click', toggle_row)
                        chk.on('click.stop', lambda _, c=chk, r=row, u=url: [update_selection(u, c.value), 
                            r.classes(add='bg-blue-900/30 border-blue-500/50', remove='bg-[#172033] border-slate-700') if c.value else r.classes(remove='bg-blue-900/30 border-blue-500/50', add='bg-[#172033] border-slate-700')])

                        with ui.column().classes('gap-0 ml-2 overflow-hidden'):
                            ui.label(s.get('name', 'Unknown')).classes('text-sm font-bold truncate text-slate-200')
                            if is_checked: ui.label('已选中').classes('text-[10px] text-green-400 font-bold')
                            else: ui.label(s.get('group','')).classes('text-[10px] text-slate-500')

        if total_pages > 1:
            with pagination_ref:
                p = ui.pagination(1, total_pages, direction_links=True).props('dense color=blue text-color=slate-400 active-text-color=white')
                p.value = state['page']
                p.on('update:model-value', lambda e: [state.update({'page': e.args}), render_servers()])

    def update_selection(url, checked):
        if checked: state['selected_urls'].add(url)
        else: state['selected_urls'].discard(url)

    def toggle_page_all(val):
        for url in state['checkboxes'].keys():
            if val: state['selected_urls'].add(url)
            else: state['selected_urls'].discard(url)
        render_servers() 

    async def save_current_group():
        old_name = state['current_group']
        new_name = title_input.value.strip()
        if not new_name: return safe_notify("名称不能为空", "warning")

        groups = ADMIN_CONFIG.get('probe_custom_groups', [])
        
        if not old_name: 
            if new_name in groups: return safe_notify("名称已存在", "negative")
            groups.append(new_name)
        elif new_name != old_name:
            if new_name in groups: return safe_notify("名称已存在", "negative")
            idx = groups.index(old_name)
            groups[idx] = new_name
            for s in SERVERS_CACHE:
                if 'tags' in s and old_name in s['tags']:
                    s['tags'].remove(old_name)
                    s['tags'].append(new_name)

        for s in SERVERS_CACHE:
            if 'tags' not in s: s['tags'] = []
            if s['url'] in state['selected_urls']:
                if new_name not in s['tags']: s['tags'].append(new_name)
            else:
                if new_name in s['tags']: s['tags'].remove(new_name)

        ADMIN_CONFIG['probe_custom_groups'] = groups
        await save_admin_config(); await save_servers()
        safe_notify(f"✅ 保存成功", "positive")
        load_group_data(new_name)
        try: await render_probe_page()
        except: pass

    async def delete_current_group():
        target = state['current_group']
        if not target: return
        if target in ADMIN_CONFIG.get('probe_custom_groups', []):
            ADMIN_CONFIG['probe_custom_groups'].remove(target)
            await save_admin_config()
        for s in SERVERS_CACHE:
            if 'tags' in s and target in s['tags']: s['tags'].remove(target)
        await save_servers()
        safe_notify("🗑️ 已删除", "positive")
        load_group_data(None)
        try: await render_probe_page()
        except: pass

    def init():
        render_views()
        load_group_data(None)
    
    ui.timer(0.1, init, once=True)
    d.open()