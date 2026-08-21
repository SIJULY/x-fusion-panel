from urllib.parse import urlparse

import httpx
from fastapi import Request
from fastapi.responses import Response
from nicegui import run

from app.core.config import AUTO_COUNTRY_MAP
from app.core.logging import logger
from app.core.state import ADMIN_CONFIG, NODES_DATA, SERVERS_CACHE, SUBS_CACHE, INDEPENDENT_NODES_CACHE
from app.utils.encoding import decode_base64_safe, generate_detail_config, generate_node_link, safe_base64


def _node_key(server_url, node):
    return f"{server_url}|{node.get('id')}"


def _prepare_node_for_detail(node, server_url, node_lookup):
    proxy_key = node.get('underlying_proxy')
    if not proxy_key:
        return node

    proxy_item = node_lookup.get(proxy_key)
    if not proxy_item:
        return node

    proxy_node, _ = proxy_item
    node_with_proxy = dict(node)
    node_with_proxy['_underlying_proxy_name'] = str(proxy_node.get('remark', '')).replace(',', '_').replace('=', '_').strip()
    return node_with_proxy


async def sub_handler(token: str):
    sub = next((s for s in SUBS_CACHE if s['token'] == token), None)
    if not sub:
        return Response("Invalid Token", 404)

    links = []

    node_lookup = {}

    for srv in SERVERS_CACHE:
        cf_domain = srv.get('cf_primary_domain')
        if cf_domain:
            host = cf_domain.strip()
        else:
            raw_url = srv['url']
            try:
                if '://' not in raw_url:
                    raw_url = f'http://{raw_url}'
                parsed = urlparse(raw_url)
                host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
            except:
                host = raw_url

        panel_nodes = NODES_DATA.get(srv['url'], []) or []
        for n in panel_nodes:
            key = _node_key(srv['url'], n)
            node_lookup[key] = (n, host)

        custom_nodes = srv.get('custom_nodes', []) or []
        for n in custom_nodes:
            key = _node_key(srv['url'], n)
            node_lookup[key] = (n, host)
            
    for inode in INDEPENDENT_NODES_CACHE:
        key = f"independent|{inode['id']}"
        node_lookup[key] = (inode, "")

    ordered_ids = sub.get('nodes', [])

    for key in ordered_ids:
        if key in node_lookup:
            node, host = node_lookup[key]

            if node.get('_raw_link'):
                links.append(node['_raw_link'])
            else:
                l = generate_node_link(node, host)
                if l:
                    links.append(l)

    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")


async def group_sub_handler(group_b64: str):
    group_name = decode_base64_safe(group_b64)
    if not group_name:
        return Response("Invalid Group Name", 400)

    links = []

    target_servers = [
        s for s in SERVERS_CACHE
        if s.get('group', '默认分组') == group_name or group_name in s.get('tags', [])
    ]

    logger.info(f"正在生成分组订阅: [{group_name}]，匹配到 {len(target_servers)} 个服务器")

    for srv in target_servers:
        panel_nodes = NODES_DATA.get(srv['url'], []) or []
        custom_nodes = srv.get('custom_nodes', []) or []
        all_nodes = panel_nodes + custom_nodes

        if not all_nodes:
            continue

        cf_domain = srv.get('cf_primary_domain')
        if cf_domain:
            host = cf_domain.strip()
        else:
            raw_url = srv['url']
            try:
                if '://' not in raw_url:
                    raw_url = f'http://{raw_url}'
                parsed = urlparse(raw_url)
                host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
            except:
                host = raw_url

        for n in all_nodes:
            if n.get('enable'):
                if n.get('_raw_link'):
                    links.append(n['_raw_link'])
                else:
                    l = generate_node_link(n, host)
                    if l:
                        links.append(l)

    if not links:
        return Response(f"// Group [{group_name}] is empty or not found", media_type="text/plain; charset=utf-8")

    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")


async def short_group_handler(target: str, group_b64: str, request: Request):
    try:
        group_name = decode_base64_safe(group_b64)
        if not group_name:
            return Response("Invalid Group Name", 400)

        if target == 'surge':
            links = []

            target_servers = [
                s for s in SERVERS_CACHE
                if s.get('group', '默认分组') == group_name or group_name in s.get('tags', [])
            ]

            for srv in target_servers:
                panel_nodes = NODES_DATA.get(srv['url'], []) or []
                custom_nodes = srv.get('custom_nodes', []) or []
                all_nodes = panel_nodes + custom_nodes

                cf_domain = srv.get('cf_primary_domain')
                if cf_domain:
                    host = cf_domain.strip()
                else:
                    raw_url = srv['url']
                    try:
                        if '://' not in raw_url:
                            raw_url = f'http://{raw_url}'
                        parsed = urlparse(raw_url)
                        host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
                    except:
                        host = raw_url

                node_lookup = {_node_key(srv['url'], item): (item, host) for item in all_nodes}
                for n in all_nodes:
                    if n.get('enable'):
                        line = generate_detail_config(_prepare_node_for_detail(n, srv['url'], node_lookup), host)
                        if line and not line.startswith('//') and not line.startswith('None'):
                            links.append(line)

            if not links:
                return Response(f"// Group [{group_name}] is empty", media_type="text/plain; charset=utf-8")

            return Response("\n".join(links), media_type="text/plain; charset=utf-8")

        custom_base = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
        if custom_base:
            base_url = custom_base
        else:
            host = request.headers.get('host', 'localhost')
            scheme = request.url.scheme
            base_url = f"{scheme}://{host}"

        internal_api = f"{base_url}/sub/group/{group_b64}"

        params = {
            "target": target,
            "url": internal_api,
            "insert": "false",
            "list": "true",
            "ver": "4",
            "emoji": "false",
            "udp": "true",
            "scv": "true",
        }

        converter_api = "http://subconverter:25500/sub"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(converter_api, params=params)
            
            if response.status_code == 200:
                return Response(content=response.content, media_type="text/plain; charset=utf-8")
            else:
                return Response(f"SubConverter Error (Code: {response.status_code})", status_code=502)
        except Exception as e:
            return Response(f"SubConverter Error: {str(e)}", status_code=502)

    except Exception as e:
        return Response(f"Error: {str(e)}", status_code=500)


async def short_sub_handler(target: str, token: str, request: Request):
    try:
        sub_obj = next((s for s in SUBS_CACHE if s['token'] == token), None)
        if not sub_obj:
            return Response("Subscription Not Found", 404)

        if target == 'surge':
            links = []

            node_lookup = {}
            for srv in SERVERS_CACHE:
                cf_domain = srv.get('cf_primary_domain')
                if cf_domain:
                    host = cf_domain.strip()
                else:
                    raw_url = srv['url']
                    try:
                        if '://' not in raw_url:
                            raw_url = f'http://{raw_url}'
                        parsed = urlparse(raw_url)
                        host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
                    except:
                        host = raw_url

                all_nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
                for n in all_nodes:
                    key = _node_key(srv['url'], n)
                    node_lookup[key] = (n, host)

            for inode in INDEPENDENT_NODES_CACHE:
                key = f"independent|{inode['id']}"
                node_lookup[key] = (inode, "")

            ordered_ids = sub_obj.get('nodes', [])

            for key in ordered_ids:
                if key in node_lookup:
                    node, host = node_lookup[key]
                    
                    if key.startswith('independent|') and node.get('_raw_link'):
                        # Surrogate detail parsing - ideally should use subconverter, but Surge config supports custom formats. 
                        # We skip detail logic for raw independent nodes unless they are standard proxies in Surge format.
                        # Simple fallback to generate_detail_config that attempts parsing raw_link internally if implemented.
                        # In this simplified case, since generate_detail_config might not parse pure raw links optimally,
                        # we let the fallback Subconverter handle the main raw links via API, or append comment if unresolved.
                        pass
                    
                    line = generate_detail_config(_prepare_node_for_detail(node, key.split('|', 1)[0], node_lookup), host)
                    if line and not line.startswith('//') and not line.startswith('None'):
                        links.append(line)

            return Response("\n".join(links), media_type="text/plain; charset=utf-8")

        custom_base = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
        if custom_base:
            base_url = custom_base
        else:
            host = request.headers.get('host', 'localhost')
            scheme = request.url.scheme
            base_url = f"{scheme}://{host}"

        internal_api = f"{base_url}/sub/{token}"
        opt = sub_obj.get('options', {})

        params = {
            "target": target, "url": internal_api,
            "insert": "false", "list": "true", "ver": "4",
            "emoji": "false",
            "udp": str(opt.get('udp', True)).lower(),
            "tfo": str(opt.get('tfo', False)).lower(),
            "scv": str(opt.get('skip_cert', True)).lower(),
            "fdn": "false",
            "sort": "false",
        }

        regions = opt.get('regions', [])
        includes = []
        if opt.get('include_regex'):
            includes.append(opt['include_regex'])
        if regions:
            region_keywords = []
            for r in regions:
                parts = r.split(' ')
                k = parts[1] if len(parts) > 1 else r
                region_keywords.append(k)
                for c, v in AUTO_COUNTRY_MAP.items():
                    if v == r and len(c) == 2:
                        region_keywords.append(c)
            if region_keywords:
                includes.append(f"({'|'.join(region_keywords)})")

        if includes:
            params['include'] = "|".join(includes)
        if opt.get('exclude_regex'):
            params['exclude'] = opt['exclude_regex']

        ren_pat = opt.get('rename_pattern', '')
        if ren_pat:
            params['rename'] = f"{ren_pat}@{opt.get('rename_replacement', '')}"

        converter_api = "http://subconverter:25500/sub"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(converter_api, params=params)
            
            if response.status_code == 200:
                return Response(content=response.content, media_type="text/plain; charset=utf-8")
            else:
                return Response(f"SubConverter Error (Code: {response.status_code})", status_code=502)
        except Exception as e:
            return Response(f"SubConverter Error: {str(e)}", status_code=502)

    except Exception as e:
        return Response(f"Error: {str(e)}", status_code=500)
