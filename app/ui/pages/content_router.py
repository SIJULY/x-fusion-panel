import asyncio
import time

from nicegui import ui

from app.core.logging import logger

from app.core.config import PAGE_SIZE, SYNC_COOLDOWN
from app.core.state import CURRENT_VIEW_STATE, LAST_SYNC_MAP, REFRESH_LOCKS, SERVERS_CACHE
from app.services.xui_fetch import fetch_inbounds_safe
from app.ui.common.notifications import safe_notify
from app.utils.formatters import smart_sort_key
from app.utils.geo import detect_country_group
from app.services.subscriptions import copy_group_link


content_container = None


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
        last_sync = LAST_SYNC_MAP.get(cache_key, 0)

        targets = get_targets_by_scope(scope, data)
        logger.info(f"[ContentRouter] refresh_content targets_resolved | scope={scope} data={data} targets={len(targets)}")
        start_idx = (page_num - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_servers = targets[start_idx:end_idx] if targets else []

        has_probe = any(s.get('probe_installed') for s in current_page_servers)
        has_api_only = any(not s.get('probe_installed') for s in current_page_servers)

        is_all_probe = has_probe and not has_api_only

        if not force_refresh and ((now - last_sync < SYNC_COOLDOWN) or is_all_probe):
            CURRENT_VIEW_STATE.update({'scope': scope, 'data': data, 'page': page_num, 'render_token': now})
            logger.info(f"[ContentRouter] refresh_content using cached/probe path | current_view={CURRENT_VIEW_STATE}")
            await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)

            if is_all_probe:
                safe_notify("⚡ 实时数据 (探针推送)", "positive", timeout=1000)
            else:
                mins_ago = int((now - last_sync) / 60)
                safe_notify(f"🕒 缓存数据 ({mins_ago}分前)", "ongoing", timeout=1000)
            return

        if lock_key in REFRESH_LOCKS:
            logger.info(f"[ContentRouter] refresh_content abort | lock_key active={lock_key}")
            return

        CURRENT_VIEW_STATE.update({'scope': scope, 'data': data, 'page': page_num, 'render_token': now})
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
                    if force_refresh:
                        safe_notify(f"🔄 正在同步 {len(sync_targets)} 台 API 节点...", "ongoing")

                    tasks = [fetch_inbounds_safe(s, force_refresh=True, sync_name=sync_name_action) for s in sync_targets]
                    await asyncio.gather(*tasks, return_exceptions=True)

                    if CURRENT_VIEW_STATE.get('render_token') == token_at_start:
                        with client:
                            await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)
                            LAST_SYNC_MAP[cache_key] = time.time()
                            if force_refresh:
                                safe_notify("✅ 同步完成", "positive")
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
            with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between'):
                with ui.row().classes('items-center gap-4'):
                    ui.label(title).classes('text-2xl font-bold')
                with ui.row().classes('items-center gap-2'):
                    if is_group_view and targets:
                        with ui.row().classes('gap-1'):
                            ui.button(icon='content_copy', on_click=lambda: copy_group_link(data)).props('flat dense round size=sm color=grey')
                            ui.button(icon='bolt', on_click=lambda: copy_group_link(data, target='surge')).props('flat dense round size=sm text-color=orange')
                            ui.button(icon='cloud_queue', on_click=lambda: copy_group_link(data, target='clash')).props('flat dense round size=sm text-color=green')
                    if targets:
                        ui.button('同步当前页', icon='sync', on_click=lambda: refresh_content(scope, data, force_refresh=True, sync_name_action=True, page_num=page_num, manual_client=client)).props('outline color=primary')

            if not targets:
                logger.info(f"[ContentRouter] _render_ui_internal empty list | scope={scope} data={data}")
                with ui.column().classes('w-full h-64 justify-center items-center text-gray-400'):
                    ui.icon('inbox', size='4rem')
                    ui.label('列表为空')
            else:
                try:
                    targets.sort(key=smart_sort_key)
                except:
                    pass
                from app.ui.dialogs.server_dialog import render_aggregated_view

                logger.info(f"[ContentRouter] _render_ui_internal render_aggregated_view | scope={scope} data={data} targets={len(targets)} show_ping={show_ping}")
                await render_aggregated_view(targets, show_ping=show_ping, token=None, initial_page=page_num)
    else:
        logger.info("[ContentRouter] _render_ui_internal abort | content_container missing")
