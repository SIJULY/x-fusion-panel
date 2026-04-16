async def render_probe_page():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'PROBE'
    content_container.clear()
    # 背景色统一为 Slate-900
    content_container.classes(replace='w-full h-full overflow-y-auto p-6 bg-[#0f172a] relative flex flex-col justify-center items-center')
    
    if not ADMIN_CONFIG.get('probe_enabled'):
        ADMIN_CONFIG['probe_enabled'] = True; await save_admin_config()

    with content_container:
        with ui.column().classes('w-full max-w-7xl gap-6'):
            
            # --- 标题 ---
            with ui.row().classes('w-full items-center gap-3'):
                 with ui.element('div').classes('p-2 bg-blue-600 rounded-lg shadow-lg shadow-blue-900/50'):
                     ui.icon('tune', color='white').classes('text-2xl')
                 with ui.column().classes('gap-0'):
                    ui.label('探针管理与设置').classes('text-2xl font-extrabold text-slate-100 tracking-tight')
                    ui.label('Configuration & Management').classes('text-xs font-bold text-slate-500 uppercase tracking-widest')

            # --- 布局 (左右分栏) ---
            with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-7 gap-6 items-stretch'):
                
                # ======================= 左侧：参数设置区 (4/7) =======================
                with ui.column().classes('lg:col-span-4 w-full gap-6'):
                    
                    # 通用卡片样式
                    card_style = 'w-full p-6 bg-[#1e293b] border border-slate-700 shadow-xl rounded-xl'
                    
                    # 1. 基础连接设置
                    with ui.card().classes(card_style):
                        with ui.row().classes('items-center gap-2 mb-4 border-b border-slate-700 pb-2 w-full'):
                            ui.icon('hub', color='blue').classes('text-xl')
                            ui.label('基础连接设置').classes('text-lg font-bold text-slate-200')
                        
                        with ui.column().classes('w-full gap-2'):
                            ui.label('📡 主控端地址 (Agent连接用)').classes('text-sm font-bold text-slate-400')
                            url_input = ui.input(value=ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')).props('outlined dense dark').classes('w-full')
                            ui.label('请填写公网 IP 或域名，带端口').classes('text-xs text-slate-500')

                        async def save_url():
                            ADMIN_CONFIG['manager_base_url'] = url_input.value.strip().rstrip('/')
                            await save_admin_config(); safe_notify('已保存', 'positive')
                        
                        # 🔥 修复：使用 with 替代 .add()
                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存', icon='save', on_click=save_url).props('unelevated color=blue-7')

                    # 2. 测速目标设置
                    with ui.card().classes(card_style):
                        with ui.row().classes('items-center gap-2 mb-4 border-b border-slate-700 pb-2 w-full'):
                            ui.icon('speed', color='orange').classes('text-xl')
                            ui.label('三网延迟测速目标').classes('text-lg font-bold text-slate-200')
                        
                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-3 gap-4'):
                            ping_ct = ui.input('电信 IP', value=ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')).props('outlined dense dark')
                            ping_cu = ui.input('联通 IP', value=ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')).props('outlined dense dark')
                            ping_cm = ui.input('移动 IP', value=ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')).props('outlined dense dark')
                        
                        async def save_ping():
                            ADMIN_CONFIG['ping_target_ct'] = ping_ct.value; ADMIN_CONFIG['ping_target_cu'] = ping_cu.value; ADMIN_CONFIG['ping_target_cm'] = ping_cm.value
                            await save_admin_config(); safe_notify('已保存 (需更新探针生效)', 'positive')
                        
                        # 🔥 修复：使用 with 替代 .add()
                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存', icon='save', on_click=save_ping).props('unelevated color=orange-7')

                    # 3. 通知设置
                    with ui.card().classes(card_style):
                        with ui.row().classes('items-center gap-2 mb-4 border-b border-slate-700 pb-2 w-full'):
                            ui.icon('notifications', color='purple').classes('text-xl')
                            ui.label('Telegram 通知').classes('text-lg font-bold text-slate-200')
                        
                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-4'):
                            tg_token = ui.input('Bot Token', value=ADMIN_CONFIG.get('tg_bot_token', '')).props('outlined dense dark')
                            tg_id = ui.input('Chat ID', value=ADMIN_CONFIG.get('tg_chat_id', '')).props('outlined dense dark')

                        async def save_tg():
                            ADMIN_CONFIG['tg_bot_token'] = tg_token.value; ADMIN_CONFIG['tg_chat_id'] = tg_id.value
                            await save_admin_config(); safe_notify('已保存', 'positive')
                        
                        # 🔥 修复：使用 with 替代 .add()
                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存', icon='save', on_click=save_tg).props('unelevated color=purple-7')

                # ======================= 右侧：快捷操作区 (3/7) =======================
                # 之前因为报错，这部分代码没执行到，所以你会觉得"少了很多功能"
                with ui.column().classes('lg:col-span-3 w-full gap-6 h-full'):
                    
                    # 卡片 A: 快捷操作
                    with ui.card().classes(card_style + ' flex-shrink-0'):
                        ui.label('快捷操作').classes('text-lg font-bold text-slate-200 mb-4 border-l-4 border-blue-500 pl-2')
                        with ui.column().classes('w-full gap-3'):
                            # 复制命令
                            async def copy_cmd():
                                try: origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
                                except: safe_notify("获取地址失败", "negative"); return
                                
                                token = ADMIN_CONFIG.get('probe_token', 'default_token')
                                mgr_url = ADMIN_CONFIG.get('manager_base_url', origin).strip().rstrip('/')
                                reg_url = f"{mgr_url}/api/probe/register"
                                
                                ct = ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')
                                cu = ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')
                                cm = ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')
                                
                                cmd = f'curl -sL https://raw.githubusercontent.com/SIJULY/x-fusion-panel/main/static/x-install.sh | bash -s -- "{token}" "{reg_url}" "{ct}" "{cu}" "{cm}"'
                                await safe_copy_to_clipboard(cmd); safe_notify("已复制安装命令", "positive")
                            
                            ui.button('复制安装命令', icon='content_copy', on_click=copy_cmd) \
                                .classes('w-full bg-[#172033] border border-slate-600 text-blue-400 shadow-sm hover:bg-[#334155] font-bold align-left')
                            
                            # 按钮组
                            with ui.row().classes('w-full gap-2'):
                                ui.button('分组管理', icon='settings', on_click=lambda: open_unified_group_manager('manage')) \
                                    .classes('flex-1 bg-[#172033] border border-slate-600 text-slate-300 hover:bg-[#334155]')
                                ui.button('排序视图', icon='sort', on_click=open_group_sort_dialog) \
                                    .classes('flex-1 bg-[#172033] border border-slate-600 text-slate-300 hover:bg-[#334155]')
                            
                            # 更新探针
                            ui.button('更新所有探针', icon='system_update_alt', on_click=batch_install_all_probes) \
                                .classes('w-full bg-orange-900/30 text-orange-400 border border-orange-800 hover:bg-orange-900/50 font-bold align-left')

                    # 卡片 B: 监控墙入口
                    with ui.card().classes('w-full p-6 bg-gradient-to-br from-indigo-900 to-slate-900 text-white rounded-xl shadow-lg relative overflow-hidden group cursor-pointer flex-grow flex flex-col justify-center border border-indigo-500/30') \
                        .on('click', lambda: ui.navigate.to('/status', new_tab=True)):
                        ui.icon('public', size='10rem').classes('absolute -right-8 -bottom-8 text-white opacity-5 group-hover:rotate-12 transition transform duration-500')
                        ui.label('公开监控墙').classes('text-2xl font-bold mb-2')
                        ui.label('点击前往查看实时状态').classes('text-sm text-indigo-200 mb-6')
                        with ui.row().classes('items-center gap-2 text-blue-300 font-bold'):
                            ui.label('立即前往'); ui.icon('arrow_forward')

                    # 卡片 C: 数据概览
                    online_count = len([s for s in SERVERS_CACHE if s.get('_status') == 'online'])
                    probe_count = len([s for s in SERVERS_CACHE if s.get('probe_installed')])
                    
                    with ui.card().classes(card_style + ' flex-shrink-0'):
                        ui.label('数据概览').classes('text-lg font-bold text-slate-200 mb-4 border-l-4 border-green-500 pl-2')
                        def stat_row(label, val, color):
                            with ui.row().classes('w-full justify-between items-center border-b border-slate-700 pb-3 mb-3 last:border-0 last:mb-0'):
                                ui.label(label).classes('text-slate-500 text-sm')
                                ui.label(str(val)).classes(f'font-bold text-xl {color}')
                        stat_row('总服务器', len(SERVERS_CACHE), 'text-slate-200')
                        stat_row('当前在线', online_count, 'text-green-400')
                        stat_row('已装探针', probe_count, 'text-purple-400')