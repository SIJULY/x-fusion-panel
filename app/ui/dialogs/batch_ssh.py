import asyncio
import socket

from nicegui import app, run, ui

from app.core.state import SERVERS_CACHE
from app.services.ssh import _ssh_exec_wrapper
from app.ui.common.notifications import safe_notify


class BatchSSH:
    def __init__(self):
        self.selected_urls = set()
        self.log_element = None
        self.dialog = None

    @staticmethod
    def _is_interactive_command(cmd: str) -> bool:
        cmd = (cmd or '').strip()
        interactive_prefixes = ('sudo -i', 'sudo su', 'su -', 'bash', 'sh')
        return any(cmd == p or cmd.startswith(f'{p} ') for p in interactive_prefixes)

    def open_dialog(self):
        self.selected_urls = set()
        is_dark = bool(app.storage.user.get('is_dark', True))
        self.is_dark = is_dark
        card_cls = 'w-full max-w-4xl h-[80vh] flex flex-col p-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-full max-w-4xl h-[80vh] flex flex-col p-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]'
        header_cls = 'w-full justify-between items-center px-5 py-4 bg-gradient-to-r from-[#0a1526] to-[#050a14] border-b border-[#1e3a5f]/60 relative overflow-hidden' if is_dark else 'w-full justify-between items-center px-5 py-4 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] border-b border-slate-300/90 relative overflow-hidden'
        icon_box_cls = 'w-9 h-9 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] shadow-[0_0_8px_rgba(0,0,0,0.7)] text-cyan-400 relative overflow-hidden' if is_dark else 'w-9 h-9 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 shadow-[0_4px_12px_rgba(148,163,184,0.14)] text-sky-600 relative overflow-hidden'
        title_cls = 'text-lg font-black text-slate-200 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide'
        close_cls = 'z-10 text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if is_dark else 'z-10 text-slate-500 hover:text-sky-700 hover:bg-sky-100'
        content_cls = 'w-full flex-grow overflow-hidden p-0 bg-[#030712]' if is_dark else 'w-full flex-grow overflow-hidden p-0 bg-[#f8fbff]'
        with ui.dialog() as d, ui.card().classes(card_cls):
            self.dialog = d
            with ui.row().classes(header_cls):
                ui.element('div').classes('absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNCwyMTEsMjM4LDAuMDcpIi8+PC9zdmc+")] opacity-100 pointer-events-none')
                with ui.row().classes('items-center gap-3 z-10'):
                    with ui.element('div').classes(icon_box_cls):
                        ui.element('div').classes('absolute inset-0 bg-cyan-400/10')
                        ui.icon('terminal').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                    ui.label('批量 SSH 执行').classes(title_cls)
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes(close_cls)

            self.content_box = ui.column().classes(content_cls)
            self.render_selection_view()
        d.open()

    def render_selection_view(self):
        self.content_box.clear()
        with self.content_box:
            with ui.row().classes('w-full p-3 border-b border-[#1e3a5f]/45 gap-2 bg-[#0a1120] items-center' if self.is_dark else 'w-full p-3 border-b border-slate-300/90 gap-2 bg-sky-50 items-center'):
                ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense').classes('bg-cyan-950/35 text-cyan-300 border border-cyan-500/35 rounded-sm px-3' if self.is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 rounded-sm px-3')
                ui.button('全不选', on_click=lambda: self.toggle_all(False)).props('flat dense').classes('bg-[#050b14] text-slate-300 border border-[#1e3a5f]/45 rounded-sm px-3' if self.is_dark else 'bg-white text-slate-600 border border-slate-300 rounded-sm px-3')
                self.count_label = ui.label('已选: 0').classes('ml-auto text-sm font-black text-cyan-400 mr-4' if self.is_dark else 'ml-auto text-sm font-black text-sky-700 mr-4')

            with ui.scroll_area().classes('w-full flex-grow p-4'):
                with ui.column().classes('w-full gap-1'):
                    groups = {}
                    for s in SERVERS_CACHE:
                        g = s.get('group', '默认分组')
                        if g not in groups:
                            groups[g] = []
                        groups[g].append(s)

                    self.checks = {}
                    for g_name, servers in groups.items():
                        ui.label(g_name).classes('text-xs font-bold text-slate-500 mt-2 uppercase')
                        for s in servers:
                            with ui.row().classes('w-full items-center p-2 hover:bg-[#0d172a] rounded-sm border border-transparent transition' if self.is_dark else 'w-full items-center p-2 hover:bg-sky-50 rounded-sm border border-transparent transition'):
                                chk = ui.checkbox(value=False, on_change=self.update_count).props('dense dark color=cyan' if self.is_dark else 'dense color=blue')
                                self.checks[s['url']] = chk
                                with ui.column().classes('gap-0 ml-2'):
                                    ui.label(s['name']).classes('text-sm font-bold text-slate-300' if self.is_dark else 'text-sm font-bold text-slate-800')
                                    ui.label(s['url']).classes('text-xs text-slate-500 font-mono')

            with ui.row().classes('w-full p-4 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14] justify-end' if self.is_dark else 'w-full p-4 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] justify-end'):
                ui.button('下一步: 输入命令', on_click=self.go_to_execution, icon='arrow_forward').props('flat').classes('bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm px-4' if self.is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 font-black rounded-sm px-4')

    def toggle_all(self, state):
        for chk in self.checks.values():
            chk.value = state
        self.update_count()

    def update_count(self):
        count = sum(1 for c in self.checks.values() if c.value)
        self.count_label.set_text(f'已选: {count}')

    def go_to_execution(self):
        self.selected_urls = {url for url, chk in self.checks.items() if chk.value}
        if not self.selected_urls:
            return safe_notify('请至少选择一个服务器', 'warning')
        self.render_execution_view()

    def render_execution_view(self):
        self.content_box.clear()
        with self.content_box:
            with ui.column().classes('w-full p-4 border-b border-[#1e3a5f]/45 bg-[#0a1120] gap-2 flex-shrink-0' if self.is_dark else 'w-full p-4 border-b border-slate-300/90 bg-sky-50 gap-2 flex-shrink-0'):
                ui.label(f'向 {len(self.selected_urls)} 台服务器发送命令:').classes('text-sm font-black text-cyan-400 tracking-wide' if self.is_dark else 'text-sm font-black text-sky-700 tracking-wide')
                self.cmd_input = ui.textarea(placeholder='例如: apt update -y').classes('w-full font-mono text-sm').props('outlined rows=3 dark color=cyan standout bg-color="[#050b14]"' if self.is_dark else 'outlined rows=3 color=blue')

                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('提示: 后台并发执行，窗口可关闭。').classes('text-xs text-slate-500')
                    with ui.row().classes('gap-2'):
                        ui.button('上一步', on_click=self.render_selection_view).props('outline dense color=grey').classes('text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if self.is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold tracking-wide rounded-sm')
                        self.run_btn = ui.button('立即执行', on_click=self.run_batch, icon='play_arrow').props('flat').classes('bg-emerald-950/45 text-emerald-300 border border-emerald-500/45 hover:bg-emerald-900/55 font-black rounded-sm px-4' if self.is_dark else 'bg-emerald-100 text-emerald-700 border border-emerald-300 hover:bg-emerald-200 font-black rounded-sm px-4')

            self.log_container = ui.log().classes('w-full flex-grow font-mono text-xs bg-black text-green-400 p-4 overflow-y-auto border-t border-cyan-900/30' if self.is_dark else 'w-full flex-grow font-mono text-xs bg-slate-900 text-green-400 p-4 overflow-y-auto border-t border-slate-300/90')

    async def run_batch(self):
        cmd = self.cmd_input.value.strip()
        if not cmd:
            return safe_notify('请输入命令', 'warning')

        if self._is_interactive_command(cmd):
            supported_examples = [
                'whoami',
                'sudo -n whoami',
                'sudo -n systemctl status snell --no-pager -l',
                "sudo -n bash -lc 'whoami && pwd'",
            ]
            if len(self.selected_urls) == 1:
                server = next((s for s in SERVERS_CACHE if s['url'] in self.selected_urls), None)
                if not server:
                    self.log_container.push('❌ 未找到目标服务器')
                    return
                self.log_container.push(f"⚠️ 检测到交互式命令，批量 SSH 不再弹出交互终端: [{server.get('name', '未命名服务器')}]")
                self.log_container.push('💡 请改用单机详情页右上角 SSH 按钮进入交互终端。')
                self.log_container.push(f'💡 当前命令: {cmd}')
                self.log_container.push('-' * 30)
                return
            self.log_container.push('❌ 当前选择了多台服务器，不能执行交互式命令。')
            self.log_container.push('💡 交互式命令示例: sudo -i / sudo su / su - / bash / sh')
            self.log_container.push('💡 如果要批量执行，请改用非交互式格式，例如:')
            for example in supported_examples:
                self.log_container.push(f'   - {example}')
            self.log_container.push('-' * 30)
            return

        self.run_btn.disable()
        self.cmd_input.disable()
        self.log_container.push(f"🚀 [Batch] Start: {cmd}")
        asyncio.create_task(self._process_batch(cmd, list(self.selected_urls)))

    async def _process_batch(self, cmd, urls):
        sem = asyncio.Semaphore(10)

        async def _worker(url):
            async with sem:
                server = next((s for s in SERVERS_CACHE if s['url'] == url), None)
                if not server:
                    return
                name = server['name']

                def log_safe(msg):
                    try:
                        if self.log_container and self.log_container.visible:
                            self.log_container.push(msg)
                    except:
                        pass

                log_safe(f"⏳ [{name}] Connecting...")
                try:
                    success, output = await _ssh_exec_wrapper(server, cmd, timeout=60)
                    if success:
                        if output:
                            log_safe(f"✅ [{name}] OUT:\n{output}")
                        else:
                            log_safe(f"✅ [{name}] Done (No Output)")
                    else:
                        if 'timeout' in str(output).lower() or '超时' in str(output):
                            log_safe(f"❌ [{name}] Failed: 执行超时：命令可能在等待交互输入（如 sudo -i / su - / vim），请改用非交互命令或去 WebSSH 执行")
                        else:
                            log_safe(f"❌ [{name}] Failed: {output}")
                except Exception as e:
                    log_safe(f"❌ [{name}] Error: {e}")
                log_safe("-" * 30)

        tasks = [_worker(u) for u in urls]
        await asyncio.gather(*tasks)
        try:
            self.log_container.push("🏁 All Done.")
            self.run_btn.enable()
            self.cmd_input.enable()
        except:
            pass
