import asyncio
import json
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
    def build_theme(is_dark: bool):
        return {
            'body_bg': 'radial-gradient(circle at top, rgba(34,211,238,0.08), transparent 28%), linear-gradient(180deg, #050a14 0%, #030712 100%)' if is_dark else 'radial-gradient(circle at top, rgba(59,130,246,0.10), transparent 24%), linear-gradient(180deg, #f8fbff 0%, #eef4ff 100%)',
            'body_text': '#e2e8f0' if is_dark else '#0f172a',
            'card_bg': '#070b14' if is_dark else '#ffffff',
            'card_border': 'rgba(30,58,95,0.55)' if is_dark else 'rgba(148,163,184,0.35)',
            'drawer_bg': '#070b14' if is_dark else '#f8fbff',
            'scroll_track': '#030712' if is_dark else '#e2e8f0',
            'scroll_thumb': '#1e3a5f' if is_dark else '#94a3b8',
            'scroll_thumb_hover': '#2563eb' if is_dark else '#64748b',
            'content_bg': '#030712' if is_dark else '#eef4ff',
            'panel_bg': '#070b14' if is_dark else '#ffffff',
            'soft_bg': '#0a1120' if is_dark else '#f8fbff',
            'elevated_bg': '#08101d' if is_dark else '#ffffff',
            'accent': '#22d3ee' if is_dark else '#0369a1',
            'accent_soft': 'rgba(34,211,238,0.10)' if is_dark else 'rgba(56,189,248,0.12)',
            'text_strong': '#e2e8f0' if is_dark else '#0f172a',
            'text_muted': '#94a3b8' if is_dark else '#64748b',
            'text_subtle': '#64748b' if is_dark else '#94a3b8',
            'hover_bg': '#0d172a' if is_dark else '#f0f9ff',
            'code_bg': '#050b14' if is_dark else '#f8fbff',
            'tooltip_bg': '#050b14' if is_dark else '#f8fbff',
            'tooltip_text': '#f1f5f9' if is_dark else '#334155',
            'tooltip_border': 'rgba(6,182,212,0.35)' if is_dark else '#cbd5e1',
            'tooltip_shadow': '0 6px 18px rgba(0,0,0,0.35)' if is_dark else '0 8px 20px rgba(148,163,184,0.18)',
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

    is_dark = bool(app.storage.user.get('is_dark', False))
    app.storage.user['is_dark'] = is_dark

    dark = ui.dark_mode()
    if is_dark:
        dark.enable()
    else:
        dark.disable()

    theme = build_theme(is_dark)

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
        <script>
            window.applyXFusionTheme = function(theme) {{
                if (!theme) return;
                const root = document.documentElement;
                const pairs = {{
                    '--xf-body-bg': theme.body_bg,
                    '--xf-bg-main': theme.content_bg,
                    '--xf-panel-bg': theme.panel_bg,
                    '--xf-soft-bg': theme.soft_bg,
                    '--xf-elevated-bg': theme.elevated_bg,
                    '--xf-card-bg': theme.card_bg,
                    '--xf-card-border': theme.card_border,
                    '--xf-drawer-bg': theme.drawer_bg,
                    '--xf-text-main': theme.body_text,
                    '--xf-text-strong': theme.text_strong,
                    '--xf-text-muted': theme.text_muted,
                    '--xf-text-subtle': theme.text_subtle,
                    '--xf-accent': theme.accent,
                    '--xf-accent-soft': theme.accent_soft,
                    '--xf-hover-bg': theme.hover_bg,
                    '--xf-code-bg': theme.code_bg,
                    '--xf-scroll-track': theme.scroll_track,
                    '--xf-scroll-thumb': theme.scroll_thumb,
                    '--xf-scroll-thumb-hover': theme.scroll_thumb_hover,
                    '--xf-tooltip-bg': theme.tooltip_bg,
                    '--xf-tooltip-text': theme.tooltip_text,
                    '--xf-tooltip-border': theme.tooltip_border,
                    '--xf-tooltip-shadow': theme.tooltip_shadow,
                }};
                Object.entries(pairs).forEach(([key, value]) => root.style.setProperty(key, value));
            }};
            window.applyXFusionShellTheme = function(payload) {{
                if (!payload) return;
                const setStyle = (id, styleText) => {{
                    const el = document.getElementById(id);
                    if (el && styleText) el.style.cssText = styleText;
                }};
                setStyle('xf-header', payload.header_style);
                setStyle('xf-drawer', payload.drawer_style);
                setStyle('xf-menu-btn', payload.menu_btn_style);
                setStyle('xf-title', payload.title_style);
                setStyle('xf-security-btn', payload.security_btn_style);
                setStyle('xf-key-btn', payload.key_btn_style);
                setStyle('xf-theme-btn', payload.theme_btn_style);
                setStyle('xf-logout-btn', payload.logout_btn_style);
                const themeIcon = document.querySelector('#xf-theme-btn i');
                if (themeIcon) themeIcon.textContent = payload.theme_icon;
                const content = document.getElementById('xf-content-container');
                if (content) content.style.backgroundColor = payload.content_bg;
            }};
            window.applyXFusionDomTheme = function(isDark) {{
                const darkToLight = [
                    ['bg-[#070b14]', 'bg-white'],
                    ['bg-[#030712]', 'bg-[#eef4ff]'],
                    ['bg-[#0a1120]', 'bg-white'],
                    ['bg-[#050a14]', 'bg-[#eef4ff]'],
                    ['bg-[#050b14]', 'bg-sky-50'],
                    ['bg-[#08101d]/80', 'bg-white'],
                    ['bg-[#08101d]/90', 'bg-white'],
                    ['bg-[#0c1728]', 'bg-sky-50'],
                    ['bg-[#0a1120]/80', 'bg-white'],
                    ['bg-[#0a1120]/85', 'bg-white/95'],
                    ['bg-[#0a1120]/90', 'bg-white/95'],
                    ['bg-[#111827]', 'bg-[#f8fbff]'],
                    ['bg-[#1e293b]', 'bg-white'],
                    ['bg-black', 'bg-[#f8fbff]'],
                    ['bg-[#0d172a]', 'bg-sky-50'],
                    ['from-[#0a1526]', 'from-[#f8fbff]'],
                    ['to-[#050a14]', 'to-[#eef4ff]'],
                    ['from-[#0a1120]', 'from-[#f8fbff]'],
                    ['from-[#10203d]', 'from-[#eff6ff]'],
                    ['to-[#050b14]', 'to-[#dbeafe]'],
                    ['text-slate-100', 'text-slate-800'],
                    ['text-slate-200', 'text-slate-800'],
                    ['text-slate-300', 'text-slate-700'],
                    ['text-slate-400', 'text-slate-500'],
                    ['text-cyan-300', 'text-sky-700'],
                    ['text-cyan-400', 'text-sky-600'],
                    ['text-cyan-500', 'text-sky-700'],
                    ['text-cyan-600/80', 'text-sky-700/80'],
                    ['text-cyan-900', 'text-sky-700'],
                    ['border-[#1e3a5f]/60', 'border-slate-300/90'],
                    ['border-[#1e3a5f]/55', 'border-slate-300/90'],
                    ['border-[#1e3a5f]/50', 'border-slate-300/90'],
                    ['border-[#1e3a5f]/45', 'border-slate-300/90'],
                    ['border-[#1e3a5f]/40', 'border-slate-300/90'],
                    ['border-[#1e3a5f]/35', 'border-slate-200/90'],
                    ['border-[#1e3a5f]', 'border-slate-300'],
                    ['border-slate-700', 'border-slate-300'],
                    ['border-slate-600', 'border-slate-300'],
                    ['border-l-cyan-700/80', 'border-l-sky-500'],
                    ['border-l-cyan-500', 'border-l-sky-600'],
                    ['hover:bg-cyan-950/30', 'hover:bg-sky-100'],
                    ['hover:bg-cyan-900/55', 'hover:bg-sky-200'],
                    ['hover:text-cyan-300', 'hover:text-sky-700'],
                    ['hover:border-cyan-500/45', 'hover:border-sky-400/60'],
                    ['hover:border-cyan-500/35', 'hover:border-sky-400/60'],
                    ['hover:border-cyan-500/40', 'hover:border-sky-400/70'],
                    ['shadow-[0_0_16px_rgba(0,0,0,0.28)]', 'shadow-[0_8px_24px_rgba(148,163,184,0.14)]'],
                    ['shadow-[0_0_12px_rgba(0,0,0,0.35)]', 'shadow-[0_6px_18px_rgba(148,163,184,0.14)]'],
                    ['shadow-[0_0_10px_rgba(0,0,0,0.2)]', 'shadow-[0_6px_18px_rgba(148,163,184,0.12)]'],
                    ['shadow-[0_10px_30px_rgba(0,0,0,0.8)]', 'shadow-[0_10px_28px_rgba(148,163,184,0.16)]'],
                ];
                const lightToDark = darkToLight.map(([a, b]) => [b, a]);
                const swaps = isDark ? lightToDark : darkToLight;
                const elements = document.querySelectorAll('[class]');
                elements.forEach(el => {{
                    let cls = el.className;
                    if (typeof cls !== 'string') return;
                    swaps.forEach(([from, to]) => {{ cls = cls.split(from).join(to); }});
                    el.className = cls;
                }});
            }};
        </script>
        <style>
            :root {{
                --xf-body-bg: {theme['body_bg']};
                --xf-bg-main: {theme['content_bg']};
                --xf-panel-bg: {theme['panel_bg']};
                --xf-soft-bg: {theme['soft_bg']};
                --xf-elevated-bg: {theme['elevated_bg']};
                --xf-card-bg: {theme['card_bg']};
                --xf-card-border: {theme['card_border']};
                --xf-drawer-bg: {theme['drawer_bg']};
                --xf-text-main: {theme['body_text']};
                --xf-text-strong: {theme['text_strong']};
                --xf-text-muted: {theme['text_muted']};
                --xf-text-subtle: {theme['text_subtle']};
                --xf-accent: {theme['accent']};
                --xf-accent-soft: {theme['accent_soft']};
                --xf-hover-bg: {theme['hover_bg']};
                --xf-code-bg: {theme['code_bg']};
                --xf-scroll-track: {theme['scroll_track']};
                --xf-scroll-thumb: {theme['scroll_thumb']};
                --xf-scroll-thumb-hover: {theme['scroll_thumb_hover']};
                --xf-tooltip-bg: {theme['tooltip_bg']};
                --xf-tooltip-text: {theme['tooltip_text']};
                --xf-tooltip-border: {theme['tooltip_border']};
                --xf-tooltip-shadow: {theme['tooltip_shadow']};
            }}
            @font-face {{
                font-family: 'Twemoji Country Flags';
                src: url('https://cdn.jsdelivr.net/npm/country-flag-emoji-polyfill@0.1/dist/TwemojiCountryFlags.woff2') format('woff2');
                unicode-range: U+1F1E6-1F1FF, U+1F3F4, U+E0062-E007F;
            }}
            html, body, #app {{
                background: var(--xf-body-bg) !important;
            }}
            body {{
                color: var(--xf-text-main) !important;
                font-family: 'Twemoji Country Flags', 'Noto Sans SC', "Roboto", "Helvetica", "Arial", sans-serif, "Noto Color Emoji";
            }}
            .nicegui-connection-lost {{ display: none !important; }}
            ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
            ::-webkit-scrollbar-track {{ background: var(--xf-scroll-track); }}
            ::-webkit-scrollbar-thumb {{ background: var(--xf-scroll-thumb); border-radius: 3px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: var(--xf-scroll-thumb-hover); }}
            .q-card {{ background-color: var(--xf-card-bg) !important; border: 1px solid var(--xf-card-border) !important; }}
            .q-drawer {{ background-color: var(--xf-drawer-bg) !important; }}
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
                background: var(--xf-tooltip-bg) !important;
                color: var(--xf-tooltip-text) !important;
                border: 1px solid var(--xf-tooltip-border) !important;
                box-shadow: var(--xf-tooltip-shadow) !important;
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
        new_is_dark = not bool(app.storage.user.get('is_dark', False))
        app.storage.user['is_dark'] = new_is_dark
        new_theme = build_theme(new_is_dark)

        if new_is_dark:
            dark.enable()
        else:
            dark.disable()

        payload = {
            'header_style': 'background: linear-gradient(to right, #070e1a, #0a1526); color: white; border-bottom: 1px solid rgba(30,58,95,0.60); box-shadow: 0 4px 20px rgba(0,0,0,0.6);' if new_is_dark else 'background: linear-gradient(to right, #f8fbff, #eaf2ff); color: #0f172a; border-bottom: 1px solid #cbd5e1; box-shadow: 0 4px 16px rgba(148,163,184,0.18);',
            'drawer_style': 'background-color: #070b14; border-right: 1px solid rgba(30,58,95,0.55);' if new_is_dark else 'background-color: #f8fbff; border-right: 1px solid rgba(203,213,225,0.80);',
            'menu_btn_style': 'color: #cbd5e1;' if new_is_dark else 'color: #475569;',
            'title_style': 'color: #22d3ee; text-shadow: 0 0 6px rgba(34,211,238,0.55);' if new_is_dark else 'color: #0369a1;',
            'security_btn_style': 'color: #fb7185;' if new_is_dark else 'color: #f43f5e;',
            'key_btn_style': 'color: #94a3b8;' if new_is_dark else 'color: #64748b;',
            'theme_btn_style': 'color: #fcd34d;' if new_is_dark else 'color: #64748b;',
            'logout_btn_style': 'color: #94a3b8;' if new_is_dark else 'color: #64748b;',
            'theme_icon': new_theme['theme_icon'],
            'content_bg': new_theme['content_bg'],
        }
        js_payload = json.dumps(payload, ensure_ascii=False)
        js_theme = json.dumps(new_theme, ensure_ascii=False)
        await ui.run_javascript(f'''
            window.applyXFusionTheme && window.applyXFusionTheme({js_theme});
            window.applyXFusionShellTheme && window.applyXFusionShellTheme({js_payload});
            window.applyXFusionDomTheme && window.applyXFusionDomTheme({str(new_is_dark).lower()});
        ''')

        from app.ui.pages import content_router
        if content_router.content_container:
            content_router.content_container.style(f'background-color: {new_theme["content_bg"]};')

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

    current_theme = build_theme(bool(app.storage.user.get('is_dark', False)))

    with ui.left_drawer(value=True, fixed=True).classes(current_theme['drawer_classes']).props('width=360 bordered id=xf-drawer') as drawer:
        render_sidebar_content()

    with ui.header().classes(current_theme['header_classes']).props('id=xf-header'):
        with ui.row().classes('w-full items-center justify-between'):
            with ui.row().classes('items-center gap-2'):
                ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense id=xf-menu-btn').classes(current_theme['menu_btn_classes'])
                ui.label('X-Fusion-Pro').classes(current_theme['title_classes']).props('id=xf-title')

            with ui.row().classes('items-center gap-3 mr-2'):
                with ui.button(icon='gpp_bad', on_click=lambda: reset_global_session(None)).props('flat dense round size=sm id=xf-security-btn').classes(current_theme['security_btn_classes']).tooltip('安全重置'):
                    ui.badge('Reset', color='orange').props('floating rounded-sm').classes('text-[10px] font-black')

                with ui.button(icon='vpn_key', on_click=lambda: safe_copy_to_clipboard(AUTO_REGISTER_SECRET)).props('flat dense round size=sm id=xf-key-btn').classes(current_theme['key_btn_classes']).tooltip('复制通讯密钥'):
                    ui.badge('Key', color='red').props('floating rounded-sm').classes('text-[10px] font-black')

                ui.button(icon=current_theme['theme_icon'], on_click=toggle_theme).props('flat round dense id=xf-theme-btn').classes(current_theme['theme_btn_classes']).tooltip(current_theme['theme_tooltip'])
                ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat round dense id=xf-logout-btn').classes(current_theme['logout_btn_classes']).tooltip('退出登录')

    from app.ui.pages import content_router

    content_router.content_container = ui.column().classes('w-full h-full min-h-[calc(100vh-56px)] pl-4 pr-4 pt-4 overflow-y-auto').props('id=xf-content-container').style(f'background-color: {current_theme["content_bg"]};')
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
        last_page = app.storage.user.get('last_view_page', 1)
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
            await refresh_content(last_scope, target_data, page_num=last_page)
        logger.info(f'♻️ 自动恢复视图: {last_scope}')

    ui.timer(0.1, lambda: asyncio.create_task(restore_last_view()), once=True)
    logger.info('✅ UI 已就绪')
