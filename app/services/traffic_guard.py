import asyncio
import time
from datetime import datetime

from app.api.notifications import send_telegram_message
from app.core.logging import logger
from app.core.state import SERVERS_CACHE
from app.services.ssh import _ssh_exec_wrapper
from app.storage.repositories import save_servers


def _to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def get_traffic_limit_enabled(server_conf: dict) -> bool:
    return bool(server_conf.get('traffic_limit_enabled')) and _to_float(server_conf.get('traffic_limit_gb'), 0) > 0


def get_traffic_total_bytes(probe_data: dict) -> int:
    total_in = int(_to_float(probe_data.get('net_total_in', 0), 0))
    total_out = int(_to_float(probe_data.get('net_total_out', 0), 0))
    return max(0, total_in + total_out)


def get_current_cycle_key(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts or time.time())
    return f'{dt.year:04d}-{dt.month:02d}'


def get_current_cycle_label(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts or time.time())
    return f'{dt.year}年{dt.month}月'


def get_traffic_limit_bytes(server_conf: dict) -> int:
    limit_gb = _to_float(server_conf.get('traffic_limit_gb', 0), 0)
    return int(limit_gb * 1024 * 1024 * 1024)


def get_traffic_cycle_key(server_conf: dict, ts: float | None = None) -> str:
    return str(server_conf.get('traffic_limit_cycle_month') or get_current_cycle_key(ts))


def get_traffic_cycle_label(server_conf: dict, ts: float | None = None) -> str:
    raw_key = get_traffic_cycle_key(server_conf, ts)
    try:
        year, month = raw_key.split('-', 1)
        return f'{int(year)}年{int(month)}月'
    except Exception:
        return get_current_cycle_label(ts)


def get_traffic_cycle_start_bytes(server_conf: dict) -> int:
    return int(_to_float(server_conf.get('traffic_limit_cycle_start_bytes', 0), 0))


def get_traffic_cycle_used_bytes(server_conf: dict, probe_data: dict) -> int:
    total_bytes = get_traffic_total_bytes(probe_data)
    baseline = get_traffic_cycle_start_bytes(server_conf)
    if total_bytes < baseline:
        return total_bytes
    return max(0, total_bytes - baseline)


def ensure_traffic_limit_cycle(server_conf: dict, probe_data: dict | None = None, ts: float | None = None) -> bool:
    current_key = get_current_cycle_key(ts)
    changed = False

    if str(server_conf.get('traffic_limit_cycle_month') or '') != current_key:
        if probe_data:
            baseline = get_traffic_total_bytes(probe_data)
            was_triggered = bool(server_conf.get('traffic_limit_triggered'))
            previous_blocked_ports = list(server_conf.get('traffic_limit_blocked_ports') or [])

            server_conf['traffic_limit_cycle_month'] = current_key
            server_conf['traffic_limit_cycle_start_bytes'] = baseline
            server_conf['traffic_limit_triggered'] = False
            server_conf['traffic_limit_triggered_at'] = None
            server_conf['traffic_limit_last_total_bytes'] = 0
            server_conf['traffic_limit_blocked_ports'] = []
            server_conf['traffic_limit_last_result'] = ''
            server_conf['traffic_limit_notified'] = False
            server_conf['traffic_limit_pending_unblock'] = was_triggered
            server_conf['traffic_limit_pending_unblock_ports'] = previous_blocked_ports if was_triggered else []
            changed = True

    if probe_data:
        total_bytes = get_traffic_total_bytes(probe_data)
        baseline = get_traffic_cycle_start_bytes(server_conf)
        if total_bytes < baseline:
            server_conf['traffic_limit_cycle_start_bytes'] = total_bytes
            changed = True

    return changed


def get_traffic_usage_percent(server_conf: dict, probe_data: dict) -> float:
    limit_bytes = get_traffic_limit_bytes(server_conf)
    if limit_bytes <= 0:
        return 0.0
    used_bytes = get_traffic_cycle_used_bytes(server_conf, probe_data)
    return min(9999.0, used_bytes * 100.0 / limit_bytes)


def _normalize_port(value):
    try:
        port = int(str(value).strip())
        if 1 <= port <= 65535:
            return port
    except Exception:
        pass
    return None


def extract_service_ports(server_conf: dict, probe_data: dict) -> list[int]:
    ports = set()

    def add_port(value):
        port = _normalize_port(value)
        if port and port != 22:
            ports.add(port)

    server_url = str(server_conf.get('url', '') or '').strip()
    if '://' in server_url:
        host_part = server_url.split('://', 1)[1]
        if ':' in host_part:
            try:
                add_port(host_part.rsplit(':', 1)[1].split('/')[0])
            except Exception:
                pass

    for node in (probe_data.get('xui_data') or []):
        if not isinstance(node, dict):
            continue
        add_port(node.get('port'))
        add_port(node.get('listen_port'))
        settings = node.get('settings') or {}
        if isinstance(settings, dict):
            add_port(settings.get('port'))
            for key in ('ports', 'listen_ports'):
                raw_ports = settings.get(key)
                if isinstance(raw_ports, list):
                    for item in raw_ports:
                        add_port(item)
        stream_settings = node.get('streamSettings') or {}
        if isinstance(stream_settings, dict):
            for section_key in ('realitySettings', 'tcpSettings', 'wsSettings', 'httpSettings', 'grpcSettings', 'kcpSettings'):
                section = stream_settings.get(section_key)
                if isinstance(section, dict):
                    add_port(section.get('port'))

    return sorted(ports)


def _normalize_ports(ports: list[int]) -> list[int]:
    return sorted({_normalize_port(port) for port in ports if _normalize_port(port) and _normalize_port(port) != 22})


def build_block_traffic_command(ports: list[int]) -> str:
    if not ports:
        return ''

    unique_ports = _normalize_ports(ports)
    if not unique_ports:
        return ''

    lines = [
        "set -e",
        "if ! command -v iptables >/dev/null 2>&1; then echo 'iptables not found'; exit 1; fi",
        "if command -v ip6tables >/dev/null 2>&1; then HAS_IP6=1; else HAS_IP6=0; fi",
    ]

    for port in unique_ports:
        lines.extend([
            f"iptables -C INPUT -p tcp --dport {port} -j REJECT >/dev/null 2>&1 || iptables -I INPUT -p tcp --dport {port} -j REJECT",
            f"iptables -C INPUT -p udp --dport {port} -j REJECT >/dev/null 2>&1 || iptables -I INPUT -p udp --dport {port} -j REJECT",
            f"if [ \"$HAS_IP6\" = \"1\" ]; then ip6tables -C INPUT -p tcp --dport {port} -j REJECT >/dev/null 2>&1 || ip6tables -I INPUT -p tcp --dport {port} -j REJECT; fi",
            f"if [ \"$HAS_IP6\" = \"1\" ]; then ip6tables -C INPUT -p udp --dport {port} -j REJECT >/dev/null 2>&1 || ip6tables -I INPUT -p udp --dport {port} -j REJECT; fi",
        ])

    lines.append(f"echo 'blocked ports: {', '.join(str(p) for p in unique_ports)}'")
    return "\n".join(lines)


def build_unblock_traffic_command(ports: list[int]) -> str:
    if not ports:
        return ''

    unique_ports = _normalize_ports(ports)
    if not unique_ports:
        return ''

    lines = [
        "set -e",
        "if ! command -v iptables >/dev/null 2>&1; then echo 'iptables not found'; exit 1; fi",
        "if command -v ip6tables >/dev/null 2>&1; then HAS_IP6=1; else HAS_IP6=0; fi",
    ]

    for port in unique_ports:
        lines.extend([
            f"while iptables -C INPUT -p tcp --dport {port} -j REJECT >/dev/null 2>&1; do iptables -D INPUT -p tcp --dport {port} -j REJECT; done",
            f"while iptables -C INPUT -p udp --dport {port} -j REJECT >/dev/null 2>&1; do iptables -D INPUT -p udp --dport {port} -j REJECT; done",
            f"if [ \"$HAS_IP6\" = \"1\" ]; then while ip6tables -C INPUT -p tcp --dport {port} -j REJECT >/dev/null 2>&1; do ip6tables -D INPUT -p tcp --dport {port} -j REJECT; done; fi",
            f"if [ \"$HAS_IP6\" = \"1\" ]; then while ip6tables -C INPUT -p udp --dport {port} -j REJECT >/dev/null 2>&1; do ip6tables -D INPUT -p udp --dport {port} -j REJECT; done; fi",
        ])

    lines.append(f"echo 'unblocked ports: {', '.join(str(p) for p in unique_ports)}'")
    return "\n".join(lines)


def _find_live_server_ref(server_conf: dict) -> dict:
    for server in SERVERS_CACHE:
        if server.get('url') == server_conf.get('url'):
            return server
    return server_conf


async def _send_limit_notification(server_conf: dict, total_bytes: int, limit_bytes: int, blocked_ports: list[int], action_result: str):
    server_name = server_conf.get('name', '未命名服务器')
    server_url = server_conf.get('url', '--')
    total_gb = total_bytes / 1024 / 1024 / 1024
    limit_gb = limit_bytes / 1024 / 1024 / 1024 if limit_bytes > 0 else 0
    ports_text = ', '.join(str(p) for p in blocked_ports) if blocked_ports else '未识别'
    text = (
        "🚨 *VPS 流量超限保护已触发*\n"
        f"- 节点: `{server_name}`\n"
        f"- 地址: `{server_url}`\n"
        f"- 本周期已用: `{total_gb:.2f} GB`\n"
        f"- 阈值: `{limit_gb:.2f} GB`\n"
        f"- 已封禁端口: `{ports_text}`\n"
        f"- 执行结果: `{action_result}`"
    )
    await send_telegram_message(text)


async def execute_traffic_block(server_conf: dict, ports: list[int]) -> tuple[bool, str]:
    command = build_block_traffic_command(ports)
    if not command:
        return False, '未识别到可封禁的业务端口'
    return await asyncio.to_thread(_ssh_exec_wrapper, server_conf, command)


async def execute_traffic_unblock(server_conf: dict, ports: list[int]) -> tuple[bool, str]:
    command = build_unblock_traffic_command(ports)
    if not command:
        return False, '未识别到可解封的业务端口'
    return await asyncio.to_thread(_ssh_exec_wrapper, server_conf, command)


async def reset_traffic_limit_block_state(server_conf: dict, unblock_ports: list[int] | None = None) -> tuple[bool, str]:
    ports = list(unblock_ports or server_conf.get('traffic_limit_blocked_ports') or [])
    ok = True
    result_parts = []

    if ports:
        ok, output = await execute_traffic_unblock(server_conf, ports)
        result_parts.append((output or '').strip() or ('已解除业务端口封禁' if ok else '解除业务端口封禁失败'))
    else:
        result_parts.append('未记录已封禁端口，跳过远程解封')

    server_conf['traffic_limit_triggered'] = False
    server_conf['traffic_limit_triggered_at'] = None
    server_conf['traffic_limit_last_total_bytes'] = 0
    server_conf['traffic_limit_blocked_ports'] = []
    server_conf['traffic_limit_last_result'] = result_parts[-1]
    server_conf['traffic_limit_notified'] = False
    server_conf['traffic_limit_pending_unblock'] = False
    server_conf['traffic_limit_pending_unblock_ports'] = []

    return ok, ' | '.join(part for part in result_parts if part)


async def check_and_handle_traffic_limit(server_conf: dict, probe_data: dict) -> None:
    try:
        live_server = _find_live_server_ref(server_conf)
        if not get_traffic_limit_enabled(live_server):
            return

        cycle_changed = ensure_traffic_limit_cycle(live_server, probe_data)
        if cycle_changed:
            await save_servers()

        if live_server.get('traffic_limit_pending_unblock'):
            pending_ports = list(live_server.get('traffic_limit_pending_unblock_ports') or [])
            ok, output = await reset_traffic_limit_block_state(live_server, pending_ports)
            live_server['traffic_limit_last_result'] = (output or '').strip() or ('新月份开始，已自动恢复业务端口' if ok else '新月份自动恢复业务端口失败')
            await save_servers()
            if ok:
                logger.info(f"🔓 [流量保护] {live_server.get('name')} 已在新月份自动恢复业务端口 | ports={pending_ports}")
            else:
                logger.warning(f"⚠️ [流量保护] {live_server.get('name')} 新月份自动恢复业务端口失败 | ports={pending_ports} result={live_server.get('traffic_limit_last_result')}")

        if live_server.get('traffic_limit_triggered'):
            return

        total_bytes = get_traffic_cycle_used_bytes(live_server, probe_data)
        limit_bytes = get_traffic_limit_bytes(live_server)
        if limit_bytes <= 0 or total_bytes < limit_bytes:
            return

        ports = extract_service_ports(live_server, probe_data)
        ok, output = await execute_traffic_block(live_server, ports)

        live_server['traffic_limit_notified'] = True
        live_server['traffic_limit_triggered'] = True
        live_server['traffic_limit_triggered_at'] = time.time()
        live_server['traffic_limit_last_total_bytes'] = total_bytes
        live_server['traffic_limit_blocked_ports'] = ports
        live_server['traffic_limit_last_result'] = (output or '').strip() or ('已执行自动断流' if ok else '自动断流失败')
        await save_servers()

        result_text = '已自动封禁业务端口' if ok else f'自动断流失败: {live_server["traffic_limit_last_result"]}'
        await _send_limit_notification(live_server, total_bytes, limit_bytes, ports, result_text)
        logger.warning(f"🚨 [流量保护] {live_server.get('name')} 已触发流量上限保护 | ok={ok} ports={ports} result={live_server.get('traffic_limit_last_result')}")
    except Exception as e:
        logger.error(f"❌ [流量保护] 检查或执行失败: {e}")
