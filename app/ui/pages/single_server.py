import asyncio
import json

from nicegui import run, ui

from app.core.logging import logger
from app.core.state import ADMIN_CONFIG, NODES_DATA, PROBE_DATA_CACHE, SERVERS_CACHE
from app.services.cloudflare import CloudflareHandler
from app.services.manager_factory import get_manager
from app.services.ssh import _ssh_exec_wrapper, get_ssh_client
from app.services.traffic_guard import (
    ensure_traffic_limit_cycle,
    get_current_cycle_key,
    get_traffic_cycle_label,
    get_traffic_cycle_used_bytes,
    get_traffic_limit_bytes,
    get_traffic_limit_enabled,
    get_traffic_total_bytes,
    get_traffic_usage_percent,
    reset_traffic_limit_block_state,
)
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

    ui.add_head_html('''
    <style>
      .xf-single-server-shell { height: calc(100vh - 80px); }
      .xf-single-server-inner { min-height: 100%; padding-bottom: 1rem; }
      .xf-single-server-cf-card { min-height: 0; }
      .xf-cf-record-list { max-height: 208px; overflow-y: auto; }
      .xf-cf-record-row { height: 44px; margin-bottom: 8px; flex-shrink: 0; }
      .xf-single-server-node-card { min-height: 260px; }
      .xf-single-server-spacer { height: 16px; }
      @media (min-height: 900px) {
        .xf-single-server-node-card { min-height: 210px; }
        .xf-single-server-spacer { height: 40px; }
      }
      .xf-proxy-active {
        background: #10b981 !important;
        color: #ffffff !important;
        border: 1px solid rgba(16, 185, 129, 0.95) !important;
        box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.35), 0 0 14px rgba(16, 185, 129, 0.65) !important;
        opacity: 1 !important;
      }
      .xf-proxy-active .q-icon,
      .xf-proxy-active .q-btn__content,
      .xf-proxy-active i {
        color: #ffffff !important;
        opacity: 1 !important;
      }
    </style>
    ''')

    with content_container:
        # 📌 布局隔离舱：创建一个专属的 wrapper 来接管高度和滚动，防止样式泄露到其他页面
        with ui.element('div').classes('xf-single-server-shell w-full flex flex-col justify-start items-stretch overflow-y-auto'):
            with ui.element('div').classes('xf-single-server-inner w-full max-w-[1440px] mx-auto flex flex-col gap-4 flex-nowrap'):
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

                async def _fetch_runtime_via_ssh():
                    if not server_conf.get('ssh_host'):
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
                        success, raw = await _ssh_exec_wrapper(server_conf, remote_script, timeout=15)
                        if success and raw:
                            parsed = json.loads(raw.splitlines()[-1])
                            if isinstance(parsed, dict) and not parsed.get('error'):
                                return parsed
                    except Exception as e:
                        logger.warning(f'初始获取静态信息失败: {e}')
                    return None

                async def run_ssh_fallback():
                    remote_data = await _fetch_runtime_via_ssh()
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
                    try:
                        import time as _time
                        probe_cache = PROBE_DATA_CACHE.get(server_conf['url'], {}) or {}
                        if not isinstance(probe_cache, dict):
                            probe_cache = {}
                        static = probe_cache.get('static', {}) or {}
                        if not isinstance(static, dict):
                            static = {}

                        now_ts = _time.time()
                        is_stale = bool(probe_cache and (now_ts - to_float(probe_cache.get('last_updated', 0), 0) > 20))

                        mem_total = to_float(probe_cache.get('mem_total', 0.0))
                        mem_usage_pct = clamp_percent(probe_cache.get('mem_usage', 0.0))
                        mem_used = round(mem_total * mem_usage_pct / 100.0, 2)
                        swap_total = to_float(probe_cache.get('swap_total', 0.0))
                        swap_free = to_float(probe_cache.get('swap_free', 0.0))

                        disk_total = to_float(probe_cache.get('disk_total', 0.0))
                        disk_usage_pct = clamp_percent(probe_cache.get('disk_usage', 0.0))
                        disk_used = round(disk_total * disk_usage_pct / 100.0, 2)

                        cpu_usage_pct = 0.0 if is_stale else clamp_percent(probe_cache.get('cpu_usage', 0.0))
                        cpu_cores = int(to_float(
                            probe_cache.get('cpu_cores') or static.get('cpu_cores') or ssh_fallback_data.get('cpu_cores') or 0,
                            0,
                        ))

                        uptime_val = probe_cache.get('uptime') or ssh_fallback_data.get('uptime') or '--'
                        if is_stale:
                            uptime_val = '⚠️ 已离线'

                        cycle_changed = ensure_traffic_limit_cycle(server_conf, probe_cache if probe_cache else None)
                        if cycle_changed:
                            asyncio.create_task(save_servers())

                        traffic_total_bytes = get_traffic_total_bytes(probe_cache)
                        traffic_cycle_used_bytes = get_traffic_cycle_used_bytes(server_conf, probe_cache) if probe_cache else 0
                        traffic_limit_enabled = get_traffic_limit_enabled(server_conf)
                        traffic_limit_bytes = get_traffic_limit_bytes(server_conf)
                        traffic_usage_pct = get_traffic_usage_percent(server_conf, probe_cache) if probe_cache and not is_stale else 0.0
                        traffic_total_gb = traffic_total_bytes / 1024 / 1024 / 1024
                        traffic_cycle_used_gb = traffic_cycle_used_bytes / 1024 / 1024 / 1024
                        traffic_limit_gb = traffic_limit_bytes / 1024 / 1024 / 1024 if traffic_limit_bytes > 0 else 0.0

                        raw_blocked_ports = server_conf.get('traffic_limit_blocked_ports') or []
                        if not isinstance(raw_blocked_ports, (list, tuple, set)):
                            raw_blocked_ports = [raw_blocked_ports] if raw_blocked_ports else []
                        traffic_blocked_ports = [str(p).strip() for p in raw_blocked_ports if str(p).strip()]

                        return {
                            'os': static.get('os') or ssh_fallback_data.get('os') or '--',
                            'arch': static.get('arch') or ssh_fallback_data.get('arch') or '--',
                            'uptime': str(uptime_val or '--'),
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
                            'disk_device': str(probe_cache.get('disk_device') or '/'),
                            'disk_total_gb': disk_total,
                            'disk_free_gb': max(disk_total - disk_used, 0.0) if disk_total else 0.0,
                            'disk_used_gb': disk_used,
                            'disk_usage_pct': disk_usage_pct,
                            'has_probe': bool(probe_cache),
                            'traffic_limit_enabled': bool(traffic_limit_enabled),
                            'traffic_limit_bytes': max(0, int(traffic_limit_bytes)),
                            'traffic_limit_gb': max(0.0, to_float(traffic_limit_gb, 0.0)),
                            'traffic_total_bytes': max(0, int(traffic_total_bytes)),
                            'traffic_total_gb': max(0.0, to_float(traffic_total_gb, 0.0)),
                            'traffic_cycle_used_bytes': max(0, int(traffic_cycle_used_bytes)),
                            'traffic_cycle_used_gb': max(0.0, to_float(traffic_cycle_used_gb, 0.0)),
                            'traffic_cycle_label': str(get_traffic_cycle_label(server_conf) or '--'),
                            'traffic_usage_pct': clamp_percent(traffic_usage_pct),
                            'traffic_limit_triggered': bool(server_conf.get('traffic_limit_triggered')),
                            'traffic_limit_triggered_at': server_conf.get('traffic_limit_triggered_at'),
                            'traffic_limit_last_result': str(server_conf.get('traffic_limit_last_result', '') or ''),
                            'traffic_blocked_ports_text': ', '.join(traffic_blocked_ports) if traffic_blocked_ports else '—',
                        }
                    except Exception as e:
                        logger.exception(f'构建服务器快照失败: {e}')
                        return {
                            'os': '--',
                            'arch': '--',
                            'uptime': '--',
                            'cpu_cores': 0,
                            'cpu_usage_pct': 0.0,
                            'mem_total_gb': 0.0,
                            'mem_free_gb': 0.0,
                            'mem_used_gb': 0.0,
                            'mem_cache_gb': 0.0,
                            'mem_usage_pct': 0.0,
                            'swap_total_gb': 0.0,
                            'swap_free_gb': 0.0,
                            'swap_used_gb': 0.0,
                            'swap_usage_pct': 0.0,
                            'disk_device': '/',
                            'disk_total_gb': 0.0,
                            'disk_free_gb': 0.0,
                            'disk_used_gb': 0.0,
                            'disk_usage_pct': 0.0,
                            'has_probe': False,
                            'traffic_limit_enabled': False,
                            'traffic_limit_bytes': 0,
                            'traffic_limit_gb': 0.0,
                            'traffic_total_bytes': 0,
                            'traffic_total_gb': 0.0,
                            'traffic_cycle_used_bytes': 0,
                            'traffic_cycle_used_gb': 0.0,
                            'traffic_cycle_label': '--',
                            'traffic_usage_pct': 0.0,
                            'traffic_limit_triggered': False,
                            'traffic_limit_triggered_at': None,
                            'traffic_limit_last_result': '',
                            'traffic_blocked_ports_text': '—',
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

                        zone_success, zone_result = await cf_handler.list_zones()
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
                    ok, result = await cf_handler.list_zones()
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
                                if not server_conf.get('cf_primary_domain'):
                                    full_domain = f"{name_val}.{zone_val}" if name_val != '@' else zone_val
                                    if full_domain.startswith('.'):
                                        full_domain = full_domain[1:]
                                    server_conf['cf_primary_domain'] = full_domain
                                    from app.storage.repositories import save_servers
                                    await save_servers()
                                    
                                safe_notify('Cloudflare A 记录已保存', 'positive')
                                d.close()
                                await load_cloudflare_records()
                                render_node_list.refresh()
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

                traffic_refreshers = {'disk': lambda: None, 'header': lambda: None, 'top_actions': lambda: None}
                traffic_dialog_state = {'open': False, 'opened_at': 0.0}

                def refresh_traffic_related_views():
                    for key in ('disk', 'header', 'top_actions'):
                        try:
                            refresher = traffic_refreshers.get(key)
                            if callable(refresher):
                                refresher()
                        except:
                            pass

                def open_traffic_limit_dialog():
                    try:
                        import time as _time
                        from nicegui import app
                        dialog_is_dark = bool(app.storage.user.get('is_dark', True))
                        snap = get_cached_snapshot()
                        current_enabled = bool(server_conf.get('traffic_limit_enabled', False))
                        current_limit = to_float(server_conf.get('traffic_limit_gb', 0), 0.0)

                        traffic_dialog_state['open'] = True
                        traffic_dialog_state['opened_at'] = _time.time()
                        logger.warning(
                            f"[流量控制诊断] 打开弹窗 server={server_conf.get('name', '--')} url={server_conf.get('url', '--')} "
                            f"enabled={current_enabled} limit_gb={current_limit} cycle={snap.get('traffic_cycle_label', '--')} "
                            f"used_gb={snap.get('traffic_cycle_used_gb', 0.0):.2f}"
                        )

                        def _mark_dialog_closed(reason='unknown'):
                            if traffic_dialog_state.get('open'):
                                opened_at = to_float(traffic_dialog_state.get('opened_at', 0), 0)
                                alive_for = max(0.0, _time.time() - opened_at) if opened_at else 0.0
                                logger.warning(
                                    f"[流量控制诊断] 弹窗关闭 reason={reason} server={server_conf.get('name', '--')} "
                                    f"url={server_conf.get('url', '--')} alive_for={alive_for:.2f}s"
                                )
                            traffic_dialog_state['open'] = False
                            traffic_dialog_state['opened_at'] = 0.0

                        with ui.dialog() as d, ui.card().classes(
                                'w-[560px] max-w-[94vw] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-amber-700/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if dialog_is_dark else 'w-[560px] max-w-[94vw] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-amber-300 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'):
                            with ui.column().classes(
                                    'w-full bg-gradient-to-r from-[#1a1206] to-[#0c0a08] p-5 gap-2 border-b border-amber-700/45 relative overflow-hidden' if dialog_is_dark else 'w-full bg-gradient-to-r from-amber-50 to-orange-50 p-5 gap-2 border-b border-amber-200 relative overflow-hidden'):
                                with ui.row().classes('items-center gap-3 z-10'):
                                    with ui.element('div').classes(
                                            'w-9 h-9 rounded-sm flex items-center justify-center bg-[#120d07] border border-amber-700/45 shadow-[0_0_8px_rgba(0,0,0,0.7)] text-amber-300 relative overflow-hidden' if dialog_is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-amber-100 border border-amber-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] text-amber-700 relative overflow-hidden'):
                                        ui.icon('shield').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                                    with ui.column().classes('gap-0'):
                                        ui.label('流量控制').classes(
                                            'text-lg font-black text-slate-100 tracking-wide' if dialog_is_dark else 'text-lg font-black text-slate-800 tracking-wide')
                                        ui.label('自然月周期阈值设置 / 手动开启或关闭').classes(
                                            'text-[10px] tracking-wide text-slate-400' if dialog_is_dark else 'text-[10px] tracking-wide text-slate-500')

                            with ui.column().classes(
                                    'w-full p-5 gap-4 bg-[#030712]' if dialog_is_dark else 'w-full p-5 gap-4 bg-[#f8fbff]'):
                                with ui.row().classes('w-full items-center justify-between gap-3 rounded-sm border px-4 py-3').style(
                                        'background: var(--xf-soft-bg); border-color: var(--xf-card-border);'):
                                    with ui.column().classes('gap-1'):
                                        ui.label('当前周期').classes('text-[11px] font-bold tracking-wide').style(
                                            'color: var(--xf-text-muted);')
                                        ui.label(str(snap.get('traffic_cycle_label', '--'))).classes(
                                            'text-sm font-black tracking-wide').style('color: var(--xf-text-strong);')
                                    with ui.column().classes('gap-1 items-end'):
                                        ui.label('本周期已用').classes('text-[11px] font-bold tracking-wide').style(
                                            'color: var(--xf-text-muted);')
                                        ui.label(f"{snap.get('traffic_cycle_used_gb', 0.0):.2f} GB").classes(
                                            'text-sm font-black font-mono tracking-wide').style('color: #38bdf8;')

                                enabled_input = ui.switch('启用流量阈值控制', value=current_enabled)
                                enabled_input.classes('font-bold')
                                enabled_input.props('color=amber')

                                limit_input = ui.input(
                                    label='本自然月阈值 (GB)',
                                    value=str(current_limit if current_limit > 0 else 500),
                                ).classes('w-full').props(
                                    'outlined dense dark color=amber standout bg-color="[#050b14]" input-class=text-slate-100' if dialog_is_dark else 'outlined dense color=orange')
                                limit_input.bind_visibility_from(enabled_input, 'value')

                                ui.label('关闭后仅保留统计，不执行断流；开启后按当前设置的自然月阈值进行监控。').classes(
                                    'text-[11px] leading-relaxed').style('color: var(--xf-text-muted);')

                                with ui.row().classes('w-full items-center justify-between gap-3 rounded-sm border px-4 py-3').style(
                                        'background: var(--xf-soft-bg); border-color: var(--xf-card-border);'):
                                    with ui.column().classes('gap-1'):
                                        ui.label('探针累计流量').classes('text-[11px] font-bold tracking-wide').style(
                                            'color: var(--xf-text-muted);')
                                        ui.label(f"{snap.get('traffic_total_gb', 0.0):.2f} GB").classes(
                                            'text-sm font-black font-mono tracking-wide').style('color: #38bdf8;')
                                    with ui.column().classes('gap-1 items-end'):
                                        ui.label('本周期已计入').classes('text-[11px] font-bold tracking-wide').style(
                                            'color: var(--xf-text-muted);')
                                        ui.label(f"{snap.get('traffic_cycle_used_gb', 0.0):.2f} GB").classes(
                                            'text-sm font-black font-mono tracking-wide').style('color: var(--xf-text-strong);')

                                if snap.get('traffic_limit_enabled'):
                                    traffic_pct = clamp_percent(snap.get('traffic_usage_pct', 0.0))
                                    status_text = '已触发断流' if snap.get('traffic_limit_triggered') else '监控中'
                                    status_color = '#f43f5e' if snap.get('traffic_limit_triggered') else '#f59e0b'
                                    with ui.row().classes('w-full items-center justify-between gap-3 rounded-sm border px-4 py-3').style(
                                            'background: var(--xf-soft-bg); border-color: var(--xf-card-border);'):
                                        with ui.column().classes('gap-1'):
                                            ui.label('当前状态').classes('text-[11px] font-bold tracking-wide').style(
                                                'color: var(--xf-text-muted);')
                                            ui.label(status_text).classes('text-sm font-black tracking-wide').style(
                                                f'color: {status_color};')
                                        with ui.column().classes('gap-1 items-end'):
                                            ui.label('阈值进度').classes('text-[11px] font-bold tracking-wide').style(
                                                'color: var(--xf-text-muted);')
                                            ui.label(
                                                f"{snap.get('traffic_cycle_used_gb', 0.0):.2f} / {snap.get('traffic_limit_gb', 0.0):.2f} GB ({traffic_pct:.0f}%)"
                                            ).classes('text-sm font-black font-mono tracking-wide').style('color: var(--xf-text-strong);')

                            async def save_traffic_limit_settings():
                                raw_value = str(limit_input.value or '').strip()
                                try:
                                    limit_gb = max(0.0, float(raw_value or 0))
                                except Exception:
                                    safe_notify('流量阈值格式错误，请填写数字', 'negative')
                                    return

                                limit_enabled = bool(enabled_input.value)
                                if limit_enabled and limit_gb <= 0:
                                    safe_notify('启用流量控制时，阈值必须大于 0', 'negative')
                                    return

                                old_enabled = bool(server_conf.get('traffic_limit_enabled'))
                                old_limit = to_float(server_conf.get('traffic_limit_gb', 0), 0.0)
                                was_triggered = bool(server_conf.get('traffic_limit_triggered'))
                                current_cycle_used_bytes = int(snap.get('traffic_cycle_used_bytes', 0) or 0)
                                new_limit_bytes = int(limit_gb * 1024 * 1024 * 1024) if limit_enabled else 0

                                should_unblock = False
                                unblock_reason = ''
                                if was_triggered:
                                    if not limit_enabled:
                                        should_unblock = True
                                        unblock_reason = '已关闭流量阈值，自动解除断流'
                                    elif new_limit_bytes > current_cycle_used_bytes:
                                        should_unblock = True
                                        unblock_reason = '新阈值已高于当前本周期已计入流量，自动解除断流'

                                if should_unblock:
                                    ok, unblock_msg = await reset_traffic_limit_block_state(server_conf)
                                    if not ok:
                                        safe_notify(f'解除断流失败：{unblock_msg}', 'negative')
                                        return

                                server_conf['traffic_limit_enabled'] = limit_enabled
                                server_conf['traffic_limit_gb'] = limit_gb

                                if not limit_enabled:
                                    if not should_unblock:
                                        server_conf['traffic_limit_triggered'] = False
                                        server_conf['traffic_limit_triggered_at'] = None
                                        server_conf['traffic_limit_last_total_bytes'] = 0
                                        server_conf['traffic_limit_blocked_ports'] = []
                                        server_conf['traffic_limit_last_result'] = ''
                                        server_conf['traffic_limit_notified'] = False
                                elif not old_enabled:
                                    if not str(server_conf.get('traffic_limit_cycle_month') or '').strip() or server_conf.get('traffic_limit_cycle_start_bytes') == 0:
                                        server_conf['traffic_limit_cycle_month'] = ''
                                    server_conf['traffic_limit_triggered'] = False
                                    server_conf['traffic_limit_triggered_at'] = None
                                    server_conf['traffic_limit_last_total_bytes'] = 0
                                    server_conf['traffic_limit_blocked_ports'] = []
                                    server_conf['traffic_limit_last_result'] = ''
                                    server_conf['traffic_limit_notified'] = False
                                elif was_triggered and should_unblock:
                                    server_conf['traffic_limit_last_result'] = unblock_reason

                                await save_servers()
                                refresh_traffic_related_views()
                                _mark_dialog_closed('save')
                                d.close()
                                if should_unblock:
                                    safe_notify(f'✅ 流量控制已保存，{unblock_reason}', 'positive')
                                else:
                                    safe_notify('✅ 流量控制已保存（按自然月周期生效）', 'positive')

                            with ui.row().classes(
                                    'w-full justify-end p-4 gap-3 border-t border-amber-700/45 bg-gradient-to-r from-[#1a1206] to-[#0c0a08]' if dialog_is_dark else 'w-full justify-end p-4 gap-3 border-t border-amber-200 bg-gradient-to-r from-amber-50 to-orange-50'):
                                ui.button('取消', on_click=lambda: (_mark_dialog_closed('cancel'), d.close())).props('outline color=grey')
                                ui.button('保存流量控制', icon='save', on_click=save_traffic_limit_settings).props('flat').classes(
                                    'bg-amber-950/45 text-amber-300 border border-amber-500/45 hover:bg-amber-900/55 hover:shadow-[0_0_12px_rgba(245,158,11,0.28)] px-5 py-1 rounded-sm font-black tracking-wide transition-all'
                                    if dialog_is_dark else
                                    'bg-amber-100 text-amber-700 border border-amber-300 hover:bg-amber-200 px-5 py-1 rounded-sm font-black tracking-wide transition-all'
                                )
                        d.on('hide', lambda e: _mark_dialog_closed('hide'))
                        d.open()
                    except Exception as e:
                        traffic_dialog_state['open'] = False
                        traffic_dialog_state['opened_at'] = 0.0
                        logger.exception(f'打开流量控制弹窗失败: {e}')
                        safe_notify(f'打开流量控制失败: {e}', 'negative')

                @ui.refreshable
                async def render_node_list():
                    xui_nodes = NODES_DATA.get(server_conf['url'], []) or []
                    if not xui_nodes:
                        fetched_nodes = await fetch_inbounds_safe(server_conf, force_refresh=False)
                        xui_nodes = fetched_nodes or xui_nodes
                    custom_nodes = server_conf.get('custom_nodes', [])
                    all_nodes = xui_nodes + custom_nodes

                    def node_key(node_item, server_url=None):
                        return f"{server_url or server_conf['url']}|{node_item.get('id')}"

                    def build_global_node_lookup():
                        lookup = {}
                        for srv in SERVERS_CACHE:
                            srv_url = srv.get('url')
                            if not srv_url:
                                continue
                            server_name = srv.get('name') or srv_url
                            server_nodes = (NODES_DATA.get(srv_url, []) or []) + (srv.get('custom_nodes', []) or [])
                            for item in server_nodes:
                                key = node_key(item, srv_url)
                                lookup[key] = {
                                    'node': item,
                                    'server_url': srv_url,
                                    'server_name': server_name,
                                }
                        return lookup

                    def resolve_underlying_proxy_name(node_item):
                        proxy_key = node_item.get('underlying_proxy')
                        if not proxy_key:
                            return ''
                        proxy_item = build_global_node_lookup().get(proxy_key)
                        if not proxy_item:
                            return ''
                        proxy_node = proxy_item.get('node') or {}
                        return str(proxy_node.get('remark', '')).replace(',', '_').replace('=', '_').strip()

                    def node_for_detail(node_item):
                        proxy_name = resolve_underlying_proxy_name(node_item)
                        if not proxy_name:
                            return node_item
                        copied_node = dict(node_item)
                        copied_node['_underlying_proxy_name'] = proxy_name
                        return copied_node

                    def underlying_proxy_support_status(node_item):
                        """判断当前节点是否适合作为“被前置代理”的目标节点。

                        前置代理最终只会体现在 Surge 明文配置的 underlying-proxy 参数里，
                        因此只对当前明文生成器明确支持、且客户端链式转发较可靠的协议开放。
                        Hy2/Hysteria2 基于 UDP/QUIC，即使拼出参数也常因客户端或前置节点
                        不支持 UDP relay 而不可用，所以这里直接禁用避免误设。
                        """
                        raw_link = str(node_item.get('_raw_link') or '').strip().lower()
                        protocol = str(node_item.get('protocol') or '').strip().lower()

                        if raw_link.startswith('hy2://') or protocol in ('hysteria2', 'hy2', 'hysteria'):
                            return False, 'Hy2/Hysteria2 基于 UDP/QUIC，前置代理兼容性差，已禁用'

                        if node_item.get('_is_custom'):
                            if raw_link.startswith('snell://'):
                                return True, ''
                            return False, '该自定义节点暂不支持生成带前置代理的 Surge 明文配置'

                        if protocol in ('vmess', 'trojan'):
                            return True, ''

                        return False, f'{protocol.upper() or "当前协议"} 暂不支持设置前置代理'

                    def sync_underlying_proxy_to_cached_node(node_data, proxy_key):
                        target_id = node_data.get('id')
                        if target_id is None:
                            return node_data in custom_nodes

                        for cached_node in NODES_DATA.get(server_conf['url'], []) or []:
                            if cached_node.get('id') == target_id:
                                if proxy_key:
                                    cached_node['underlying_proxy'] = proxy_key
                                else:
                                    cached_node.pop('underlying_proxy', None)
                                if cached_node is not node_data:
                                    if proxy_key:
                                        node_data['underlying_proxy'] = proxy_key
                                    else:
                                        node_data.pop('underlying_proxy', None)
                                return False

                        for custom_node in custom_nodes:
                            if custom_node.get('id') == target_id:
                                if proxy_key:
                                    custom_node['underlying_proxy'] = proxy_key
                                else:
                                    custom_node.pop('underlying_proxy', None)
                                if custom_node is not node_data:
                                    if proxy_key:
                                        node_data['underlying_proxy'] = proxy_key
                                    else:
                                        node_data.pop('underlying_proxy', None)
                                return True

                        if proxy_key:
                            node_data['underlying_proxy'] = proxy_key
                        else:
                            node_data.pop('underlying_proxy', None)
                        return node_data in custom_nodes

                    def open_underlying_proxy_dialog(node_data):
                        from nicegui import app
                        dialog_is_dark = bool(app.storage.user.get('is_dark', True))
                        current_key = node_key(node_data)
                        no_proxy_label = '不使用前置代理'
                        options = [no_proxy_label]
                        option_key_map = {no_proxy_label: ''}
                        current_value = no_proxy_label
                        global_node_lookup = build_global_node_lookup()
                        for key, proxy_item in global_node_lookup.items():
                            if key == current_key:
                                continue
                            item = proxy_item.get('node') or {}
                            server_name = proxy_item.get('server_name') or proxy_item.get('server_url') or '未知服务器'
                            label = f"{item.get('remark', '未命名')} · {str(item.get('protocol', 'unk')).upper()}:{item.get('port', '')}"
                            label = f"{server_name} / {label}"
                            # NiceGUI 旧版本对 select 的 dict options + 空字符串 value 兼容性较差；
                            # 这里使用纯文本 options，并通过映射表保存真实节点 key。
                            while label in option_key_map:
                                label = f"{label} "
                            options.append(label)
                            option_key_map[label] = key
                            if key == node_data.get('underlying_proxy'):
                                current_value = label

                        with ui.dialog() as d, ui.card().classes(
                                'w-[480px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if dialog_is_dark else 'w-[480px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_18px_42px_rgba(148,163,184,0.18)]'):
                            with ui.column().classes(
                                    'w-full bg-gradient-to-r from-[#0a1526] to-[#050a14] p-5 gap-2 border-b border-[#1e3a5f]/60 relative overflow-hidden' if dialog_is_dark else 'w-full bg-gradient-to-r from-[#f8fbff] to-[#eef4ff] p-5 gap-2 border-b border-slate-300/90 relative overflow-hidden'):
                                with ui.row().classes('items-center gap-3 z-10'):
                                    with ui.element('div').classes(
                                            'w-9 h-9 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] shadow-[0_0_8px_rgba(0,0,0,0.7)] text-cyan-400 relative overflow-hidden' if dialog_is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] text-sky-600 relative overflow-hidden'):
                                        ui.icon('account_tree').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                                    with ui.column().classes('gap-0'):
                                        ui.label('设置前置代理').classes(
                                            'text-lg font-black text-slate-100 tracking-wide' if dialog_is_dark else 'text-lg font-black text-slate-800 tracking-wide')
                                        ui.label(f"目标节点：{node_data.get('remark', '未命名')}").classes('text-[10px] text-slate-500 tracking-wide')
                            with ui.column().classes(
                                    'w-full p-5 gap-4 bg-[#030712]' if dialog_is_dark else 'w-full p-5 gap-4 bg-[#f8fbff]'):
                                if len(options) <= 1:
                                    ui.label('项目中没有其它节点可作为前置代理。').classes('text-sm font-bold').style(
                                        'color: var(--xf-text-muted);')
                                proxy_select = ui.select(options, value=current_value, label='前置代理节点').classes('w-full').props(
                                    'dense outlined dark color=cyan' if dialog_is_dark else 'dense outlined color=blue')
                                ui.label('可选择项目中任意服务器的节点。保存后，Surge 明文配置会为该节点追加 underlying-proxy 参数。').classes(
                                    'text-[11px] leading-relaxed').style('color: var(--xf-text-muted);')

                            async def save_proxy():
                                selected = option_key_map.get(proxy_select.value, '')
                                should_save_servers = sync_underlying_proxy_to_cached_node(node_data, selected)
                                if should_save_servers:
                                    await save_servers()
                                else:
                                    await save_nodes_cache()
                                safe_notify('前置代理设置已保存', 'positive')
                                d.close()
                                render_node_list.refresh()

                            with ui.row().classes(
                                    'w-full justify-end p-4 gap-3 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if dialog_is_dark else 'w-full justify-end p-4 gap-3 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eef4ff]'):
                                ui.button('取消', on_click=d.close).props('outline color=grey')
                                ui.button('保存', on_click=save_proxy).props('flat').classes(
                                    'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 px-6 font-black text-xs tracking-wide rounded-sm' if dialog_is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 px-6 font-black text-xs tracking-wide rounded-sm')
                        d.open()

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
                                    cf_domain = server_conf.get('cf_primary_domain')
                                    node_host = cf_domain.strip() if cf_domain else server_conf['url'].split('://')[-1].split(':')[0]
                                    raw_link = n.get('_raw_link', '') or generate_node_link(n, node_host)
                                    if raw_link:
                                        raw_btn = ui.button(icon='link',
                                                            on_click=lambda u=raw_link: safe_copy_to_clipboard(u)).props(
                                            btn_props).classes(
                                            'text-slate-400 transition-all').style('color: var(--xf-text-muted);')
                                        apply_tooltip(raw_btn, '复制原始链接')

                                    async def copy_detail_action(node_item=n):
                                        cf_domain = server_conf.get('cf_primary_domain')
                                        host = cf_domain.strip() if cf_domain else \
                                            server_conf.get('url', '').replace('http://', '').replace('https://', '').split(
                                                ':')[0]
                                        text = generate_detail_config(node_for_detail(node_item), host)
                                        if text and not str(text).startswith('//'):
                                            await safe_copy_to_clipboard(text)
                                        else:
                                            ui.notify(text or '该协议不支持生成明文配置', type='warning')

                                    detail_btn = ui.button(icon='data_object', on_click=copy_detail_action).props(
                                        btn_props).classes(
                                        'text-slate-400 transition-all').style('color: var(--xf-text-muted);')
                                    apply_tooltip(detail_btn, '复制明文配置')

                                    proxy_supported, proxy_disabled_reason = underlying_proxy_support_status(n)
                                    proxy_has_value = bool(n.get('underlying_proxy')) and proxy_supported
                                    proxy_btn_props = (
                                        'unelevated dense size=sm round color=positive text-color=white'
                                        if proxy_has_value else
                                        btn_props
                                        if proxy_supported else
                                        f'{btn_props} disable'
                                    )
                                    proxy_btn_classes = (
                                        'xf-proxy-active transition-all'
                                        if proxy_has_value else
                                        'text-cyan-500 hover:bg-cyan-900/30 hover:text-cyan-300 transition-all'
                                        if proxy_supported else
                                        'text-slate-500 opacity-40 cursor-not-allowed transition-all'
                                    )
                                    proxy_btn_style = (
                                        'background-color: #10b981 !important; color: #ffffff !important; border-color: #10b981 !important; opacity: 1 !important;'
                                        if proxy_has_value else
                                        'color: #06b6d4;'
                                        if proxy_supported else
                                        'color: #64748b; opacity: 0.45;'
                                    )
                                    proxy_btn = ui.button(
                                        icon='account_tree',
                                        on_click=lambda node=n: open_underlying_proxy_dialog(node) if underlying_proxy_support_status(node)[0] else None,
                                    ).props(proxy_btn_props).classes(proxy_btn_classes).style(proxy_btn_style)
                                    proxy_tip = (
                                        f"前置代理：{resolve_underlying_proxy_name(n)}"
                                        if proxy_has_value else
                                        '设置前置代理'
                                        if proxy_supported else
                                        proxy_disabled_reason
                                    )
                                    apply_tooltip(proxy_btn, proxy_tip)

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

                    def preserve_node_proxy_settings(nodes):
                        old_proxy_by_id = {
                            item.get('id'): item.get('underlying_proxy')
                            for item in old_nodes
                            if item.get('id') is not None and item.get('underlying_proxy')
                        }
                        for item in nodes or []:
                            proxy_value = old_proxy_by_id.get(item.get('id'))
                            if proxy_value:
                                item['underlying_proxy'] = proxy_value
                        return nodes

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
                        new_nodes = preserve_node_proxy_settings(new_nodes)
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
                    @ui.refreshable
                    def render_top_actions():
                        with ui.row().classes('items-center justify-end gap-2 z-10 flex-wrap'):
                            import time as _time
                            probe_cache = PROBE_DATA_CACHE.get(server_conf['url'])
                            is_probe_online = bool(probe_cache and (_time.time() - probe_cache.get('last_updated', 0)) <= 20)
                            with ui.row().classes('items-center gap-1.5 px-2 py-1 rounded-sm border').style(
                                    'background: var(--xf-soft-bg); border-color: var(--xf-card-border);'):
                                ui.element('div').classes(
                                    'w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,1)] animate-pulse'
                                    if is_probe_online else
                                    'w-2 h-2 rounded-full bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,1)]'
                                )
                                ui.label('探针实时同步中' if is_probe_online else '探针已断联').classes(
                                    'text-[11px] font-bold tracking-normal text-emerald-400/90'
                                    if is_probe_online else
                                    'text-[11px] font-bold tracking-normal text-rose-500/90'
                                )
                            if server_conf.get('ssh_host'):
                                ui.button('进入 SSH 终端', icon='terminal', on_click=open_ssh_page).props(
                                    'flat size=sm').classes(
                                    'px-4 py-1.5 font-bold text-[11px] rounded-sm transition-all border').style(
                                    'background: var(--xf-soft-bg); border-color: var(--xf-card-border); color: var(--xf-accent);')

                    render_top_actions()
                    traffic_refreshers['top_actions'] = render_top_actions.refresh

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
                        def render_traffic_header_action():
                            snap = get_cached_snapshot()
                            is_enabled = bool(snap.get('traffic_limit_enabled'))
                            button_text = (
                                f"{snap.get('traffic_cycle_used_gb', 0.0):.2f} / {snap.get('traffic_limit_gb', 0.0):.2f} GB"
                                if is_enabled else
                                '流量控制'
                            )
                            button_icon = 'bar_chart' if is_enabled else 'tune'
                            button = ui.button(button_text, icon=button_icon, on_click=open_traffic_limit_dialog).props(
                                'flat size=sm no-caps'
                            ).classes(
                                'px-3 py-1.5 font-bold text-[11px] rounded-sm transition-all border'
                            )
                            button.style(
                                'background: var(--xf-soft-bg); border-color: var(--xf-card-border); color: #f59e0b;'
                                if is_enabled else
                                'background: var(--xf-soft-bg); border-color: var(--xf-card-border); color: var(--xf-accent);'
                            )
                            apply_tooltip(
                                button,
                                f"周期：{snap.get('traffic_cycle_label', '--')} / 已用 {snap.get('traffic_cycle_used_gb', 0.0):.2f} GB"
                                if is_enabled else
                                '点击设置流量阈值与开启/关闭流量控制'
                            )

                        render_traffic_header_action()
                        traffic_refreshers['header'] = render_traffic_header_action.refresh

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

                        # ===== 下方：磁盘信息 + 流量控制卡片 =====
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
                                with ui.column().classes('w-full p-4 gap-5'):
                                    with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-3 gap-5'):
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

                    traffic_refreshers['disk'] = render_disk_card.refresh

                    def safe_refresh():
                        try:
                            if vps_container.is_deleted:
                                return
                            if traffic_dialog_state.get('open'):
                                import time as _time
                                opened_at = to_float(traffic_dialog_state.get('opened_at', 0), 0)
                                alive_for = max(0.0, _time.time() - opened_at) if opened_at else 0.0
                                logger.warning(
                                    f"[流量控制诊断] 检测到弹窗打开中，已暂停自动刷新 server={server_conf.get('name', '--')} "
                                    f"url={server_conf.get('url', '--')} alive_for={alive_for:.2f}s"
                                )
                                return
                            refresh_traffic_related_views()
                            render_sys_dyn.refresh()
                            render_mem_card.refresh()
                            render_disk_card.refresh()
                        except Exception as e:
                            logger.exception(f'单服务器页自动刷新失败: {e}')

                    ui.timer(2.0, safe_refresh)


                # --------------------- 3. Cloudflare 记录区 (动态伸缩：小屏压缩，大屏恢复原设定) ---------------------
                with ui.element('div').classes(
                        f'xf-single-server-cf-card w-full flex-shrink-0 flex flex-col p-0 gap-0 relative z-10 {shell_card_cls}'):
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

                        # 内容区：卡片随内容自适应；记录列表单独限制最多 4 行完整高度，避免出现半行截断
                        with ui.element('div').classes(f'w-full flex-shrink-0 py-3 px-[16px] relative {shell_body_cls}'):
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
                                        with ui.element('div').classes('xf-cf-record-list w-full'):
                                            for rec in records:
                                                row_tech_cls = 'xf-cf-record-row w-full items-center justify-between gap-3 py-0 px-3 group border border-l-[3px] transition-all duration-300 cursor-default rounded-sm relative overflow-hidden'
                                                row_overlay_cls = 'absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none'
                                                row_accent = '#f59e0b'
                                                row_shadow = f'0 0 0 1px color-mix(in srgb, {row_accent} 18%, transparent), 0 0 16px color-mix(in srgb, {row_accent} {38 if is_dark else 16}%, transparent), 0 6px 18px rgba(15,23,42,0.10)'

                                                with ui.row().classes(row_tech_cls).style(
                                                        f'background: var(--xf-soft-bg); border-color: var(--xf-card-border); border-left-color: {row_accent}; box-shadow: {row_shadow};'):
                                                    ui.element('div').classes(row_overlay_cls).style(
                                                        f'background: linear-gradient(to right, color-mix(in srgb, {row_accent} 16%, transparent), transparent);')
                                                    
                                                    domain_name = rec.get('name', '--')
                                                    is_primary = bool(domain_name and domain_name == server_conf.get('cf_primary_domain'))
                                                    
                                                    with ui.row().classes('items-center gap-2 flex-1 min-w-0 pl-2 relative z-10'):
                                                        ui.label(domain_name).classes(
                                                            'font-bold truncate text-left text-[13px] transition-colors').style(
                                                            'color: var(--xf-text-strong);')
                                                        if is_primary:
                                                            ui.badge('主域名', color='amber').props('outline rounded-sm').classes(
                                                                'text-[9px] font-bold tracking-wider px-1 py-0 shadow-[0_0_5px_rgba(245,158,11,0.3)]')
                                                            
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
                                                            if not is_primary:
                                                                async def set_primary(domain):
                                                                    server_conf['cf_primary_domain'] = domain
                                                                    from app.storage.repositories import save_servers
                                                                    await save_servers()
                                                                    from app.ui.common.notifications import safe_notify
                                                                    safe_notify(f"已设置 {domain} 为主域名", "positive")
                                                                    render_cloudflare_dns_card.refresh()
                                                                    render_node_list.refresh()

                                                                primary_btn = ui.button(icon='star',
                                                                                        on_click=lambda d=rec.get('name'): set_primary(d)).props(
                                                                    'flat dense round size=sm')
                                                                primary_btn.style('color: #f59e0b;')
                                                                apply_tooltip(primary_btn, '设为主域名')
                                                            else:
                                                                async def unset_primary(domain):
                                                                    server_conf['cf_primary_domain'] = ""
                                                                    from app.storage.repositories import save_servers
                                                                    await save_servers()
                                                                    from app.ui.common.notifications import safe_notify
                                                                    safe_notify(f"已取消 {domain} 为主域名", "info")
                                                                    render_cloudflare_dns_card.refresh()
                                                                    render_node_list.refresh()
                                                                    
                                                                primary_btn = ui.button(icon='star',
                                                                                        on_click=lambda d=rec.get('name'): unset_primary(d)).props(
                                                                    'flat dense round size=sm')
                                                                primary_btn.style('color: #f43f5e;')
                                                                apply_tooltip(primary_btn, '取消主域名')
                                                                
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


                # --------------------- 4. 节点列表区 (小屏可见保底 + 大屏恢复原布局比例) ---------------------
                # 13 寸等低高度屏幕下，外层允许纵向滚动；大屏高度足够时恢复原来的节点列表最小高度。
                with ui.element('div').classes(
                        f'xf-single-server-node-card w-full flex-1 flex flex-col p-0 relative z-10 {shell_card_cls}'):
                    
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

                # --------------------- 5. 底部空白垫高 (小屏压缩，大屏恢复原设定) ---------------------
                ui.element('div').classes('xf-single-server-spacer w-full flex-shrink-0')
