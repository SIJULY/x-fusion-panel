async def _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client):
    if content_container:
        content_container.clear()
        content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
        with content_container:
            targets = get_targets_by_scope(scope, data)
            if scope == 'SINGLE': 
                if targets: await render_single_server_view(targets[0]); return 
                else: ui.label('服务器未找到'); return 
            
            title = ""
            is_group_view = False
            show_ping = False
            if scope == 'ALL': title = f"🌍 所有服务器 ({len(targets)})"
            elif scope == 'TAG': title = f"🏷️ 自定义分组: {data} ({len(targets)})"; is_group_view = True
            elif scope == 'COUNTRY': title = f"🏳️ 区域: {data} ({len(targets)})"; is_group_view = True; show_ping = True 

            with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between'):
                with ui.row().classes('items-center gap-4'): ui.label(title).classes('text-2xl font-bold')
                with ui.row().classes('items-center gap-2'):
                    if is_group_view and targets:
                        with ui.row().classes('gap-1'):
                            ui.button(icon='content_copy', on_click=lambda: copy_group_link(data)).props('flat dense round size=sm color=grey')
                            ui.button(icon='bolt', on_click=lambda: copy_group_link(data, target='surge')).props('flat dense round size=sm text-color=orange')
                            ui.button(icon='cloud_queue', on_click=lambda: copy_group_link(data, target='clash')).props('flat dense round size=sm text-color=green')
                    if targets:
                            # 按钮点击 = 强制刷新 (绕过冷却)
                            ui.button('同步当前页', icon='sync', on_click=lambda: refresh_content(scope, data, force_refresh=True, sync_name_action=True, page_num=page_num, manual_client=client)).props('outline color=primary')

            if not targets:
                with ui.column().classes('w-full h-64 justify-center items-center text-gray-400'): ui.icon('inbox', size='4rem'); ui.label('列表为空')
            else: 
                try: targets.sort(key=smart_sort_key)
                except: pass
                await render_aggregated_view(targets, show_ping=show_ping, token=None, initial_page=page_num)