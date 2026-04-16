def render_single_sidebar_row(s):
    # 样式定义
    # 按钮基底：背景 Slate-800 (#1e293b)，边框 Slate-600
    btn_keycap_base = 'bg-[#1e293b] border-t border-x border-slate-600 border-b-[3px] border-b-slate-800 rounded-lg transition-all active:border-b-0 active:border-t-[3px] active:translate-y-[3px]'
    
    # 名字按钮：普通文字 Slate-400，悬浮 Slate-300 / White
    btn_name_cls = f'{btn_keycap_base} flex-grow text-xs font-bold text-slate-400 truncate px-3 py-2.5 hover:bg-[#334155] hover:text-white hover:border-slate-500'
    
    # 设置按钮：
    btn_settings_cls = f'{btn_keycap_base} w-10 py-2.5 px-0 flex items-center justify-center text-slate-500 hover:text-white hover:bg-[#334155] hover:border-slate-500'

    # 创建行容器
    with ui.row().classes('w-full gap-2 no-wrap items-stretch') as row:
        # 1. 服务器名字按钮
        ui.button(on_click=lambda _, s=s: on_server_click_handler(s)) \
            .bind_text_from(s, 'name') \
            .props('no-caps align=left flat text-color=grey-4') \
            .classes(btn_name_cls)
        
        # 2. 设置按钮
        ui.button(icon='settings', on_click=lambda _, s=s: open_server_dialog(SERVERS_CACHE.index(s))) \
            .props('flat square size=sm text-color=grey-5') \
            .classes(btn_settings_cls).tooltip('配置 / 删除')
    
    # 注册到全局索引
    SIDEBAR_UI_REFS['rows'][s['url']] = row
    return row