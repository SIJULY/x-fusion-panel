async def job_check_geo_ip():
    logger.info("🌍 [定时任务] 开始全量 IP 归属地检测与名称修正...")
    data_changed = False
    
    # 1.  动态生成所有已知国旗列表 (防止漏判)
    known_flags = []
    for val in AUTO_COUNTRY_MAP.values():
        icon = val.split(' ')[0] # 提取 "🇺🇸", "🇯🇵" 等
        if icon and icon not in known_flags:
            known_flags.append(icon)
    
    for s in SERVERS_CACHE:
        old_name = s.get('name', '')
        new_name = old_name

        # --- 🧹 步骤 A: 强力清洗白旗  ---
        # 如果名字以 "🏳️ " 开头，且后面还有内容，直接把白旗切掉
        if new_name.startswith('🏳️ ') or new_name.startswith('🏳️'):
            # 只有当名字里除了白旗还有别的东西时才删，防止名字被删空
            if len(new_name) > 2:
                new_name = new_name.replace('🏳️', '').strip()
                logger.info(f"🧹 [清洗白旗] {old_name} -> {new_name}")

        # --- 🔍 步骤 B: 正常的 GeoIP 修正逻辑 ---
        # 检查现在的名字里有没有国旗
        has_flag = any(flag in new_name for flag in known_flags)
        
        if not has_flag:
            try:
                # 只有没国旗的时候，才去查 IP
                geo = await run.io_bound(fetch_geo_from_ip, s['url'])
                if geo:
                    s['lat'] = geo[0]; s['lon'] = geo[1]; s['_detected_region'] = geo[2]
                    
                    flag_prefix = get_flag_for_country(geo[2])
                    flag_icon = flag_prefix.split(' ')[0]
                    
                    # 加上正确的国旗
                    if flag_icon and flag_icon not in new_name:
                        new_name = f"{flag_icon} {new_name}"
                        logger.info(f"✨ [自动修正] {old_name} -> {new_name}")
            except: pass
        
        # 如果名字变了，标记需要保存
        if new_name != old_name:
            s['name'] = new_name
            data_changed = True

    if data_changed:
        await save_servers()
        await refresh_dashboard_ui()
        try: render_sidebar_content.refresh()
        except: pass
        safe_notify("✅ 已清理白旗并修正服务器名称", "positive")
    else:
        logger.info("✅ 名称检查完毕，无需修正")