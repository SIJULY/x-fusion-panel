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
    page_bg = 'var(--xf-bg-main)'
    shell_card_cls = 'rounded-sm border overflow-hidden'
    shell_header_cls = 'border-b'
    shell_body_cls = ''
    section_card_cls = 'rounded-sm p-0 gap-0 overflow-hidden border'

    def apply_tooltip(target, text):
        tip = target.tooltip(text)
        tip.classes('text-[11px] font-bold px-2 py-1 rounded-sm')
        tip.style(
            'background:var(--xf-tooltip-bg);color:var(--xf-tooltip-text);border:1px solid var(--xf-tooltip-border);box-shadow:var(--xf-tooltip-shadow);')
        return tip

    SINGLE_COLS_NO_PING = _server_dialog.SINGLE_COLS_NO_PING
    XHTTP_UNINSTALL_SCRIPT = _server_dialog.XHTTP_UNINSTALL_SCRIPT
    _sync_resolve_ip = _server_dialog._sync_resolve_ip

    # 防止侧边栏切换导致的 SSH 僵尸进程残留
    _server_dialog.cleanup_ssh_route_terminal()

    from app.ui.pages.content_router import content_container, refresh_content

    if content_container:
        content_container.clear()
        # 📌 修复全局状态污染：不再使用 .classes(replace=...) 覆盖全局容器
        # 仅修改背景色，将高度和溢出控制交还给原系统，防止污染 content_router
        content_container.style(f'background-color: {page_bg};')

    with content_container:
        # 📌 布局隔离舱：创建一个专属的 wrapper 来接管高度和滚动，防止样式泄露到其他页面
        with ui.element('div').classes('w-full flex flex-col justify-start items-stretch overflow-y-auto').style('height: calc(100vh - 80px);'):
            with ui.element('div').classes('w-full max-w-[1440px] mx-auto min-h-full flex flex-col gap-4 flex-nowrap pb-4'):
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
                    if pct >= 72:
                        return 'absolute inset-0 z-10 flex items-center justify-center text-[11px] font-black text-slate-900 font-mono leading-none tracking-tight'
                    return 'absolute inset-0 z-10 flex items-center justify-center text-[11px] font-black font-mono leading-none tracking-tight'

                def progress_text_style(pct):
                    try:
                        pct = float(pct or 0)
                    except:
                        pct = 0
                    if pct >= 72:
                        return 'color: #0f172a;'
                    return 'color: var(--xf-text-strong); text-shadow: 0 1px 1px rgba(15,23,42,0.35);'

                def render_progress_row(label, pct, text, accent='#22d3ee'):
                    progress_row_cls = 'w-full min-h-[32px] items-center justify-between gap-2 px-4 py-2 rounded-sm border border-l-[3px] flex-nowrap relative overflow-hidden group transition-all'
                    progress_overlay_cls = 'absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none'
                    glow_shadow = f'0 0 0 1px color-mix(in srgb, {accent} 18%, transparent), 0 0 16px color-mix(in srgb, {accent} {36 if is_dark else 16}%, transparent), 0 6px 18px rgba(15,23,42,0.10)'
                    with ui.row().classes(progress_row_cls).style(
                            f'background: var(--xf-soft-bg); border-color: var(--xf-card-border); border-left-color: {accent}; box-shadow: {glow_shadow};'):
                        ui.element('div').classes(progress_overlay_cls).style(
                            f'background: linear-gradient(to right, color-mix(in srgb, {accent} 16%, transparent), transparent);')
                        ui.label(label).classes('text-[11px] font-bold tracking-wider leading-none shrink-0 z-10').style(
                            f'color: {accent};')
                        with ui.element('div').classes(
                                'w-1/2 max-w-[190px] ml-auto rounded-none h-[24px] relative overflow-hidden border shrink-0 z-10').style(
                            'background: var(--xf-code-bg); border-color: var(--xf-card-border);'):
                            ui.element('div').classes('h-full transition-all duration-500').style(
                                f'width: {pct}%; background: {accent}; box-shadow: 0 0 10px color-mix(in srgb, {accent} 60%, transparent);')
                            ui.label(text).classes(progress_text_class(pct)).style(progress_text_style(pct))

                def render_metric_row(label, value, sub_text='', value_color='#22d3ee', accent='#22d3ee'):
                    metric_row_cls = 'w-full min-h-[31px] items-center justify-between gap-2 px-4 py-2 border border-l-[3px] transition-all flex-nowrap relative overflow-hidden group'
                    metric_overlay_cls = 'absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none'
                    glow_shadow = f'0 0 0 1px color-mix(in srgb, {accent} 18%, transparent), 0 0 16px color-mix(in srgb, {accent} {36 if is_dark else 16}%, transparent), 0 6px 18px rgba(15,23,42,0.10)'
                    with ui.row().classes(metric_row_cls).style(
                            f'background: var(--xf-soft-bg); border-color: var(--xf-card-border); border-left-color: {accent}; box-shadow: {glow_shadow};'):
                        ui.element('div').classes(metric_overlay_cls).style(
                            f'background: linear-gradient(to right, color-mix(in srgb, {accent} 16%, transparent), transparent);')
                        with ui.column().classes('gap-0.5 min-w-0 flex-1 justify-center z-10'):
                            ui.label(label).classes('text-[11px] font-bold tracking-wide leading-none').style(
                                f'color: {accent}; opacity: 0.92;')
                            if sub_text:
                                ui.label(sub_text).classes('text-[10px] break-all leading-relaxed font-mono').style(
                                    'color: var(--xf-text-muted);')
                        ui.label(str(value)).classes(
                            'text-sm font-black text-right shrink-0 font-mono tracking-wide z-10').style(
                            f'color: {value_color};')

                def render_section_header(title, icon, accent_class, desc='', right_renderer=None):
                    header_row_cls = 'w-full items-center justify-between px-4 py-2.5 border-b min-h-[56px] relative overflow-hidden'
                    header_line_cls = 'absolute top-0 left-0 w-1/3 h-[1px]'
                    icon_wrap_base = 'w-8 h-8 rounded-sm flex items-center justify-center relative overflow-hidden group'
                    icon_wrap_cls = f'{icon_wrap_base} border {accent_class}'
                    with ui.row().classes(header_row_cls).style(
                            'border-color: var(--xf-card-border); background: linear-gradient(to right, var(--xf-soft-bg), transparent);'):
                        ui.element('div').classes(header_line_cls).style(
                            'background: linear-gradient(to right, var(--xf-accent), transparent); opacity: 0.65;')
                        with ui.row().classes('items-center gap-3 z-10'):
                            with ui.element('div').classes(icon_wrap_cls).style(
                                    'background: var(--xf-code-bg); border-color: var(--xf-card-border); box-shadow: 0 4px 12px rgba(15,23,42,0.12);'):
                                ui.element('div').classes('absolute inset-0 bg-current opacity-10')
                                ui.icon(icon).classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                            with ui.column().classes('gap-0 justify-center'):
                                ui.label(title).classes('text-sm font-black tracking-wide').style(
                                    'color: var(--xf-text-strong);')
                                if desc:
                                    ui.label(desc).classes('text-[10px] tracking-wide').style(
                                        'color: var(--xf-text-muted);')
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

                cloudflare_dns_state = {
                    'loading': True,
                    'error': '',
                    'records': [],
                    'zones': [],
                    'ip': '--',
                }

                def relative_record_name(full_name, zone_name):
                    full_name = str(full_name or '').strip()
                    zone_name = str(zone_name or '').strip()
                    if not full_name or not zone_name:
                        return ''
                    if full_name == zone_name:
                        return '@'
                    suffix = f'.{zone_name}'
                    if full_name.endswith(suffix):
                        return full_name[:-len(suffix)]
                    return full_name

                async def load_cloudflare_records():
                    cloudflare_dns_state.update({
                        'loading': True,
                        'error': '',
                        'records': [],
                    })
                    try:
                        cf_handler = CloudflareHandler()
                        if not cf_handler.token or not cf_handler.root_domain:
                            cloudflare_dns_state.update({
                                'loading': False,
                                'error': '',
                                'records': [],
                                'zones': [],
                                'ip': '--',
                            })
                            return

                        zone_success, zone_result = await run.io_bound(cf_handler.list_zones)
                        zones = []
                        if zone_success:
                            zones = [item.get('name', '') for item in (zone_result or []) if item.get('name')]
                        elif cf_handler.root_domain:
                            zones = [cf_handler.root_domain]

                        cloudflare_dns_state['zones'] = zones
                        if not zones:
                            cloudflare_dns_state.update({
                                'loading': False,
                                'error': '未找到可用的 Cloudflare 域名',
                                'records': [],
                                'ip': '--',
                            })
                            return

                        target_host = server_conf.get('ssh_host') or \
                                      server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[
                                          0]
                        resolved_ip = await run.io_bound(lambda: _sync_resolve_ip(target_host))
                        cloudflare_dns_state['ip'] = resolved_ip or '--'

                        if not resolved_ip:
                            cloudflare_dns_state.update({
                                'loading': False,
                                'error': '无法解析当前服务器 IP',
                                'records': [],
                            })
                            return

                        success, result = await cf_handler.list_a_records_by_ip(resolved_ip)
                        if success:
                            cloudflare_dns_state.update({
                                'loading': False,
                                'error': '',
                                'records': result or [],
                            })
                        else:
                            cloudflare_dns_state.update({
                                'loading': False,
                                'error': str(result or 'Cloudflare 查询失败'),
                                'records': [],
                            })
                    except Exception as e:
                        cloudflare_dns_state.update({
                            'loading': False,
                            'error': f'Cloudflare 查询失败: {e}',
                            'records': [],
                        })
                    finally:
                        try:
                            render_cloudflare_dns_card.refresh()
                        except:
                            pass

                async def open_cloudflare_record_dialog(record=None):
                    from nicegui import app
                    dialog_is_dark = bool(app.storage.user.get('is_dark', True))
                    cf_handler = CloudflareHandler()
                    ok, result = await run.io_bound(cf_handler.list_zones)
                    zones = [item.get('name', '') for item in (result or []) if item.get('name')] if ok else []
                    if not zones:
                        zones = cloudflare_dns_state.get('zones', []) or (
                            [] if not cf_handler.root_domain else [cf_handler.root_domain])
                    if not zones:
                        safe_notify('未获取到 Cloudflare 域名列表，请检查 Token 权限', 'warning')
                        return

                    cloudflare_dns_state['zones'] = zones
                    default_zone = (record or {}).get('zone_name') or (zones[0] if zones else '')
                    default_name = relative_record_name((record or {}).get('name', ''), default_zone) if record else ''
                    dialog_title = '编辑 A 记录' if record else '添加 A 记录'

                    with ui.dialog() as d, ui.card().classes(
                            'w-[680px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if dialog_is_dark else 'w-[680px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                        with ui.column().classes(
                                'w-full bg-gradient-to-r from-[#0a1526] to-[#050a14] p-5 gap-2 border-b border-[#1e3a5f]/60 relative overflow-hidden' if dialog_is_dark else 'w-full bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] p-5 gap-2 border-b border-slate-300/90 relative overflow-hidden'):
                            with ui.row().classes('items-center gap-3 z-10'):
                                ui.icon('cloud').classes('text-orange-400 drop-shadow-[0_0_6px_currentColor]')
                                ui.label(dialog_title).classes(
                                    'text-lg font-black text-slate-100 tracking-wide' if dialog_is_dark else 'text-lg font-black text-slate-800 tracking-wide')
                        with ui.column().classes(
                                'w-full p-5 gap-4 bg-[#030712]' if dialog_is_dark else 'w-full p-5 gap-4 bg-[#f8fbff]'):
                            with ui.grid().classes('w-full grid-cols-1 md:grid-cols-2 gap-4'):
                                name_input = ui.input('名称', value=default_name, placeholder='例如: api 或 @').classes(
                                    'w-full').props(
                                    'outlined dense dark color=cyan standout bg-color="[#050b14]" input-class=text-slate-100' if dialog_is_dark else 'outlined dense color=blue')
                                zone_select = ui.select(zones, value=default_zone, label='域名').classes('w-full').props(
                                    'outlined dense dark color=cyan standout bg-color="[#050b14]" options-dark popup-content-class=bg-[#050b14] input-class=text-slate-100' if dialog_is_dark else 'outlined dense color=blue')
                            ui.label(f"将解析到当前 VPS IP：{cloudflare_dns_state.get('ip', '--')}").classes(
                                'text-[11px]').style(
                                'color: var(--xf-text-muted);')

                        async def save_record():
                            name_val = str(name_input.value or '').strip()
                            zone_val = str(zone_select.value or '').strip()
                            ip_val = str(cloudflare_dns_state.get('ip', '--')).strip()
                            if not name_val:
                                safe_notify('记录名称不能为空', 'warning')
                                return
                            if not zone_val:
                                safe_notify('请选择域名', 'warning')
                                return
                            if not ip_val or ip_val == '--':
                                safe_notify('当前 VPS IP 无效，无法保存', 'warning')
                                return

                            cf_handler = CloudflareHandler()
                            if record:
                                ok, msg = await cf_handler.update_a_record(record.get('id', ''), name_val, zone_val, ip_val,
                                                                           proxied=bool(record.get('proxied', False)))
                            else:
                                ok, msg = await cf_handler.create_a_record(name_val, zone_val, ip_val, proxied=False)

                            if ok:
                                safe_notify('Cloudflare A 记录已保存', 'positive')
                                d.close()
                                await load_cloudflare_records()
                            else:
                                safe_notify(str(msg), 'negative')

                        with ui.row().classes(
                                'w-full justify-end p-4 gap-3 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if dialog_is_dark else 'w-full justify-end p-4 gap-3 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]'):
                            ui.button('取消', on_click=d.close).props('outline color=grey')
                            ui.button('保存', on_click=save_record).props('flat').classes(
                                'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 px-6 font-black text-xs tracking-wide rounded-sm' if dialog_is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 px-6 font-black text-xs tracking-wide rounded-sm')
                    d.open()

                def open_delete_cloudflare_record(record):
                    from nicegui import app
                    dialog_is_dark = bool(app.storage.user.get('is_dark', True))
                    with ui.dialog() as d, ui.card().classes(
                            'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if dialog_is_dark else 'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                        with ui.column().classes(
                                'w-full bg-gradient-to-r from-[#0a1526] to-[#050a14] p-5 gap-2 border-b border-[#1e3a5f]/60 relative overflow-hidden' if dialog_is_dark else 'w-full bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] p-5 gap-2 border-b border-slate-300/90 relative overflow-hidden'):
                            with ui.row().classes('items-center gap-3 z-10'):
                                ui.icon('delete').classes('text-rose-400 drop-shadow-[0_0_6px_currentColor]')
                                ui.label('删除 A 记录').classes(
                                    'text-lg font-black text-slate-100 tracking-wide' if dialog_is_dark else 'text-lg font-black text-slate-800 tracking-wide')
                        with ui.column().classes(
                                'w-full p-5 gap-4 bg-[#030712]' if dialog_is_dark else 'w-full p-5 gap-4 bg-[#f8fbff]'):
                            ui.label('确认删除下面这条 Cloudflare A 记录吗？').classes('text-sm font-bold').style(
                                'color: var(--xf-text-strong);')
                            with ui.row().classes('items-center gap-2 rounded-sm border px-4 py-3').style(
                                    'background: var(--xf-soft-bg); border-color: var(--xf-card-border);'):
                                ui.icon('cloud').classes('text-orange-400')
                                ui.label(record.get('name', '--')).classes('text-sm font-black break-all').style(
                                    'color: var(--xf-text-strong);')

                        async def do_delete():
                            cf_handler = CloudflareHandler()
                            ok, msg = await cf_handler.delete_record_by_id(record.get('id', ''),
                                                                           record.get('zone_name', ''))
                            if ok:
                                safe_notify('Cloudflare A 记录已删除', 'positive')
                                d.close()
                                await load_cloudflare_records()
                            else:
                                safe_notify(str(msg), 'negative')

                        with ui.row().classes(
                                'w-full justify-end p-4 gap-3 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if dialog_is_dark else 'w-full justify-end p-4 gap-3 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]'):
                            ui.button('取消', on_click=d.close).props('outline color=grey')
                            ui.button('删除', on_click=do_delete).props('flat').classes(
                                'bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 px-6 font-black text-xs tracking-wide rounded-sm' if dialog_is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 px-6 font-black text-xs tracking-wide rounded-sm')
                    d.open()

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
                    xui_nodes = NODES_DATA.get(server_conf['url'], []) or []
                    if not xui_nodes:
                        fetched_nodes = await fetch_inbounds_safe(server_conf, force_refresh=False)
                        xui_nodes = fetched_nodes or xui_nodes
                    custom_nodes = server_conf.get('custom_nodes', [])
                    all_nodes = xui_nodes + custom_nodes

                    if not all_nodes:
                        with ui.column().classes('w-full py-12 items-center justify-center opacity-50'):
                            ui.icon('radar', size='4rem').classes('mb-2 drop-shadow-[0_0_10px_rgba(6,182,212,0.5)]').style(
                                'color: var(--xf-accent);')
                            ui.label('暂无节点 (可直接新建)').classes('text-xs font-mono tracking-widest').style(
                                'color: var(--xf-accent); opacity: 0.8;')
                    else:
                        for n in all_nodes:
                            is_custom = n.get('_is_custom', False)
                            is_ssh_mode = (not is_custom) and (
                                    server_conf.get('probe_installed') and server_conf.get('ssh_host'))

                            row_tech_cls = 'grid w-full gap-4 py-2.5 px-3 mb-2 items-center group border border-l-[3px] transition-all duration-300 cursor-default rounded-sm relative overflow-hidden'
                            row_overlay_cls = 'absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none'
                            row_accent = '#a855f7' if is_custom else ('#14b8a6' if is_ssh_mode else '#3b82f6')
                            row_shadow = f'0 0 0 1px color-mix(in srgb, {row_accent} 18%, transparent), 0 0 16px color-mix(in srgb, {row_accent} {38 if is_dark else 16}%, transparent), 0 6px 18px rgba(15,23,42,0.10)'
                            with ui.element('div').classes(row_tech_cls).style(
                                    f'{SINGLE_COLS_NO_PING} background: var(--xf-soft-bg); border-color: var(--xf-card-border); border-left-color: {row_accent}; box-shadow: {row_shadow};'):
                                ui.element('div').classes(row_overlay_cls).style(
                                    f'background: linear-gradient(to right, color-mix(in srgb, {row_accent} 16%, transparent), transparent);')
                                ui.label(n.get('remark', '未命名')).classes(
                                    'font-bold truncate w-full text-left pl-2 text-[13px] transition-colors relative z-10').style(
                                    'color: var(--xf-text-strong);')
                                if is_custom:
                                    ui.label('独立').classes(
                                        'text-[10px] text-purple-400 font-black w-full text-center tracking-wider relative z-10')
                                elif is_ssh_mode:
                                    ui.label('Root').classes(
                                        'text-[10px] text-teal-400 font-black w-full text-center tracking-wider relative z-10')
                                else:
                                    ui.label('API').classes(
                                        'text-[10px] text-blue-300 font-black w-full text-center tracking-wider relative z-10')

                                traffic = format_bytes(n.get('up', 0) + n.get('down', 0)) if not is_custom else '--'
                                ui.label(traffic).classes(
                                    'text-[11px] w-full text-center font-mono font-bold tracking-wide relative z-10').style(
                                    'color: var(--xf-accent); opacity: 0.8;')
                                proto = str(n.get('protocol', 'unk')).upper()
                                ui.label(proto).classes(
                                    'text-[10px] font-black w-full text-center tracking-widest relative z-10').style(
                                    'color: var(--xf-text-muted);')
                                ui.label(str(n.get('port', 0))).classes(
                                    'font-mono w-full text-center font-bold text-[11px] relative z-10').style(
                                    'color: var(--xf-accent);')
                                is_enable = n.get('enable', True)
                                with ui.row().classes('w-full justify-center items-center gap-1.5 relative z-10'):
                                    color = 'emerald' if is_enable else 'rose'
                                    text = '启用' if is_enable else '停止'
                                    ui.element('div').classes(
                                        f'w-1.5 h-1.5 rounded-none bg-{color}-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]' if is_enable else f'w-1.5 h-1.5 rounded-none bg-{color}-500 shadow-[0_0_8px_rgba(244,63,94,0.8)]')
                                    ui.label(text).classes(f'text-[10px] font-bold text-{color}-400 tracking-wider')

                                with ui.row().classes(
                                        'gap-1 justify-center w-full no-wrap min-w-0 opacity-40 group-hover:opacity-100 transition-opacity duration-300 relative z-10'):
                                    btn_props = 'flat dense size=sm round'
                                    raw_link = n.get('_raw_link', '') or generate_node_link(n, server_conf['url'])
                                    if raw_link:
                                        raw_btn = ui.button(icon='link',
                                                            on_click=lambda u=raw_link: safe_copy_to_clipboard(u)).props(
                                            btn_props).classes(
                                            'text-slate-400 transition-all').style('color: var(--xf-text-muted);')
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

                                    detail_btn = ui.button(icon='data_object', on_click=copy_detail_action).props(
                                        btn_props).classes(
                                        'text-slate-400 transition-all').style('color: var(--xf-text-muted);')
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
                                            await refresh_after_inbound_change(delay_second_refresh=True)

                                        edit_btn = ui.button(icon='edit_square',
                                                             on_click=lambda i=n: open_inbound_dialog(mgr, i,
                                                                                                      on_edit_success,
                                                                                                      is_3x_ui=server_conf.get(
                                                                                                          'is_3x_ui',
                                                                                                          False))).props(
                                            btn_props).classes(
                                            'text-blue-500 hover:bg-blue-900/30 hover:text-blue-300 transition-all')
                                        apply_tooltip(edit_btn, '编辑节点')

                                        async def on_del_success(inbound_id=n.get('id')):
                                            ui.notify('删除成功')
                                            await refresh_after_inbound_change(removed_inbound_id=inbound_id)

                                        delete_btn = ui.button(icon='delete_sweep',
                                                               on_click=lambda i=n: delete_inbound_with_confirm(mgr,
                                                                                                                i['id'],
                                                                                                                i.get(
                                                                                                                    'remark',
                                                                                                                    ''),
                                                                                                                on_del_success)).props(
                                            btn_props).classes(
                                            'text-rose-500 hover:bg-rose-900/30 hover:text-rose-300 transition-all')
                                        apply_tooltip(delete_btn, '删除节点')
                                    else:
                                        lock_icon = ui.icon('lock', size='xs').classes('text-slate-600')
                                        apply_tooltip(lock_icon, '拒绝访问')

                async def reload_and_refresh_ui():
                    old_nodes = NODES_DATA.get(server_conf['url'], []) or []
                    new_nodes = None
                    fetch_success = False

                    try:
                        fetched_nodes = await fetch_inbounds_safe(server_conf, force_refresh=True)
                        if fetched_nodes is not None:
                            new_nodes = fetched_nodes
                            fetch_success = True
                    except Exception as e:
                        logger.warning(f"API 获取节点失败: {e}")

                    if not fetch_success and mgr and hasattr(mgr, '_exec_remote_script'):
                        try:
                            if not asyncio.iscoroutinefunction(mgr.get_inbounds):
                                ssh_nodes = await run.io_bound(lambda: asyncio.run(mgr.get_inbounds()))
                            else:
                                ssh_nodes = await mgr.get_inbounds()

                            if ssh_nodes is not None:
                                new_nodes = ssh_nodes
                                fetch_success = True
                        except Exception as e:
                            logger.warning(f"SSH 获取节点失败: {e}")

                    if fetch_success:
                        NODES_DATA[server_conf['url']] = new_nodes
                        server_conf['_status'] = 'online'
                        asyncio.create_task(save_nodes_cache())
                    else:
                        NODES_DATA[server_conf['url']] = old_nodes

                    render_node_list.refresh()

                REFRESH_CURRENT_NODES = reload_and_refresh_ui
                _server_dialog.REFRESH_CURRENT_NODES = reload_and_refresh_ui

                async def refresh_after_inbound_change(delay_second_refresh=False, removed_inbound_id=None,
                                                       refresh_cloudflare=False):
                    server_url = server_conf.get('url')
                    if removed_inbound_id is not None and server_url in NODES_DATA:
                        old_cached_nodes = NODES_DATA.get(server_url, []) or []
                        NODES_DATA[server_url] = [node for node in old_cached_nodes if node.get('id') != removed_inbound_id]
                        render_node_list.refresh()
                    await reload_and_refresh_ui()
                    if delay_second_refresh:
                        await asyncio.sleep(0.8)
                        await reload_and_refresh_ui()
                        await asyncio.sleep(1.2)
                        await reload_and_refresh_ui()
                    if refresh_cloudflare:
                        await load_cloudflare_records()

                def open_edit_custom_node(node_data):
                    from nicegui import app
                    dialog_is_dark = bool(app.storage.user.get('is_dark', True))

                    with ui.dialog() as d, ui.card().classes(
                            'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if dialog_is_dark else 'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_18px_42px_rgba(148,163,184,0.18)]'):
                        with ui.column().classes(
                                'w-full bg-gradient-to-r from-[#0a1526] to-[#050a14] p-5 gap-2 border-b border-[#1e3a5f]/60 relative overflow-hidden' if dialog_is_dark else 'w-full bg-gradient-to-r from-[#f8fbff] to-[#eef4ff] p-5 gap-2 border-b border-slate-300/90 relative overflow-hidden'):
                            ui.element('div').classes(
                                'absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNCwyMTEsMjM4LDAuMDcpIi8+PC9zdmc+")] opacity-100 pointer-events-none')
                            with ui.row().classes('items-center gap-3 z-10'):
                                with ui.element('div').classes(
                                        'w-9 h-9 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] shadow-[0_0_8px_rgba(0,0,0,0.7)] text-cyan-400 relative overflow-hidden' if dialog_is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] text-sky-600 relative overflow-hidden'):
                                    ui.element('div').classes('absolute inset-0 bg-cyan-400/10' if dialog_is_dark else 'absolute inset-0 bg-sky-400/10')
                                    ui.icon('edit_square').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                                with ui.column().classes('gap-0'):
                                    ui.label('编辑节点备注').classes(
                                        'text-lg font-black text-slate-100 tracking-wide' if dialog_is_dark else 'text-lg font-black text-slate-800 tracking-wide')
                                    ui.label('修改自定义节点名称').classes('text-[10px] text-slate-500 tracking-wide')
                        with ui.column().classes(
                                'w-full p-5 gap-4 bg-[#030712]' if dialog_is_dark else 'w-full p-5 gap-4 bg-[#f8fbff]'):
                            ui.label('节点名称').classes(
                                'text-[11px] font-bold text-cyan-500/80 tracking-wide mb-[-6px]' if dialog_is_dark else 'text-[11px] font-bold text-sky-700/80 tracking-wide mb-[-6px]')
                            with ui.element('div').classes(
                                    'w-full rounded-sm border border-[#1e3a5f]/45 bg-[#08101d]/80 px-3 py-2 shadow-[0_0_8px_rgba(0,0,0,0.35)] transition-all hover:border-cyan-500/35' if dialog_is_dark else 'w-full rounded-sm border border-slate-300/90 bg-white px-3 py-2 shadow-[0_4px_12px_rgba(148,163,184,0.10)] transition-all hover:border-sky-400/60'):
                                name_input = ui.input(value=node_data.get('remark', '')).classes('w-full').props(
                                    'dense outlined dark color=cyan standout' if dialog_is_dark else 'dense outlined color=blue')

                        async def save():
                            node_data['remark'] = name_input.value.strip()
                            await save_servers()
                            safe_notify('修改已保存', 'positive')
                            d.close()
                            render_node_list.refresh()

                        with ui.row().classes(
                                'w-full justify-end p-4 gap-3 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if dialog_is_dark else 'w-full justify-end p-4 gap-3 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eef4ff]'):
                            ui.button('取消', on_click=d.close).props('outline color=grey').classes(
                                'text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if dialog_is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm')
                            ui.button('保存', on_click=save).props('flat').classes(
                                'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 hover:shadow-[0_0_12px_rgba(34,211,238,0.32)] px-6 font-black text-xs tracking-wide rounded-sm' if dialog_is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 px-6 font-black text-xs tracking-wide rounded-sm')
                    d.open()

                async def uninstall_and_delete(node_data):
                    from nicegui import app
                    dialog_is_dark = bool(app.storage.user.get('is_dark', True))

                    with ui.dialog() as d, ui.card().classes(
                            'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-rose-800/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if dialog_is_dark else 'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-rose-300 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                        with ui.column().classes(
                                'w-full p-5 gap-3 bg-gradient-to-r from-[#19070d] to-[#0b0911] border-b border-rose-900/60 relative overflow-hidden' if dialog_is_dark else 'w-full p-5 gap-3 bg-gradient-to-r from-rose-50 to-orange-50 border-b border-rose-200 relative overflow-hidden'):
                            ui.element('div').classes(
                                'absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgyNDQsNjMsOTQsMC4wNykiLz48L3N2Zz4=")] opacity-100 pointer-events-none')
                            with ui.row().classes('items-center gap-3 text-rose-400 z-10'):
                                with ui.element('div').classes(
                                        'w-9 h-9 rounded-sm flex items-center justify-center bg-[#14070b] border border-rose-900/60 shadow-[0_0_8px_rgba(0,0,0,0.7)] relative overflow-hidden' if dialog_is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-rose-50 border border-rose-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] relative overflow-hidden'):
                                    ui.element('div').classes('absolute inset-0 bg-rose-400/10')
                                    ui.icon('warning').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                                with ui.column().classes('gap-0'):
                                    ui.label('卸载并清理环境').classes('font-black text-lg tracking-wide').style(
                                        'color: var(--xf-text-strong);')
                                    ui.label('此操作将删除节点并清理远程服务').classes(
                                        'text-[10px] tracking-wide').style('color: var(--xf-text-muted);')

                        with ui.column().classes(
                                'w-full p-5 gap-3 bg-[#030712]' if dialog_is_dark else 'w-full p-5 gap-3 bg-white'):
                            ui.label(f"目标节点：{node_data.get('remark', '未命名节点')}").classes(
                                'text-sm font-bold').style('color: var(--xf-text-strong);')
                            ui.label('确认后将执行卸载脚本，并从当前服务器节点列表中移除。').classes('text-xs').style(
                                'color: var(--xf-text-muted);')

                        raw_link = node_data.get('_raw_link', '')
                        domain_to_del = None
                        if raw_link and '://' in raw_link:
                            try:
                                from urllib.parse import parse_qs, urlparse
                                query = urlparse(raw_link).query
                                params = parse_qs(query)
                                if params.get('sni'):
                                    domain_to_del = str(params['sni'][0]).strip()
                                elif params.get('host'):
                                    domain_to_del = str(params['host'][0]).strip()
                            except:
                                pass

                        async def start_uninstall():
                            d.close()
                            notification = ui.notification(message='正在执行卸载与清理...', timeout=0, spinner=True)
                            success, output = await run.io_bound(
                                lambda: _ssh_exec_wrapper(server_conf, XHTTP_UNINSTALL_SCRIPT))
                            notification.dismiss()
                            if success:
                                safe_notify('✅ 服务已卸载，进程已清理', 'positive')
                            else:
                                safe_notify('⚠️ 远程卸载可能未完全成功，请检查日志或服务器状态', 'warning')

                            if domain_to_del:
                                try:
                                    cf = CloudflareHandler()
                                    if cf.token and cf.root_domain and (cf.root_domain in domain_to_del):
                                        ok, msg = await cf.delete_record_by_domain(domain_to_del)
                                        if ok:
                                            safe_notify(f'☁️ {msg}', 'positive')
                                        else:
                                            safe_notify(f'⚠️ DNS 删除失败: {msg}', 'warning')
                                except Exception as e:
                                    safe_notify(f'⚠️ DNS 删除异常: {e}', 'warning')

                            if 'custom_nodes' in server_conf and node_data in server_conf['custom_nodes']:
                                server_conf['custom_nodes'].remove(node_data)
                                await save_servers()
                            await reload_and_refresh_ui()
                            await load_cloudflare_records()

                        with ui.row().classes(
                                'w-full justify-end p-4 gap-3 border-t border-rose-900/40 bg-[#0b0911]' if dialog_is_dark else 'w-full justify-end p-4 gap-3 border-t border-rose-200 bg-rose-50'):
                            ui.button('取消', on_click=d.close).props('outline color=grey').classes(
                                'text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if dialog_is_dark else 'text-slate-600 border-slate-300 hover:bg-white text-xs font-bold tracking-wide rounded-sm')
                            ui.button('确认执行', color='red', on_click=start_uninstall).props('flat').classes(
                                'bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 hover:shadow-[0_0_12px_rgba(244,63,94,0.28)] px-5 font-black text-xs tracking-wide rounded-sm' if dialog_is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 px-5 font-black text-xs tracking-wide rounded-sm')
                    d.open()

                # --------------------- 1. 顶部核心资产卡片 (强锁定高度 flex-shrink-0) ---------------------
                with ui.row().classes(
                        'w-full justify-between items-center p-4 border border-t-[3px] flex-shrink-0 rounded-sm relative overflow-hidden').style(
                    'background: linear-gradient(to right, var(--xf-panel-bg), var(--xf-soft-bg)); border-color: var(--xf-card-border); border-top-color: var(--xf-accent); box-shadow: 0 8px 24px rgba(15,23,42,0.12);'):
                    ui.element('div').classes(
                        'absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNCwyMTEsMjM4LDAuMDcpIi8+PC9zdmc+")] opacity-100 pointer-events-none')

                    with ui.row().classes('items-center gap-4 z-10'):
                        sys_icon = 'memory' if 'Oracle' in server_conf.get('name', '') else 'dns'
                        with ui.element('div').classes(
                                'p-3 rounded-sm border').style(
                            'background: var(--xf-code-bg); border-color: var(--xf-card-border); box-shadow: inset 0 0 12px rgba(15,23,42,0.10);'):
                            ui.icon(sys_icon, size='md').classes('drop-shadow-[0_0_8px_rgba(34,211,238,0.8)]').style(
                                'color: var(--xf-accent);')
                        with ui.column().classes('gap-1 min-w-0'):
                            with ui.row().classes('items-center gap-3 no-wrap'):
                                ui.label(server_conf.get('name', '未命名服务器')).classes(
                                    'text-xl font-black tracking-wide drop-shadow-md truncate max-w-[520px]').style(
                                    'color: var(--xf-text-strong);')
                            with ui.row().classes('items-center gap-3 flex-wrap'):
                                raw_host = server_conf.get('ssh_host') or \
                                           server_conf.get('url', '').replace('http://', '').replace('https://', '').split(
                                               ':')[0]
                                ui.label(raw_host).classes('text-[11px] font-mono font-bold').style(
                                    'color: var(--xf-accent); opacity: 0.85;')

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
                                                'text-[11px] font-bold truncate max-w-[180px]').style(
                                                'color: var(--xf-text-muted);')

                                live_status_badge()
                                ui.timer(3.0, live_status_badge.refresh)
                    with ui.row().classes('items-center justify-end z-10'):
                        if server_conf.get('ssh_host'):
                            ui.button('进入 SSH 终端', icon='terminal', on_click=open_ssh_page).props(
                                'flat size=sm').classes(
                                'px-4 py-1.5 font-bold text-[11px] rounded-sm transition-all border').style(
                                'background: var(--xf-soft-bg); border-color: var(--xf-card-border); color: var(--xf-accent);')

                # --------------------- 2. VPS 运行信息区 (强锁定高度 flex-shrink-0) ---------------------
                vps_container = ui.element('div').classes(
                    f'w-full flex-shrink-0 p-0 gap-0 flex flex-col relative {shell_card_cls}')
                with vps_container:
                    with ui.row().classes(
                            f'w-full items-center justify-between px-4 py-2 min-h-[48px] {shell_header_cls}'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('query_stats').classes('drop-shadow-[0_0_5px_rgba(6,182,212,0.8)]').style(
                                'color: var(--xf-accent);')
                            ui.label('VPS 运行信息').classes('text-sm font-black tracking-wide').style(
                                'color: var(--xf-text-strong);')

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
                                                          'text-[10px] font-black px-2 py-1 rounded-sm border tracking-widest').style(
                                                          'color: var(--xf-accent); background: var(--xf-soft-bg); border-color: var(--xf-card-border); box-shadow: 0 4px 10px rgba(15,23,42,0.10);'))

                                with ui.column().classes('w-full p-4 gap-4'):
                                    @ui.refreshable
                                    def render_sys_dyn():
                                        snap = get_cached_snapshot()
                                        pct = snap.get('cpu_usage_pct', 0.0)
                                        cpu_color = '#22d3ee' if pct < 60 else ('#facc15' if pct < 85 else '#f43f5e')
                                        render_progress_row('CPU 使用率', pct, f'{pct:.1f}%', cpu_color)
                                        render_metric_row('处理器架构', format_arch_text(snap['arch']),
                                                          value_color='#3b82f6', accent='#3b82f6')
                                        render_metric_row('在线运行时间', snap['uptime'], value_color='#10b981',
                                                          accent='#10b981')

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
                                                              'text-[10px] font-black px-2 py-1 rounded-sm border tracking-widest').style(
                                                              'color: #10b981; background: var(--xf-soft-bg); border-color: var(--xf-card-border); box-shadow: 0 4px 10px rgba(15,23,42,0.10);'))
                                    with ui.column().classes('w-full p-4 gap-4'):
                                        pct, val = snap['mem_usage_pct'], fmt_gb(snap['mem_used_gb'])
                                        render_progress_row('已使用内存', pct, f'{val} ({pct:.0f}%)',
                                                            '#10b981' if pct <= 80 else '#facc15')

                                        free_pct, free_val = max(0.0, 100.0 - snap['mem_usage_pct']), fmt_gb(
                                            snap['mem_free_gb'])
                                        render_progress_row('空闲可用内存', free_pct, f'{free_val} ({free_pct:.0f}%)',
                                                            '#14b8a6')

                                        swap_pct = snap['swap_usage_pct']
                                        swap_val = f"{fmt_gb(snap['swap_used_gb'])} / {fmt_gb(snap['swap_total_gb'])}"
                                        render_progress_row('SWAP 虚拟内存', swap_pct, f'{swap_val} ({swap_pct:.0f}%)',
                                                            '#a855f7')

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
                                                          'text-[10px] font-black px-2 py-1 rounded-sm border tracking-widest').style(
                                                          'color: #f59e0b; background: var(--xf-soft-bg); border-color: var(--xf-card-border); box-shadow: 0 4px 10px rgba(15,23,42,0.10);'))
                                with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-3 gap-5 p-4'):
                                    render_metric_row('磁盘设备', snap.get('disk_device', '/'),
                                                      value_color='#8b5cf6', accent='#8b5cf6')

                                    pct = snap.get('disk_usage_pct', 0.0)
                                    val = fmt_gb(snap['disk_used_gb'])
                                    render_progress_row('已用容量', pct, f'{val} ({pct:.0f}%)',
                                                        '#f59e0b' if pct <= 85 else '#f97316')

                                    free_pct = 100.0 - pct if pct > 0 else 100.0
                                    val = fmt_gb(snap['disk_free_gb'])
                                    render_progress_row('空闲剩余', free_pct, f'{val} ({free_pct:.0f}%)', '#10b981')

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


                # --------------------- 3. Cloudflare 记录区 (动态伸缩：小屏不抢占节点列表空间) ---------------------
                # 小屏高度有限时不要强制保底 140px，否则会把节点列表挤出视口；内容自身滚动即可。
                with ui.element('div').classes(
                        f'w-full flex-shrink flex flex-col min-h-[96px] max-h-[240px] p-0 gap-0 relative z-10 {shell_card_cls}'):
                    @ui.refreshable
                    def render_cloudflare_dns_card():
                        async def open_new_cloudflare_record(_=None):
                            await open_cloudflare_record_dialog()

                        async def open_edit_cloudflare_record(item):
                            await open_cloudflare_record_dialog(item)

                        cf_config_ready = bool(
                            ADMIN_CONFIG.get('cf_api_token', '').strip() and ADMIN_CONFIG.get('cf_root_domain', '').strip())

                        def render_cf_header_actions():
                            add_btn = ui.button('添加记录', icon='add', on_click=open_new_cloudflare_record).props(
                                'flat size=sm')
                            add_btn.classes(
                                'px-4 py-1.5 font-bold text-[11px] tracking-wider rounded-sm transition-all border')
                            add_btn.style(
                                'background: var(--xf-soft-bg); border-color: var(--xf-card-border); color: var(--xf-accent);')

                            if not cf_config_ready:
                                add_btn.disable()
                                apply_tooltip(add_btn, '请先完成 Cloudflare API 配置')

                        # Header 强锁高度
                        with ui.row().classes(
                                f'w-full flex-shrink-0 items-center justify-between px-4 py-2 min-h-[48px] {shell_header_cls}'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('cloud').classes('text-orange-400 drop-shadow-[0_0_5px_rgba(251,146,60,0.8)]')
                                ui.label('Cloudflare 解析记录').classes('text-sm font-black tracking-wide').style(
                                    'color: var(--xf-text-strong);')
                            with ui.row().classes('items-center justify-end'):
                                render_cf_header_actions()

                        # 内容区：原生弹性滚动接管
                        with ui.element('div').classes(f'w-full flex-1 overflow-y-auto py-3 px-[16px] relative {shell_body_cls}'):
                            with ui.column().classes('w-full gap-2'):
                                if not cf_config_ready:
                                    with ui.column().classes(
                                            'w-full items-center justify-center gap-3 rounded-sm border px-6 py-8 text-center').style(
                                            'background: var(--xf-soft-bg); border-color: var(--xf-card-border);'):
                                        ui.icon('cloud_off').classes(
                                            'text-[28px] text-orange-400 drop-shadow-[0_0_8px_rgba(251,146,60,0.45)]')
                                        ui.label('尚未设置 Cloudflare API 配置').classes('text-sm font-black').style(
                                            'color: var(--xf-text-strong);')
                                elif cloudflare_dns_state.get('loading', False):
                                    with ui.row().classes('items-center gap-2 rounded-sm border px-4 py-3').style(
                                            'background: var(--xf-soft-bg); border-color: var(--xf-card-border);'):
                                        ui.spinner(size='sm', color='orange')
                                        ui.label('正在查询 Cloudflare A 记录...').classes('text-sm font-bold').style(
                                            'color: var(--xf-text-strong);')
                                elif cloudflare_dns_state.get('error'):
                                    with ui.row().classes('items-center gap-2 rounded-sm border px-4 py-3').style(
                                            'background: var(--xf-soft-bg); border-color: var(--xf-card-border);'):
                                        ui.icon('warning').classes('text-amber-400')
                                        ui.label(cloudflare_dns_state.get('error')).classes('text-sm break-all').style(
                                            'color: var(--xf-text-muted);')
                                else:
                                    records = cloudflare_dns_state.get('records', []) or []
                                    if not records:
                                        with ui.row().classes('items-center gap-2 rounded-sm border px-4 py-3').style(
                                                'background: var(--xf-soft-bg); border-color: var(--xf-card-border);'):
                                            ui.icon('dns').classes('text-slate-400')
                                            ui.label('当前没有解析到该 VPS IP 的 Cloudflare A 记录').classes('text-sm').style(
                                                'color: var(--xf-text-muted);')
                                    else:
                                        for rec in records:
                                            row_tech_cls = 'w-full items-center justify-between gap-3 py-2.5 px-3 mb-2 group border border-l-[3px] transition-all duration-300 cursor-default rounded-sm relative overflow-hidden'
                                            row_overlay_cls = 'absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none'
                                            row_accent = '#f59e0b'
                                            row_shadow = f'0 0 0 1px color-mix(in srgb, {row_accent} 18%, transparent), 0 0 16px color-mix(in srgb, {row_accent} {38 if is_dark else 16}%, transparent), 0 6px 18px rgba(15,23,42,0.10)'

                                            with ui.row().classes(row_tech_cls).style(
                                                    f'background: var(--xf-soft-bg); border-color: var(--xf-card-border); border-left-color: {row_accent}; box-shadow: {row_shadow};'):
                                                ui.element('div').classes(row_overlay_cls).style(
                                                    f'background: linear-gradient(to right, color-mix(in srgb, {row_accent} 16%, transparent), transparent);')
                                                ui.label(rec.get('name', '--')).classes(
                                                    'font-bold truncate flex-1 min-w-0 text-left pl-2 text-[13px] transition-colors relative z-10').style(
                                                    'color: var(--xf-text-strong);')
                                                with ui.row().classes('items-center gap-1 shrink-0 relative z-10'):
                                                    ui.label('已代理' if rec.get('proxied') else '仅 DNS').classes(
                                                        'text-[10px] font-black px-2 py-1 rounded-sm border tracking-wider').style(
                                                        (
                                                            'color: #f59e0b; background: rgba(245, 158, 11, 0.10); border-color: rgba(245, 158, 11, 0.35);'
                                                            if rec.get('proxied') else
                                                            'color: #94a3b8; background: rgba(148, 163, 184, 0.10); border-color: rgba(148, 163, 184, 0.35);'))
                                                    action_wrap = ui.row().classes(
                                                        'gap-1 justify-center no-wrap min-w-0 opacity-40 group-hover:opacity-100 transition-opacity duration-300 relative z-10')
                                                    with action_wrap:
                                                        copy_btn = ui.button(icon='content_copy',
                                                                             on_click=lambda domain=rec.get('name',
                                                                                                            ''): safe_copy_to_clipboard(
                                                                                 domain)).props(
                                                            'flat dense round size=sm')
                                                        copy_btn.style('color: var(--xf-text-muted);')
                                                        apply_tooltip(copy_btn, '复制域名')
                                                        edit_btn = ui.button(icon='edit_square', on_click=lambda _,
                                                                                                                 item=rec: open_edit_cloudflare_record(
                                                            item)).props(
                                                            'flat dense round size=sm')
                                                        edit_btn.style('color: #3b82f6;')
                                                        apply_tooltip(edit_btn, '编辑记录')
                                                        del_btn = ui.button(icon='delete', on_click=lambda
                                                            item=rec: open_delete_cloudflare_record(item)).props(
                                                            'flat dense round size=sm')
                                                        del_btn.style('color: #f43f5e;')
                                                        apply_tooltip(del_btn, '删除记录')

                    render_cloudflare_dns_card()
                    if ADMIN_CONFIG.get('cf_api_token', '').strip() and ADMIN_CONFIG.get('cf_root_domain', '').strip():
                        ui.timer(0.2, load_cloudflare_records, once=True)


                # --------------------- 4. 节点列表区 (小屏可见保底 + 页面可滚动兜底) ---------------------
                # 13 寸等低高度屏幕下，外层允许纵向滚动；节点列表自身保底展示，避免被上方卡片挤没。
                with ui.element('div').classes(
                        f'w-full flex-1 min-h-[260px] flex flex-col p-0 relative z-10 {shell_card_cls}'):
                    
                    # Header
                    with ui.row().classes(
                            f'w-full flex-shrink-0 items-center justify-between px-4 py-3 gap-3 flex-wrap relative z-10 {shell_header_cls}'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('hub').classes('text-blue-500 drop-shadow-[0_0_5px_rgba(59,130,246,0.8)]')
                            ui.label('节点列表').classes(
                                'text-sm font-black tracking-wider text-slate-200' if is_dark else 'text-sm font-black tracking-wider text-slate-800')
                            if server_conf.get('probe_installed') and server_conf.get('ssh_host'):
                                ui.badge('Root 模式', color='teal').props('outline rounded-sm').classes(
                                    'text-[10px] font-bold tracking-wider shadow-[0_0_5px_rgba(20,184,166,0.3)] ml-2')

                        with ui.row().classes('items-center gap-3 flex-wrap justify-end'):
                            from app.services.deployment import open_deploy_hysteria_dialog, open_deploy_snell_dialog, \
                                open_deploy_xhttp_dialog

                            btn_tech_base = 'text-[11px] font-bold px-4 py-1.5 border transition-all duration-300 tracking-wider rounded-sm backdrop-blur-sm'
                            btn_cyan = btn_tech_base
                            btn_purple = btn_tech_base

                            async def open_xhttp_deploy():
                                await open_deploy_xhttp_dialog(server_conf, lambda: refresh_after_inbound_change(
                                    delay_second_refresh=True, refresh_cloudflare=True))

                            async def open_hy2_deploy():
                                await open_deploy_hysteria_dialog(server_conf, lambda: refresh_after_inbound_change(
                                    delay_second_refresh=True))

                            async def open_snell_deploy():
                                await open_deploy_snell_dialog(server_conf, lambda: refresh_after_inbound_change(
                                    delay_second_refresh=True))

                            ui.button('一键部署 XHTTP', icon='rocket_launch', on_click=open_xhttp_deploy).props(
                                'flat size=sm').classes(btn_cyan).style(
                                'background: var(--xf-soft-bg); color: var(--xf-accent); border-color: var(--xf-card-border);')
                            ui.button('一键部署 Hy2', icon='bolt', on_click=open_hy2_deploy).props(
                                'flat size=sm').classes(btn_cyan).style(
                                'background: var(--xf-soft-bg); color: var(--xf-accent); border-color: var(--xf-card-border);')
                            ui.button('一键部署 Snell', icon='security', on_click=open_snell_deploy).props(
                                'flat size=sm').classes(btn_cyan).style(
                                'background: var(--xf-soft-bg); color: var(--xf-accent); border-color: var(--xf-card-border);')

                            if has_manager_access:
                                async def on_add_success():
                                    ui.notify('添加节点成功')
                                    await refresh_after_inbound_change(delay_second_refresh=True)

                                ui.button('新建 XUI 节点', icon='add',
                                          on_click=lambda: open_inbound_dialog(mgr, None, on_add_success,
                                                                               is_3x_ui=server_conf.get('is_3x_ui',
                                                                                                        False))).props(
                                    'flat size=sm').classes(btn_purple).style(
                                    'background: var(--xf-soft-bg); color: #a855f7; border-color: var(--xf-card-border);')
                            else:
                                ui.button('探针只读', icon='visibility', on_click=None).props(
                                    'flat size=sm disabled').classes(
                                    'text-[11px] font-bold tracking-wider rounded-sm px-4 py-1.5 border').style(
                                    'background: var(--xf-soft-bg); color: var(--xf-text-subtle); border-color: var(--xf-card-border); opacity: 0.8;')

                    # Table Header 强锁高度
                    with ui.element('div').classes(
                            'grid w-full gap-4 font-bold pb-2 pt-2 pl-[48px] pr-[46px] text-[11px] tracking-wider flex-shrink-0 z-10 border-b border-[#1e3a5f]/50 text-cyan-600/80 bg-[#030712]' if is_dark else 'grid w-full gap-4 font-bold pb-2 pt-2 pl-[48px] pr-[46px] text-[11px] tracking-wider flex-shrink-0 z-10 border-b border-slate-300/90 text-sky-700/80 bg-[#f8fbff]').style(
                        SINGLE_COLS_NO_PING):
                        ui.label('节点名称').classes('text-left pl-2')
                        ui.label('类型').classes('text-center')
                        ui.label('流量').classes('text-center')
                        ui.label('协议').classes('text-center')
                        ui.label('端口').classes('text-center')
                        ui.label('状态').classes('text-center')
                        ui.label('操作').classes('text-center')

                    # Body 绝对定位处理自身滚动
                    with ui.element('div').classes('w-full flex-1 min-h-0 relative'):
                        with ui.element('div').classes('absolute inset-0 overflow-y-auto px-[16px] py-2 bg-[#030712]' if is_dark else 'absolute inset-0 overflow-y-auto px-[16px] py-2 bg-[#f8fbff]'):
                            await render_node_list()

                if has_manager_access and not NODES_DATA.get(server_conf['url']):
                    ui.timer(0.2, lambda: asyncio.create_task(reload_and_refresh_ui()), once=True)

                # --------------------- 5. 底部空白垫高 (小屏压缩，避免额外挤占节点列表) ---------------------
                ui.element('div').classes('w-full h-[16px] flex-shrink-0')
