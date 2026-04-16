async def force_geoip_naming_task(server_conf, max_retries=10):
    """
    强制执行 GeoIP 解析，直到成功或达到最大重试次数。
    成功后：
    1. 命名格式：🇺🇸 美国-1, 🇭🇰 香港-2
    2. 分组：自动分入对应国家组
    """
    url = server_conf['url']
    logger.info(f"🌍 [强制修正] 开始处理: {url} (目标: 国旗+国家+序号)")
    
    for i in range(max_retries):
        try:
            # 1. 查询 GeoIP
            geo_info = await run.io_bound(fetch_geo_from_ip, url)
            
            if geo_info:
                # geo_info 格式: (lat, lon, 'United States')
                country_raw = geo_info[2]
                
                # 2. 获取标准化的 "国旗+国家" 字符串，例如 "🇺🇸 美国"
                flag_group = get_flag_for_country(country_raw)
                
                # 3. 计算序号 (查找现有多少个同类服务器)
                # 逻辑：遍历所有服务器，看有多少个名字是以 "🇺🇸 美国" 开头的
                count = 1
                for s in SERVERS_CACHE:
                    # 排除自己 (如果是刚加进去的，可能已经存在于列表中，需要注意去重逻辑，这里简单处理)
                    if s is not server_conf and s.get('name', '').startswith(flag_group):
                        count += 1
                
                # 4. 生成最终名称
                final_name = f"{flag_group}-{count}"
                
                # 5. 应用更改
                old_name = server_conf.get('name', '')
                if old_name != final_name:
                    server_conf['name'] = final_name
                    server_conf['group'] = flag_group # 自动分组
                    server_conf['_detected_region'] = country_raw # 记录原始地区信息
                    
                    # 保存并刷新
                    await save_servers()
                    await refresh_dashboard_ui()
                    try: render_sidebar_content.refresh()
                    except: pass
                    
                    logger.info(f"✅ [强制修正] 成功: {old_name} -> {final_name} (第 {i+1} 次尝试)")
                    return # 成功退出
            
            # 如果没查到，打印日志
            logger.warning(f"⏳ [强制修正] 第 {i+1} 次解析 IP 归属地失败，3秒后重试...")
            
        except Exception as e:
            logger.error(f"❌ [强制修正] 异常: {e}")

        # 等待后重试
        await asyncio.sleep(3)

    logger.warning(f"⚠️ [强制修正] 最终失败: 达到最大重试次数，保持原名 {server_conf.get('name')}")