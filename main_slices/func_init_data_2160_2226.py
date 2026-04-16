def init_data():
    # 如果强制路径不存在，说明 Docker 挂载失败，必须报错提醒
    if not os.path.exists(DATA_DIR):
        logger.error(f"❌ 严重错误: 找不到数据目录 {DATA_DIR}！请检查 docker-compose volumes 挂载！")
        # 尝试创建以免程序崩溃，但大概率读不到旧数据
        os.makedirs(DATA_DIR)
    
    global SERVERS_CACHE, SUBS_CACHE, NODES_DATA, ADMIN_CONFIG
    
    logger.info(f"正在读取数据... (目标: {DATA_DIR})")
    
    # 1. 加载服务器
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f: 
                raw_data = json.load(f)
                SERVERS_CACHE = [s for s in raw_data if isinstance(s, dict)]
            logger.info(f"✅ 成功加载服务器: {len(SERVERS_CACHE)} 台")
        except Exception as e:
            logger.error(f"❌ 读取 servers.json 失败: {e}")
            SERVERS_CACHE = []
    else:
        logger.warning(f"⚠️ 未找到服务器配置文件: {CONFIG_FILE}")

    # 2. 加载订阅
    if os.path.exists(SUBS_FILE):
        try:
            with open(SUBS_FILE, 'r', encoding='utf-8') as f: SUBS_CACHE = json.load(f)
        except: SUBS_CACHE = []

    # 3. 加载缓存
    if os.path.exists(NODES_CACHE_FILE):
        # 处理之前误生成的文件夹
        if os.path.isdir(NODES_CACHE_FILE):
             try: 
                import shutil
                shutil.rmtree(NODES_CACHE_FILE)
                logger.info("♻️ 已自动删除错误的缓存文件夹")
             except: pass
             NODES_DATA = {}
        else:
            try:
                with open(NODES_CACHE_FILE, 'r', encoding='utf-8') as f: NODES_DATA = json.load(f)
                count = sum([len(v) for v in NODES_DATA.values() if isinstance(v, list)])
                logger.info(f"✅ 加载缓存节点: {count} 个")
            except: NODES_DATA = {}
    else:
        NODES_DATA = {}
        
    # 4. 加载配置
    if os.path.exists(ADMIN_CONFIG_FILE):
        try:
            with open(ADMIN_CONFIG_FILE, 'r', encoding='utf-8') as f: ADMIN_CONFIG = json.load(f)
        except: ADMIN_CONFIG = {}

    # 初始化设置
    if 'probe_enabled' not in ADMIN_CONFIG:
        ADMIN_CONFIG['probe_enabled'] = True
    if 'probe_token' not in ADMIN_CONFIG:
        ADMIN_CONFIG['probe_token'] = uuid.uuid4().hex

    # 保存一次配置确保持久化
    try:
        with open(ADMIN_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(ADMIN_CONFIG, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"❌ 配置保存失败: {e}")