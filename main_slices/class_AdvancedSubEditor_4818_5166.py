class AdvancedSubEditor:
    def __init__(self, sub_data=None):
        import copy
        if sub_data:
            self.sub = copy.deepcopy(sub_data)
        else:
            self.sub = {'name': '', 'token': str(uuid.uuid4()), 'nodes': [], 'options': {}}
            
        if 'options' not in self.sub: self.sub['options'] = {}
        
        # 核心数据：选中的节点ID (有序)
        self.selected_ids = list(self.sub.get('nodes', []))
        
        # 缓存映射与UI引用
        self.all_nodes_map = {} 
        self.ui_groups = {}
        self.server_expansions = {}
        self.server_items = {}
        self.search_text = ""    
        self.preview_container = None
        self.left_scroll = None
        self.list_container = None

    def ui(self, dlg):
        # 1. 加载数据
        self._preload_data()

        # 2. 渲染主弹窗 (深色背景: bg-[#1e293b], 边框: border-slate-700)
        with ui.card().classes('w-full max-w-6xl h-[90vh] flex flex-col p-0 overflow-hidden bg-[#1e293b] border border-slate-700 shadow-2xl'):
            
            # --- 顶部标题栏 ---
            with ui.row().classes('w-full p-4 border-b border-slate-700 bg-[#0f172a] justify-between items-center flex-shrink-0'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('tune', color='primary').classes('text-xl')
                    ui.label('订阅高级管理').classes('text-lg font-bold text-slate-200')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense color=grey')

            # --- 内容区 (三栏布局) ---
            with ui.row().classes('w-full flex-grow overflow-hidden gap-0'):
                
                # ================= 1. 左栏：节点仓库 (bg-[#0f172a]) =================
                with ui.column().classes('w-2/5 h-full border-r border-slate-700 flex flex-col bg-[#0f172a]'):
                    # 搜索栏
                    with ui.column().classes('w-full p-2 border-b border-slate-700 bg-[#1e293b] gap-2'):
                        ui.input(placeholder='🔍 搜索源节点...', on_change=self.on_search).props('outlined dense dark debounce="300"').classes('w-full')
                        
                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label('筛选结果操作:').classes('text-xs text-slate-500')
                            with ui.row().classes('gap-1'):
                                ui.button('全选', icon='add_circle', on_click=lambda: self.batch_select(True)) \
                                    .props('unelevated dense size=sm color=blue-7').tooltip('将搜索结果加入右侧')
                                
                                ui.button('清空', icon='remove_circle', on_click=lambda: self.batch_select(False)) \
                                    .props('flat dense size=sm color=grey-6').tooltip('从右侧移除搜索结果')

                    # 滚动列表
                    with ui.scroll_area().classes('w-full flex-grow p-2') as area:
                        self.left_scroll = area
                        self.list_container = ui.column().classes('w-full gap-2')
                        # 延迟渲染以避免卡顿
                        ui.timer(0.1, lambda: asyncio.create_task(self._render_node_tree()), once=True)

                # ================= 2. 中栏：功能区 (bg-[#1e293b]) =================
                with ui.column().classes('w-1/4 h-full border-r border-slate-700 flex flex-col bg-[#1e293b] overflow-y-auto'):
                    with ui.column().classes('w-full p-4 gap-4'):
                        
                        # A. 基础设置
                        ui.label('① 基础设置').classes('text-xs font-bold text-blue-400 uppercase')
                        ui.input('订阅名称', value=self.sub.get('name', '')) \
                            .bind_value_to(self.sub, 'name') \
                            .props('outlined dense dark').classes('w-full')
                        
                        with ui.row().classes('w-full gap-1'):
                            ui.input('Token', value=self.sub.get('token', '')) \
                                .bind_value_to(self.sub, 'token') \
                                .props('outlined dense dark').classes('flex-grow')
                            
                            ui.button(icon='refresh', on_click=lambda: self.sub.update({'token': str(uuid.uuid4())[:8]})).props('flat dense color=blue')

                        ui.separator().classes('bg-slate-700')

                        # B. 排序工具
                        ui.label('② 排序工具').classes('text-xs font-bold text-blue-400 uppercase')
                        with ui.grid().classes('w-full grid-cols-2 gap-2'):
                            ui.button('名称 A-Z', on_click=lambda: self.sort_nodes('name_asc')).props('outline dense size=sm color=slate-400')
                            ui.button('名称 Z-A', on_click=lambda: self.sort_nodes('name_desc')).props('outline dense size=sm color=slate-400')
                            ui.button('随机打乱', on_click=lambda: self.sort_nodes('random')).props('outline dense size=sm color=slate-400')
                            ui.button('列表倒序', on_click=lambda: self.sort_nodes('reverse')).props('outline dense size=sm color=slate-400')

                        ui.separator().classes('bg-slate-700')

                        # C. 批量重命名
                        ui.label('③ 批量重命名').classes('text-xs font-bold text-blue-400 uppercase')
                        with ui.column().classes('w-full gap-2 bg-[#0f172a] p-2 rounded border border-slate-700'):
                            opt = self.sub.get('options', {})
                            pat = ui.input('正则 (如: ^)', value=opt.get('rename_pattern', '')).props('outlined dense dark bg-color="slate-900"').classes('w-full')
                            rep = ui.input('替换 (如: VIP-)', value=opt.get('rename_replacement', '')).props('outlined dense dark bg-color="slate-900"').classes('w-full')
                            
                            def apply_regex():
                                self.sub['options']['rename_pattern'] = pat.value
                                self.sub['options']['rename_replacement'] = rep.value
                                self.update_preview()
                                safe_notify('预览已刷新', 'positive')

                            ui.button('刷新预览', on_click=apply_regex).props('unelevated dense size=sm color=blue').classes('w-full')

                # ================= 3. 右栏：已选清单 (bg-[#0f172a]) =================
                with ui.column().classes('w-[35%] h-full bg-[#0f172a] flex flex-col'):
                    with ui.row().classes('w-full p-3 border-b border-slate-700 bg-[#1e293b] items-center justify-between shadow-sm z-10'):
                        ui.label('已选节点清单').classes('font-bold text-slate-200')
                        with ui.row().classes('items-center gap-2'):
                            ui.label('').bind_text_from(self, 'selected_ids', lambda x: f"{len(x)}").classes('text-slate-400')
                            ui.button('清空全部', icon='delete_forever', on_click=self.clear_all_selected).props('flat dense size=sm color=red')

                    with ui.scroll_area().classes('w-full flex-grow p-2'):
                        self.preview_container = ui.column().classes('w-full gap-1')
                        self.update_preview() # 初始渲染

            # --- 底部保存 ---
            with ui.row().classes('w-full p-3 border-t border-slate-700 bg-[#0f172a] justify-end gap-3 flex-shrink-0'):
                async def save_all():
                    if not self.sub.get('name'): return safe_notify('名称不能为空', 'negative')
                    self.sub['nodes'] = self.selected_ids
                    
                    found = False
                    for i, s in enumerate(SUBS_CACHE):
                        if s.get('token') == self.sub['token']:
                            SUBS_CACHE[i] = self.sub; found = True; break
                    if not found: SUBS_CACHE.append(self.sub)
                    
                    await save_subs(); await load_subs_view(); dlg.close(); safe_notify('✅ 订阅保存成功', 'positive')

                ui.button('保存配置', icon='save', on_click=save_all).classes('bg-blue-600 text-white shadow-lg')

    # ================= 辅助方法 (完全补全) =================
    
    def _preload_data(self):
        """预加载所有节点数据并建立索引"""
        self.all_nodes_map = {}
        for srv in SERVERS_CACHE:
            # 兼容：如果有缓存数据则读取，否则读取自定义节点
            nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
            for n in nodes:
                key = f"{srv['url']}|{n['id']}"
                n['_server_name'] = srv['name']
                self.all_nodes_map[key] = n

    async def _render_node_tree(self):
        """渲染左侧服务器分组树 (耗时操作，需异步)"""
        self.list_container.clear()
        self.ui_groups = {}
        self.server_expansions = {}
        self.server_items = {}
        
        # 1. 整理分组数据
        grouped = {}
        for srv in SERVERS_CACHE:
            nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
            if not nodes: continue
            
            g_name = srv.get('group', '默认分组')
            # 尝试智能分组
            if g_name in ['默认分组', '自动注册', '未分组', '自动导入']:
                try: g_name = detect_country_group(srv.get('name'), srv)
                except: pass
            
            if g_name not in grouped: grouped[g_name] = []
            grouped[g_name].append({'server': srv, 'nodes': nodes})

        # 2. 渲染 UI
        sorted_groups = sorted(grouped.keys())
        with self.list_container:
            for i, g_name in enumerate(sorted_groups):
                # 防止一次性渲染过多卡死
                if i % 2 == 0: await asyncio.sleep(0.01)
                
                # 创建分组折叠面板 (深色)
                exp = ui.expansion(g_name, icon='folder', value=True).classes('w-full border border-slate-700 rounded bg-[#1e293b]').props('header-class="bg-[#172033] text-slate-300 text-sm font-bold p-2 min-h-0"')
                
                self.server_expansions[g_name] = exp
                self.server_items[g_name] = [] 
                
                with exp:
                    with ui.column().classes('w-full p-2 gap-2'):
                        for item in grouped[g_name]:
                            srv = item['server']
                            search_key = f"{srv['name']}".lower()
                            container = ui.column().classes('w-full gap-1')
                            
                            with container:
                                # 服务器标题
                                server_header = ui.row().classes('w-full items-center gap-1 mt-1 px-1')
                                with server_header:
                                    ui.icon('dns', size='xs').classes('text-blue-400')
                                    ui.label(srv['name']).classes('text-xs font-bold text-slate-500 truncate')

                                # 节点列表
                                for n in item['nodes']:
                                    key = f"{srv['url']}|{n['id']}"
                                    is_checked = key in self.selected_ids
                                    self.server_items[g_name].append(key)
                                    
                                    # 节点行：悬浮变亮
                                    with ui.row().classes('w-full items-center pl-2 py-1 hover:bg-slate-700 rounded cursor-pointer transition border border-transparent') as row:
                                        chk = ui.checkbox(value=is_checked).props('dense size=xs dark color=blue')
                                        chk.disable() 
                                        row.on('click', lambda _, k=key: self.toggle_node_from_left(k))
                                        
                                        ui.label(n.get('remark', '未命名')).classes('text-xs text-slate-300 truncate flex-grow')
                                        
                                        full_text = f"{search_key} {n.get('remark','')} {n.get('protocol','')}".lower()
                                        
                                        # 存储 UI 引用以便控制显隐
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
        """刷新右侧已选列表"""
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
                    if not node: continue
                    
                    original_name = node.get('remark', 'Unknown')
                    final_name = original_name
                    if pat:
                        try:
                            import re
                            final_name = re.sub(pat, rep, original_name)
                        except: pass
                    
                    # 预览行 (深色)
                    with ui.row().classes('w-full items-center p-1.5 bg-[#1e293b] border border-slate-700 rounded shadow-sm group hover:border-red-500 transition'):
                        ui.label(str(idx+1)).classes('text-[10px] text-slate-500 w-5 text-center')
                        chk = ui.checkbox(value=True).props('dense size=xs color=green dark')
                        chk.on_value_change(lambda e, k=key: self.remove_node(k) if not e.value else None)
                        
                        with ui.column().classes('gap-0 leading-none flex-grow ml-1'):
                            if final_name != original_name:
                                ui.label(final_name).classes('text-xs font-bold text-blue-400')
                                ui.label(original_name).classes('text-[9px] text-slate-500 line-through')
                            else:
                                ui.label(final_name).classes('text-xs font-bold text-slate-300')
                        
                        ui.button(icon='close', on_click=lambda _, k=key: self.remove_node(k)).props('flat dense size=xs color=red').classes('opacity-0 group-hover:opacity-100 transition')

    def sort_nodes(self, mode):
        if not self.selected_ids: return safe_notify('列表为空', 'warning')
        objs = []
        for k in self.selected_ids:
            n = self.all_nodes_map.get(k)
            if n: objs.append({'key': k, 'name': n.get('remark', '').lower()})
        
        if mode == 'name_asc': objs.sort(key=lambda x: x['name'])
        elif mode == 'name_desc': objs.sort(key=lambda x: x['name'], reverse=True)
        elif mode == 'random': import random; random.shuffle(objs)
        elif mode == 'reverse': objs.reverse()
        
        self.selected_ids = [x['key'] for x in objs]
        self.update_preview()
        safe_notify(f'已按 {mode} 重新排序', 'positive')

    def on_search(self, e):
        """左侧列表搜索过滤逻辑"""
        txt = str(e.value).lower().strip()
        
        visible_groups = set()
        visible_headers = set()
        
        # 1. 过滤具体节点行
        for key, item in self.ui_groups.items():
            visible = (not txt) or (txt in item['text'])
            item['row'].set_visibility(visible)
            if visible:
                visible_groups.add(item['group_name'])
                visible_headers.add(item['header'])
        
        # 2. 控制分组折叠面板
        for g_name, exp in self.server_expansions.items():
            is_group_visible = g_name in visible_groups
            exp.set_visibility(is_group_visible)
            if txt and is_group_visible:
                exp.value = True # 搜索时自动展开
        
        # 3. 控制服务器标题头
        all_headers = set(item['header'] for item in self.ui_groups.values())
        for header in all_headers:
            header.set_visibility(header in visible_headers)

    def batch_select(self, val):
        """批量全选/清空当前搜索结果"""
        count = 0
        for key, item in self.ui_groups.items():
            # 只操作当前可见的项
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