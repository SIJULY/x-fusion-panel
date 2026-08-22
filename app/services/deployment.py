import asyncio
import shlex

from nicegui import app, run, ui

from app.core.config import (
    HYSTERIA_INSTALL_SCRIPT_TEMPLATE,
    SNELL_INSTALL_SCRIPT_TEMPLATE,
    XHTTP_INSTALL_SCRIPT_TEMPLATE,
)
from app.services.cloudflare import CloudflareHandler
from app.services.ssh import _ssh_exec_wrapper
from app.storage.repositories import save_servers
from app.utils.encoding import parse_vless_link_to_node


def _deploy_is_dark():
    return bool(app.storage.user.get('is_dark', True))


def _deploy_dialog_card():
    return 'w-[560px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if _deploy_is_dark() else 'w-[560px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'


def _deploy_dialog_header(icon, title, subtitle):
    is_dark = _deploy_is_dark()
    header_cls = 'w-full bg-gradient-to-r from-[#0a1526] to-[#050a14] p-6 gap-2 border-b border-[#1e3a5f]/60 relative overflow-hidden' if is_dark else 'w-full bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] p-6 gap-2 border-b border-slate-300/90 relative overflow-hidden'
    icon_box_cls = 'w-9 h-9 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] shadow-[0_0_8px_rgba(0,0,0,0.7)] text-cyan-400 relative overflow-hidden' if is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] text-sky-600 relative overflow-hidden'
    title_cls = 'text-lg font-black text-slate-100 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide'
    subtitle_cls = 'text-[10px] text-slate-500 font-mono tracking-wide' if is_dark else 'text-[10px] text-slate-500 font-mono tracking-wide'
    overlay_cls = 'absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNCwyMTEsMjM4LDAuMDcpIi8+PC9zdmc+")] opacity-100 pointer-events-none' if is_dark else 'absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNyw5OSwyMzUsMC4wNyk iLz48L3N2Zz4=")] opacity-100 pointer-events-none'
    with ui.column().classes(header_cls):
        ui.element('div').classes(overlay_cls)
        with ui.row().classes('items-center gap-3 z-10'):
            with ui.element('div').classes(icon_box_cls):
                ui.element('div').classes('absolute inset-0 bg-cyan-400/10' if is_dark else 'absolute inset-0 bg-sky-400/10')
                ui.icon(icon, size='md').classes('drop-shadow-[0_0_5px_currentColor]')
            with ui.column().classes('gap-0'):
                ui.label(title).classes(title_cls)
                ui.label(subtitle).classes(subtitle_cls)


def _deploy_body():
    return 'w-full p-6 gap-4 bg-[#030712]' if _deploy_is_dark() else 'w-full p-6 gap-4 bg-[#f8fbff]'


def _deploy_field_box(width_classes='w-full'):
    return f'{width_classes} rounded-sm border border-[#1e3a5f]/45 bg-[#08101d]/80 px-3 py-2 shadow-[0_0_8px_rgba(0,0,0,0.35)] transition-all hover:border-cyan-500/35' if _deploy_is_dark() else f'{width_classes} rounded-sm border border-slate-300/90 bg-white px-3 py-2 shadow-[0_4px_12px_rgba(148,163,184,0.10)] transition-all hover:border-sky-400/60'


def _deploy_input_props(extra=''):
    suffix = f' {extra}' if extra else ''
    return f'dense outlined dark color=cyan standout bg-color="[#050b14]"{suffix}' if _deploy_is_dark() else f'dense outlined color=blue{suffix}'


def _deploy_log_classes():
    return 'w-full h-48 bg-black/90 text-green-400 text-[11px] font-mono p-3 rounded-sm border border-[#1e3a5f]/60 hidden transition-all' if _deploy_is_dark() else 'w-full h-48 bg-slate-900 text-green-400 text-[11px] font-mono p-3 rounded-sm border border-slate-300/90 hidden transition-all'


def _deploy_footer():
    return 'w-full p-4 bg-gradient-to-r from-[#0a1526] to-[#050a14] border-t border-[#1e3a5f]/60 justify-end gap-3' if _deploy_is_dark() else 'w-full p-4 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] border-t border-slate-300/90 justify-end gap-3'


def _deploy_cancel_button(on_click):
    return ui.button('取消', on_click=on_click).props('outline color=grey').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if _deploy_is_dark() else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm')


def _deploy_confirm_button(label, on_click):
    return ui.button(label, on_click=on_click).props('flat').classes('bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 hover:shadow-[0_0_12px_rgba(34,211,238,0.32)] px-6 font-black text-xs tracking-wide rounded-sm' if _deploy_is_dark() else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 hover:shadow-[0_6px_16px_rgba(56,189,248,0.18)] px-6 font-black text-xs tracking-wide rounded-sm')


def _push_deploy_output(log_area, output, empty_hint='(无输出，请检查 SSH 主机、端口、认证方式或服务器防火墙设置)'):
    text = (output or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        log_area.push(empty_hint)
        return
    for line in text.split('\n'):
        line = line.rstrip()
        if line:
            log_area.push(line)


def _build_privileged_script_command(server_conf, script_content: str, *args) -> str:
    """构造可兼容 root/非 root SSH 用户的一键部署命令。

    XHTTP/Hysteria/Snell 部署脚本都会安装依赖、写入 /etc、创建 systemd 服务，
    因此非 root SSH 用户必须 sudo 提权。这里不再先写 /tmp 脚本后普通 bash 执行，
    而是统一把脚本通过 stdin 传给 root bash，避免权限不足和 sudo 交互卡住。
    """
    eof = 'XFUSION_DEPLOY_SCRIPT_EOF'
    quoted_args = ' '.join(shlex.quote(str(arg)) for arg in args)
    arg_suffix = f' -- {quoted_args}' if quoted_args else ''
    body = script_content.strip() + '\n'
    ssh_user = (server_conf.get('ssh_user') or 'root').strip()

    if ssh_user == 'root':
        return f"bash -s{arg_suffix} <<'{eof}'\n{body}{eof}"

    auth_type = server_conf.get('ssh_auth_type', '全局密钥').strip()
    if auth_type == '独立密码' and server_conf.get('ssh_password'):
        sudo_password = shlex.quote(server_conf.get('ssh_password', ''))
        return (
            f"{{ printf '%s\\n' {sudo_password}; cat <<'{eof}'\n"
            f"{body}{eof}\n"
            f"}} | sudo -S -p '' bash -s{arg_suffix}"
        )

    return f"sudo -n bash -s{arg_suffix} <<'{eof}'\n{body}{eof}"


async def open_deploy_xhttp_dialog(server_conf, callback):
    # 1. 准备 IP
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    real_ip = target_host
    import re, socket
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try:
            real_ip = await run.io_bound(socket.gethostbyname, target_host)
        except:
            from app.ui.common.notifications import safe_notify

            safe_notify(f"❌ 无法解析 IP: {target_host}", "negative")
            return

    # 2. 检查 CF
    cf_handler = CloudflareHandler()
    if not cf_handler.token or not cf_handler.root_domain:
        from app.ui.common.notifications import safe_notify

        safe_notify("❌ 请先配置 Cloudflare API", "negative")
        return

    # 3. 生成域名
    import random, string
    rand_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    sub_prefix = f"node-{real_ip.replace('.', '-')}-{rand_suffix}"
    target_domain = f"{sub_prefix}.{cf_handler.root_domain}"

    with ui.dialog() as d, ui.card().classes(_deploy_dialog_card()):
        _deploy_dialog_header('rocket_launch', '部署 XHTTP-Reality (V76)', f'部署目标: {target_domain}')

        with ui.column().classes(_deploy_body()):
            ui.label('节点备注名称').classes('text-[11px] font-bold tracking-wide mb-[-8px] text-cyan-500/80' if _deploy_is_dark() else 'text-[11px] font-bold tracking-wide mb-[-8px] text-sky-700/80')
            with ui.element('div').classes(_deploy_field_box()):
                remark_input = ui.input(placeholder=f'默认: Reality-{target_domain}').props(_deploy_input_props('clearable')).classes('w-full')
            log_area = ui.log().classes(_deploy_log_classes())

        with ui.row().classes(_deploy_footer()):
            btn_cancel = _deploy_cancel_button(d.close)

            async def run_deploy_script():
                try:
                    log_area.push(f"🔄 [CF] 添加解析: {target_domain} -> {real_ip}...")
                    success, msg = await cf_handler.auto_configure(real_ip, sub_prefix)
                    if not success:
                        raise Exception(f"CF配置失败: {msg}")

                    log_area.push(f"🚀 [SSH] 开始执行安装脚本...")

                    deploy_cmd = _build_privileged_script_command(server_conf, XHTTP_INSTALL_SCRIPT_TEMPLATE, target_domain)
                    success, output = await _ssh_exec_wrapper(server_conf, deploy_cmd)
                    _push_deploy_output(log_area, output)

                    if success:
                        match = re.search(r'DEPLOY_SUCCESS_LINK: (vless://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("✅ 部署成功！正在保存...")

                            custom_name = remark_input.value.strip()
                            final_remark = custom_name if custom_name else f"Reality-{target_domain}"
                            node_data = parse_vless_link_to_node(link, remark_override=final_remark)

                            if node_data:
                                if 'custom_nodes' not in server_conf:
                                    server_conf['custom_nodes'] = []
                                server_conf['custom_nodes'].append(node_data)
                                await save_servers()
                                from app.ui.common.notifications import safe_notify

                                safe_notify(f"✅ 节点已添加", "positive")
                                await asyncio.sleep(1)
                                d.close()
                                if callback:
                                    await callback()
                            else:
                                log_area.push("❌ 链接解析失败")
                        else:
                            log_area.push("❌ 未捕获链接，请检查完整日志输出")
                    else:
                        log_area.push("❌ SSH 执行出错，请查看上方详细日志")
                except Exception as e:
                    log_area.push(f"❌ 异常: {str(e)}")
                finally:
                    btn_deploy.props(remove='loading')
                    btn_cancel.enable()

            async def start_process():
                btn_cancel.disable()
                btn_deploy.props('loading')
                log_area.classes(remove='hidden')

                log_area.push("🔍 正在检查端口占用 (80/443)...")

                check_cmd = "netstat -tlpn | grep -E ':80 |:443 ' || lsof -i :80 -i :443"
                is_occupied = False
                check_output = ""

                try:
                    success, output = await _ssh_exec_wrapper(server_conf, check_cmd)
                    if success and output.strip():
                        is_occupied = True
                        check_output = output.strip()
                except:
                    pass

                if is_occupied:
                    log_area.push("⚠️ 端口被占用！等待确认...")

                    with ui.dialog() as confirm_d, ui.card().classes('w-96 p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-rose-800/60 shadow-[0_18px_40px_rgba(0,0,0,0.7)]' if _deploy_is_dark() else 'w-96 p-0 gap-0 overflow-hidden rounded-sm bg-white border border-rose-300 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                        with ui.column().classes('w-full p-5 gap-3 bg-gradient-to-r from-[#19070d] to-[#0b0911] border-b border-rose-900/60' if _deploy_is_dark() else 'w-full p-5 gap-3 bg-gradient-to-r from-rose-50 to-orange-50 border-b border-rose-200'):
                            with ui.row().classes('items-center gap-2 text-rose-400'):
                                ui.icon('warning', size='md').classes('drop-shadow-[0_0_6px_currentColor]')
                                ui.label('端口冲突警告').classes('font-black text-lg tracking-wide')
                            ui.label('检测到 80 或 443 端口被占用：').classes('text-sm text-slate-300' if _deploy_is_dark() else 'text-sm text-slate-600')
                        with ui.column().classes('w-full p-5 gap-3 bg-[#030712]' if _deploy_is_dark() else 'w-full p-5 gap-3 bg-[#f8fbff]'):
                            short_log = "\n".join(check_output.split("\n")[:5])
                            ui.code(short_log).classes('w-full text-xs bg-black/90 text-slate-300 p-2 rounded-sm border border-slate-800 mb-1' if _deploy_is_dark() else 'w-full text-xs bg-slate-900 text-slate-200 p-2 rounded-sm border border-slate-300 mb-1')
                            ui.label('继续部署将【强制杀掉】这些进程。').classes('text-xs font-bold text-rose-400 tracking-wide')
                        with ui.row().classes('w-full justify-end gap-2 p-4 border-t border-rose-900/40 bg-[#0b0911]' if _deploy_is_dark() else 'w-full justify-end gap-2 p-4 border-t border-rose-200 bg-rose-50/60'):
                            _deploy_cancel_button(lambda: [confirm_d.close(), d.close()])

                            async def confirm_force():
                                confirm_d.close()
                                log_area.push("⚔️ 用户确认强制霸占，继续...")
                                await run_deploy_script()

                            ui.button('强制部署', color='red', on_click=confirm_force).props('flat').classes('bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 hover:shadow-[0_0_12px_rgba(244,63,94,0.28)] px-5 font-black text-xs tracking-wide rounded-sm')

                    confirm_d.open()

                else:
                    log_area.push("✅ 端口空闲，开始部署...")
                    await run_deploy_script()

            btn_deploy = _deploy_confirm_button('开始部署', start_process)

    d.open()


async def open_deploy_hysteria_dialog(server_conf, callback):
    # --- 1. IP 获取逻辑 ---
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    real_ip = target_host
    import re, socket, urllib.parse, uuid, asyncio

    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try:
            real_ip = await run.io_bound(socket.gethostbyname, target_host)
        except:
            from app.ui.common.notifications import safe_notify

            safe_notify(f"❌ 无法解析 IP: {target_host}", "negative")
            return

    with ui.dialog() as d, ui.card().classes(_deploy_dialog_card()):
        _deploy_dialog_header('bolt', '部署 Hysteria 2 (Surge 兼容版)', f'服务器 IP: {real_ip}')

        with ui.column().classes(_deploy_body()):
            with ui.element('div').classes(_deploy_field_box()):
                name_input = ui.input('节点备注 (可选)', placeholder='例如: 狮城 Hy2').props(_deploy_input_props()).classes('w-full')
            with ui.element('div').classes(_deploy_field_box()):
                sni_input = ui.input('伪装域名 (SNI)', value='www.bing.com').props(_deploy_input_props()).classes('w-full')

            enable_hopping = ui.checkbox('启用端口跳跃', value=True).props('dark color=cyan' if _deploy_is_dark() else 'color=blue').classes('text-sm font-bold text-slate-300' if _deploy_is_dark() else 'text-sm font-bold text-slate-700')
            with ui.row().classes('w-full items-center gap-2'):
                with ui.element('div').classes(_deploy_field_box('flex-1')):
                    hop_start = ui.number('起始端口', value=20000, format='%.0f').props(_deploy_input_props()).classes('w-full').bind_visibility_from(enable_hopping, 'value')
                ui.label('-').classes('text-slate-400' if _deploy_is_dark() else 'text-slate-500').bind_visibility_from(enable_hopping, 'value')
                with ui.element('div').classes(_deploy_field_box('flex-1')):
                    hop_end = ui.number('结束端口', value=50000, format='%.0f').props(_deploy_input_props()).classes('w-full').bind_visibility_from(enable_hopping, 'value')

            log_area = ui.log().classes(_deploy_log_classes())

        with ui.row().classes(_deploy_footer()):
            btn_cancel = _deploy_cancel_button(d.close)

            async def start_process():
                btn_cancel.disable()
                btn_deploy.props('loading')
                log_area.classes(remove='hidden')
                try:
                    hy2_password = str(uuid.uuid4()).replace('-', '')[:16]
                    params = {
                        "password": hy2_password,
                        "sni": sni_input.value,
                        "enable_hopping": "true" if enable_hopping.value else "false",
                        "port_range_start": int(hop_start.value),
                        "port_range_end": int(hop_end.value),
                    }

                    script_content = HYSTERIA_INSTALL_SCRIPT_TEMPLATE.format(**params)
                    deploy_cmd = _build_privileged_script_command(server_conf, script_content)

                    log_area.push(f"🚀 [SSH] 连接到 {real_ip} 开始安装...")
                    success, output = await _ssh_exec_wrapper(server_conf, deploy_cmd)
                    _push_deploy_output(log_area, output)

                    if success:
                        match = re.search(r'HYSTERIA_DEPLOY_SUCCESS_LINK: (hy2://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("🎉 部署成功！")

                            custom_name = name_input.value.strip()
                            node_name = custom_name if custom_name else f"Hy2-{real_ip[-3:]}"

                            if enable_hopping.value:
                                final_port_display = f"{int(hop_start.value)}-{int(hop_end.value)}"
                            else:
                                try:
                                    final_port_display = int(link.split('@')[1].split(':')[1].split('?')[0])
                                except:
                                    final_port_display = 443

                            if '#' in link:
                                link = link.split('#')[0]
                            final_raw_link = f"{link}#{urllib.parse.quote(node_name)}"

                            new_node = {
                                "id": str(uuid.uuid4()),
                                "remark": node_name,
                                "port": final_port_display,
                                "protocol": "hysteria2",
                                "settings": {},
                                "streamSettings": {},
                                "enable": True,
                                "_is_custom": True,
                                "_raw_link": final_raw_link,
                            }
                            if 'custom_nodes' not in server_conf:
                                server_conf['custom_nodes'] = []
                            server_conf['custom_nodes'].append(new_node)
                            await save_servers()

                            from app.ui.common.notifications import safe_notify

                            safe_notify(f"✅ 节点 {node_name} 已添加", "positive")
                            await asyncio.sleep(1)
                            d.close()
                            if callback:
                                await callback()
                        else:
                            log_area.push("❌ 未捕获链接，请查看完整日志输出")
                    else:
                        log_area.push("❌ SSH 执行失败，请查看上方详细日志")
                except Exception as e:
                    log_area.push(f"❌ 异常: {e}")
                    print(e)
                finally:
                    btn_cancel.enable()
                    btn_deploy.props(remove='loading')

            btn_deploy = _deploy_confirm_button('开始部署', start_process)
    d.open()


async def open_deploy_snell_dialog(server_conf, callback):
    # 解析目标 IP 作为兜底
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    import random, string, uuid, urllib.parse

    with ui.dialog() as d, ui.card().classes(_deploy_dialog_card()):
        _deploy_dialog_header('security', '部署 Snell 节点 (v5 最新版)', f'目标服务器: {target_host}')

        with ui.column().classes(_deploy_body()):
            with ui.element('div').classes(_deploy_field_box()):
                name_input = ui.input('节点备注', placeholder='例如: HK-Snell-v5').props(_deploy_input_props()).classes('w-full')

            with ui.row().classes('w-full gap-2 items-center'):
                rand_port = random.randint(30000, 60000)
                rand_psk = ''.join(random.choices(string.ascii_letters + string.digits, k=20))

                with ui.element('div').classes(_deploy_field_box('w-1/3')):
                    port_input = ui.number('端口', value=rand_port, format='%.0f').props(_deploy_input_props()).classes('w-full')
                with ui.element('div').classes(_deploy_field_box('flex-grow')):
                    psk_input = ui.input('密钥 (PSK)', value=rand_psk).props(_deploy_input_props()).classes('w-full')

            log_area = ui.log().classes(_deploy_log_classes())

        with ui.row().classes(_deploy_footer()):
            btn_cancel = _deploy_cancel_button(d.close)

            async def start_process():
                btn_cancel.disable()
                btn_deploy.props('loading')
                log_area.classes(remove='hidden')
                try:
                    params = {
                        "port": int(port_input.value),
                        "psk": psk_input.value,
                        "target_ip": target_host,
                    }
                    script_content = SNELL_INSTALL_SCRIPT_TEMPLATE.format(**params)
                    deploy_cmd = _build_privileged_script_command(server_conf, script_content)

                    log_area.push(f"🚀 [SSH] 开始在 {target_host} 安装 Snell v5 ...")
                    success, output = await _ssh_exec_wrapper(server_conf, deploy_cmd)
                    _push_deploy_output(log_area, output)

                    if success:
                        import re
                        match = re.search(r'SNELL_DEPLOY_SUCCESS_LINK: (snell://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("🎉 Snell v5 部署成功并已启动！")

                            custom_name = name_input.value.strip() or f"Snell-v5-{target_host[-3:]}"

                            if '#' in link:
                                link = link.split('#')[0]
                            final_raw_link = f"{link}#{urllib.parse.quote(custom_name)}"

                            new_node = {
                                "id": str(uuid.uuid4()),
                                "remark": custom_name,
                                "port": params['port'],
                                "protocol": "snell",
                                "settings": {},
                                "streamSettings": {},
                                "enable": True,
                                "_is_custom": True,
                                "_raw_link": final_raw_link,
                            }
                            if 'custom_nodes' not in server_conf:
                                server_conf['custom_nodes'] = []
                            server_conf['custom_nodes'].append(new_node)
                            await save_servers()

                            from app.ui.common.notifications import safe_notify

                            safe_notify(f"✅ 节点 {custom_name} 已添加", "positive")
                            await asyncio.sleep(1)
                            d.close()
                            if callback:
                                await callback()
                        else:
                            log_area.push("❌ 部署失败：未能成功启动服务，请查看完整日志输出。")
                    else:
                        log_area.push("❌ SSH 连接失败，请查看上方详细日志")
                except Exception as e:
                    log_area.push(f"❌ 异常: {e}")
                    print(e)
                finally:
                    btn_cancel.enable()
                    btn_deploy.props(remove='loading')

            btn_deploy = _deploy_confirm_button('开始部署', start_process)
    d.open()
