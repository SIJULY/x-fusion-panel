async def open_deploy_snell_dialog(server_conf, callback):
    # 解析目标 IP 作为兜底
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    import random, string, uuid, urllib.parse

    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl bg-[#1e293b] border border-slate-700 shadow-2xl'):
        # 顶部标题栏
        with ui.column().classes('w-full bg-[#0f172a] border-b border-slate-700 p-6 gap-2'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('security', size='md').classes('text-blue-400')
                ui.label('部署 Snell 节点 (v5 最新版)').classes('text-lg font-bold')
            ui.label(f"目标服务器: {target_host}").classes('text-xs text-gray-400 font-mono')

        # 内容区
        with ui.column().classes('w-full p-6 gap-4'):
            name_input = ui.input('节点备注', placeholder='例如: HK-Snell-v5').props('outlined dense dark').classes('w-full')
            
            with ui.row().classes('w-full gap-2 items-center'):
                rand_port = random.randint(30000, 60000)
                rand_psk = ''.join(random.choices(string.ascii_letters + string.digits, k=20))
                
                port_input = ui.number('端口', value=rand_port, format='%.0f').props('outlined dense dark').classes('w-1/3')
                psk_input = ui.input('密钥 (PSK)', value=rand_psk).props('outlined dense dark').classes('flex-grow')
                
            # 日志区
            log_area = ui.log().classes('w-full h-48 bg-black text-green-400 text-[11px] font-mono p-3 rounded border border-slate-700 hidden transition-all')

        # 底部操作栏
        with ui.row().classes('w-full p-4 bg-[#0f172a] border-t border-slate-700 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')
            
            async def start_process():
                btn_cancel.disable(); btn_deploy.props('loading'); log_area.classes(remove='hidden')
                try:
                    params = {
                        "port": int(port_input.value),
                        "psk": psk_input.value,
                        "target_ip": target_host # 传入兜底 IP
                    }
                    script_content = SNELL_INSTALL_SCRIPT_TEMPLATE.format(**params)
                    deploy_cmd = f"cat > /tmp/install_snell.sh << 'EOF_SCRIPT'\n{script_content}\nEOF_SCRIPT\nbash /tmp/install_snell.sh"
                    
                    log_area.push(f"🚀 [SSH] 开始在 {target_host} 安装 Snell v5 ...")
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, deploy_cmd))
                    
                    if success:
                        import re
                        match = re.search(r'SNELL_DEPLOY_SUCCESS_LINK: (snell://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("🎉 Snell v5 部署成功并已启动！")
                            
                            custom_name = name_input.value.strip() or f"Snell-v5-{target_host[-3:]}"
                            
                            # 组装节点数据
                            if '#' in link: link = link.split('#')[0]
                            final_raw_link = f"{link}#{urllib.parse.quote(custom_name)}"
                            
                            new_node = {
                                "id": str(uuid.uuid4()), 
                                "remark": custom_name, 
                                "port": params['port'], 
                                "protocol": "snell",
                                "settings": {}, 
                                "streamSettings": {}, 
                                "enable": True, 
                                "_is_custom": True, 
                                "_raw_link": final_raw_link 
                            }
                            if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                            server_conf['custom_nodes'].append(new_node)
                            await save_servers()
                            
                            safe_notify(f"✅ 节点 {custom_name} 已添加", "positive")
                            await asyncio.sleep(1); d.close()
                            if callback: await callback()
                        else: 
                            log_area.push("❌ 部署失败：未能成功启动服务。")
                            log_area.push(output[-500:]) # 打印最后的错误日志
                    else: 
                        log_area.push(f"❌ SSH 连接失败: {output}")
                finally:
                    btn_cancel.enable(); btn_deploy.props(remove='loading')

            btn_deploy = ui.button('开始部署', on_click=start_process).props('unelevated').classes('bg-blue-600 text-white')
    d.open()