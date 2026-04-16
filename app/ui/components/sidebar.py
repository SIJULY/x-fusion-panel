import asyncio

from nicegui import app, ui

from app.core.logging import logger

from app.core.state import (
    ADMIN_CONFIG,
    CURRENT_VIEW_STATE,
    EXPANDED_GROUPS,
    REFRESH_CURRENT_NODES,
    SERVERS_CACHE,
    SIDEBAR_UI_REFS,
)
from app.storage.repositories import save_admin_config
from app.ui.common.dialogs_data import open_data_mgmt_dialog, open_global_settings_dialog
from app.ui.common.dialogs_settings import open_cloudflare_settings_dialog
from app.ui.dialogs.batch_ssh import BatchSSH
from app.ui.dialogs.bulk_edit import open_bulk_edit_dialog
from app.ui.dialogs.group_dialogs import (
    open_combined_group_management,
    open_quick_group_create_dialog,
)
from app.utils.formatters import smart_sort_key
from app.utils.geo import detect_country_group


batch_ssh_manager = BatchSSH()
_current_dragged_group = None


def _sidebar_theme():
    is_dark = bool(app.storage.user.get('is_dark', True))
    return {
        'is_dark': is_dark,
        'btn_keycap_base': 'border rounded-sm transition-all',
        'btn_name_text': '',
        'btn_settings_text': '',
        'top_btn': 'w-full border rounded-sm font-bold px-3 py-2 transition-all',
        'top_wrap': 'w-full p-4 border-b flex-shrink-0 relative overflow-hidden',
        'logo_text': 'hidden',
        'title': 'text-sm font-black tracking-widest uppercase z-10',
        'ip_wrap': 'items-center gap-1 px-2 py-0.5 rounded-sm border z-10',
        'ip_label': 'text-[11px] font-bold',
        'ip_value': 'text-[11px] font-mono font-bold',
        'scroll_wrap': 'w-full flex-grow overflow-y-auto p-2 gap-2',
        'group_action_base': 'flex-grow text-xs font-black rounded-sm border px-3 py-2 tracking-wide transition-all',
        'new_group_btn': '',
        'new_server_btn': '',
        'list_item': 'w-full items-center justify-between p-3 border rounded-sm mb-1 cursor-pointer group transition-all duration-200',
        'list_icon_box': 'p-1.5 rounded-sm border transition-colors',
        'list_icon': 'text-sm',
        'list_label': 'font-bold text-sm',
        'section_label': 'text-xs font-bold mt-4 mb-2 px-2 uppercase tracking-wider',
        'expansion_custom': 'w-full border rounded-sm mb-2 transition-all',
        'expansion_region': 'w-full border rounded-sm',
        'expansion_header_props': 'expand-icon-toggle',
        'drag_icon': 'cursor-move p-1 rounded transition-colors',
        'group_name': 'font-bold truncate text-sm',
        'icon_btn': '',
        'expansion_body': 'w-full gap-2 p-2 border-t',
        'flag_name': 'font-bold truncate',
        'bottom_wrap': 'w-full p-2 border-t mt-auto mb-4 gap-2 z-10',
        'bottom_btn': 'w-full text-xs font-bold border rounded-sm px-3 py-2 transition-all',
    }


async def on_server_click_handler(server):
    logger.info(f"[SidebarClick] on_server_click_handler called | server_url={server.get('url')} server_name={server.get('name')} current_view_before={CURRENT_VIEW_STATE}")
    current_scope = CURRENT_VIEW_STATE.get('scope')
    current_data = CURRENT_VIEW_STATE.get('data')

    is_same_server = False
    if current_scope == 'SINGLE' and current_data:
        try:
            if current_data.get('url') == server.get('url'):
                is_same_server = True
        except:
            pass

    if is_same_server:
        if REFRESH_CURRENT_NODES:
            res = REFRESH_CURRENT_NODES()
            if res and asyncio.iscoroutine(res):
                await res
        return

    from app.ui.pages.content_router import refresh_content

    await refresh_content('SINGLE', server)
    logger.info(f"[SidebarClick] on_server_click_handler done | server_url={server.get('url')} current_view_after={CURRENT_VIEW_STATE}")


def render_single_sidebar_row(s):
    theme = _sidebar_theme()
    btn_name_cls = f"{theme['btn_keycap_base']} flex-grow text-xs font-bold truncate px-3 py-2.5 {theme['btn_name_text']}"
    btn_settings_cls = f"{theme['btn_keycap_base']} w-10 py-2.5 px-0 flex items-center justify-center {theme['btn_settings_text']}"

    async def open_server_settings():
        await _open_server_dialog_by_server(s, ui.context.client)

    with ui.row().classes('w-full gap-2 no-wrap items-stretch') as row:
        ui.button(on_click=lambda _, s=s: on_server_click_handler(s)) \
            .bind_text_from(s, 'name') \
            .props('no-caps align=left flat') \
            .classes(btn_name_cls) \
            .style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); color: var(--xf-text-strong);')

        ui.button(icon='settings', on_click=open_server_settings) \
            .props('flat square size=sm') \
            .classes(btn_settings_cls).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); color: var(--xf-text-muted);').tooltip('配置 / 删除')

    SIDEBAR_UI_REFS['rows'][s['url']] = row
    return row


@ui.refreshable
def render_sidebar_content():
    global _current_dragged_group
    theme = _sidebar_theme()

    logger.info(f"[Sidebar] render_sidebar_content called | servers={len(SERVERS_CACHE)} before_clear_groups={len(SIDEBAR_UI_REFS.get('groups', {}))} before_clear_rows={len(SIDEBAR_UI_REFS.get('rows', {}))}")

    SIDEBAR_UI_REFS['groups'].clear()
    SIDEBAR_UI_REFS['rows'].clear()

    with ui.column().classes(theme['top_wrap']).style('border-color: var(--xf-card-border); background: linear-gradient(to bottom, var(--xf-soft-bg), var(--xf-panel-bg));'):
        ui.element('div').classes('absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNCwyMTEsMjM4LDAuMDcpIi8+PC9zdmc+")] opacity-100 pointer-events-none')
        ui.label('X-Fusion-pro').classes(theme['logo_text']).style('color: var(--xf-accent);')

        with ui.row().classes('w-full items-center mb-2 z-10 relative'):
            ui.label('控制中心').classes(theme['title']).style('color: var(--xf-accent);')

        with ui.column().classes('w-full gap-2 z-10 relative -mt-1'):
            ui.button('仪表盘', icon='dashboard', on_click=lambda: asyncio.create_task(_load_dashboard())).props('flat align=left').classes(theme['top_btn']).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); color: var(--xf-text-strong);')
            ui.button('探针设置', icon='tune', on_click=lambda: asyncio.create_task(_render_probe())).props('flat align=left').classes(theme['top_btn']).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); color: var(--xf-text-strong);')
            ui.button('订阅管理', icon='rss_feed', on_click=lambda: asyncio.create_task(_load_subs())).props('flat align=left').classes(theme['top_btn']).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); color: var(--xf-text-strong);')

    async def open_new_server_dialog():
        await _open_server_dialog(None, ui.context.client)

    async def open_all_servers(_=None):
        await _refresh_scope('ALL', client=ui.context.client)

    async def open_tag_group(tag_name):
        await _refresh_scope('TAG', tag_name, client=ui.context.client)

    async def open_country_group(group_name):
        await _refresh_scope('COUNTRY', group_name, client=ui.context.client)

    with ui.column().props('id=sidebar-scroll-box').classes(theme['scroll_wrap']).style('background: var(--xf-bg-main);'):
        with ui.row().classes('w-full gap-2 px-1 mb-2'):
            ui.button('新建分组', icon='create_new_folder', on_click=open_quick_group_create_dialog).props('flat dense').classes(f"{theme['new_group_btn']} {theme['group_action_base']}").style('background: var(--xf-soft-bg); color: var(--xf-accent); border-color: var(--xf-card-border);')
            ui.button('添加服务器', icon='add', on_click=open_new_server_dialog).props('flat dense').classes(f"{theme['new_server_btn']} {theme['group_action_base']}").style('background: var(--xf-soft-bg); color: var(--xf-text-strong); border-color: var(--xf-card-border);')

        with ui.row().classes(theme['list_item']).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); box-shadow: 0 6px 18px rgba(15,23,42,0.10);').on('click', open_all_servers):
            with ui.row().classes('items-center gap-3'):
                with ui.column().classes(theme['list_icon_box']):
                    ui.icon('dns').classes(theme['list_icon']).style('color: var(--xf-accent);')
                ui.label('所有服务器').classes(theme['list_label']).style('color: var(--xf-text-strong);')
            ui.badge(str(len(SERVERS_CACHE)), color='blue').props('rounded-sm outline').classes('text-[10px] font-black').style('color: var(--xf-accent); border-color: var(--xf-card-border);')

        def on_drag_start(e, name):
            global _current_dragged_group
            _current_dragged_group = name

        final_tags = ADMIN_CONFIG.get('custom_groups', [])

        async def on_tag_drop(e, target_name):
            global _current_dragged_group
            if not _current_dragged_group or _current_dragged_group == target_name:
                return
            try:
                current_list = list(final_tags)
                if _current_dragged_group in current_list and target_name in current_list:
                    old_idx = current_list.index(_current_dragged_group)
                    item = current_list.pop(old_idx)
                    new_idx = current_list.index(target_name)
                    current_list.insert(new_idx, item)
                    ADMIN_CONFIG['custom_groups'] = current_list
                    await save_admin_config()
                    _current_dragged_group = None
                    render_sidebar_content.refresh()
            except:
                pass

        if final_tags:
            ui.label('自定义分组').classes(theme['section_label']).style('color: var(--xf-accent); opacity: 0.75;')
            for tag_group in final_tags:
                tag_servers = [s for s in SERVERS_CACHE if isinstance(s, dict) and (tag_group in s.get('tags', []) or s.get('group') == tag_group)]
                try:
                    tag_servers.sort(key=smart_sort_key)
                except:
                    tag_servers.sort(key=lambda x: x.get('name', ''))
                is_open = tag_group in EXPANDED_GROUPS

                with ui.element('div').classes('w-full').on('dragover.prevent', lambda _: None).on('drop', lambda e, n=tag_group: on_tag_drop(e, n)):
                    with ui.expansion('', icon=None, value=is_open).classes(theme['expansion_custom']).props(theme['expansion_header_props']).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); box-shadow: 0 6px 18px rgba(15,23,42,0.10);').on_value_change(lambda e, g=tag_group: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                        with exp.add_slot('header'):
                            with ui.row().classes('w-full h-full items-center justify-between no-wrap py-2 px-3 cursor-pointer group/header transition-all').style('background: var(--xf-elevated-bg); color: var(--xf-text-strong);').on('click', lambda _, g=tag_group: open_tag_group(g)):
                                with ui.row().classes('items-center gap-3 flex-grow overflow-hidden no-wrap'):
                                    ui.icon('drag_indicator').props('draggable="true"').classes(theme['drag_icon']).on('dragstart', lambda e, n=tag_group: on_drag_start(e, n)).on('click.stop').tooltip('按住拖拽')

                                    with ui.row().classes('items-center gap-2 flex-grow overflow-hidden no-wrap'):
                                        ui.label(tag_group).classes(theme['group_name'])

                                with ui.row().classes('items-center gap-2 pr-2 flex-shrink-0').on('mousedown.stop').on('click.stop'):
                                    ui.button(icon='settings', on_click=lambda _, g=tag_group: open_combined_group_management(g)).props('flat dense round size=xs').classes(theme['icon_btn']).tooltip('管理分组')
                                    ui.badge(str(len(tag_servers)), color='green').props('rounded-sm outline text-color=green-4').classes('text-[10px] font-black').style('border-color: var(--xf-card-border);')

                        with ui.column().classes(theme['expansion_body']).style('background: color-mix(in srgb, var(--xf-elevated-bg) 82%, var(--xf-bg-main)); border-color: var(--xf-card-border);') as col:
                            SIDEBAR_UI_REFS['groups'][tag_group] = col
                            for s in tag_servers:
                                render_single_sidebar_row(s)

        ui.label('区域分组').classes(theme['section_label']).style('color: var(--xf-accent); opacity: 0.75;')
        country_buckets = {}
        for s in SERVERS_CACHE:
            c_group = detect_country_group(s.get('name', ''), s)
            if c_group in ['默认分组', '自动注册', '自动导入', '未分组', '', None]:
                c_group = '🏳️ 其他地区'
            if c_group not in country_buckets:
                country_buckets[c_group] = []
            country_buckets[c_group].append(s)

        saved_order = ADMIN_CONFIG.get('group_order', [])

        def region_sort_key(name):
            return saved_order.index(name) if name in saved_order else 9999

        sorted_regions = sorted(country_buckets.keys(), key=region_sort_key)

        async def on_region_drop(e, target_name):
            global _current_dragged_group
            if not _current_dragged_group or _current_dragged_group == target_name:
                return
            try:
                current_list = list(sorted_regions)
                if _current_dragged_group in current_list and target_name in current_list:
                    old_idx = current_list.index(_current_dragged_group)
                    item = current_list.pop(old_idx)
                    new_idx = current_list.index(target_name)
                    current_list.insert(new_idx, item)
                    ADMIN_CONFIG['group_order'] = current_list
                    await save_admin_config()
                    _current_dragged_group = None
                    render_sidebar_content.refresh()
            except:
                pass

        with ui.column().classes('w-full gap-2 pb-4'):
            for c_name in sorted_regions:
                c_servers = country_buckets[c_name]
                try:
                    c_servers.sort(key=smart_sort_key)
                except:
                    c_servers.sort(key=lambda x: x.get('name', ''))
                is_open = c_name in EXPANDED_GROUPS

                with ui.element('div').classes('w-full').on('dragover.prevent', lambda _: None).on('drop', lambda e, n=c_name: on_region_drop(e, n)):
                    with ui.expansion('', icon=None, value=is_open).classes(theme['expansion_region']).props(theme['expansion_header_props']).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); box-shadow: 0 6px 18px rgba(15,23,42,0.10);').on_value_change(lambda e, g=c_name: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                        with exp.add_slot('header'):
                            with ui.row().classes('w-full h-full items-center justify-between no-wrap py-2 px-3 cursor-pointer group/header transition-all').style('background: var(--xf-elevated-bg); color: var(--xf-text-strong);').on('click', lambda _, g=c_name: open_country_group(g)):
                                with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                                    ui.icon('drag_indicator').props('draggable="true"').classes(theme['drag_icon']).on('dragstart', lambda e, n=c_name: on_drag_start(e, n)).on('click.stop').tooltip('按住拖拽')
                                    with ui.row().classes('items-center gap-2 flex-grow'):
                                        flag = c_name.split(' ')[0] if ' ' in c_name else '🏳️'
                                        ui.label(flag).classes('text-lg filter drop-shadow-md').style('color: var(--xf-text-strong);')
                                        display_name = c_name.split(' ')[1] if ' ' in c_name else c_name
                                        ui.label(display_name).classes(theme['flag_name']).style('color: var(--xf-text-strong);')
                                with ui.row().classes('items-center gap-2 pr-2').on('mousedown.stop').on('click.stop'):
                                    ui.button(icon='edit_note', on_click=lambda _, s=c_servers, t=c_name: open_bulk_edit_dialog(s, f"区域: {t}")).props('flat dense round size=xs').classes(theme['icon_btn']).tooltip('批量管理')
                                    ui.badge(str(len(c_servers)), color='green').props('rounded-sm outline text-color=green-4').classes('text-[10px] font-black').style('border-color: var(--xf-card-border);')

                        with ui.column().classes(theme['expansion_body']).style('background: color-mix(in srgb, var(--xf-elevated-bg) 82%, var(--xf-bg-main)); border-color: var(--xf-card-border);') as col:
                            SIDEBAR_UI_REFS['groups'][c_name] = col
                            for s in c_servers:
                                render_single_sidebar_row(s)

    ui.run_javascript('''
        (function() {
            var el = document.getElementById("sidebar-scroll-box");
            if (el) {
                if (window.sidebarScroll) el.scrollTop = window.sidebarScroll;
                el.addEventListener("scroll", function() { window.sidebarScroll = el.scrollTop; });
            }
        })();
    ''')

    logger.info(f"[Sidebar] render_sidebar_content finished | servers={len(SERVERS_CACHE)} groups={len(SIDEBAR_UI_REFS.get('groups', {}))} rows={len(SIDEBAR_UI_REFS.get('rows', {}))}")

    with ui.column().classes(theme['bottom_wrap']).style('border-color: var(--xf-card-border); background: var(--xf-bg-main);'):
        ui.button('批量 SSH 执行', icon='playlist_play', on_click=batch_ssh_manager.open_dialog).props('flat align=left').classes(theme['bottom_btn']).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); color: var(--xf-text-strong);')
        ui.button('Cloudflare 设置', icon='cloud', on_click=open_cloudflare_settings_dialog).props('flat align=left').classes(theme['bottom_btn']).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); color: var(--xf-text-strong);')
        ui.button('全局 SSH 设置', icon='vpn_key', on_click=open_global_settings_dialog).props('flat align=left').classes(theme['bottom_btn']).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); color: var(--xf-text-strong);')
        ui.button('数据备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes(theme['bottom_btn']).style('background: var(--xf-elevated-bg); border-color: var(--xf-card-border); color: var(--xf-text-strong);')


async def _load_dashboard():
    from app.ui.components.dashboard import load_dashboard_stats

    await load_dashboard_stats()


async def _render_probe():
    from app.ui.pages.probe_page import render_probe_page

    await render_probe_page()


async def _load_subs():
    from app.ui.pages.subs_page import load_subs_view

    await load_subs_view()


async def _refresh_scope(scope, data=None, client=None):
    from app.ui.pages.content_router import refresh_content

    logger.info(f"[SidebarClick] _refresh_scope called | scope={scope} data={data} client_present={client is not None} current_view_before={CURRENT_VIEW_STATE}")
    await refresh_content(scope, data, manual_client=client)
    logger.info(f"[SidebarClick] _refresh_scope done | scope={scope} data={data} client_present={client is not None} current_view_after={CURRENT_VIEW_STATE}")


async def _open_server_dialog(index, client=None):
    from app.ui.dialogs.server_dialog import open_server_dialog

    if client:
        with client:
            await open_server_dialog(index)
        return

    await open_server_dialog(index)


async def _open_server_dialog_by_server(server, client=None):
    from app.ui.dialogs.server_dialog import open_server_dialog

    if client:
        with client:
            await open_server_dialog(SERVERS_CACHE.index(server))
        return

    await open_server_dialog(SERVERS_CACHE.index(server))
