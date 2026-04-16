import asyncio
import json

from nicegui import run, ui

from app.core.logging import logger
from app.core.state import ADMIN_CONFIG, NODES_DATA, PROBE_DATA_CACHE
from app.services.cloudflare import CloudflareHandler
from app.services.manager_factory import get_manager
from app.services.ssh import _ssh_exec_wrapper, get_ssh_client_sync
from app.services.xui_fetch import fetch_inbounds_safe
from app.storage.repositories import save_nodes_cache, save_servers
from app.ui.common.notifications import safe_copy_to_clipboard, safe_notify
from app.ui.dialogs.inbound_dialog import delete_inbound_with_confirm, open_inbound_dialog
from app.utils.encoding import generate_detail_config, generate_node_link
from app.utils.formatters import format_bytes
from app.ui.dialogs import server_dialog as _server_dialog

REFRESH_CURRENT_NODES = lambda: None


async def render_single_server_view(server_conf, force_refresh=False):
    global REFRESH_CURRENT_NODES

    from nicegui import app
    is_dark = bool(app.storage.user.get('is_dark', True))
    page_bg = '#030712' if is_dark else '#eef4ff'
    shell_card_cls = 'rounded-sm border border-[#1e3a5f]/50 shadow-[0_10px_30px_rgba(0,0,0,0.8)] overflow-hidden bg-[#070b14]' if is_dark else 'rounded-sm border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.16)] overflow-hidden bg-white'
    shell_header_cls = 'bg-gradient-to-r from-[#0a1526] to-[#050a14] border-b border-[#1e3a5f]/60' if is_dark else 'bg-gradient-to-r from-[#f8fbff] to-[#eef4ff] border-b border-slate-300/90'
    shell_body_cls = 'bg-[#030712]' if is_dark else 'bg-[#f8fbff]'
    section_card_cls = 'bg-gradient-to-br from-[#0a1120] to-[#050a14] border border-[#1e3a5f]/40 rounded-sm shadow-xl p-0 gap-0 overflow-hidden' if is_dark else 'bg-gradient-to-br from-white to-[#f8fbff] border border-slate-300/90 rounded-sm shadow-[0_8px_24px_rgba(148,163,184,0.14)] p-0 gap-0 overflow-hidden'

    def apply_tooltip(target, text):
        tip = target.tooltip(text)
        tip.classes('bg-[#050b14] text-slate-100 border border-cyan-500/35 text-[11px] font-bold px-2 py-1 rounded-sm shadow-[0_6px_18px_rgba(0,0,0,0.35)]' if is_dark else 'bg-[#f8fbff] text-slate-700 border border-slate-300 text-[11px] font-bold px-2 py-1 rounded-sm shadow-[0_8px_20px_rgba(148,163,184,0.18)]')
        return tip

    SINGLE_COLS_NO_PING = _server_dialog.SINGLE_COLS_NO_PING
    XHTTP_UNINSTALL_SCRIPT = _server_dialog.XHTTP_UNINSTALL_SCRIPT
    _sync_resolve_ip = _server_dialog._sync_resolve_ip

    # 防止侧边栏切换导致的 SSH 僵尸进程残留
    _server_dialog.cleanup_ssh_route_terminal()

    from app.ui.pages.content_router import content_container, refresh_content

    if content_container:
        content_container.clear()
        content_container.classes(remove='overflow-y-auto block justify-start',
                                  add='h-full flex-1 min-h-0 overflow-hidden flex flex-col p-4')
        content_container.style(f'background-color: {page_bg};')

    with content_container:
        with ui.element('div').classes(
                'w-full max-w-[1440px] mx-auto h-full flex-1 min-h-[calc(100vh-130px)] flex flex-col gap-0 flex-nowrap'):
            has_manager_access = (server_conf.get('url') and server_conf.get('user') and server_conf.get('pass')) or (
                    server_conf.get('probe_installed') and server_conf.get('ssh_host'))
            mgr = None
            if has_manager_access:
                try:
                    mgr = get_manager(server_conf)
                except:
                    pass

            def to_float(value, default=0.0):
                try:
                    return float(value)
                except:
                    return default

            def clamp_percent(value):
                return max(0.0, min(100.0, to_float(value, 0.0)))

            def fmt_gb(value):
                if value in [None, '', '--']:
                    return '--'
                return f"{to_float(value):.2f} GB"

            def progress_text_class(pct):
                try:
                    pct = float(pct or 0)
                except:
                    pct = 0
                if is_dark:
                    if pct >= 72:
                        return 'absolute inset-0 z-10 flex items-center justify-center text-[11px] font-black text-[#03111f] font-mono leading-none tracking-tight'
                    return 'absolute inset-0 z-10 flex items-center justify-center text-[11px] font-black text-slate-200 font-mono leading-none tracking-tight drop-shadow-[0_1px_1px_rgba(0,0,0,0.85)]'
                if pct >= 72:
                    return 'absolute inset-0 z-10 flex items-center justify-center text-[11px] font-black text-slate-900 font-mono leading-none tracking-tight'
                return 'absolute inset-0 z-10 flex items-center justify-center text-[11px] font-black text-slate-700 font-mono leading-none tracking-tight'

            # 🛠️ 科技风：重构指标数据行（带发光左边框和悬浮高亮）
            def render_metric_row(label, value, sub_text='', value_color='text-cyan-300'):
                metric_row_cls = 'w-full min-h-[62px] items-center justify-between gap-4 px-4 py-4 bg-[#0a1120]/80 border border-[#1e3a5f]/45 border-l-[3px] border-l-cyan-700/80 shadow-[0_0_8px_rgba(0,0,0,0.45)] transition-all hover:border-cyan-500/40 hover:shadow-[0_0_12px_rgba(34,211,238,0.10)] hover:border-l-cyan-500 flex-nowrap relative overflow-hidden group' if is_dark else 'w-full min-h-[62px] items-center justify-between gap-4 px-4 py-4 bg-white border border-slate-300/90 border-l-[3px] border-l-sky-500 shadow-[0_6px_18px_rgba(148,163,184,0.12)] transition-all hover:border-sky-400/70 hover:shadow-[0_8px_20px_rgba(56,189,248,0.10)] hover:border-l-sky-600 flex-nowrap relative overflow-hidden group'
                metric_overlay_cls = 'absolute inset-0 bg-gradient-to-r from-cyan-500/4 to-transparent opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none' if is_dark else 'absolute inset-0 bg-gradient-to-r from-sky-400/8 to-transparent opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none'
                metric_label_cls = 'text-[11px] font-bold tracking-wide text-cyan-500/85 group-hover:text-cyan-400 transition-colors leading-none' if is_dark else 'text-[11px] font-bold tracking-wide text-sky-700/85 group-hover:text-sky-700 transition-colors leading-none'
                metric_sub_cls = 'text-[10px] text-slate-400 break-all leading-relaxed font-mono' if is_dark else 'text-[10px] text-slate-500 break-all leading-relaxed font-mono'
                with ui.row().classes(metric_row_cls):
                    ui.element('div').classes(metric_overlay_cls)
                    with ui.column().classes('gap-0.5 min-w-0 flex-1 justify-center z-10'):
                        ui.label(label).classes(metric_label_cls)
                        if sub_text:
                            ui.label(sub_text).classes(metric_sub_cls)
                    ui.label(str(value)).classes(
                        f'text-sm font-black text-right shrink-0 font-mono tracking-wide z-10 {value_color}')

            # 🛠️ 科技风：重构模块标题（发光图标与机械感）
            def render_section_header(title, icon, accent_class, desc='', right_renderer=None):
                header_row_cls = 'w-full items-center justify-between px-4 py-2.5 border-b border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1120] to-transparent min-h-[56px] relative overflow-hidden' if is_dark else 'w-full items-center justify-between px-4 py-2.5 border-b border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-transparent min-h-[56px] relative overflow-hidden'
                header_line_cls = 'absolute top-0 left-0 w-1/3 h-[1px] bg-gradient-to-r from-cyan-500/65 to-transparent' if is_dark else 'absolute top-0 left-0 w-1/3 h-[1px] bg-gradient-to-r from-sky-400/65 to-transparent'
                icon_wrap_base = 'w-8 h-8 rounded-sm flex items-center justify-center relative overflow-hidden group'
                icon_wrap_cls = f'{icon_wrap_base} bg-[#050b14] border border-[#1e3a5f] shadow-[0_0_8px_rgba(0,0,0,0.75)] {accent_class}' if is_dark else f'{icon_wrap_base} bg-sky-50 border border-slate-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] {accent_class}'
                title_cls = 'text-sm font-black text-slate-200 tracking-wide' if is_dark else 'text-sm font-black text-slate-800 tracking-wide'
                desc_cls = 'text-[10px] text-slate-500 tracking-wide' if is_dark else 'text-[10px] text-slate-500 tracking-wide'
                with ui.row().classes(header_row_cls):
                    ui.element('div').classes(header_line_cls)
                    with ui.row().classes('items-center gap-3 z-10'):
                        with ui.element('div').classes(icon_wrap_cls):
                            ui.element('div').classes('absolute inset-0 bg-current opacity-10')
                            ui.icon(icon).classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                        with ui.column().classes('gap-0 justify-center'):
                            ui.label(title).classes(title_cls)
                            if desc:
                                ui.label(desc).classes(desc_cls)
                    if right_renderer:
                        with ui.element('div').classes('z-10'):
                            right_renderer()

            def get_os_visual(os_name):
                name = str(os_name or '').lower()
                if 'ubuntu' in name:
                    return 'https://upload.wikimedia.org/wikipedia/commons/a/ab/Logo-ubuntu_cof-orange-hex.svg', 'Ubuntu'
                if 'debian' in name:
                    return 'https://upload.wikimedia.org/wikipedia/commons/6/66/Openlogo-debianV2.svg', 'Debian'
                if 'centos' in name:
                    return 'https://upload.wikimedia.org/wikipedia/commons/9/9e/CentOS_Icon.svg', 'CentOS'
                if 'red hat' in name:
                    return 'https://upload.wikimedia.org/wikipedia/commons/d/d8/Red_Hat_logo.svg', 'RedHat'
                if 'rocky' in name:
                    return 'https://upload.wikimedia.org/wikipedia/commons/1/11/Rocky_Linux_logo.svg', 'RockyLinux'
                if 'alma' in name:
                    return 'https://upload.wikimedia.org/wikipedia/commons/0/07/AlmaLinux_logo.svg', 'AlmaLinux'
                if 'alpine' in name:
                    return 'https://upload.wikimedia.org/wikipedia/commons/1/18/Alpine_Linux_logo.svg', 'Alpine'
                if 'arch' in name:
                    return 'https://upload.wikimedia.org/wikipedia/commons/a/a5/Archlinux-icon-crystal-64.svg', 'ArchLinux'

                return 'https://upload.wikimedia.org/wikipedia/commons/3/35/Tux.svg', 'Linux'

            def format_arch_text(arch_value):
                value = str(arch_value or '--').strip().lower()
                if value in ['x86_64', 'amd64']:
                    return 'AMD64 / x86_64'
                if value in ['aarch64', 'arm64']:
                    return 'ARM64 / AArch64'
                if value.startswith('arm'):
                    return 'ARM'
                if value in ['', '--']:
                    return '--'
                return str(arch_value)

            ssh_fallback_data = {}

            def _fetch_runtime_via_ssh():
                if not server_conf.get('ssh_host'):
                    return None
                client, msg = get_ssh_client_sync(server_conf)
                if not client:
                    return None
                try:
                    remote_script = r'''python3 - <<'PY'
import json, os, platform, multiprocessing
info = {}
try:
    pretty = '--'
    if os.path.exists('/etc/os-release'):
        with open('/etc/os-release') as f:
            for line in f:
                if line.startswith('PRETTY_NAME='):
                    pretty = line.split('=', 1)[1].strip().strip('"')
                    break

    uptime_text = '--'
    try:
        with open('/proc/uptime') as f:
            u = float(f.read().split()[0])
        d = int(u // 86400); h = int((u % 86400) // 3600); m = int((u % 3600) // 60)
        uptime_text = f'{d}天 {h}时 {m}分'
    except:
        pass

    xui_path = None
    is_3x_ui = False

    import sqlite3
    for p in ['/etc/x-ui/x-ui.db', '/usr/local/x-ui/bin/x-ui.db', '/usr/local/x-ui/x-ui.db']:
        if os.path.exists(p):
            try:
                conn = sqlite3.connect(p)
                res = conn.execute("SELECT value FROM settings WHERE key='webBasePath'").fetchone()
                if res and res[0]: xui_path = res[0].strip('/')

                res_3x = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='client_traffics'").fetchone()
                res_sub = conn.execute("SELECT value FROM settings WHERE key='subURI'").fetchone()
                if res_3x or res_sub:
                    is_3x_ui = True

                conn.close()
                if xui_path is not None: break
            except: pass

    info = {
        'os': pretty,
        'arch': platform.machine(),
        'cpu_cores': multiprocessing.cpu_count(),
        'uptime': uptime_text,
        'xui_path': xui_path,
        'is_3x_ui': is_3x_ui
    }
except Exception as e:
    info = {'error': str(e)}
print(json.dumps(info, ensure_ascii=False))
PY'''
                    stdin, stdout, stderr = client.exec_command(remote_script, timeout=15)
                    raw = stdout.read().decode('utf-8', errors='ignore').strip()
                    if raw:
                        parsed = json.loads(raw.splitlines()[-1])
                        if isinstance(parsed, dict) and not parsed.get('error'):
                            return parsed
                except Exception as e:
                    logger.warning(f'初始获取静态信息失败: {e}')
                finally:
                    try:
                        client.close()
                    except:
                        pass
                return None

            async def run_ssh_fallback():
                remote_data = await run.io_bound(_fetch_runtime_via_ssh)
                if isinstance(remote_data, dict):
                    ssh_fallback_data.update(remote_data)

                    need_save = False
                    if 'is_3x_ui' in remote_data and server_conf.get('is_3x_ui') != remote_data['is_3x_ui']:
                        server_conf['is_3x_ui'] = remote_data['is_3x_ui']
                        need_save = True

                    if 'xui_path' in remote_data:
                        detected_prefix = f"/{remote_data['xui_path']}" if remote_data['xui_path'] else ""
                        if server_conf.get('prefix') != detected_prefix:
                            server_conf['prefix'] = detected_prefix
                            need_save = True
                            logger.info(f"[AutoDetect] Server path automatically self-healed to: '{detected_prefix}'")

                    if need_save:
                        asyncio.create_task(save_servers())
                        if has_manager_access:
                            asyncio.create_task(reload_and_refresh_ui())

            ui.timer(0.1, run_ssh_fallback, once=True)

            def get_cached_snapshot():
                import time as _time
                probe_cache = PROBE_DATA_CACHE.get(server_conf['url'], {}) or {}
                static = probe_cache.get('static', {}) or {}

                now_ts = _time.time()
                is_stale = bool(probe_cache and (now_ts - probe_cache.get('last_updated', 0) > 20))

                mem_total = to_float(probe_cache.get('mem_total', 0.0))
                mem_usage_pct = clamp_percent(probe_cache.get('mem_usage', 0.0))
                mem_used = round(mem_total * mem_usage_pct / 100.0, 2)
                swap_total = to_float(probe_cache.get('swap_total', 0.0))
                swap_free = to_float(probe_cache.get('swap_free', 0.0))

                disk_total = to_float(probe_cache.get('disk_total', 0.0))
                disk_usage_pct = clamp_percent(probe_cache.get('disk_usage', 0.0))
                disk_used = round(disk_total * disk_usage_pct / 100.0, 2)

                cpu_usage_pct = 0.0 if is_stale else clamp_percent(probe_cache.get('cpu_usage', 0.0))
                cpu_cores = probe_cache.get('cpu_cores') or static.get('cpu_cores') or ssh_fallback_data.get(
                    'cpu_cores') or 0

                uptime_val = probe_cache.get('uptime') or ssh_fallback_data.get('uptime') or '--'
                if is_stale:
                    uptime_val = '⚠️ 已离线'

                return {
                    'os': static.get('os') or ssh_fallback_data.get('os') or '--',
                    'arch': static.get('arch') or ssh_fallback_data.get('arch') or '--',
                    'uptime': uptime_val,
                    'cpu_cores': cpu_cores,
                    'cpu_usage_pct': cpu_usage_pct,
                    'mem_total_gb': mem_total,
                    'mem_free_gb': max(mem_total - mem_used, 0.0) if mem_total else 0.0,
                    'mem_used_gb': mem_used,
                    'mem_cache_gb': to_float(probe_cache.get('mem_cache_gb', 0.0)),
                    'mem_usage_pct': 0.0 if is_stale else mem_usage_pct,
                    'swap_total_gb': swap_total,
                    'swap_free_gb': swap_free,
                    'swap_used_gb': max(swap_total - swap_free, 0.0),
                    'swap_usage_pct': 0.0 if is_stale else clamp_percent(
                        (max(swap_total - swap_free, 0.0) / swap_total * 100.0) if swap_total else 0.0),
                    'disk_device': probe_cache.get('disk_device') or '/',
                    'disk_total_gb': disk_total,
                    'disk_free_gb': max(disk_total - disk_used, 0.0) if disk_total else 0.0,
                    'disk_used_gb': disk_used,
                    'disk_usage_pct': disk_usage_pct,
                    'has_probe': bool(probe_cache)
                }

            server_dialog_key = server_conf.get('url') or server_conf.get('ssh_host') or str(id(server_conf))

            def open_ssh_page():
                if not server_conf.get('ssh_host'):
                    safe_notify('当前服务器未配置 SSH 主机，无法打开终端', 'warning')
                    return
                try:
                    client = ui.context.client
                except:
                    client = None
                asyncio.create_task(refresh_content('SSH_SINGLE', server_conf, manual_client=client))

            @ui.refreshable
            async def render_node_list():
                xui_nodes = await fetch_inbounds_safe(server_conf, force_refresh=False)
                if xui_nodes is None: xui_nodes = []
                custom_nodes = server_conf.get('custom_nodes', [])
                all_nodes = xui_nodes + custom_nodes

                if not all_nodes:
                    with ui.column().classes('w-full py-12 items-center justify-center opacity-50'):
                        ui.icon('radar', size='4rem').classes(
                            'text-cyan-900 mb-2 drop-shadow-[0_0_10px_rgba(6,182,212,0.5)]')
                        ui.label('暂无节点 (可直接新建)').classes('text-cyan-600/80 text-xs font-mono tracking-widest')
                else:
                    for n in all_nodes:
                        is_custom = n.get('_is_custom', False)
                        is_ssh_mode = (not is_custom) and (
                                server_conf.get('probe_installed') and server_conf.get('ssh_host'))

                        # 🛠️ 科技风：节点列表行
                        row_tech_cls = 'grid w-full gap-4 py-2.5 px-3 mb-2 items-center group bg-[#0a1120]/60 border border-[#1e3a5f]/40 border-l-[3px] border-l-transparent hover:border-[#1e3a5f] hover:border-l-cyan-400 hover:bg-[#0d172a] hover:shadow-[0_0_15px_rgba(34,211,238,0.15)] transition-all duration-300 cursor-default rounded-sm' if is_dark else 'grid w-full gap-4 py-2.5 px-3 mb-2 items-center group bg-white border border-slate-300/90 border-l-[3px] border-l-transparent hover:border-slate-300 hover:border-l-sky-500 hover:bg-sky-50 hover:shadow-[0_8px_20px_rgba(56,189,248,0.10)] transition-all duration-300 cursor-default rounded-sm'
                        with ui.element('div').classes(row_tech_cls).style(SINGLE_COLS_NO_PING):
                            ui.label(n.get('remark', '未命名')).classes(
                                'font-bold truncate w-full text-left pl-1 text-slate-300 text-[13px] group-hover:text-cyan-300 transition-colors' if is_dark else 'font-bold truncate w-full text-left pl-1 text-slate-800 text-[13px] group-hover:text-sky-700 transition-colors')
                            if is_custom:
                                ui.label('独立').classes(
                                    'text-[10px] bg-purple-950/50 text-purple-400 font-black px-2 py-0.5 rounded-sm w-fit mx-auto border border-purple-700/50 tracking-wider shadow-[0_0_5px_rgba(168,85,247,0.3)]')
                            elif is_ssh_mode:
                                ui.label('Root').classes(
                                    'text-[10px] bg-teal-950/50 text-teal-400 font-black px-2 py-0.5 rounded-sm w-fit mx-auto border border-teal-700/50 tracking-wider shadow-[0_0_5px_rgba(20,184,166,0.3)]')
                            else:
                                ui.label('API').classes(
                                    'text-[10px] bg-[#1e3a5f]/50 text-blue-300 font-black px-2 py-0.5 rounded-sm w-fit mx-auto border border-blue-700/50 tracking-wider shadow-[0_0_5px_rgba(59,130,246,0.3)]')

                            traffic = format_bytes(n.get('up', 0) + n.get('down', 0)) if not is_custom else '--'
                            ui.label(traffic).classes(
                                'text-[11px] text-cyan-500/70 w-full text-center font-mono font-bold tracking-wide' if is_dark else 'text-[11px] text-sky-700/80 w-full text-center font-mono font-bold tracking-wide')
                            proto = str(n.get('protocol', 'unk')).upper()
                            ui.label(proto).classes(
                                'text-[10px] font-black w-full text-center text-slate-500 tracking-widest' if is_dark else 'text-[10px] font-black w-full text-center text-slate-600 tracking-widest')
                            ui.label(str(n.get('port', 0))).classes(
                                'text-blue-400 font-mono w-full text-center font-bold text-[11px] drop-shadow-[0_0_3px_rgba(96,165,250,0.5)]' if is_dark else 'text-sky-700 font-mono w-full text-center font-bold text-[11px]')
                            is_enable = n.get('enable', True)
                            with ui.row().classes('w-full justify-center items-center gap-1.5'):
                                color = 'emerald' if is_enable else 'rose'
                                text = '启用' if is_enable else '停止'
                                ui.element('div').classes(
                                    f'w-1.5 h-1.5 rounded-none bg-{color}-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]' if is_enable else f'w-1.5 h-1.5 rounded-none bg-{color}-500 shadow-[0_0_8px_rgba(244,63,94,0.8)]')
                                ui.label(text).classes(f'text-[10px] font-bold text-{color}-400 tracking-wider')

                            with ui.row().classes(
                                    'gap-1.5 justify-center w-full no-wrap opacity-40 group-hover:opacity-100 transition-opacity duration-300'):
                                btn_props = 'flat dense size=sm round'
                                raw_link = n.get('_raw_link', '') or generate_node_link(n, server_conf['url'])
                                if raw_link:
                                    raw_btn = ui.button(icon='link', on_click=lambda u=raw_link: safe_copy_to_clipboard(u)).props(
                                        btn_props).classes(
                                        'text-slate-400 hover:bg-[#1e3a5f]/50 hover:text-cyan-400 hover:shadow-[0_0_8px_rgba(34,211,238,0.4)] transition-all' if is_dark else 'text-slate-400 hover:bg-sky-100 hover:text-sky-700 transition-all')
                                    apply_tooltip(raw_btn, '复制原始链接')

                                async def copy_detail_action(node_item=n):
                                    host = \
                                    server_conf.get('url', '').replace('http://', '').replace('https://', '').split(
                                        ':')[0]
                                    text = generate_detail_config(node_item, host)
                                    if text and not str(text).startswith('//'):
                                        await safe_copy_to_clipboard(text)
                                    else:
                                        ui.notify(text or '该协议不支持生成明文配置', type='warning')

                                detail_btn = ui.button(icon='data_object', on_click=copy_detail_action).props(btn_props).classes(
                                    'text-slate-400 hover:bg-[#1e3a5f]/50 hover:text-amber-400 hover:shadow-[0_0_8px_rgba(251,191,36,0.4)] transition-all' if is_dark else 'text-slate-400 hover:bg-amber-100 hover:text-amber-600 transition-all')
                                apply_tooltip(detail_btn, '复制明文配置')

                                if is_custom:
                                    edit_btn = ui.button(icon='edit_square',
                                              on_click=lambda node=n: open_edit_custom_node(node)).props(
                                        btn_props).classes(
                                        'text-blue-500 hover:bg-blue-900/30 hover:text-blue-300 transition-all')
                                    apply_tooltip(edit_btn, '编辑自定义节点')
                                    delete_btn = ui.button(icon='delete_sweep',
                                              on_click=lambda node=n: uninstall_and_delete(node)).props(
                                        btn_props).classes(
                                        'text-rose-500 hover:bg-rose-900/30 hover:text-rose-300 transition-all')
                                    apply_tooltip(delete_btn, '删除自定义节点')
                                elif has_manager_access:
                                    async def on_edit_success():
                                        ui.notify('修改成功')
                                        await reload_and_refresh_ui()

                                    edit_btn = ui.button(icon='edit_square',
                                              on_click=lambda i=n: open_inbound_dialog(mgr, i, on_edit_success,
                                                                                       is_3x_ui=server_conf.get(
                                                                                           'is_3x_ui', False))).props(
                                        btn_props).classes(
                                        'text-blue-500 hover:bg-blue-900/30 hover:text-blue-300 transition-all')
                                    apply_tooltip(edit_btn, '编辑节点')

                                    async def on_del_success():
                                        ui.notify('删除成功')
                                        await reload_and_refresh_ui()

                                    delete_btn = ui.button(icon='delete_sweep',
                                              on_click=lambda i=n: delete_inbound_with_confirm(mgr, i['id'],
                                                                                               i.get('remark', ''),
                                                                                               on_del_success)).props(
                                        btn_props).classes(
                                        'text-rose-500 hover:bg-rose-900/30 hover:text-rose-300 transition-all')
                                    apply_tooltip(delete_btn, '删除节点')
                                else:
                                    lock_icon = ui.icon('lock', size='xs').classes('text-slate-600')
                                    apply_tooltip(lock_icon, '拒绝访问')

            async def reload_and_refresh_ui():
                if mgr and hasattr(mgr, '_exec_remote_script'):
                    try:
                        new_inbounds = await run.io_bound(
                            lambda: asyncio.run(mgr.get_inbounds())) if not asyncio.iscoroutinefunction(
                            mgr.get_inbounds) else await mgr.get_inbounds()
                        if new_inbounds is not None:
                            NODES_DATA[server_conf['url']] = new_inbounds
                            server_conf['_status'] = 'online'
                            await save_nodes_cache()
                    except:
                        pass
                else:
                    try:
                        await fetch_inbounds_safe(server_conf, force_refresh=True)
                    except:
                        pass
                render_node_list.refresh()

            REFRESH_CURRENT_NODES = reload_and_refresh_ui
            _server_dialog.REFRESH_CURRENT_NODES = reload_and_refresh_ui

            def open_edit_custom_node(node_data):
                with ui.dialog() as d, ui.card().classes(
                        'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_18px_42px_rgba(148,163,184,0.18)]'):
                    with ui.column().classes('w-full bg-gradient-to-r from-[#0a1526] to-[#050a14] p-5 gap-2 border-b border-[#1e3a5f]/60 relative overflow-hidden' if is_dark else 'w-full bg-gradient-to-r from-[#f8fbff] to-[#eef4ff] p-5 gap-2 border-b border-slate-300/90 relative overflow-hidden'):
                        ui.element('div').classes('absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNCwyMTEsMjM4LDAuMDcpIi8+PC9zdmc+")] opacity-100 pointer-events-none')
                        with ui.row().classes('items-center gap-3 z-10'):
                            with ui.element('div').classes('w-9 h-9 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] shadow-[0_0_8px_rgba(0,0,0,0.7)] text-cyan-400 relative overflow-hidden' if is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] text-sky-600 relative overflow-hidden'):
                                ui.element('div').classes('absolute inset-0 bg-cyan-400/10')
                                ui.icon('edit_square').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                            with ui.column().classes('gap-0'):
                                ui.label('编辑节点备注').classes('text-lg font-black text-slate-100 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide')
                                ui.label('修改自定义节点名称').classes('text-[10px] text-slate-500 tracking-wide')
                    with ui.column().classes('w-full p-5 gap-4 bg-[#030712]' if is_dark else 'w-full p-5 gap-4 bg-[#f8fbff]'):
                        ui.label('节点名称').classes('text-[11px] font-bold text-cyan-500/80 tracking-wide mb-[-6px]' if is_dark else 'text-[11px] font-bold text-sky-700/80 tracking-wide mb-[-6px]')
                        with ui.element('div').classes('w-full rounded-sm border border-[#1e3a5f]/45 bg-[#08101d]/80 px-3 py-2 shadow-[0_0_8px_rgba(0,0,0,0.35)] transition-all hover:border-cyan-500/35' if is_dark else 'w-full rounded-sm border border-slate-300/90 bg-white px-3 py-2 shadow-[0_4px_12px_rgba(148,163,184,0.10)] transition-all hover:border-sky-400/60'):
                            name_input = ui.input(value=node_data.get('remark', '')).classes('w-full').props(
                                'dense outlined dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'dense outlined color=blue')

                    async def save():
                        node_data['remark'] = name_input.value.strip()
                        await save_servers()
                        safe_notify('修改已保存', 'positive')
                        d.close()
                        render_node_list.refresh()

                    with ui.row().classes('w-full justify-end p-4 gap-3 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'w-full justify-end p-4 gap-3 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eef4ff]'):
                        ui.button('取消', on_click=d.close).props('outline color=grey').classes(
                            'text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm')
                        ui.button('保存', on_click=save).props('flat').classes(
                            'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 hover:shadow-[0_0_12px_rgba(34,211,238,0.32)] px-6 font-black text-xs tracking-wide rounded-sm' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 px-6 font-black text-xs tracking-wide rounded-sm')
                d.open()

            async def uninstall_and_delete(node_data):
                with ui.dialog() as d, ui.card().classes(
                        'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-rose-800/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-rose-300 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                    with ui.column().classes('w-full p-5 gap-3 bg-gradient-to-r from-[#19070d] to-[#0b0911] border-b border-rose-900/60 relative overflow-hidden' if is_dark else 'w-full p-5 gap-3 bg-gradient-to-r from-rose-50 to-orange-50 border-b border-rose-200 relative overflow-hidden'):
                        ui.element('div').classes('absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgyNDQsNjMsOTQsMC4wNykiLz48L3N2Zz4=")] opacity-100 pointer-events-none')
                        with ui.row().classes('items-center gap-3 text-rose-400 z-10'):
                            with ui.element('div').classes('w-9 h-9 rounded-sm flex items-center justify-center bg-[#14070b] border border-rose-900/60 shadow-[0_0_8px_rgba(0,0,0,0.7)] relative overflow-hidden'):
                                ui.element('div').classes('absolute inset-0 bg-rose-400/10')
                                ui.icon('warning').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                            with ui.column().classes('gap-0'):
                                ui.label('卸载并清理环境').classes('font-black text-lg tracking-wide')
                                ui.label('此操作将删除节点并清理远程服务').classes('text-[10px] text-slate-400 tracking-wide')

                    with ui.column().classes('w-full p-5 gap-3 bg-[#030712]' if is_dark else 'w-full p-5 gap-3 bg-white'):
                        ui.label(f"目标节点：{node_data.get('remark', '未命名节点')}").classes('text-sm text-slate-200 font-bold')
                        ui.label('确认后将执行卸载脚本，并从当前服务器节点列表中移除。').classes('text-xs text-slate-400')

                    async def start_uninstall():
                        d.close()
                        notification = ui.notification(message='正在执行卸载与清理...', timeout=0, spinner=True)
                        success, output = await run.io_bound(
                            lambda: _ssh_exec_wrapper(server_conf, XHTTP_UNINSTALL_SCRIPT))
                        notification.dismiss()
                        if success: safe_notify('✅ 服务已卸载，进程已清理', 'positive')
                        if 'custom_nodes' in server_conf and node_data in server_conf['custom_nodes']:
                            server_conf['custom_nodes'].remove(node_data)
                            await save_servers()
                        await reload_and_refresh_ui()

                    with ui.row().classes('w-full justify-end p-4 gap-3 border-t border-rose-900/40 bg-[#0b0911]' if is_dark else 'w-full justify-end p-4 gap-3 border-t border-rose-200 bg-rose-50'):
                        ui.button('取消', on_click=d.close).props('outline color=grey').classes(
                            'text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-white text-xs font-bold tracking-wide rounded-sm')
                        ui.button('确认执行', color='red', on_click=start_uninstall).props('flat').classes(
                            'bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 hover:shadow-[0_0_12px_rgba(244,63,94,0.28)] px-5 font-black text-xs tracking-wide rounded-sm' if is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 px-5 font-black text-xs tracking-wide rounded-sm')
                d.open()

            # 🛠️ 科技风：重构顶部核心资产卡片
            with ui.row().classes(
                    'w-full justify-between items-center bg-gradient-to-r from-[#070e1a] to-[#0a1526] p-4 border border-[#1e3a5f]/60 border-t-[3px] border-t-cyan-500 shadow-[0_4px_20px_rgba(0,0,0,0.6)] flex-shrink-0 rounded-sm relative overflow-hidden' if is_dark else 'w-full justify-between items-center bg-gradient-to-r from-white to-[#eef4ff] p-4 border border-slate-300/90 border-t-[3px] border-t-sky-500 shadow-[0_8px_24px_rgba(148,163,184,0.14)] flex-shrink-0 rounded-sm relative overflow-hidden'):
                # 顶部卡片的赛博背景纹理
                ui.element('div').classes(
                    'absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNCwyMTEsMjM4LDAuMDcpIi8+PC9zdmc+")] opacity-100 pointer-events-none')

                with ui.row().classes('items-center gap-4 z-10'):
                    sys_icon = 'memory' if 'Oracle' in server_conf.get('name', '') else 'dns'
                    with ui.element('div').classes(
                            'p-3 bg-[#030712] rounded-sm border border-cyan-900/50 shadow-[inset_0_0_15px_rgba(6,182,212,0.1)]' if is_dark else 'p-3 bg-sky-50 rounded-sm border border-sky-200 shadow-[inset_0_0_12px_rgba(56,189,248,0.08)]'):
                        ui.icon(sys_icon, size='md').classes('text-cyan-400 drop-shadow-[0_0_8px_rgba(34,211,238,0.8)]')
                    with ui.column().classes('gap-1 min-w-0'):
                        with ui.row().classes('items-center gap-3 no-wrap'):
                            ui.label(server_conf.get('name', '未命名服务器')).classes(
                                'text-xl font-black text-slate-100 tracking-wide drop-shadow-md truncate max-w-[520px]' if is_dark else 'text-xl font-black text-slate-800 tracking-wide truncate max-w-[520px]')
                        with ui.row().classes('items-center gap-3 flex-wrap'):
                            raw_host = server_conf.get('ssh_host') or \
                                       server_conf.get('url', '').replace('http://', '').replace('https://', '').split(
                                           ':')[0]
                            ui.label(raw_host).classes('text-[11px] font-mono font-bold text-cyan-500/85' if is_dark else 'text-[11px] font-mono font-bold text-sky-700/85')

                            @ui.refreshable
                            def live_status_badge():
                                import time as _time
                                is_online = False
                                now_ts = _time.time()
                                probe_cache = PROBE_DATA_CACHE.get(server_conf['url'])
                                if probe_cache and (now_ts - probe_cache.get('last_updated', 0) < 20):
                                    is_online = True
                                elif server_conf.get('_status') == 'online':
                                    is_online = True

                                status_color = 'green' if is_online else 'rose'
                                ui.badge('Online' if is_online else 'Offline',
                                         color=status_color).props('outline rounded-sm').classes(
                                    f'text-[10px] font-black tracking-wide text-green-400 shadow-[0_0_6px_rgba(16,185,129,0.22)]' if is_online else f'text-[10px] font-black tracking-wide text-rose-400 shadow-[0_0_6px_rgba(244,63,94,0.22)]')

                                snap = get_cached_snapshot()
                                os_name = snap.get('os')
                                if os_name and os_name != '--':
                                    clean_os = os_name.split('(')[0].replace('GNU/Linux', '').replace('  ', ' ').strip()
                                    os_logo_url, _ = get_os_visual(os_name)
                                    with ui.row().classes(
                                            'items-center gap-1.5 opacity-80 hover:opacity-100 transition-opacity'):
                                        ui.element('img').props(f'src="{os_logo_url}"').classes(
                                            'w-3.5 h-3.5 object-contain shrink-0 filter brightness-125')
                                        ui.label(clean_os).classes(
                                            'text-[11px] font-bold text-cyan-200/80 truncate max-w-[180px]')

                            live_status_badge()
                            ui.timer(3.0, live_status_badge.refresh)
                with ui.row().classes('items-center justify-end z-10'):
                    if server_conf.get('ssh_host'):
                        ui.button('进入 SSH 终端', icon='terminal', on_click=open_ssh_page).props(
                            'flat size=sm').classes(
                            'bg-[#0a1120]/80 border border-cyan-700/50 text-cyan-400 hover:bg-cyan-900/40 hover:shadow-[0_0_15px_rgba(34,211,238,0.4)] px-4 py-1.5 font-bold text-[11px] rounded-sm transition-all' if is_dark else 'bg-sky-100 border border-sky-300 text-sky-700 hover:bg-sky-200 px-4 py-1.5 font-bold text-[11px] rounded-sm transition-all')

            ui.element('div').classes('h-4 flex-shrink-0')

            vps_container = ui.element('div').classes(
                f'w-full flex-shrink-0 p-0 gap-0 flex flex-col relative {shell_card_cls}')
            with vps_container:
                with ui.row().classes(
                        f'w-full items-center justify-between px-4 py-2 min-h-[48px] {shell_header_cls}'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('query_stats').classes('text-cyan-500 drop-shadow-[0_0_5px_rgba(6,182,212,0.8)]')
                        ui.label('VPS 运行信息').classes('text-xs font-black tracking-wide text-slate-200' if is_dark else 'text-xs font-black tracking-wide text-slate-800')

                    @ui.refreshable
                    def render_sync_status():
                        import time as _time
                        probe_cache = PROBE_DATA_CACHE.get(server_conf['url'])
                        if probe_cache and (_time.time() - probe_cache.get('last_updated', 0)) <= 20:
                            with ui.row().classes('items-center gap-1.5'):
                                ui.element('div').classes(
                                    'w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,1)] animate-pulse')
                                ui.label('探针实时同步中').classes(
                                    'text-[11px] text-emerald-400/90 font-bold tracking-normal')
                        else:
                            with ui.row().classes('items-center gap-1.5'):
                                ui.element('div').classes(
                                    'w-2 h-2 rounded-full bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,1)]')
                                ui.label('探针已断联 (离线)').classes(
                                    'text-[11px] text-rose-500/90 font-bold tracking-normal')

                    render_sync_status()

                with ui.column().classes(f'w-full gap-4 p-4 relative {shell_body_cls}'):
                    with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-2 gap-4 items-stretch relative z-10'):
                        # ===== 左侧：系统信息卡片 =====
                        with ui.card().classes(f'w-full h-full {section_card_cls}'):
                            snap = get_cached_snapshot()
                            render_section_header('系统信息', 'developer_board', 'text-cyan-400',
                                                  '操作系统 / 架构 / 在线时间',
                                                  right_renderer=lambda: ui.label(f"{snap['cpu_cores']} C").classes(
                                                      'text-[10px] font-black text-cyan-300 bg-cyan-900/30 px-2 py-1 rounded-sm border border-cyan-700/50 shadow-[0_0_8px_rgba(6,182,212,0.2)] tracking-widest' if is_dark else 'text-[10px] font-black text-sky-700 bg-sky-100 px-2 py-1 rounded-sm border border-sky-300 shadow-[0_4px_10px_rgba(56,189,248,0.10)] tracking-widest'))

                            with ui.column().classes('w-full p-4 gap-4'):
                                @ui.refreshable
                                def render_sys_dyn():
                                    snap = get_cached_snapshot()
                                    with ui.row().classes(
                                            'w-full min-h-[64px] items-center justify-between gap-4 px-4 py-4 rounded-sm bg-[#0a1120]/80 border border-[#1e3a5f]/50 border-l-[3px] border-l-cyan-600 shadow-[0_0_10px_rgba(0,0,0,0.5)] flex-nowrap' if is_dark else 'w-full min-h-[64px] items-center justify-between gap-4 px-4 py-4 rounded-sm bg-white border border-slate-300/90 border-l-[3px] border-l-sky-500 shadow-[0_6px_18px_rgba(148,163,184,0.12)] flex-nowrap'):
                                        ui.label('CPU 使用率').classes(
                                            'text-[11px] font-bold tracking-wider text-cyan-600/80 leading-none shrink-0' if is_dark else 'text-[11px] font-bold tracking-wider text-sky-700/85 leading-none shrink-0')
                                        pct = snap.get('cpu_usage_pct', 0.0)
                                        # 🛠️ 科技风进度条：直角、发光
                                        bar_glow = 'shadow-[0_0_10px_rgba(34,211,238,0.8)] bg-cyan-400' if pct < 60 else (
                                            'shadow-[0_0_10px_rgba(250,204,21,0.8)] bg-yellow-400' if pct < 85 else 'shadow-[0_0_10px_rgba(244,63,94,0.8)] bg-rose-500')
                                        with ui.element('div').classes(
                                                'w-1/2 max-w-[190px] ml-auto bg-[#030712] rounded-none h-[24px] relative overflow-hidden border border-[#1e3a5f] shrink-0' if is_dark else 'w-1/2 max-w-[190px] ml-auto bg-slate-100 rounded-none h-[24px] relative overflow-hidden border border-slate-300 shrink-0'):
                                            ui.element('div').classes(
                                                f'h-full {bar_glow} transition-all duration-500').style(
                                                f'width: {pct}%')
                                            ui.label(f'{pct:.1f}%').classes(progress_text_class(pct))
                                    render_metric_row('处理器架构', format_arch_text(snap['arch']),
                                                      value_color='text-blue-300')
                                    render_metric_row('在线运行时间', snap['uptime'], value_color='text-emerald-400')

                                render_sys_dyn()

                        # ===== 右侧：内存信息卡片 =====
                        with ui.card().classes(f'w-full h-full {section_card_cls}'):
                            @ui.refreshable
                            def render_mem_card():
                                snap = get_cached_snapshot()
                                render_section_header('内存信息', 'memory', 'text-emerald-400',
                                                      '系统内存 / 空闲 / SWAP 使用情况',
                                                      right_renderer=lambda: ui.label(
                                                          f"{fmt_gb(snap['mem_total_gb'])}").classes(
                                                          'text-[10px] font-black text-emerald-300 bg-emerald-900/30 px-2 py-1 rounded-sm border border-emerald-700/50 shadow-[0_0_8px_rgba(16,185,129,0.2)] tracking-widest' if is_dark else 'text-[10px] font-black text-emerald-700 bg-emerald-100 px-2 py-1 rounded-sm border border-emerald-300 shadow-[0_4px_10px_rgba(16,185,129,0.10)] tracking-widest'))
                                with ui.column().classes('w-full p-4 gap-4'):
                                    with ui.row().classes(
                                            'w-full min-h-[64px] items-center justify-between gap-4 px-4 py-4 rounded-sm bg-[#0a1120]/80 border border-[#1e3a5f]/50 border-l-[3px] border-l-emerald-600 shadow-[0_0_10px_rgba(0,0,0,0.5)] flex-nowrap' if is_dark else 'w-full min-h-[64px] items-center justify-between gap-4 px-4 py-4 rounded-sm bg-white border border-slate-300/90 border-l-[3px] border-l-emerald-500 shadow-[0_6px_18px_rgba(148,163,184,0.12)] flex-nowrap'):
                                        ui.label('真实使用内存').classes(
                                            'text-[11px] font-bold tracking-wider text-emerald-600/80 leading-none shrink-0' if is_dark else 'text-[11px] font-bold tracking-wider text-emerald-700/85 leading-none shrink-0')
                                        pct, val = snap['mem_usage_pct'], fmt_gb(snap['mem_used_gb'])
                                        bar_glow = 'shadow-[0_0_10px_rgba(250,204,21,0.8)] bg-yellow-400' if pct > 80 else 'shadow-[0_0_10px_rgba(16,185,129,0.8)] bg-emerald-400'
                                        with ui.element('div').classes(
                                                'w-1/2 max-w-[190px] ml-auto bg-[#030712] rounded-none h-[24px] relative overflow-hidden border border-[#1e3a5f] shrink-0' if is_dark else 'w-1/2 max-w-[190px] ml-auto bg-slate-100 rounded-none h-[24px] relative overflow-hidden border border-slate-300 shrink-0'):
                                            ui.element('div').classes(
                                                f'h-full {bar_glow} transition-all duration-500').style(
                                                f'width: {pct}%')
                                            ui.label(f'{val} ({pct:.0f}%)').classes(progress_text_class(pct))
                                    render_metric_row('空闲可用内存', fmt_gb(snap['mem_free_gb']),
                                                      f"剩余占比: {max(0.0, 100.0 - snap['mem_usage_pct']):.0f}%",
                                                      value_color='text-teal-300')
                                    render_metric_row('SWAP 虚拟内存',
                                                      f"{fmt_gb(snap['swap_used_gb'])} / {fmt_gb(snap['swap_total_gb'])}",
                                                      f"使用率: {snap['swap_usage_pct']:.0f}%",
                                                      value_color='text-purple-400')

                            render_mem_card()

                    # ===== 下方：磁盘信息卡片 =====
                    with ui.card().classes(f'w-full relative z-10 {section_card_cls}'):
                        @ui.refreshable
                        def render_disk_card():
                            snap = get_cached_snapshot()
                            render_section_header('磁盘信息', 'storage', 'text-amber-400',
                                                  '根分区容量、已用空间、剩余空间与占用率',
                                                  right_renderer=lambda: ui.label(
                                                      f"{fmt_gb(snap['disk_total_gb'])}").classes(
                                                      'text-[10px] font-black text-amber-300 bg-amber-900/30 px-2 py-1 rounded-sm border border-amber-700/50 shadow-[0_0_8px_rgba(245,158,11,0.2)] tracking-widest' if is_dark else 'text-[10px] font-black text-amber-700 bg-amber-100 px-2 py-1 rounded-sm border border-amber-300 shadow-[0_4px_10px_rgba(245,158,11,0.10)] tracking-widest'))
                            with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-3 gap-5 p-4'):
                                render_metric_row('磁盘设备', snap.get('disk_device', '/'),
                                                  value_color='text-indigo-300')

                                with ui.row().classes(
                                        'w-full min-h-[64px] items-center justify-between gap-4 px-4 py-4 rounded-sm bg-[#0a1120]/80 border border-[#1e3a5f]/50 border-l-[3px] border-l-amber-600 shadow-[0_0_10px_rgba(0,0,0,0.5)] flex-nowrap' if is_dark else 'w-full min-h-[64px] items-center justify-between gap-4 px-4 py-4 rounded-sm bg-white border border-slate-300/90 border-l-[3px] border-l-amber-500 shadow-[0_6px_18px_rgba(148,163,184,0.12)] flex-nowrap'):
                                    ui.label('已用容量').classes(
                                        'text-[11px] font-bold tracking-wider text-amber-600/80 leading-none shrink-0' if is_dark else 'text-[11px] font-bold tracking-wider text-amber-700/85 leading-none shrink-0')

                                    pct = snap.get('disk_usage_pct', 0.0)
                                    val = fmt_gb(snap['disk_used_gb'])
                                    bar_glow = 'shadow-[0_0_10px_rgba(249,115,22,0.8)] bg-orange-500' if pct > 85 else 'shadow-[0_0_10px_rgba(251,191,36,0.8)] bg-amber-400'
                                    with ui.element('div').classes(
                                            'w-1/2 max-w-[170px] ml-auto bg-[#030712] rounded-none h-[24px] relative overflow-hidden border border-[#1e3a5f] shrink-0' if is_dark else 'w-1/2 max-w-[170px] ml-auto bg-slate-100 rounded-none h-[24px] relative overflow-hidden border border-slate-300 shrink-0'):
                                        ui.element('div').classes(
                                            f'h-full {bar_glow} transition-all duration-500').style(f'width: {pct}%')
                                        ui.label(f'{val} ({pct:.0f}%)').classes(progress_text_class(pct))


                                with ui.row().classes(
                                        'w-full min-h-[64px] items-center justify-between gap-4 px-4 py-4 rounded-sm bg-[#0a1120]/80 border border-[#1e3a5f]/50 border-l-[3px] border-l-emerald-600 shadow-[0_0_10px_rgba(0,0,0,0.5)] flex-nowrap' if is_dark else 'w-full min-h-[64px] items-center justify-between gap-4 px-4 py-4 rounded-sm bg-white border border-slate-300/90 border-l-[3px] border-l-emerald-500 shadow-[0_6px_18px_rgba(148,163,184,0.12)] flex-nowrap'):
                                    ui.label('空闲剩余').classes(
                                        'text-[11px] font-bold tracking-wider text-emerald-600/80 leading-none shrink-0' if is_dark else 'text-[11px] font-bold tracking-wider text-emerald-700/85 leading-none shrink-0')

                                    free_pct = 100.0 - pct if pct > 0 else 100.0
                                    val = fmt_gb(snap['disk_free_gb'])
                                    bar_glow = 'shadow-[0_0_10px_rgba(16,185,129,0.8)] bg-emerald-400'
                                    with ui.element('div').classes(
                                            'w-1/2 max-w-[170px] ml-auto bg-[#030712] rounded-none h-[24px] relative overflow-hidden border border-[#1e3a5f] shrink-0' if is_dark else 'w-1/2 max-w-[170px] ml-auto bg-slate-100 rounded-none h-[24px] relative overflow-hidden border border-slate-300 shrink-0'):
                                        ui.element('div').classes(
                                            f'h-full {bar_glow} transition-all duration-500').style(
                                            f'width: {free_pct}%')
                                        ui.label(f'{val} ({free_pct:.0f}%)').classes(progress_text_class(free_pct))

                        render_disk_card()

                def safe_refresh():
                    try:
                        if not vps_container.is_deleted:
                            render_sync_status.refresh()
                            render_sys_dyn.refresh()
                            render_mem_card.refresh()
                            render_disk_card.refresh()
                    except:
                        pass

                ui.timer(2.0, safe_refresh)

            ui.element('div').classes('h-6 flex-shrink-0')

            with ui.element('div').classes(
                    f'w-full flex-1 min-h-[300px] flex flex-col p-0 relative {shell_card_cls}'):
                with ui.row().classes(
                        f'w-full items-center justify-between p-3 gap-3 flex-wrap flex-shrink-0 relative z-10 {shell_header_cls}'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('hub').classes('text-blue-500 drop-shadow-[0_0_5px_rgba(59,130,246,0.8)]')
                        ui.label('节点列表').classes('text-sm font-black tracking-wider text-slate-200' if is_dark else 'text-sm font-black tracking-wider text-slate-800')
                        if server_conf.get('probe_installed') and server_conf.get('ssh_host'):
                            ui.badge('Root 模式', color='teal').props('outline rounded-sm').classes(
                                'text-[10px] font-bold tracking-wider shadow-[0_0_5px_rgba(20,184,166,0.3)] ml-2')

                    with ui.row().classes('items-center gap-3 flex-wrap justify-end'):
                        from app.services.deployment import open_deploy_hysteria_dialog, open_deploy_snell_dialog, \
                            open_deploy_xhttp_dialog

                        # 🛠️ 科技风：霓虹线框按钮
                        btn_tech_base = 'text-[11px] font-bold px-4 py-1.5 border transition-all duration-300 tracking-wider rounded-sm backdrop-blur-sm'
                        btn_cyan_theme = 'bg-cyan-950/40 text-cyan-400 border-cyan-500/50 hover:bg-cyan-900/60 hover:shadow-[0_0_12px_rgba(34,211,238,0.5)]' if is_dark else 'bg-sky-100 text-sky-700 border-sky-300 hover:bg-sky-200'
                        btn_purple_theme = 'bg-purple-950/40 text-purple-400 border-purple-500/50 hover:bg-purple-900/60 hover:shadow-[0_0_12px_rgba(168,85,247,0.5)]' if is_dark else 'bg-violet-100 text-violet-700 border-violet-300 hover:bg-violet-200'
                        btn_cyan = f'{btn_cyan_theme} {btn_tech_base}'
                        btn_purple = f'{btn_purple_theme} {btn_tech_base}'

                        ui.button('一键部署 XHTTP', icon='rocket_launch',
                                  on_click=lambda: open_deploy_xhttp_dialog(server_conf, reload_and_refresh_ui)).props(
                            'flat size=sm').classes(btn_cyan)
                        ui.button('一键部署 Hy2', icon='bolt', on_click=lambda: open_deploy_hysteria_dialog(server_conf,
                                                                                                            reload_and_refresh_ui)).props(
                            'flat size=sm').classes(btn_cyan)
                        ui.button('一键部署 Snell', icon='security',
                                  on_click=lambda: open_deploy_snell_dialog(server_conf, reload_and_refresh_ui)).props(
                            'flat size=sm').classes(btn_cyan)

                        if has_manager_access:
                            async def on_add_success():
                                ui.notify('添加节点成功')
                                await reload_and_refresh_ui()

                            ui.button('新建 XUI 节点', icon='add',
                                      on_click=lambda: open_inbound_dialog(mgr, None, on_add_success,
                                                                           is_3x_ui=server_conf.get('is_3x_ui', False))).props(
                                'flat size=sm').classes(btn_purple)
                        else:
                            ui.button('探针只读', icon='visibility', on_click=None).props(
                                'flat size=sm disabled').classes(
                                'bg-slate-900/50 text-slate-600 border border-slate-700/50 text-[11px] font-bold tracking-wider rounded-sm px-4 py-1.5' if is_dark else 'bg-slate-100 text-slate-500 border border-slate-300 text-[11px] font-bold tracking-wider rounded-sm px-4 py-1.5')

                with ui.element('div').classes(
                        'grid w-full gap-4 font-bold pb-2 pt-2 px-3 text-[11px] tracking-wider flex-shrink-0 z-10 border-b border-[#1e3a5f]/50 text-cyan-600/80 bg-[#030712]' if is_dark else 'grid w-full gap-4 font-bold pb-2 pt-2 px-3 text-[11px] tracking-wider flex-shrink-0 z-10 border-b border-slate-300/90 text-sky-700/80 bg-[#f8fbff]').style(
                    SINGLE_COLS_NO_PING):
                    ui.label('节点名称').classes('text-left pl-1')
                    for h in ['类型', '流量', '协议', '端口', '状态', '操作']: ui.label(h).classes('text-center')

                with ui.element('div').classes('w-full relative flex-1 min-h-0'):
                    with ui.element('div').classes('absolute inset-0 bg-[#030712]' if is_dark else 'absolute inset-0 bg-[#f8fbff]'):
                        with ui.scroll_area().classes('w-full h-full p-2'):
                            await render_node_list()

            if has_manager_access and not NODES_DATA.get(server_conf['url']):
                ui.timer(0.2, lambda: asyncio.create_task(reload_and_refresh_ui()), once=True)


__all__ = ['render_single_server_view']