import asyncio
import uuid

from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app, ui

from app.core.state import ADMIN_CONFIG, PROBE_DATA_CACHE, SERVERS_CACHE
from app.services.ssh import WebSSH, get_ssh_client
from app.storage.repositories import save_admin_config
from app.ui.pages.login_page import check_auth

MOBILE_UI_VERSION = 'ServerCat Mobile v3.7'


def _server_host(server: dict) -> str:
    if server.get('ssh_host'):
        return str(server.get('ssh_host') or '').strip()
    raw_url = str(server.get('url') or '').strip()
    return raw_url.split('://')[-1].split('/')[0].split(':')[0] if raw_url else ''


def _ssh_servers() -> list[dict]:
    return [server for server in SERVERS_CACHE if _server_host(server)]


def _probe_data(server: dict) -> dict:
    return PROBE_DATA_CACHE.get(server.get('url') or '') or {}


def _fmt_speed(value) -> str:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return '--'
    if n <= 0:
        return '0 B'
    units = ['B', 'K', 'M', 'G']
    idx = 0
    while n >= 1024 and idx < len(units) - 1:
        n /= 1024
        idx += 1
    return f'{n:.0f} {units[idx]}' if n >= 10 else f'{n:.1f} {units[idx]}'


def _fmt_percent(value) -> str:
    try:
        return f'{float(value):.0f}%'
    except (TypeError, ValueError):
        return '--'


def _ring_style(percent, color='#22c55e') -> str:
    try:
        deg = max(0, min(100, float(percent))) * 3.6
    except (TypeError, ValueError):
        deg = 0
    return f'background:conic-gradient({color} 0 {deg:.0f}deg,#e5e7eb {deg:.0f}deg 360deg);'


def _quick_commands() -> list[dict]:
    saved = ADMIN_CONFIG.get('quick_commands') or []
    defaults = [
        {'name': '负载', 'cmd': 'uptime'},
        {'name': '磁盘', 'cmd': 'df -h'},
        {'name': '内存', 'cmd': 'free -h'},
        {'name': '进程', 'cmd': 'top'},
        {'name': '网络', 'cmd': 'ss -tulnp'},
        {'name': '系统', 'cmd': 'uname -a'},
    ]
    commands = []
    for item in saved if isinstance(saved, list) else []:
        name, cmd = str(item.get('name') or '').strip(), str(item.get('cmd') or '').strip()
        if name and cmd:
            item['name'] = name
            item['cmd'] = cmd
            item.setdefault('id', str(uuid.uuid4())[:8])
            commands.append(item)
    return commands or defaults


def _auth_label(value) -> str:
    text = str(value or '全局密钥').strip()
    if text in {'全局密钥', '密钥认证', 'key', 'private_key'} or '密钥' in text:
        return '密钥'
    if text in {'password', '密码认证'} or '密码' in text:
        return '密码'
    return text


def mobile_page(request: Request):
    """ServerCat 风格手机端 SSH 管理入口：只暴露账号列表与 SSH 终端能力。"""
    if not check_auth(request):
        return RedirectResponse('/login?next=/m')

    app.storage.user['is_dark'] = False
    ui.dark_mode().disable()
    ui.colors(primary='#ef4444', positive='#22c55e', negative='#ef4444')
    ui.add_head_html('''
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, maximum-scale=1" />
        <link rel="stylesheet" href="/static/xterm.css?v=servercat-mobile-v37" />
        <script src="/static/xterm.js?v=servercat-mobile-v37"></script>
        <script src="/static/xterm-addon-fit.js?v=servercat-mobile-v37"></script>
        <style>
            body{margin:0;background:#f4f4f8!important;color:#111827;overscroll-behavior:none}.nicegui-content{padding:0!important}.q-page-container,.q-page,.q-layout{background:transparent!important}
            .xfm-page{padding:max(14px,env(safe-area-inset-top)) 14px max(86px,env(safe-area-inset-bottom))}.xfm-title{font-size:28px;line-height:1.1;font-weight:850;letter-spacing:-.6px;color:#111827}.xfm-pill{border-radius:999px;background:rgba(255,255,255,.94);box-shadow:0 8px 22px rgba(17,24,39,.08)}
            .xfm-search .q-field__control{height:46px!important;border-radius:18px!important;background:#e9e9ee!important;box-shadow:none!important}.xfm-card{width:100%;border-radius:16px!important;background:rgba(255,255,255,.96)!important;border:1px solid rgba(229,231,235,.9)!important;box-shadow:0 8px 20px rgba(17,24,39,.06)!important}.xfm-chip{border-radius:999px;border:1px solid #e5e7eb;background:#f8fafc;color:#6b7280;padding:4px 8px;font-size:11px;font-weight:700;line-height:1}
            .xfm-dot{width:9px;height:9px;border-radius:999px;background:#ef4444;box-shadow:0 0 0 3px rgba(239,68,68,.14);animation:xfm-pulse-red 1.8s ease-in-out infinite}.xfm-dot-online{background:#22c55e;box-shadow:0 0 0 3px rgba(34,197,94,.14);animation:xfm-pulse-green 1.8s ease-in-out infinite}.xfm-dot-offline{background:#ef4444;box-shadow:0 0 0 3px rgba(239,68,68,.14);animation:xfm-pulse-red 1.8s ease-in-out infinite}@keyframes xfm-pulse-green{0%,100%{box-shadow:0 0 0 3px rgba(34,197,94,.14);transform:scale(1)}50%{box-shadow:0 0 0 8px rgba(34,197,94,0);transform:scale(1.12)}}@keyframes xfm-pulse-red{0%,100%{box-shadow:0 0 0 3px rgba(239,68,68,.14);transform:scale(1)}50%{box-shadow:0 0 0 8px rgba(239,68,68,0);transform:scale(1.12)}}.xfm-red-icon{color:#ef4444;font-size:20px}.xfm-ring{width:58px;height:58px;border-radius:999px;background:conic-gradient(#e5e7eb 0 360deg);display:flex;align-items:center;justify-content:center}.xfm-ring-inner{width:44px;height:44px;border-radius:999px;background:#f4f4f8;display:flex;align-items:center;justify-content:center;color:#4b5563;font-size:12px;font-weight:800}
            .xfm-bottom{position:fixed;left:34px;right:34px;bottom:max(12px,env(safe-area-inset-bottom));height:64px;border-radius:32px;background:rgba(255,255,255,.9);box-shadow:0 12px 30px rgba(17,24,39,.12);backdrop-filter:blur(18px);z-index:50}.xfm-nav-active{background:rgba(239,68,68,.08);color:#ef4444;border-radius:26px}.xfm-terminal-page{position:fixed;inset:0;background:#fff;padding:max(14px,env(safe-area-inset-top)) 12px max(84px,env(safe-area-inset-bottom));z-index:40}.xfm-terminal-pill{height:50px;border-radius:25px;background:rgba(255,255,255,.92);box-shadow:0 10px 28px rgba(17,24,39,.08);backdrop-filter:blur(18px)}.xfm-terminal-wrap{height:calc(100dvh - 174px);min-height:320px;background:#050505;color:#fff}.xfm-terminal-wrap .xterm,.xfm-terminal-wrap .xterm-viewport,.xfm-terminal-wrap .xterm-screen{background:#050505!important}.xfm-keybar{height:58px;border-radius:29px;background:rgba(255,255,255,.94);box-shadow:0 14px 34px rgba(17,24,39,.12);backdrop-filter:blur(18px);z-index:60}.xfm-key{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:900;font-size:13px;color:#111827}.xfm-terminal-status{font-size:11px;color:#9ca3af;font-weight:800;text-align:center;min-height:16px}
        </style>
    ''')

    state = {'terminal': None, 'list_keyword': '', 'list_refreshing': False, 'view': 'status'}
    with ui.column().classes('xfm-page w-full min-h-[100dvh] gap-4'):
        content = ui.column().classes('w-full gap-4')
    bottom_nav = ui.row().classes('xfm-bottom items-center justify-around px-3')

    def close_terminal():
        if state.get('terminal'):
            try:
                state['terminal'].close()
            except Exception:
                pass
        state['terminal'] = None

    def logout():
        close_terminal()
        app.storage.user.clear()
        ui.navigate.to('/login?next=/m')

    def nav_item(label: str, icon: str, active=False, action=None):
        col = ui.column().classes(f'items-center justify-center gap-1 w-[64px] h-[50px] {"xfm-nav-active" if active else "text-black"}')
        with col:
            ui.icon(icon).classes('text-[23px]')
            ui.label(label).classes('text-[11px] font-bold')
        if action:
            col.on('click', action)

    def render_bottom(active='status'):
        bottom_nav.set_visibility(True)
        bottom_nav.clear()
        with bottom_nav:
            nav_item('状态', 'speed', active == 'status', lambda: render_list())
            nav_item('命令片段', 'code', active == 'cmd', lambda: render_snippets())

    def render_empty(keyword=''):
        with ui.column().classes('w-full items-center justify-center gap-3 py-14 text-center'):
            ui.icon('search_off').classes('text-5xl text-gray-300')
            ui.label('没有找到 SSH 账号' if not keyword else '没有匹配的账号').classes('text-base font-black text-gray-900')
            ui.label('请先在电脑端后台添加 VPS 的 SSH 主机、端口和认证信息。').classes('text-xs text-gray-500 max-w-[290px]')

    def open_command_editor(existing_cmd=None):
        with ui.dialog() as dialog, ui.card().classes('w-[92vw] max-w-[420px] rounded-3xl p-4 gap-3'):
            ui.label('编辑快捷命令' if existing_cmd else '新增快捷命令').classes('text-xl font-black text-black')
            name_input = ui.input('按钮名称', value=(existing_cmd or {}).get('name', '')).props('outlined dense').classes('w-full')
            cmd_input = ui.textarea('执行命令', value=(existing_cmd or {}).get('cmd', '')).props('outlined rows=4').classes('w-full font-mono')

            async def save_cmd():
                name, cmd = str(name_input.value or '').strip(), str(cmd_input.value or '').strip()
                if not name or not cmd:
                    ui.notify('按钮名称和执行命令不能为空', type='warning', position='top')
                    return
                ADMIN_CONFIG.setdefault('quick_commands', [])
                if existing_cmd:
                    existing_cmd['name'] = name
                    existing_cmd['cmd'] = cmd
                    existing_cmd.setdefault('id', str(uuid.uuid4())[:8])
                else:
                    ADMIN_CONFIG['quick_commands'].append({'name': name, 'cmd': cmd, 'id': str(uuid.uuid4())[:8]})
                await save_admin_config()
                dialog.close()
                ui.notify('快捷命令已保存', type='positive', position='top')
                render_snippets()

            async def delete_cmd():
                if existing_cmd and existing_cmd in ADMIN_CONFIG.get('quick_commands', []):
                    ADMIN_CONFIG['quick_commands'].remove(existing_cmd)
                    await save_admin_config()
                    dialog.close()
                    ui.notify('快捷命令已删除', type='positive', position='top')
                    render_snippets()

            with ui.row().classes('w-full justify-between items-center pt-1'):
                if existing_cmd:
                    ui.button('删除', icon='delete', on_click=delete_cmd).props('flat no-caps').classes('text-red-500 font-black')
                else:
                    ui.element('div')
                with ui.row().classes('gap-2'):
                    ui.button('取消', on_click=dialog.close).props('flat no-caps').classes('text-gray-600 font-bold')
                    ui.button('保存', icon='save', on_click=save_cmd).props('unelevated no-caps').classes('bg-red-500 text-white font-black rounded-full px-4')
        dialog.open()

    def render_server_card(server: dict):
        host, port, user = _server_host(server), str(server.get('ssh_port') or '22'), str(server.get('ssh_user') or 'root')
        name = str(server.get('name') or host or '未命名 VPS')
        auth_type = _auth_label(server.get('ssh_auth_type'))
        probe = _probe_data(server)
        online = bool(probe)
        mem_text = _fmt_percent(probe.get('mem_usage')) if probe else '--'
        cpu_text = _fmt_percent(probe.get('cpu_usage')) if probe else '--'
        up_speed = _fmt_speed(probe.get('net_speed_out')) if probe else '--'
        down_speed = _fmt_speed(probe.get('net_speed_in')) if probe else '--'
        disk_text = _fmt_percent(probe.get('disk_usage')) if probe else '--'

        async def test_connect():
            test_btn.disable(); test_btn.props('loading')
            try:
                client, msg = await get_ssh_client(server)
                if client:
                    client.close(); ui.notify(msg, type='positive', position='top')
                else:
                    ui.notify(msg, type='negative', position='top')
            finally:
                test_btn.enable(); test_btn.props(remove='loading')

        with ui.card().classes('xfm-card p-3 gap-3'):
            with ui.row().classes('w-full items-start justify-between no-wrap gap-3'):
                with ui.column().classes('gap-1 min-w-0 flex-1'):
                    ui.label(name).classes('text-[18px] leading-tight font-extrabold text-black truncate')
                    ui.label(f'{user}@{host}:{port}').classes('text-[13px] font-mono text-gray-700 truncate')
                with ui.row().classes('items-center gap-3 shrink-0 pt-1'):
                    ui.element('div').classes(f'xfm-dot {"xfm-dot-online" if online else "xfm-dot-offline"}')
                    ui.icon('graphic_eq').classes('xfm-red-icon')
                    ui.icon('terminal').classes('xfm-red-icon cursor-pointer').on('click', lambda s=server: render_terminal(s))
            with ui.row().classes('w-full items-center justify-between gap-2 no-wrap'):
                with ui.row().classes('gap-5 items-center'):
                    for title, value, pct, color in [('CPU', cpu_text, probe.get('cpu_usage') if probe else None, '#ef4444'), ('内存', mem_text, probe.get('mem_usage') if probe else None, '#22c55e')]:
                        with ui.column().classes('items-center gap-1'):
                            with ui.element('div').classes('xfm-ring').style(_ring_style(pct, color)):
                                with ui.element('div').classes('xfm-ring-inner'):
                                    ui.label(value).classes('text-[12px]')
                            ui.label(title).classes('text-[12px] text-gray-500')
                with ui.grid(columns=2).classes('gap-x-4 gap-y-1 pr-1'):
                    for value, label, good in [(up_speed, '↑/s', True), (disk_text, '磁盘', False), (down_speed, '↓/s', True), (auth_type, '认证', False)]:
                        with ui.column().classes('gap-0'):
                            ui.label(value).classes(f'text-[16px] leading-none font-extrabold {"text-green-600" if good and value != "--" else "text-gray-600"}')
                            ui.label(label).classes('text-[11px] text-gray-500')
            with ui.row().classes('w-full items-center gap-2 pt-1'):
                ui.label(f'SSH {port}').classes('xfm-chip')
                ui.label('online' if online else 'offline').classes('xfm-chip text-green-600' if online else 'xfm-chip text-red-500')
                ui.label(auth_type).classes('xfm-chip ml-auto')
                test_btn = ui.button('测试', icon='bolt', on_click=test_connect).props('flat no-caps dense').classes('h-8 px-2 rounded-full text-red-500 font-black').style('min-width:66px')

    def render_list(keyword='', *, from_timer=False):
        if from_timer and state.get('view') != 'status':
            return
        if state.get('list_refreshing'):
            return
        state['list_refreshing'] = True
        state['view'] = 'status'
        state['list_keyword'] = keyword
        close_terminal(); render_bottom('status'); content.clear()
        servers = _ssh_servers(); kw = keyword.strip().lower()
        if kw:
            servers = [s for s in servers if kw in ' '.join([str(s.get('name') or ''), _server_host(s), str(s.get('ssh_user') or ''), str(s.get('ssh_port') or '')]).lower()]
        with content:
            with ui.row().classes('w-full items-center justify-between'):
                with ui.column().classes('gap-1'):
                    ui.label('状态').classes('xfm-title')
                    ui.label(MOBILE_UI_VERSION).classes('text-[10px] text-gray-400 font-bold')
                ui.button(icon='offline_bolt', on_click=lambda: ui.notify(MOBILE_UI_VERSION, position='top')).props('flat round').classes('xfm-pill w-[46px] h-[46px] text-black')
            with ui.row().classes('w-full items-center gap-2 no-wrap'):
                search = ui.input(placeholder='搜索', value=keyword).props('borderless clearable input-class=text-base').classes('xfm-search flex-1')
                search.on('keydown.enter', lambda e: render_list(search.value or ''))
                ui.button(icon='search', on_click=lambda: render_list(search.value or '')).props('flat round').classes('xfm-pill w-[46px] h-[46px] text-black')
            with ui.row().classes('w-full items-center justify-between px-1 -mt-1'):
                ui.label(f'{len(servers)} 台可 SSH 管理 VPS').classes('text-[13px] font-bold text-gray-500')
                ui.button('刷新', icon='refresh', on_click=lambda: render_list(keyword)).props('flat no-caps dense').classes('text-gray-700 font-bold')
            if not servers:
                render_empty(keyword)
                state['list_refreshing'] = False
                return
            for server in servers:
                render_server_card(server)
        state['list_refreshing'] = False

    def silent_refresh_list():
        if state.get('view') == 'status':
            render_list(state.get('list_keyword', ''), from_timer=True)

    def back_to_list():
        state['view'] = 'status'
        close_terminal()
        render_list(state.get('list_keyword', ''))

    async def start_terminal(server: dict, terminal_box, status_label=None):
        await asyncio.sleep(0.35)
        close_terminal()
        app.storage.user['is_dark'] = True
        if status_label:
            status_label.text = '正在建立 SSH 连接...'
        ssh = WebSSH(terminal_box, server)
        state['terminal'] = ssh
        await ssh.connect()
        if status_label:
            status_label.text = '已连接，点击黑色终端区域输入命令' if ssh.active else '连接失败，请检查 SSH 配置或点击账号卡片“测试”'

    def send_cmd(command: str):
        terminal = state.get('terminal')
        if terminal and terminal.active and terminal.channel:
            terminal.channel.send(command + '\n'); ui.notify(f'已发送: {command}', type='positive', position='top')
        else:
            ui.notify('终端尚未连接完成', type='warning', position='top')

    async def paste_clipboard():
        terminal = state.get('terminal')
        if not terminal or not terminal.active or not terminal.channel:
            ui.notify('终端尚未连接完成', type='warning', position='top'); return
        text = await ui.run_javascript('return navigator.clipboard ? await navigator.clipboard.readText() : ""', timeout=8.0)
        if text:
            terminal.channel.send(str(text)); ui.notify('已粘贴到终端', type='positive', position='top')
        else:
            ui.notify('剪贴板为空或浏览器未授权', type='warning', position='top')

    def send_key(value: str, label: str):
        terminal = state.get('terminal')
        if terminal and terminal.active and terminal.channel:
            terminal.channel.send(value)
        else:
            ui.notify(f'{label}：终端尚未连接完成', type='warning', position='top')

    def render_snippets():
        state['view'] = 'cmd'
        close_terminal(); render_bottom('cmd'); content.clear()
        with content:
            with ui.row().classes('w-full items-center justify-between'):
                ui.button(icon='arrow_back_ios_new', on_click=render_list).props('flat round').classes('xfm-pill w-[46px] h-[46px] text-black')
                with ui.column().classes('gap-1'):
                    ui.label('命令片段').classes('xfm-title')
                    ui.label('常用 SSH 命令，进入终端后一键发送').classes('text-[11px] text-gray-400 font-bold')
                ui.button(icon='add', on_click=lambda: open_command_editor(None)).props('flat round').classes('xfm-pill w-[46px] h-[46px] text-black')
            commands = _quick_commands()
            ui.label('这里与电脑端 SSH 页面保存的快捷命令共用；可在手机端新增、编辑、删除。').classes('text-[12px] text-gray-500 -mt-2')
            for item in commands:
                with ui.card().classes('xfm-card p-3 gap-1'):
                    with ui.row().classes('w-full items-start justify-between no-wrap gap-2'):
                        with ui.column().classes('gap-1 min-w-0 flex-1'):
                            ui.label(item['name']).classes('text-[16px] font-black text-black')
                            ui.label(item['cmd']).classes('text-[14px] font-mono text-gray-500 break-all')
                        editable = item if item in ADMIN_CONFIG.get('quick_commands', []) else None
                        if editable:
                            ui.button(icon='edit', on_click=lambda c=editable: open_command_editor(c)).props('flat round dense').classes('text-gray-600')

    def open_terminal_command_picker():
        with ui.dialog() as dialog, ui.card().classes('w-[92vw] max-w-[420px] rounded-3xl p-3 gap-2'):
            with ui.row().classes('w-full items-center justify-between'):
                ui.label('命令片段').classes('text-xl font-black text-black')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense').classes('text-gray-600')
            ui.label('选择后会立即发送到当前 SSH 终端').classes('text-xs text-gray-500 mb-1')
            for item in _quick_commands():
                def choose(cmd=item['cmd']):
                    dialog.close()
                    send_cmd(cmd)
                with ui.card().classes('w-full rounded-2xl p-3 bg-gray-50 shadow-none border border-gray-100 cursor-pointer').on('click', choose):
                    ui.label(item['name']).classes('text-[16px] font-black text-black')
        dialog.open()

    def render_terminal(server: dict):
        state['view'] = 'terminal'
        bottom_nav.set_visibility(False)
        name, host, port, user = str(server.get('name') or _server_host(server) or 'VPS'), _server_host(server), str(server.get('ssh_port') or '22'), str(server.get('ssh_user') or 'root')
        content.clear()
        with content:
            with ui.column().classes('xfm-terminal-page w-full gap-2'):
                with ui.row().classes('w-full items-center justify-between no-wrap'):
                    with ui.row().classes('xfm-terminal-pill items-center px-1 gap-1'):
                        ui.button(icon='arrow_back_ios_new', on_click=back_to_list).props('flat round').classes('w-[42px] h-[42px] text-black')
                        ui.button(icon='close', on_click=back_to_list).props('flat round').classes('w-[42px] h-[42px] text-black')
                    with ui.column().classes('items-center gap-0 min-w-0 px-2'):
                        ui.label(name).classes('text-[17px] leading-tight font-black text-black truncate max-w-[210px]')
                        ui.label(f'{user}@{host}:{port}').classes('text-[12px] font-mono text-gray-400 truncate max-w-[220px]')
                    with ui.button(icon='more_horiz').props('flat round').classes('xfm-terminal-pill w-[50px] h-[50px] text-black'):
                        with ui.menu().classes('rounded-2xl p-2 shadow-2xl bg-white'):
                            ui.menu_item('粘贴', lambda: asyncio.create_task(paste_clipboard()))
                            ui.menu_item('命令片段', open_terminal_command_picker)
                            ui.menu_item('清屏', lambda: send_cmd('clear'))
                            ui.menu_item('退出登录', logout)
                status_label = ui.label('正在初始化终端...').classes('xfm-terminal-status')
                terminal_box = ui.element('div').classes('xfm-terminal-wrap w-full rounded-[14px] overflow-hidden border border-black/10 shadow-xl')
                with ui.row().classes('xfm-keybar w-full items-center justify-around px-2 no-wrap'):
                    ui.button('CTRL-C', on_click=lambda: send_key('\x03', 'CTRL-C')).props('flat dense no-caps').classes('xfm-key')
                    ui.button('TAB', on_click=lambda: send_key('\t', 'TAB')).props('flat dense no-caps').classes('xfm-key')
                    ui.button('-', on_click=lambda: send_key('-', '-')).props('flat dense no-caps').classes('xfm-key')
                    ui.button('/', on_click=lambda: send_key('/', '/')).props('flat dense no-caps').classes('xfm-key')
                    ui.button('ESC', on_click=lambda: send_key('\x1b', 'ESC')).props('flat dense no-caps').classes('xfm-key')
                    ui.button('↑', on_click=lambda: send_key('\x1b[A', '↑')).props('flat dense no-caps').classes('xfm-key')
                    ui.button(icon='keyboard', on_click=lambda: ui.notify('点击终端区域可唤起键盘', position='top')).props('flat round dense').classes('text-black')
        asyncio.create_task(start_terminal(server, terminal_box, status_label))

    render_list()
    ui.timer(3.0, silent_refresh_list)
