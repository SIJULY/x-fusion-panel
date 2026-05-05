import asyncio
import inspect

from app.core.logging import logger
from app.core.state import NODES_DATA, SYNC_SEMAPHORE
from app.services.manager_factory import get_manager
from app.utils.async_tools import run_in_bg_executor


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
            if inspect.iscoroutinefunction(mgr.get_inbounds):
                inbounds = await asyncio.wait_for(mgr.get_inbounds(), timeout=15)
            else:
                inbounds = await asyncio.wait_for(run_in_bg_executor(mgr.get_inbounds), timeout=15)

            if inbounds is not None:
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
