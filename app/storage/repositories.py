import os
import time

from app.core import state
from app.core.config import (
    ADMIN_CONFIG_FILE,
    CONFIG_FILE,
    GLOBAL_SSH_KEY_FILE,
    NODES_CACHE_FILE,
    SUBS_FILE,
)
from app.core.logging import logger
from app.storage.files import safe_save


def load_global_key():
    if os.path.exists(GLOBAL_SSH_KEY_FILE):
        with open(GLOBAL_SSH_KEY_FILE, 'r') as f:
            return f.read()
    return ""


def save_global_key(content):
    with open(GLOBAL_SSH_KEY_FILE, 'w') as f:
        f.write(content)


async def save_servers():
    await safe_save(CONFIG_FILE, state.SERVERS_CACHE)
    state.GLOBAL_UI_VERSION = time.time()


async def save_admin_config():
    await safe_save(ADMIN_CONFIG_FILE, state.ADMIN_CONFIG)
    state.GLOBAL_UI_VERSION = time.time()


async def save_subs():
    await safe_save(SUBS_FILE, state.SUBS_CACHE)


async def save_nodes_cache():
    try:
        data_snapshot = state.NODES_DATA.copy()
        await safe_save(NODES_CACHE_FILE, data_snapshot)
    except Exception as e:
        logger.error(f"❌ 保存缓存失败: {e}")
