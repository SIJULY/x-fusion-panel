import asyncio

from nicegui import app, ui

from app.core.state import DNS_CACHE, DNS_WAITING_LABELS
from app.ui.common.notifications import safe_copy_to_clipboard, safe_notify
from app.utils.encoding import generate_detail_config, generate_node_link
from app.utils.formatters import format_bytes
from app.utils.geo import detect_country_group
from app.utils.network import get_real_ip_display


def bind_ip_label(url, label):
    """
    ✨ 新增辅助函数：将 UI Label 绑定到 DNS 监听列表
    用法：在创建 ui.label 后调用 bind_ip_label(url, label)
    """
    try:
        host = url.split('://')[-1].split(':')[0]
        import re
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host):
            return
        if host in DNS_CACHE:
            return

        if host not in DNS_WAITING_LABELS:
            DNS_WAITING_LABELS[host] = []
        DNS_WAITING_LABELS[host].append(label)
    except:
        pass


def show_custom_node_info(node):
    is_dark = bool(app.storage.user.get('is_dark', True))
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm p-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-full max-w-sm p-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
        with ui.row().classes('w-full justify-between items-center p-4 border-b border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'w-full justify-between items-center p-4 border-b border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]'):
            ui.label(node.get('remark', '节点详情')).classes('text-lg font-black text-slate-100 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide')
            ui.button(icon='close', on_click=d.close).props('flat round dense').classes('text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100')

        link = node.get('_raw_link') or node.get('link') or "无法获取链接"

        with ui.column().classes('w-full p-4 gap-4 bg-[#030712]' if is_dark else 'w-full p-4 gap-4 bg-[#f8fbff]'):
            with ui.row().classes('w-full bg-black p-3 rounded-sm break-all font-mono text-xs border border-[#1e3a5f]/45 text-emerald-400' if is_dark else 'w-full bg-sky-50 p-3 rounded-sm break-all font-mono text-xs border border-slate-300/90 text-slate-700'):
                ui.label(link)

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('复制', icon='content_copy', on_click=lambda: [safe_copy_to_clipboard(link), d.close()]).props('flat').classes('bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm px-4' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 font-black rounded-sm px-4')
                ui.button('关闭', on_click=d.close).props('outline color=grey').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm')
    d.open()


def _apply_tooltip(target, text, is_dark):
    tip = target.tooltip(text)
    tip.classes('text-[11px] font-bold px-2 py-1 rounded-sm')
    tip.style('background:var(--xf-tooltip-bg);color:var(--xf-tooltip-text);border:1px solid var(--xf-tooltip-border);box-shadow:var(--xf-tooltip-shadow);')
    return tip


def draw_row(srv, node, css_style, use_special_mode, is_first=True):
    parent_client = None
    try:
        parent_client = ui.context.client
    except:
        pass

    is_dark = bool(app.storage.user.get('is_dark', True))
    card_cls = 'grid w-full gap-4 py-3 px-4 items-center group relative rounded-sm border border-b-[3px] transition-all duration-150 ease-out hover:-translate-y-[1px] mb-2'
    card_style = f'{css_style} background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 6px 18px rgba(15,23,42,0.10);'

    with ui.element('div').classes(card_cls).style(card_style):
        srv_name = srv.get('name', '未命名')
        if not is_first:
            ui.label(srv_name).classes('text-xs truncate w-full text-left pl-2 font-mono').style('color: var(--xf-text-muted);')
        else:
            ui.label(srv_name).classes('text-xs font-black truncate w-full text-left pl-2 font-mono').style('color: var(--xf-text-muted);')

        if not node:
            is_probe = srv.get('probe_installed', False)
            msg = '同步中...' if not is_probe else '无节点配置'
            ui.label(msg).classes('font-black truncate text-xs italic').style('color: var(--xf-text-muted);')
            ui.label('--').classes('text-center').style('color: var(--xf-text-muted);')
            ui.label('--').classes('text-center').style('color: var(--xf-text-muted);')
            ui.label('UNK').classes('text-center font-black text-[10px]').style('color: var(--xf-text-muted);')
            ui.label('--').classes('text-center').style('color: var(--xf-text-muted);')
            if not use_special_mode:
                ui.element('div')
            with ui.row().classes('gap-1 justify-center w-full no-wrap'):
                ssh_btn = ui.button(icon='terminal', on_click=lambda _, s=srv, c=parent_client: asyncio.create_task(_open_single_ssh(s, c))).props('flat dense size=sm round').classes('text-slate-500').style('color: var(--xf-text-muted);')
                _apply_tooltip(ssh_btn, '进入 SSH 终端', is_dark)
                settings_btn = ui.button(icon='settings', on_click=lambda _, s=srv, c=parent_client: asyncio.create_task(_refresh_single_server(s, c))).props('flat dense size=sm round').classes('text-slate-500').style('color: var(--xf-text-muted);')
                _apply_tooltip(settings_btn, '管理服务器', is_dark)
            return

        remark = node.get('ps') or node.get('remark') or '未命名节点'
        ui.label(remark).classes('font-black truncate w-full text-left pl-2 text-sm').style('color: var(--xf-text-strong);')

        if use_special_mode:
            with ui.row().classes('w-full justify-center items-center gap-1.5 no-wrap'):
                is_online = srv.get('_status') == 'online'
                color = 'text-green-500' if is_online else 'text-red-500'
                if not srv.get('probe_installed') and not node.get('_is_custom'):
                    color = 'text-orange-400'
                ui.icon('bolt').classes(f'{color} text-sm')
                display_ip = get_real_ip_display(srv['url'])
                ip_lbl = ui.label(display_ip).classes('text-[10px] font-mono font-black px-1.5 py-0.5 rounded-sm select-all border').style('color: var(--xf-accent); background: var(--xf-code-bg); border-color: var(--xf-card-border);')
                bind_ip_label(srv['url'], ip_lbl)
        else:
            group_display = srv.get('group', '默认分组')
            if group_display in ['默认分组', '自动注册', '未分组', '自动导入']:
                try:
                    detected = detect_country_group(srv.get('name', ''), None)
                    if detected:
                        group_display = detected
                except:
                    pass
            ui.label(group_display).classes('text-xs font-black w-full text-center truncate px-2 py-0.5 rounded-sm border').style('color: var(--xf-text-strong); background: var(--xf-code-bg); border-color: var(--xf-card-border);')

        if node.get('_is_custom'):
            ui.label('-').classes('text-xs w-full text-center font-mono').style('color: var(--xf-text-muted);')
        else:
            traffic = sum([node.get('up', 0), node.get('down', 0)])
            ui.label(format_bytes(traffic)).classes('text-xs w-full text-center font-mono font-black').style('color: var(--xf-accent);')

        proto = str(node.get('protocol', 'unk')).upper()
        if 'HYSTERIA' in proto:
            proto = 'HY2'
        if 'SHADOWSOCKS' in proto:
            proto = 'SS'
        proto_color = 'text-slate-500'
        if 'HY2' in proto:
            proto_color = 'text-purple-400'
        elif 'VLESS' in proto:
            proto_color = 'text-blue-400'
        elif 'VMESS' in proto:
            proto_color = 'text-green-400'
        elif 'TROJAN' in proto:
            proto_color = 'text-orange-400'
        ui.label(proto).classes(f'text-[11px] font-black w-full text-center {proto_color} tracking-wide')

        port_val = str(node.get('port', 0))
        ui.label(port_val).classes('font-mono w-full text-center font-black text-xs').style('color: var(--xf-text-muted);')

        if not use_special_mode:
            with ui.element('div').classes('flex justify-center w-full'):
                is_enable = node.get('enable', True)
                dot_cls = "bg-green-500 shadow-[0_0_6px_rgba(34,197,94,0.6)]" if is_enable else "bg-red-500 shadow-[0_0_6px_rgba(239,68,68,0.6)]"
                ui.element('div').classes(f'w-2 h-2 rounded-full {dot_cls}')

        with ui.row().classes('gap-1 justify-center w-full no-wrap'):
            async def copy_link(n=node, s=srv):
                link = n.get('_raw_link') or n.get('link')
                if not link:
                    cf_domain = s.get('cf_primary_domain')
                    host = cf_domain.strip() if cf_domain else s['url'].split('://')[-1].split(':')[0]
                    link = generate_node_link(n, host)
                await safe_copy_to_clipboard(link)

            copy_btn = ui.button(icon='content_copy', on_click=copy_link).props('flat dense size=sm round').classes('text-slate-500').style('color: var(--xf-text-muted);')
            _apply_tooltip(copy_btn, '复制链接', is_dark)

            async def copy_detail():
                cf_domain = srv.get('cf_primary_domain')
                host = cf_domain.strip() if cf_domain else srv['url'].split('://')[-1].split(':')[0]
                text = generate_detail_config(node, host)
                if text:
                    await safe_copy_to_clipboard(text)
                else:
                    ui.notify('不支持生成配置', type='warning')

            detail_btn = ui.button(icon='description', on_click=copy_detail).props('flat dense size=sm round').classes('text-slate-500').style('color: var(--xf-text-muted);')
            _apply_tooltip(detail_btn, '复制明文配置', is_dark)
            ssh_btn = ui.button(icon='terminal', on_click=lambda _, s=srv, c=parent_client: asyncio.create_task(_open_single_ssh(s, c))).props('flat dense size=sm round').classes('text-slate-500').style('color: var(--xf-text-muted);')
            _apply_tooltip(ssh_btn, '进入 SSH 终端', is_dark)
            settings_btn = ui.button(icon='settings', on_click=lambda _, s=srv, c=parent_client: asyncio.create_task(_refresh_single_server(s, c))).props('flat dense size=sm round').classes('text-slate-500').style('color: var(--xf-text-muted);')
            _apply_tooltip(settings_btn, '管理服务器', is_dark)


async def _open_single_ssh(server, client=None):
    from app.core.state import SERVERS_CACHE
    from app.ui.pages.content_router import refresh_content

    target = server
    try:
        server_url = server.get('url') if isinstance(server, dict) else None
        if server_url:
            target = next((s for s in SERVERS_CACHE if s.get('url') == server_url), server)
    except:
        pass

    if not isinstance(target, dict) or not target.get('ssh_host'):
        safe_notify('当前服务器未配置 SSH 主机，无法打开终端', 'warning')
        return

    await refresh_content('SSH_SINGLE', target, manual_client=client)


async def _refresh_single_server(server, client=None):
    from app.core.state import SERVERS_CACHE
    from app.ui.pages.content_router import refresh_content

    target = server
    try:
        server_url = server.get('url') if isinstance(server, dict) else None
        if server_url:
            target = next((s for s in SERVERS_CACHE if s.get('url') == server_url), server)
    except:
        pass

    await refresh_content('SINGLE', target, manual_client=client)
