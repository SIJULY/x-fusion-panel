def open_group_sort_dialog():
    # 读取当前分组
    current_groups = ADMIN_CONFIG.get('probe_custom_groups', [])
    if not current_groups:
        safe_notify("暂无自定义视图", "warning")
        return

    # 临时列表用于编辑
    temp_list = list(current_groups)

    # 弹窗容器：深色背景 + 边框
    with ui.dialog() as d, ui.card().classes('w-[400px] max-w-[95vw] h-[60vh] flex flex-col p-0 gap-0 bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # --- 顶部标题 (bg-[#0f172a]) ---
        with ui.row().classes('w-full p-4 border-b border-slate-700 justify-between items-center bg-[#0f172a] flex-shrink-0'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('sort', color='blue').classes('text-lg')
                ui.label('视图排序').classes('font-bold text-slate-200 text-base')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
        
        # --- 列表容器 (bg-[#1e293b]) ---
        with ui.scroll_area().classes('w-full flex-grow p-3'):
            list_container = ui.column().classes('w-full gap-2')

        def render_list():
            list_container.clear()
            with list_container:
                for i, name in enumerate(temp_list):
                    # 列表项：深色卡片风格
                    with ui.row().classes('w-full p-3 items-center gap-3 border border-slate-700 rounded-lg bg-[#0f172a] shadow-sm transition-all hover:border-blue-500'):
                        # 序号
                        ui.label(str(i+1)).classes('text-xs text-slate-500 font-mono w-4 text-center font-bold')
                        
                        # 组名
                        ui.label(name).classes('font-bold text-slate-300 flex-grow text-sm truncate')
                        
                        # 移动按钮
                        with ui.row().classes('gap-1'):
                            # 上移
                            if i > 0:
                                ui.button(icon='arrow_upward', on_click=lambda _, idx=i: move_item(idx, -1)) \
                                    .props('flat dense round size=xs color=blue-4').classes('hover:bg-slate-700')
                            else:
                                ui.element('div').classes('w-6') # 占位
                            
                            # 下移
                            if i < len(temp_list) - 1:
                                ui.button(icon='arrow_downward', on_click=lambda _, idx=i: move_item(idx, 1)) \
                                    .props('flat dense round size=xs color=blue-4').classes('hover:bg-slate-700')
                            else:
                                ui.element('div').classes('w-6')

        def move_item(index, direction):
            target = index + direction
            if 0 <= target < len(temp_list):
                temp_list[index], temp_list[target] = temp_list[target], temp_list[index]
                render_list()

        render_list()

        # --- 底部保存 (bg-[#0f172a]) ---
        async def save():
            ADMIN_CONFIG['probe_custom_groups'] = temp_list
            await save_admin_config()
            safe_notify("✅ 分组顺序已更新", "positive")
            d.close()
            # 尝试刷新探针设置页面的视图列表
            try: await render_probe_page()
            except: pass

        with ui.row().classes('w-full p-4 border-t border-slate-700 bg-[#0f172a] flex-shrink-0'):
            ui.button('保存顺序', icon='save', on_click=save).classes('w-full bg-blue-600 text-white shadow-lg hover:bg-blue-500')
    
    d.open()