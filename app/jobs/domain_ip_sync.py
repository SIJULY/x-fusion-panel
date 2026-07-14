import asyncio
import socket
from urllib.parse import urlparse, urlunparse

from app.core.logging import logger
from app.core.state import SERVERS_CACHE
from app.storage.repositories import save_servers

def _resolve_ip(domain):
    try:
        return socket.gethostbyname(domain)
    except Exception:
        return None

async def job_sync_domain_ips():
    """
    1. 检查所有服务器，如果 cf_primary_domain 为空，但 CF 中有该 IP 的解析记录，则自动取第一条作为 cf_primary_domain。
    2. 如果该域名解析的 IP 与当前服务器的 IP 不一致，则自动更新服务器的 IP 并保存。
    """
    updated = False
    from app.services.cloudflare import CloudflareHandler
    cf = CloudflareHandler()

    for srv in SERVERS_CACHE:
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
        if not domain and current_ip:
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
                    srv['url'] = new_url
                    updated = True
            except Exception as e:
                logger.warning(f"Failed to parse or update URL for {srv.get('name')}: {e}")

        ssh_host = srv.get('ssh_host')
        if ssh_host and ssh_host != new_ip and ssh_host != domain:
            logger.info(f"🔄 [域名IP同步] {srv.get('name', 'Unknown')} SSH Host 更新: {ssh_host} -> {new_ip}")
            srv['ssh_host'] = new_ip
            updated = True
            
    if updated:
        await save_servers()