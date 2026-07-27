import asyncio
import uuid

from nicegui import app, ui

from app.core.state import NODES_DATA, SERVERS_CACHE, SUBS_CACHE, INDEPENDENT_NODES_CACHE
from app.services.xui_fetch import fetch_inbounds_safe
from app.storage.repositories import save_subs, save_independent_nodes
from app.ui.common.notifications import safe_notify
from app.ui.pages.subs_page import load_subs_view
from app.utils.geo import detect_country_group


class SubEditor:
    def __init__(self, data=None):
        self.data = data
        if data:
            self.d = data.copy()
            if 'token' not in self.d:
                self.d['token'] = str(uuid.uuid4())
            if 'nodes' not in self.d:
                self.d['nodes'] = []
        else:
            self.d = {'name': '', 'token': str(uuid.uuid4()), 'nodes': []}

        self.sel = set(self.d.get('nodes', []))
        self.groups_data = {}
        self.all_node_keys = set()

        self.search_term = ""
        self.visible_node_keys = set()

        self.name_input = None
        self.token_input = None

    def ui(self, dlg):
        is_dark = bool(app.storage.user.get('is_dark', True))
        self.is_dark = is_dark
        with ui.card().classes('w-[90vw] max-w-4xl p-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-[90vw] max-w-4xl p-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]').style('display: flex; flex-direction: column; height: 85vh;'):
            with ui.row().classes('w-full justify-between items-center p-4 border-b border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'w-full justify-between items-center p-4 border-b border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]'):
                ui.label('订阅编辑器').classes('text-xl font-black text-slate-100 tracking-wide' if is_dark else 'text-xl font-black text-slate-800 tracking-wide')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100')

            with ui.element('div').classes('w-full flex-grow overflow-y-auto p-4 bg-[#030712]' if is_dark else 'w-full flex-grow overflow-y-auto p-4 bg-[#f8fbff]').style('display: flex; flex-direction: column; gap: 1rem;'):
                self.name_input = ui.input('订阅名称', value=self.d.get('name', '')).classes('w-full').props('outlined dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined color=blue')
                self.name_input.on_value_change(lambda e: self.d.update({'name': e.value}))

                with ui.row().classes('w-full items-center gap-2'):
                    self.token_input = ui.input('订阅路径 (Token)', value=self.d.get('token', ''), placeholder='例如: my-phone').classes('flex-grow').props('outlined dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined color=blue')
                    self.token_input.on_value_change(lambda e: self.d.update({'token': e.value.strip()}))
                    ui.button(icon='refresh', on_click=lambda: self.token_input.set_value(str(uuid.uuid4()))).props('flat dense').classes('text-cyan-400 hover:bg-cyan-950/30 hover:text-cyan-300' if is_dark else 'text-sky-600 hover:bg-sky-100 hover:text-sky-700').tooltip('生成随机 UUID')

                with ui.column().classes('w-full gap-2 bg-[#0a1120] p-3 rounded-sm border border-[#1e3a5f]/45' if is_dark else 'w-full gap-2 bg-white p-3 rounded-sm border border-slate-300/90'):
                    with ui.row().classes('w-full items-center gap-4'):
                        ui.label('节点列表').classes('font-black ml-2 flex-shrink-0 text-slate-200' if is_dark else 'font-black ml-2 flex-shrink-0 text-slate-800')
                        ui.input(placeholder='🔍 搜索节点或服务器...', on_change=self.on_search_change).props('outlined dense dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense color=blue').classes('flex-grow')

                    with ui.row().classes('w-full justify-end gap-2'):
                        ui.label('操作当前列表:').classes('text-xs text-slate-500 self-center')
                        ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense size=sm').classes('bg-cyan-950/40 text-cyan-300 border border-cyan-500/40 rounded-sm px-3' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 rounded-sm px-3')
                        ui.button('清空', on_click=lambda: self.toggle_all(False)).props('flat dense size=sm').classes('bg-rose-950/40 text-rose-300 border border-rose-500/40 rounded-sm px-3' if is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 rounded-sm px-3')

                self.cont = ui.column().classes('w-full').style('display: flex; flex-direction: column; gap: 10px;')

            with ui.row().classes('w-full p-4 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'w-full p-4 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]'):
                async def save():
                    if self.name_input:
                        self.d['name'] = self.name_input.value
                    if self.token_input:
                        new_token = self.token_input.value.strip()
                        if not new_token:
                            return safe_notify("订阅路径不能为空", "negative")
                        if (not self.data) or (self.data.get('token') != new_token):
                            for s in SUBS_CACHE:
                                if s.get('token') == new_token:
                                    return safe_notify(f"路径 '{new_token}' 已被占用", "negative")
                        self.d['token'] = new_token

                    self.d['nodes'] = list(self.sel)
                    if self.data:
                        try:
                            idx = SUBS_CACHE.index(self.data)
                            SUBS_CACHE[idx] = self.d
                        except:
                            SUBS_CACHE.append(self.d)
                    else:
                        SUBS_CACHE.append(self.d)

                    await save_subs()
                    await load_subs_view()
                    dlg.close()
                    ui.notify('订阅保存成功', color='positive')

                ui.button('保存', icon='save', on_click=save).props('flat').classes('w-full h-12 bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm' if is_dark else 'w-full h-12 bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 font-black rounded-sm')

        asyncio.create_task(self.load_data())

    def on_search_change(self, e):
        self.search_term = str(e.value).lower().strip()
        self.render_list()

    async def load_data(self):
        with self.cont:
            ui.spinner('dots').classes('self-center mt-10')

        current_servers_snapshot = list(SERVERS_CACHE)

        tasks = [fetch_inbounds_safe(s, force_refresh=False) for s in current_servers_snapshot]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        self.groups_data = {}
        self.all_node_keys = set()

        for i, srv in enumerate(current_servers_snapshot):
            nodes = results[i]
            if not nodes or isinstance(nodes, Exception):
                nodes = NODES_DATA.get(srv['url'], []) or []

            custom = srv.get('custom_nodes', []) or []
            all_server_nodes = nodes + custom

            if all_server_nodes:
                for n in all_server_nodes:
                    k = f"{srv['url']}|{n['id']}"
                    self.all_node_keys.add(k)

            g_name = srv.get('group', '默认分组') or '默认分组'
            if g_name not in self.groups_data:
                self.groups_data[g_name] = []

            self.groups_data[g_name].append({'server': srv, 'nodes': all_server_nodes})

        self.render_list()

    def render_list(self):
        self.cont.clear()
        self.visible_node_keys = set()

        with self.cont:
            if not self.groups_data:
                ui.label('暂无数据').classes('text-center w-full mt-4')
                return

            sorted_groups = sorted(self.groups_data.keys())
            has_match = False

            for g_name in sorted_groups:
                servers_in_group = self.groups_data[g_name]
                visible_servers_ui = []

                for item in servers_in_group:
                    srv = item['server']
                    nodes = item['nodes']

                    matched_nodes = []
                    for n in nodes:
                        if (not self.search_term) or \
                           (self.search_term in n['remark'].lower()) or \
                           (self.search_term in srv['name'].lower()):
                            matched_nodes.append(n)
                            self.visible_node_keys.add(f"{srv['url']}|{n['id']}")

                    if matched_nodes:
                        visible_servers_ui.append({'server': srv, 'nodes': matched_nodes})

                if visible_servers_ui:
                    has_match = True
                    expand_value = True if self.search_term else True

                    with ui.expansion(g_name, icon='folder', value=expand_value).classes('w-full border rounded mb-2' if self.is_dark else 'w-full border border-slate-300 rounded mb-2 bg-white').style('width: 100%;'):
                        with ui.column().classes('w-full p-0').style('display: flex; flex-direction: column; width: 100%;'):
                            for item in visible_servers_ui:
                                srv = item['server']
                                nodes = item['nodes']

                                with ui.column().classes('w-full p-2 border-b').style('display: flex; flex-direction: column; align-items: flex-start; width: 100%;'):
                                    with ui.row().classes('items-center gap-2 mb-2'):
                                        ui.icon('dns', size='xs')
                                        ui.label(srv['name']).classes('font-bold')

                                    if nodes:
                                        with ui.column().classes('w-full pl-4 gap-1').style('display: flex; flex-direction: column; width: 100%;'):
                                            for n in nodes:
                                                key = f"{srv['url']}|{n['id']}"
                                                cb = ui.checkbox(n['remark'], value=(key in self.sel))
                                                cb.classes('w-full text-sm dense').style('display: flex; width: 100%;')
                                                cb.on_value_change(lambda e, k=key: self.on_check(k, e.value))

            if not has_match:
                ui.label('未找到匹配的节点').classes('text-center w-full mt-4 text-gray-400')

    def on_check(self, key, value):
        if value:
            self.sel.add(key)
        else:
            self.sel.discard(key)

    def toggle_all(self, select_state):
        if select_state:
            self.sel.update(self.visible_node_keys)
        else:
            self.sel.difference_update(self.visible_node_keys)
        self.render_list()


def open_sub_editor(d):
    with ui.dialog() as dlg:
        SubEditor(d).ui(dlg)
        dlg.open()


class IndependentNodeEditor:
    def __init__(self, data=None):
        self.data = data
        if data:
            self.d = data.copy()
        else:
            self.d = {'id': f'indep_{uuid.uuid4().hex[:8]}', 'remark': '', '_raw_link': '', 'enable': True}

        self.remark_input = None
        self.link_input = None

    def ui(self, dlg):
        is_dark = bool(app.storage.user.get('is_dark', True))
        with ui.card().classes('w-[90vw] max-w-lg p-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-[90vw] max-w-lg p-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
            with ui.row().classes('w-full justify-between items-center p-4 border-b border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'w-full justify-between items-center p-4 border-b border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]'):
                ui.label('独立节点编辑器').classes('text-xl font-black text-slate-100 tracking-wide' if is_dark else 'text-xl font-black text-slate-800 tracking-wide')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100')

            with ui.column().classes('w-full p-4 gap-4 bg-[#030712]' if is_dark else 'w-full p-4 gap-4 bg-[#f8fbff]'):
                self.remark_input = ui.input('节点名称', value=self.d.get('remark', '')).classes('w-full').props('outlined dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined color=blue')
                
                self.link_input = ui.textarea('节点分享链接', value=self.d.get('_raw_link', '')).classes('w-full').props('outlined dark color=cyan standout bg-color="[#050b14]" rows=4' if is_dark else 'outlined color=blue rows=4')
                ui.label('支持 vmess://, vless://, trojan://, ss://, hy2:// 等各种分享链接').classes('text-xs text-slate-500 mt-[-10px]')

            with ui.row().classes('w-full p-4 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'w-full p-4 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]'):
                async def save():
                    remark = self.remark_input.value.strip()
                    link = self.link_input.value.strip()
                    
                    if not remark:
                        return safe_notify("节点名称不能为空", "negative")
                    if not link:
                        return safe_notify("节点链接不能为空", "negative")
                        
                    self.d['remark'] = remark
                    self.d['_raw_link'] = link
                    
                    if self.data:
                        try:
                            idx = INDEPENDENT_NODES_CACHE.index(self.data)
                            INDEPENDENT_NODES_CACHE[idx] = self.d
                        except ValueError:
                            INDEPENDENT_NODES_CACHE.append(self.d)
                    else:
                        INDEPENDENT_NODES_CACHE.append(self.d)

                    await save_independent_nodes()
                    await load_subs_view()
                    dlg.close()
                    ui.notify('独立节点保存成功', color='positive')

                ui.button('保存', icon='save', on_click=save).props('flat').classes('w-full h-12 bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm' if is_dark else 'w-full h-12 bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 font-black rounded-sm')

def open_independent_node_editor(data=None):
    with ui.dialog() as dlg:
        IndependentNodeEditor(data).ui(dlg)
        dlg.open()


class AdvancedSubEditor:
    def __init__(self, sub_data=None):
        import copy
        if sub_data:
            self.sub = copy.deepcopy(sub_data)
        else:
            self.sub = {'name': '', 'token': str(uuid.uuid4()), 'nodes': [], 'options': {}}

        if 'options' not in self.sub:
            self.sub['options'] = {}

        self.selected_ids = list(self.sub.get('nodes', []))

        self.all_nodes_map = {}
        self.ui_groups = {}
        self.server_expansions = {}
        self.server_items = {}
        self.search_text = ""
        self.preview_container = None
        self.left_scroll = None
        self.list_container = None

    def ui(self, dlg):
        self._preload_data()

        is_dark = bool(app.storage.user.get('is_dark', True))
        self.is_dark = is_dark
        with ui.card().classes('w-full max-w-6xl h-[90vh] flex flex-col p-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-full max-w-6xl h-[90vh] flex flex-col p-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
            with ui.row().classes('w-full p-4 border-b border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14] justify-between items-center flex-shrink-0' if is_dark else 'w-full p-4 border-b border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] justify-between items-center flex-shrink-0'):
                with ui.row().classes('items-center gap-3'):
                    with ui.element('div').classes('w-9 h-9 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] text-cyan-400 shadow-[0_0_8px_rgba(0,0,0,0.7)] relative overflow-hidden' if is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 text-sky-600 shadow-[0_4px_12px_rgba(148,163,184,0.14)] relative overflow-hidden'):
                        ui.element('div').classes('absolute inset-0 bg-cyan-400/10')
                        ui.icon('tune').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                    ui.label('订阅高级管理').classes('text-lg font-black text-slate-100 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense color=grey').classes('text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100')

            with ui.row().classes('w-full flex-grow overflow-hidden gap-0'):
                with ui.column().classes('w-2/5 h-full border-r border-[#1e3a5f]/45 flex flex-col bg-[#030712]' if is_dark else 'w-2/5 h-full border-r border-slate-300/90 flex flex-col bg-[#f8fbff]'):
                    with ui.column().classes('w-full p-2 border-b border-[#1e3a5f]/45 bg-[#070b14] gap-2' if is_dark else 'w-full p-2 border-b border-slate-300/90 bg-white gap-2'):
                        ui.input(placeholder='🔍 搜索源节点...', on_change=self.on_search).props('outlined dense dark color=cyan standout debounce="300" bg-color="[#050b14]"' if is_dark else 'outlined dense color=blue debounce="300"').classes('w-full')

                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label('筛选结果操作:').classes('text-xs text-slate-500')
                            with ui.row().classes('gap-1'):
                                ui.button('全选', icon='add_circle', on_click=lambda: self.batch_select(True)) \
                                    .props('unelevated dense size=sm color=blue-7').tooltip('将搜索结果加入右侧')

                                ui.button('清空', icon='remove_circle', on_click=lambda: self.batch_select(False)) \
                                    .props('flat dense size=sm color=grey-6').tooltip('从右侧移除搜索结果')

                    with ui.scroll_area().classes('w-full flex-grow p-2') as area:
                        self.left_scroll = area
                        self.list_container = ui.column().classes('w-full gap-2')
                        ui.timer(0.1, lambda: asyncio.create_task(self._render_node_tree()), once=True)

                with ui.column().classes('w-1/4 h-full border-r border-slate-700 flex flex-col bg-[#1e293b] overflow-y-auto' if is_dark else 'w-1/4 h-full border-r border-slate-300 flex flex-col bg-white overflow-y-auto'):
                    with ui.column().classes('w-full p-4 gap-4'):
                        ui.label('① 基础设置').classes('text-xs font-bold text-blue-400 uppercase' if is_dark else 'text-xs font-bold text-sky-700 uppercase')
                        ui.input('订阅名称', value=self.sub.get('name', '')) \
                            .bind_value_to(self.sub, 'name') \
                            .props('outlined dense dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense color=blue').classes('w-full')

                        with ui.row().classes('w-full gap-1'):
                            ui.input('Token', value=self.sub.get('token', '')) \
                                .bind_value_to(self.sub, 'token') \
                                .props('outlined dense dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense color=blue').classes('flex-grow')

                            ui.button(icon='refresh', on_click=lambda: self.sub.update({'token': str(uuid.uuid4())[:8]})).props('flat dense color=blue').classes('text-cyan-400 hover:bg-cyan-950/30 hover:text-cyan-300' if is_dark else 'text-sky-600 hover:bg-sky-100 hover:text-sky-700')

                        ui.separator().classes('bg-slate-700' if is_dark else 'bg-slate-300')

                        ui.label('② 排序工具').classes('text-xs font-bold text-blue-400 uppercase' if is_dark else 'text-xs font-bold text-sky-700 uppercase')
                        with ui.grid().classes('w-full grid-cols-2 gap-2'):
                            ui.button('名称 A-Z', on_click=lambda: self.sort_nodes('name_asc')).props('outline dense size=sm color=slate-400').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100')
                            ui.button('名称 Z-A', on_click=lambda: self.sort_nodes('name_desc')).props('outline dense size=sm color=slate-400').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100')
                            ui.button('随机打乱', on_click=lambda: self.sort_nodes('random')).props('outline dense size=sm color=slate-400').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100')
                            ui.button('列表倒序', on_click=lambda: self.sort_nodes('reverse')).props('outline dense size=sm color=slate-400').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100')

                        ui.separator().classes('bg-slate-700' if is_dark else 'bg-slate-300')

                        ui.label('③ 批量重命名').classes('text-xs font-bold text-blue-400 uppercase' if is_dark else 'text-xs font-bold text-sky-700 uppercase')
                        with ui.column().classes('w-full gap-2 bg-[#0f172a] p-2 rounded border border-slate-700' if is_dark else 'w-full gap-2 bg-sky-50 p-2 rounded border border-slate-300'):
                            opt = self.sub.get('options', {})
                            pat = ui.input('正则 (如: ^)', value=opt.get('rename_pattern', '')).props('outlined dense dark bg-color="slate-900" color=cyan' if is_dark else 'outlined dense color=blue').classes('w-full')
                            rep = ui.input('替换 (如: VIP-)', value=opt.get('rename_replacement', '')).props('outlined dense dark bg-color="slate-900" color=cyan' if is_dark else 'outlined dense color=blue').classes('w-full')

                            def apply_regex():
                                self.sub['options']['rename_pattern'] = pat.value
                                self.sub['options']['rename_replacement'] = rep.value
                                self.update_preview()
                                safe_notify('预览已刷新', 'positive')

                            ui.button('刷新预览', on_click=apply_regex).props('unelevated dense size=sm color=blue').classes('w-full bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55' if is_dark else 'w-full bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200')

                with ui.column().classes('w-[35%] h-full bg-[#0f172a] flex flex-col' if is_dark else 'w-[35%] h-full bg-slate-50 flex flex-col'):
                    with ui.row().classes('w-full p-3 border-b border-slate-700 bg-[#1e293b] items-center justify-between shadow-sm z-10' if is_dark else 'w-full p-3 border-b border-slate-300 bg-white items-center justify-between shadow-sm z-10'):
                        ui.label('已选节点清单').classes('font-bold text-slate-200' if is_dark else 'font-bold text-slate-800')
                        with ui.row().classes('items-center gap-2'):
                            ui.label('').bind_text_from(self, 'selected_ids', lambda x: f"{len(x)}").classes('text-slate-400')
                            ui.button('清空全部', icon='delete_forever', on_click=self.clear_all_selected).props('flat dense size=sm color=red')

                    with ui.scroll_area().classes('w-full flex-grow p-2'):
                        self.preview_container = ui.column().classes('w-full gap-1')
                        self.update_preview()

            with ui.row().classes('w-full p-3 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14] justify-end gap-3 flex-shrink-0' if is_dark else 'w-full p-3 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] justify-end gap-3 flex-shrink-0'):
                async def save_all():
                    if not self.sub.get('name'):
                        return safe_notify('名称不能为空', 'negative')
                    self.sub['nodes'] = self.selected_ids

                    found = False
                    for i, s in enumerate(SUBS_CACHE):
                        if s.get('token') == self.sub['token']:
                            SUBS_CACHE[i] = self.sub
                            found = True
                            break
                    if not found:
                        SUBS_CACHE.append(self.sub)

                    await save_subs()
                    await load_subs_view()
                    dlg.close()
                    safe_notify('✅ 订阅保存成功', 'positive')

                ui.button('保存配置', icon='save', on_click=save_all).props('flat').classes('bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm px-5' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 font-black rounded-sm px-5')

    def _preload_data(self):
        self.all_nodes_map = {}
        for srv in SERVERS_CACHE:
            nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
            for n in nodes:
                key = f"{srv['url']}|{n['id']}"
                n['_server_name'] = srv['name']
                self.all_nodes_map[key] = n
                
        for inode in INDEPENDENT_NODES_CACHE:
            key = f"independent|{inode['id']}"
            inode['_server_name'] = '独立节点'
            self.all_nodes_map[key] = inode

    async def _render_node_tree(self):
        self.list_container.clear()
        self.ui_groups = {}
        self.server_expansions = {}
        self.server_items = {}

        grouped = {}
        for srv in SERVERS_CACHE:
            nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
            if not nodes:
                continue

            g_name = srv.get('group', '默认分组')
            if g_name in ['默认分组', '自动注册', '未分组', '自动导入']:
                try:
                    g_name = detect_country_group(srv.get('name'), srv)
                except:
                    pass

            if g_name not in grouped:
                grouped[g_name] = []
            grouped[g_name].append({'server': srv, 'nodes': nodes, 'type': 'server'})

        if INDEPENDENT_NODES_CACHE:
            g_name = '独立节点'
            if g_name not in grouped:
                grouped[g_name] = []
            grouped[g_name].append({'server': {'name': '独立节点库', 'url': 'independent'}, 'nodes': INDEPENDENT_NODES_CACHE, 'type': 'independent'})

        sorted_groups = sorted(grouped.keys())
        with self.list_container:
            for i, g_name in enumerate(sorted_groups):
                if i % 2 == 0:
                    await asyncio.sleep(0.01)

                exp = ui.expansion(g_name, icon='folder', value=True).classes('w-full border border-slate-700 rounded bg-[#1e293b]' if self.is_dark else 'w-full border border-slate-300 rounded bg-white').props('header-class="bg-[#172033] text-slate-300 text-sm font-bold p-2 min-h-0"' if self.is_dark else 'header-class="bg-sky-50 text-slate-700 text-sm font-bold p-2 min-h-0"')

                self.server_expansions[g_name] = exp
                self.server_items[g_name] = []

                with exp:
                    with ui.column().classes('w-full p-2 gap-2'):
                        for item in grouped[g_name]:
                            srv = item['server']
                            search_key = f"{srv['name']}".lower()
                            container = ui.column().classes('w-full gap-1')

                            with container:
                                server_header = ui.row().classes('w-full items-center gap-1 mt-1 px-1')
                                with server_header:
                                    ui.icon('dns', size='xs').classes('text-blue-400' if self.is_dark else 'text-sky-600')
                                    ui.label(srv['name']).classes('text-xs font-bold text-slate-500 truncate' if self.is_dark else 'text-xs font-bold text-slate-700 truncate')

                                for n in item['nodes']:
                                    key = f"{srv['url']}|{n['id']}"
                                    is_checked = key in self.selected_ids
                                    self.server_items[g_name].append(key)

                                    with ui.row().classes('w-full items-center pl-2 py-1 hover:bg-slate-700 rounded cursor-pointer transition border border-transparent' if self.is_dark else 'w-full items-center pl-2 py-1 hover:bg-sky-50 rounded cursor-pointer transition border border-transparent') as row:
                                        chk = ui.checkbox(value=is_checked).props('dense size=xs dark color=blue' if self.is_dark else 'dense size=xs color=blue')
                                        chk.disable()
                                        row.on('click', lambda _, k=key: self.toggle_node_from_left(k))

                                        ui.label(n.get('remark', '未命名')).classes('text-xs text-slate-300 truncate flex-grow' if self.is_dark else 'text-xs text-slate-700 truncate flex-grow')

                                        full_text = f"{search_key} {n.get('remark','')} {n.get('protocol','')}".lower()

                                        self.ui_groups[key] = {
                                            'row': row, 'chk': chk, 'text': full_text,
                                            'group_name': g_name, 'header': server_header,
                                            'container': container
                                        }

    def toggle_node_from_left(self, key):
        if key in self.selected_ids:
            self.remove_node(key)
        else:
            self.selected_ids.append(key)
            self.update_preview()
            if key in self.ui_groups:
                self.ui_groups[key]['chk'].value = True
                self.ui_groups[key]['row'].classes(add='bg-blue-900/30 border-blue-500/30', remove='border-transparent')

    def remove_node(self, key):
        if key in self.selected_ids:
            self.selected_ids.remove(key)
            self.update_preview()
            if key in self.ui_groups:
                self.ui_groups[key]['chk'].value = False
                self.ui_groups[key]['row'].classes(remove='bg-blue-900/30 border-blue-500/30', add='border-transparent')

    def clear_all_selected(self):
        for key in list(self.selected_ids):
            self.remove_node(key)

    def update_preview(self):
        self.preview_container.clear()
        pat = self.sub.get('options', {}).get('rename_pattern', '')
        rep = self.sub.get('options', {}).get('rename_replacement', '')

        with self.preview_container:
            if not self.selected_ids:
                with ui.column().classes('w-full items-center mt-10 text-slate-600 gap-2'):
                    ui.icon('shopping_cart', size='3rem')
                    ui.label('清单为空').classes('text-sm')
                return

            with ui.column().classes('w-full gap-1'):
                for idx, key in enumerate(self.selected_ids):
                    node = self.all_nodes_map.get(key)
                    if not node:
                        continue

                    original_name = node.get('remark', 'Unknown')
                    final_name = original_name
                    if pat:
                        try:
                            import re
                            final_name = re.sub(pat, rep, original_name)
                        except:
                            pass

                    with ui.row().classes('w-full items-center p-1.5 bg-[#1e293b] border border-slate-700 rounded shadow-sm group hover:border-red-500 transition' if self.is_dark else 'w-full items-center p-1.5 bg-white border border-slate-300 rounded shadow-sm group hover:border-rose-300 transition'):
                        ui.label(str(idx + 1)).classes('text-[10px] text-slate-500 w-5 text-center')
                        chk = ui.checkbox(value=True).props('dense size=xs color=green dark' if self.is_dark else 'dense size=xs color=green')
                        chk.on_value_change(lambda e, k=key: self.remove_node(k) if not e.value else None)

                        with ui.column().classes('gap-0 leading-none flex-grow ml-1'):
                            if final_name != original_name:
                                ui.label(final_name).classes('text-xs font-bold text-blue-400' if self.is_dark else 'text-xs font-bold text-sky-700')
                                ui.label(original_name).classes('text-[9px] text-slate-500 line-through')
                            else:
                                ui.label(final_name).classes('text-xs font-bold text-slate-300' if self.is_dark else 'text-xs font-bold text-slate-800')

                        ui.button(icon='close', on_click=lambda _, k=key: self.remove_node(k)).props('flat dense size=xs color=red').classes('opacity-0 group-hover:opacity-100 transition')

    def sort_nodes(self, mode):
        if not self.selected_ids:
            return safe_notify('列表为空', 'warning')
        objs = []
        for k in self.selected_ids:
            n = self.all_nodes_map.get(k)
            if n:
                objs.append({'key': k, 'name': n.get('remark', '').lower()})

        if mode == 'name_asc':
            objs.sort(key=lambda x: x['name'])
        elif mode == 'name_desc':
            objs.sort(key=lambda x: x['name'], reverse=True)
        elif mode == 'random':
            import random
            random.shuffle(objs)
        elif mode == 'reverse':
            objs.reverse()

        self.selected_ids = [x['key'] for x in objs]
        self.update_preview()
        safe_notify(f'已按 {mode} 重新排序', 'positive')

    def on_search(self, e):
        txt = str(e.value).lower().strip()

        visible_groups = set()
        visible_headers = set()

        for key, item in self.ui_groups.items():
            visible = (not txt) or (txt in item['text'])
            item['row'].set_visibility(visible)
            if visible:
                visible_groups.add(item['group_name'])
                visible_headers.add(item['header'])

        for g_name, exp in self.server_expansions.items():
            is_group_visible = g_name in visible_groups
            exp.set_visibility(is_group_visible)
            if txt and is_group_visible:
                exp.value = True

        all_headers = set(item['header'] for item in self.ui_groups.values())
        for header in all_headers:
            header.set_visibility(header in visible_headers)

    def batch_select(self, val):
        count = 0
        for key, item in self.ui_groups.items():
            if item['row'].visible:
                if val:
                    if key not in self.selected_ids:
                        self.selected_ids.append(key)
                        item['chk'].value = True
                        item['row'].classes(add='bg-blue-900/30 border-blue-500/30', remove='border-transparent')
                        count += 1
                else:
                    if key in self.selected_ids:
                        self.selected_ids.remove(key)
                        item['chk'].value = False
                        item['row'].classes(remove='bg-blue-900/30 border-blue-500/30', add='border-transparent')
                        count += 1

        if count > 0:
            self.update_preview()
            safe_notify(f"已{'添加' if val else '移除'} {count} 个节点", "positive")
        else:
            safe_notify("当前没有可操作的节点", "warning")


def open_advanced_sub_editor(sub_data=None):
    with ui.dialog() as d:
        AdvancedSubEditor(sub_data).ui(d)
        d.open()
