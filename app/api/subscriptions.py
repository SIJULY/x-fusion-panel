from urllib.parse import urlparse

import requests
from fastapi import Request
from fastapi.responses import Response
from nicegui import run

from app.core.config import AUTO_COUNTRY_MAP
from app.core.logging import logger
from app.core.state import ADMIN_CONFIG, NODES_DATA, SERVERS_CACHE, SUBS_CACHE
from app.utils.encoding import decode_base64_safe, generate_detail_config, generate_node_link, safe_base64


async def sub_handler(token: str):
    sub = next((s for s in SUBS_CACHE if s['token'] == token), None)
    if not sub:
        return Response("Invalid Token", 404)

    links = []

    node_lookup = {}

    for srv in SERVERS_CACHE:
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
            key = f"{srv['url']}|{n['id']}"
            node_lookup[key] = (n, host)

        custom_nodes = srv.get('custom_nodes', []) or []
        for n in custom_nodes:
            key = f"{srv['url']}|{n['id']}"
            node_lookup[key] = (n, host)

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

                raw_url = srv['url']
                try:
                    if '://' not in raw_url:
                        raw_url = f'http://{raw_url}'
                    parsed = urlparse(raw_url)
                    host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
                except:
                    host = raw_url

                for n in (panel_nodes + custom_nodes):
                    if n.get('enable'):
                        line = generate_detail_config(n, host)
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
            "udp": "true",
            "scv": "true",
        }

        converter_api = "http://subconverter:25500/sub"

        def _fetch_sync():
            try:
                return requests.get(converter_api, params=params, timeout=10)
            except:
                return None

        response = await run.io_bound(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            return Response(f"SubConverter Error (Code: {getattr(response, 'status_code', 'Unk')})", status_code=502)

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
                    key = f"{srv['url']}|{n['id']}"
                    node_lookup[key] = (n, host)

            ordered_ids = sub_obj.get('nodes', [])

            for key in ordered_ids:
                if key in node_lookup:
                    node, host = node_lookup[key]
                    line = generate_detail_config(node, host)
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
            "emoji": str(opt.get('emoji', True)).lower(),
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

        def _fetch_sync():
            try:
                return requests.get(converter_api, params=params, timeout=10)
            except:
                return None

        response = await run.io_bound(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            return Response(f"SubConverter Error (Code: {getattr(response, 'status_code', 'Unk')})", status_code=502)

    except Exception as e:
        return Response(f"Error: {str(e)}", status_code=500)
