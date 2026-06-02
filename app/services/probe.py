import asyncio
import json
import re
import shlex
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
from app.services.xui_fetch import merge_local_node_fields
from app.services.traffic_guard import check_and_handle_traffic_limit
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
        logger.warning(f"⚠️ [Push Agent] {name} 跳过安装：认证方式为独立密码，但未保存 SSH 密码")
        return False
    if auth_type == '独立密钥' and not server_conf.get('ssh_key'):
        logger.warning(f"⚠️ [Push Agent] {name} 跳过安装：认证方式为独立密钥，但未保存 SSH 私钥")
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

    def _sudo_wrap_command(command: str) -> str:
        """后台 SSH 推送安装时使用的非交互式提权包装。

        探针需要写入 /root 和 /etc/systemd/system，并启动 systemd 服务，
        因此非 root SSH 用户必须通过 sudo 提权。这里不使用 `sudo -i`，
        因为 `sudo -i` 会进入交互式登录 shell，后台 exec_command 容易卡住。
        """
        ssh_user = (server_conf.get('ssh_user') or 'root').strip()
        if ssh_user == 'root':
            return command

        if auth_type == '独立密码' and server_conf.get('ssh_password'):
            sudo_password = shlex.quote(server_conf.get('ssh_password', ''))
            return f"printf '%s\\n' {sudo_password} | sudo -S -p '' {command}"

        return f"sudo -n {command}"

    def _extract_install_script_body(script: str) -> str:
        """把配置中的 `bash -c '...'` 模板提取成可通过 stdin 传给 bash 的纯脚本。

        之前直接把整段 `bash -c '...'` 拼到 sudo 后面执行时，模板内部的
        `exec sudo bash "$0" "$@"` 在 `bash -c` 场景下会把 `$0` 解析成 bash
        二进制路径，最终导致远端报：`/usr/bin/bash: cannot execute binary file`。
        后台 SSH 推送本身已经负责 sudo 提权，因此这里移除模板内自提权行，
        并统一用 `sudo bash -s` 执行 stdin 脚本。
        """
        body = script.strip()
        if body.startswith("bash -c '") and body.endswith("'"):
            body = body[len("bash -c '"):-1]
        body = body.lstrip('\n')
        body = re.sub(
            r'(?m)^# 1\. 提升权限\n\[ "\$\(id -u\)" -eq 0 \].*?exit 1; \}\n\n?',
            '',
            body,
            count=1,
        )
        return body.strip() + '\n'

    def _build_install_command(script: str) -> str:
        body = _extract_install_script_body(script)
        eof = 'XFUSION_PROBE_INSTALL_EOF'
        ssh_user = (server_conf.get('ssh_user') or 'root').strip()
        if ssh_user == 'root':
            return f"bash -s <<'{eof}'\n{body}{eof}"

        if auth_type == '独立密码' and server_conf.get('ssh_password'):
            sudo_password = shlex.quote(server_conf.get('ssh_password', ''))
            return f"printf '%s\\n' {sudo_password} | sudo -S -p '' bash -s <<'{eof}'\n{body}{eof}"

        return f"sudo -n bash -s <<'{eof}'\n{body}{eof}"

    install_command = _build_install_command(real_script)

    def _exec_with_timeout(client, command: str, timeout_seconds: int, get_pty: bool = False):
        """执行 SSH 命令并强制限制总耗时，避免 sudo/apt/systemctl 卡住导致页面一直 loading。"""
        stdin, stdout, stderr = client.exec_command(command, timeout=10, get_pty=get_pty)
        channel = stdout.channel
        deadline = time.time() + timeout_seconds

        while not channel.exit_status_ready():
            if time.time() > deadline:
                try:
                    channel.close()
                except Exception:
                    pass
                return 124, '', f'命令执行超时 ({timeout_seconds}s)，可能卡在 sudo 密码、软件源安装或 systemd 启动'
            time.sleep(0.2)

        exit_status = channel.recv_exit_status()
        stdout_text = stdout.read().decode(errors='ignore').strip()
        stderr_text = stderr.read().decode(errors='ignore').strip()
        return exit_status, stdout_text, stderr_text

    def _do_install():
        client = None
        try:
            client, msg = get_ssh_client_sync(server_conf)
            if not client:
                return False, f"SSH连接失败: {msg}"
            needs_pty = (server_conf.get('ssh_user') or 'root').strip() != 'root'
            exit_status, install_out, install_err = _exec_with_timeout(client, install_command, 120, get_pty=needs_pty)
            if exit_status == 0:
                verify_cmd = _sudo_wrap_command("test -f /root/x_fusion_agent.py && test -f /etc/systemd/system/x-fusion-agent.service && systemctl is-active --quiet x-fusion-agent")
                verify_status, verify_out, verify_err = _exec_with_timeout(client, verify_cmd, 30, get_pty=needs_pty)
                if verify_status == 0:
                    return True, "Agent 安装成功并启动"
                verify_error = verify_err or verify_out
                return False, f"安装后校验失败 (Exit {verify_status}){': ' + verify_error if verify_error else ''}"
            error_detail = install_err or install_out
            if exit_status == 124:
                return False, error_detail
            return False, f"安装脚本错误 (Exit {exit_status}){': ' + error_detail if error_detail else ''}"
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
        server_conf['probe_installed'] = False
        await save_servers()
        logger.warning(f"⚠️ [Push Agent] {name} 部署失败: {msg}")
    return success


async def batch_install_all_probes():
    if not SERVERS_CACHE:
        from app.ui.common.notifications import safe_notify

        safe_notify("没有服务器可安装", "warning")
        logger.warning("⚠️ [AutoInstall] 批量更新探针被触发，但服务器列表为空")
        return

    from app.ui.common.notifications import safe_notify

    logger.info(f"🚀 [AutoInstall] 批量更新探针已触发，服务器数量={len(SERVERS_CACHE)}")
    safe_notify(f"已开始后台安装/更新 {len(SERVERS_CACHE)} 台探针，页面无需等待", "ongoing")

    async def _run_batch_install():
        logger.info("🚀 [AutoInstall] 后台批量任务已启动")
        sema = asyncio.Semaphore(10)

        async def _worker(server_conf):
            name = server_conf.get('name', 'Unknown')
            async with sema:
                logger.info(f"🚀 [AutoInstall] {name} 开始安装...")
                success = await install_probe_on_server(server_conf)
                logger.info(f"{'✅' if success else '❌'} [AutoInstall] {name} 安装结果={'成功' if success else '失败'}")
                return success

        tasks = [_worker(s) for s in SERVERS_CACHE]

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            ok_count = sum(1 for r in results if r is True)
            fail_count = len(results) - ok_count
        else:
            ok_count = 0
            fail_count = 0

        logger.info(f"✅ [AutoInstall] 批量更新探针完成：成功={ok_count} 失败={fail_count}")
        safe_notify(f"✅ 探针安装/更新完成：成功 {ok_count}，失败 {fail_count}。失败原因请查看后端日志。", "positive" if fail_count == 0 else "warning", timeout=5000)

    asyncio.create_task(_run_batch_install())


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

                parsed_nodes = merge_local_node_fields(target_server['url'], parsed_nodes)
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

            asyncio.create_task(check_and_handle_traffic_limit(target_server, data))
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
