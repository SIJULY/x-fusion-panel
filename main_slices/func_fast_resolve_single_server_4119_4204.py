async def fast_resolve_single_server(s):
    """
    后台全自动修正流程：
    1. 尝试连接面板，读取第一个节点的备注名 (Smart Name)
    2. 尝试查询 IP 归属地，获取国旗 (GeoIP)
    3. 自动组合名字 (防止国旗重复)
    4. 自动归类分组
    """
    await asyncio.sleep(1.5) # 稍微错峰
    
    raw_ip = s['url'].split('://')[-1].split(':')[0]
    logger.info(f"🔍 [智能修正] 正在处理: {raw_ip} ...")
    
    data_changed = False
    
    try:
        # --- 步骤 1: 尝试从面板获取真实备注 ---
        # 只有当名字看起来像默认 IP (或带白旗的IP) 时，才去面板读取
        # 这样防止覆盖用户手动修改过的名字
        current_pure_name = s['name'].replace('🏳️', '').strip()
        
        if current_pure_name == raw_ip:
            try:
                smart_name = await generate_smart_name(s)
                # 如果获取到了有效名字 (不是 IP，也不是默认的 Server-X)
                if smart_name and smart_name != raw_ip and not smart_name.startswith('Server-'):
                    s['name'] = smart_name
                    data_changed = True
                    logger.info(f"🏷️ [获取备注] 成功: {smart_name}")
            except Exception as e:
                logger.warning(f"⚠️ [获取备注] 失败: {e}")

        # --- 步骤 2: 查 IP 归属地并修正国旗/分组 ---
        geo = await run.io_bound(fetch_geo_from_ip, s['url'])
        
        if geo:
            # geo: (lat, lon, "CountryName")
            country_name = geo[2]
            s['lat'] = geo[0]; s['lon'] = geo[1]; s['_detected_region'] = country_name
            
            # 获取正确的国旗
            flag_group = get_flag_for_country(country_name)
            flag_icon = flag_group.split(' ')[0] # 提取 "🇸🇬"
            
            # ✨✨✨ [核心修复] 国旗防重复逻辑 ✨✨✨
            # 1. 先把白旗去掉，拿到干净的名字
            temp_name = s['name'].replace('🏳️', '').strip()
            
            # 2. 检查名字里是否已经包含了正确的国旗 (无论在什么位置)
            if flag_icon in temp_name:
                # 如果包含了 (例如 "微软云|🇸🇬新加坡")，我们只更新去掉白旗后的样子
                # 绝不强行加前缀
                if s['name'] != temp_name:
                    s['name'] = temp_name
                    data_changed = True
            else:
                # 3. 如果完全没包含，才加到最前面
                s['name'] = f"{flag_icon} {temp_name}"
                data_changed = True

            # --- 步骤 3: 强制自动分组 ---
            target_group = flag_group 
            
            # 尝试在配置里找精确匹配
            for k, v in AUTO_COUNTRY_MAP.items():
                if flag_icon in k or flag_icon in v:
                    target_group = v
                    break
            
            if s.get('group') != target_group:
                s['group'] = target_group
                data_changed = True
                
        else:
            logger.warning(f"⚠️ [GeoIP] 未获取到地理位置: {raw_ip}")

        # --- 步骤 4: 保存变更 ---
        if data_changed:
            await save_servers()
            await refresh_dashboard_ui()
            try: render_sidebar_content.refresh()
            except: pass
            logger.info(f"✅ [智能修正] 完毕: {s['name']} -> [{s['group']}]")
            
    except Exception as e:
        logger.error(f"❌ [智能修正] 严重错误: {e}")