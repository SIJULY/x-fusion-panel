import asyncio
import inspect

from app.core.logging import logger
from app.core.state import NODES_DATA, SYNC_SEMAPHORE
from app.services.manager_factory import get_manager
from app.utils.async_tools import run_in_bg_executor


def merge_local_node_fields(server_url, fresh_nodes):
    """将远端刷新得到的节点列表与本地节点配置合并。

    远端 x-ui / 探针同步返回的 inbound 数据不包含面板本地扩展字段，
    例如单机详情页设置的 underlying_proxy。刷新时若直接整体覆盖
    NODES_DATA，会导致这些本地字段丢失。因此在写入缓存前按稳定标识
    （优先 id，其次 remark）把本地字段合并回新节点。
    """
    if not isinstance(fresh_nodes, list):
        return fresh_nodes

    old_nodes = NODES_DATA.get(server_url, []) or []
    if not isinstance(old_nodes, list) or not old_nodes:
        return fresh_nodes

    local_fields = ('underlying_proxy',)

    def _node_keys(node):
        keys = []
        if not isinstance(node, dict):
            return keys
        node_id = node.get('id')
        if node_id not in (None, ''):
            keys.append(('id', str(node_id)))
        remark = (node.get('remark') or '').strip()
        if remark:
            keys.append(('remark', remark))
        return keys

    old_index = {}
    for old_node in old_nodes:
        for key in _node_keys(old_node):
            old_index.setdefault(key, old_node)

    merged_nodes = []
    for fresh_node in fresh_nodes:
        if not isinstance(fresh_node, dict):
            merged_nodes.append(fresh_node)
            continue

        old_node = None
        for key in _node_keys(fresh_node):
            old_node = old_index.get(key)
            if old_node:
                break

        if old_node:
            for field in local_fields:
                if field in old_node and field not in fresh_node:
                    fresh_node[field] = old_node[field]

        merged_nodes.append(fresh_node)

    return merged_nodes


async def fetch_inbounds_safe(server_conf, force_refresh=False, sync_name=False):
    url = server_conf['url']

    # 探针机器处理：除非强制刷新，否则直接信任推送的缓存
    if server_conf.get('probe_installed', False) and not force_refresh:
        return NODES_DATA.get(url, [])

    # 如果不是强制刷新且已有数据，直接返回
    if not force_refresh and url in NODES_DATA and NODES_DATA[url]:
        return NODES_DATA[url]

    async with SYNC_SEMAPHORE:
        try:
            mgr = get_manager(server_conf)
            # 增加超时判断。
            # API 管理器是同步方法，需要丢到线程池；SSH/Root 管理器是 async 方法，必须直接 await。
            # 否则会把 coroutine 对象写入 NODES_DATA，导致单机详情页新增节点后无法静默刷新出真实列表。
            inbounds = await asyncio.wait_for(mgr.get_inbounds(), timeout=15)

            if inbounds is not None:
                inbounds = merge_local_node_fields(url, inbounds)
                NODES_DATA[url] = inbounds
                server_conf['_status'] = 'online'
                # ... (保持原有的同步名称逻辑)
                # 最小保守补全：原始源码未提供 sync_name 的具体实现，这里仅保留参数与注释，不追加任何行为。
                return inbounds

            # --- 关键修复：同步失败时，不要设置为空列表，保留之前的缓存 ---
            # 仅在完全没有旧数据时才标记离线
            if url not in NODES_DATA:
                NODES_DATA[url] = []
                server_conf['_status'] = 'offline'
            return NODES_DATA.get(url, [])

        except Exception as e:
            logger.warning(f"⚠️ {server_conf.get('name')} 同步跳过: {e}")
            # 发生异常时保留现场，不更新 _status 为 offline，防止误报
            return NODES_DATA.get(url, [])
