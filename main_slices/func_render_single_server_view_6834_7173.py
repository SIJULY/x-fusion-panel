async def render_single_server_view(server_conf, force_refresh=False):
    global REFRESH_CURRENT_NODES
    
    # 1. 布局初始化
    if content_container:
        content_container.clear()
        content_container.classes(remove='overflow-y-auto block', add='h-full overflow-hidden flex flex-col p-4')
    
    with content_container:
        has_manager_access = (server_conf.get('url') and server_conf.get('user') and server_conf.get('pass')) or \
                             (server_conf.get('probe_installed') and server_conf.get('ssh_host'))
        mgr = None
        if has_manager_access:
            try: mgr = get_manager(server_conf)
            except: pass

        @ui.refreshable
        async def render_node_list():
            xui_nodes = await fetch_inbounds_safe(server_conf, force_refresh=False)
            if xui_nodes is None: xui_nodes = []
            custom_nodes = server_conf.get('custom_nodes', [])
            all_nodes = xui_nodes + custom_nodes
            
            if not all_nodes:
                with ui.column().classes('w-full py-12 items-center justify-center opacity-50'):
                    msg = "暂无节点 (可直接新建)" if has_manager_access else "暂无数据"
                    ui.icon('inbox', size='4rem').classes('text-slate-600 mb-2')
                    ui.label(msg).classes('text-slate-500 text-sm')
            else:
                for n in all_nodes:
                    is_custom = n.get('_is_custom', False)
                    is_ssh_mode = (not is_custom) and (server_conf.get('probe_installed') and server_conf.get('ssh_host'))
                    
                    # 列表行样式: bg-[#1e293b], border-slate-700
                    row_3d_cls = 'grid w-full gap-4 py-3 px-2 mb-2 items-center group bg-[#1e293b] rounded-xl border border-slate-700 border-b-[3px] shadow-sm transition-all duration-150 ease-out hover:shadow-md hover:border-blue-500 hover:bg-[#252f45] active:border-b active:translate-y-[2px] active:shadow-none cursor-default'
                    
                    with ui.element('div').classes(row_3d_cls).style(SINGLE_COLS_NO_PING):
                        ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-2 text-slate-300 text-sm group-hover:text-white')
                        
                        if is_custom: ui.label("独立").classes('text-[10px] bg-purple-900/50 text-purple-300 font-bold px-2 py-0.5 rounded-full w-fit mx-auto border border-purple-700')
                        elif is_ssh_mode: ui.label("Root").classes('text-[10px] bg-teal-900/50 text-teal-300 font-bold px-2 py-0.5 rounded-full w-fit mx-auto border border-teal-700')
                        else: ui.label("API").classes('text-[10px] bg-slate-700 text-slate-300 font-bold px-2 py-0.5 rounded-full w-fit mx-auto border border-slate-600')
                        
                        traffic = format_bytes(n.get('up', 0) + n.get('down', 0)) if not is_custom else "--"
                        ui.label(traffic).classes('text-xs text-slate-400 w-full text-center font-mono font-bold')
                        
                        proto = str(n.get('protocol', 'unk')).upper()
                        ui.label(proto).classes(f'text-[11px] font-extrabold w-full text-center text-slate-500 tracking-wide')

                        ui.label(str(n.get('port', 0))).classes('text-blue-400 font-mono w-full text-center font-bold text-xs')
                        
                        is_enable = n.get('enable', True)
                        with ui.row().classes('w-full justify-center items-center gap-1'):
                            color = "green" if is_enable else "red"; text = "启用" if is_enable else "停止"
                            ui.element('div').classes(f'w-2 h-2 rounded-full bg-{color}-500 shadow-[0_0_5px_rgba(0,0,0,0.5)]')
                            ui.label(text).classes(f'text-[10px] font-bold text-{color}-400')
                        
                        # 操作按钮 (深色适配)
                        with ui.row().classes('gap-2 justify-center w-full no-wrap opacity-60 group-hover:opacity-100 transition'):
                            btn_props = 'flat dense size=sm round'
                            link = n.get('_raw_link', '') if is_custom else generate_node_link(n, server_conf['url'])
                            if link: ui.button(icon='content_copy', on_click=lambda u=link: safe_copy_to_clipboard(u)).props(btn_props).tooltip('复制链接').classes('text-slate-400 hover:bg-slate-600 hover:text-blue-400')
                            
                            async def copy_detail_action(node_item=n):
                                host = server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
                                text = generate_detail_config(node_item, host)
                                if text: await safe_copy_to_clipboard(text)
                                else: ui.notify('该协议不支持生成明文配置', type='warning')
                            ui.button(icon='description', on_click=copy_detail_action).props(btn_props).tooltip('复制明文配置').classes('text-slate-400 hover:bg-slate-600 hover:text-orange-400')

                            if is_custom:
                                ui.button(icon='edit', on_click=lambda node=n: open_edit_custom_node(node)).props(btn_props).classes('text-blue-400 hover:bg-slate-600')
                                ui.button(icon='delete', on_click=lambda node=n: uninstall_and_delete(node)).props(btn_props).classes('text-red-400 hover:bg-slate-600')
                            elif has_manager_access:
                                async def on_edit_success(): ui.notify('修改成功'); await reload_and_refresh_ui()
                                ui.button(icon='edit', on_click=lambda i=n: open_inbound_dialog(mgr, i, on_edit_success)).props(btn_props).classes('text-blue-400 hover:bg-slate-600')
                                async def on_del_success(): ui.notify('删除成功'); await reload_and_refresh_ui()
                                ui.button(icon='delete', on_click=lambda i=n: delete_inbound_with_confirm(mgr, i['id'], i.get('remark',''), on_del_success)).props(btn_props).classes('text-red-400 hover:bg-slate-600')
                            else: ui.icon('lock', size='xs').classes('text-slate-600').tooltip('无权限')

        async def reload_and_refresh_ui():
            if mgr and hasattr(mgr, '_exec_remote_script'):
                try:
                    new_inbounds = await run.io_bound(lambda: asyncio.run(mgr.get_inbounds())) if not asyncio.iscoroutinefunction(mgr.get_inbounds) else await mgr.get_inbounds()
                    if new_inbounds is not None:
                        NODES_DATA[server_conf['url']] = new_inbounds
                        server_conf['_status'] = 'online'
                        await save_nodes_cache()
                except Exception as e: logger.error(f"SSH 强制刷新失败: {e}")
            else:
                try: await fetch_inbounds_safe(server_conf, force_refresh=True)
                except: pass
            render_node_list.refresh()

        REFRESH_CURRENT_NODES = reload_and_refresh_ui

        # ================== 辅助函数 (完整逻辑) ==================
        def open_edit_custom_node(node_data):
            with ui.dialog() as d, ui.card().classes('w-96 p-4'):
                ui.label('编辑节点备注').classes('text-lg font-bold mb-4')
                name_input = ui.input('节点名称', value=node_data.get('remark', '')).classes('w-full')
                async def save():
                    node_data['remark'] = name_input.value.strip()
                    await save_servers()
                    safe_notify('修改已保存', 'positive')
                    d.close()
                    render_node_list.refresh()
                with ui.row().classes('w-full justify-end mt-4'):
                    ui.button('取消', on_click=d.close).props('flat')
                    ui.button('保存', on_click=save).classes('bg-blue-600 text-white')
            d.open()

        async def uninstall_and_delete(node_data):
            with ui.dialog() as d, ui.card().classes('w-96 p-6'):
                with ui.row().classes('items-center gap-2 text-red-600 mb-2'):
                    ui.icon('delete_forever', size='md'); ui.label('卸载并清理环境').classes('font-bold text-lg')
                ui.label(f"节点: {node_data.get('remark')}").classes('text-sm font-bold text-gray-800')
                ui.label("即将执行以下操作：").classes('text-xs text-gray-500 mt-2')
                
                domain_to_del = None
                raw_link = node_data.get('_raw_link', '')
                if raw_link and '://' in raw_link:
                    try:
                        from urllib.parse import urlparse, parse_qs
                        query = urlparse(raw_link).query; params = parse_qs(query)
                        if 'sni' in params: domain_to_del = params['sni'][0]
                        elif 'host' in params: domain_to_del = params['host'][0]
                    except: pass
                
                with ui.column().classes('ml-2 gap-1 mt-1'):
                    ui.label('1. 停止 Xray 服务并清除残留进程').classes('text-xs text-gray-600')
                    ui.label('2. 删除 Xray 配置文件').classes('text-xs text-gray-600')
                    if domain_to_del and ADMIN_CONFIG.get('cf_root_domain') in domain_to_del:
                        ui.label(f'3. 🗑️ 自动删除 CF 解析: {domain_to_del}').classes('text-xs text-red-500 font-bold')
                    else: ui.label('3. 跳过 DNS 清理 (非托管域名)').classes('text-xs text-gray-400')

                async def start_uninstall():
                    d.close(); notification = ui.notification(message='正在执行卸载与清理...', timeout=0, spinner=True)
                    if domain_to_del:
                        cf = CloudflareHandler()
                        if cf.token and cf.root_domain and (cf.root_domain in domain_to_del):
                            ok, msg = await cf.delete_record_by_domain(domain_to_del)
                            if ok: safe_notify(f"☁️ {msg}", "positive")
                            else: safe_notify(f"⚠️ DNS 删除失败: {msg}", "warning")
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, XHTTP_UNINSTALL_SCRIPT))
                    notification.dismiss()
                    if success: safe_notify('✅ 服务已卸载，进程已清理', 'positive')
                    else: safe_notify(f'⚠️ SSH 卸载可能有残留: {output}', 'warning')
                    if 'custom_nodes' in server_conf and node_data in server_conf['custom_nodes']:
                        server_conf['custom_nodes'].remove(node_data)
                        await save_servers()
                    await reload_and_refresh_ui()

                with ui.row().classes('w-full justify-end mt-6 gap-2'):
                    ui.button('取消', on_click=d.close).props('flat color=grey')
                    ui.button('确认执行', color='red', on_click=start_uninstall).props('unelevated')
            d.open()

        # ================= 布局构建 =================
        # --- 顶部卡片 (深色) ---
        btn_3d_base = 'text-xs font-bold text-white rounded-lg px-4 py-2 border-b-4 active:border-b-0 active:translate-y-[4px] transition-all duration-150 shadow-sm'
        btn_blue = f'bg-blue-600 border-blue-800 hover:bg-blue-500 {btn_3d_base}'
        btn_green = f'bg-green-600 border-green-800 hover:bg-green-500 {btn_3d_base}'

        with ui.row().classes('w-full justify-between items-center bg-[#1e293b] p-4 rounded-xl border border-slate-700 border-b-[4px] border-b-slate-800 shadow-sm flex-shrink-0'):
            with ui.row().classes('items-center gap-4'):
                sys_icon = 'computer' if 'Oracle' in server_conf.get('name', '') else 'dns'
                with ui.element('div').classes('p-3 bg-[#0f172a] rounded-lg border border-slate-600'):
                    ui.icon(sys_icon, size='md').classes('text-blue-400')
                with ui.column().classes('gap-1'):
                    ui.label(server_conf.get('name', '未命名服务器')).classes('text-xl font-black text-slate-200 leading-tight tracking-tight')
                    with ui.row().classes('items-center gap-2'):
                        ip_addr = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').split(':')[0]
                        ui.label(ip_addr).classes('text-xs font-mono font-bold text-slate-400 bg-[#0f172a] px-2 py-0.5 rounded border border-slate-700')
                        
                        # ✨✨✨ 核心修复：引入实时刷新机制，判断探针真实在线状态 ✨✨✨
                        @ui.refreshable
                        def live_status_badge():
                            import time
                            is_online = False
                            now_ts = time.time()
                            
                            # 1. 优先判断探针实时心跳 (20秒内有更新算在线)
                            probe_cache = PROBE_DATA_CACHE.get(server_conf['url'])
                            if probe_cache and (now_ts - probe_cache.get('last_updated', 0) < 20):
                                is_online = True
                            # 2. 其次判断 API 探测结果
                            elif server_conf.get('_status') == 'online':
                                is_online = True
                                
                            if is_online:
                                ui.badge('Online', color='green').props('rounded outline size=xs')
                            else:
                                ui.badge('Offline', color='grey').props('rounded outline size=xs')

                        live_status_badge()
                        # 每 3 秒静默刷新一次这个徽章，页面不会闪烁
                        ui.timer(3.0, live_status_badge.refresh)
            
            with ui.row().classes('gap-3'):
                ui.button('一键部署 XHTTP', icon='rocket_launch', on_click=lambda: open_deploy_xhttp_dialog(server_conf, reload_and_refresh_ui)).props('unelevated').classes(btn_blue)
                ui.button('一键部署 Hy2', icon='bolt', on_click=lambda: open_deploy_hysteria_dialog(server_conf, reload_and_refresh_ui)).props('unelevated').classes(btn_blue)
                ui.button('一键部署 Snell', icon='security', on_click=lambda: open_deploy_snell_dialog(server_conf, reload_and_refresh_ui)).props('unelevated').classes(btn_blue)
                if has_manager_access:
                    async def on_add_success(): 
                        ui.notify('添加节点成功'); await reload_and_refresh_ui()
                    ui.button('新建 XUI 节点', icon='add', on_click=lambda: open_inbound_dialog(mgr, None, on_add_success)).props('unelevated').classes(btn_green)
                else:
                    ui.button('探针只读', icon='visibility', on_click=None).props('unelevated disabled').classes('bg-slate-700 text-slate-400 rounded-lg px-4 py-2 border-b-4 border-slate-800 text-xs font-bold opacity-70')

        ui.element('div').classes('h-4 flex-shrink-0')

        # --- 中间列表 (深色) ---
        with ui.card().classes('w-full flex-grow flex flex-col p-0 rounded-xl border border-slate-700 border-b-[4px] border-b-slate-800 shadow-sm overflow-hidden bg-[#1e293b]'):
            with ui.row().classes('w-full items-center justify-between p-3 bg-[#0f172a] border-b border-slate-700'):
                 ui.label('节点列表').classes('text-sm font-black text-slate-400 uppercase tracking-wide ml-1')
                 if server_conf.get('probe_installed') and server_conf.get('ssh_host'):
                     ui.badge('Root 模式', color='teal').props('outline rounded size=xs')
                 elif server_conf.get('user'):
                     ui.badge('API 托管模式', color='blue').props('outline rounded size=xs')

            # 表头
            with ui.element('div').classes('grid w-full gap-4 font-bold text-slate-500 border-b border-slate-700 pb-2 pt-2 px-2 text-xs uppercase tracking-wider bg-[#1e293b]').style(SINGLE_COLS_NO_PING):
                ui.label('节点名称').classes('text-left pl-2')
                for h in ['类型', '流量', '协议', '端口', '状态', '操作']: ui.label(h).classes('text-center')

            # 滚动区
            with ui.scroll_area().classes('w-full flex-grow bg-[#0f172a] p-1'): 
                await render_node_list()

        ui.element('div').classes('h-6 flex-shrink-0') 


        # --- 第三段：SSH 窗口  ---
        with ui.card().classes('w-full h-[750px] flex-shrink-0 p-0 rounded-xl border border-gray-300 border-b-[4px] border-b-gray-400 shadow-lg overflow-hidden bg-slate-900 flex flex-col'):
            ssh_state = {'active': False, 'instance': None}

            def render_ssh_area():
                with ui.row().classes('w-full h-10 bg-slate-800 items-center justify-between px-4 flex-shrink-0 border-b border-slate-700'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('terminal').classes('text-white text-sm')
                        ui.label(f"SSH Console: {server_conf.get('ssh_user','root')}@{server_conf.get('ssh_host') or 'IP'}").classes('text-gray-300 text-xs font-mono font-bold')
                    if ssh_state['active']: ui.button(icon='link_off', on_click=stop_ssh).props('flat dense round color=red size=sm').tooltip('断开连接')
                    else: ui.label('Disconnected').classes('text-[10px] text-gray-500')

                box_cls = 'w-full flex-grow bg-[#0f0f0f] overflow-hidden'
                if not ssh_state['active']: box_cls += ' flex justify-center items-center'
                else: box_cls += ' relative block'

                terminal_box = ui.element('div').classes(box_cls)
                with terminal_box:
                    if not ssh_state['active']:
                        with ui.column().classes('items-center gap-4'):
                            ui.icon('dns', size='4rem').classes('text-gray-800')
                            ui.label('安全终端已就绪').classes('text-gray-600 text-sm font-bold')
                            ui.button('立即连接 SSH', icon='login', on_click=start_ssh).classes('bg-blue-600 text-white font-bold px-6 py-2 rounded-lg border-b-4 border-blue-800 active:border-b-0 active:translate-y-[2px] transition-all')
                    else:
                        ssh = WebSSH(terminal_box, server_conf)
                        ssh_state['instance'] = ssh
                        ui.timer(0.1, lambda: asyncio.create_task(ssh.connect()), once=True)

                # --- 快捷命令区 ---
                with ui.row().classes('w-full min-h-[60px] bg-slate-800 border-t border-slate-700 px-4 py-4 gap-3 items-center flex-wrap'):
                    ui.label('快捷命令:').classes('text-xs font-bold text-gray-400 mr-2')
                    
                    commands = ADMIN_CONFIG.get('quick_commands', [])
                    for cmd_obj in commands:
                        cmd_name = cmd_obj.get('name', '未命名')
                        cmd_text = cmd_obj.get('cmd', '')
                        with ui.element('div').classes('flex items-center bg-slate-700 rounded overflow-hidden border-b-2 border-slate-900 transition-all active:border-b-0 active:translate-y-[2px] hover:bg-slate-600'):
                            ui.button(cmd_name, on_click=lambda c=cmd_text: exec_quick_cmd(c)).props('unelevated').classes('bg-transparent text-[11px] font-bold text-slate-300 px-3 py-1.5 hover:text-white rounded-none')
                            ui.element('div').classes('w-[1px] h-4 bg-slate-500 opacity-50')
                            ui.button(icon='settings', on_click=lambda c=cmd_obj: open_cmd_editor(c)).props('flat dense size=xs').classes('text-slate-400 hover:text-white px-1 py-1.5 rounded-none')

                    ui.button(icon='add', on_click=lambda: open_cmd_editor(None)).props('flat dense round size=sm color=green').tooltip('添加常用命令')

            async def start_ssh():
                ssh_state['active'] = True
                render_card_content()

            async def stop_ssh():
                if ssh_state['instance']:
                    ssh_state['instance'].close()
                    ssh_state['instance'] = None
                ssh_state['active'] = False
                render_card_content()

            def exec_quick_cmd(cmd_text):
                if ssh_state['instance'] and ssh_state['instance'].active:
                    ssh_state['instance'].channel.send(cmd_text + "\n")
                    ui.notify(f"已发送: {cmd_text[:20]}...", type='positive', position='bottom')
                else:
                    ui.notify("请先连接 SSH", type='warning', position='bottom')

            def open_cmd_editor(existing_cmd=None):
                with ui.dialog() as d, ui.card().classes('w-96 p-5 bg-[#1e293b] border border-slate-600 shadow-2xl'):
                    with ui.row().classes('w-full justify-between items-center mb-4'):
                        ui.label('管理快捷命令').classes('text-lg font-bold text-white')
                        ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

                    name_input = ui.input('按钮名称', value=existing_cmd['name'] if existing_cmd else '').classes('w-full mb-3').props('outlined dense dark bg-color="slate-800"')
                    cmd_input = ui.textarea('执行命令', value=existing_cmd['cmd'] if existing_cmd else '').classes('w-full mb-4').props('outlined dense dark bg-color="slate-800" rows=4')
                    
                    async def save():
                        name = name_input.value.strip(); cmd = cmd_input.value.strip()
                        if not name or not cmd: return ui.notify("内容不能为空", type='negative')
                        if 'quick_commands' not in ADMIN_CONFIG: ADMIN_CONFIG['quick_commands'] = []
                        if existing_cmd: existing_cmd['name'] = name; existing_cmd['cmd'] = cmd
                        else: ADMIN_CONFIG['quick_commands'].append({'name': name, 'cmd': cmd, 'id': str(uuid.uuid4())[:8]})
                        await save_admin_config()
                        d.close()
                        render_card_content()
                        ui.notify("命令已保存", type='positive')

                    async def delete_current():
                        if existing_cmd and 'quick_commands' in ADMIN_CONFIG:
                            ADMIN_CONFIG['quick_commands'].remove(existing_cmd)
                            await save_admin_config()
                            d.close()
                            render_card_content()
                            ui.notify("命令已删除", type='positive')

                    with ui.row().classes('w-full justify-between mt-2'):
                        if existing_cmd: ui.button('删除', icon='delete', color='red', on_click=delete_current).props('flat dense')
                        else: ui.element('div')
                        ui.button('保存', icon='save', on_click=save).classes('bg-blue-600 text-white font-bold rounded-lg border-b-4 border-blue-800 active:border-b-0 active:translate-y-[2px]')

                d.open()

            def render_card_content():
                ssh_wrapper.clear()
                with ssh_wrapper:
                    render_ssh_area()

            ssh_wrapper = ui.column().classes('w-full h-full p-0 gap-0')
            render_card_content()

        # 如果配置了面板账号，且本地缓存为空，则在进入页面 0.2 秒后自动触发后台拉取
        if has_manager_access and not NODES_DATA.get(server_conf['url']):
            ui.timer(0.2, lambda: asyncio.create_task(reload_and_refresh_ui()), once=True)