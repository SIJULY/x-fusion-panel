import asyncio
import json
import re
import socket
import time

from fastapi import Request
from fastapi.responses import Response
from nicegui import run

from app.core.config import AUTO_COUNTRY_MAP, AUTO_REGISTER_SECRET, PROBE_INSTALL_SCRIPT
from app.core.logging import logger
from app.core.state import (
    ADMIN_CONFIG,
    NODES_DATA,
    PING_CACHE,
    PING_TREND_CACHE,
    PROBE_DATA_CACHE,
    PROCESS_POOL,
    SERVERS_CACHE,
)
from app.services.ssh import get_ssh_client_sync
from app.storage.repositories import save_servers
from app.utils.geo import get_flag_for_country
from app.utils.network import sync_ping_worker


# 延迟导入的跨模块函数：
# - safe_notify / render_sidebar_content 位于 UI 层
# - refresh_dashboard_ui / force_geoip_naming_task 位于业务/页面相关模块
# 为避免当前迁移阶段的循环导入，保持在函数内部按需导入。


def record_ping_history(url, pings_dict):
    """
    后台收到数据调用此函数记录历史。
    ✨ 新增逻辑：同一服务器，至少间隔 60 秒才记录一次数据 (防抖)。
    """
    if not url or not pings_dict:
        return

    current_ts = time.time()

    if url not in PING_TREND_CACHE:
        PING_TREND_CACHE[url] = []

    if PING_TREND_CACHE[url]:
        last_record = PING_TREND_CACHE[url][-1]
        if current_ts - last_record['ts'] < 60:
            return

    import datetime
    time_str = datetime.datetime.fromtimestamp(current_ts).strftime('%m/%d %H:%M')

    ct = pings_dict.get('电信', 0)
    ct = ct if ct > 0 else 0
    cu = pings_dict.get('联通', 0)
    cu = cu if cu > 0 else 0
    cm = pings_dict.get('移动', 0)
    cm = cm if cm > 0 else 0

    PING_TREND_CACHE[url].append({
        'ts': current_ts,
        'time_str': time_str,
        'ct': ct,
        'cu': cu,
        'cm': cm,
    })

    if len(PING_TREND_CACHE[url]) > 1000:
        PING_TREND_CACHE[url] = PING_TREND_CACHE[url][-1000:]


async def install_probe_on_server(server_conf):
    name = server_conf.get('name', 'Unknown')
    auth_type = server_conf.get('ssh_auth_type', '全局密钥')
    if auth_type == '独立密码' and not server_conf.get('ssh_password'):
        return False
    if auth_type == '独立密钥' and not server_conf.get('ssh_key'):
        return False

    my_token = ADMIN_CONFIG.get('probe_token', 'default_token')

    manager_url = ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')

    ping_ct = ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')
    ping_cu = ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')
    ping_cm = ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')

    real_script = PROBE_INSTALL_SCRIPT \
        .replace("__MANAGER_URL__", manager_url) \
        .replace("__TOKEN__", my_token) \
        .replace("__SERVER_URL__", server_conf['url']) \
        .replace("__PING_CT__", ping_ct) \
        .replace("__PING_CU__", ping_cu) \
        .replace("__PING_CM__", ping_cm)

    def _do_install():
        client = None
        try:
            client, msg = get_ssh_client_sync(server_conf)
            if not client:
                return False, f"SSH连接失败: {msg}"
            stdin, stdout, stderr = client.exec_command(real_script, timeout=60)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status == 0:
                verify_cmd = "test -f /root/x_fusion_agent.py && test -f /etc/systemd/system/x-fusion-agent.service && systemctl is-active --quiet x-fusion-agent"
                _, verify_stdout, verify_stderr = client.exec_command(verify_cmd, timeout=20)
                verify_status = verify_stdout.channel.recv_exit_status()
                if verify_status == 0:
                    return True, "Agent 安装成功并启动"
                verify_error = verify_stderr.read().decode().strip()
                return False, f"安装后校验失败 (Exit {verify_status}){': ' + verify_error if verify_error else ''}"
            return False, f"安装脚本错误 (Exit {exit_status})"
        except Exception as e:
            return False, f"异常: {str(e)}"
        finally:
            if client:
                client.close()

    success, msg = await run.io_bound(_do_install)
    if success:
        server_conf['probe_installed'] = True
        await save_servers()
        logger.info(f"✅ [Push Agent] {name} 部署成功")
    else:
        logger.warning(f"⚠️ [Push Agent] {name} 部署失败: {msg}")
    return success


async def batch_install_all_probes():
    if not SERVERS_CACHE:
        from app.ui.common.notifications import safe_notify

        safe_notify("没有服务器可安装", "warning")
        return

    from app.ui.common.notifications import safe_notify

    safe_notify(f"正在后台为 {len(SERVERS_CACHE)} 台服务器安装/更新探针...", "ongoing")

    sema = asyncio.Semaphore(10)

    async def _worker(server_conf):
        name = server_conf.get('name', 'Unknown')
        async with sema:
            logger.info(f"🚀 [AutoInstall] {name} 开始安装...")
            success = await install_probe_on_server(server_conf)

    tasks = [_worker(s) for s in SERVERS_CACHE]

    if tasks:
        await asyncio.gather(*tasks)

    safe_notify("✅ 所有探针安装/更新任务已完成", "positive")


async def get_server_status(server_conf):
    raw_url = server_conf['url']

    if server_conf.get('probe_installed', False) or raw_url in PROBE_DATA_CACHE:
        cache = PROBE_DATA_CACHE.get(raw_url)
        if cache:
            if time.time() - cache.get('last_updated', 0) < 15:
                return cache
            else:
                return {'status': 'offline', 'msg': '探针离线 (超时)'}

    return {'status': 'offline', 'msg': '未安装探针'}


async def batch_ping_nodes(nodes, raw_host):
    """
    使用多进程池并行 Ping，彻底解放主线程。
    """
    if not PROCESS_POOL:
        return

    loop = asyncio.get_running_loop()

    targets = []
    for n in nodes:
        host = n.get('listen')
        if not host or host == '0.0.0.0':
            host = raw_host
        port = n.get('port')
        key = f"{host}:{port}"
        targets.append((host, port, key))

    async def run_single_ping(t_host, t_port, t_key):
        try:
            latency = await loop.run_in_executor(PROCESS_POOL, sync_ping_worker, t_host, t_port)
            PING_CACHE[t_key] = latency
        except:
            PING_CACHE[t_key] = -1

    tasks = [run_single_ping(h, p, k) for h, p, k in targets]
    if tasks:
        await asyncio.gather(*tasks)


async def probe_push_data(request: Request):
    try:
        data = await request.json()
        token = data.get('token')
        server_url = data.get('server_url')

        correct_token = ADMIN_CONFIG.get('probe_token')
        if not token or token != correct_token:
            return Response("Invalid Token", 403)

        target_server = next((s for s in SERVERS_CACHE if s['url'] == server_url), None)
        if not target_server:
            try:
                push_ip = server_url.split('://')[-1].split(':')[0]
                for s in SERVERS_CACHE:
                    cache_ip = s['url'].split('://')[-1].split(':')[0]
                    if cache_ip == push_ip:
                        target_server = s
                        break
            except:
                pass

        if target_server:
            if not target_server.get('probe_installed'):
                target_server['probe_installed'] = True

            data['status'] = 'online'
            data['last_updated'] = time.time()
            PROBE_DATA_CACHE[target_server['url']] = data

            if 'xui_data' in data and isinstance(data['xui_data'], list):
                raw_nodes = data['xui_data']
                parsed_nodes = []
                for n in raw_nodes:
                    try:
                        if isinstance(n.get('settings'), str):
                            n['settings'] = json.loads(n['settings'])
                        if isinstance(n.get('streamSettings'), str):
                            n['streamSettings'] = json.loads(n['streamSettings'])
                        parsed_nodes.append(n)
                    except:
                        parsed_nodes.append(n)

                NODES_DATA[target_server['url']] = parsed_nodes
                target_server['_status'] = 'online'

                if parsed_nodes:
                    first_remark = parsed_nodes[0].get('remark', '').strip()
                    current_name = target_server.get('name', '').strip()

                    if first_remark and (first_remark not in current_name):
                        has_own_flag = False
                        for v in AUTO_COUNTRY_MAP.values():
                            known_flag = v.split(' ')[0]
                            if known_flag in first_remark:
                                has_own_flag = True
                                break

                        if has_own_flag:
                            new_name_candidate = first_remark
                        else:
                            flag = "🏳️"
                            if ' ' in current_name:
                                parts = current_name.split(' ', 1)
                                if len(parts[0]) < 10:
                                    flag = parts[0]
                            else:
                                try:
                                    from app.core.state import IP_GEO_CACHE

                                    ip_key = target_server['url'].split('://')[-1].split(':')[0]
                                    geo_info = IP_GEO_CACHE.get(ip_key)
                                    if geo_info:
                                        flag = get_flag_for_country(geo_info[2]).split(' ')[0]
                                except:
                                    pass

                            new_name_candidate = f"{flag} {first_remark}"

                        if target_server['name'] != new_name_candidate:
                            target_server['name'] = new_name_candidate
                            asyncio.create_task(save_servers())
                            logger.info(f"🏷️ [探针同步] 根据节点备注自动改名: {new_name_candidate}")

            record_ping_history(target_server['url'], data.get('pings', {}))

        return Response("OK", 200)
    except Exception:
        return Response("Error", 500)


async def probe_register(request: Request):
    try:
        data = await request.json()

        submitted_token = data.get('token')
        correct_token = ADMIN_CONFIG.get('probe_token')

        if not submitted_token or submitted_token != correct_token:
            return Response(json.dumps({"success": False, "msg": "Token 错误"}), status_code=403)

        client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()

        target_server = None

        for s in SERVERS_CACHE:
            if client_ip in s['url']:
                target_server = s
                break

        if not target_server:
            logger.info(f"🔍 [探针注册] IP {client_ip} 未直接匹配，尝试解析现有域名...")
            for s in SERVERS_CACHE:
                try:
                    cached_host = s['url'].split('://')[-1].split(':')[0]

                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", cached_host):
                        continue

                    resolved_ip = await run.io_bound(socket.gethostbyname, cached_host)

                    if resolved_ip == client_ip:
                        target_server = s
                        logger.info(f"✅ [探针注册] 域名 {cached_host} 解析为 {client_ip}，匹配成功！")
                        break
                except:
                    pass

        if target_server:
            if not target_server.get('probe_installed'):
                target_server['probe_installed'] = True
                await save_servers()

                from app.ui.components.dashboard import refresh_dashboard_ui

                await refresh_dashboard_ui()

            return Response(json.dumps({"success": True, "msg": "已合并现有服务器"}), status_code=200)

        else:
            new_server = {
                'name': f"🏳️ {client_ip}",
                'group': '自动注册',
                'url': f"http://{client_ip}:54321",
                'user': 'admin',
                'pass': 'admin',
                'ssh_auth_type': '全局密钥',
                'probe_installed': True,
                '_status': 'online',
            }
            SERVERS_CACHE.append(new_server)
            await save_servers()

            from app.services.server_ops import force_geoip_naming_task
            from app.ui.components.dashboard import refresh_dashboard_ui
            from app.ui.components.sidebar import render_sidebar_content

            asyncio.create_task(force_geoip_naming_task(new_server))

            await refresh_dashboard_ui()
            try:
                render_sidebar_content.refresh()
            except:
                pass

            logger.info(f"✨ [主动注册] 新服务器上线: {client_ip}")
            return Response(json.dumps({"success": True, "msg": "注册成功"}), status_code=200)

    except Exception as e:
        logger.error(f"❌ 注册接口异常: {e}")
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500)


async def smart_detect_ssh_user_task(server_conf):
    """
    后台任务：尝试使用不同的用户名 (ubuntu -> root) 连接 SSH。
    连接成功后：
    1. 更新配置并保存。
    2. 自动触发探针安装。
    """
    candidates = ['root', 'ubuntu']

    ip = server_conf['url'].split('://')[-1].split(':')[0]
    original_user = server_conf.get('ssh_user', '')

    logger.info(f"🕵️‍♂️ [智能探测] 开始探测 {server_conf['name']} ({ip}) 的 SSH 用户名...")

    found_user = None

    for user in candidates:
        server_conf['ssh_user'] = user

        client, msg = await run.io_bound(get_ssh_client_sync, server_conf)

        if client:
            client.close()
            found_user = user
            logger.info(f"✅ [智能探测] 成功匹配用户名: {user}")
            break
        else:
            logger.warning(f"⚠️ [智能探测] 用户名 '{user}' 连接失败，尝试下一个...")

    if found_user:
        server_conf['ssh_user'] = found_user
        server_conf['_ssh_verified'] = True
        await save_servers()

        if ADMIN_CONFIG.get('probe_enabled', False):
            logger.info(f"🚀 [自动部署] SSH 验证通过，开始安装探针...")
            await asyncio.sleep(2)
            await install_probe_on_server(server_conf)

    else:
        logger.error(f"❌ [智能探测] {server_conf['name']} 所有用户名均尝试失败 (请检查安全组或密钥)")
        if original_user:
            server_conf['ssh_user'] = original_user
        await save_servers()


async def auto_register_node(request: Request):
    try:
        data = await request.json()

        secret = data.get('secret')
        if secret != AUTO_REGISTER_SECRET:
            logger.warning(f"⚠️ [自动注册] 密钥错误: {secret}")
            return Response(json.dumps({"success": False, "msg": "密钥错误"}), status_code=403, media_type="application/json")

        ip = data.get('ip')
        port = data.get('port')
        username = data.get('username')
        password = data.get('password')
        alias = data.get('alias', f'Auto-{ip}')

        ssh_port = data.get('ssh_port', 22)

        if not all([ip, port, username, password]):
            return Response(json.dumps({"success": False, "msg": "参数不完整"}), status_code=400, media_type="application/json")

        target_url = f"http://{ip}:{port}"

        new_server_config = {
            'name': alias,
            'group': '默认分组',
            'url': target_url,
            'user': username,
            'pass': password,
            'prefix': '',
            'ssh_port': ssh_port,
            'ssh_auth_type': '全局密钥',
            'ssh_user': 'detecting...',
            'probe_installed': False,
        }

        existing_index = -1
        for idx, srv in enumerate(SERVERS_CACHE):
            cache_url = srv['url'].replace('http://', '').replace('https://', '')
            new_url_clean = target_url.replace('http://', '').replace('https://', '')
            if cache_url == new_url_clean:
                existing_index = idx
                break

        action_msg = ""
        target_server_ref = None

        if existing_index != -1:
            SERVERS_CACHE[existing_index].update(new_server_config)
            target_server_ref = SERVERS_CACHE[existing_index]
            action_msg = f"🔄 更新节点: {alias}"
        else:
            SERVERS_CACHE.append(new_server_config)
            target_server_ref = new_server_config
            action_msg = f"✅ 新增节点: {alias}"

        await save_servers()

        from app.services.server_ops import force_geoip_naming_task
        from app.ui.components.sidebar import render_sidebar_content

        asyncio.create_task(force_geoip_naming_task(target_server_ref))
        asyncio.create_task(smart_detect_ssh_user_task(target_server_ref))

        try:
            render_sidebar_content.refresh()
        except:
            pass

        logger.info(f"[自动注册] {action_msg} ({ip}) - 已加入 SSH 探测与命名队列")
        return Response(json.dumps({"success": True, "msg": "注册成功，后台正在探测连接..."}), status_code=200, media_type="application/json")

    except Exception as e:
        logger.error(f"❌ [自动注册] 处理异常: {e}")
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500, media_type="application/json")
