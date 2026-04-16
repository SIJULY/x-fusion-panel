class BatchSSH:
    def __init__(self):
        self.selected_urls = set()
        self.log_element = None
        self.dialog = None

    def open_dialog(self):
        self.selected_urls = set()
        with ui.dialog() as d, ui.card().classes('w-full max-w-4xl h-[80vh] flex flex-col p-0 overflow-hidden bg-[#1e293b] border border-slate-700'):
            self.dialog = d
            # 标题栏
            with ui.row().classes('w-full justify-between items-center p-4 bg-[#0f172a] border-b border-slate-700'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('terminal', color='green').classes('text-xl')
                    ui.label('批量 SSH 执行').classes('text-lg font-bold text-slate-200')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

            self.content_box = ui.column().classes('w-full flex-grow overflow-hidden p-0 bg-[#1e293b]')
            self.render_selection_view()
        d.open()

    def render_selection_view(self):
        self.content_box.clear()
        with self.content_box:
            # 工具栏
            with ui.row().classes('w-full p-2 border-b border-slate-700 gap-2 bg-[#172033] items-center'):
                ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense color=blue')
                ui.button('全不选', on_click=lambda: self.toggle_all(False)).props('flat dense color=grey')
                self.count_label = ui.label('已选: 0').classes('ml-auto text-sm font-bold text-slate-400 mr-4')

            # 列表
            with ui.scroll_area().classes('w-full flex-grow p-4'):
                with ui.column().classes('w-full gap-1'):
                    groups = {}
                    for s in SERVERS_CACHE:
                        g = s.get('group', '默认分组')
                        if g not in groups: groups[g] = []
                        groups[g].append(s)

                    self.checks = {}
                    for g_name, servers in groups.items():
                        ui.label(g_name).classes('text-xs font-bold text-slate-500 mt-2 uppercase')
                        for s in servers:
                            with ui.row().classes('w-full items-center p-2 hover:bg-slate-700 rounded border border-transparent transition'):
                                chk = ui.checkbox(value=False, on_change=self.update_count).props('dense dark color=green')
                                self.checks[s['url']] = chk
                                with ui.column().classes('gap-0 ml-2'):
                                    ui.label(s['name']).classes('text-sm font-bold text-slate-300')
                                    ui.label(s['url']).classes('text-xs text-slate-600 font-mono')

            # 底部
            with ui.row().classes('w-full p-4 border-t border-slate-700 bg-[#0f172a] justify-end'):
                ui.button('下一步: 输入命令', on_click=self.go_to_execution, icon='arrow_forward').classes('bg-blue-600 text-white')

    def toggle_all(self, state):
        for chk in self.checks.values(): chk.value = state
        self.update_count()

    def update_count(self):
        count = sum(1 for c in self.checks.values() if c.value)
        self.count_label.set_text(f'已选: {count}')

    def go_to_execution(self):
        self.selected_urls = {url for url, chk in self.checks.items() if chk.value}
        if not self.selected_urls: return safe_notify('请至少选择一个服务器', 'warning')
        self.render_execution_view()

    def render_execution_view(self):
        self.content_box.clear()
        with self.content_box:
            # 输入区
            with ui.column().classes('w-full p-4 border-b border-slate-700 bg-[#172033] gap-2 flex-shrink-0'):
                ui.label(f'向 {len(self.selected_urls)} 台服务器发送命令:').classes('text-sm font-bold text-slate-400')
                self.cmd_input = ui.textarea(placeholder='例如: apt update -y').classes('w-full font-mono text-sm').props('outlined rows=3 dark bg-color="slate-900"')
                
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('提示: 后台并发执行，窗口可关闭。').classes('text-xs text-slate-500')
                    with ui.row().classes('gap-2'):
                        ui.button('上一步', on_click=self.render_selection_view).props('flat dense color=grey')
                        self.run_btn = ui.button('立即执行', on_click=self.run_batch, icon='play_arrow').classes('bg-green-600 text-white')

            # 日志区 (纯黑背景)
            self.log_container = ui.log().classes('w-full flex-grow font-mono text-xs bg-[#0b1121] text-green-400 p-4 overflow-y-auto')

    async def run_batch(self):
        cmd = self.cmd_input.value.strip()
        if not cmd: return safe_notify('请输入命令', 'warning')
        self.run_btn.disable(); self.cmd_input.disable()
        self.log_container.push(f"🚀 [Batch] Start: {cmd}")
        asyncio.create_task(self._process_batch(cmd, list(self.selected_urls)))

    async def _process_batch(self, cmd, urls):
        sem = asyncio.Semaphore(10)
        async def _worker(url):
            async with sem:
                server = next((s for s in SERVERS_CACHE if s['url'] == url), None)
                if not server: return
                name = server['name']
                
                def log_safe(msg):
                    try: 
                        if self.log_container and self.log_container.visible: self.log_container.push(msg)
                    except: pass

                log_safe(f"⏳ [{name}] Connecting...")
                try:
                    def ssh_exec():
                        client, msg = get_ssh_client_sync(server)
                        if not client: return False, msg
                        try:
                            stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
                            out = stdout.read().decode().strip()
                            err = stderr.read().decode().strip()
                            client.close(); return True, (out, err)
                        except Exception as e: return False, str(e)

                    success, result = await run.io_bound(ssh_exec)
                    if success:
                        out, err = result
                        if out: log_safe(f"✅ [{name}] OUT:\n{out}")
                        if err: log_safe(f"⚠️ [{name}] ERR:\n{err}")
                        if not out and not err: log_safe(f"✅ [{name}] Done (No Output)")
                    else: log_safe(f"❌ [{name}] Failed: {result}")
                except Exception as e: log_safe(f"❌ [{name}] Error: {e}")
                log_safe("-" * 30)

        tasks = [_worker(u) for u in urls]
        await asyncio.gather(*tasks)
        try: 
            self.log_container.push("🏁 All Done.")
            self.run_btn.enable(); self.cmd_input.enable()
        except: pass