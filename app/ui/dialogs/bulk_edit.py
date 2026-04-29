from nicegui import app, ui

from app.core.state import ADMIN_CONFIG, SERVERS_CACHE
from app.storage.repositories import save_admin_config, save_servers
from app.ui.common.notifications import safe_notify
from app.utils.formatters import smart_sort_key
from app.utils.geo import detect_country_group
from app.utils.network import get_real_ip_display


class BulkEditor:
    def __init__(self, target_servers, title="批量管理"):
        self.all_servers = target_servers
        self.title = title
        self.selected_urls = set()
        self.ui_rows = {}
        self.dialog = None

    def open(self):
        is_dark = bool(app.storage.user.get('is_dark', True))
        self.is_dark = is_dark
        with ui.dialog() as d, ui.card().classes('w-full max-w-4xl h-[85vh] flex flex-col p-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-full max-w-4xl h-[85vh] flex flex-col p-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
            self.dialog = d

            with ui.row().classes('w-full justify-between items-center px-5 py-4 bg-gradient-to-r from-[#0a1526] to-[#050a14] border-b border-[#1e3a5f]/60 flex-shrink-0 relative overflow-hidden' if is_dark else 'w-full justify-between items-center px-5 py-4 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] border-b border-slate-300/90 flex-shrink-0 relative overflow-hidden'):
                ui.element('div').classes('absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNCwyMTEsMjM4LDAuMDcpIi8+PC9zdmc+")] opacity-100 pointer-events-none')
                with ui.row().classes('items-center gap-3 z-10'):
                    with ui.element('div').classes('w-9 h-9 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] shadow-[0_0_8px_rgba(0,0,0,0.7)] text-cyan-400 relative overflow-hidden' if is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] text-sky-600 relative overflow-hidden'):
                        ui.element('div').classes('absolute inset-0 bg-cyan-400/10')
                        ui.icon('edit_note').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                    ui.label(self.title).classes('text-lg font-black text-slate-200 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes('z-10 text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'z-10 text-slate-500 hover:text-sky-700 hover:bg-sky-100')

            with ui.column().classes('w-full p-4 gap-3 border-b border-[#1e3a5f]/45 bg-[#030712] flex-shrink-0' if is_dark else 'w-full p-4 gap-3 border-b border-slate-300/90 bg-[#f8fbff] flex-shrink-0'):
                self.search_input = ui.input(placeholder='🔍 搜索服务器名称...').props('outlined dense clearable dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense clearable color=blue').classes('w-full')
                self.search_input.on_value_change(self.on_search)

                with ui.row().classes('w-full justify-between items-center'):
                    with ui.row().classes('gap-2'):
                        ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense size=sm').classes('bg-cyan-950/35 text-cyan-300 border border-cyan-500/35 rounded-sm px-3' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 rounded-sm px-3')
                        ui.button('全不选', on_click=lambda: self.toggle_all(False)).props('flat dense size=sm').classes('bg-[#0a1120] text-slate-300 border border-[#1e3a5f]/45 rounded-sm px-3' if is_dark else 'bg-white text-slate-600 border border-slate-300 rounded-sm px-3')
                        self.count_label = ui.label('已选: 0').classes('text-xs font-bold text-cyan-400 self-center ml-2' if is_dark else 'text-xs font-bold text-sky-700 self-center ml-2')

            with ui.scroll_area().classes('w-full flex-grow p-2 bg-[#030712]' if is_dark else 'w-full flex-grow p-2 bg-[#f8fbff]'):
                with ui.column().classes('w-full gap-1') as self.list_container:
                    if not self.all_servers:
                        ui.label('当前组无服务器').classes('w-full text-center text-slate-500 mt-10')

                    try:
                        sorted_srv = sorted(self.all_servers, key=lambda x: smart_sort_key(x))
                    except:
                        sorted_srv = self.all_servers

                    for s in sorted_srv:
                        with ui.row().classes('w-full items-center p-2 bg-[#0a1120]/85 rounded-sm border border-[#1e3a5f]/45 hover:border-cyan-500/45 hover:bg-[#0d172a] transition' if is_dark else 'w-full items-center p-2 bg-white rounded-sm border border-slate-300/90 hover:border-sky-400/60 hover:bg-sky-50 transition') as row:
                            chk = ui.checkbox(value=False).props('dense dark color=cyan' if is_dark else 'dense color=blue').classes('mr-2')
                            chk.on_value_change(lambda e, u=s['url']: self.on_check(u, e.value))

                            with ui.column().classes('gap-0 flex-grow overflow-hidden'):
                                display_name = s['name']
                                try:
                                    country = detect_country_group(s['name'])
                                    flag = country.split(' ')[0]
                                    if flag not in s['name']:
                                        display_name = f"{flag} {s['name']}"
                                except:
                                    pass

                                ui.label(display_name).classes('text-sm font-bold text-slate-300 truncate' if is_dark else 'text-sm font-bold text-slate-800 truncate')
                                ui.label(s['url']).classes('text-xs text-slate-500 font-mono truncate hidden')

                            ip_addr = get_real_ip_display(s['url'])
                            status = s.get('_status')
                            stat_color, stat_icon = ('green-500', 'bolt') if status == 'online' else (('red-500', 'bolt') if status == 'offline' else ('grey-500', 'help_outline'))

                            with ui.row().classes('items-center gap-1'):
                                ui.icon(stat_icon).classes(f'text-{stat_color} text-sm')
                                ip_lbl = ui.label(ip_addr).classes('text-xs font-mono text-cyan-500/70' if is_dark else 'text-xs font-mono text-sky-700/80')
                                from app.ui.components.server_rows import bind_ip_label
                                bind_ip_label(s['url'], ip_lbl)

                        self.ui_rows[s['url']] = {'el': row, 'search_text': f"{s['name']} {s['url']} {ip_addr}".lower(), 'checkbox': chk}

            with ui.row().classes('w-full p-4 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14] justify-between items-center flex-shrink-0' if is_dark else 'w-full p-4 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] justify-between items-center flex-shrink-0'):
                with ui.row().classes('gap-2'):
                    ui.label('批量操作:').classes('text-sm font-black text-cyan-400 self-center tracking-wide' if is_dark else 'text-sm font-black text-sky-700 self-center tracking-wide')

                    async def move_group():
                        if not self.selected_urls:
                            return safe_notify('未选择服务器', 'warning')
                        with ui.dialog() as sub_d, ui.card().classes('w-[360px] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-[360px] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                            with ui.column().classes('w-full p-5 gap-3 bg-gradient-to-r from-[#0a1526] to-[#050a14] border-b border-[#1e3a5f]/60' if is_dark else 'w-full p-5 gap-3 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] border-b border-slate-300/90'):
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('移动到分组').classes('font-black text-lg text-slate-200 tracking-wide' if is_dark else 'font-black text-lg text-slate-800 tracking-wide')
                                    ui.button(icon='close', on_click=sub_d.close).props('flat round dense color=grey').classes('text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100')
                            with ui.column().classes('w-full p-4 gap-4 bg-[#030712]' if is_dark else 'w-full p-4 gap-4 bg-[#f8fbff]'):
                                from app.services.server_ops import get_all_groups
                                groups = get_all_groups()
                                sel = ui.select(groups, label='选择或输入分组', with_input=True, new_value_mode='add-unique').classes('w-full').props('outlined dense dark color=cyan bg-color="[#050b14]"' if is_dark else 'outlined dense color=blue')
                                async def do_move(target_group):
                                    if not target_group:
                                        return
                                    count = 0
                                    for s in SERVERS_CACHE:
                                        if s['url'] in self.selected_urls:
                                            s['group'] = target_group
                                            count += 1
                                    if 'custom_groups' not in ADMIN_CONFIG:
                                        ADMIN_CONFIG['custom_groups'] = []
                                    if target_group not in ADMIN_CONFIG['custom_groups'] and target_group != '默认分组':
                                        ADMIN_CONFIG['custom_groups'].append(target_group)
                                        await save_admin_config()
                                    await save_servers()
                                    sub_d.close()
                                    self.dialog.close()
                                    from app.ui.components.sidebar import render_sidebar_content
                                    from app.ui.pages.content_router import refresh_content
                                    render_sidebar_content.refresh()
                                    try:
                                        await refresh_content('ALL')
                                    except:
                                        pass
                                    safe_notify(f'已移动 {count} 个服务器到 [{target_group}]', 'positive')
                            ui.button('确定移动', on_click=lambda: do_move(sel.value)).props('flat').classes('w-full mt-1 bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm' if is_dark else 'w-full mt-1 bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 font-black rounded-sm')
                        sub_d.open()

                    ui.button('移动分组', icon='folder_open', on_click=move_group).props('flat dense').classes('bg-cyan-950/35 text-cyan-300 border border-cyan-500/35 rounded-sm px-3' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 rounded-sm px-3')

                    async def batch_ssh_config():
                        if not self.selected_urls:
                            return safe_notify('未选择服务器', 'warning')
                        with ui.dialog() as d_ssh, ui.card().classes('w-[420px] p-0 gap-0 overflow-hidden flex flex-col bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)] rounded-sm' if is_dark else 'w-[420px] p-0 gap-0 overflow-hidden flex flex-col bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)] rounded-sm'):
                            with ui.column().classes('w-full p-5 gap-3 bg-gradient-to-r from-[#0a1526] to-[#050a14] border-b border-[#1e3a5f]/60' if is_dark else 'w-full p-5 gap-3 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] border-b border-slate-300/90'):
                                with ui.row().classes('items-center gap-2 mb-1'):
                                    ui.icon('vpn_key').classes('text-xl text-cyan-400')
                                    ui.label('批量 SSH 配置').classes('text-lg font-black text-slate-200 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide')
                                ui.label(f'正在修改 {len(self.selected_urls)} 个服务器的连接信息').classes('text-xs text-slate-500')
                            with ui.column().classes('w-full p-5 gap-3 bg-[#030712]' if is_dark else 'w-full p-5 gap-3 bg-[#f8fbff]'):
                                ui.label('SSH 用户名').classes('text-xs font-bold text-slate-400 mt-2')
                                user_input = ui.input(placeholder='留空则保持原样').props('outlined dense dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense color=blue').classes('w-full')
                                ui.label('认证方式').classes('text-xs font-bold text-slate-400 mt-2')
                                auth_opts = ['不修改', '全局密钥', '独立密码', '独立密钥']
                                auth_sel = ui.select(auth_opts, value='不修改').props('outlined dense options-dense dark color=cyan bg-color="[#050b14]"' if is_dark else 'outlined dense options-dense color=blue').classes('w-full')
                                pwd_input = ui.input('输入新密码', password=True).props('outlined dense dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense color=blue').classes('w-full')
                                pwd_input.bind_visibility_from(auth_sel, 'value', value='独立密码')
                                key_input = ui.textarea('输入新私钥', placeholder='-----BEGIN...').props('outlined dense rows=4 input-class="text-xs font-mono" dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense rows=4 input-class="text-xs font-mono" color=blue').classes('w-full')
                                key_input.bind_visibility_from(auth_sel, 'value', value='独立密钥')
                                global_hint = ui.label('✅ 将统一使用全局 SSH 密钥连接').classes('text-xs text-emerald-300 bg-emerald-950/25 p-2 rounded-sm w-full text-center border border-emerald-500/35' if is_dark else 'text-xs text-emerald-700 bg-emerald-50 p-2 rounded-sm w-full text-center border border-emerald-300')
                                global_hint.bind_visibility_from(auth_sel, 'value', value='全局密钥')
                                async def save_ssh_changes():
                                    count = 0
                                    target_user = user_input.value.strip()
                                    target_auth = auth_sel.value
                                    for s in SERVERS_CACHE:
                                        if s['url'] in self.selected_urls:
                                            changed = False
                                            if target_user:
                                                s['ssh_user'] = target_user
                                                changed = True
                                            if target_auth != '不修改':
                                                s['ssh_auth_type'] = target_auth
                                                changed = True
                                                if target_auth == '独立密码':
                                                    s['ssh_password'] = pwd_input.value
                                                elif target_auth == '独立密钥':
                                                    s['ssh_key'] = key_input.value
                                            if changed:
                                                count += 1
                                    if count > 0:
                                        await save_servers()
                                        d_ssh.close()
                                        safe_notify(f'✅ 已更新 {count} 个 SSH 配置', 'positive')
                                    else:
                                        d_ssh.close()
                                        safe_notify('未做任何修改', 'warning')
                                with ui.row().classes('w-full justify-end mt-4 gap-2'):
                                    ui.button('取消', on_click=d_ssh.close).props('outline color=grey').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm')
                                    ui.button('保存配置', icon='save', on_click=save_ssh_changes).props('flat').classes('bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm px-4' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 font-black rounded-sm px-4')
                        d_ssh.open()

                    ui.button('SSH 设置', icon='vpn_key', on_click=batch_ssh_config).props('flat dense').classes('bg-cyan-950/35 text-cyan-300 border border-cyan-500/35 rounded-sm px-3' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 rounded-sm px-3')

                    async def delete_servers():
                        if not self.selected_urls:
                            return safe_notify('未选择服务器', 'warning')
                        with ui.dialog() as sub_d, ui.card().classes('w-[360px] p-0 gap-0 overflow-hidden bg-[#070b14] border border-rose-800/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)] rounded-sm' if is_dark else 'w-[360px] p-0 gap-0 overflow-hidden bg-white border border-rose-300 shadow-[0_10px_28px_rgba(148,163,184,0.18)] rounded-sm'):
                            with ui.column().classes('w-full p-5 gap-3 bg-gradient-to-r from-[#19070d] to-[#0b0911] border-b border-rose-900/60' if is_dark else 'w-full p-5 gap-3 bg-gradient-to-r from-rose-50 to-orange-50 border-b border-rose-200'):
                                ui.label(f'确定删除 {len(self.selected_urls)} 个服务器?').classes('font-black text-rose-300 text-lg tracking-wide' if is_dark else 'font-black text-rose-700 text-lg tracking-wide')
                            with ui.row().classes('w-full justify-end mt-4 p-4 bg-[#030712]' if is_dark else 'w-full justify-end mt-4 p-4 bg-white'):
                                ui.button('取消', on_click=sub_d.close).props('outline color=grey').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm')
                                async def confirm_del():
                                    SERVERS_CACHE[:] = [s for s in SERVERS_CACHE if s['url'] not in self.selected_urls]
                                    await save_servers()
                                    sub_d.close()
                                    d.close()
                                    from app.ui.components.sidebar import render_sidebar_content
                                    from app.ui.pages.content_router import content_container
                                    render_sidebar_content.refresh()
                                    if content_container:
                                        content_container.clear()
                                    safe_notify('删除成功', 'positive')
                                ui.button('确定删除', on_click=confirm_del).props('flat').classes('bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 font-black rounded-sm px-4' if is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 font-black rounded-sm px-4')
                        sub_d.open()

                    ui.button('删除', icon='delete', on_click=delete_servers).props('flat dense').classes('bg-rose-950/35 text-rose-300 border border-rose-500/35 rounded-sm px-3')

                ui.button('关闭', on_click=d.close).props('outline color=grey').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm')

        d.open()

    def on_search(self, e):
        keyword = str(e.value).lower().strip()
        for url, item in self.ui_rows.items():
            visible = keyword in item['search_text']
            item['el'].set_visibility(visible)

    def on_check(self, url, value):
        if value:
            self.selected_urls.add(url)
        else:
            self.selected_urls.discard(url)
        self.count_label.set_text(f'已选: {len(self.selected_urls)}')

    def toggle_all(self, state):
        visible_urls = [u for u, item in self.ui_rows.items() if item['el'].visible]
        for url in visible_urls:
            self.ui_rows[url]['checkbox'].value = state
        if not state:
            for url in visible_urls:
                self.selected_urls.discard(url)
        self.count_label.set_text(f'已选: {len(self.selected_urls)}')


def open_bulk_edit_dialog(servers, title="管理"):
    editor = BulkEditor(servers, title)
    editor.open()
