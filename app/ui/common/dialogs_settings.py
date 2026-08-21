import csv
import io
import socket
from datetime import datetime
from urllib.parse import urlparse

from nicegui import app, run, ui


def _settings_theme():
    is_dark = bool(app.storage.user.get('is_dark', True))
    return {
        'is_dark': is_dark,
        'card': 'w-[500px] max-w-[92vw] p-0 gap-0 flex flex-col overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-[500px] max-w-[92vw] p-0 gap-0 flex flex-col overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]',
        'wide_card': 'w-full max-w-2xl p-0 gap-0 flex flex-col overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-full max-w-2xl p-0 gap-0 flex flex-col overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]',
        'header': 'w-full p-5 gap-3 bg-gradient-to-r from-[#0a1526] to-[#050a14] border-b border-[#1e3a5f]/60' if is_dark else 'w-full p-5 gap-3 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] border-b border-slate-300/90',
        'header_row': 'justify-between items-center w-full px-5 py-4 border-b border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'justify-between items-center w-full px-5 py-4 border-b border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]',
        'icon_box': 'w-9 h-9 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] shadow-[0_0_8px_rgba(0,0,0,0.7)] relative overflow-hidden' if is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] relative overflow-hidden',
        'title': 'text-lg font-black text-slate-100 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide',
        'sub': 'text-xs text-slate-400' if is_dark else 'text-xs text-slate-500',
        'body': 'w-full p-5 gap-4 bg-[#030712]' if is_dark else 'w-full p-5 gap-4 bg-[#f8fbff]',
        'scroll': 'w-full h-[60vh] pr-4 bg-[#030712]' if is_dark else 'w-full h-[60vh] pr-4 bg-[#f8fbff]',
        'input_props': 'outlined dense dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense color=blue',
        'password_props': 'outlined dense type=password dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense type=password color=blue',
        'footer': 'w-full justify-end mt-4 p-4 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'w-full justify-end mt-4 p-4 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]',
        'footer_full': 'w-full p-4 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'w-full p-4 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]',
        'cancel': 'text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm',
        'save': 'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm px-5' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 font-black rounded-sm px-5',
        'save_full': 'w-full bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 shadow-[0_0_12px_rgba(34,211,238,0.22)] h-12 font-black rounded-sm' if is_dark else 'w-full bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 shadow-[0_6px_16px_rgba(56,189,248,0.16)] h-12 font-black rounded-sm',
    }

from app.core.state import ADMIN_CONFIG, SERVERS_CACHE
from app.services.cloudflare import CloudflareHandler
from app.storage.repositories import save_admin_config
from app.ui.common.notifications import safe_notify


def _extract_server_ip(server_conf):
    url = str(server_conf.get('url', '') or '').strip()
    if url:
        try:
            parsed = urlparse(url if '://' in url else f'http://{url}')
            host = parsed.hostname
            if host:
                return socket.gethostbyname(host)
        except Exception:
            pass

    ssh_host = str(server_conf.get('ssh_host', '') or '').strip()
    if ssh_host:
        try:
            return socket.gethostbyname(ssh_host)
        except Exception:
            return ssh_host

    return ''


def open_cloudflare_settings_dialog():
    theme = _settings_theme()
    with ui.dialog() as d, ui.card().classes(theme['card']):
        with ui.column().classes(theme['header']):
            with ui.row().classes('items-center gap-3 text-cyan-400' if theme['is_dark'] else 'items-center gap-3 text-sky-600'):
                with ui.element('div').classes(theme['icon_box']):
                    ui.element('div').classes('absolute inset-0 bg-cyan-400/10' if theme['is_dark'] else 'absolute inset-0 bg-sky-400/10')
                    ui.icon('cloud').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                ui.label('Cloudflare API 配置').classes(theme['title'])
            ui.label('用于自动解析域名、开启 CDN 和设置 SSL (Flexible)。').classes(theme['sub'])

        with ui.column().classes(theme['body']):
            cf_token = ui.input('API Token', value=ADMIN_CONFIG.get('cf_api_token', '')).props(theme['password_props']).classes('w-full')
            ui.label('权限要求: Zone.DNS (Edit), Zone.Settings (Edit)').classes('text-[10px] text-slate-500 ml-1')

            with ui.row().classes('w-full items-end gap-2'):
                cf_domain_root = ui.select([], label='根域名').props(theme['input_props']).classes('flex-1')
                ui.button('刷新域名', icon='refresh', on_click=lambda: refresh_zones(True)).props('flat').classes(theme['save'])

            async def refresh_zones(show_notify=False):
                token_val = cf_token.value.strip()
                saved_root = ADMIN_CONFIG.get('cf_root_domain', '').strip()
                current_value = str(cf_domain_root.value or '').strip() or saved_root
                if not token_val:
                    cf_domain_root.options = [saved_root] if saved_root else []
                    cf_domain_root.value = saved_root or None
                    if show_notify:
                        safe_notify('请先输入 Cloudflare API Token', 'warning')
                    return

                handler = CloudflareHandler()
                handler.token = token_val
                ok, result = await handler.list_zones()
                if ok:
                    zones = [item.get('name', '') for item in (result or []) if item.get('name')]
                    cf_domain_root.options = zones
                    if current_value in zones:
                        cf_domain_root.value = current_value
                    elif saved_root in zones:
                        cf_domain_root.value = saved_root
                    elif zones:
                        cf_domain_root.value = zones[0]
                    else:
                        cf_domain_root.value = None
                    if show_notify:
                        safe_notify(f'已获取 {len(zones)} 个 Cloudflare 域名', 'positive')
                else:
                    fallback = [saved_root] if saved_root else []
                    cf_domain_root.options = fallback
                    cf_domain_root.value = saved_root or None
                    if show_notify:
                        safe_notify(str(result), 'warning')

            async def export_cloudflare_data():
                token_val = cf_token.value.strip()
                if not token_val:
                    safe_notify('请先输入 Cloudflare API Token', 'warning')
                    return

                export_btn.props('loading')
                try:
                    handler = CloudflareHandler()
                    handler.token = token_val
                    handler.root_domain = str(cf_domain_root.value or ADMIN_CONFIG.get('cf_root_domain', '')).strip()
                    ok, result = await handler.list_all_a_records()
                    if not ok:
                        safe_notify(str(result), 'warning')
                        return

                    domains_by_ip = {}
                    for rec in result or []:
                        ip = str(rec.get('content', '') or '').strip()
                        domain = str(rec.get('name', '') or '').strip()
                        if ip and domain:
                            domains_by_ip.setdefault(ip, []).append(domain)

                    def build_csv():
                        output = io.StringIO()
                        writer = csv.writer(output)
                        writer.writerow(['服务器名称', '服务器IP', '服务器IP对应的域名解析'])

                        for server in SERVERS_CACHE:
                            if not isinstance(server, dict):
                                continue
                            server_name = str(server.get('name', '') or '').strip()
                            server_ip = _extract_server_ip(server)
                            domains = domains_by_ip.get(server_ip, [])
                            writer.writerow([server_name, server_ip, '\n'.join(domains)])

                        return output.getvalue()

                    csv_content = await run.io_bound(build_csv)

                    filename = f"cloudflare_server_dns_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                    ui.download(csv_content.encode('utf-8-sig'), filename)
                    safe_notify(f'已导出 {len(SERVERS_CACHE)} 台服务器数据', 'positive')
                except Exception as e:
                    safe_notify(f'导出失败: {e}', 'negative')
                finally:
                    export_btn.props(remove='loading')


        async def save_cf():
            ADMIN_CONFIG['cf_api_token'] = cf_token.value.strip()
            ADMIN_CONFIG['cf_root_domain'] = str(cf_domain_root.value or '').strip()
            await save_admin_config()
            safe_notify('✅ Cloudflare 配置已保存', 'positive')
            d.close()

        with ui.row().classes(theme['footer']):
            export_btn = ui.button('导出数据', icon='download', on_click=export_cloudflare_data).props('outline color=cyan').classes(theme['cancel'])
            ui.button('取消', on_click=d.close).props('outline color=grey').classes(theme['cancel'])
            ui.button('保存配置', on_click=save_cf).props('flat').classes(theme['save'])

        ui.timer(0.1, lambda: refresh_zones(False), once=True)
    d.open()


def open_probe_settings_dialog():
    theme = _settings_theme()
    with ui.dialog() as d, ui.card().classes(theme['wide_card']):
        with ui.row().classes(theme['header_row']):
            with ui.row().classes('items-center gap-3'):
                with ui.element('div').classes(theme['icon_box'] + (' text-cyan-400' if theme['is_dark'] else ' text-sky-600')):
                    ui.element('div').classes('absolute inset-0 bg-cyan-400/10' if theme['is_dark'] else 'absolute inset-0 bg-sky-400/10')
                    ui.icon('tune').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                ui.label('探针与监控设置').classes(theme['title'])
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes('text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if theme['is_dark'] else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100')

        with ui.scroll_area().classes(theme['scroll']):
            with ui.column().classes('w-full gap-6'):
                with ui.column().classes('w-full bg-cyan-950/15 p-4 rounded-sm border border-cyan-500/25' if theme['is_dark'] else 'w-full bg-sky-50 p-4 rounded-sm border border-sky-200'):
                    ui.label('📡 主控端外部地址 (Agent连接地址)').classes('text-sm font-black text-cyan-300' if theme['is_dark'] else 'text-sm font-black text-sky-700')
                    ui.label('Agent 将向此地址推送数据。请填写 http://公网IP:端口 或 https://域名').classes('text-xs text-cyan-500/80 mb-2' if theme['is_dark'] else 'text-xs text-sky-700/80 mb-2')
                    default_url = ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')
                    url_input = ui.input(value=default_url, placeholder='http://1.2.3.4:8080').classes('w-full').props(theme['input_props'])

                with ui.column().classes('w-full'):
                    ui.label('🚀 三网延迟测速目标 (Ping)').classes('text-sm font-black text-slate-200' if theme['is_dark'] else 'text-sm font-black text-slate-800')
                    ui.label('修改后需点击“更新探针”才能在服务器上生效。').classes('text-xs text-slate-500 mb-2')

                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-3 gap-3'):
                        ping_ct = ui.input('电信目标 IP', value=ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')).props(theme['input_props'])
                        ping_cu = ui.input('联通目标 IP', value=ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')).props(theme['input_props'])
                        ping_cm = ui.input('移动目标 IP', value=ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')).props(theme['input_props'])

                with ui.column().classes('w-full'):
                    ui.label('🤖 Telegram 通知 ').classes('text-sm font-black text-slate-200' if theme['is_dark'] else 'text-sm font-black text-slate-800')
                    ui.label('用于掉线报警等通知 (当前版本尚未实装)').classes('text-xs text-slate-500 mb-2')

                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-3'):
                        tg_token = ui.input('Bot Token', value=ADMIN_CONFIG.get('tg_bot_token', '')).props(theme['input_props'])
                        tg_id = ui.input('Chat ID', value=ADMIN_CONFIG.get('tg_chat_id', '')).props(theme['input_props'])

        async def save_settings():
            url_val = url_input.value.strip().rstrip('/')
            if url_val:
                ADMIN_CONFIG['manager_base_url'] = url_val

            ADMIN_CONFIG['ping_target_ct'] = ping_ct.value.strip()
            ADMIN_CONFIG['ping_target_cu'] = ping_cu.value.strip()
            ADMIN_CONFIG['ping_target_cm'] = ping_cm.value.strip()

            ADMIN_CONFIG['tg_bot_token'] = tg_token.value.strip()
            ADMIN_CONFIG['tg_chat_id'] = tg_id.value.strip()

            await save_admin_config()
            safe_notify('✅ 设置已保存 (请记得重新安装/更新探针以应用新配置)', 'positive')
            d.close()

        with ui.row().classes(theme['footer_full']):
            ui.button('保存设置', icon='save', on_click=save_settings).props('flat').classes(theme['save_full'])
    d.open()
