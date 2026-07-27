import asyncio

from nicegui import app, ui

from app.core.state import ADMIN_CONFIG, CURRENT_VIEW_STATE, NODES_DATA, SERVERS_CACHE, SUBS_CACHE, INDEPENDENT_NODES_CACHE
from app.storage.repositories import save_admin_config, save_subs, save_independent_nodes
from app.ui.common.notifications import safe_copy_to_clipboard, safe_notify, show_loading


async def load_subs_view():
    global CURRENT_VIEW_STATE
    from app.ui.dialogs.sub_dialogs import open_advanced_sub_editor

    CURRENT_VIEW_STATE['scope'] = 'SUBS'
    CURRENT_VIEW_STATE['data'] = None
    CURRENT_VIEW_STATE['page'] = 1
    app.storage.user['last_view_scope'] = 'SUBS'
    app.storage.user['last_view_data'] = None
    app.storage.user['last_view_page'] = 1

    from app.ui.pages.content_router import content_container

    show_loading(content_container)

    origin = ""

    db_url = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
    if db_url and not ('127.0.0.1' in db_url or 'localhost' in db_url):
        origin = db_url

    if not origin:
        try:
            origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
        except:
            pass

    if not origin or origin == 'null':
        try:
            req = ui.context.client.request
            real_host = req.headers.get('X-Forwarded-Host') or req.headers.get('host')
            real_proto = req.headers.get('X-Forwarded-Proto') or req.url.scheme
            if real_host:
                origin = f"{real_proto}://{real_host}"
        except:
            pass

    if not origin:
        origin = "http://x-fusion-panel"

    if origin and "x-fusion-panel" not in origin:
        if ADMIN_CONFIG.get('manager_base_url') != origin:
            ADMIN_CONFIG['manager_base_url'] = origin
            asyncio.create_task(save_admin_config())

    is_dark = bool(app.storage.user.get('is_dark', True))

    content_container.clear()
    content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='h-full overflow-y-auto p-4 pl-6 justify-start')
    content_container.style('background-color: var(--xf-bg-main);')

    all_active_keys = set()
    for srv in SERVERS_CACHE:
        panel = NODES_DATA.get(srv['url'], []) or []
        custom = srv.get('custom_nodes', []) or []
        for n in (panel + custom):
            key = f"{srv['url']}|{n['id']}"
            all_active_keys.add(key)

    with content_container:
        page_header_cls = 'w-full mb-5 justify-between items-center border-b pb-3'
        page_header_style = 'border-color: var(--xf-card-border);'
        page_icon_cls = 'w-10 h-10 rounded-sm flex items-center justify-center border relative overflow-hidden'
        page_icon_style = 'background: var(--xf-code-bg); border-color: var(--xf-card-border); color: var(--xf-accent); box-shadow: 0 4px 12px rgba(15,23,42,0.12);'
        page_title_cls = 'text-2xl font-black tracking-wide'
        page_title_style = 'color: var(--xf-text-strong);'
        card_cls = 'w-full p-4 mb-3 transition border border-l-4 rounded-sm'
        card_style = 'background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 8px 24px rgba(15,23,42,0.10);'

        with ui.row().classes(page_header_cls).style(page_header_style):
            with ui.row().classes('items-center gap-3'):
                with ui.element('div').classes(page_icon_cls).style(page_icon_style):
                    ui.element('div').classes('absolute inset-0').style('background: var(--xf-accent-soft);')
                    ui.icon('rss_feed').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                ui.label('订阅管理').classes(page_title_cls).style(page_title_style)
            ui.button('新建订阅', icon='add', on_click=lambda: open_advanced_sub_editor(None)).props('flat').classes('bg-emerald-950/45 text-emerald-300 border border-emerald-500/45 hover:bg-emerald-900/55 font-black rounded-sm px-4' if is_dark else 'bg-emerald-100 text-emerald-700 border border-emerald-300 hover:bg-emerald-200 font-black rounded-sm px-4')

        if not SUBS_CACHE:
            with ui.column().classes('w-full h-64 justify-center items-center border border-dashed rounded-sm').style('background: var(--xf-panel-bg); border-color: var(--xf-card-border); color: var(--xf-text-muted);'):
                ui.icon('rss_feed', size='4rem').style('color: var(--xf-accent); opacity: 0.8;')
                ui.label('暂无订阅').classes('text-sm font-bold').style('color: var(--xf-text-muted);')

        for idx, sub in enumerate(SUBS_CACHE):
            with ui.card().classes(card_cls).style(card_style):
                with ui.row().classes('justify-between w-full items-start'):
                    with ui.column().classes('gap-1'):
                        with ui.row().classes('items-center gap-2'):
                            ui.label(sub.get('name', '未命名订阅')).classes('font-black text-lg tracking-wide').style('color: var(--xf-text-strong);')
                            ui.badge('普通', color='cyan').props('outline size=xs').classes('text-cyan-300 border-cyan-500/45 rounded-sm' if is_dark else 'text-sky-700 border-sky-300 rounded-sm')

                        saved_node_ids = set(sub.get('nodes', []))
                        valid_count = len(saved_node_ids.intersection(all_active_keys))
                        total_count = len(saved_node_ids)

                        color_cls = 'text-green-400' if valid_count > 0 else 'text-slate-500'
                        ui.label(f"⚡ 包含节点: {valid_count} (有效) / {total_count} (总计)").classes(f'text-xs font-bold {color_cls} font-mono')

                    with ui.row().classes('gap-2'):
                        ui.button('管理订阅', icon='tune', on_click=lambda _, s=sub: open_advanced_sub_editor(s)) \
                            .props('flat dense size=sm') \
                            .classes('rounded-sm px-3 font-black border') \
                            .style('background: var(--xf-soft-bg); color: var(--xf-accent); border-color: var(--xf-card-border);') \
                            .tooltip('重命名 / 排序 / 筛选节点')

                        async def dl(i=idx):
                            with ui.dialog() as d, ui.card().classes('w-[360px] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-rose-800/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-[360px] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-rose-300 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                                with ui.column().classes('w-full p-5 gap-3 bg-gradient-to-r from-[#19070d] to-[#0b0911] border-b border-rose-900/60' if is_dark else 'w-full p-5 gap-3 bg-gradient-to-r from-rose-50 to-orange-50 border-b border-rose-200'):
                                    ui.label('确定删除此订阅？').classes('font-black text-rose-300 text-lg tracking-wide' if is_dark else 'font-black text-rose-700 text-lg tracking-wide')
                                with ui.row().classes('justify-end w-full mt-4 p-4 bg-[#030712] gap-2' if is_dark else 'justify-end w-full mt-4 p-4 bg-white gap-2'):
                                    ui.button('取消', on_click=d.close).props('outline color=grey').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm')

                                    async def confirm():
                                        del SUBS_CACHE[i]
                                        await save_subs()
                                        await load_subs_view()
                                        d.close()
                                        safe_notify('已删除', 'positive')

                                    ui.button('删除', on_click=confirm).props('flat').classes('bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 font-black rounded-sm px-4' if is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 font-black rounded-sm px-4')
                            d.open()

                        ui.button(icon='delete', on_click=dl).props('flat dense size=sm').classes('text-rose-400 hover:bg-rose-950/30 hover:text-rose-300')

                ui.separator().classes('my-3 opacity-80').style('background: var(--xf-card-border);')

                path = f"/sub/{sub['token']}"
                raw_url = f"{origin}{path}"

                with ui.row().classes('w-full items-center gap-2 p-2.5 rounded-sm justify-between border').style('background: var(--xf-code-bg); border-color: var(--xf-card-border);'):
                    with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                        ui.icon('link').classes('text-sm').style('color: var(--xf-accent);')
                        ui.label(raw_url).classes('text-xs font-mono font-bold truncate select-all').style('color: var(--xf-text-strong);')

                    with ui.row().classes('gap-1'):
                        def btn_copy(icon, color, text, func):
                            ui.button(icon=icon, on_click=func).props(f'flat dense round size=xs text-color={color}').tooltip(text).style('color: var(--xf-text-muted);')

                        btn_copy('content_copy', 'grey-4', '复制原始链接', lambda u=raw_url: safe_copy_to_clipboard(u))

                        surge_short = f"{origin}/get/sub/surge/{sub['token']}"
                        btn_copy('bolt', 'orange', '复制 Surge 订阅', lambda u=surge_short: safe_copy_to_clipboard(u))

                        clash_short = f"{origin}/get/sub/clash/{sub['token']}"
                        btn_copy('cloud_queue', 'green', '复制 Clash 订阅', lambda u=clash_short: safe_copy_to_clipboard(u))

        ui.separator().classes('my-6 opacity-80').style('background: var(--xf-card-border);')

        with ui.row().classes(page_header_cls).style(page_header_style):
            with ui.row().classes('items-center gap-3'):
                with ui.element('div').classes(page_icon_cls).style(page_icon_style):
                    ui.element('div').classes('absolute inset-0').style('background: var(--xf-accent-soft);')
                    ui.icon('hub').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                ui.label('独立节点管理').classes(page_title_cls).style(page_title_style)
            
            def open_add_independent_node():
                from app.ui.dialogs.sub_dialogs import open_independent_node_editor
                open_independent_node_editor(None)

            ui.button('添加独立节点', icon='add', on_click=open_add_independent_node).props('flat').classes('bg-blue-950/45 text-blue-300 border border-blue-500/45 hover:bg-blue-900/55 font-black rounded-sm px-4' if is_dark else 'bg-blue-100 text-blue-700 border border-blue-300 hover:bg-blue-200 font-black rounded-sm px-4')

        if not INDEPENDENT_NODES_CACHE:
            with ui.column().classes('w-full h-64 justify-center items-center border border-dashed rounded-sm').style('background: var(--xf-panel-bg); border-color: var(--xf-card-border); color: var(--xf-text-muted);'):
                ui.icon('hub', size='4rem').style('color: var(--xf-accent); opacity: 0.8;')
                ui.label('暂无独立节点').classes('text-sm font-bold').style('color: var(--xf-text-muted);')
        
        for idx, inode in enumerate(INDEPENDENT_NODES_CACHE):
            with ui.card().classes(card_cls).style(card_style):
                with ui.row().classes('justify-between w-full items-start'):
                    with ui.column().classes('gap-1'):
                        with ui.row().classes('items-center gap-2'):
                            ui.label(inode.get('remark', '未命名节点')).classes('font-black text-lg tracking-wide').style('color: var(--xf-text-strong);')
                            ui.badge('独立', color='blue').props('outline size=xs').classes('text-blue-300 border-blue-500/45 rounded-sm' if is_dark else 'text-blue-700 border-blue-300 rounded-sm')
                        
                        link = inode.get('_raw_link', '')
                        protocol = "unknown"
                        if link:
                            protocol = link.split('://')[0]
                        ui.label(f"⚡ 协议: {protocol} | ID: {inode.get('id', 'N/A')}").classes('text-xs font-bold text-slate-500 font-mono')
                    
                    with ui.row().classes('gap-2'):
                        def edit_inode(node=inode):
                            from app.ui.dialogs.sub_dialogs import open_independent_node_editor
                            open_independent_node_editor(node)
                            
                        ui.button('编辑', icon='edit', on_click=edit_inode) \
                            .props('flat dense size=sm') \
                            .classes('rounded-sm px-3 font-black border') \
                            .style('background: var(--xf-soft-bg); color: var(--xf-accent); border-color: var(--xf-card-border);')
                            
                        async def dl_inode(i=idx):
                            with ui.dialog() as d, ui.card().classes('w-[360px] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-rose-800/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-[360px] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-rose-300 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                                with ui.column().classes('w-full p-5 gap-3 bg-gradient-to-r from-[#19070d] to-[#0b0911] border-b border-rose-900/60' if is_dark else 'w-full p-5 gap-3 bg-gradient-to-r from-rose-50 to-orange-50 border-b border-rose-200'):
                                    ui.label('确定删除此独立节点？').classes('font-black text-rose-300 text-lg tracking-wide' if is_dark else 'font-black text-rose-700 text-lg tracking-wide')
                                with ui.row().classes('justify-end w-full mt-4 p-4 bg-[#030712] gap-2' if is_dark else 'justify-end w-full mt-4 p-4 bg-white gap-2'):
                                    ui.button('取消', on_click=d.close).props('outline color=grey').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm')

                                    async def confirm():
                                        del INDEPENDENT_NODES_CACHE[i]
                                        await save_independent_nodes()
                                        await load_subs_view()
                                        d.close()
                                        safe_notify('已删除独立节点', 'positive')

                                    ui.button('删除', on_click=confirm).props('flat').classes('bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 font-black rounded-sm px-4' if is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 font-black rounded-sm px-4')
                            d.open()
                            
                        ui.button(icon='delete', on_click=dl_inode).props('flat dense size=sm').classes('text-rose-400 hover:bg-rose-950/30 hover:text-rose-300')
                
                ui.separator().classes('my-3 opacity-80').style('background: var(--xf-card-border);')
                
                with ui.row().classes('w-full items-center gap-2 p-2.5 rounded-sm justify-between border').style('background: var(--xf-code-bg); border-color: var(--xf-card-border);'):
                    with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                        ui.icon('link').classes('text-sm').style('color: var(--xf-accent);')
                        ui.label(inode.get('_raw_link', '')).classes('text-xs font-mono font-bold truncate select-all').style('color: var(--xf-text-strong);')
                    
                    with ui.row().classes('gap-1'):
                        def btn_copy(icon, color, text, func):
                            ui.button(icon=icon, on_click=func).props(f'flat dense round size=xs text-color={color}').tooltip(text).style('color: var(--xf-text-muted);')
                            
                        btn_copy('content_copy', 'grey-4', '复制节点链接', lambda u=inode.get('_raw_link', ''): safe_copy_to_clipboard(u))
