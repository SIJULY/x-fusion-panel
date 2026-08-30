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
from app.storage.repositories import save_admin_config, save_nodes_cache, save_servers, save_single_server
from app.utils.async_tools import run_in_bg_executor
from app.utils.geo import detect_country_group, fetch_geo_from_ip, get_flag_for_country
from app.utils.network import extract_host, is_ip_literal, resolve_domain_ip


def make_cf_origin_ip_lookup():
    """
    返回一个带记忆的「域名 -> 源站 IP」查询函数；未配置 Cloudflare Token 时返回 None。

    域名被 CF 橙云代理时，普通 DNS 解析出来的是 CF 边缘 IP 而不是机器本身，
    所以优先读 CF 上 A 记录里的 content（源站 IP）。
    CloudflareHandler 每次查询完会关掉内部 httpx client，用锁串行化避免并发打架。
    """
    try:
        from app.services.cloudflare import CloudflareHandler

        cf = CloudflareHandler()
        if not cf.token:
            return None
    except Exception as e:
        logger.warning(f"⚠️ [主机归位] Cloudflare 初始化失败，改用普通 DNS: {e}")
        return None

    cache = {}
    lock = asyncio.Lock()

    async def _lookup(domain):
        key = str(domain or '').strip().lower()
        if not key:
            return None
        if key in cache:
            return cache[key]
        async with lock:
            if key in cache:
                return cache[key]
            ip = None
            try:
                ok, val = await cf.get_a_record_ip_by_domain(key)
                if ok and val and is_ip_literal(val):
                    ip = val
            except Exception as e:
                logger.warning(f"⚠️ [主机归位] CF 查询 {key} 失败: {e}")
            cache[key] = ip
            return ip

    return _lookup


def backfill_primary_domain(srv):
    """
    主机字段归位第一步（纯同步）：服务器身上出现过域名（url 主机优先，其次 ssh_host），
    而 `cf_primary_domain` 为空时，把该域名回填到 `cf_primary_domain`。

    url 主机优先是为了跟订阅链接现有的回退顺序保持一致 —— 回填后订阅出站主机不变。
    返回是否改动了 srv。
    """
    if not isinstance(srv, dict):
        return False
    if str(srv.get('cf_primary_domain') or '').strip():
        return False

    ssh_host = str(srv.get('ssh_host') or '').strip()
    url_host = extract_host(srv.get('url'))
    domain = next((h for h in (url_host, ssh_host) if h and not is_ip_literal(h)), '')
    if not domain:
        return False

    srv['cf_primary_domain'] = domain
    logger.info(f"🔀 [主机归位] {srv.get('name', 'Unknown')} 主域名回填: {domain}")
    return True


async def resolve_host_to_ip(host, use_cache=True, use_cf=True):
    """单个主机 -> IP：优先 CF 上的源站 IP，拿不到再退回普通 DNS。已是 IP 则原样返回。"""
    h = str(host or '').strip().strip('[]')
    if not h:
        return None
    if is_ip_literal(h):
        return h
    if use_cf:
        lookup = make_cf_origin_ip_lookup()
        if lookup:
            ip = await lookup(h)
            if ip:
                return ip
    return await resolve_domain_ip(h, use_cache=use_cache)


async def resolve_ssh_host_to_ip(srv, use_cache=True, cf_lookup=None, use_cf=True):
    """
    主机字段归位第二步：`ssh_host` 里存的是域名时，解析成 IP 后写回 `ssh_host`。
    `ssh_host` 本来就为空则不动它，避免给纯 X-UI 服务器凭空造出一份 SSH 配置。
    优先取 CF 上的源站 IP，拿不到再退回普通 DNS。返回是否改动了 srv。
    """
    if not isinstance(srv, dict):
        return False

    ssh_host = str(srv.get('ssh_host') or '').strip()
    if not ssh_host or is_ip_literal(ssh_host):
        return False

    if cf_lookup:
        ip = await cf_lookup(ssh_host) or await resolve_domain_ip(ssh_host, use_cache=use_cache)
    else:
        ip = await resolve_host_to_ip(ssh_host, use_cache=use_cache, use_cf=use_cf)

    if not ip:
        logger.warning(f"⚠️ [主机归位] {srv.get('name', 'Unknown')} 域名 {ssh_host} 解析失败，SSH 主机保持原样")
        return False
    if ip == ssh_host:
        return False

    logger.info(f"🔀 [主机归位] {srv.get('name', 'Unknown')} SSH 主机 {ssh_host} -> {ip}")
    srv['ssh_host'] = ip
    return True


async def normalize_server_host_fields(srv, use_cache=True, cf_lookup=None, use_cf=True):
    """主机字段归位：`ssh_host` 只存 IP，域名只存在 `cf_primary_domain`。返回是否改动了 srv。"""
    if not isinstance(srv, dict):
        return False
    changed = backfill_primary_domain(srv)
    resolved = await resolve_ssh_host_to_ip(srv, use_cache=use_cache, cf_lookup=cf_lookup, use_cf=use_cf)
    return resolved or changed


async def normalize_all_server_host_fields(servers=None, use_cache=True, concurrency=16, budget=20.0):
    """
    批量执行主机字段归位。DNS 解析并发跑并带总时长预算，超时只是提前收工
    （已改好的服务器会被计入，调用方照样会落库），不会卡住调用方。
    返回被改动的服务器数量。
    """
    targets = [s for s in (SERVERS_CACHE if servers is None else servers) if isinstance(s, dict)]
    if not targets:
        return 0

    sem = asyncio.Semaphore(concurrency)
    changed = set()
    cf_lookup = make_cf_origin_ip_lookup() if any(
        str(s.get('ssh_host') or '').strip() and not is_ip_literal(str(s.get('ssh_host') or '').strip())
        for s in targets
    ) else None

    async def _one(srv):
        async with sem:
            try:
                # 同步那步先记账，这样即使后面解析被预算掐断，改动也不会漏计
                if backfill_primary_domain(srv):
                    changed.add(id(srv))
                if await resolve_ssh_host_to_ip(srv, use_cache=use_cache, cf_lookup=cf_lookup):
                    changed.add(id(srv))
            except Exception as e:
                logger.warning(f"⚠️ [主机归位] {srv.get('name', 'Unknown')} 处理失败: {e}")

    task = asyncio.gather(*[_one(s) for s in targets])
    try:
        if budget:
            await asyncio.wait_for(task, budget)
        else:
            await task
    except asyncio.TimeoutError:
        logger.warning(f"⏳ [主机归位] 超过 {budget}s 预算，本轮提前结束 (已修正 {len(changed)} 台)")
    except Exception as e:
        logger.error(f"❌ [主机归位] 批量处理异常: {e}")

    return len(changed)


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
        inbounds = await mgr.get_inbounds()
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

    # 仅修改单行数据时调用单行写入方法，极大提升页面响应速度
    await save_single_server(server_data)

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
