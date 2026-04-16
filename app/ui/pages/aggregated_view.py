import asyncio

from nicegui import app, ui

from app.core.state import CURRENT_VIEW_STATE, NODES_DATA, SERVERS_CACHE
from app.ui.components.server_rows import draw_row


COLS_NO_PING = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 0.5fr 1.5fr; align-items: center;'
COLS_SPECIAL_WITH_PING = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 1.5fr; align-items: center;'


async def render_aggregated_view(server_list, show_ping=False, token=None, initial_page=1):

    is_dark = bool(app.storage.user.get('is_dark', True))
    summary_cls = 'text-xs font-black tracking-wide'
    summary_style = 'color: var(--xf-accent); opacity: 0.75;'
    pagination_props = 'dense flat color=blue text-color=slate-400 active-text-color=white' if is_dark else 'dense flat color=blue text-color=slate-600 active-text-color=blue-8'
    pagination_cls = 'text-slate-300' if is_dark else 'text-slate-600'
    head_row_cls = 'grid w-full gap-4 font-black pb-2 px-6 mb-1 uppercase tracking-wider text-xs rounded-sm pt-3'
    head_row_style = 'color: var(--xf-text-muted); background: var(--xf-panel-bg); border-bottom: 1px solid var(--xf-card-border); box-shadow: 0 6px 18px rgba(15,23,42,0.08);'

    parent_client = ui.context.client
    list_container = ui.column().classes('w-full max-w-[1440px] mx-auto gap-3 p-1')

    cols_ping = 'grid-template-columns: 2fr 2fr 1.5fr 1.5fr 1fr 1fr 1.5fr'
    cols_no_ping = 'grid-template-columns: 2fr 2fr 1.5fr 1.5fr 1fr 1fr 0.5fr 1.5fr'

    try:
        is_all_servers = (len(server_list) == len(SERVERS_CACHE) and not show_ping)
        use_special_mode = is_all_servers or show_ping
        current_css = COLS_SPECIAL_WITH_PING if use_special_mode else COLS_NO_PING
    except:
        current_css = cols_ping if show_ping else cols_no_ping

    PAGE_SIZE = 30
    total_items = len(server_list)
    total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
    if initial_page > total_pages:
        initial_page = 1
    if initial_page < 1:
        initial_page = 1

    def render_page(page_num):
        list_container.clear()
        CURRENT_VIEW_STATE['page'] = page_num

        with list_container:
            with ui.row().classes('w-full justify-between items-center px-2 mb-2'):
                ui.label(f'共 {total_items} 台服务器 (第 {page_num}/{total_pages} 页)').classes(summary_cls).style(summary_style)
                if total_pages > 1:
                    ui.pagination(1, total_pages, direction_links=True, value=page_num).props(pagination_props).classes(pagination_cls).on_value_change(lambda e: handle_pagination_click(e.value))

            with ui.element('div').classes(head_row_cls).style(f'{current_css} {head_row_style}'):
                ui.label('服务器').classes('text-left pl-1')
                ui.label('节点名称').classes('text-left pl-1')
                if use_special_mode:
                    ui.label('在线状态 / IP').classes('text-center')
                else:
                    ui.label('所在组').classes('text-center')
                ui.label('已用流量').classes('text-center')
                ui.label('协议').classes('text-center')
                ui.label('端口').classes('text-center')
                if not use_special_mode:
                    ui.label('状态').classes('text-center')
                ui.label('操作').classes('text-center')

            start_idx = (page_num - 1) * PAGE_SIZE
            end_idx = start_idx + PAGE_SIZE
            current_page_data = server_list[start_idx:end_idx]

            for srv in current_page_data:
                panel_n = NODES_DATA.get(srv['url'], []) or []
                custom_n = srv.get('custom_nodes', []) or []
                for cn in custom_n:
                    cn['_is_custom'] = True
                all_nodes = panel_n + custom_n

                if not all_nodes:
                    draw_row(srv, None, current_css, use_special_mode, is_first=True)
                    continue

                for index, node in enumerate(all_nodes):
                    draw_row(srv, node, current_css, use_special_mode, is_first=(index == 0))

            if total_pages > 1:
                with ui.row().classes('w-full justify-center mt-4'):
                    ui.pagination(1, total_pages, direction_links=True, value=page_num).props(pagination_props).classes(pagination_cls).on_value_change(lambda e: handle_pagination_click(e.value))

    def handle_pagination_click(new_page):
        try:
            target_page = int(new_page)
        except:
            return

        current_scope = CURRENT_VIEW_STATE.get('scope', 'ALL')
        current_data = CURRENT_VIEW_STATE.get('data', None)
        print(f'👉 [Debug] 翻页至: {target_page} (自然浏览)', flush=True)

        with parent_client:
            from app.ui.pages.content_router import refresh_content

            asyncio.create_task(refresh_content(scope=current_scope, data=current_data, force_refresh=False, sync_name_action=True, page_num=target_page, manual_client=parent_client))

    render_page(initial_page)


__all__ = ['render_aggregated_view']
