async def render_aggregated_view(server_list, show_ping=False, token=None, initial_page=1):
    parent_client = ui.context.client
    list_container = ui.column().classes('w-full gap-3 p-1')
    
    cols_ping = 'grid-template-columns: 2fr 2fr 1.5fr 1.5fr 1fr 1fr 1.5fr' 
    cols_no_ping = 'grid-template-columns: 2fr 2fr 1.5fr 1.5fr 1fr 1fr 0.5fr 1.5fr'
    
    try:
        is_all_servers = (len(server_list) == len(SERVERS_CACHE) and not show_ping)
        use_special_mode = is_all_servers or show_ping
        current_css = COLS_SPECIAL_WITH_PING if use_special_mode else COLS_NO_PING
    except:
        current_css = cols_ping if show_ping else cols_no_ping

    PAGE_SIZE = 30
    total_items = len(server_list)
    total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
    if initial_page > total_pages: initial_page = 1
    if initial_page < 1: initial_page = 1

    def render_page(page_num):
        list_container.clear()
        if 'CURRENT_VIEW_STATE' in globals(): CURRENT_VIEW_STATE['page'] = page_num

        with list_container:
            # 顶部翻页
            with ui.row().classes('w-full justify-between items-center px-2 mb-2'):
                ui.label(f'共 {total_items} 台服务器 (第 {page_num}/{total_pages} 页)').classes('text-xs text-slate-400 font-bold')
                if total_pages > 1:
                    ui.pagination(1, total_pages, direction_links=True, value=page_num) \
                        .props('dense flat color=blue text-color=slate-400 active-text-color=white') \
                        .on_value_change(lambda e: handle_pagination_click(e.value))

            # ✨ 修改点：表头背景 bg-[#1e293b], 边框 border-slate-700
            with ui.element('div').classes('grid w-full gap-4 font-bold text-slate-500 border-b border-slate-700 pb-2 px-6 mb-1 uppercase tracking-wider text-xs bg-[#1e293b] rounded-t-lg pt-3').style(current_css):
                ui.label('服务器').classes('text-left pl-1')
                ui.label('节点名称').classes('text-left pl-1')
                if use_special_mode: ui.label('在线状态 / IP').classes('text-center')
                else: ui.label('所在组').classes('text-center')
                ui.label('已用流量').classes('text-center')
                ui.label('协议').classes('text-center')
                ui.label('端口').classes('text-center')
                if not use_special_mode: ui.label('状态').classes('text-center')
                ui.label('操作').classes('text-center')
            
            # 数据渲染
            start_idx = (page_num - 1) * PAGE_SIZE
            end_idx = start_idx + PAGE_SIZE
            current_page_data = server_list[start_idx:end_idx]

            for srv in current_page_data:
                panel_n = NODES_DATA.get(srv['url'], []) or []
                custom_n = srv.get('custom_nodes', []) or []
                for cn in custom_n: cn['_is_custom'] = True
                all_nodes = panel_n + custom_n
                
                if not all_nodes:
                    draw_row(srv, None, current_css, use_special_mode, is_first=True)
                    continue

                for index, node in enumerate(all_nodes):
                    draw_row(srv, node, current_css, use_special_mode, is_first=(index==0))
            
            # 底部翻页
            if total_pages > 1:
                with ui.row().classes('w-full justify-center mt-4'):
                    ui.pagination(1, total_pages, direction_links=True, value=page_num) \
                        .props('dense flat color=blue text-color=slate-400 active-text-color=white') \
                        .on_value_change(lambda e: handle_pagination_click(e.value))



    # ================= 🚀 核心逻辑：翻页事件处理 =================
    def handle_pagination_click(new_page):
        try: target_page = int(new_page)
        except: return 

        current_scope = CURRENT_VIEW_STATE.get('scope', 'ALL')
        current_data = CURRENT_VIEW_STATE.get('data', None)

        print(f"👉 [Debug] 翻页至: {target_page} (自然浏览)", flush=True)

        # 使用父级上下文包裹异步任务，防止 Context Lost
        with parent_client:
            asyncio.create_task(
                refresh_content(
                    scope=current_scope,
                    data=current_data,
                    # 🛑 [关键修改]：设置为 False
                    # 这告诉 refresh_content：“我是自然翻页，请先检查是否有缓存且未过期”
                    force_refresh=False, 
                    sync_name_action=True,
                    page_num=target_page,
                    manual_client=parent_client
                )
            )

    # 初次渲染
    render_page(initial_page)