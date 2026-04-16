async def open_server_dialog(idx=None):
    is_edit = idx is not None
    original_data = SERVERS_CACHE[idx] if is_edit else {}
    data = original_data.copy()
    
    # --- 1. 智能检测初始状态 ---
    if is_edit:
        has_xui_conf = bool(data.get('url') and data.get('user') and data.get('pass'))
        raw_ssh_host = data.get('ssh_host')
        if not raw_ssh_host and not has_xui_conf: 
            raw_ssh_host = data.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
        
        has_ssh_conf = bool(
            raw_ssh_host or 
            data.get('ssh_user') or 
            data.get('ssh_key') or 
            data.get('ssh_password') or 
            data.get('probe_installed')
        )
        if not has_ssh_conf and not has_xui_conf: has_ssh_conf = True
    else:
        has_xui_conf = True; has_ssh_conf = True

    state = {'ssh_active': has_ssh_conf, 'xui_active': has_xui_conf}

    with ui.dialog() as d, ui.card().classes('w-full max-w-sm p-5 flex flex-col gap-4'):
        
        # --- 标题栏 ---
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('编辑服务器' if is_edit else '添加服务器').classes('text-lg font-bold')
            tabs = ui.tabs().classes('text-blue-600')
            with tabs:
                t_ssh = ui.tab('SSH / 探针', icon='terminal')
                t_xui = ui.tab('X-UI面板', icon='settings')

        # ================= 独立的基础信息保存逻辑 =================
        async def save_basic_info_only():
            if not is_edit: 
                safe_notify("新增服务器请使用下方的保存按钮", "warning")
                return

            new_name = name_input.value.strip()
            new_group = group_input.value
            
            if not new_name: new_name = await generate_smart_name(data)
            
            SERVERS_CACHE[idx]['name'] = new_name
            SERVERS_CACHE[idx]['group'] = new_group
            
            await save_servers()
            render_sidebar_content.refresh()
            
            # 基础信息修改同步刷新右侧
            current_scope = CURRENT_VIEW_STATE.get('scope')
            if current_scope == 'SINGLE' and CURRENT_VIEW_STATE.get('data') == SERVERS_CACHE[idx]:
                try: await refresh_content('SINGLE', SERVERS_CACHE[idx])
                except: pass
            elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
                # ⚠️ 关键修改：强制重绘
                CURRENT_VIEW_STATE['scope'] = None
                try: await refresh_content(current_scope, CURRENT_VIEW_STATE.get('data'), force_refresh=False)
                except: pass
            
            safe_notify("✅ 基础信息已更新", "positive")
            d.close()
            
        # --- 通用字段区域 ---
        with ui.column().classes('w-full gap-2'):
            name_input = ui.input(value=data.get('name',''), label='备注名称 (留空自动获取)').classes('w-full').props('outlined dense')
            
            with ui.row().classes('w-full items-center gap-2 no-wrap'):
                group_input = ui.select(options=get_all_groups(), value=data.get('group','默认分组'), new_value_mode='add-unique', label='分组').classes('flex-grow').props('outlined dense')
                
                if is_edit:
                    ui.button(icon='save', on_click=save_basic_info_only) \
                        .props('flat dense round color=primary') \
                        .tooltip('仅保存名称和分组 (不重新部署)')

        inputs = {}
        btn_keycap_blue = 'bg-white rounded-lg font-bold tracking-wide border-t border-x border-gray-100 border-b-4 border-blue-100 text-blue-600 px-4 py-1 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1 hover:bg-blue-50'
        btn_keycap_delete = 'bg-white rounded-xl font-bold tracking-wide w-full border-t border-x border-gray-100 border-b-4 border-red-100 text-red-500 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1 hover:bg-red-50'
        btn_keycap_red_confirm = 'rounded-lg font-bold tracking-wide text-white border-b-4 border-red-900 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1'

        # ==================== 保存逻辑 (完整保存) ====================
        async def save_panel_data(panel_type):
            final_name = name_input.value.strip()
            final_group = group_input.value
            new_server_data = data.copy()
            new_server_data['group'] = final_group

            if panel_type == 'ssh':
                if not inputs.get('ssh_host'): return
                s_host = inputs['ssh_host'].value.strip()
                if not s_host: safe_notify("SSH 主机 IP 不能为空", "negative"); return

                new_server_data.update({
                    'ssh_host': s_host,
                    'ssh_port': str(inputs['ssh_port'].value).strip(),
                    'ssh_user': inputs['ssh_user'].value.strip(),
                    'ssh_auth_type': inputs['auth_type'].value,
                    'ssh_password': inputs['ssh_pwd'].value if inputs['ssh_pwd'] else '',
                    'ssh_key': inputs['ssh_key'].value if inputs['ssh_key'] else '',
                    
                    # 🛑 [核心修改点]：只要点击保存SSH，就强制开启探针开关
                    'probe_installed': True 
                })
                
                # 同步更新 UI 上的复选框状态（如果界面上有显示的话）
                if 'probe_chk' in inputs: 
                    inputs['probe_chk'].value = True

                if not new_server_data.get('url'): new_server_data['url'] = f"http://{s_host}:22"

            elif panel_type == 'xui':
                if not inputs.get('xui_url'): return
                x_url_raw = inputs['xui_url'].value.strip()
                x_user = inputs['xui_user'].value.strip()
                x_pass = inputs['xui_pass'].value.strip()
                
                if not (x_url_raw and x_user and x_pass): 
                    safe_notify("必填项不能为空", "negative"); return

                # === ✨ 核心修改：智能 URL 解析逻辑 (支持自动提取路径) ===
                # 1. 补全协议头
                if '://' not in x_url_raw: x_url_raw = f"http://{x_url_raw}"
                
                from urllib.parse import urlparse
                try:
                    parsed = urlparse(x_url_raw)
                    
                    # 2. 处理端口 (如果 netloc 里没有冒号且不是IPv6，说明没端口，补全默认)
                    netloc = parsed.netloc
                    if ':' not in netloc and ']' not in netloc:
                        netloc = f"{netloc}:54321"
                        safe_notify(f"已自动添加默认端口: 54321", "positive")
                    
                    # 3. 重组纯净的 Base URL (Scheme + Host + Port，不带路径)
                    final_base_url = f"{parsed.scheme}://{netloc}"
                    
                    # 4. 智能提取路径作为 Prefix
                    # 逻辑：如果输入栏的 URL 自带了路径(如 /my-path)，则优先使用它作为前缀
                    # 否则使用下方 "API 前缀" 输入框的值
                    path_from_url = parsed.path.strip().strip('/')
                    
                    if path_from_url:
                        final_prefix = f"/{path_from_url}"
                        # 可选：更新 UI 上的前缀输入框显示，给用户反馈
                        if 'xui_prefix' in inputs:
                            inputs['xui_prefix'].value = final_prefix
                        safe_notify(f"已自动识别路径: {final_prefix}", "positive")
                    else:
                        # URL 里没路径，读取手动填写的
                        final_prefix = inputs['xui_prefix'].value.strip()
                        
                except Exception as e:
                    # 解析异常兜底
                    logger.error(f"URL Parse Error: {e}")
                    final_base_url = x_url_raw
                    final_prefix = inputs['xui_prefix'].value.strip()

                probe_val = inputs['probe_chk'].value
                new_server_data.update({
                    'url': final_base_url, 
                    'user': x_user, 
                    'pass': x_pass,
                    'prefix': final_prefix,
                    'probe_installed': probe_val
                })
                
                if probe_val:
                    if not new_server_data.get('ssh_host'):
                        # 从解析后的 URL 中提取 Host
                        try:
                            # 再次解析 final_base_url 提取纯 IP/域名
                            clean_host = urlparse(final_base_url).hostname or final_base_url.split('://')[-1].split(':')[0]
                            new_server_data['ssh_host'] = clean_host
                        except:
                            new_server_data['ssh_host'] = final_base_url.split('://')[-1].split(':')[0]
                    if not new_server_data.get('ssh_port'): new_server_data['ssh_port'] = '22'
                    if not new_server_data.get('ssh_user'): new_server_data['ssh_user'] = 'root'
                    if not new_server_data.get('ssh_auth_type'): new_server_data['ssh_auth_type'] = '全局密钥'

            if not final_name:
                safe_notify("正在生成名称...", "ongoing")
                final_name = await generate_smart_name(new_server_data)
            new_server_data['name'] = final_name

            success = await save_server_config(new_server_data, is_add=not is_edit, idx=idx)
            
            if success:
                data.update(new_server_data)
                if panel_type == 'ssh': state['ssh_active'] = True
                if panel_type == 'xui': state['xui_active'] = True
                
                if panel_type == 'xui' and new_server_data.get('probe_installed'):
                    state['ssh_active'] = True

                # 🛑 [判断逻辑]：这里检查 probe_installed 是否为 True
                # 因为上面我们在保存 SSH 时强制设为了 True，所以这里一定会触发安装
                if new_server_data.get('probe_installed'):
                     safe_notify(f"🚀 配置已保存，正在自动推送探针...", "ongoing")
                     # 立即触发安装任务
                     asyncio.create_task(install_probe_on_server(new_server_data))
                else:
                     safe_notify(f"✅ {panel_type.upper()} 已保存", "positive")

        # ==================== SSH 面板渲染 ====================
        @ui.refreshable
        def render_ssh_panel():
            if not state['ssh_active']:
                with ui.column().classes('w-full h-48 justify-center items-center bg-gray-50 rounded border border-dashed border-gray-300'):
                    ui.icon('terminal', color='grey').classes('text-4xl mb-2')
                    ui.label('SSH 功能未启用').classes('text-gray-500 font-bold mb-2')
                    ui.button('启用 SSH 配置', icon='add', on_click=lambda: _activate_panel('ssh')).props('flat bg-blue-50 text-blue-600')
            else:
                init_host = data.get('ssh_host')
                if not init_host and is_edit:
                     if '://' in data.get('url', ''): init_host = data.get('url', '').split('://')[-1].split(':')[0]
                     else: init_host = data.get('url', '').split(':')[0]

                inputs['ssh_host'] = ui.input(label='SSH 主机 IP', value=init_host).classes('w-full').props('outlined dense')
                
                with ui.column().classes('w-full gap-3'):
                    with ui.row().classes('w-full gap-2'):
                        inputs['ssh_user'] = ui.input(value=data.get('ssh_user','root'), label='SSH 用户').classes('flex-1').props('outlined dense')
                        inputs['ssh_port'] = ui.input(value=data.get('ssh_port','22'), label='端口').classes('w-1/3').props('outlined dense')
                    
                    valid_auth_options = ['全局密钥', '独立密码', '独立密钥']
                    current_auth = data.get('ssh_auth_type', '全局密钥')
                    if current_auth not in valid_auth_options: current_auth = '全局密钥'
                    
                    inputs['auth_type'] = ui.select(valid_auth_options, value=current_auth, label='认证方式').classes('w-full').props('outlined dense options-dense')

                    inputs['ssh_pwd'] = ui.input(label='SSH 密码', password=True, value=data.get('ssh_password','')).classes('w-full').props('outlined dense')
                    inputs['ssh_pwd'].bind_visibility_from(inputs['auth_type'], 'value', value='独立密码')
                    
                    inputs['ssh_key'] = ui.textarea(label='SSH 私钥', value=data.get('ssh_key','')).classes('w-full').props('outlined dense rows=3 input-class=font-mono text-xs')
                    inputs['ssh_key'].bind_visibility_from(inputs['auth_type'], 'value', value='独立密钥')
                
                ui.separator().classes('my-1')
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('✅ 自动使用全局私钥').bind_visibility_from(inputs['auth_type'], 'value', value='全局密钥').classes('text-green-600 text-xs font-bold')
                    ui.element('div').bind_visibility_from(inputs['auth_type'], 'value', value='独立密码') 
                    ui.element('div').bind_visibility_from(inputs['auth_type'], 'value', value='独立密钥') 
                    
                    ui.button('保存 SSH', icon='save', on_click=lambda: save_panel_data('ssh')).props('flat').classes(btn_keycap_blue)

        # ==================== X-UI 面板渲染 ====================
        @ui.refreshable
        def render_xui_panel():
            if not state['xui_active']:
                with ui.column().classes('w-full h-48 justify-center items-center bg-gray-50 rounded border border-dashed border-gray-300'):
                    ui.icon('settings_applications', color='grey').classes('text-4xl mb-2')
                    ui.label('X-UI 面板未配置').classes('text-gray-500 font-bold mb-2')
                    ui.button('配置 X-UI 信息', icon='add', on_click=lambda: _activate_panel('xui')).props('flat bg-purple-50 text-purple-600')
            else:
                inputs['xui_url'] = ui.input(value=data.get('url',''), label='面板 URL (http://ip:port)').classes('w-full').props('outlined dense')
                ui.label('默认端口 54321，如不填写将自动补全').classes('text-[10px] text-gray-400 ml-1 -mt-1 mb-1')
                
                with ui.row().classes('w-full gap-2'):
                    inputs['xui_user'] = ui.input(value=data.get('user',''), label='账号').classes('flex-1').props('outlined dense')
                    inputs['xui_pass'] = ui.input(value=data.get('pass',''), label='密码', password=True).classes('flex-1').props('outlined dense')
                inputs['xui_prefix'] = ui.input(value=data.get('prefix',''), label='面板根路径 (选填)').classes('w-full').props('outlined dense')

                ui.separator().classes('my-1')
                
                with ui.row().classes('w-full justify-between items-center'):
                    inputs['probe_chk'] = ui.checkbox('启用 Root 探针', value=data.get('probe_installed', False))
                    inputs['probe_chk'].classes('text-sm font-bold text-slate-700')
                    
                    ui.button('保存 X-UI', icon='save', on_click=lambda: save_panel_data('xui')).props('flat').classes(btn_keycap_blue)

                ui.label('提示: 启用探针需先配置 SSH 登录信息').classes('text-[10px] text-red-500 ml-8 -mt-2')

                def auto_fill_ssh():
                    if inputs['probe_chk'].value and state['ssh_active'] and inputs.get('ssh_host') and not inputs['ssh_host'].value:
                        p_url = inputs['xui_url'].value
                        if p_url:
                            clean_ip = p_url.split('://')[-1].split(':')[0]
                            if ':' in clean_ip: clean_ip = clean_ip.split(':')[0]
                            inputs['ssh_host'].set_value(clean_ip)
                inputs['probe_chk'].on_value_change(auto_fill_ssh)

        def _activate_panel(panel_type):
            state[f'{panel_type}_active'] = True
            if panel_type == 'ssh': render_ssh_panel.refresh()
            elif panel_type == 'xui': render_xui_panel.refresh()

        default_tab = t_ssh
        if is_edit and not state['ssh_active'] and state['xui_active']: default_tab = t_xui

        with ui.tab_panels(tabs, value=default_tab).classes('w-full animated fadeIn'):
            with ui.tab_panel(t_ssh).classes('p-0 flex flex-col gap-3'):
                render_ssh_panel()
            with ui.tab_panel(t_xui).classes('p-0 flex flex-col gap-3'):
                render_xui_panel()

        # ================= 5. 全局删除逻辑  =================
        if is_edit:
            with ui.row().classes('w-full justify-start mt-4 pt-2 border-t border-gray-100'):
                async def open_delete_confirm():
                    with ui.dialog() as del_d, ui.card().classes('w-80 p-4'):
                        ui.label('删除确认').classes('text-lg font-bold text-red-600')
                        ui.label('请选择要删除的内容：').classes('text-sm text-gray-600 mb-2')
                        
                        real_ssh_exists = bool(data.get('ssh_host') or data.get('ssh_user'))
                        real_xui_exists = bool(data.get('url') and data.get('user') and data.get('pass'))
                        has_probe = data.get('probe_installed', False)

                        if not real_ssh_exists and not real_xui_exists:
                            real_ssh_exists = True; real_xui_exists = True

                        chk_ssh = ui.checkbox('SSH 连接信息', value=real_ssh_exists).classes('text-sm font-bold')
                        chk_xui = ui.checkbox('X-UI 面板信息', value=real_xui_exists).classes('text-sm font-bold')
                        
                        chk_uninstall = ui.checkbox('同时卸载远程探针脚本', value=True).classes('text-sm font-bold text-red-500')
                        chk_uninstall.set_visibility(has_probe)
                        
                        if not real_ssh_exists: chk_ssh.value = False; chk_ssh.disable()
                        if not real_xui_exists: chk_xui.value = False; chk_xui.disable()
                        if real_ssh_exists and not real_xui_exists: chk_ssh.disable()
                        if real_xui_exists and not real_ssh_exists: chk_xui.disable()

                        async def confirm_execution():
                            if idx >= len(SERVERS_CACHE): return
                            target_srv = SERVERS_CACHE[idx]
                            
                            will_delete_ssh = chk_ssh.value
                            will_delete_xui = chk_xui.value
                            will_uninstall = chk_uninstall.value and chk_uninstall.visible
                            
                            remaining_ssh = real_ssh_exists and not will_delete_ssh
                            remaining_xui = real_xui_exists and not will_delete_xui
                            
                            is_full_delete = False

                            # 使用 notification + try/finally 
                            if will_uninstall:
                                # 1. 开启转圈通知
                                loading_notify = ui.notification('正在尝试连接并卸载探针...', timeout=None, spinner=True)
                                
                                try:
                                    uninstall_cmd = "systemctl stop x-fusion-agent && systemctl disable x-fusion-agent && rm -f /etc/systemd/system/x-fusion-agent.service && systemctl daemon-reload && rm -f /root/x_fusion_agent.py"
                                    
                                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(target_srv, uninstall_cmd))
                                    
                                    if success:
                                        ui.notify('✅ 远程探针已卸载清理', type='positive')
                                    else:
                                        ui.notify(f'⚠️ 远程卸载失败 (可能是连接超时)，将仅删除本地记录', type='warning')
                                finally:
                                    # 2. 无论结果如何，必须关闭转圈
                                    loading_notify.dismiss()

                            if not remaining_ssh and not remaining_xui:
                                SERVERS_CACHE.pop(idx)
                                u = target_srv.get('url'); p_u = target_srv.get('ssh_host') or u
                                for k in [u, p_u]:
                                    if k in PROBE_DATA_CACHE: del PROBE_DATA_CACHE[k]
                                    if k in NODES_DATA: del NODES_DATA[k]
                                    if k in PING_TREND_CACHE: del PING_TREND_CACHE[k]
                                safe_notify('✅ 服务器已彻底删除', 'positive')
                                is_full_delete = True
                            else:
                                if will_delete_ssh:
                                    for k in ['ssh_host', 'ssh_port', 'ssh_user', 'ssh_password', 'ssh_key', 'ssh_auth_type']: target_srv[k] = ''
                                    target_srv['probe_installed'] = False
                                    state['ssh_active'] = False
                                    data['ssh_host'] = ''
                                    safe_notify('✅ SSH 信息已清除', 'positive')
                                
                                if will_delete_xui:
                                    for k in ['url', 'user', 'pass', 'prefix']: target_srv[k] = ''
                                    state['xui_active'] = False
                                    data['url'] = '' 
                                    safe_notify('✅ X-UI 信息已清除', 'positive')

                            await save_servers()
                            del_d.close()
                            d.close()
                            
                            render_sidebar_content.refresh()
                            current_scope = CURRENT_VIEW_STATE.get('scope')
                            current_data = CURRENT_VIEW_STATE.get('data')

                            if is_full_delete:
                                if current_scope == 'SINGLE' and current_data == target_srv:
                                    content_container.clear()
                                    with content_container:
                                        ui.label('该服务器已删除').classes('text-gray-400 text-lg w-full text-center mt-20')
                                elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
                                    CURRENT_VIEW_STATE['scope'] = None
                                    await refresh_content(current_scope, current_data, force_refresh=False)
                            else:
                                if current_scope == 'SINGLE' and current_data == target_srv:
                                    await refresh_content('SINGLE', target_srv)

                        with ui.row().classes('w-full justify-end mt-4 gap-2'):
                            ui.button('取消', on_click=del_d.close).props('flat dense color=grey')
                            ui.button('确认执行', color='red', on_click=confirm_execution).props('unelevated').classes(btn_keycap_red_confirm)
                    del_d.open()

                ui.button('删除 / 卸载配置', icon='delete', on_click=open_delete_confirm).props('flat').classes(btn_keycap_delete)
    d.open()