async def load_subs_view():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'SUBS'
    CURRENT_VIEW_STATE['data'] = None
    
    show_loading(content_container)
    
    # ✨✨✨ 终极域名获取逻辑：三层防线，绝不输出乱码 ✨✨✨
    origin = ""
    
    # [防线1] 优先读取数据库配置
    db_url = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
    if db_url and not ('127.0.0.1' in db_url or 'localhost' in db_url):
        origin = db_url

    # [防线2] 数据库没有？问浏览器拿最真实的地址 (JS)
    if not origin:
        try: 
            origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
        except: 
            pass # JS 超时也不怕，进入防线3

    # [防线3] JS 超时了？直接在 Python 后端扒 Nginx/Cloudflare 的请求头
    if not origin or origin == 'null':
        try:
            req = ui.context.client.request
            real_host = req.headers.get('X-Forwarded-Host') or req.headers.get('host')
            real_proto = req.headers.get('X-Forwarded-Proto') or req.url.scheme
            if real_host:
                origin = f"{real_proto}://{real_host}"
        except:
            pass
            
    # 绝对保底 (如果前三层全军覆没，使用标准 URL 格式防止崩溃)
    if not origin: origin = "http://x-fusion-panel" 

    # 将获取到的正确域名顺手存入数据库，下次秒开
    if origin and "x-fusion-panel" not in origin:
        if ADMIN_CONFIG.get('manager_base_url') != origin:
            ADMIN_CONFIG['manager_base_url'] = origin
            asyncio.create_task(save_admin_config())

    content_container.clear()
    
    # 容器背景：Slate-900 (#0f172a)
    content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='h-full overflow-y-auto p-4 pl-6 justify-start bg-[#0f172a]')
    
    # === 预统计有效节点 ===
    all_active_keys = set()
    for srv in SERVERS_CACHE:
        panel = NODES_DATA.get(srv['url'], []) or []
        custom = srv.get('custom_nodes', []) or []
        for n in (panel + custom):
            key = f"{srv['url']}|{n['id']}"
            all_active_keys.add(key)

    with content_container:
        # 标题栏
        with ui.row().classes('w-full mb-4 justify-between items-center border-b border-slate-700 pb-2'): 
            ui.label('订阅管理').classes('text-2xl font-bold text-slate-200')
            ui.button('新建订阅', icon='add', color='green', on_click=lambda: open_advanced_sub_editor(None)).props('unelevated')
        
        if not SUBS_CACHE:
            with ui.column().classes('w-full h-64 justify-center items-center text-slate-600'): 
                ui.icon('rss_feed', size='4rem'); ui.label('暂无订阅').classes('text-sm')

        for idx, sub in enumerate(SUBS_CACHE):
            # 卡片背景：Slate-800 (#1e293b)
            with ui.card().classes('w-full p-4 mb-3 shadow-lg hover:shadow-xl transition border border-slate-700 border-l-4 border-l-blue-500 rounded-lg bg-[#1e293b]'):
                
                # 顶部信息栏
                with ui.row().classes('justify-between w-full items-start'):
                    with ui.column().classes('gap-1'):
                        with ui.row().classes('items-center gap-2'):
                            ui.label(sub.get('name', '未命名订阅')).classes('font-bold text-lg text-slate-200')
                            ui.badge('普通', color='blue').props('outline size=xs') 
                        
                        # 有效性统计
                        saved_node_ids = set(sub.get('nodes', []))
                        valid_count = len(saved_node_ids.intersection(all_active_keys))
                        total_count = len(saved_node_ids)
                        
                        color_cls = 'text-green-400' if valid_count > 0 else 'text-slate-500'
                        ui.label(f"⚡ 包含节点: {valid_count} (有效) / {total_count} (总计)").classes(f'text-xs font-bold {color_cls} font-mono')
                    
                    # 操作按钮区
                    with ui.row().classes('gap-2'):
                        ui.button('管理订阅', icon='tune', on_click=lambda _, s=sub: open_advanced_sub_editor(s)) \
                            .props('unelevated dense size=sm color=blue-7') \
                            .tooltip('重命名 / 排序 / 筛选节点')
                        
                        async def dl(i=idx): 
                            with ui.dialog() as d, ui.card().classes('bg-[#1e293b] border border-slate-700'):
                                ui.label('确定删除此订阅？').classes('font-bold text-red-500')
                                with ui.row().classes('justify-end w-full mt-4'):
                                    ui.button('取消', on_click=d.close).props('flat color=grey')
                                    async def confirm():
                                        del SUBS_CACHE[i]; await save_subs(); await load_subs_view(); d.close()
                                        safe_notify('已删除', 'positive')
                                    ui.button('删除', color='red', on_click=confirm).props('unelevated')
                            d.open()

                        ui.button(icon='delete', color='red', on_click=dl).props('flat dense size=sm')
                        
                ui.separator().classes('my-3 bg-slate-600 opacity-50')
                
                # 链接显示区
                path = f"/sub/{sub['token']}"
                raw_url = f"{origin}{path}"
                
                # bg-[#0b1121] (纯黑背景) + text-green-400
                with ui.row().classes('w-full items-center gap-2 bg-[#0b1121] p-2.5 rounded-lg justify-between border border-slate-700'):
                    with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                        ui.icon('link').classes('text-blue-500 text-sm')
                        # 链接文字：绿色高亮，字体加粗
                        ui.label(raw_url).classes('text-xs font-mono text-green-400 font-bold truncate select-all')
                    
                    with ui.row().classes('gap-1'):
                        def btn_copy(icon, color, text, func):
                            ui.button(icon=icon, on_click=func).props(f'flat dense round size=xs text-color={color}').tooltip(text).classes('hover:bg-slate-800')

                        btn_copy('content_copy', 'grey-4', '复制原始链接', lambda u=raw_url: safe_copy_to_clipboard(u))
                        
                        surge_short = f"{origin}/get/sub/surge/{sub['token']}"
                        btn_copy('bolt', 'orange', '复制 Surge 订阅', lambda u=surge_short: safe_copy_to_clipboard(u))
                        
                        clash_short = f"{origin}/get/sub/clash/{sub['token']}"
                        btn_copy('cloud_queue', 'green', '复制 Clash 订阅', lambda u=clash_short: safe_copy_to_clipboard(u))