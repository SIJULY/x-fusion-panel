async def open_deploy_hysteria_dialog(server_conf, callback):
    # --- 1. IP 获取逻辑 ---
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    real_ip = target_host
    import re, socket, urllib.parse, uuid, asyncio
    
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try: real_ip = await run.io_bound(socket.gethostbyname, target_host)
        except: safe_notify(f"❌ 无法解析 IP: {target_host}", "negative"); return

    # --- 2. 构建 UI (全深色适配) ---
    # 主卡片：bg-[#1e293b] border-slate-700
    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # 顶部标题栏：bg-[#0f172a] border-b border-slate-700
        with ui.column().classes('w-full bg-[#0f172a] border-b border-slate-700 p-6 gap-2'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('bolt', size='md').classes('text-blue-400')
                ui.label('部署 Hysteria 2 (Surge 兼容版)').classes('text-lg font-bold')
            ui.label(f"服务器 IP: {real_ip}").classes('text-xs text-gray-400 font-mono')

        # 内容区
        with ui.column().classes('w-full p-6 gap-4'):
            name_input = ui.input('节点备注 (可选)', placeholder='例如: 狮城 Hy2').props('outlined dense dark').classes('w-full')
            sni_input = ui.input('伪装域名 (SNI)', value='www.bing.com').props('outlined dense dark').classes('w-full')
            
            # 复选框和数字输入框增加 dark 属性和浅色文字
            enable_hopping = ui.checkbox('启用端口跳跃', value=True).props('dark').classes('text-sm font-bold text-slate-300')
            with ui.row().classes('w-full items-center gap-2'):
                hop_start = ui.number('起始端口', value=20000, format='%.0f').props('outlined dense dark').classes('flex-1').bind_visibility_from(enable_hopping, 'value')
                ui.label('-').classes('text-slate-400').bind_visibility_from(enable_hopping, 'value')
                hop_end = ui.number('结束端口', value=50000, format='%.0f').props('outlined dense dark').classes('flex-1').bind_visibility_from(enable_hopping, 'value')

            # 日志区：纯黑背景更显眼
            log_area = ui.log().classes('w-full h-48 bg-black text-green-400 text-[11px] font-mono p-3 rounded border border-slate-700 hidden transition-all')

        # 底部操作栏：bg-[#0f172a] border-t border-slate-700
        with ui.row().classes('w-full p-4 bg-[#0f172a] border-t border-slate-700 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')
            
            async def start_process():
                btn_cancel.disable(); btn_deploy.props('loading'); log_area.classes(remove='hidden')
                try:
                    hy2_password = str(uuid.uuid4()).replace('-', '')[:16]
                    params = {
                        "password": hy2_password,
                        "sni": sni_input.value,
                        "enable_hopping": "true" if enable_hopping.value else "false",
                        "port_range_start": int(hop_start.value),
                        "port_range_end": int(hop_end.value)
                    }
                    
                    script_content = HYSTERIA_INSTALL_SCRIPT_TEMPLATE.format(**params)
                    deploy_cmd = f"cat > /tmp/install_hy2.sh << 'EOF_SCRIPT'\n{script_content}\nEOF_SCRIPT\nbash /tmp/install_hy2.sh"
                    
                    log_area.push(f"🚀 [SSH] 连接到 {real_ip} 开始安装...")
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, deploy_cmd))
                    
                    if success:
                        match = re.search(r'HYSTERIA_DEPLOY_SUCCESS_LINK: (hy2://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("🎉 部署成功！")
                            
                            custom_name = name_input.value.strip()
                            node_name = custom_name if custom_name else f"Hy2-{real_ip[-3:]}"
                            
                            # --- 强制使用端口范围字符串 ---
                            if enable_hopping.value:
                                final_port_display = f"{int(hop_start.value)}-{int(hop_end.value)}"
                            else:
                                try: final_port_display = int(link.split('@')[1].split(':')[1].split('?')[0])
                                except: final_port_display = 443

                            # 处理 Raw Link
                            if '#' in link: link = link.split('#')[0]
                            final_raw_link = f"{link}#{urllib.parse.quote(node_name)}"

                            new_node = {
                                "id": str(uuid.uuid4()), 
                                "remark": node_name, 
                                "port": final_port_display, 
                                "protocol": "hysteria2",
                                "settings": {}, 
                                "streamSettings": {}, 
                                "enable": True, 
                                "_is_custom": True, 
                                "_raw_link": final_raw_link 
                            }
                            if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                            server_conf['custom_nodes'].append(new_node)
                            await save_servers()
                            
                            safe_notify(f"✅ 节点 {node_name} 已添加", "positive")
                            await asyncio.sleep(1); d.close()
                            if callback: await callback()
                        else: 
                            log_area.push("❌ 未捕获链接"); log_area.push(output[-500:])
                    else: 
                        log_area.push(f"❌ SSH 失败: {output}")
                except Exception as e: 
                    log_area.push(f"❌ 异常: {e}"); print(e)
                finally:
                    btn_cancel.enable(); btn_deploy.props(remove='loading')

            btn_deploy = ui.button('开始部署', on_click=start_process).props('unelevated').classes('bg-blue-600 text-white')
    d.open()