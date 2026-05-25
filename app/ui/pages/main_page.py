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
            'body_bg': 'radial-gradient(circle at top, rgba(34,211,238,0.08), transparent 28%), linear-gradient(180deg, #050a14 0%, #030712 100%)' if is_dark else '#ffffff',
            'body_text': '#e2e8f0' if is_dark else '#0f172a',
            'card_bg': '#070b14' if is_dark else '#ffffff',
            'card_border': 'rgba(30,58,95,0.55)' if is_dark else 'rgba(148,163,184,0.35)',
            'drawer_bg': '#070b14' if is_dark else '#f8fbff',
            'scroll_track': '#030712' if is_dark else '#e2e8f0',
            'scroll_thumb': '#1e3a5f' if is_dark else '#94a3b8',
            'scroll_thumb_hover': '#2563eb' if is_dark else '#64748b',
            'content_bg': '#030712' if is_dark else '#ffffff',
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
            'popup_bg': 'rgba(7,11,20,0.6)' if is_dark else 'rgba(255,255,255,0.6)',
            'popup_border': 'rgba(30,58,95,0.70)' if is_dark else 'rgba(148,163,184,0.55)',
            'popup_text': '#e2e8f0' if is_dark else '#0f172a',
            'stat_servers_bg': 'linear-gradient(135deg, #0f172a 0%, #102a43 45%, #155e75 100%)' if is_dark else 'linear-gradient(135deg, #eff6ff 0%, #dbeafe 50%, #bfdbfe 100%)',
            'stat_servers_badge': '#38bdf8' if is_dark else '#0284c7',
            'stat_servers_icon_bg': 'rgba(14, 165, 233, 0.14)' if is_dark else 'rgba(255,255,255,0.72)',
            'stat_nodes_bg': 'linear-gradient(135deg, #1e1b4b 0%, #312e81 45%, #6d28d9 100%)' if is_dark else 'linear-gradient(135deg, #f5f3ff 0%, #ede9fe 55%, #ddd6fe 100%)',
            'stat_nodes_badge': '#a78bfa' if is_dark else '#7c3aed',
            'stat_nodes_icon_bg': 'rgba(139, 92, 246, 0.14)' if is_dark else 'rgba(255,255,255,0.76)',
            'stat_traffic_bg': 'linear-gradient(135deg, #052e2b 0%, #065f46 50%, #0f766e 100%)' if is_dark else 'linear-gradient(135deg, #ecfdf5 0%, #d1fae5 55%, #ccfbf1 100%)',
            'stat_traffic_badge': '#34d399' if is_dark else '#059669',
            'stat_traffic_icon_bg': 'rgba(16, 185, 129, 0.14)' if is_dark else 'rgba(255,255,255,0.76)',
            'stat_subs_bg': 'linear-gradient(135deg, #431407 0%, #9a3412 55%, #ea580c 100%)' if is_dark else 'linear-gradient(135deg, #fff7ed 0%, #ffedd5 55%, #fed7aa 100%)',
            'stat_subs_badge': '#fb923c' if is_dark else '#ea580c',
            'stat_subs_icon_bg': 'rgba(249, 115, 22, 0.14)' if is_dark else 'rgba(255,255,255,0.76)',
            'stat_title': 'rgba(226, 232, 240, 0.88)' if is_dark else '#334155',
            'stat_value': '#f8fafc' if is_dark else '#0f172a',
            'stat_sub': 'rgba(191, 219, 254, 0.82)' if is_dark else '#475569',
            'map_geo_area': '#172033' if is_dark else '#dbeafe',
            'map_geo_border': '#334155' if is_dark else '#94a3b8',
            'map_geo_emphasis': '#1e293b' if is_dark else '#bfdbfe',
            'map_highlight_area': '#2563eb' if is_dark else '#60a5fa',
            'map_highlight_border': '#22d3ee' if is_dark else '#0284c7',
            'map_scatter_shadow': 'rgba(15,23,42,0.9)' if is_dark else 'rgba(148,163,184,0.65)',
            'map_me': '#facc15' if is_dark else '#f59e0b',
            'map_line': '#22d3ee' if is_dark else '#38bdf8',
            
            # 高度设为 64px
            'header_classes': 'bg-gradient-to-r from-[#070e1a] to-[#0a1526] text-white h-16 border-b border-[#1e3a5f]/60 shadow-[0_4px_20px_rgba(0,0,0,0.6)]' if is_dark else 'bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] text-slate-900 h-16 border-b border-[#cbd5e1] shadow-[0_4px_16px_rgba(148,163,184,0.18)]',
            'drawer_classes': 'bg-[#070b14] border-r border-[#1e3a5f]/55' if is_dark else 'bg-[#f8fbff] border-r border-[#cbd5e1]/80',
            'menu_btn_classes': 'text-slate-300 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'text-slate-600 hover:text-blue-600 hover:bg-blue-100/80',
            
            # 🛠️ 扫光渐变背景色：中间是一个高亮耀眼的白色 #ffffff 光斑
            'title_grad': 'linear-gradient(110deg, #3b82f6 0%, #22d3ee 35%, #ffffff 50%, #a855f7 65%, #3b82f6 100%)' if is_dark else 'linear-gradient(110deg, #2563eb 0%, #0284c7 35%, #ffffff 50%, #4f46e5 65%, #2563eb 100%)',
            'title_shadow': 'drop-shadow(0 0 6px rgba(34,211,238,0.5))' if is_dark else 'drop-shadow(0 2px 4px rgba(2,132,199,0.25))',
            
            'security_btn_classes': 'w-10 h-10 min-w-[40px] min-h-[40px] shrink-0 flex items-center justify-center text-rose-400 hover:bg-rose-950/30 hover:text-rose-300' if is_dark else 'w-10 h-10 min-w-[40px] min-h-[40px] shrink-0 flex items-center justify-center text-rose-500 hover:bg-rose-100 hover:text-rose-600',
            'key_btn_classes': 'w-10 h-10 min-w-[40px] min-h-[40px] shrink-0 flex items-center justify-center text-slate-400 hover:bg-cyan-950/30 hover:text-cyan-300' if is_dark else 'w-10 h-10 min-w-[40px] min-h-[40px] shrink-0 flex items-center justify-center text-slate-500 hover:bg-sky-100 hover:text-sky-600',
            'theme_btn_classes': 'w-10 h-10 min-w-[40px] min-h-[40px] shrink-0 flex items-center justify-center text-amber-300 hover:bg-amber-950/30 hover:text-yellow-200' if is_dark else 'w-10 h-10 min-w-[40px] min-h-[40px] shrink-0 flex items-center justify-center text-slate-500 hover:bg-indigo-100 hover:text-indigo-600',
            'logout_btn_classes': 'w-10 h-10 min-w-[40px] min-h-[40px] shrink-0 flex items-center justify-center text-slate-400 hover:bg-slate-800/50 hover:text-cyan-300' if is_dark else 'w-10 h-10 min-w-[40px] min-h-[40px] shrink-0 flex items-center justify-center text-slate-500 hover:bg-slate-200 hover:text-slate-700',
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
        <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@700;900&display=swap" rel="stylesheet">
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
                    '--xf-popup-bg': theme.popup_bg,
                    '--xf-popup-border': theme.popup_border,
                    '--xf-popup-text': theme.popup_text,
                    '--xf-stat-servers-bg': theme.stat_servers_bg,
                    '--xf-stat-servers-badge': theme.stat_servers_badge,
                    '--xf-stat-servers-icon-bg': theme.stat_servers_icon_bg,
                    '--xf-stat-nodes-bg': theme.stat_nodes_bg,
                    '--xf-stat-nodes-badge': theme.stat_nodes_badge,
                    '--xf-stat-nodes-icon-bg': theme.stat_nodes_icon_bg,
                    '--xf-stat-traffic-bg': theme.stat_traffic_bg,
                    '--xf-stat-traffic-badge': theme.stat_traffic_badge,
                    '--xf-stat-traffic-icon-bg': theme.stat_traffic_icon_bg,
                    '--xf-stat-subs-bg': theme.stat_subs_bg,
                    '--xf-stat-subs-badge': theme.stat_subs_badge,
                    '--xf-stat-subs-icon-bg': theme.stat_subs_icon_bg,
                    '--xf-stat-title': theme.stat_title,
                    '--xf-stat-value': theme.stat_value,
                    '--xf-stat-sub': theme.stat_sub,
                    '--xf-map-geo-area': theme.map_geo_area,
                    '--xf-map-geo-border': theme.map_geo_border,
                    '--xf-map-geo-emphasis': theme.map_geo_emphasis,
                    '--xf-map-highlight-area': theme.map_highlight_area,
                    '--xf-map-highlight-border': theme.map_highlight_border,
                    '--xf-map-scatter-shadow': theme.map_scatter_shadow,
                    '--xf-map-me': theme.map_me,
                    '--xf-map-line': theme.map_line,
                    '--xf-title-grad': theme.title_grad,
                    '--xf-title-shadow': theme.title_shadow,
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
                --xf-popup-bg: {theme['popup_bg']};
                --xf-popup-border: {theme['popup_border']};
                --xf-popup-text: {theme['popup_text']};
                --xf-stat-servers-bg: {theme['stat_servers_bg']};
                --xf-stat-servers-badge: {theme['stat_servers_badge']};
                --xf-stat-servers-icon-bg: {theme['stat_servers_icon_bg']};
                --xf-stat-nodes-bg: {theme['stat_nodes_bg']};
                --xf-stat-nodes-badge: {theme['stat_nodes_badge']};
                --xf-stat-nodes-icon-bg: {theme['stat_nodes_icon_bg']};
                --xf-stat-traffic-bg: {theme['stat_traffic_bg']};
                --xf-stat-traffic-badge: {theme['stat_traffic_badge']};
                --xf-stat-traffic-icon-bg: {theme['stat_traffic_icon_bg']};
                --xf-stat-subs-bg: {theme['stat_subs_bg']};
                --xf-stat-subs-badge: {theme['stat_subs_badge']};
                --xf-stat-subs-icon-bg: {theme['stat_subs_icon_bg']};
                --xf-stat-title: {theme['stat_title']};
                --xf-stat-value: {theme['stat_value']};
                --xf-stat-sub: {theme['stat_sub']};
                --xf-map-geo-area: {theme['map_geo_area']};
                --xf-map-geo-border: {theme['map_geo_border']};
                --xf-map-geo-emphasis: {theme['map_geo_emphasis']};
                --xf-map-highlight-area: {theme['map_highlight_area']};
                --xf-map-highlight-border: {theme['map_highlight_border']};
                --xf-map-scatter-shadow: {theme['map_scatter_shadow']};
                --xf-map-me: {theme['map_me']};
                --xf-map-line: {theme['map_line']};
                --xf-title-grad: {theme['title_grad']};
                --xf-title-shadow: {theme['title_shadow']};
            }}
            
            /* 🛠️ 从左至右光效扫过动画：5秒为一个周期，其中大部分时间保持停滞，每隔几秒迅速扫过 */
            @keyframes sweep-shine {{
                0%, 75% {{ background-position: 120% center; }}  /* 光效停留在右侧视窗外 (隐藏状态) */
                90%, 100% {{ background-position: -20% center; }} /* 迅速从左往右扫到左侧视窗外 */
            }}
            
            @keyframes slow-spin {{
                100% {{ transform: rotate(360deg); }}
            }}
            .tech-spin {{
                animation: slow-spin 8s linear infinite;
            }}
            
            .xf-tech-title {{
                font-family: 'Orbitron', sans-serif;
                font-weight: 900;
                font-size: 1.45rem;
                letter-spacing: 0.08em;
                background: var(--xf-title-grad);
                background-size: 200% auto; /* 放大背景，制造局部高光 */
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                animation: sweep-shine 5s ease-in-out infinite; /* 5秒触发一次间歇扫光 */
                filter: var(--xf-title-shadow);
                line-height: 1;
            }}
            
            .xf-tech-logo {{
                font-size: 1.8rem;
                color: var(--xf-accent);
                filter: var(--xf-title-shadow);
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
            .q-card {{
                background-color: var(--xf-card-bg) !important;
                border: 1px solid var(--xf-card-border) !important;
                box-shadow: 0 10px 28px rgba(15, 23, 42, 0.10), 0 1px 0 rgba(255,255,255,0.06) inset !important;
                transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease !important;
            }}
            .q-card:hover {{
                transform: translateY(-1px);
                box-shadow: 0 16px 34px rgba(15, 23, 42, 0.13), 0 1px 0 rgba(255,255,255,0.08) inset !important;
            }}
            .q-drawer {{ background-color: var(--xf-drawer-bg) !important; }}
            .q-btn {{
                border-radius: 10px !important;
                position: relative;
                transform: translateY(0);
                box-shadow: 0 5px 14px rgba(15, 23, 42, 0.08), 0 1px 0 rgba(255,255,255,0.08) inset !important;
                transition: transform .14s ease, box-shadow .14s ease, filter .14s ease, border-color .14s ease, background-color .14s ease !important;
            }}
            .q-btn::before {{
                content: '';
                position: absolute;
                inset: 1px;
                border-radius: inherit;
                pointer-events: none;
                background: linear-gradient(180deg, rgba(255,255,255,0.11) 0%, rgba(255,255,255,0.04) 42%, rgba(255,255,255,0) 100%);
                opacity: 0.9;
            }}
            .q-btn:hover {{
                transform: translateY(-1px);
                box-shadow: 0 9px 20px rgba(15, 23, 42, 0.11), 0 1px 0 rgba(255,255,255,0.10) inset !important;
                filter: saturate(1.02);
            }}
            .q-btn:active, .q-btn.q-btn--active {{
                transform: translateY(1px) scale(0.996);
                box-shadow: 0 3px 8px rgba(15, 23, 42, 0.09), 0 1px 0 rgba(255,255,255,0.05) inset !important;
            }}
            .q-btn--round, .q-btn--fab, .q-btn--fab-mini,
            .q-btn--flat, .q-btn--outline {{
                box-shadow: 0 4px 12px rgba(15, 23, 42, 0.07), 0 1px 0 rgba(255,255,255,0.07) inset !important;
            }}
            .xf-btn-primary,
            .xf-btn-primary.q-btn {{
                box-shadow: 0 10px 24px color-mix(in srgb, var(--xf-accent) 20%, rgba(15, 23, 42, 0.14)), 0 1px 0 rgba(255,255,255,0.14) inset !important;
                border-color: color-mix(in srgb, var(--xf-accent) 32%, var(--xf-card-border)) !important;
            }}
            .xf-btn-primary:hover,
            .xf-btn-primary.q-btn:hover {{
                box-shadow: 0 14px 30px color-mix(in srgb, var(--xf-accent) 24%, rgba(15, 23, 42, 0.16)), 0 1px 0 rgba(255,255,255,0.18) inset !important;
                filter: saturate(1.06);
            }}
            .xf-btn-primary:active,
            .xf-btn-primary.q-btn:active {{
                box-shadow: 0 5px 12px color-mix(in srgb, var(--xf-accent) 18%, rgba(15, 23, 42, 0.10)), 0 1px 0 rgba(255,255,255,0.10) inset !important;
            }}
            .xf-btn-subtle,
            .xf-btn-subtle.q-btn {{
                box-shadow: 0 4px 12px rgba(15, 23, 42, 0.07), 0 1px 0 rgba(255,255,255,0.07) inset !important;
            }}
            .xf-btn-subtle:hover,
            .xf-btn-subtle.q-btn:hover {{
                box-shadow: 0 7px 16px rgba(15, 23, 42, 0.10), 0 1px 0 rgba(255,255,255,0.10) inset !important;
            }}
            .xf-icon-3d,
            .xf-icon-3d.q-btn,
            .xf-sidebar-icon-box {{
                border-radius: 12px !important;
                background: color-mix(in srgb, var(--xf-elevated-bg) 88%, white 12%) !important;
                border-color: color-mix(in srgb, var(--xf-accent) 16%, var(--xf-card-border)) !important;
                box-shadow: 0 6px 14px rgba(15, 23, 42, 0.08), 0 1px 0 rgba(255,255,255,0.10) inset !important;
                transition: transform .14s ease, box-shadow .14s ease, border-color .14s ease, background-color .14s ease !important;
            }}
            .xf-icon-3d:hover,
            .xf-icon-3d.q-btn:hover,
            .xf-sidebar-icon-box:hover {{
                transform: translateY(-1px);
                box-shadow: 0 10px 20px rgba(15, 23, 42, 0.11), 0 1px 0 rgba(255,255,255,0.14) inset !important;
            }}
            .xf-sidebar-icon-glyph {{
                filter: drop-shadow(0 1px 1px rgba(255,255,255,0.18));
            }}
            .xf-sidebar-section-text {{
                letter-spacing: .08em;
            }}
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
                min-height: calc(100vh - 64px) !important;
            }}
            .q-dialog__backdrop,
            .q-overlay,
            .q-popup__backdrop {{
                background: transparent !important;
                opacity: 1 !important;
                backdrop-filter: none !important;
            }}
            .q-dialog__inner,
            .q-dialog__inner > div {{
                background: transparent !important;
                background-color: transparent !important;
            }}
            .q-menu,
            .q-menu .q-list,
            .q-select__dialog,
            .q-virtual-scroll__content,
            .q-item {{
                background: var(--xf-popup-bg) !important;
                background-color: var(--xf-popup-bg) !important;
                color: var(--xf-popup-text) !important;
                backdrop-filter: blur(12px) !important;
            }}
            .q-menu,
            .q-select__dialog {{
                border: 1px solid var(--xf-popup-border) !important;
                box-shadow: 0 16px 34px rgba(15, 23, 42, 0.14), 0 1px 0 rgba(255,255,255,0.08) inset !important;
            }}
            .q-dialog .q-card,
            .q-menu,
            .q-select__dialog,
            .q-field__control {{
                transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease !important;
            }}
            .q-field__control {{
                border-radius: 12px !important;
                background: color-mix(in srgb, var(--xf-elevated-bg) 86%, white 14%) !important;
                box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08), 0 1px 0 rgba(255,255,255,0.08) inset !important;
            }}
            .q-field--focused .q-field__control,
            .q-field:hover .q-field__control {{
                transform: translateY(-1px);
                box-shadow: 0 8px 18px rgba(15, 23, 42, 0.11), 0 1px 0 rgba(255,255,255,0.10) inset !important;
            }}
            .q-item:hover,
            .q-manual-focusable--focused,
            .q-manual-focusable--focused > .q-item__section,
            .q-item.q-router-link--active,
            .q-item--active {{
                background: var(--xf-accent-soft) !important;
            }}
            .q-tooltip {{
                background: var(--xf-tooltip-bg) !important;
                color: var(--xf-tooltip-text) !important;
                border: 1px solid var(--xf-tooltip-border) !important;
                box-shadow: var(--xf-tooltip-shadow) !important;
            }}
            #xf-security-btn,
            #xf-key-btn,
            #xf-theme-btn,
            #xf-logout-btn {{
                width: 40px !important;
                height: 40px !important;
                min-width: 40px !important;
                min-height: 40px !important;
                padding: 0 !important;
                display: inline-flex !important;
                align-items: center !important;
                justify-content: center !important;
                flex: 0 0 40px !important;
                border-radius: 12px !important;
            }}
            #xf-security-btn .q-icon,
            #xf-key-btn .q-icon,
            #xf-theme-btn .q-icon,
            #xf-logout-btn .q-icon {{
                font-size: 20px !important;
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
            'security_btn_style': 'color: #fb7185;' if new_is_dark else 'color: #f43f5e;',
            'key_btn_style': 'color: #94a3b8;' if new_is_dark else 'color: #64748b;',
            'theme_btn_style': 'color: #fcd34d;' if new_is_dark else 'color: #64748b;',
            'logout_btn_style': 'color: #94a3b8;' if new_is_dark else 'color: #64748b;',
            'theme_icon': new_theme['theme_icon'],
            'content_bg': new_theme['content_bg'],
            'popup_bg': new_theme['popup_bg'],
            'popup_border': new_theme['popup_border'],
            'popup_text': new_theme['popup_text'],
        }
        js_payload = json.dumps(payload, ensure_ascii=False)
        js_theme = json.dumps(new_theme, ensure_ascii=False)
        await ui.run_javascript(f'''
            window.applyXFusionTheme && window.applyXFusionTheme({js_theme});
            window.applyXFusionShellTheme && window.applyXFusionShellTheme({js_payload});
            window.applyXFusionDomTheme && window.applyXFusionDomTheme({str(new_is_dark).lower()});
            window.dispatchEvent(new CustomEvent('xfusion-theme-change', {{ detail: {{ isDark: {str(new_is_dark).lower()} }} }}));
            window.applyDashboardTheme && window.applyDashboardTheme();
        ''')

        from app.ui.pages import content_router
        if content_router.content_container:
            content_router.content_container.style(f'background-color: {new_theme["content_bg"]};')

        from app.core.state import CURRENT_VIEW_STATE
        current_scope = CURRENT_VIEW_STATE.get('scope')
        if current_scope == 'DASHBOARD':
            from app.ui.components.dashboard import refresh_dashboard_ui
            await refresh_dashboard_ui()
            await ui.run_javascript('setTimeout(() => { window.applyDashboardTheme && window.applyDashboardTheme(); }, 80)')
        elif current_scope == 'PROBE':
            from app.ui.pages.probe_page import render_probe_page
            await render_probe_page()
        elif current_scope == 'SUBS':
            from app.ui.pages.subs_page import load_subs_view
            await load_subs_view()

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
            
            with ui.row().classes('items-center gap-2 cursor-default flex-nowrap pl-1'):
                ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense id=xf-menu-btn').classes(f"{current_theme['menu_btn_classes']} xf-icon-3d")
                
                # 🛠️ 炫酷科技风图标 (bubble_chart) 与流光扫掠文字
                with ui.row().classes('items-center gap-2 ml-1'):
                    ui.icon('bubble_chart').classes('xf-tech-logo tech-spin')
                    ui.label('X-FUSION').classes('xf-tech-title')
                    
                    ui.badge('PRO').classes(
                        'bg-gradient-to-r from-cyan-500 to-blue-600 text-white font-black border-none px-1.5 py-0.5 shadow-[0_0_8px_rgba(34,211,238,0.5)] tracking-widest text-[10px] transform -skew-x-12 mt-0.5'
                    )

            with ui.row().classes('items-center gap-3 mr-2 flex-nowrap'):
                with ui.button(icon='gpp_bad', on_click=lambda: reset_global_session(None)).props('flat round id=xf-security-btn').classes(f"{current_theme['security_btn_classes']} xf-icon-3d").tooltip('安全重置'):
                    ui.badge('Reset', color='orange').props('floating rounded-sm').classes('text-[10px] font-black')

                with ui.button(icon='vpn_key', on_click=lambda: safe_copy_to_clipboard(AUTO_REGISTER_SECRET)).props('flat round id=xf-key-btn').classes(f"{current_theme['key_btn_classes']} xf-icon-3d").tooltip('复制通讯密钥'):
                    ui.badge('Key', color='red').props('floating rounded-sm').classes('text-[10px] font-black')

                ui.button(icon=current_theme['theme_icon'], on_click=toggle_theme).props('flat round id=xf-theme-btn').classes(f"{current_theme['theme_btn_classes']} xf-icon-3d").tooltip(current_theme['theme_tooltip'])
                ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat round id=xf-logout-btn').classes(f"{current_theme['logout_btn_classes']} xf-icon-3d").tooltip('退出登录')

    from app.ui.pages import content_router

    content_router.content_container = ui.column().classes('w-full h-full min-h-[calc(100vh-64px)] pl-4 pr-4 pt-4 overflow-y-auto').props('id=xf-content-container').style(f'background-color: {current_theme["content_bg"]};')
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

            if need_save:
                await save_admin_config()
        except:
            pass

    ui.timer(1.0, auto_init_system_settings, once=True)

    try:
        page_client = ui.context.client
    except:
        page_client = None

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
        elif last_scope == 'PROBE' and ADMIN_CONFIG.get('probe_enabled', True):
            logger.info("[MainPage] restore_last_view branch=PROBE")
            await render_probe_page()
        elif last_scope == 'SUBS':
            logger.info("[MainPage] restore_last_view branch=SUBS")
            await load_subs_view()
        else:
            logger.info(f"[MainPage] restore_last_view branch={last_scope} target_data={target_data} client_present={page_client is not None}")
            await refresh_content(last_scope, target_data, page_num=last_page, manual_client=page_client)
        logger.info(f'♻️ 自动恢复视图: {last_scope}')

    ui.timer(0.1, lambda: asyncio.create_task(restore_last_view()), once=True)
    logger.info('✅ UI 已就绪')
