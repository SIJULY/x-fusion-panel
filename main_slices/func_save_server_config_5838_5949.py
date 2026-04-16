async def save_server_config(server_data, is_add=True, idx=None):
    # 1. 基础校验
    if not server_data.get('name') or not server_data.get('url'):
        safe_notify("名称和地址不能为空", "negative"); return False

    # 记录旧信息 (用于判断是否移动了分组)
    old_group = None
    if not is_add and idx is not None and 0 <= idx < len(SERVERS_CACHE):
        old_group = SERVERS_CACHE[idx].get('group')

    # 2. 逻辑处理
    if is_add:
        for s in SERVERS_CACHE:
            if s['url'] == server_data['url']: safe_notify(f"已存在！", "warning"); return False
        
        # 自动补全白旗 (如果没国旗的话)
        has_flag = False
        for v in AUTO_COUNTRY_MAP.values():
            if v.split(' ')[0] in server_data['name']: has_flag = True; break
        if not has_flag and '🏳️' not in server_data['name']: server_data['name'] = f"🏳️ {server_data['name']}"

        SERVERS_CACHE.append(server_data)
        safe_notify(f"已添加: {server_data['name']}", "positive")
    else:
        if idx is not None and 0 <= idx < len(SERVERS_CACHE):
            # 直接更新字典，UI 会自动响应（因为有 bind_text_from）
            SERVERS_CACHE[idx].update(server_data)
            safe_notify(f"已更新: {server_data['name']}", "positive")
        else:
            safe_notify("目标不存在", "negative"); return False

    # 3. 保存到硬盘
    await save_servers()

    # ================= ✨✨✨ 左侧侧边栏 UI 零闪烁操作区 ✨✨✨ =================
    # 获取新分组名称
    new_group = server_data.get('group', '默认分组')
    # 计算新分组对应的区域 (用于侧边栏归类)
    if new_group in ['默认分组', '自动注册', '未分组', '自动导入']:
        try: new_group = detect_country_group(server_data.get('name', ''), server_data)
        except: pass
        if not new_group: new_group = '🏳️ 其他地区'

    need_full_refresh = False

    try:
        if is_add:
            # === 新增 ===
            # 如果目标分组已展开，直接插入新行
            if new_group in SIDEBAR_UI_REFS['groups']:
                with SIDEBAR_UI_REFS['groups'][new_group]:
                    render_single_sidebar_row(server_data)
                EXPANDED_GROUPS.add(new_group)
            else:
                need_full_refresh = True # 分组还没渲染过，只能全刷
                
        elif old_group != new_group:
            # === 移动分组 ===
            # 尝试将旧行移动到新分组容器
            row_el = SIDEBAR_UI_REFS['rows'].get(server_data['url'])
            target_col = SIDEBAR_UI_REFS['groups'].get(new_group)
            
            if row_el and target_col:
                row_el.move(target_col)
                EXPANDED_GROUPS.add(new_group)
            else:
                need_full_refresh = True
        
    except Exception as e:
        logger.error(f"UI Move Error: {e}")
        need_full_refresh = True

    if need_full_refresh:
        try: render_sidebar_content.refresh()
        except: pass

    # =================  右侧主视图同步逻辑 =================
    current_scope = CURRENT_VIEW_STATE.get('scope')
    current_data = CURRENT_VIEW_STATE.get('data')
    
    # 情况1: 如果当前正在查看这台服务器的详情页 -> 立即刷新该单页
    if current_scope == 'SINGLE' and (current_data == server_data or (is_add and server_data == SERVERS_CACHE[-1])):
        try: await refresh_content('SINGLE', server_data, force_refresh=True)
        except: pass
        
    # 情况2: 如果当前在列表视图 (全部/分组/区域) -> 立即刷新列表并重置冷却
    elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
        # 强制置空 scope 以绕过 refresh_content 内部的状态判断 (确保 _render_ui 被调用)
        CURRENT_VIEW_STATE['scope'] = None 
        try: 
            # 🟢 [Trigger 2 生效点]：force_refresh=True
            # 这会：
            # 1. 忽略 30分钟 冷却
            # 2. 立即启动后台同步
            # 3. 同步完成后更新 LAST_SYNC_MAP，开启新的 30分钟 倒计时
            await refresh_content(current_scope, current_data, force_refresh=True) 
        except: pass
        
    elif current_scope == 'DASHBOARD':
        try: await refresh_dashboard_ui()
        except: pass

    # =================  后台任务 (GeoIP / 探针安装)  =================
    asyncio.create_task(fast_resolve_single_server(server_data))
    
    if ADMIN_CONFIG.get('probe_enabled', False) and server_data.get('probe_installed', False):
        async def delayed_install():
            await asyncio.sleep(1)
            await install_probe_on_server(server_data)
        asyncio.create_task(delayed_install())
        
    return True