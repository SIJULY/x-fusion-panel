import asyncio
import time

from nicegui import app, ui

from app.core.logging import logger

from app.core.config import PAGE_SIZE
from app.core.state import CURRENT_VIEW_STATE, LAST_SYNC_MAP, REFRESH_LOCKS, SERVERS_CACHE
from app.services.xui_fetch import fetch_inbounds_safe
from app.ui.common.notifications import safe_notify
from app.utils.formatters import smart_sort_key
from app.utils.geo import detect_country_group
from app.services.subscriptions import copy_group_link


content_container = None


def _match_server_search(server, keyword):
    keyword = str(keyword or '').strip().lower()
    if not keyword:
        return True

    name = str(server.get('name', '') or '').lower()
    url = str(server.get('url', '') or '').lower()
    ssh_host = str(server.get('ssh_host', '') or '').lower()

    host_from_url = url.split('://')[-1].split('/')[0].split(':')[0] if url else ''
    search_pool = [name, url, ssh_host, host_from_url]
    return any(keyword in field for field in search_pool if field)


def _persist_last_view(scope, data, page_num=1):
    try:
        stored_data = data
        if scope in ['SINGLE', 'SSH_SINGLE'] and isinstance(data, dict):
            stored_data = data.get('url')
        app.storage.user['last_view_scope'] = scope
        app.storage.user['last_view_data'] = stored_data
        app.storage.user['last_view_page'] = page_num
    except:
        pass


def get_targets_by_scope(scope, data):
    targets = []
    try:
        if scope == 'ALL':
            targets = list(SERVERS_CACHE)
        elif scope == 'TAG':
            targets = [s for s in SERVERS_CACHE if data in s.get('tags', [])]
        elif scope == 'COUNTRY':
            for s in SERVERS_CACHE:
                saved = s.get('group')
                real = saved if saved and saved not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区'] else detect_country_group(s.get('name', ''))
                if real == data:
                    targets.append(s)
        elif scope in ['SINGLE', 'SSH_SINGLE']:
            if data in SERVERS_CACHE:
                targets = [data]
            elif isinstance(data, dict):
                data_url = data.get('url')
                if data_url:
                    matched = next((s for s in SERVERS_CACHE if s.get('url') == data_url), None)
                    if matched:
                        targets = [matched]
    except:
        pass
    return targets


async def refresh_content(scope='ALL', data=None, force_refresh=False, sync_name_action=False, page_num=1, manual_client=None):
    global CURRENT_VIEW_STATE, REFRESH_LOCKS, LAST_SYNC_MAP

    client = manual_client
    if not client:
        try:
            client = ui.context.client
        except:
            pass
    logger.info(f"[ContentRouter] refresh_content called | scope={scope} data={data} force_refresh={force_refresh} page_num={page_num} client_present={client is not None} container_present={content_container is not None} current_view_before={CURRENT_VIEW_STATE}")
    if not client:
        logger.info("[ContentRouter] refresh_content abort | no client")
        return

    with client:
        cache_key = f"{scope}::{data}::P{page_num}"
        lock_key = cache_key

        now = time.time()

        targets = get_targets_by_scope(scope, data)
        logger.info(f"[ContentRouter] refresh_content targets_resolved | scope={scope} data={data} targets={len(targets)}")
        start_idx = (page_num - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_servers = targets[start_idx:end_idx] if targets else []

        has_probe = any(s.get('probe_installed') for s in current_page_servers)
        has_api_only = any(not s.get('probe_installed') for s in current_page_servers)

        is_all_probe = has_probe and not has_api_only

        if not force_refresh and is_all_probe:
            CURRENT_VIEW_STATE.update({'scope': scope, 'data': data, 'page': page_num, 'render_token': now})
            _persist_last_view(scope, data, page_num)
            logger.info(f"[ContentRouter] refresh_content using probe realtime path | current_view={CURRENT_VIEW_STATE}")
            await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)
            safe_notify("⚡ 实时数据 (探针推送)", "positive", timeout=1000)
            return

        if lock_key in REFRESH_LOCKS:
            logger.info(f"[ContentRouter] refresh_content abort | lock_key active={lock_key}")
            return

        CURRENT_VIEW_STATE.update({'scope': scope, 'data': data, 'page': page_num, 'render_token': now})
        _persist_last_view(scope, data, page_num)
        logger.info(f"[ContentRouter] refresh_content state updated | current_view={CURRENT_VIEW_STATE}")

        await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)
        logger.info(f"[ContentRouter] refresh_content initial render done | scope={scope} data={data} current_view_after={CURRENT_VIEW_STATE}")

        if not current_page_servers:
            return

        async def _background_fetch(token_at_start):
            REFRESH_LOCKS.add(lock_key)
            try:
                sync_targets = [s for s in current_page_servers if not s.get('probe_installed')]

                if sync_targets:
                    logger.info(f"[ContentRouter] auto background sync current page | scope={scope} data={data} page_num={page_num} api_targets={len(sync_targets)}")
                    with client:
                        safe_notify(f"🔄 后台同步当前页 {len(sync_targets)} 台 API 节点...", "ongoing", timeout=1200)

                    tasks = [fetch_inbounds_safe(s, force_refresh=True, sync_name=sync_name_action) for s in sync_targets]
                    await asyncio.gather(*tasks, return_exceptions=True)

                    if CURRENT_VIEW_STATE.get('render_token') == token_at_start:
                        with client:
                            await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)
                            LAST_SYNC_MAP[cache_key] = time.time()
                            safe_notify("✅ 当前页后台同步完成", "positive", timeout=1000)
                            try:
                                from app.ui.components.sidebar import render_sidebar_content

                                render_sidebar_content.refresh()
                            except:
                                pass
                else:
                    LAST_SYNC_MAP[cache_key] = time.time()
            finally:
                REFRESH_LOCKS.discard(lock_key)

        asyncio.create_task(_background_fetch(now))


async def _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client):
    logger.info(f"[ContentRouter] _render_ui_internal called | scope={scope} data={data} page_num={page_num} container_present={content_container is not None} container_id={id(content_container) if content_container else None}")
    if content_container:
        content_container.clear()
        content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
        with content_container:
            targets = get_targets_by_scope(scope, data)
            logger.info(f"[ContentRouter] _render_ui_internal targets | scope={scope} data={data} targets={len(targets)}")
            from app.ui.dialogs.server_dialog import cleanup_ssh_route_terminal

            if scope == 'SSH_SINGLE':
                if targets:
                    from app.ui.dialogs.server_dialog import render_single_ssh_view

                    logger.info(f"[ContentRouter] _render_ui_internal branch=SSH_SINGLE found target | url={targets[0].get('url')}")
                    await render_single_ssh_view(targets[0])
                    return
                else:
                    logger.info("[ContentRouter] _render_ui_internal branch=SSH_SINGLE target missing")
                    ui.label('服务器未找到')
                    return

            cleanup_ssh_route_terminal()

            if scope == 'SINGLE':
                if targets:
                    from app.ui.dialogs.server_dialog import render_single_server_view

                    logger.info(f"[ContentRouter] _render_ui_internal branch=SINGLE found target | url={targets[0].get('url')}")
                    await render_single_server_view(targets[0])
                    return
                else:
                    logger.info("[ContentRouter] _render_ui_internal branch=SINGLE target missing")
                    ui.label('服务器未找到')
                    return

            title = ""
            is_group_view = False
            show_ping = False
            if scope == 'ALL':
                title = f"🌍 所有服务器 ({len(targets)})"
            elif scope == 'TAG':
                title = f"🏷️ 自定义分组: {data} ({len(targets)})"
                is_group_view = True
            elif scope == 'COUNTRY':
                title = f"🏳️ 区域: {data} ({len(targets)})"
                is_group_view = True
                show_ping = True

            logger.info(f"[ContentRouter] _render_ui_internal branch={scope} title={title}")
            search_state = {'keyword': ''}

            with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between gap-4'):
                with ui.row().classes('items-center gap-4 flex-wrap'):
                    ui.label(title).classes('text-2xl font-bold')
                with ui.row().classes('items-center gap-2 flex-wrap justify-end'):
                    if is_group_view and targets:
                        with ui.row().classes('gap-1'):
                            ui.button(icon='content_copy', on_click=lambda: copy_group_link(data)).props('flat dense round size=sm color=grey')
                            ui.button(icon='bolt', on_click=lambda: copy_group_link(data, target='surge')).props('flat dense round size=sm text-color=orange')
                            ui.button(icon='cloud_queue', on_click=lambda: copy_group_link(data, target='clash')).props('flat dense round size=sm text-color=green')
                    if scope == 'ALL':
                        ui.input(
                            placeholder='搜索服务器名称或 IP',
                            on_change=lambda e: [search_state.__setitem__('keyword', e.value or ''), render_target_list.refresh()],
                        ).props('outlined dense clearable').classes('w-[320px] max-w-full')

            @ui.refreshable
            async def render_target_list():
                filtered_targets = list(targets)
                keyword = search_state['keyword'].strip()

                if keyword:
                    filtered_targets = [s for s in targets if _match_server_search(s, keyword)]
                    with ui.row().classes('w-full items-center justify-between mb-2 px-1'):
                        ui.label(f'匹配到 {len(filtered_targets)} 台服务器').classes('text-sm font-bold text-slate-500')

                if not filtered_targets:
                    logger.info(f"[ContentRouter] _render_ui_internal empty list | scope={scope} data={data} keyword={keyword}")
                    with ui.column().classes('w-full h-64 justify-center items-center text-gray-400'):
                        ui.icon('inbox', size='4rem')
                        ui.label('未找到匹配的服务器' if keyword else '列表为空')
                    return

                try:
                    filtered_targets.sort(key=smart_sort_key)
                except:
                    pass

                from app.ui.dialogs.server_dialog import render_aggregated_view

                logger.info(f"[ContentRouter] _render_ui_internal render_aggregated_view | scope={scope} data={data} filtered_targets={len(filtered_targets)} show_ping={show_ping} keyword={keyword}")
                await render_aggregated_view(filtered_targets, show_ping=show_ping, token=None, initial_page=(1 if keyword else page_num))

            await render_target_list()
    else:
        logger.info("[ContentRouter] _render_ui_internal abort | content_container missing")
