import asyncio
import socket
from urllib.parse import urlparse, urlunparse

from app.core.logging import logger
from app.core.state import SERVERS_CACHE, PROBE_DATA_CACHE, NODES_DATA, PING_TREND_CACHE
from app.services.server_ops import normalize_server_host_fields
from app.storage.repositories import save_servers
from app.utils.network import is_ip_literal

def _resolve_ip(domain):
    try:
        return socket.gethostbyname(domain)
    except Exception:
        return None

async def job_sync_domain_ips():
    """
    1. 主机字段归位：域名统一放进 cf_primary_domain，ssh_host 只留解析出的 IP。
    2. cf_primary_domain 仍为空时，用服务器 IP 去 CF 查 A 记录，取第一条回填。
    3. 域名解析出的 IP 与当前记录不一致时，同步更新 url / ssh_host 并落库。
    """
    updated = False
    from app.services.cloudflare import CloudflareHandler
    cf = CloudflareHandler()

    for srv in SERVERS_CACHE:
        # 先把域名/IP 各归各位，这样下面拿到的 current_ip 才是真的 IP
        try:
            if await normalize_server_host_fields(srv, use_cache=False):
                updated = True
        except Exception as e:
            logger.warning(f"Failed to normalize host fields for {srv.get('name')}: {e}")

        current_ip = None
        url_str = srv.get('url', '')
        if url_str:
            try:
                parsed = urlparse(url_str)
                current_ip = parsed.hostname
            except:
                pass
        if not current_ip:
            current_ip = srv.get('ssh_host')

        domain = srv.get('cf_primary_domain')
        if not domain and current_ip and is_ip_literal(current_ip):
            try:
                ok, records = await cf.list_a_records_by_ip(current_ip)
                if ok and records and len(records) > 0:
                    domain = records[0].get('name')
                    if domain:
                        srv['cf_primary_domain'] = domain
                        updated = True
                        logger.info(f"🔄 [域名自动绑定] {srv.get('name', 'Unknown')} 自动绑定主域名: {domain}")
            except Exception as e:
                logger.warning(f"Failed to auto-bind CF domain for {srv.get('name')}: {e}")

        domain = srv.get('cf_primary_domain')
        if not domain:
            continue
        domain = domain.strip()
        if not domain:
            continue

        new_ip = None
        if cf.token:
            ok, ip_or_err = await cf.get_a_record_ip_by_domain(domain)
            if ok and ip_or_err:
                new_ip = ip_or_err

        if not new_ip:
            new_ip = await asyncio.to_thread(_resolve_ip, domain)

        if not new_ip:
            continue

        url_str = srv.get('url', '')
        if url_str:
            try:
                parsed = urlparse(url_str)
                current_host = parsed.hostname
                if current_host and current_host != new_ip and current_host != domain:
                    netloc = new_ip
                    if parsed.port:
                        netloc = f"{netloc}:{parsed.port}"
                    if parsed.username:
                        auth = parsed.username
                        if parsed.password:
                            auth = f"{auth}:{parsed.password}"
                        netloc = f"{auth}@{netloc}"

                    new_url = urlunparse(parsed._replace(netloc=netloc))
                    logger.info(f"🔄 [域名IP同步] {srv.get('name', 'Unknown')} URL 更新: {current_host} -> {new_ip}")

                    if url_str in PROBE_DATA_CACHE:
                        PROBE_DATA_CACHE[new_url] = PROBE_DATA_CACHE.pop(url_str)
                    if url_str in NODES_DATA:
                        NODES_DATA[new_url] = NODES_DATA.pop(url_str)
                    if url_str in PING_TREND_CACHE:
                        PING_TREND_CACHE[new_url] = PING_TREND_CACHE.pop(url_str)

                    srv['url'] = new_url
                    updated = True
            except Exception as e:
                logger.warning(f"Failed to parse or update URL for {srv.get('name')}: {e}")

        # ssh_host 必须是 IP：等于域名时也要改写，否则备份导出里就一直是域名
        ssh_host = srv.get('ssh_host')
        if ssh_host and ssh_host != new_ip:
            logger.info(f"🔄 [域名IP同步] {srv.get('name', 'Unknown')} SSH Host 更新: {ssh_host} -> {new_ip}")
            srv['ssh_host'] = new_ip
            updated = True

    if updated:
        await save_servers()
