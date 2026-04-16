import asyncio
import time

from nicegui import run

from app.core.config import AUTO_COUNTRY_MAP, SYNC_COOLDOWN_SECONDS
from app.core.logging import logger
from app.core.state import (
    ADMIN_CONFIG,
    CURRENT_VIEW_STATE,
    EXPANDED_GROUPS,
    NODES_DATA,
    SERVERS_CACHE,
    SIDEBAR_UI_REFS,
)
from app.services.manager_factory import get_manager
from app.services.probe import install_probe_on_server
from app.services.xui_fetch import fetch_inbounds_safe
from app.storage.repositories import save_admin_config, save_nodes_cache, save_servers
from app.utils.async_tools import run_in_bg_executor
from app.utils.geo import detect_country_group, fetch_geo_from_ip, get_flag_for_country


async def force_geoip_naming_task(server_conf, max_retries=10):
    """
    强制执行 GeoIP 解析，直到成功或达到最大重试次数。
    成功后：
    1. 命名格式：🇺🇸 美国-1, 🇭🇰 香港-2
    2. 分组：自动分入对应国家组
    """
    url = server_conf['url']
    logger.info(f"🌍 [强制修正] 开始处理: {url} (目标: 国旗+国家+序号)")

    for i in range(max_retries):
        try:
            geo_info = await run.io_bound(fetch_geo_from_ip, url)

            if geo_info:
                country_raw = geo_info[2]
                flag_group = get_flag_for_country(country_raw)

                count = 1
                for s in SERVERS_CACHE:
                    if s is not server_conf and s.get('name', '').startswith(flag_group):
                        count += 1

                final_name = f"{flag_group}-{count}"

                old_name = server_conf.get('name', '')
                if old_name != final_name:
                    server_conf['name'] = final_name
                    server_conf['group'] = flag_group
                    server_conf['_detected_region'] = country_raw

                    await save_servers()

                    logger.info(f"✅ [强制修正] 成功: {old_name} -> {final_name} (第 {i+1} 次尝试)")
                    return

            logger.warning(f"⏳ [强制修正] 第 {i+1} 次解析 IP 归属地失败，3秒后重试...")

        except Exception as e:
            logger.error(f"❌ [强制修正] 异常: {e}")

        await asyncio.sleep(3)

    logger.warning(f"⚠️ [强制修正] 最终失败: 达到最大重试次数，保持原名 {server_conf.get('name')}")


async def generate_smart_name(server_conf):
    """尝试获取面板节点名，获取不到则用 GeoIP+序号"""
    try:
        mgr = get_manager(server_conf)
        inbounds = await run_in_bg_executor(mgr.get_inbounds)
        if inbounds and len(inbounds) > 0:
            for node in inbounds:
                if node.get('remark'):
                    return node['remark']
    except:
        pass

    try:
        geo_info = await run.io_bound(fetch_geo_from_ip, server_conf['url'])
        if geo_info:
            country_name = geo_info[2]
            flag_prefix = get_flag_for_country(country_name)

            count = 1
            for s in SERVERS_CACHE:
                if s.get('name', '').startswith(flag_prefix):
                    count += 1
            return f"{flag_prefix}-{count}"
    except:
        pass

    return f"Server-{len(SERVERS_CACHE) + 1}"


async def silent_refresh_all(is_auto_trigger=False):
    last_time = ADMIN_CONFIG.get('last_sync_time', 0)

    if is_auto_trigger:
        current_time = time.time()

        total_nodes = 0
        try:
            for nodes in NODES_DATA.values():
                if isinstance(nodes, list):
                    total_nodes += len(nodes)
        except:
            pass

        if len(SERVERS_CACHE) > 0 and total_nodes == 0:
            logger.warning(f"⚠️ [防抖穿透] 缓存为空 (节点数0)，强制触发首次修复同步！")

        elif current_time - last_time < SYNC_COOLDOWN_SECONDS:
            remaining = int(SYNC_COOLDOWN_SECONDS - (current_time - last_time))
            logger.info(f"⏳ [防抖生效] 距离上次同步不足 {SYNC_COOLDOWN_SECONDS}秒，跳过 (剩余: {remaining}s)")
            return

    from app.ui.common.notifications import safe_notify
    from app.ui.components.dashboard import load_dashboard_stats
    from app.ui.components.sidebar import render_sidebar_content

    safe_notify(f'🚀 开始后台静默刷新 ({len(SERVERS_CACHE)} 个服务器)...')

    ADMIN_CONFIG['last_sync_time'] = time.time()
    await save_admin_config()

    tasks = []
    for srv in SERVERS_CACHE:
        tasks.append(fetch_inbounds_safe(srv, force_refresh=True))

    await asyncio.gather(*tasks, return_exceptions=True)

    await save_nodes_cache()

    safe_notify('✅ 后台刷新完成', 'positive')
    try:
        render_sidebar_content.refresh()
        await load_dashboard_stats()
    except:
        pass


async def fast_resolve_single_server(s):
    """
    后台全自动修正流程：
    1. 尝试连接面板，读取第一个节点的备注名 (Smart Name)
    2. 尝试查询 IP 归属地，获取国旗 (GeoIP)
    3. 自动组合名字 (防止国旗重复)
    4. 自动归类分组
    """
    await asyncio.sleep(1.5)

    raw_ip = s['url'].split('://')[-1].split(':')[0]
    logger.info(f"🔍 [智能修正] 正在处理: {raw_ip} ...")

    data_changed = False

    try:
        current_pure_name = s['name'].replace('🏳️', '').strip()

        if current_pure_name == raw_ip:
            try:
                smart_name = await generate_smart_name(s)
                if smart_name and smart_name != raw_ip and not smart_name.startswith('Server-'):
                    s['name'] = smart_name
                    data_changed = True
                    logger.info(f"🏷️ [获取备注] 成功: {smart_name}")
            except Exception as e:
                logger.warning(f"⚠️ [获取备注] 失败: {e}")

        geo = await run.io_bound(fetch_geo_from_ip, s['url'])

        if geo:
            country_name = geo[2]
            s['lat'] = geo[0]
            s['lon'] = geo[1]
            s['_detected_region'] = country_name

            flag_group = get_flag_for_country(country_name)
            flag_icon = flag_group.split(' ')[0]

            temp_name = s['name'].replace('🏳️', '').strip()

            if flag_icon in temp_name:
                if s['name'] != temp_name:
                    s['name'] = temp_name
                    data_changed = True
            else:
                s['name'] = f"{flag_icon} {temp_name}"
                data_changed = True

            target_group = flag_group

            for k, v in AUTO_COUNTRY_MAP.items():
                if flag_icon in k or flag_icon in v:
                    target_group = v
                    break

            if s.get('group') != target_group:
                s['group'] = target_group
                data_changed = True

        else:
            logger.warning(f"⚠️ [GeoIP] 未获取到地理位置: {raw_ip}")

        if data_changed:
            await save_servers()
            logger.info(f"✅ [智能修正] 完毕: {s['name']} -> [{s['group']}]")

    except Exception as e:
        logger.error(f"❌ [智能修正] 严重错误: {e}")


def get_all_groups():
    groups = {'默认分组', '自动注册'}
    for s in SERVERS_CACHE:
        g = s.get('group')
        if g:
            groups.add(g)
    return sorted(list(groups))


async def save_server_config(server_data, is_add=True, idx=None):
    if not server_data.get('name') or not server_data.get('url'):
        from app.ui.common.notifications import safe_notify

        safe_notify("名称和地址不能为空", "negative")
        return False

    old_group = None
    if not is_add and idx is not None and 0 <= idx < len(SERVERS_CACHE):
        old_group = SERVERS_CACHE[idx].get('group')

    if is_add:
        for s in SERVERS_CACHE:
            if s['url'] == server_data['url']:
                from app.ui.common.notifications import safe_notify

                safe_notify(f"已存在！", "warning")
                return False

        has_flag = False
        for v in AUTO_COUNTRY_MAP.values():
            if v.split(' ')[0] in server_data['name']:
                has_flag = True
                break
        if not has_flag and '🏳️' not in server_data['name']:
            server_data['name'] = f"🏳️ {server_data['name']}"

        SERVERS_CACHE.append(server_data)
        from app.ui.common.notifications import safe_notify

        safe_notify(f"已添加: {server_data['name']}", "positive")
    else:
        if idx is not None and 0 <= idx < len(SERVERS_CACHE):
            SERVERS_CACHE[idx].update(server_data)
            from app.ui.common.notifications import safe_notify

            safe_notify(f"已更新: {server_data['name']}", "positive")
        else:
            from app.ui.common.notifications import safe_notify

            safe_notify("目标不存在", "negative")
            return False

    await save_servers()

    new_group = server_data.get('group', '默认分组')
    if new_group in ['默认分组', '自动注册', '未分组', '自动导入']:
        try:
            new_group = detect_country_group(server_data.get('name', ''), server_data)
        except:
            pass
        if not new_group:
            new_group = '🏳️ 其他地区'

    need_full_refresh = False

    try:
        from app.ui.components.sidebar import render_sidebar_content, render_single_sidebar_row

        if is_add:
            if new_group in SIDEBAR_UI_REFS['groups']:
                with SIDEBAR_UI_REFS['groups'][new_group]:
                    render_single_sidebar_row(server_data)
                EXPANDED_GROUPS.add(new_group)
            else:
                need_full_refresh = True

        elif old_group != new_group:
            row_el = SIDEBAR_UI_REFS['rows'].get(server_data['url'])
            target_col = SIDEBAR_UI_REFS['groups'].get(new_group)

            if row_el and target_col:
                row_el.move(target_col)
                EXPANDED_GROUPS.add(new_group)
            else:
                need_full_refresh = True

    except Exception as e:
        logger.error(f"UI Move Error: {e}")
        need_full_refresh = True

    if need_full_refresh:
        try:
            from app.ui.components.sidebar import render_sidebar_content

            render_sidebar_content.refresh()
        except:
            pass

    current_scope = CURRENT_VIEW_STATE.get('scope')
    current_data = CURRENT_VIEW_STATE.get('data')

    if current_scope == 'SINGLE' and (current_data == server_data or (is_add and server_data == SERVERS_CACHE[-1])):
        try:
            from app.ui.pages.content_router import refresh_content

            await refresh_content('SINGLE', server_data, force_refresh=True)
        except:
            pass

    elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
        CURRENT_VIEW_STATE['scope'] = None
        try:
            from app.ui.pages.content_router import refresh_content

            await refresh_content(current_scope, current_data, force_refresh=True)
        except:
            pass

    elif current_scope == 'DASHBOARD':
        try:
            from app.ui.components.dashboard import refresh_dashboard_ui

            await refresh_dashboard_ui()
        except:
            pass

    asyncio.create_task(fast_resolve_single_server(server_data))

    if ADMIN_CONFIG.get('probe_enabled', False) and server_data.get('probe_installed', False):
        async def delayed_install():
            await asyncio.sleep(1)
            await install_probe_on_server(server_data)
        asyncio.create_task(delayed_install())

    return True


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
        elif scope == 'SINGLE':
            if data in SERVERS_CACHE:
                targets = [data]
    except:
        pass
    return targets
