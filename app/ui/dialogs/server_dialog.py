import asyncio
import socket

from nicegui import app, run, ui

from app.core.config import AUTO_COUNTRY_MAP
from app.core.logging import logger
from app.core.state import (
    ADMIN_CONFIG,
    CURRENT_VIEW_STATE,
    EXPANDED_GROUPS,
    NODES_DATA,
    PING_TREND_CACHE,
    PROBE_DATA_CACHE,
    SERVERS_CACHE,
    SIDEBAR_UI_REFS,
)
from app.services.probe import install_probe_on_server
from app.services.server_ops import fast_resolve_single_server, generate_smart_name
from app.services.ssh import _ssh_exec_wrapper
from app.storage.repositories import save_servers
from app.ui.common.notifications import safe_notify
from app.ui.components.dashboard import refresh_dashboard_ui
from app.ui.components.sidebar import render_sidebar_content, render_single_sidebar_row
from app.utils.geo import detect_country_group


COLS_NO_PING = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 0.5fr 1.5fr; align-items: center;'
COLS_SPECIAL_WITH_PING = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 1.5fr; align-items: center;'
SINGLE_COLS_NO_PING = 'grid-template-columns: minmax(0, 3fr) minmax(0, 1fr) minmax(0, 1.5fr) minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr) 140px; align-items: center;'
XHTTP_UNINSTALL_SCRIPT = r"""
#!/bin/bash
systemctl stop xray
systemctl disable xray
rm -f /etc/systemd/system/xray.service
systemctl daemon-reload
rm -rf /usr/local/etc/xray

echo "Xray Service Uninstalled (Binary kept safe)"
"""


SSH_PAGE_TERMINALS = {}


def _sync_resolve_ip(host):
    try:
        return socket.gethostbyname(host)
    except:
        return host


def _server_dialog_theme():
    is_dark = bool(app.storage.user.get('is_dark', True))
    return {
        'is_dark': is_dark,
        'card': 'w-full max-w-sm p-0 gap-0 flex flex-col overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-full max-w-sm p-0 gap-0 flex flex-col overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]',
        'header': 'w-full justify-between items-center px-5 py-4 border-b border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14] relative overflow-hidden' if is_dark else 'w-full justify-between items-center px-5 py-4 border-b border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] relative overflow-hidden',
        'icon_box': 'w-9 h-9 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] shadow-[0_0_8px_rgba(0,0,0,0.7)] text-cyan-400 relative overflow-hidden' if is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] text-sky-600 relative overflow-hidden',
        'title': 'text-lg font-black text-slate-100 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide',
        'body': 'w-full gap-2 p-5 bg-[#030712]' if is_dark else 'w-full gap-2 p-5 bg-[#f8fbff]',
        'input': 'outlined dense dark color=cyan standout' if is_dark else 'outlined dense color=blue',
        'select': 'outlined dense dark color=cyan options-dense' if is_dark else 'outlined dense color=blue options-dense',
        'panel_bg': 'w-full animated fadeIn bg-[#030712] text-slate-200 px-5 pb-5' if is_dark else 'w-full animated fadeIn bg-[#f8fbff] text-slate-700 px-5 pb-5',
        'empty_box': 'w-full h-48 justify-center items-center bg-[#050b14] rounded-sm border border-dashed border-[#1e3a5f]/55' if is_dark else 'w-full h-48 justify-center items-center bg-sky-50 rounded-sm border border-dashed border-slate-300',
        'btn_primary': 'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 hover:shadow-[0_0_12px_rgba(34,211,238,0.32)] px-4 py-1 rounded-sm font-black tracking-wide transition-all' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 px-4 py-1 rounded-sm font-black tracking-wide transition-all',
        'btn_delete': 'bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 hover:shadow-[0_0_12px_rgba(244,63,94,0.28)] w-full rounded-sm font-black tracking-wide transition-all' if is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 w-full rounded-sm font-black tracking-wide transition-all',
        'btn_confirm': 'bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 hover:shadow-[0_0_12px_rgba(244,63,94,0.28)] rounded-sm font-black tracking-wide transition-all' if is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 rounded-sm font-black tracking-wide transition-all',
        'close_btn': 'text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100',
        'outline_btn': 'text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm',
    }


async def save_server_config(server_data, is_add=True, idx=None):
    client = None
    try:
        client = ui.context.client
    except:
        pass

    logger.info(f"[SaveServerDialog] save_server_config called | is_add={is_add} idx={idx} client_present={client is not None} servers_before={len(SERVERS_CACHE)} url={server_data.get('url')} name={server_data.get('name')}")

    if not server_data.get('name') or not server_data.get('url'):
        safe_notify("名称和地址不能为空", "negative")
        return False

    old_group = None
    if not is_add and idx is not None and 0 <= idx < len(SERVERS_CACHE):
        old_group = SERVERS_CACHE[idx].get('group')

    if is_add:
        for s in SERVERS_CACHE:
            if s['url'] == server_data['url']:
                safe_notify("已存在！", "warning")
                return False

        has_flag = False
        for v in AUTO_COUNTRY_MAP.values():
            if v.split(' ')[0] in server_data['name']:
                has_flag = True
                break
        if not has_flag and '🏳️' not in server_data['name']:
            server_data['name'] = f"🏳️ {server_data['name']}"

        SERVERS_CACHE.append(server_data)
        safe_notify(f"已添加: {server_data['name']}", "positive")
    else:
        if idx is not None and 0 <= idx < len(SERVERS_CACHE):
            SERVERS_CACHE[idx].update(server_data)
            safe_notify(f"已更新: {server_data['name']}", "positive")
        else:
            safe_notify("目标不存在", "negative")
            return False

    await save_servers()
    logger.info(f"[SaveServerDialog] save_servers done | servers_after={len(SERVERS_CACHE)} rows_refs={len(SIDEBAR_UI_REFS.get('rows', {}))} group_refs={len(SIDEBAR_UI_REFS.get('groups', {}))}")

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

    logger.info(f"[SaveServerDialog] sidebar refresh decision | need_full_refresh={need_full_refresh} new_group={new_group} rows_refs={len(SIDEBAR_UI_REFS.get('rows', {}))} group_refs={len(SIDEBAR_UI_REFS.get('groups', {}))}")
    if need_full_refresh:
        try:
            logger.info(f"[SaveServerDialog] calling render_sidebar_content.refresh | client_present={client is not None}")
            if client:
                with client:
                    render_sidebar_content.refresh()
            else:
                render_sidebar_content.refresh()
            logger.info("[SaveServerDialog] render_sidebar_content.refresh returned")
        except Exception as e:
            logger.error(f"[SaveServerDialog] render_sidebar_content.refresh failed: {e}")

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
            logger.info(f"[SaveServerDialog] calling refresh_dashboard_ui | client_present={client is not None} current_scope={current_scope}")
            if client:
                with client:
                    await refresh_dashboard_ui()
            else:
                await refresh_dashboard_ui()
            logger.info("[SaveServerDialog] refresh_dashboard_ui returned")
        except Exception as e:
            logger.error(f"[SaveServerDialog] refresh_dashboard_ui failed: {e}")

    asyncio.create_task(fast_resolve_single_server(server_data))

    return True


async def open_server_dialog(idx=None):
    is_edit = idx is not None
    original_data = SERVERS_CACHE[idx] if is_edit else {}
    data = original_data.copy()

    if is_edit:
        has_xui_conf = bool(data.get('url') and data.get('user') and data.get('pass'))
        raw_ssh_host = data.get('ssh_host')
        if not raw_ssh_host and not has_xui_conf:
            raw_ssh_host = data.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]

        has_ssh_conf = bool(raw_ssh_host or data.get('ssh_user') or data.get('ssh_key') or data.get('ssh_password') or data.get('probe_installed'))
        if not has_ssh_conf and not has_xui_conf:
            has_ssh_conf = True
    else:
        has_xui_conf = True
        has_ssh_conf = True

    state = {'ssh_active': has_ssh_conf, 'xui_active': has_xui_conf}
    theme = _server_dialog_theme()

    with ui.dialog() as d, ui.card().classes(theme['card']):
        with ui.column().classes(theme['header'].replace('items-center', 'items-stretch') + ' gap-3'):
            ui.element('div').classes('absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNCwyMTEsMjM4LDAuMDcpIi8+PC9zdmc+")] opacity-100 pointer-events-none')
            with ui.row().classes('w-full justify-between items-start z-10'):
                with ui.row().classes('items-center gap-3'):
                    with ui.element('div').classes(theme['icon_box']):
                        ui.element('div').classes('absolute inset-0 bg-cyan-400/10')
                        ui.icon('dns').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                    ui.label('编辑服务器' if is_edit else '添加服务器').classes(theme['title'])
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes(theme['close_btn'] + ' z-10')
            tabs = ui.tabs().classes('text-cyan-400 z-10 self-start')
            with tabs:
                t_ssh = ui.tab('SSH / 探针', icon='terminal')
                t_xui = ui.tab('X-UI面板', icon='settings')

        async def save_basic_info_only():
            if not is_edit:
                safe_notify("新增服务器请使用下方的保存按钮", "warning")
                return

            new_name = name_input.value.strip()
            new_group = group_input.value

            if not new_name:
                new_name = await generate_smart_name(data)

            SERVERS_CACHE[idx]['name'] = new_name
            SERVERS_CACHE[idx]['group'] = new_group

            await save_servers()
            render_sidebar_content.refresh()

            current_scope = CURRENT_VIEW_STATE.get('scope')
            if current_scope == 'SINGLE' and CURRENT_VIEW_STATE.get('data') == SERVERS_CACHE[idx]:
                try:
                    from app.ui.pages.content_router import refresh_content

                    await refresh_content('SINGLE', SERVERS_CACHE[idx])
                except:
                    pass
            elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
                CURRENT_VIEW_STATE['scope'] = None
                try:
                    from app.ui.pages.content_router import refresh_content

                    await refresh_content(current_scope, CURRENT_VIEW_STATE.get('data'), force_refresh=False)
                except:
                    pass

            safe_notify("✅ 基础信息已更新", "positive")
            d.close()

        with ui.column().classes(theme['body']):
            name_input = ui.input(value=data.get('name', ''), label='备注名称 (留空自动获取)').classes('w-full').props(theme['input'])

            with ui.row().classes('w-full items-center gap-2 no-wrap'):
                from app.services.server_ops import get_all_groups

                group_input = ui.select(options=get_all_groups(), value=data.get('group', '默认分组'), new_value_mode='add-unique', label='分组').classes('flex-grow').props(theme['select'])

                if is_edit:
                    ui.button(icon='save', on_click=save_basic_info_only).props('flat dense round').classes(theme['close_btn']).tooltip('仅保存名称和分组 (不重新部署)')

        inputs = {}
        btn_keycap_blue = theme['btn_primary']
        btn_keycap_delete = theme['btn_delete']
        btn_keycap_red_confirm = theme['btn_confirm']

        async def save_panel_data(panel_type):
            final_name = name_input.value.strip()
            final_group = group_input.value
            new_server_data = data.copy()
            new_server_data['group'] = final_group

            if panel_type == 'ssh':
                if not inputs.get('ssh_host'):
                    return
                s_host = inputs['ssh_host'].value.strip()
                if not s_host:
                    safe_notify("SSH 主机 IP 不能为空", "negative")
                    return

                new_server_data.update({
                    'ssh_host': s_host,
                    'ssh_port': str(inputs['ssh_port'].value).strip(),
                    'ssh_user': inputs['ssh_user'].value.strip(),
                    'ssh_auth_type': inputs['auth_type'].value,
                    'ssh_password': inputs['ssh_pwd'].value if inputs['ssh_pwd'] else '',
                    'ssh_key': inputs['ssh_key'].value if inputs['ssh_key'] else '',
                    'probe_installed': True,
                })

                if 'probe_chk' in inputs:
                    inputs['probe_chk'].value = True

                if not new_server_data.get('url'):
                    new_server_data['url'] = f"http://{s_host}:22"

            elif panel_type == 'xui':
                if not inputs.get('xui_url'):
                    return
                x_url_raw = inputs['xui_url'].value.strip()
                x_user = inputs['xui_user'].value.strip()
                x_pass = inputs['xui_pass'].value.strip()

                if not (x_url_raw and x_user and x_pass):
                    safe_notify("必填项不能为空", "negative")
                    return

                if '://' not in x_url_raw:
                    x_url_raw = f"http://{x_url_raw}"

                from urllib.parse import urlparse
                try:
                    parsed = urlparse(x_url_raw)
                    netloc = parsed.netloc
                    if ':' not in netloc and ']' not in netloc:
                        netloc = f"{netloc}:54321"
                        safe_notify("已自动添加默认端口: 54321", "positive")

                    final_base_url = f"{parsed.scheme}://{netloc}"
                    path_from_url = parsed.path.strip().strip('/')

                    if path_from_url:
                        final_prefix = f"/{path_from_url}"
                        if 'xui_prefix' in inputs:
                            inputs['xui_prefix'].value = final_prefix
                        safe_notify(f"已自动识别路径: {final_prefix}", "positive")
                    else:
                        final_prefix = inputs['xui_prefix'].value.strip()
                except Exception as e:
                    logger.error(f"URL Parse Error: {e}")
                    final_base_url = x_url_raw
                    final_prefix = inputs['xui_prefix'].value.strip()

                probe_val = inputs['probe_chk'].value

                new_server_data.update({
                    'url': final_base_url,
                    'user': x_user,
                    'pass': x_pass,
                    'prefix': final_prefix,
                    'probe_installed': probe_val,
                })

                if probe_val:
                    if not new_server_data.get('ssh_host'):
                        try:
                            clean_host = urlparse(final_base_url).hostname or final_base_url.split('://')[-1].split(':')[0]
                            new_server_data['ssh_host'] = clean_host
                        except:
                            new_server_data['ssh_host'] = final_base_url.split('://')[-1].split(':')[0]
                    if not new_server_data.get('ssh_port'):
                        new_server_data['ssh_port'] = '22'
                    if not new_server_data.get('ssh_user'):
                        new_server_data['ssh_user'] = 'root'
                    if not new_server_data.get('ssh_auth_type'):
                        new_server_data['ssh_auth_type'] = '全局密钥'

            if not final_name:
                safe_notify("正在生成名称...", "ongoing")
                final_name = await generate_smart_name(new_server_data)
            new_server_data['name'] = final_name

            success = await save_server_config(new_server_data, is_add=not is_edit, idx=idx)

            if success:
                data.update(new_server_data)
                if panel_type == 'ssh':
                    state['ssh_active'] = True
                if panel_type == 'xui':
                    state['xui_active'] = True
                if panel_type == 'xui' and new_server_data.get('probe_installed'):
                    state['ssh_active'] = True

                if new_server_data.get('probe_installed'):
                    safe_notify("🚀 配置已保存，正在自动推送探针...", "ongoing")
                    target_for_install = new_server_data
                    if is_edit and idx is not None and 0 <= idx < len(SERVERS_CACHE):
                        target_for_install = SERVERS_CACHE[idx]
                    elif not is_edit:
                        target_for_install = next((s for s in SERVERS_CACHE if s.get('url') == new_server_data.get('url')), new_server_data)

                    async def _install_and_report(target_server):
                        ok = await install_probe_on_server(target_server)
                        if ok:
                            safe_notify("✅ 探针安装成功，等待首次上报", "positive")
                        else:
                            target_server['probe_installed'] = False
                            await save_servers()
                            safe_notify("⚠️ 探针安装失败，请检查 SSH 凭据、sudo/root 权限以及主控端地址", "warning")
                    asyncio.create_task(_install_and_report(target_for_install))
                else:
                    safe_notify(f"✅ {panel_type.upper()} 已保存", "positive")

        @ui.refreshable
        def render_ssh_panel():
            if not state['ssh_active']:
                with ui.column().classes(theme['empty_box']):
                    ui.icon('terminal').classes('text-4xl mb-2 text-cyan-400')
                    ui.label('SSH 功能未启用').classes('text-slate-400 font-bold mb-2')
                    ui.button('启用 SSH 配置', icon='add', on_click=lambda: _activate_panel('ssh')).props('flat').classes('bg-cyan-950/40 text-cyan-300 border border-cyan-500/45 rounded-sm px-4 py-2')
            else:
                init_host = data.get('ssh_host')
                if not init_host and is_edit:
                    if '://' in data.get('url', ''):
                        init_host = data.get('url', '').split('://')[-1].split(':')[0]
                    else:
                        init_host = data.get('url', '').split(':')[0]

                inputs['ssh_host'] = ui.input(label='SSH 主机 IP', value=init_host).classes('w-full').props(theme['input'])

                with ui.column().classes('w-full gap-3'):
                    with ui.row().classes('w-full gap-2'):
                        inputs['ssh_user'] = ui.input(value=data.get('ssh_user', 'root'), label='SSH 用户').classes('flex-1').props(theme['input'])
                        inputs['ssh_port'] = ui.input(value=data.get('ssh_port', '22'), label='端口').classes('w-1/3').props(theme['input'])

                    valid_auth_options = ['全局密钥', '独立密码', '独立密钥']
                    current_auth = data.get('ssh_auth_type', '全局密钥')
                    if current_auth not in valid_auth_options:
                        current_auth = '全局密钥'

                    inputs['auth_type'] = ui.select(valid_auth_options, value=current_auth, label='认证方式').classes('w-full').props(theme['select'])
                    inputs['ssh_pwd'] = ui.input(label='SSH 密码', password=True, value=data.get('ssh_password', '')).classes('w-full').props(theme['input'])
                    inputs['ssh_pwd'].bind_visibility_from(inputs['auth_type'], 'value', value='独立密码')
                    
                    # 修复点：移除了 props 里的 bg-color="[#050b14]"
                    inputs['ssh_key'] = ui.textarea(label='SSH 私钥', value=data.get('ssh_key', '')).classes('w-full').props('outlined dense rows=3 input-class=font-mono text-xs dark color=cyan standout' if theme['is_dark'] else 'outlined dense rows=3 input-class=font-mono text-xs color=blue')
                    inputs['ssh_key'].bind_visibility_from(inputs['auth_type'], 'value', value='独立密钥')

                ui.separator().classes('my-1')
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('✅ 自动使用全局私钥').bind_visibility_from(inputs['auth_type'], 'value', value='全局密钥').classes('text-emerald-400 text-xs font-bold')
                    ui.element('div').bind_visibility_from(inputs['auth_type'], 'value', value='独立密码')
                    ui.element('div').bind_visibility_from(inputs['auth_type'], 'value', value='独立密钥')
                    ui.button('保存 SSH', icon='save', on_click=lambda: save_panel_data('ssh')).props('flat').classes(btn_keycap_blue)

        @ui.refreshable
        def render_xui_panel():
            if not state['xui_active']:
                with ui.column().classes(theme['empty_box']):
                    ui.icon('settings_applications').classes('text-4xl mb-2 text-purple-400')
                    ui.label('X-UI 面板未配置').classes('text-slate-400 font-bold mb-2')
                    ui.button('配置 X-UI 信息', icon='add', on_click=lambda: _activate_panel('xui')).props('flat').classes('bg-purple-950/40 text-purple-300 border border-purple-500/45 rounded-sm px-4 py-2')
            else:
                inputs['xui_url'] = ui.input(value=data.get('url', ''), label='面板 URL (http://ip:port)').classes('w-full').props(theme['input'])
                ui.label('默认端口 54321，如不填写将自动补全').classes('text-[10px] text-slate-500 ml-1 -mt-1 mb-1')
                with ui.row().classes('w-full gap-2'):
                    inputs['xui_user'] = ui.input(value=data.get('user', ''), label='账号').classes('flex-1').props(theme['input'])
                    inputs['xui_pass'] = ui.input(value=data.get('pass', ''), label='密码', password=True).classes('flex-1').props(theme['input'])

                # --- 修复手动探测按钮 ---
                with ui.row().classes('w-full gap-2 items-center no-wrap'):
                    inputs['xui_prefix'] = ui.input(value=data.get('prefix', ''), label='面板根路径 (选填)').classes('flex-1 min-w-0').props(theme['input'])

                    async def auto_detect_path():
                        s_host = inputs.get('ssh_host').value if 'ssh_host' in inputs else data.get('ssh_host')
                        s_user = inputs.get('ssh_user').value if 'ssh_user' in inputs else data.get('ssh_user')
                        s_port = inputs.get('ssh_port').value if 'ssh_port' in inputs else data.get('ssh_port')
                        s_pwd = inputs.get('ssh_pwd').value if 'ssh_pwd' in inputs else data.get('ssh_password')
                        s_key = inputs.get('ssh_key').value if 'ssh_key' in inputs else data.get('ssh_key')
                        s_auth = inputs.get('auth_type').value if 'auth_type' in inputs else data.get('ssh_auth_type', '全局密钥')
                        
                        if not s_host:
                            safe_notify('请先在左侧【SSH / 探针】配置好服务器 IP', 'warning')
                            return
                            
                        temp_conf = {
                            'url': data.get('url') or f'http://{s_host}:22',
                            'ssh_host': s_host,
                            'ssh_user': s_user or 'root',
                            'ssh_port': s_port or '22',
                            'ssh_password': s_pwd,
                            'ssh_key': s_key,
                            'ssh_auth_type': s_auth,
                        }
                        
                        detect_script = r'''python3 - <<'PY'
import sqlite3, os
xui_path = ""
for p in ['/etc/x-ui/x-ui.db', '/usr/local/x-ui/bin/x-ui.db', '/usr/local/x-ui/x-ui.db']:
    if os.path.exists(p):
        try:
            res = sqlite3.connect(p).cursor().execute("SELECT value FROM settings WHERE key='webBasePath'").fetchone()
            if res and res[0]: xui_path = res[0].strip('/')
            break
        except: pass
print(xui_path)
PY'''
                        s_notify = ui.notification('正在通过 SSH 探测路径...', timeout=0, spinner=True)
                        success, output = await run.io_bound(lambda: _ssh_exec_wrapper(temp_conf, detect_script))
                        s_notify.dismiss()
                        
                        if success:
                            detected = output.strip().strip('/')
                            inputs['xui_prefix'].value = f"/{detected}" if detected else ""
                            safe_notify('✅ 路径提取成功', 'positive')
                        else:
                            safe_notify('⚠️ 探测失败，请检查 SSH', 'warning')

                    ui.button(icon='travel_explore', on_click=auto_detect_path).props('flat').tooltip('一键探测面板路径').classes('px-3 bg-purple-950/40 text-purple-300 border border-purple-500/45 rounded-sm hover:bg-purple-900/55')

                ui.separator().classes('my-1')
                with ui.column().classes('w-full gap-2'):
                    with ui.row().classes('w-full justify-between items-center'):
                        inputs['probe_chk'] = ui.checkbox('启用 Root 探针', value=data.get('probe_installed', False))
                        inputs['probe_chk'].classes('text-sm font-bold text-cyan-300')
                        ui.button('保存 X-UI', icon='save', on_click=lambda: save_panel_data('xui')).props('flat').classes(btn_keycap_blue)
                    ui.label('提示: 启用探针需先配置 SSH 登录信息').classes('text-[10px] text-rose-400 ml-8 -mt-2')

                def auto_fill_ssh():
                    if inputs['probe_chk'].value and state['ssh_active'] and inputs.get('ssh_host') and not inputs['ssh_host'].value:
                        p_url = inputs['xui_url'].value
                        if p_url:
                            clean_ip = p_url.split('://')[-1].split(':')[0]
                            if ':' in clean_ip:
                                clean_ip = clean_ip.split(':')[0]
                            inputs['ssh_host'].set_value(clean_ip)
                inputs['probe_chk'].on_value_change(auto_fill_ssh)

        def _activate_panel(panel_type):
            state[f'{panel_type}_active'] = True
            if panel_type == 'ssh':
                render_ssh_panel.refresh()
            elif panel_type == 'xui':
                render_xui_panel.refresh()

        default_tab = t_ssh
        if is_edit and not state['ssh_active'] and state['xui_active']:
            default_tab = t_xui

        with ui.tab_panels(tabs, value=default_tab).classes(theme['panel_bg']):
            with ui.tab_panel(t_ssh).classes('p-0 flex flex-col gap-3'):
                render_ssh_panel()
            with ui.tab_panel(t_xui).classes('p-0 flex flex-col gap-3'):
                render_xui_panel()

        if is_edit:
            with ui.row().classes('w-full justify-start mt-4 pt-2 border-t border-[#1e3a5f]/35'):
                async def open_delete_confirm():
                    with ui.dialog() as del_d, ui.card().classes('w-[360px] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-rose-800/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if theme['is_dark'] else 'w-[360px] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-rose-300 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                        with ui.column().classes('w-full p-5 gap-3 bg-gradient-to-r from-[#19070d] to-[#0b0911] border-b border-rose-900/60' if theme['is_dark'] else 'w-full p-5 gap-3 bg-gradient-to-r from-rose-50 to-orange-50 border-b border-rose-200'):
                            ui.label('删除确认').classes('text-lg font-black text-rose-300 tracking-wide' if theme['is_dark'] else 'text-lg font-black text-rose-700 tracking-wide')
                            ui.label('请选择要删除的内容：').classes('text-sm text-slate-400' if theme['is_dark'] else 'text-sm text-slate-600')
                        with ui.column().classes('w-full p-4 gap-2 bg-[#030712]' if theme['is_dark'] else 'w-full p-4 gap-2 bg-white'):
                            real_ssh_exists = bool(data.get('ssh_host') or data.get('ssh_user'))
                            real_xui_exists = bool(data.get('url') and data.get('user') and data.get('pass'))
                            has_probe = data.get('probe_installed', False)

                            if not real_ssh_exists and not real_xui_exists:
                                real_ssh_exists = True
                                real_xui_exists = True

                            chk_ssh = ui.checkbox('SSH 连接信息', value=real_ssh_exists).classes('text-sm font-bold text-slate-200' if theme['is_dark'] else 'text-sm font-bold text-slate-700')
                            chk_xui = ui.checkbox('X-UI 面板信息', value=real_xui_exists).classes('text-sm font-bold text-slate-200' if theme['is_dark'] else 'text-sm font-bold text-slate-700')
                            chk_uninstall = ui.checkbox('同时卸载远程探针脚本', value=True).classes('text-sm font-bold text-rose-400')
                            chk_uninstall.set_visibility(has_probe)

                            if not real_ssh_exists:
                                chk_ssh.value = False
                                chk_ssh.disable()
                            if not real_xui_exists:
                                chk_xui.value = False
                                chk_xui.disable()
                            if real_ssh_exists and not real_xui_exists:
                                chk_ssh.disable()
                            if real_xui_exists and not real_ssh_exists:
                                chk_xui.disable()

                            async def confirm_execution():
                                if idx >= len(SERVERS_CACHE):
                                    return
                                target_srv = SERVERS_CACHE[idx]
                                will_delete_ssh = chk_ssh.value
                                will_delete_xui = chk_xui.value
                                will_uninstall = chk_uninstall.value and chk_uninstall.visible
                                remaining_ssh = real_ssh_exists and not will_delete_ssh
                                remaining_xui = real_xui_exists and not will_delete_xui
                                is_full_delete = False

                                if will_uninstall:
                                    loading_notify = ui.notification('正在尝试连接并卸载探针...', timeout=None, spinner=True)
                                    try:
                                        uninstall_cmd = "systemctl stop x-fusion-agent && systemctl disable x-fusion-agent && rm -f /etc/systemd/system/x-fusion-agent.service && systemctl daemon-reload && rm -f /root/x_fusion_agent.py"
                                        success, output = await run.io_bound(lambda: _ssh_exec_wrapper(target_srv, uninstall_cmd))
                                        if success:
                                            ui.notify('✅ 远程探针已卸载清理', type='positive')
                                        else:
                                            ui.notify('⚠️ 远程卸载失败 (可能是连接超时)，将仅删除本地记录', type='warning')
                                    finally:
                                        loading_notify.dismiss()

                                if not remaining_ssh and not remaining_xui:
                                    SERVERS_CACHE.pop(idx)
                                    u = target_srv.get('url')
                                    p_u = target_srv.get('ssh_host') or u
                                    for k in [u, p_u]:
                                        if k in PROBE_DATA_CACHE:
                                            del PROBE_DATA_CACHE[k]
                                        if k in NODES_DATA:
                                            del NODES_DATA[k]
                                        if k in PING_TREND_CACHE:
                                            del PING_TREND_CACHE[k]
                                    safe_notify('✅ 服务器已彻底删除', 'positive')
                                    is_full_delete = True
                                else:
                                    if will_delete_ssh:
                                        for k in ['ssh_host', 'ssh_port', 'ssh_user', 'ssh_password', 'ssh_key', 'ssh_auth_type']:
                                            target_srv[k] = ''
                                        target_srv['probe_installed'] = False
                                        state['ssh_active'] = False
                                        data['ssh_host'] = ''
                                        safe_notify('✅ SSH 信息已清除', 'positive')

                                    if will_delete_xui:
                                        for k in ['url', 'user', 'pass', 'prefix']:
                                            target_srv[k] = ''
                                        state['xui_active'] = False
                                        data['url'] = ''
                                        safe_notify('✅ X-UI 信息已清除', 'positive')

                                await save_servers()
                                del_d.close()
                                d.close()
                                render_sidebar_content.refresh()
                                current_scope = CURRENT_VIEW_STATE.get('scope')
                                current_data = CURRENT_VIEW_STATE.get('data')

                                from app.ui.pages.content_router import content_container, refresh_content

                                if is_full_delete:
                                    if current_scope == 'SINGLE' and current_data == target_srv:
                                        content_container.clear()
                                        with content_container:
                                            ui.label('该服务器已删除').classes('text-gray-400 text-lg w-full text-center mt-20')
                                    elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
                                        CURRENT_VIEW_STATE['scope'] = None
                                        await refresh_content(current_scope, current_data, force_refresh=False)
                                else:
                                    if current_scope == 'SINGLE' and current_data == target_srv:
                                        await refresh_content('SINGLE', target_srv)

                            with ui.row().classes('w-full justify-end mt-4 gap-2'):
                                ui.button('取消', on_click=del_d.close).props('outline color=grey').classes(theme['outline_btn'])
                                ui.button('确认执行', on_click=confirm_execution).props('flat').classes(btn_keycap_red_confirm)
                    del_d.open()

                ui.button('删除 / 卸载配置', icon='delete', on_click=open_delete_confirm).props('flat').classes(btn_keycap_delete)

    d.open()


def cleanup_ssh_route_terminal(server_key=None):
    keys = [server_key] if server_key else list(SSH_PAGE_TERMINALS.keys())
    for key in keys:
        inst = SSH_PAGE_TERMINALS.pop(key, None)
        try:
            if inst:
                inst.close()
        except:
            pass


async def render_single_ssh_view(server_conf):
    from app.ui.pages.single_ssh import render_single_ssh_view as _impl

    return await _impl(server_conf)


async def render_single_server_view(server_conf, force_refresh=False):
    from app.ui.pages.single_server import render_single_server_view as _impl

    return await _impl(server_conf, force_refresh=force_refresh)


async def render_aggregated_view(server_list, show_ping=False, token=None, initial_page=1):
    from app.ui.pages.aggregated_view import render_aggregated_view as _impl

    return await _impl(server_list, show_ping=show_ping, token=token, initial_page=initial_page)
