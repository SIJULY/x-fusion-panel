import asyncio

from nicegui import app, ui

from app.core.state import ADMIN_CONFIG, CURRENT_VIEW_STATE, NODES_DATA, SERVERS_CACHE, SUBS_CACHE
from app.storage.repositories import save_admin_config, save_subs
from app.ui.common.notifications import safe_copy_to_clipboard, safe_notify, show_loading


async def load_subs_view():
    global CURRENT_VIEW_STATE
    from app.ui.dialogs.sub_dialogs import open_advanced_sub_editor

    CURRENT_VIEW_STATE['scope'] = 'SUBS'
    CURRENT_VIEW_STATE['data'] = None

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
    content_container.style(f'background-color: {"#030712" if is_dark else "#eef4ff"};')

    all_active_keys = set()
    for srv in SERVERS_CACHE:
        panel = NODES_DATA.get(srv['url'], []) or []
        custom = srv.get('custom_nodes', []) or []
        for n in (panel + custom):
            key = f"{srv['url']}|{n['id']}"
            all_active_keys.add(key)

    with content_container:
        page_header_cls = 'w-full mb-5 justify-between items-center border-b border-[#1e3a5f]/60 pb-3' if is_dark else 'w-full mb-5 justify-between items-center border-b border-slate-300/90 pb-3'
        page_icon_cls = 'w-10 h-10 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] text-cyan-400 shadow-[0_0_10px_rgba(0,0,0,0.45)] relative overflow-hidden' if is_dark else 'w-10 h-10 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 text-sky-600 shadow-[0_4px_12px_rgba(148,163,184,0.12)] relative overflow-hidden'
        page_title_cls = 'text-2xl font-black text-slate-100 tracking-wide' if is_dark else 'text-2xl font-black text-slate-800 tracking-wide'
        card_cls = 'w-full p-4 mb-3 shadow-[0_0_16px_rgba(0,0,0,0.28)] hover:shadow-[0_0_24px_rgba(34,211,238,0.08)] transition border border-[#1e3a5f]/55 border-l-4 border-l-cyan-500 rounded-sm bg-[#070b14]' if is_dark else 'w-full p-4 mb-3 shadow-[0_8px_24px_rgba(148,163,184,0.14)] hover:shadow-[0_10px_26px_rgba(56,189,248,0.12)] transition border border-slate-300/90 border-l-4 border-l-sky-500 rounded-sm bg-white'

        with ui.row().classes(page_header_cls):
            with ui.row().classes('items-center gap-3'):
                with ui.element('div').classes(page_icon_cls):
                    ui.element('div').classes('absolute inset-0 bg-cyan-400/10' if is_dark else 'absolute inset-0 bg-sky-400/10')
                    ui.icon('rss_feed').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                ui.label('订阅管理').classes(page_title_cls)
            ui.button('新建订阅', icon='add', on_click=lambda: open_advanced_sub_editor(None)).props('flat').classes('bg-emerald-950/45 text-emerald-300 border border-emerald-500/45 hover:bg-emerald-900/55 font-black rounded-sm px-4' if is_dark else 'bg-emerald-100 text-emerald-700 border border-emerald-300 hover:bg-emerald-200 font-black rounded-sm px-4')

        if not SUBS_CACHE:
            with ui.column().classes('w-full h-64 justify-center items-center text-slate-600 border border-dashed border-[#1e3a5f]/45 rounded-sm bg-[#070b14]' if is_dark else 'w-full h-64 justify-center items-center text-slate-600 border border-dashed border-slate-300/90 rounded-sm bg-white'):
                ui.icon('rss_feed', size='4rem').classes('text-cyan-400 opacity-80' if is_dark else 'text-sky-600 opacity-80')
                ui.label('暂无订阅').classes('text-sm font-bold text-slate-500')

        for idx, sub in enumerate(SUBS_CACHE):
            with ui.card().classes(card_cls):
                with ui.row().classes('justify-between w-full items-start'):
                    with ui.column().classes('gap-1'):
                        with ui.row().classes('items-center gap-2'):
                            ui.label(sub.get('name', '未命名订阅')).classes('font-black text-lg text-slate-100 tracking-wide' if is_dark else 'font-black text-lg text-slate-800 tracking-wide')
                            ui.badge('普通', color='cyan').props('outline size=xs').classes('text-cyan-300 border-cyan-500/45 rounded-sm' if is_dark else 'text-sky-700 border-sky-300 rounded-sm')

                        saved_node_ids = set(sub.get('nodes', []))
                        valid_count = len(saved_node_ids.intersection(all_active_keys))
                        total_count = len(saved_node_ids)

                        color_cls = 'text-green-400' if valid_count > 0 else 'text-slate-500'
                        ui.label(f"⚡ 包含节点: {valid_count} (有效) / {total_count} (总计)").classes(f'text-xs font-bold {color_cls} font-mono')

                    with ui.row().classes('gap-2'):
                        ui.button('管理订阅', icon='tune', on_click=lambda _, s=sub: open_advanced_sub_editor(s)) \
                            .props('flat dense size=sm') \
                            .classes('bg-cyan-950/40 text-cyan-300 border border-cyan-500/40 hover:bg-cyan-900/55 rounded-sm px-3 font-black' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 rounded-sm px-3 font-black') \
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

                ui.separator().classes('my-3 bg-[#1e3a5f]/60 opacity-80' if is_dark else 'my-3 bg-slate-300/80 opacity-80')

                path = f"/sub/{sub['token']}"
                raw_url = f"{origin}{path}"

                with ui.row().classes('w-full items-center gap-2 bg-black p-2.5 rounded-sm justify-between border border-[#1e3a5f]/45' if is_dark else 'w-full items-center gap-2 bg-sky-50 p-2.5 rounded-sm justify-between border border-slate-300/90'):
                    with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                        ui.icon('link').classes('text-cyan-400 text-sm' if is_dark else 'text-sky-600 text-sm')
                        ui.label(raw_url).classes('text-xs font-mono text-emerald-400 font-bold truncate select-all' if is_dark else 'text-xs font-mono text-slate-700 font-bold truncate select-all')

                    with ui.row().classes('gap-1'):
                        def btn_copy(icon, color, text, func):
                            ui.button(icon=icon, on_click=func).props(f'flat dense round size=xs text-color={color}').tooltip(text).classes('hover:bg-cyan-950/30' if is_dark else 'hover:bg-sky-100')

                        btn_copy('content_copy', 'grey-4', '复制原始链接', lambda u=raw_url: safe_copy_to_clipboard(u))

                        surge_short = f"{origin}/get/sub/surge/{sub['token']}"
                        btn_copy('bolt', 'orange', '复制 Surge 订阅', lambda u=surge_short: safe_copy_to_clipboard(u))

                        clash_short = f"{origin}/get/sub/clash/{sub['token']}"
                        btn_copy('cloud_queue', 'green', '复制 Clash 订阅', lambda u=clash_short: safe_copy_to_clipboard(u))
