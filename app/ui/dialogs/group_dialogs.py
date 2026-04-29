import asyncio
import json

from nicegui import app, ui

from app.core.state import ADMIN_CONFIG, CURRENT_VIEW_STATE, SERVERS_CACHE
from app.storage.repositories import save_admin_config, save_servers
from app.ui.common.notifications import safe_notify
from app.utils.geo import detect_country_group


def _group_theme():
    is_dark = bool(app.storage.user.get('is_dark', True))
    return {
        'is_dark': is_dark,
        'card': 'bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]',
        'header': 'bg-gradient-to-r from-[#0a1526] to-[#050a14] border-[#1e3a5f]/60' if is_dark else 'bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] border-slate-300/90',
        'body': 'bg-[#030712]' if is_dark else 'bg-[#f8fbff]',
        'subbar': 'bg-[#070b14] border-[#1e3a5f]/45' if is_dark else 'bg-sky-50 border-slate-300/90',
        'row_idle': 'bg-[#0a1120] border-[#1e3a5f]/45' if is_dark else 'bg-white border-slate-300/90',
        'row_active': 'bg-cyan-950/25 border-cyan-500/45' if is_dark else 'bg-sky-100 border-sky-300',
        'title': 'text-slate-100' if is_dark else 'text-slate-800',
        'text': 'text-slate-300' if is_dark else 'text-slate-700',
        'muted': 'text-slate-400' if is_dark else 'text-slate-600',
        
        # 修复点：移除了会导致底层 ast 解析崩溃的 bg-color="[#050b14]"
        'input': 'outlined dense dark color=cyan standout' if is_dark else 'outlined dense color=blue',
        'input_clearable': 'outlined dense clearable dark color=cyan standout' if is_dark else 'outlined dense clearable color=blue',
        
        'checkbox_blue': 'dense dark color=blue' if is_dark else 'dense color=blue',
        'checkbox_green': 'dense dark color=green' if is_dark else 'dense color=green',
        'btn_primary': 'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm px-4' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 font-black rounded-sm px-4',
        'btn_secondary': 'bg-[#0a1120] text-slate-300 border border-[#1e3a5f]/45 rounded-sm px-3' if is_dark else 'bg-white text-slate-600 border border-slate-300 rounded-sm px-3',
        'btn_accent': 'bg-cyan-950/40 text-cyan-300 border border-cyan-500/40 rounded-sm px-3' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 rounded-sm px-3',
        'btn_success': 'bg-emerald-950/45 text-emerald-300 border border-emerald-500/45 hover:bg-emerald-900/55 rounded-sm font-black' if is_dark else 'bg-emerald-100 text-emerald-700 border border-emerald-300 hover:bg-emerald-200 rounded-sm font-black',
        'btn_close': 'text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100',
        'btn_outline': 'text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm',
    }


def open_quick_group_create_dialog(callback=None):
    theme = _group_theme()
    selection_map = {s['url']: False for s in SERVERS_CACHE}
    ui_rows = {}

    with ui.dialog() as d, ui.card().classes(f'w-full max-w-lg h-[85vh] flex flex-col p-0 overflow-hidden rounded-sm {theme["card"]}'):
        with ui.column().classes(f'w-full p-4 border-b gap-3 flex-shrink-0 {theme["header"]}'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('新建分组 (标签模式)').classes(f'text-lg font-black tracking-wide {theme["title"]}')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes(theme['btn_close'])

            name_input = ui.input('分组名称', placeholder='例如: 甲骨文云').props(theme['input']).classes('w-full')
            search_input = ui.input(placeholder='🔍 搜索筛选服务器...').props(theme['input_clearable']).classes('w-full')

            def on_search(e):
                keyword = str(e.value).lower().strip()
                for url, item in ui_rows.items():
                    is_match = keyword in item['search_text']
                    item['row'].set_visibility(is_match)
            search_input.on_value_change(on_search)

        with ui.column().classes(f'w-full flex-grow overflow-hidden relative {theme["body"]}'):
            with ui.row().classes(f'w-full p-2 justify-between items-center border-b flex-shrink-0 {theme["subbar"]}'):
                ui.label('勾选加入该组:').classes(f'text-xs font-bold ml-2 {theme["muted"]}')
                with ui.row().classes('gap-1'):
                    ui.button('全选', on_click=lambda: toggle_visible(True)).props('flat dense size=xs').classes(theme['btn_accent'])
                    ui.button('清空', on_click=lambda: toggle_visible(False)).props('flat dense size=xs').classes(theme['btn_secondary'])

            with ui.scroll_area().classes('w-full flex-grow p-2'):
                with ui.column().classes('w-full gap-1'):
                    try:
                        sorted_srv = sorted(SERVERS_CACHE, key=lambda x: str(x.get('name', '')))
                    except:
                        sorted_srv = SERVERS_CACHE

                    for s in sorted_srv:
                        search_key = f"{s['name']} {s['url']}".lower()

                        with ui.row().classes('w-full items-center p-2 rounded-sm border transition cursor-pointer group ' + theme['row_idle'] + (' hover:bg-cyan-950/20' if theme['is_dark'] else ' hover:bg-sky-50')) as row:
                            chk = ui.checkbox(value=False).props(theme['checkbox_blue'])
                            chk.on('click.stop', lambda: None)
                            chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                            row.on('click', lambda _, c=chk: c.set_value(not c.value))

                            ui.label(s['name']).classes(f'text-sm font-black ml-2 truncate flex-grow select-none group-hover:text-cyan-300 {theme["text"]}')

                            detected = "未知"
                            try:
                                detected = detect_country_group(s['name'], s)
                            except:
                                pass
                            ui.label(detected).classes('text-xs text-slate-500 font-mono')

                        ui_rows[s['url']] = {'row': row, 'chk': chk, 'search_text': search_key}

            def toggle_visible(state):
                count = 0
                for item in ui_rows.values():
                    if item['row'].visible:
                        item['chk'].value = state
                        count += 1
                if state and count > 0:
                    safe_notify(f"已选中 {count} 个", "positive")

        async def save():
            new_name = name_input.value.strip()
            if not new_name:
                return safe_notify('名称不能为空', 'warning')
            existing = set(ADMIN_CONFIG.get('custom_groups', []))
            if new_name in existing:
                return safe_notify('分组已存在', 'warning')
            if 'custom_groups' not in ADMIN_CONFIG:
                ADMIN_CONFIG['custom_groups'] = []
            ADMIN_CONFIG['custom_groups'].append(new_name)
            await save_admin_config()

            count = 0
            for s in SERVERS_CACHE:
                if selection_map.get(s['url'], False):
                    if 'tags' not in s:
                        s['tags'] = []
                    if new_name not in s['tags']:
                        s['tags'].append(new_name)
                        count += 1
                    if s.get('group') == new_name:
                        s['group'] = detect_country_group(s['name'], None)

            if count > 0:
                await save_servers()
            from app.ui.components.sidebar import render_sidebar_content

            render_sidebar_content.refresh()
            safe_notify(f'✅ 分组 "{new_name}" 创建成功', 'positive')
            d.close()
            if callback:
                await callback(new_name)

        with ui.row().classes(f'w-full p-4 border-t justify-end gap-2 flex-shrink-0 {theme["header"]}'):
            ui.button('取消', on_click=d.close).props('outline color=grey').classes(theme['btn_outline'])
            ui.button('创建并保存', on_click=save).props('flat').classes(theme['btn_primary'])
    d.open()


def open_group_sort_dialog():
    theme = _group_theme()
    current_groups = ADMIN_CONFIG.get('probe_custom_groups', [])
    if not current_groups:
        safe_notify("暂无自定义视图", "warning")
        return

    dialog_card_cls = 'w-[420px] max-w-[95vw] h-[60vh] flex flex-col p-0 gap-0 overflow-hidden rounded-sm'
    dialog_card_style = (
        'background: rgba(7, 11, 20, 0.8); border-color: rgba(30, 58, 95, 0.55); '
        'backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);'
        if theme['is_dark'] else
        'background: rgba(255, 255, 255, 0.8); border-color: rgba(203, 213, 225, 0.9); '
        'backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);'
    )

    with ui.dialog() as d, ui.card().classes(f'{dialog_card_cls} {theme["card"]}').style(dialog_card_style):
        with ui.row().classes(f'w-full p-4 border-b justify-between items-center flex-shrink-0 {theme["header"]}'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('sort').classes('text-lg text-cyan-400' if theme['is_dark'] else 'text-lg text-sky-600')
                with ui.column().classes('gap-0'):
                    ui.label('视图排序').classes(f'font-black text-base tracking-wide {theme["title"]}')
                    ui.label('拖动分组即可调整显示顺序').classes(f'text-[11px] {theme["muted"]}')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes(theme['btn_close'])

        with ui.scroll_area().classes('w-full flex-grow p-3'):
            with ui.column().props('id=probe-group-sort-list').classes('w-full gap-2'):
                for i, name in enumerate(current_groups):
                    with ui.element('div').props(f'data-group-name={json.dumps(name, ensure_ascii=False)}').classes('probe-group-sort-item w-full'):
                        with ui.row().classes(
                                'probe-group-sort-handle w-full p-3 items-center gap-3 border rounded-sm shadow-sm transition-all cursor-grab active:cursor-grabbing select-none ' + theme['row_idle'] + (' hover:border-cyan-500/45' if theme['is_dark'] else ' hover:border-sky-300')):
                            ui.icon('drag_indicator').classes('text-slate-500')
                            ui.label(str(i + 1)).classes('text-xs text-slate-500 font-mono w-4 text-center font-bold')
                            ui.label(name).classes(f'font-bold flex-grow text-sm truncate {theme["text"]}')

        async def save():
            sorted_names = await ui.run_javascript('''
                return Array.from(document.querySelectorAll('#probe-group-sort-list .probe-group-sort-item'))
                    .map(el => el.dataset.groupName)
                    .filter(Boolean);
            ''', timeout=3.0)

            if not isinstance(sorted_names, list) or not sorted_names:
                sorted_names = list(current_groups)

            ADMIN_CONFIG['probe_custom_groups'] = sorted_names
            await save_admin_config()
            safe_notify("✅ 分组顺序已更新", "positive")
            d.close()
            try:
                from app.ui.pages.probe_page import render_probe_page

                await render_probe_page()
            except:
                pass

        with ui.row().classes(f'w-full p-4 border-t flex-shrink-0 {theme["header"]}'):
            ui.button('保存顺序', icon='save', on_click=save).props('flat').classes('w-full ' + theme['btn_primary'].replace(' px-4', ''))

    ui.run_javascript('''
        (function() {
            function ensureStyle() {
                if (document.getElementById('xf-probe-sort-style')) return;
                const style = document.createElement('style');
                style.id = 'xf-probe-sort-style';
                style.textContent = `
                    .xf-probe-drag-ghost { opacity: 0.22 !important; }
                    .xf-probe-drag-chosen { transform: scale(1.01); }
                    .xf-probe-drag-active {
                        opacity: 0.96 !important;
                        transform: scale(1.015);
                        filter: drop-shadow(0 14px 24px rgba(15, 23, 42, 0.28));
                        z-index: 9999 !important;
                    }
                    .probe-group-sort-handle {
                        touch-action: none;
                        user-select: none;
                        -webkit-user-select: none;
                    }
                `;
                document.head.appendChild(style);
            }

            function ensureSortableScript() {
                if (window.Sortable) return Promise.resolve(window.Sortable);
                if (!window.__xfProbeSortableLoading) {
                    window.__xfProbeSortableLoading = new Promise((resolve, reject) => {
                        const existing = document.getElementById('xf-probe-sortable-script');
                        if (existing) {
                            existing.addEventListener('load', () => resolve(window.Sortable));
                            existing.addEventListener('error', reject);
                            return;
                        }
                        const script = document.createElement('script');
                        script.id = 'xf-probe-sortable-script';
                        script.src = 'https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js';
                        script.onload = () => resolve(window.Sortable);
                        script.onerror = reject;
                        document.head.appendChild(script);
                    });
                }
                return window.__xfProbeSortableLoading;
            }

            function bootSortable(retries) {
                const container = document.getElementById('probe-group-sort-list');
                const items = container ? container.querySelectorAll('.probe-group-sort-item') : [];
                if (!container || !items.length) {
                    if (retries > 0) setTimeout(() => bootSortable(retries - 1), 180);
                    return;
                }

                ensureSortableScript().then(() => {
                    if (!window.Sortable) return;
                    if (window.__xfProbeGroupSortable) {
                        window.__xfProbeGroupSortable.destroy();
                    }
                    window.__xfProbeGroupSortable = new window.Sortable(container, {
                        animation: 220,
                        easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
                        handle: '.probe-group-sort-handle',
                        draggable: '.probe-group-sort-item',
                        ghostClass: 'xf-probe-drag-ghost',
                        chosenClass: 'xf-probe-drag-chosen',
                        dragClass: 'xf-probe-drag-active',
                        forceFallback: true,
                        fallbackOnBody: true,
                        swapThreshold: 0.65,
                        fallbackTolerance: 3,
                        scroll: true,
                        bubbleScroll: true,
                        scrollSensitivity: 70,
                        scrollSpeed: 18,
                    });
                }).catch(err => console.error('[ProbeGroupSort] SortableJS 加载失败', err));
            }

            ensureStyle();
            setTimeout(() => bootSortable(8), 120);
        })();
    ''')
    d.open()



async def _open_server_dialog_by_server(server, client=None):
    from app.ui.dialogs.server_dialog import open_server_dialog

    if client:
        with client:
            await open_server_dialog(SERVERS_CACHE.index(server))
        return

    await open_server_dialog(SERVERS_CACHE.index(server))


def open_unified_group_manager(mode='manage'):
    theme = _group_theme()
    if 'probe_custom_groups' not in ADMIN_CONFIG:
        ADMIN_CONFIG['probe_custom_groups'] = []

    state = {
        'current_group': None,
        'selected_urls': set(),
        'checkboxes': {},
        'page': 1,
        'search_text': ''
    }

    view_list_container = None
    server_list_container = None
    title_input = None
    pagination_ref = None

    with ui.dialog() as d, ui.card().classes(f'w-full max-w-5xl h-[90vh] flex flex-col p-0 gap-0 overflow-hidden rounded-sm {theme["card"]}'):
        with ui.row().classes(f'w-full p-3 border-b items-center gap-2 overflow-x-auto flex-shrink-0 {theme["header"]}'):
            ui.label('视图列表:').classes('font-black text-cyan-400 mr-2 text-xs tracking-wider' if theme['is_dark'] else 'font-black text-sky-700 mr-2 text-xs tracking-wider')
            ui.button('➕ 新建分组', on_click=lambda: load_group_data(None)).props('flat size=sm').classes(theme['btn_success'])
            ui.separator().props('vertical dark').classes('mx-2 h-6 bg-[#1e3a5f]/60')
            view_list_container = ui.row().classes('gap-2 items-center flex-nowrap')
            ui.space()
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes(theme['btn_close'])

        with ui.row().classes(f'w-full p-4 border-b items-center gap-4 flex-shrink-0 wrap {theme["subbar"]}'):
            title_input = ui.input('视图名称', placeholder='请输入分组名称...').props(theme['input']).classes('min-w-[200px] flex-grow font-bold')
            ui.input(placeholder='🔍 搜索服务器...', on_change=lambda e: update_search(e.value)).props(theme['input']).classes('w-48')

            with ui.row().classes('gap-2'):
                ui.button('全选本页', on_click=lambda: toggle_page_all(True)).props('flat dense size=sm').classes(theme['btn_accent'])
                ui.button('清空本页', on_click=lambda: toggle_page_all(False)).props('flat dense size=sm').classes(theme['btn_secondary'])

        with ui.scroll_area().classes(f'w-full flex-grow p-4 {theme["body"]}'):
            server_list_container = ui.column().classes('w-full gap-2')

        with ui.row().classes(f'w-full p-2 justify-center border-t {theme["subbar"]}'):
            pagination_ref = ui.row()

        with ui.row().classes(f'w-full p-4 border-t justify-between items-center flex-shrink-0 {theme["header"]}'):
            ui.button('删除此视图', icon='delete', on_click=lambda: delete_current_group()).props('flat').classes('text-rose-400 hover:bg-rose-950/30 hover:text-rose-300 rounded-sm')
            ui.button('保存当前配置', icon='save', on_click=lambda: save_current_group()).props('flat').classes(theme['btn_primary'])

    def update_search(val):
        state['search_text'] = str(val).lower().strip()
        state['page'] = 1
        render_servers()

    def render_views():
        view_list_container.clear()
        groups = ADMIN_CONFIG.get('probe_custom_groups', [])
        with view_list_container:
            for g in groups:
                is_active = (g == state['current_group'])
                btn_props = 'flat' if is_active else 'outline color=grey-5 text-color=grey-4'
                btn = ui.button(g, on_click=lambda _, name=g: load_group_data(name)).props(f'{btn_props} size=sm')
                if is_active:
                    btn.classes('bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 font-black rounded-sm')
                else:
                    btn.classes('text-slate-300 border-[#1e3a5f]/45 hover:bg-cyan-950/20 hover:text-cyan-300 rounded-sm' if theme['is_dark'] else 'text-slate-700 border-slate-300 hover:bg-sky-100 hover:text-sky-700 rounded-sm')

    def load_group_data(group_name):
        state['current_group'] = group_name
        state['page'] = 1
        state['selected_urls'] = set()

        if group_name:
            for s in SERVERS_CACHE:
                if (group_name in s.get('tags', [])) or (s.get('group') == group_name):
                    state['selected_urls'].add(s['url'])

        render_views()
        title_input.value = group_name if group_name else ''
        if not group_name:
            title_input.run_method('focus')
        render_servers()

    def render_servers():
        server_list_container.clear()
        pagination_ref.clear()
        state['checkboxes'] = {}

        if not SERVERS_CACHE:
            with server_list_container:
                ui.label('暂无服务器').classes('text-center text-slate-500 mt-10 w-full')
            return

        all_srv = SERVERS_CACHE
        if state['search_text']:
            all_srv = [s for s in all_srv if state['search_text'] in s.get('name', '').lower() or state['search_text'] in s.get('url', '').lower()]

        try:
            sorted_servers = sorted(all_srv, key=lambda x: str(x.get('name', '')))
        except:
            sorted_servers = all_srv

        PAGE_SIZE = 48
        total_items = len(sorted_servers)
        total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
        if state['page'] > total_pages:
            state['page'] = 1
        if state['page'] < 1:
            state['page'] = 1

        start_idx = (state['page'] - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_items = sorted_servers[start_idx:end_idx]

        with server_list_container:
            ui.label(f"共 {total_items} 台 (第 {state['page']}/{total_pages} 页)").classes('text-xs text-slate-400 mb-2')

            with ui.grid().classes('w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2'):
                for s in current_page_items:
                    url = s.get('url')
                    if not url:
                        continue
                    is_checked = url in state['selected_urls']

                    bg_cls = 'bg-cyan-950/25 border-cyan-500/45' if is_checked else ('bg-[#0a1120] border-[#1e3a5f]/45' if theme['is_dark'] else 'bg-white border-slate-300/90')

                    with ui.row().classes(f'items-center p-2 border rounded-sm cursor-pointer transition {bg_cls}' + (' hover:bg-cyan-950/20' if theme['is_dark'] else ' hover:bg-sky-50')) as row:
                        chk = ui.checkbox(value=is_checked).props('dense dark color=green' if theme['is_dark'] else 'dense color=green')
                        state['checkboxes'][url] = chk

                        def toggle_row(c=chk, r=row, u=url):
                            c.value = not c.value
                            update_selection(u, c.value)
                            if c.value:
                                r.classes(add='bg-cyan-950/25 border-cyan-500/45' if theme['is_dark'] else 'bg-sky-100 border-sky-300', remove='bg-[#0a1120] border-[#1e3a5f]/45' if theme['is_dark'] else 'bg-white border-slate-300/90')
                            else:
                                r.classes(remove='bg-cyan-950/25 border-cyan-500/45' if theme['is_dark'] else 'bg-sky-100 border-sky-300', add='bg-[#0a1120] border-[#1e3a5f]/45' if theme['is_dark'] else 'bg-white border-slate-300/90')

                        row.on('click', toggle_row)
                        chk.on('click.stop', lambda _, c=chk, r=row, u=url: [update_selection(u, c.value),
                            r.classes(add='bg-cyan-950/25 border-cyan-500/45' if theme['is_dark'] else 'bg-sky-100 border-sky-300', remove='bg-[#0a1120] border-[#1e3a5f]/45' if theme['is_dark'] else 'bg-white border-slate-300/90') if c.value else r.classes(remove='bg-cyan-950/25 border-cyan-500/45' if theme['is_dark'] else 'bg-sky-100 border-sky-300', add='bg-[#0a1120] border-[#1e3a5f]/45' if theme['is_dark'] else 'bg-white border-slate-300/90')])

                        async def open_server_detail(server=s):
                            await _open_server_dialog_by_server(server, ui.context.client)

                        with ui.column().classes('gap-0 ml-2 overflow-hidden'):
                            name_label = ui.label(s.get('name', 'Unknown'))
                            if theme['is_dark']:
                                name_label.classes('text-sm font-black truncate cursor-pointer text-slate-200 hover:text-cyan-300')
                            else:
                                name_label.classes('text-sm font-black truncate cursor-pointer text-slate-800 hover:text-sky-700')
                            name_label.on('click.stop', lambda _, server=s: asyncio.create_task(open_server_detail(server)))
                            
                            if is_checked:
                                ui.label('已选中').classes('text-[10px] text-green-400 font-bold')
                            else:
                                ui.label(s.get('group', '')).classes('text-[10px] text-slate-500')

        if total_pages > 1:
            with pagination_ref:
                p = ui.pagination(1, total_pages, direction_links=True).props('dense color=blue text-color=slate-400 active-text-color=white')
                p.value = state['page']
                p.on('update:model-value', lambda e: [state.update({'page': e.args}), render_servers()])

    def update_selection(url, checked):
        if checked:
            state['selected_urls'].add(url)
        else:
            state['selected_urls'].discard(url)

    def toggle_page_all(val):
        for url in state['checkboxes'].keys():
            if val:
                state['selected_urls'].add(url)
            else:
                state['selected_urls'].discard(url)
        render_servers()

    async def save_current_group():
        old_name = state['current_group']
        new_name = title_input.value.strip()
        if not new_name:
            return safe_notify("名称不能为空", "warning")

        groups = ADMIN_CONFIG.get('probe_custom_groups', [])

        if not old_name:
            if new_name in groups:
                return safe_notify("名称已存在", "negative")
            groups.append(new_name)
        elif new_name != old_name:
            if new_name in groups:
                return safe_notify("名称已存在", "negative")
            idx = groups.index(old_name)
            groups[idx] = new_name
            for s in SERVERS_CACHE:
                if 'tags' in s and old_name in s['tags']:
                    s['tags'].remove(old_name)
                    s['tags'].append(new_name)

        for s in SERVERS_CACHE:
            if 'tags' not in s:
                s['tags'] = []
            if s['url'] in state['selected_urls']:
                if new_name not in s['tags']:
                    s['tags'].append(new_name)
            else:
                if new_name in s['tags']:
                    s['tags'].remove(new_name)

        ADMIN_CONFIG['probe_custom_groups'] = groups
        await save_admin_config()
        await save_servers()
        safe_notify(f"✅ 保存成功", "positive")
        load_group_data(new_name)
        try:
            from app.ui.pages.probe_page import render_probe_page

            await render_probe_page()
        except:
            pass

    async def delete_current_group():
        target = state['current_group']
        if not target:
            return
        if target in ADMIN_CONFIG.get('probe_custom_groups', []):
            ADMIN_CONFIG['probe_custom_groups'].remove(target)
            await save_admin_config()
        for s in SERVERS_CACHE:
            if 'tags' in s and target in s['tags']:
                s['tags'].remove(target)
        await save_servers()
        safe_notify("🗑️ 已删除", "positive")
        load_group_data(None)
        try:
            from app.ui.pages.probe_page import render_probe_page

            await render_probe_page()
        except:
            pass

    def init():
        render_views()
        load_group_data(None)

    ui.timer(0.1, init, once=True)
    d.open()


def open_combined_group_management(group_name):
    theme = _group_theme()
    ui_rows = {}

    with ui.dialog() as d, ui.card().classes(f'w-[95vw] max-w-[600px] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden rounded-sm {theme["card"]}'):
        with ui.row().classes(f'w-full justify-between items-center p-4 border-b flex-shrink-0 {theme["header"]}'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('settings', color='primary').classes('text-xl')
                ui.label(f'管理分组: {group_name}').classes(f'text-lg font-black tracking-wide {theme["title"]}')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes(theme['btn_close'])

        with ui.column().classes('w-full flex-grow overflow-hidden p-0'):
            with ui.column().classes(f'w-full p-4 border-b gap-3 flex-shrink-0 {theme["subbar"]}'):
                ui.label('分组名称').classes('text-xs font-bold text-slate-500 mb-[-5px]')
                name_input = ui.input(value=group_name).props(theme['input']).classes('w-full')

                ui.label('搜索筛选').classes('text-xs font-bold text-slate-500 mb-[-5px]')
                search_input = ui.input(placeholder='🔍 搜名称 / IP...').props(theme['input_clearable']).classes('w-full')

                def on_search(e):
                    keyword = str(e.value).lower().strip()
                    for url, item in ui_rows.items():
                        is_match = keyword in item['search_text']
                        item['row'].set_visibility(is_match)

                search_input.on_value_change(on_search)

            with ui.column().classes(f'w-full flex-grow overflow-hidden relative {theme["body"]}'):
                with ui.row().classes(f'w-full p-2 justify-between items-center border-b flex-shrink-0 {theme["subbar"]}'):
                    ui.label('成员选择:').classes(f'text-xs font-bold ml-2 {theme["muted"]}')
                    with ui.row().classes('gap-1'):
                        ui.button('全选 (当前)', on_click=lambda: toggle_visible(True)).props('flat dense size=xs').classes(theme['btn_accent'])
                        ui.button('清空', on_click=lambda: toggle_visible(False)).props('flat dense size=xs').classes(theme['btn_secondary'])

                with ui.scroll_area().classes('w-full flex-grow p-2'):
                    with ui.column().classes('w-full gap-1'):
                        selection_map = {}

                        try:
                            sorted_servers = sorted(SERVERS_CACHE, key=lambda x: str(x.get('name', '')))
                        except:
                            sorted_servers = SERVERS_CACHE

                        if not sorted_servers:
                            ui.label('暂无服务器数据').classes('w-full text-center text-slate-500 mt-4')

                        for s in sorted_servers:
                            tags = s.get('tags', [])
                            if not isinstance(tags, list):
                                tags = []
                            is_in_group = group_name in tags
                            if s.get('group') == group_name:
                                is_in_group = True

                            selection_map[s['url']] = is_in_group

                            ip_addr = s['url'].split('//')[-1].split(':')[0]
                            search_key = f"{s['name']} {ip_addr}".lower()

                            bg_cls = 'bg-cyan-950/25 border-cyan-500/45' if is_in_group else ('bg-[#0a1120] border-[#1e3a5f]/45' if theme['is_dark'] else 'bg-white border-slate-300/90')

                            with ui.row().classes(f'w-full items-center p-2 rounded-sm border transition cursor-pointer {bg_cls}' + (' hover:bg-cyan-950/20' if theme['is_dark'] else ' hover:bg-sky-50')) as row:
                                chk = ui.checkbox(value=is_in_group).props('dense dark color=green' if theme['is_dark'] else 'dense color=green')

                                def toggle_row(c=chk, r=row, u=s['url']):
                                    c.set_value(not c.value)
                                    if c.value:
                                        r.classes(add='bg-cyan-950/25 border-cyan-500/45' if theme['is_dark'] else 'bg-sky-100 border-sky-300', remove='bg-[#0a1120] border-[#1e3a5f]/45' if theme['is_dark'] else 'bg-white border-slate-300/90')
                                    else:
                                        r.classes(remove='bg-cyan-950/25 border-cyan-500/45' if theme['is_dark'] else 'bg-sky-100 border-sky-300', add='bg-[#0a1120] border-[#1e3a5f]/45' if theme['is_dark'] else 'bg-white border-slate-300/90')

                                row.on('click', toggle_row)
                                chk.on('click.stop', lambda: None)

                                chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))

                                async def open_server_detail(server=s):
                                    await _open_server_dialog_by_server(server, ui.context.client)

                                with ui.column().classes('gap-0 ml-2 flex-grow overflow-hidden'):
                                    with ui.row().classes('items-center gap-2'):
                                        name_label = ui.label(s['name'])
                                        if theme['is_dark']:
                                            name_label.classes('text-sm font-black truncate cursor-pointer text-slate-300 hover:text-cyan-300')
                                        else:
                                            name_label.classes('text-sm font-black truncate cursor-pointer text-slate-800 hover:text-sky-700')
                                        name_label.on('click.stop', lambda _, server=s: asyncio.create_task(open_server_detail(server)))

                                try:
                                    real_region = detect_country_group(s['name'], None)
                                    ui.label(real_region).classes('text-xs font-mono text-slate-500')
                                except:
                                    pass

                            ui_rows[s['url']] = {
                                'row': row,
                                'chk': chk,
                                'search_text': search_key
                            }

                def toggle_visible(state):
                    count = 0
                    for item in ui_rows.values():
                        if item['row'].visible:
                            item['chk'].value = state
                            if state:
                                item['row'].classes(add='bg-cyan-950/25 border-cyan-500/45' if theme['is_dark'] else 'bg-sky-100 border-sky-300', remove='bg-[#0a1120] border-[#1e3a5f]/45' if theme['is_dark'] else 'bg-white border-slate-300/90')
                            else:
                                item['row'].classes(remove='bg-cyan-950/25 border-cyan-500/45' if theme['is_dark'] else 'bg-sky-100 border-sky-300', add='bg-[#0a1120] border-[#1e3a5f]/45' if theme['is_dark'] else 'bg-white border-slate-300/90')
                            count += 1
                    if state and count > 0:
                        safe_notify(f"已选中当前显示的 {count} 个服务器", "positive")

        with ui.row().classes(f'w-full p-4 border-t justify-between items-center flex-shrink-0 {theme["header"]}'):
            async def delete_group():
                with ui.dialog() as confirm_d, ui.card().classes('w-[360px] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-rose-800/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if theme['is_dark'] else 'w-[360px] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-rose-300 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                    with ui.column().classes('w-full p-5 gap-3 bg-gradient-to-r from-[#19070d] to-[#0b0911] border-b border-rose-900/60' if theme['is_dark'] else 'w-full p-5 gap-3 bg-gradient-to-r from-rose-50 to-orange-50 border-b border-rose-200'):
                        ui.label(f'确定永久删除分组 "{group_name}"?').classes('font-black text-rose-300 text-lg tracking-wide' if theme['is_dark'] else 'font-black text-rose-700 text-lg tracking-wide')
                        ui.label('服务器将保留，仅移除此标签。').classes('text-xs text-slate-400' if theme['is_dark'] else 'text-xs text-slate-600')
                    with ui.row().classes('w-full justify-end p-4 bg-[#030712] gap-2' if theme['is_dark'] else 'w-full justify-end p-4 bg-white gap-2'):
                        ui.button('取消', on_click=confirm_d.close).props('outline color=grey').classes(theme['btn_outline'])

                        async def do_del():
                            if 'custom_groups' in ADMIN_CONFIG and group_name in ADMIN_CONFIG['custom_groups']:
                                ADMIN_CONFIG['custom_groups'].remove(group_name)

                            for s in SERVERS_CACHE:
                                if 'tags' in s and group_name in s['tags']:
                                    s['tags'].remove(group_name)
                                if s.get('group') == group_name:
                                    try:
                                        s['group'] = detect_country_group(s['name'], None)
                                    except:
                                        s['group'] = '默认分组'

                            await save_admin_config()
                            await save_servers()
                            confirm_d.close()
                            d.close()

                            from app.ui.components.sidebar import render_sidebar_content
                            from app.ui.pages.content_router import refresh_content

                            render_sidebar_content.refresh()
                            if CURRENT_VIEW_STATE.get('scope') == 'TAG' and CURRENT_VIEW_STATE.get('data') == group_name:
                                await refresh_content('ALL')
                            else:
                                safe_notify(f'分组 "{group_name}" 已删除', 'positive')

                        ui.button('确认删除', on_click=do_del).props('flat').classes('bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 font-black rounded-sm px-4' if theme['is_dark'] else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 font-black rounded-sm px-4')
                confirm_d.open()

            ui.button('删除分组', icon='delete', on_click=delete_group).props('flat').classes('text-rose-400 hover:bg-rose-950/30 hover:text-rose-300 rounded-sm')

            async def save_changes():
                new_name = name_input.value.strip()
                if not new_name:
                    return safe_notify('分组名称不能为空', 'warning')

                if new_name != group_name:
                    if 'custom_groups' in ADMIN_CONFIG:
                        if group_name in ADMIN_CONFIG['custom_groups']:
                            idx = ADMIN_CONFIG['custom_groups'].index(group_name)
                            ADMIN_CONFIG['custom_groups'][idx] = new_name
                        else:
                            ADMIN_CONFIG['custom_groups'].append(new_name)
                    await save_admin_config()

                for s in SERVERS_CACHE:
                    if 'tags' not in s:
                        s['tags'] = []
                    should_have_tag = selection_map.get(s['url'], False)

                    if should_have_tag:
                        if new_name not in s['tags']:
                            s['tags'].append(new_name)
                        if new_name != group_name and group_name in s['tags']:
                            s['tags'].remove(group_name)
                    else:
                        if new_name in s['tags']:
                            s['tags'].remove(new_name)
                        if group_name in s['tags']:
                            s['tags'].remove(group_name)

                await save_servers()
                d.close()
                from app.ui.components.sidebar import render_sidebar_content
                from app.ui.pages.content_router import refresh_content

                render_sidebar_content.refresh()

                if CURRENT_VIEW_STATE.get('scope') == 'TAG' and CURRENT_VIEW_STATE.get('data') == group_name:
                    await refresh_content('TAG', new_name, force_refresh=True)

                safe_notify('分组设置已保存', 'positive')

            ui.button('保存修改', icon='save', on_click=save_changes).props('flat').classes('bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm px-4')

    d.open()


def open_create_group_dialog():
    theme = _group_theme()
    with ui.dialog() as d, ui.card().classes(f'w-full max-w-sm flex flex-col gap-0 p-0 overflow-hidden rounded-sm {theme["card"]}'):
        with ui.row().classes(f'w-full justify-between items-center p-4 border-b {theme["header"]}'):
            ui.label('新建自定义分组').classes(f'text-lg font-black tracking-wide {theme["title"]}')
            ui.button(icon='close', on_click=d.close).props('flat round dense').classes(theme['btn_close'])

        with ui.column().classes(f'w-full p-4 gap-4 {theme["body"]}'):
            name_input = ui.input('分组名称', placeholder='例如: 微软云 / 生产环境').classes('w-full').props(theme['input'])

        async def save_new_group():
            new_name = name_input.value.strip()
            if not new_name:
                safe_notify("分组名称不能为空", "warning")
                return

            from app.services.server_ops import get_all_groups
            existing_groups = set(get_all_groups())
            if new_name in existing_groups:
                safe_notify("该分组已存在", "warning")
                return

            if 'custom_groups' not in ADMIN_CONFIG:
                ADMIN_CONFIG['custom_groups'] = []
            ADMIN_CONFIG['custom_groups'].append(new_name)
            await save_admin_config()

            d.close()
            from app.ui.components.sidebar import render_sidebar_content

            render_sidebar_content.refresh()
            safe_notify(f"已创建分组: {new_name}", "positive")

        with ui.row().classes(f'w-full justify-end gap-2 p-4 border-t {theme["header"]}'):
            ui.button('取消', on_click=d.close).props('outline color=grey').classes(theme['btn_outline'])
            ui.button('保存', on_click=save_new_group).props('flat').classes(theme['btn_primary'])
    d.open()
