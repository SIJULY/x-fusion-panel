import json
import os
import uuid
import sqlite3

from app.core import state
from app.core.config import ADMIN_CONFIG_FILE, CONFIG_FILE, DATA_DIR, NODES_CACHE_FILE, SUBS_FILE, INDEPENDENT_NODES_FILE
from app.core.logging import logger

DB_FILE = os.path.join(DATA_DIR, "xfusion.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS kv_store (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    # 创建关系型拆分表 - Servers表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            url TEXT PRIMARY KEY,
            config TEXT
        )
    ''')
    conn.commit()
    return conn

def get_db_value(conn, key, default=None):
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM kv_store WHERE key=?", (key,))
    row = cursor.fetchone()
    if row:
        try:
            return json.loads(row[0])
        except:
            return default
    return default

def set_db_value(conn, key, value):
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO kv_store (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
        (key, json.dumps(value, ensure_ascii=False), json.dumps(value, ensure_ascii=False))
    )
    conn.commit()

def init_data():
    if not os.path.exists(DATA_DIR):
        logger.error(f"❌ 严重错误: 找不到数据目录 {DATA_DIR}！请检查 docker-compose volumes 挂载！")
        os.makedirs(DATA_DIR)

    logger.info(f"正在读取数据... (目标: {DATA_DIR}, 数据库: {DB_FILE})")
    
    conn = init_db()

    # 1. 加载服务器
    state.SERVERS_CACHE.clear()
    
    # 尝试从新的 servers 表加载数据
    cursor = conn.cursor()
    cursor.execute("SELECT config FROM servers")
    rows = cursor.fetchall()
    
    if rows:
        for row in rows:
            try:
                state.SERVERS_CACHE.append(json.loads(row[0]))
            except:
                pass
        logger.info(f"✅ 从 SQLite(关系表) 加载服务器: {len(state.SERVERS_CACHE)} 台")
    else:
        # 如果新表为空，检查是否有旧的 KV 数据或旧的 JSON 文件进行平滑迁移
        servers_data = get_db_value(conn, "servers")
        if servers_data is not None:
            state.SERVERS_CACHE.extend([s for s in servers_data if isinstance(s, dict)])
            # 迁移到新表
            for s in state.SERVERS_CACHE:
                if 'url' in s:
                    cursor.execute(
                        "INSERT INTO servers (url, config) VALUES (?, ?) ON CONFLICT(url) DO UPDATE SET config=?",
                        (s['url'], json.dumps(s, ensure_ascii=False), json.dumps(s, ensure_ascii=False))
                    )
            conn.commit()
            logger.info(f"✅ 成功将 {len(state.SERVERS_CACHE)} 台服务器从 KV 迁移至关系型表")
        elif os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    raw_data = json.load(f)
                    state.SERVERS_CACHE.extend([s for s in raw_data if isinstance(s, dict)])
                # 迁移到新表
                for s in state.SERVERS_CACHE:
                    if 'url' in s:
                        cursor.execute(
                            "INSERT INTO servers (url, config) VALUES (?, ?) ON CONFLICT(url) DO UPDATE SET config=?",
                            (s['url'], json.dumps(s, ensure_ascii=False), json.dumps(s, ensure_ascii=False))
                        )
                conn.commit()
                logger.info(f"✅ 成功将服务器从 JSON 迁移到 SQLite 关系型表: {len(state.SERVERS_CACHE)} 台")
            except Exception as e:
                logger.error(f"❌ 读取 servers.json 失败: {e}")
        else:
            logger.warning(f"⚠️ 未找到服务器配置数据")

    # 2. 加载订阅
    state.SUBS_CACHE.clear()
    subs_data = get_db_value(conn, "subs")
    if subs_data is not None:
        if isinstance(subs_data, list):
            state.SUBS_CACHE.extend(subs_data)
            logger.info(f"✅ 从 SQLite 加载订阅: {len(state.SUBS_CACHE)} 个")
    elif os.path.exists(SUBS_FILE):
        try:
            with open(SUBS_FILE, 'r', encoding='utf-8') as f:
                loaded_subs = json.load(f)
                if isinstance(loaded_subs, list):
                    state.SUBS_CACHE.extend(loaded_subs)
            set_db_value(conn, "subs", state.SUBS_CACHE)
            logger.info("✅ 成功迁移订阅到 SQLite")
        except:
            pass

    # 2.5 加载独立节点
    state.INDEPENDENT_NODES_CACHE.clear()
    ind_nodes_data = get_db_value(conn, "independent_nodes")
    if ind_nodes_data is not None:
        if isinstance(ind_nodes_data, list):
            state.INDEPENDENT_NODES_CACHE.extend(ind_nodes_data)
            logger.info(f"✅ 从 SQLite 加载独立节点: {len(state.INDEPENDENT_NODES_CACHE)} 个")
    elif os.path.exists(INDEPENDENT_NODES_FILE):
        try:
            with open(INDEPENDENT_NODES_FILE, 'r', encoding='utf-8') as f:
                loaded_ind_nodes = json.load(f)
                if isinstance(loaded_ind_nodes, list):
                    state.INDEPENDENT_NODES_CACHE.extend(loaded_ind_nodes)
            set_db_value(conn, "independent_nodes", state.INDEPENDENT_NODES_CACHE)
            logger.info("✅ 成功迁移独立节点到 SQLite")
        except:
            pass

    # 3. 加载缓存
    state.NODES_DATA.clear()
    nodes_cache_data = get_db_value(conn, "nodes_cache")
    if nodes_cache_data is not None:
        if isinstance(nodes_cache_data, dict):
            state.NODES_DATA.update(nodes_cache_data)
        count = sum([len(v) for v in state.NODES_DATA.values() if isinstance(v, list)])
        logger.info(f"✅ 从 SQLite 加载节点缓存: {count} 个")
    elif os.path.exists(NODES_CACHE_FILE):
        if os.path.isdir(NODES_CACHE_FILE):
            try:
                import shutil
                shutil.rmtree(NODES_CACHE_FILE)
            except:
                pass
        else:
            try:
                with open(NODES_CACHE_FILE, 'r', encoding='utf-8') as f:
                    loaded_nodes = json.load(f)
                    if isinstance(loaded_nodes, dict):
                        state.NODES_DATA.update(loaded_nodes)
                set_db_value(conn, "nodes_cache", state.NODES_DATA)
                count = sum([len(v) for v in state.NODES_DATA.values() if isinstance(v, list)])
                logger.info(f"✅ 成功迁移缓存节点到 SQLite: {count} 个")
            except:
                pass

    # 4. 加载配置
    state.ADMIN_CONFIG.clear()
    admin_config_data = get_db_value(conn, "admin_config")
    if admin_config_data is not None:
        if isinstance(admin_config_data, dict):
            state.ADMIN_CONFIG.update(admin_config_data)
        logger.info("✅ 从 SQLite 加载系统配置")
    elif os.path.exists(ADMIN_CONFIG_FILE):
        try:
            with open(ADMIN_CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded_admin_config = json.load(f)
                if isinstance(loaded_admin_config, dict):
                    state.ADMIN_CONFIG.update(loaded_admin_config)
            logger.info("✅ 成功迁移系统配置到 SQLite")
        except:
            pass

    # 初始化设置
    if 'probe_enabled' not in state.ADMIN_CONFIG:
        state.ADMIN_CONFIG['probe_enabled'] = True
    if 'probe_token' not in state.ADMIN_CONFIG:
        state.ADMIN_CONFIG['probe_token'] = uuid.uuid4().hex

    # 保存一次配置确保持久化
    try:
        set_db_value(conn, "admin_config", state.ADMIN_CONFIG)
    except Exception as e:
        logger.error(f"❌ 配置保存到 SQLite 失败: {e}")
        
    conn.close()