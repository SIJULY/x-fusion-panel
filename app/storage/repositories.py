import os
import time
import json
import asyncio
import aiosqlite

from app.core import state
from app.core.config import (
    ADMIN_CONFIG_FILE,
    CONFIG_FILE,
    GLOBAL_SSH_KEY_FILE,
    NODES_CACHE_FILE,
    SUBS_FILE,
    INDEPENDENT_NODES_FILE,
    DATA_DIR
)
from app.core.logging import logger

DB_FILE = os.path.join(DATA_DIR, "xfusion.db")

async def get_db_connection():
    return await aiosqlite.connect(DB_FILE)

async def async_set_db_value(key, value):
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            val_str = json.dumps(value, ensure_ascii=False)
            await db.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
                (key, val_str, val_str)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"❌ 异步保存 {key} 到 SQLite 失败: {e}")

async def load_global_key():
    import aiofiles
    import aiofiles.os
    if await aiofiles.os.path.exists(GLOBAL_SSH_KEY_FILE):
        async with aiofiles.open(GLOBAL_SSH_KEY_FILE, 'r') as f:
            return await f.read()
    return ""


async def save_global_key(content):
    import aiofiles
    async with aiofiles.open(GLOBAL_SSH_KEY_FILE, 'w') as f:
        await f.write(content)


async def save_servers():
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS servers (
                    url TEXT PRIMARY KEY,
                    config TEXT
                )
            ''')
            current_urls = []
            for s in state.SERVERS_CACHE:
                if 'url' in s:
                    current_urls.append(s['url'])
                    val_str = json.dumps(s, ensure_ascii=False)
                    await db.execute(
                        "INSERT INTO servers (url, config) VALUES (?, ?) ON CONFLICT(url) DO UPDATE SET config=?",
                        (s['url'], val_str, val_str)
                    )
            
            # Delete servers that are no longer in memory
            if current_urls:
                placeholders = ','.join(['?'] * len(current_urls))
                await db.execute(f"DELETE FROM servers WHERE url NOT IN ({placeholders})", current_urls)
            else:
                await db.execute("DELETE FROM servers")
                
            await db.commit()
        state.GLOBAL_UI_VERSION = time.time()
    except Exception as e:
        logger.error(f"❌ 批量保存 servers 到关系型表失败: {e}")

async def save_single_server(server_data):
    """单独保存某一台服务器的更新，极大降低批量序列化开销"""
    if 'url' not in server_data:
        return
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS servers (
                    url TEXT PRIMARY KEY,
                    config TEXT
                )
            ''')
            val_str = json.dumps(server_data, ensure_ascii=False)
            await db.execute(
                "INSERT INTO servers (url, config) VALUES (?, ?) ON CONFLICT(url) DO UPDATE SET config=?",
                (server_data['url'], val_str, val_str)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"❌ 保存单个服务器 {server_data.get('url')} 到关系型表失败: {e}")


async def save_admin_config():
    await async_set_db_value("admin_config", state.ADMIN_CONFIG)
    state.GLOBAL_UI_VERSION = time.time()


async def save_subs():
    await async_set_db_value("subs", state.SUBS_CACHE)


async def save_independent_nodes():
    await async_set_db_value("independent_nodes", state.INDEPENDENT_NODES_CACHE)


async def save_nodes_cache():
    try:
        data_snapshot = state.NODES_DATA.copy()
        await async_set_db_value("nodes_cache", data_snapshot)
    except Exception as e:
        logger.error(f"❌ 保存缓存失败: {e}")