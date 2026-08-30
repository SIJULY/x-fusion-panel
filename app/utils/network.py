import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit

from nicegui import run

from app.core import state


def is_ip_literal(host):
    """判断给定主机是否已经是 IP 字面量 (IPv4 / IPv6)，而不是域名。"""
    h = str(host or '').strip().strip('[]')
    if not h:
        return False
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def extract_host(value):
    """从 url / host:port / 裸主机 中提取纯主机名 (兼容 IPv6 方括号写法)。"""
    raw = str(value or '').strip()
    if not raw:
        return ''
    if '://' not in raw:
        raw = f'//{raw}'
    try:
        return urlsplit(raw).hostname or ''
    except Exception:
        return ''


async def resolve_domain_ip(host, timeout=4.0, use_cache=True):
    """
    域名 -> IPv4 地址（不阻塞事件循环）。
    - 传入的已经是 IP 时原样返回
    - 解析成功写入 DNS_CACHE (供 UI 的非阻塞显示复用)
    - 解析失败返回 None，且不写入缓存，便于下次重试
    """
    h = str(host or '').strip().strip('[]')
    if not h:
        return None
    if is_ip_literal(h):
        return h

    if use_cache:
        cached = state.DNS_CACHE.get(h)
        if cached and cached != 'failed':
            return cached

    try:
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(
            loop.getaddrinfo(h, None, family=socket.AF_INET, type=socket.SOCK_STREAM),
            timeout,
        )
        ip = infos[0][4][0]
        state.DNS_CACHE[h] = ip
        return ip
    except Exception:
        return None


def sync_ping_worker(host, port):
    try:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect((host, int(port)))
        sock.close()
        return int((time.time() - start) * 1000)
    except:
        return -1


def get_dynamic_origin():
    """
    智能侦测当前面板的真实访问地址（适配开源分发）。
    侦测优先级：
    1. 用户在后台手动设置的 `manager_base_url`
    2. Cloudflare / Nginx 传递的真实协议和域名 (X-Forwarded-Proto / Host)
    3. 默认的 Request Host
    """
    saved_url = state.ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
    if saved_url and not ('127.0.0.1' in saved_url or 'localhost' in saved_url):
        if 'sijuly.nyc.mn' not in saved_url:
            return saved_url

    try:
        from nicegui import ui

        req = ui.context.client.request

        real_host = req.headers.get('X-Forwarded-Host') or req.headers.get('host')
        real_proto = req.headers.get('X-Forwarded-Proto') or req.url.scheme

        if real_host:
            detected_url = f"{real_proto}://{real_host}"
            return detected_url
    except Exception:
        pass

    return "http://{YOUR-DOMAIN-OR-IP}"


async def _resolve_dns_bg(host):
    """后台线程池解析 DNS，解析完自动刷新所有绑定的 UI 标签"""
    try:
        ip = await run.io_bound(socket.gethostbyname, host)
        state.DNS_CACHE[host] = ip

        if host in state.DNS_WAITING_LABELS:
            for label in state.DNS_WAITING_LABELS[host]:
                try:
                    if not label.is_deleted:
                        label.set_text(ip)
                except:
                    pass

            del state.DNS_WAITING_LABELS[host]

    except:
        state.DNS_CACHE[host] = "failed"


def get_real_ip_display(url):
    """
    非阻塞获取 IP：
    1. 有缓存 -> 直接返回 IP
    2. 没缓存 -> 先返回域名，同时偷偷启动后台解析任务
    """
    try:
        host = url.split('://')[-1].split(':')[0]

        import re
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host):
            return host

        if host in state.DNS_CACHE:
            val = state.DNS_CACHE[host]
            return val if val != "failed" else host

        asyncio.create_task(_resolve_dns_bg(host))
        return host

    except:
        return url
