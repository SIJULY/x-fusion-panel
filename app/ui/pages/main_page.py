import asyncio
import uuid

from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app, run, ui

from app.core.config import AUTO_REGISTER_SECRET
from app.core.logging import logger
from app.core.state import ADMIN_CONFIG, SERVERS_CACHE
from app.storage.repositories import save_admin_config
from app.ui.common.notifications import safe_copy_to_clipboard
from app.ui.components.sidebar import render_sidebar_content
from app.ui.pages.login_page import check_auth
from app.utils.geo import fetch_geo_from_ip
from app.utils.network import get_dynamic_origin


def main_page(request: Request):
    is_dark = bool(app.storage.user.get('is_dark', True))
    app.storage.user['is_dark'] = is_dark

    dark = ui.dark_mode()
    if is_dark:
        dark.enable()
    else:
        dark.disable()

    theme = {
        'body_bg': 'radial-gradient(circle at top, rgba(34,211,238,0.08), transparent 28%), linear-gradient(180deg, #050a14 0%, #030712 100%)' if is_dark else 'radial-gradient(circle at top, rgba(59,130,246,0.10), transparent 24%), linear-gradient(180deg, #f8fbff 0%, #eef4ff 100%)',
        'body_text': '#e2e8f0' if is_dark else '#0f172a',
        'card_bg': '#070b14' if is_dark else '#ffffff',
        'card_border': 'rgba(30,58,95,0.55)' if is_dark else 'rgba(148,163,184,0.35)',
        'drawer_bg': '#070b14' if is_dark else '#f8fbff',
        'scroll_track': '#030712' if is_dark else '#e2e8f0',
        'scroll_thumb': '#1e3a5f' if is_dark else '#94a3b8',
        'scroll_thumb_hover': '#2563eb' if is_dark else '#64748b',
        'content_bg': '#030712' if is_dark else '#eef4ff',
        'header_classes': 'bg-gradient-to-r from-[#070e1a] to-[#0a1526] text-white h-14 border-b border-[#1e3a5f]/60 shadow-[0_4px_20px_rgba(0,0,0,0.6)]' if is_dark else 'bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] text-slate-900 h-14 border-b border-[#cbd5e1] shadow-[0_4px_16px_rgba(148,163,184,0.18)]',
        'drawer_classes': 'bg-[#070b14] border-r border-[#1e3a5f]/55' if is_dark else 'bg-[#f8fbff] border-r border-[#cbd5e1]/80',
        'menu_btn_classes': 'text-slate-300 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'text-slate-600 hover:text-blue-600 hover:bg-blue-100/80',
        'title_classes': 'text-xl font-black ml-2 tracking-wide text-cyan-400 drop-shadow-[0_0_6px_rgba(34,211,238,0.55)]' if is_dark else 'text-xl font-black ml-2 tracking-wide text-sky-700',
        'security_btn_classes': 'text-rose-400 hover:bg-rose-950/30 hover:text-rose-300' if is_dark else 'text-rose-500 hover:bg-rose-100 hover:text-rose-600',
        'key_btn_classes': 'text-slate-400 hover:bg-cyan-950/30 hover:text-cyan-300' if is_dark else 'text-slate-500 hover:bg-sky-100 hover:text-sky-600',
        'theme_btn_classes': 'text-amber-300 hover:bg-amber-950/30 hover:text-yellow-200' if is_dark else 'text-slate-500 hover:bg-indigo-100 hover:text-indigo-600',
        'logout_btn_classes': 'text-slate-400 hover:bg-slate-800/50 hover:text-cyan-300' if is_dark else 'text-slate-500 hover:bg-slate-200 hover:text-slate-700',
        'theme_icon': 'light_mode' if is_dark else 'dark_mode',
        'theme_tooltip': '切换到浅色模式' if is_dark else '切换到深色模式',
    }

    ui.colors(
        primary='#22d3ee',
        secondary='#334155',
        accent='#8b5cf6',
        dark='#030712',
        positive='#10b981',
        negative='#ef4444',
        info='#38bdf8',
        warning='#f59e0b',
    )

    ui.add_head_html(f'''
        <link rel="stylesheet" href="/static/xterm.css" />
        <script src="/static/xterm.js"></script>
        <script src="/static/xterm-addon-fit.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
        <style>
            :root {{
                --xf-bg-main: {theme['content_bg']};
                --xf-card-bg: {theme['card_bg']};
                --xf-card-border: {theme['card_border']};
                --xf-drawer-bg: {theme['drawer_bg']};
                --xf-text-main: {theme['body_text']};
            }}
            @font-face {{
                font-family: 'Twemoji Country Flags';
                src: url('https://cdn.jsdelivr.net/npm/country-flag-emoji-polyfill@0.1/dist/TwemojiCountryFlags.woff2') format('woff2');
                unicode-range: U+1F1E6-1F1FF, U+1F3F4, U+E0062-E007F;
            }}
            html, body, #app {{
                background: {theme['body_bg']} !important;
            }}
            body {{
                color: {theme['body_text']} !important;
                font-family: 'Twemoji Country Flags', 'Noto Sans SC', "Roboto", "Helvetica", "Arial", sans-serif, "Noto Color Emoji";
            }}
            .nicegui-connection-lost {{ display: none !important; }}
            ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
            ::-webkit-scrollbar-track {{ background: {theme['scroll_track']}; }}
            ::-webkit-scrollbar-thumb {{ background: {theme['scroll_thumb']}; border-radius: 3px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: {theme['scroll_thumb_hover']}; }}
            .q-card {{ background-color: {theme['card_bg']} !important; border: 1px solid {theme['card_border']} !important; }}
            .q-drawer {{ background-color: {theme['drawer_bg']} !important; }}
            .q-layout,
            .q-page-container,
            .q-page,
            .q-page-sticky,
            .q-layout__section,
            .q-layout__shadow,
            .nicegui-content {{
                background: transparent !important;
                background-color: transparent !important;
            }}
            .q-page-container {{
                min-height: calc(100vh - 56px) !important;
            }}
            .q-dialog__backdrop,
            .q-overlay,
            .q-popup__backdrop {{
                background: transparent !important;
                opacity: 1 !important;
                backdrop-filter: none !important;
            }}
            .q-dialog__inner,
            .q-dialog__inner > div,
            .q-menu {{
                background: transparent !important;
                background-color: transparent !important;
            }}
            .q-tooltip {{
                background: #050b14 !important;
                color: #f1f5f9 !important;
                border: 1px solid rgba(6,182,212,0.35) !important;
                box-shadow: 0 6px 18px rgba(0,0,0,0.35) !important;
            }}
            body:not(.body--dark) .q-tooltip {{
                background: #f8fbff !important;
                color: #334155 !important;
                border: 1px solid #cbd5e1 !important;
                box-shadow: 0 8px 20px rgba(148,163,184,0.18) !important;
            }}
        </style>
    ''')

    if not check_auth(request):
        return RedirectResponse('/login')

    try:
        current_ip = request.headers.get('X-Forwarded-For', request.client.host).split(',')[0].strip()
        current_device_id = request.cookies.get('fp_device_id', 'Unknown')
    except:
        current_ip = 'Unknown'
        current_device_id = 'Unknown'

    last_ip = app.storage.user.get('last_known_ip', '')
    last_device_id = app.storage.user.get('device_id', '')
    login_region = app.storage.user.get('login_region', '未知区域')

    async def reset_global_session(dialog_ref=None):
        new_ver = str(uuid.uuid4())[:8]
        ADMIN_CONFIG['session_version'] = new_ver
        await save_admin_config()
        if dialog_ref:
            dialog_ref.close()
        ui.notify('🔒 安全密钥已重置，正在强制所有设备下线...', type='warning', close_button=False)
        await asyncio.sleep(1.5)
        app.storage.user.clear()
        ui.navigate.to('/login')

    def trigger_geo_alert(new_ip, old_ip, old_loc, new_loc):
        app.storage.user['last_known_ip'] = new_ip
        with ui.dialog() as d, ui.card().classes('w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-rose-800/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-rose-300 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
            with ui.column().classes('w-full p-5 gap-3 bg-gradient-to-r from-[#19070d] to-[#0b0911] border-b border-rose-900/60 relative overflow-hidden' if is_dark else 'w-full p-5 gap-3 bg-gradient-to-r from-rose-50 to-orange-50 border-b border-rose-200 relative overflow-hidden'):
                ui.element('div').classes('absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgyNDQsNjMsOTQsMC4wNykiLz48L3N2Zz4=")] opacity-100 pointer-events-none')
                with ui.row().classes('items-center gap-3 text-rose-400 z-10'):
                    with ui.element('div').classes('w-9 h-9 rounded-sm flex items-center justify-center bg-[#14070b] border border-rose-900/60 shadow-[0_0_8px_rgba(0,0,0,0.7)] relative overflow-hidden'):
                        ui.element('div').classes('absolute inset-0 bg-rose-400/10')
                        ui.icon('gpp_bad').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                    with ui.column().classes('gap-0'):
                        ui.label('安全拦截：异地/异常设备登录').classes('font-black text-lg tracking-wide')
                        ui.label('检测到会话异常跳变，可能存在 Cookie 劫持风险').classes('text-[10px] text-slate-400 tracking-wide')
            with ui.column().classes('w-full p-5 gap-4 bg-[#030712]' if is_dark else 'w-full p-5 gap-4 bg-white'):
                ui.label('系统检测到您的会话出现了异常跳变，可能存在 Cookie 劫持风险：').classes('text-sm text-slate-300' if is_dark else 'text-sm text-slate-700')
                with ui.grid().classes('grid-cols-1 gap-2 bg-rose-950/20 p-3 rounded-sm border border-rose-500/35'):
                    ui.label(f'原始登录地: {old_ip} ({old_loc})').classes('text-xs font-mono font-bold text-slate-400')
                    ui.label(f'当前请求源: {new_ip} ({new_loc})').classes('text-xs font-mono font-bold text-rose-400')
                ui.label('如果您正在使用代理节点访问面板，请忽略；如果不是您本人的操作，请立即强制下线所有设备！').classes('text-xs text-rose-300 font-bold')
            with ui.row().classes('w-full justify-end gap-3 p-4 border-t border-rose-900/40 bg-[#0b0911]' if is_dark else 'w-full justify-end gap-3 p-4 border-t border-rose-200 bg-rose-50'):
                ui.button('是本人操作 (忽略)', on_click=d.close).props('outline color=grey').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-white text-xs font-bold tracking-wide rounded-sm')
                ui.button('冻结并强制下线', icon='block', on_click=lambda: reset_global_session(d)).props('flat').classes('bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 hover:shadow-[0_0_12px_rgba(244,63,94,0.28)] px-5 font-black text-xs tracking-wide rounded-sm' if is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 px-5 font-black text-xs tracking-wide rounded-sm')
        d.open()

    async def toggle_theme():
        app.storage.user['is_dark'] = not is_dark
        ui.navigate.reload()

    async def run_security_check():
        if last_ip and last_ip != current_ip:
            if last_device_id and last_device_id == current_device_id:
                current_geo = await run.io_bound(fetch_geo_from_ip, current_ip)
                current_region = f"{current_geo[2]}-{current_geo[3]}" if current_geo else '未知区域'
                if current_region == login_region or '未知' in current_region:
                    app.storage.user['last_known_ip'] = current_ip
                else:
                    trigger_geo_alert(current_ip, last_ip, login_region, current_region)
            else:
                trigger_geo_alert(current_ip, last_ip, '旧设备', '未知新设备')

    ui.timer(0.5, run_security_check, once=True)

    with ui.left_drawer(value=True, fixed=True).classes(theme['drawer_classes']).props('width=360 bordered') as drawer:
        render_sidebar_content()

    with ui.header().classes(theme['header_classes']):
        with ui.row().classes('w-full items-center justify-between'):
            with ui.row().classes('items-center gap-2'):
                ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense').classes(theme['menu_btn_classes'])
                ui.label('X-Fusion-pro').classes(theme['title_classes'])

            with ui.row().classes('items-center gap-3 mr-2'):
                with ui.button(icon='gpp_bad', on_click=lambda: reset_global_session(None)).props('flat dense round size=sm').classes(theme['security_btn_classes']).tooltip('安全重置'):
                    ui.badge('Reset', color='orange').props('floating rounded-sm').classes('text-[10px] font-black')

                with ui.button(icon='vpn_key', on_click=lambda: safe_copy_to_clipboard(AUTO_REGISTER_SECRET)).props('flat dense round size=sm').classes(theme['key_btn_classes']).tooltip('复制通讯密钥'):
                    ui.badge('Key', color='red').props('floating rounded-sm').classes('text-[10px] font-black')

                ui.button(icon=theme['theme_icon'], on_click=toggle_theme).props('flat round dense').classes(theme['theme_btn_classes']).tooltip(theme['theme_tooltip'])
                ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat round dense').classes(theme['logout_btn_classes']).tooltip('退出登录')

    from app.ui.pages import content_router

    content_router.content_container = ui.column().classes('w-full h-full min-h-[calc(100vh-56px)] pl-4 pr-4 pt-4 overflow-y-auto').style(f'background-color: {theme["content_bg"]};')
    logger.info(f"[MainPage] content_container assigned | id={id(content_router.content_container)}")

    async def auto_init_system_settings():
        try:
            real_origin = get_dynamic_origin()
            if 'YOUR-DOMAIN' in real_origin:
                real_origin = await ui.run_javascript('return window.location.origin', timeout=3.0)

            if not real_origin:
                return

            stored_url = ADMIN_CONFIG.get('manager_base_url', '')
            need_save = False

            if 'session_version' not in ADMIN_CONFIG:
                ADMIN_CONFIG['session_version'] = 'init_v1'
                need_save = True

            if not stored_url or 'sijuly.nyc.mn' in stored_url or '127.0.0.1' in stored_url:
                ADMIN_CONFIG['manager_base_url'] = real_origin
                need_save = True

            if not ADMIN_CONFIG.get('probe_enabled'):
                ADMIN_CONFIG['probe_enabled'] = True
                need_save = True

            if need_save:
                await save_admin_config()
        except:
            pass

    ui.timer(1.0, auto_init_system_settings, once=True)

    async def restore_last_view():
        from app.ui.components.dashboard import load_dashboard_stats
        from app.ui.pages.content_router import refresh_content
        from app.ui.pages.probe_page import render_probe_page
        from app.ui.pages.subs_page import load_subs_view

        logger.info(f"[MainPage] restore_last_view start | stored_scope={app.storage.user.get('last_view_scope', 'DASHBOARD')} stored_data={app.storage.user.get('last_view_data', None)} content_container_id={id(content_router.content_container) if content_router.content_container else None}")

        last_scope = app.storage.user.get('last_view_scope', 'DASHBOARD')
        last_data_id = app.storage.user.get('last_view_data', None)
        target_data = last_data_id
        if last_scope in ['SINGLE', 'SSH_SINGLE'] and last_data_id:
            target_data = next((s for s in SERVERS_CACHE if s['url'] == last_data_id), None)
            if not target_data:
                last_scope = 'DASHBOARD'

        if last_scope == 'DASHBOARD':
            logger.info("[MainPage] restore_last_view branch=DASHBOARD")
            await load_dashboard_stats()
        elif last_scope == 'PROBE':
            logger.info("[MainPage] restore_last_view branch=PROBE")
            await render_probe_page()
        elif last_scope == 'SUBS':
            logger.info("[MainPage] restore_last_view branch=SUBS")
            await load_subs_view()
        else:
            logger.info(f"[MainPage] restore_last_view branch={last_scope} target_data={target_data}")
            await refresh_content(last_scope, target_data)
        logger.info(f'♻️ 自动恢复视图: {last_scope}')

    ui.timer(0.1, lambda: asyncio.create_task(restore_last_view()), once=True)
    logger.info('✅ UI 已就绪')
