async def open_deploy_xhttp_dialog(server_conf, callback):
    # 1. 准备 IP
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    real_ip = target_host
    import re, socket
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try: real_ip = await run.io_bound(socket.gethostbyname, target_host)
        except: safe_notify(f"❌ 无法解析 IP: {target_host}", "negative"); return

    # 2. 检查 CF
    cf_handler = CloudflareHandler()
    if not cf_handler.token or not cf_handler.root_domain:
        safe_notify("❌ 请先配置 Cloudflare API", "negative"); return

    # 3. 生成域名
    import random, string
    rand_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    sub_prefix = f"node-{real_ip.replace('.', '-')}-{rand_suffix}"
    target_domain = f"{sub_prefix}.{cf_handler.root_domain}"

    # === 构建弹窗 (深色) ===
    # bg-[#1e293b] border-slate-700
    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # 顶部
        with ui.column().classes('w-full bg-[#0f172a] p-6 gap-2 border-b border-slate-700'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('rocket_launch', size='md').classes('text-blue-400')
                ui.label('部署 XHTTP-Reality (V76)').classes('text-lg font-bold')
            ui.label(f"部署目标: {target_domain}").classes('text-xs text-green-400 font-mono')

        # 内容区
        with ui.column().classes('w-full p-6 gap-4'):
            ui.label('节点备注名称').classes('text-xs font-bold text-slate-500 mb-[-8px]')
            # 输入框适配深色
            remark_input = ui.input(placeholder=f'默认: Reality-{target_domain}').props('outlined dense clearable dark').classes('w-full')
            
            # 日志区 (纯黑背景)
            log_area = ui.log().classes('w-full h-48 bg-black text-green-400 text-[11px] font-mono p-3 rounded border border-slate-700 hidden transition-all')

        # 底部按钮
        with ui.row().classes('w-full p-4 bg-[#0f172a] border-t border-slate-700 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')
            
            # --- 逻辑 A: 执行部署脚本 ---
            async def run_deploy_script():
                try:
                    log_area.push(f"🔄 [CF] 添加解析: {target_domain} -> {real_ip}...")
                    success, msg = await cf_handler.auto_configure(real_ip, sub_prefix)
                    if not success: raise Exception(f"CF配置失败: {msg}")
                    
                    log_area.push(f"🚀 [SSH] 开始执行安装脚本...")
                    
                    deploy_cmd = f"""
cat > /tmp/install_xhttp.sh << 'EOF_SCRIPT'
{XHTTP_INSTALL_SCRIPT_TEMPLATE}
EOF_SCRIPT
bash /tmp/install_xhttp.sh "{target_domain}"
"""
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, deploy_cmd))
                    
                    if success:
                        match = re.search(r'DEPLOY_SUCCESS_LINK: (vless://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("✅ 部署成功！正在保存...")
                            
                            custom_name = remark_input.value.strip()
                            final_remark = custom_name if custom_name else f"Reality-{target_domain}"
                            node_data = parse_vless_link_to_node(link, remark_override=final_remark)
                            
                            if node_data:
                                if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                                server_conf['custom_nodes'].append(node_data)
                                await save_servers()
                                safe_notify(f"✅ 节点已添加", "positive")
                                await asyncio.sleep(1)
                                d.close()
                                if callback: await callback()
                            else: log_area.push("❌ 链接解析失败")
                        else:
                            log_area.push("❌ 未捕获链接，请检查日志")
                            log_area.push(output[-500:])
                    else:
                        log_area.push(f"❌ SSH 执行出错: {output}")
                except Exception as e:
                    log_area.push(f"❌ 异常: {str(e)}")
                finally:
                    btn_deploy.props(remove='loading')
                    btn_cancel.enable()

            # --- 逻辑 B: 端口检测与启动 ---
            async def start_process():
                btn_cancel.disable()
                btn_deploy.props('loading')
                log_area.classes(remove='hidden')
                
                log_area.push("🔍 正在检查端口占用 (80/443)...")
                
                check_cmd = "netstat -tlpn | grep -E ':80 |:443 ' || lsof -i :80 -i :443"
                is_occupied = False
                check_output = ""
                
                try:
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, check_cmd))
                    if success and output.strip():
                        is_occupied = True
                        check_output = output.strip()
                except: pass

                if is_occupied:
                    log_area.push("⚠️ 端口被占用！等待确认...")
                    
                    # 深色确认弹窗
                    with ui.dialog() as confirm_d, ui.card().classes('w-96 p-5 border-t-4 border-red-500 shadow-xl bg-[#1e293b]'):
                        with ui.row().classes('items-center gap-2 text-red-500 mb-2'):
                            ui.icon('warning', size='md')
                            ui.label('端口冲突警告').classes('font-bold text-lg')
                        
                        ui.label('检测到 80 或 443 端口被占用：').classes('text-sm text-slate-300 mb-2')
                        
                        # 显示占用详情
                        short_log = "\n".join(check_output.split("\n")[:5])
                        ui.code(short_log).classes('w-full text-xs bg-black text-slate-300 p-2 rounded mb-3')
                        
                        ui.label('继续部署将【强制杀掉】这些进程。').classes('text-xs font-bold text-red-400')

                        with ui.row().classes('w-full justify-end gap-2 mt-4'):
                            ui.button('取消', on_click=lambda: [confirm_d.close(), d.close()]).props('flat color=grey')
                            
                            async def confirm_force():
                                confirm_d.close()
                                log_area.push("⚔️ 用户确认强制霸占，继续...")
                                await run_deploy_script()
                                
                            ui.button('强制部署', color='red', on_click=confirm_force).props('unelevated')
                    
                    confirm_d.open()
                    
                else:
                    log_area.push("✅ 端口空闲，开始部署...")
                    await run_deploy_script()

            btn_deploy = ui.button('开始部署', on_click=start_process).classes('bg-blue-600 text-white shadow-lg font-bold')

    d.open()