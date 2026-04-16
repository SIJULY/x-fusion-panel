async def open_data_mgmt_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl max-h-[90vh] flex flex-col gap-0 p-0 overflow-hidden bg-[#1e293b] border border-slate-700'):
        
        # Tabs
        with ui.tabs().classes('w-full bg-[#0f172a] flex-shrink-0 border-b border-slate-700 text-slate-400') \
            .props('indicator-color=blue active-color=blue') as tabs:
            tab_export = ui.tab('完整备份 (导出)')
            tab_import = ui.tab('恢复 / 批量添加')
            
        with ui.tab_panels(tabs, value=tab_import).classes('w-full p-6 overflow-y-auto flex-grow bg-[#1e293b] text-slate-200'):
            
            # --- 导出 ---
            with ui.tab_panel(tab_export).classes('flex flex-col gap-8 items-center justify-center h-full'):
                full_backup = {
                    "version": "3.0", "timestamp": __import__('time').time(),
                    "servers": SERVERS_CACHE, "subscriptions": SUBS_CACHE,
                    "admin_config": ADMIN_CONFIG, "global_ssh_key": load_global_key(), "cache": NODES_DATA
                }
                json_str = json.dumps(full_backup, indent=2, ensure_ascii=False)
                
                with ui.column().classes('items-center gap-2'):
                    ui.icon('cloud_download', size='5rem', color='primary').classes('opacity-90')
                    ui.label('备份数据已准备就绪').classes('text-xl font-bold text-slate-200 tracking-wide')
                    ui.label(f'包含 {len(SERVERS_CACHE)} 个服务器配置').classes('text-xs text-slate-500')

                with ui.column().classes('w-full max-w-md gap-4'):
                    ui.button('复制到剪贴板', icon='content_copy', on_click=lambda: safe_copy_to_clipboard(json_str)).classes('w-full h-12 text-base font-bold bg-blue-600 text-white shadow-lg rounded-lg hover:bg-blue-500')
                    ui.button('下载 .json 文件', icon='download', on_click=lambda: ui.download(json_str.encode('utf-8'), 'xui_backup.json')).classes('w-full h-12 text-base font-bold bg-green-600 text-white shadow-lg rounded-lg hover:bg-green-500')

            # --- 导入 ---
            with ui.tab_panel(tab_import).classes('flex flex-col gap-6'):
                # 恢复备份
                with ui.expansion('方式一：恢复 JSON 备份文件', icon='restore', value=False).classes('w-full border border-slate-700 rounded bg-[#172033]').props('header-class="text-slate-300"'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        import_text = ui.textarea(placeholder='粘贴备份 JSON...').classes('w-full h-32 font-mono text-xs').props('outlined dark bg-color="slate-900"')
                        with ui.row().classes('w-full gap-4 items-center'):
                            overwrite_chk = ui.checkbox('覆盖同名服务器', value=False).props('dense dark color=red')
                            restore_key_chk = ui.checkbox('恢复 SSH 密钥', value=True).props('dense dark color=blue')
                            restore_sub_chk = ui.checkbox('恢复订阅设置', value=True).props('dense dark color=blue')
                        
                        async def process_import():
                            try:
                                raw = import_text.value.strip(); data = json.loads(raw)
                                new_servers = data.get('servers', []) if isinstance(data, dict) else data
                                new_subs = data.get('subscriptions', []); new_config = data.get('admin_config', {})
                                new_ssh_key = data.get('global_ssh_key', ''); new_cache = data.get('cache', {})
                                
                                added=0; updated=0
                                existing_map = {s['url']: i for i, s in enumerate(SERVERS_CACHE)}
                                for item in new_servers:
                                    url = item.get('url')
                                    if url in existing_map:
                                        if overwrite_chk.value: SERVERS_CACHE[existing_map[url]] = item; updated+=1
                                    else: SERVERS_CACHE.append(item); existing_map[url]=len(SERVERS_CACHE)-1; added+=1
                                
                                if restore_key_chk.value and data.get('global_ssh_key'): save_global_key(data['global_ssh_key'])
                                if restore_sub_chk.value and isinstance(data, dict):
                                    global SUBS_CACHE, ADMIN_CONFIG
                                    if data.get('subscriptions'): SUBS_CACHE = data['subscriptions']
                                    if data.get('admin_config'): ADMIN_CONFIG.update(data['admin_config'])
                                
                                await save_servers(); await save_subs(); await save_admin_config()
                                render_sidebar_content.refresh()
                                safe_notify(f"恢复: +{added} / ~{updated}", 'positive'); d.close()
                            except Exception as e: safe_notify(f"错误: {e}", 'negative')
                        ui.button('执行恢复', on_click=process_import).classes('w-full bg-slate-700 text-white')

                # 批量添加
                with ui.expansion('方式二：批量添加服务器', icon='playlist_add', value=True).classes('w-full border border-slate-700 rounded bg-[#172033]').props('header-class="text-slate-300"'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        ui.label('批量输入 (每行一个，支持 IP 或 URL)').classes('text-xs font-bold text-slate-500')
                        url_area = ui.textarea(placeholder='192.168.1.10\n...').classes('w-full h-32 font-mono text-sm').props('outlined dark bg-color="slate-900"')
                        
                        # 默认设置
                        with ui.grid().classes('w-full gap-2 grid-cols-2'):
                            def_ssh_user = ui.input('默认 SSH 用户', value=ADMIN_CONFIG.get('pref_ssh_user','root')).props('dense outlined dark')
                            def_ssh_port = ui.input('默认 SSH 端口', value=ADMIN_CONFIG.get('pref_ssh_port','22')).props('dense outlined dark')
                            
                            def_auth = ui.select(['全局密钥', '独立密码'], value='全局密钥', label='认证').classes('col-span-2').props('dense outlined dark options-dense')
                            def_pwd = ui.input('SSH 密码').props('dense outlined dark').classes('col-span-2').bind_visibility_from(def_auth, 'value', value='独立密码')
                            
                            def_xui_port = ui.input('X-UI 端口', value=ADMIN_CONFIG.get('pref_xui_port','54321')).props('dense outlined dark')
                            def_xui_user = ui.input('X-UI 账号', value=ADMIN_CONFIG.get('pref_xui_user','admin')).props('dense outlined dark')
                            def_xui_pass = ui.input('X-UI 密码', value=ADMIN_CONFIG.get('pref_xui_pass','admin')).props('dense outlined dark')

                        # 开关
                        with ui.row().classes('w-full justify-between items-center bg-slate-800 p-2 rounded border border-slate-700'):
                            chk_xui = ui.checkbox('添加 X-UI 面板', value=True).props('dark dense').classes('text-blue-400 font-bold')
                            chk_probe = ui.checkbox('启用 Root 探针', value=False).props('dark dense').classes('text-green-400 font-bold')

                        # 执行
                        async def run_batch_import():
                            # 1. 先保存用户的偏好设置到 ADMIN_CONFIG
                            ADMIN_CONFIG['pref_ssh_user'] = def_ssh_user.value
                            ADMIN_CONFIG['pref_ssh_port'] = def_ssh_port.value
                            ADMIN_CONFIG['pref_xui_port'] = def_xui_port.value
                            ADMIN_CONFIG['pref_xui_user'] = def_xui_user.value
                            ADMIN_CONFIG['pref_xui_pass'] = def_xui_pass.value
                            await save_admin_config() # 立即写入磁盘
                            
                            # 2. 开始处理添加逻辑
                            raw_text = url_area.value.strip()
                            if not raw_text: safe_notify("请输入内容", "warning"); return
                            
                            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
                            count = 0
                            existing_urls = {s['url'] for s in SERVERS_CACHE}
                            post_tasks = []
                            
                            should_add_xui = chk_xui.value
                            should_add_probe = chk_probe.value

                            for line in lines:
                                target_ssh_port = def_ssh_port.value
                                target_xui_port = def_xui_port.value
                                
                                if '://' in line:
                                    final_url = line
                                    try: 
                                        parsed = urlparse(line)
                                        name = parsed.hostname or line
                                    except: name = line
                                else:
                                    if ':' in line and not line.startswith('['): 
                                        parts = line.split(':')
                                        host_ip = parts[0]
                                        target_xui_port = parts[1] 
                                    else: 
                                        host_ip = line
                                        target_xui_port = def_xui_port.value
                                    
                                    final_url = f"http://{host_ip}:{target_xui_port}"
                                    name = host_ip

                                if final_url in existing_urls: continue
                                
                                # 根据开关决定是否填入账号密码
                                final_xui_user = def_xui_user.value if should_add_xui else ""
                                final_xui_pass = def_xui_pass.value if should_add_xui else ""

                                new_server = {
                                    'name': name, 
                                    'group': '', 
                                    'url': final_url,
                                    'user': final_xui_user, 
                                    'pass': final_xui_pass, 
                                    'prefix': '',
                                    'ssh_user': def_ssh_user.value, 
                                    'ssh_port': target_ssh_port,
                                    'ssh_auth_type': def_auth.value,
                                    'ssh_password': def_pwd.value,
                                    'ssh_key': '',
                                    'probe_installed': should_add_probe
                                }

                                SERVERS_CACHE.append(new_server)
                                existing_urls.add(final_url)
                                count += 1
                                
                                post_tasks.append(fast_resolve_single_server(new_server))
                                
                                if ADMIN_CONFIG.get('probe_enabled', False) and should_add_probe:
                                    post_tasks.append(install_probe_on_server(new_server))

                            if count > 0:
                                await save_servers()
                                render_sidebar_content.refresh()
                                safe_notify(f"成功添加 {count} 台服务器", 'positive')
                                d.close()
                                
                                if post_tasks:
                                    safe_notify(f"正在后台处理 {len(post_tasks)} 个初始化任务...", "ongoing")
                                    async def _run_bg_tasks():
                                        await asyncio.gather(*post_tasks, return_exceptions=True)
                                    asyncio.create_task(_run_bg_tasks())
                                    
                            else: safe_notify("未添加任何服务器 (可能已存在)", 'warning')

                        ui.button('确认批量添加', icon='add_box', on_click=run_batch_import).classes('w-full bg-blue-600 text-white h-10')
    d.open()