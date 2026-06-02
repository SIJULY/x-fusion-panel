import asyncio
import time

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


def get_traffic_limit_bytes(server_conf: dict) -> int:
    limit_gb = _to_float(server_conf.get('traffic_limit_gb', 0), 0)
    return int(limit_gb * 1024 * 1024 * 1024)


def get_traffic_usage_percent(server_conf: dict, probe_data: dict) -> float:
    limit_bytes = get_traffic_limit_bytes(server_conf)
    if limit_bytes <= 0:
        return 0.0
    return min(9999.0, get_traffic_total_bytes(probe_data) * 100.0 / limit_bytes)


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


def build_block_traffic_command(ports: list[int]) -> str:
    if not ports:
        return ''

    unique_ports = sorted({_normalize_port(port) for port in ports if _normalize_port(port) and _normalize_port(port) != 22})
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
        f"- 当前累计流量: `{total_gb:.2f} GB`\n"
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


async def check_and_handle_traffic_limit(server_conf: dict, probe_data: dict) -> None:
    try:
        live_server = _find_live_server_ref(server_conf)
        if not get_traffic_limit_enabled(live_server):
            return
        if live_server.get('traffic_limit_triggered'):
            return

        total_bytes = get_traffic_total_bytes(probe_data)
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