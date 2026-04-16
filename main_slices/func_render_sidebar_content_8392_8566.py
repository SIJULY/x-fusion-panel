def render_sidebar_content():
    global _current_dragged_group
    
    # 清空索引
    SIDEBAR_UI_REFS['groups'].clear()
    SIDEBAR_UI_REFS['rows'].clear()

    # --- 1. 顶部固定区域 ---
    # 样式：深蓝灰背景，悬浮变亮
    btn_top_style = 'w-full bg-[#0f172a] border border-slate-700 rounded-lg shadow-sm text-slate-400 font-bold px-3 py-2 transition-all hover:bg-[#334155] hover:text-white hover:border-blue-500 active:translate-y-0 active:scale-[0.98]'
    
    # 顶部容器背景：Slate-800 (#1e293b)
    with ui.column().classes('w-full p-4 border-b border-slate-700 bg-[#1e293b] flex-shrink-0 relative overflow-hidden'):
        ui.label('X-Fusion').classes('absolute top-2 right-6 text-[3rem] font-black text-slate-800 opacity-20 pointer-events-none -rotate-12 select-none z-0 tracking-tighter leading-tight')
        
        # ✨✨✨ 修改点：原本这里只是一个控制中心文字，现在改为了文字+IP显示 ✨✨✨
        sidebar_ip = app.storage.user.get('last_known_ip', 'Unknown')
        with ui.row().classes('w-full items-center justify-between mb-4 z-10 relative'):
            ui.label('控制中心').classes('text-sm font-black text-blue-500 tracking-widest uppercase')
            
            # 右侧：登陆IP显示 (深色背景，蓝色字体，绿色盾牌)
            with ui.row().classes('items-center gap-1 bg-[#0f172a] px-2 py-0.5 rounded border border-slate-700 shadow-sm'):
                ui.label('登陆IP:').classes('text-[11px] font-bold text-blue-500')
                ui.icon('security', color='green-500').classes('text-xs')
                ui.label(sidebar_ip).classes('text-[11px] font-mono font-bold text-blue-500')
        # ✨✨✨ 修改点结束 ✨✨✨

        with ui.column().classes('w-full gap-2 z-10 relative'):
            ui.button('仪表盘', icon='dashboard', on_click=lambda: asyncio.create_task(load_dashboard_stats())).props('flat align=left').classes(btn_top_style)
            ui.button('探针设置', icon='tune', on_click=render_probe_page).props('flat align=left').classes(btn_top_style)
            ui.button('订阅管理', icon='rss_feed', on_click=load_subs_view).props('flat align=left').classes(btn_top_style)
            
    # --- 2. 列表区域 ---
    # 滚动区背景：Slate-800 (#1e293b)
    with ui.column().props('id=sidebar-scroll-box').classes('w-full flex-grow overflow-y-auto p-2 gap-2 bg-[#1e293b]'):
        # 功能按钮
        with ui.row().classes('w-full gap-2 px-1 mb-2'):
            func_btn_base = 'flex-grow text-xs font-bold text-white rounded-lg border-b-4 border-black/20 active:border-b-0 active:translate-y-[4px] transition-all'
            ui.button('新建分组', icon='create_new_folder', on_click=open_quick_group_create_dialog).props('dense unelevated').classes(f'bg-blue-600 hover:bg-blue-500 {func_btn_base}')
            ui.button('添加服务器', icon='add', color='green', on_click=lambda: open_server_dialog(None)).props('dense unelevated').classes(f'bg-emerald-600 hover:bg-emerald-500 {func_btn_base}')
                
        # --- A. 全部服务器 ---
        # 列表卡片：背景更深 (#0f172a) 以突出内容
        list_item_3d = 'w-full items-center justify-between p-3 border border-slate-700 rounded-xl mb-1 bg-[#0f172a] shadow-md cursor-pointer group transition-all duration-200 hover:border-blue-500 hover:bg-[#172033] active:scale-[0.98]'
        with ui.row().classes(list_item_3d).on('click', lambda _: refresh_content('ALL')):
            with ui.row().classes('items-center gap-3'):
                with ui.column().classes('p-1.5 bg-slate-800 rounded-lg group-hover:bg-blue-900 transition-colors'):
                    ui.icon('dns', color='blue-4').classes('text-sm')
                ui.label('所有服务器').classes('font-bold text-slate-300 group-hover:text-white')
            ui.badge(str(len(SERVERS_CACHE)), color='blue-9').props('rounded outline').classes('text-blue-200')

        def on_drag_start(e, name): global _current_dragged_group; _current_dragged_group = name

        # --- B. 自定义分组  ---
        final_tags = ADMIN_CONFIG.get('custom_groups', [])
        async def on_tag_drop(e, target_name):
            global _current_dragged_group
            if not _current_dragged_group or _current_dragged_group == target_name: return
            try:
                current_list = list(final_tags)
                if _current_dragged_group in current_list and target_name in current_list:
                    old_idx = current_list.index(_current_dragged_group); item = current_list.pop(old_idx)
                    new_idx = current_list.index(target_name); current_list.insert(new_idx, item)
                    ADMIN_CONFIG['custom_groups'] = current_list; await save_admin_config()
                    _current_dragged_group = None; render_sidebar_content.refresh()
            except: pass

        if final_tags:
            ui.label('自定义分组').classes('text-xs font-bold text-slate-500 mt-4 mb-2 px-2 uppercase tracking-wider')
            for tag_group in final_tags:
                tag_servers = [s for s in SERVERS_CACHE if isinstance(s, dict) and (tag_group in s.get('tags', []) or s.get('group') == tag_group)]
                try: tag_servers.sort(key=smart_sort_key)
                except: tag_servers.sort(key=lambda x: x.get('name', ''))
                is_open = tag_group in EXPANDED_GROUPS
                
                with ui.element('div').classes('w-full').on('dragover.prevent', lambda _: None).on('drop', lambda e, n=tag_group: on_tag_drop(e, n)):
                    # 分组面板
                    with ui.expansion('', icon=None, value=is_open).classes('w-full border border-slate-700 rounded-xl mb-2 bg-[#0f172a] shadow-sm transition-all').props('expand-icon-toggle header-class="bg-[#0f172a] hover:bg-[#172033]"').on_value_change(lambda e, g=tag_group: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                        
                        # Header 布局
                        with exp.add_slot('header'):
                            with ui.row().classes('w-full h-full items-center justify-between no-wrap py-2 cursor-pointer group/header transition-all').on('click', lambda _, g=tag_group: refresh_content('TAG', g)):
                                
                                # 左侧区域
                                with ui.row().classes('items-center gap-3 flex-grow overflow-hidden no-wrap'):
                                    # 拖拽手柄
                                    ui.icon('drag_indicator').props('draggable="true"').classes('cursor-move text-slate-600 hover:text-slate-400 p-1 rounded transition-colors group-hover/header:text-slate-400').on('dragstart', lambda e, n=tag_group: on_drag_start(e, n)).on('click.stop').tooltip('按住拖拽')
                                    
                                    # 组名
                                    with ui.row().classes('items-center gap-2 flex-grow overflow-hidden no-wrap'):
                                        ui.label(tag_group).classes('font-bold text-slate-300 truncate group-hover/header:text-white text-sm')

                                # 右侧区域：设置按钮 + 数量
                                with ui.row().classes('items-center gap-2 pr-2 flex-shrink-0').on('mousedown.stop').on('click.stop'):
                                    ui.button(icon='settings', on_click=lambda _, g=tag_group: open_combined_group_management(g)).props('flat dense round size=xs color=grey-6').classes('hover:text-white').tooltip('管理分组')
                                    
                                    ui.badge(str(len(tag_servers)), color='green-9').props('rounded outline text-color=green-4')
                        
                        # 注册分组容器
                        with ui.column().classes('w-full gap-2 p-2 bg-[#172033] border-t border-slate-800') as col:
                            SIDEBAR_UI_REFS['groups'][tag_group] = col
                            for s in tag_servers:
                                render_single_sidebar_row(s)

        # --- C. 区域分组 ---
        ui.label('区域分组').classes('text-xs font-bold text-slate-500 mt-4 mb-2 px-2 uppercase tracking-wider')
        country_buckets = {}
        for s in SERVERS_CACHE:
            c_group = detect_country_group(s.get('name', ''), s)
            if c_group in ['默认分组', '自动注册', '自动导入', '未分组', '', None]: c_group = '🏳️ 其他地区'
            if c_group not in country_buckets: country_buckets[c_group] = []
            country_buckets[c_group].append(s)
        
        saved_order = ADMIN_CONFIG.get('group_order', [])
        def region_sort_key(name): return saved_order.index(name) if name in saved_order else 9999
        sorted_regions = sorted(country_buckets.keys(), key=region_sort_key)

        async def on_region_drop(e, target_name):
            global _current_dragged_group
            if not _current_dragged_group or _current_dragged_group == target_name: return
            try:
                current_list = list(sorted_regions)
                if _current_dragged_group in current_list and target_name in current_list:
                    old_idx = current_list.index(_current_dragged_group); item = current_list.pop(old_idx)
                    new_idx = current_list.index(target_name); current_list.insert(new_idx, item)
                    ADMIN_CONFIG['group_order'] = current_list; await save_admin_config()
                    _current_dragged_group = None; render_sidebar_content.refresh()
            except: pass

        with ui.column().classes('w-full gap-2 pb-4'):
            for c_name in sorted_regions:
                c_servers = country_buckets[c_name]
                try: c_servers.sort(key=smart_sort_key)
                except: c_servers.sort(key=lambda x: x.get('name', ''))
                is_open = c_name in EXPANDED_GROUPS

                with ui.element('div').classes('w-full').on('dragover.prevent', lambda _: None).on('drop', lambda e, n=c_name: on_region_drop(e, n)):
                    with ui.expansion('', icon=None, value=is_open).classes('w-full border border-slate-700 rounded-xl bg-[#0f172a] shadow-sm').props('expand-icon-toggle header-class="bg-[#0f172a] hover:bg-[#172033]"').on_value_change(lambda e, g=c_name: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                        with exp.add_slot('header'):
                            with ui.row().classes('w-full h-full items-center justify-between no-wrap py-2 cursor-pointer group/header transition-all').on('click', lambda _, g=c_name: refresh_content('COUNTRY', g)):
                                with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                                    ui.icon('drag_indicator').props('draggable="true"').classes('cursor-move text-slate-600 hover:text-slate-400 p-1').on('dragstart', lambda e, n=c_name: on_drag_start(e, n)).on('click.stop').tooltip('按住拖拽')
                                    with ui.row().classes('items-center gap-2 flex-grow'):
                                        flag = c_name.split(' ')[0] if ' ' in c_name else '🏳️'
                                        ui.label(flag).classes('text-lg filter drop-shadow-md')
                                        display_name = c_name.split(' ')[1] if ' ' in c_name else c_name
                                        ui.label(display_name).classes('font-bold text-slate-300 truncate group-hover/header:text-white')
                                with ui.row().classes('items-center gap-2 pr-2').on('mousedown.stop').on('click.stop'):
                                    ui.button(icon='edit_note', on_click=lambda _, s=c_servers, t=c_name: open_bulk_edit_dialog(s, f"区域: {t}")).props('flat dense round size=xs color=grey-6').classes('hover:text-white').tooltip('批量管理')
                                    ui.badge(str(len(c_servers)), color='green-9').props('rounded outline text-color=green-4')

                        # 注册区域容器
                        with ui.column().classes('w-full gap-2 p-2 bg-[#172033] border-t border-slate-800') as col:
                            SIDEBAR_UI_REFS['groups'][c_name] = col
                            for s in c_servers:
                                render_single_sidebar_row(s) # 使用提取的函数

    # JS 滚动记忆
    ui.run_javascript('''
        (function() {
            var el = document.getElementById("sidebar-scroll-box");
            if (el) {
                if (window.sidebarScroll) el.scrollTop = window.sidebarScroll;
                el.addEventListener("scroll", function() { window.sidebarScroll = el.scrollTop; });
            }
        })();
    ''')
    
    # 3. 底部 (深色化)
    with ui.column().classes('w-full p-2 border-t border-slate-700 mt-auto mb-4 gap-2 bg-[#1e293b] z-10'):
        bottom_btn_3d = 'w-full text-slate-400 text-xs font-bold bg-[#0f172a] border border-slate-700 rounded-lg px-3 py-2 transition-all hover:bg-[#334155] hover:text-white hover:border-blue-500 active:translate-y-[1px]'
        ui.button('批量 SSH 执行', icon='playlist_play', on_click=batch_ssh_manager.open_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('Cloudflare 设置', icon='cloud', on_click=open_cloudflare_settings_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('全局 SSH 设置', icon='vpn_key', on_click=open_global_settings_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('数据备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes(bottom_btn_3d)