import asyncio
import base64
import os
import tempfile
import time as time_module
import uuid

from nicegui import app, run, ui

from app.core.state import ADMIN_CONFIG
from app.services.ssh import WebSSH, _ssh_exec_wrapper
from app.storage.repositories import save_admin_config
from app.ui.common.notifications import safe_notify
from app.ui.dialogs import server_dialog as _server_dialog


def cleanup_ssh_route_terminal(server_key=None):
    return _server_dialog.cleanup_ssh_route_terminal(server_key=server_key)


async def render_single_ssh_view(server_conf):
    from app.services.sftp import (
        create_empty_remote_file,
        delete_remote_path,
        download_remote_file,
        get_parent_remote_path,
        is_probably_text_file,
        join_remote_path,
        list_remote_dir,
        make_remote_dir,
        normalize_remote_path,
        read_remote_file,
        rename_remote_path,
        upload_remote_file,
        write_remote_file,
    )
    from app.ui.pages.content_router import content_container, refresh_content

    _sync_resolve_ip = _server_dialog._sync_resolve_ip
    SSH_PAGE_TERMINALS = _server_dialog.SSH_PAGE_TERMINALS

    server_key = server_conf.get('url') or server_conf.get('ssh_host') or str(id(server_conf))
    cleanup_ssh_route_terminal(server_key)

    current_client = None
    try:
        current_client = ui.context.client
    except:
        pass

    is_dark = bool(app.storage.user.get('is_dark', False))

    current_client = None
    try:
        current_client = ui.context.client
    except:
        pass

    # 生成唯一 ID 供 JS 操作
    _tid = uuid.uuid4().hex[:8]
    terminal_box_id = f'term_box_{_tid}'
    resize_handle_id = f'term_resize_{_tid}'
    file_panel_id = f'file_panel_{_tid}'
    outer_column_id = f'outer_col_{_tid}'

    if content_container:
        content_container.clear()

        bg_removes = 'overflow-y-auto block justify-start bg-[#0f172a] bg-[#030712] bg-[#eef4ff] bg-[#f8fbff] bg-white dark:bg-[#030712]'
        content_container.classes(remove=bg_removes,
                                  add='h-full flex-1 min-h-0 overflow-hidden flex flex-col p-4 gap-4')

        content_container.style('background-color: var(--xf-bg-main) !important;')

        ui.run_javascript(f'''
            setTimeout(() => {{
                const isDark = {str(is_dark).lower()};
                if (isDark) {{
                    const themeIcon = document.querySelector('#xf-theme-btn i');
                    if (themeIcon) themeIcon.textContent = 'light_mode';

                    const header = document.getElementById('xf-header');
                    if (header) {{
                        header.style.cssText = 'background: linear-gradient(to right, #070e1a, #0a1526) !important; color: white !important; border-bottom: 1px solid rgba(30,58,95,0.60) !important; box-shadow: 0 4px 20px rgba(0,0,0,0.6) !important;';
                    }}
                }}
            }}, 50);
        ''')

    terminal_state = {'instance': None}
    file_state = {'current_path': '/', 'entries': [], 'loading': False}
    tree_state = {'expanded': {'/'}, 'selected': '/', 'cache': {}, 'loading': set()}
    path_input = None

    editor_state = {
        'dialog': None,
        'files': {},
        'active_path': None,
        'refresh_tabs': None,
    }

    async def _start_terminal(terminal_box):
        await asyncio.sleep(0.15)
        try:
            terminal_box.clear()
        except:
            pass
        ssh = WebSSH(terminal_box, server_conf)
        terminal_state['instance'] = ssh
        SSH_PAGE_TERMINALS[server_key] = ssh
        await ssh.connect()

    async def _back_to_detail():
        cleanup_ssh_route_terminal(server_key)
        await refresh_content('SINGLE', server_conf, manual_client=current_client)

    def format_file_size(size):
        try:
            size = float(size or 0)
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if size < 1024 or unit == 'TB':
                    return f'{size:.1f} {unit}' if unit != 'B' else f'{int(size)} B'
                size /= 1024
        except:
            return '--'

    def format_mtime(value):
        try:
            if not value:
                return '--'
            return time_module.strftime('%Y-%m-%d %H:%M', time_module.localtime(value))
        except:
            return '--'

    def basename(path):
        if path == '/':
            return '/'
        return path.rstrip('/').split('/')[-1] or '/'

    def exec_quick_cmd(cmd_text):
        if terminal_state['instance'] and terminal_state['instance'].active:
            terminal_state['instance'].channel.send(cmd_text + '\n')
            safe_notify(f'已发送: {cmd_text[:20]}...', 'positive')
        else:
            safe_notify('SSH 正在连接或已断开，请稍后重试', 'warning')

    def open_cmd_editor(existing_cmd=None):
        with ui.dialog() as edit_d, ui.card().classes(
                'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm border').style(
                'background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 10px 28px rgba(15,23,42,0.18);'):
            with ui.row().classes(
                    'w-full justify-between items-center px-5 py-4 border-b relative overflow-hidden').style(
                    'border-color: var(--xf-card-border); background: linear-gradient(to right, var(--xf-soft-bg), var(--xf-code-bg));'):
                ui.element('div').classes(
                    'absolute inset-0 bg-[url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9InRyYW5zcGFyZW50Ii8+PHJlY3Qgd2lkdGg9IjEiIGhlaWdodD0iMSIgZmlsbD0icmdiYSgzNCwyMTEsMjM4LDAuMDcpIi8+PC9zdmc+")] opacity-100 pointer-events-none')
                with ui.row().classes('items-center gap-3 z-10'):
                    with ui.element('div').classes(
                            'w-9 h-9 rounded-sm flex items-center justify-center border text-cyan-400 relative overflow-hidden').style(
                            'background: var(--xf-code-bg); border-color: var(--xf-card-border); box-shadow: 0 4px 12px rgba(15,23,42,0.12);'):
                        ui.element('div').classes('absolute inset-0 bg-cyan-400/10')
                        ui.icon('terminal').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                    ui.label('管理快捷命令').classes('text-lg font-black tracking-wide').style(
                        'color: var(--xf-text-strong);')
                ui.button(icon='close', on_click=edit_d.close).props('flat round dense color=grey').classes(
                    'z-10 text-slate-500').style('color: var(--xf-text-muted);')
            with ui.column().classes('w-full p-5 gap-4').style('background: var(--xf-bg-main);'):
                with ui.element('div').classes('w-full rounded-sm border px-3 py-2 transition-all').style(
                        'background: var(--xf-elevated-bg); border-color: var(--xf-card-border); box-shadow: 0 4px 12px rgba(15,23,42,0.10);'):
                    name_input = ui.input('按钮名称', value=existing_cmd['name'] if existing_cmd else '').classes(
                        'w-full').props(
                        'dense outlined dark color=cyan standout' if is_dark else 'dense outlined color=blue')
                with ui.element('div').classes(
                        'w-full rounded-sm border border-[#1e3a5f]/45 bg-[#08101d]/80 px-3 py-2 shadow-[0_0_8px_rgba(0,0,0,0.35)] transition-all hover:border-cyan-500/35' if is_dark else 'w-full rounded-sm border border-slate-300/90 bg-white px-3 py-2 shadow-[0_4px_12px_rgba(148,163,184,0.12)] transition-all hover:border-sky-400/60'):
                    cmd_input = ui.textarea('执行命令', value=existing_cmd['cmd'] if existing_cmd else '').classes(
                        'w-full').props(
                        'dense outlined dark color=cyan standout rows=4' if is_dark else 'dense outlined color=blue rows=4')

            async def save():
                name = name_input.value.strip()
                cmd = cmd_input.value.strip()
                if not name or not cmd:
                    return ui.notify('内容不能为空', type='negative')
                if 'quick_commands' not in ADMIN_CONFIG:
                    ADMIN_CONFIG['quick_commands'] = []
                if existing_cmd:
                    existing_cmd['name'] = name
                    existing_cmd['cmd'] = cmd
                else:
                    ADMIN_CONFIG['quick_commands'].append({'name': name, 'cmd': cmd, 'id': str(uuid.uuid4())[:8]})
                await save_admin_config()
                render_quick_commands.refresh()
                edit_d.close()

            async def delete_current():
                if existing_cmd and 'quick_commands' in ADMIN_CONFIG:
                    ADMIN_CONFIG['quick_commands'].remove(existing_cmd)
                    await save_admin_config()
                    render_quick_commands.refresh()
                    edit_d.close()

            with ui.row().classes('w-full justify-between items-center mt-2'):
                if existing_cmd:
                    ui.button('删除', icon='delete', on_click=delete_current).props('flat').classes(
                        'bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 hover:shadow-[0_0_12px_rgba(244,63,94,0.28)] px-5 font-black text-xs tracking-wide rounded-sm' if is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 px-5 font-black text-xs tracking-wide rounded-sm')
                else:
                    ui.element('div')
                ui.button('保存', icon='save', on_click=save).props('flat').classes(
                    'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 hover:shadow-[0_0_12px_rgba(34,211,238,0.32)] px-5 font-black text-xs tracking-wide rounded-sm' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 px-5 font-black text-xs tracking-wide rounded-sm')
        edit_d.open()

    @ui.refreshable
    def render_quick_commands():
        commands = ADMIN_CONFIG.get('quick_commands', [])
        with ui.row().classes('w-full gap-2 items-center flex-wrap'):
            ui.label('快捷命令').classes('text-xs font-bold mr-2 tracking-wide').style(
                'color: var(--xf-accent); opacity: 0.75;')
            for cmd_obj in commands:
                cmd_name = cmd_obj.get('name', '未命名')
                cmd_text = cmd_obj.get('cmd', '')
                with ui.element('div').classes(
                        'flex items-center rounded-sm overflow-hidden border transition-all').style(
                    'background: var(--xf-elevated-bg); border-color: var(--xf-card-border);'):
                    ui.button(cmd_name, on_click=lambda c=cmd_text: exec_quick_cmd(c)).props('flat').classes(
                        'bg-transparent text-[11px] font-bold px-3 py-1.5 rounded-none').style(
                        'color: var(--xf-text-strong);')
                    ui.element('div').classes('w-[1px] h-4 opacity-80').style('background: var(--xf-card-border);')
                    ui.button(icon='settings', on_click=lambda c=cmd_obj: open_cmd_editor(c)).props(
                        'flat dense size=xs').classes('px-1 py-1.5 rounded-none').style('color: var(--xf-text-muted);')
            ui.button(icon='add', on_click=lambda: open_cmd_editor(None)).props(
                'flat dense round size=sm color=cyan').style('color: var(--xf-accent);').tooltip('添加常用命令')

    async def ensure_tree_children(path, force=False):
        path = normalize_remote_path(path)
        if not force and path in tree_state['cache']:
            return
        tree_state['loading'].add(path)
        render_tree.refresh()
        try:
            entries = await list_remote_dir(server_conf, path)
            tree_state['cache'][path] = [e for e in entries if e.get('is_dir')]
        except Exception:
            tree_state['cache'][path] = []
        finally:
            tree_state['loading'].discard(path)
            render_tree.refresh()

    async def refresh_remote_dir(target_path=None):
        nonlocal path_input
        if target_path is not None:
            normalized = normalize_remote_path(target_path)
            file_state['current_path'] = normalized
            tree_state['selected'] = normalized
        file_state['loading'] = True
        render_file_list.refresh()
        try:
            file_state['entries'] = await list_remote_dir(server_conf, file_state['current_path'])
            await ensure_tree_children(file_state['current_path'], force=True)
            if path_input:
                path_input.value = file_state['current_path']
                path_input.update()
        except Exception as e:
            file_state['entries'] = []
            safe_notify(f'读取目录失败: {e}', 'negative')
        finally:
            file_state['loading'] = False
            render_file_list.refresh()
            render_tree.refresh()

    async def change_dir(target_path):
        target_path = normalize_remote_path(target_path)
        tree_state['expanded'].add(get_parent_remote_path(target_path))
        await refresh_remote_dir(target_path)

    async def go_parent_dir():
        await refresh_remote_dir(get_parent_remote_path(file_state['current_path']))

    async def toggle_tree_node(path):
        path = normalize_remote_path(path)
        if path in tree_state['expanded']:
            tree_state['expanded'].discard(path)
            render_tree.refresh()
            return
        tree_state['expanded'].add(path)
        await ensure_tree_children(path)
        render_tree.refresh()

    async def select_tree_node(path):
        await change_dir(path)

    async def handle_entry_open(entry):
        if entry.get('is_dir'):
            await change_dir(entry.get('path', '/'))
        else:
            await open_file_editor(entry)

    def detect_language(filename):
        ext = os.path.splitext(filename)[1].lower()
        mapping = {
            '.py': 'python', '.js': 'javascript', '.json': 'json',
            '.html': 'html', '.css': 'css', '.sh': 'shell',
            '.yaml': 'yaml', '.yml': 'yaml', '.xml': 'xml',
            '.sql': 'sql', '.md': 'markdown', '.conf': 'ini', '.ini': 'ini',
            '.service': 'ini', '.env': 'ini', '.vue': 'html', '.jsx': 'javascript',
        }
        return mapping.get(ext, 'plaintext')

    def switch_tab(path):
        if path not in editor_state['files']:
            return
        editor_state['active_path'] = path
        f_data = editor_state['files'][path]

        b64 = base64.b64encode(f_data['content'].encode('utf-8')).decode('utf-8')
        js = f'''
            if (window.editorInstance) {{
                window.isSwitchingTab = true;
                const text = decodeURIComponent(escape(window.atob("{b64}")));
                window.editorInstance.setValue(text);
                monaco.editor.setModelLanguage(window.editorInstance.getModel(), "{f_data['lang']}");
                window.isSwitchingTab = false;
            }}
        '''
        ui.run_javascript(js)
        if editor_state.get('refresh_tabs'):
            editor_state['refresh_tabs']()

    def close_tab(path):
        if path in editor_state['files']:
            del editor_state['files'][path]

        if not editor_state['files']:
            close_all()
            return

        if editor_state['active_path'] == path:
            switch_tab(list(editor_state['files'].keys())[0])
        else:
            if editor_state.get('refresh_tabs'):
                editor_state['refresh_tabs']()

    async def save_active_file():
        path = editor_state['active_path']
        if not path:
            return
        f_data = editor_state['files'][path]

        s_notify = ui.notification('正在保存...', timeout=0, spinner=True)
        try:
            await write_remote_file(server_conf, path, f_data['content'])
            f_data['saved_content'] = f_data['content']
            s_notify.dismiss()
            safe_notify(f'✅ {f_data["name"]} 已保存', 'positive')
            if editor_state.get('refresh_tabs'):
                editor_state['refresh_tabs']()
            await refresh_remote_dir(file_state['current_path'])
        except Exception as e:
            s_notify.dismiss()
            safe_notify(f'❌ 保存失败: {e}', 'negative')

    def close_all():
        if editor_state['dialog']:
            editor_state['dialog'].close()
        editor_state.update({'dialog': None, 'files': {}})
        ui.run_javascript('if(window.editorInstance){window.editorInstance.dispose(); window.editorInstance=null;}')

    async def open_file_editor(entry):
        remote_path = entry.get('path', '')
        if not is_probably_text_file(remote_path):
            safe_notify('该文件可能不是文本文件，请下载后本地编辑', 'warning')
            return

        client = ui.context.client

        if remote_path not in editor_state['files']:
            loading_notify = ui.notification(f'正在读取 {entry.get("name", basename(remote_path))}...', timeout=0,
                                             spinner=True)
            try:
                result = await read_remote_file(server_conf, remote_path)
                content = result.get('content', '')
            except Exception as e:
                loading_notify.dismiss()
                safe_notify(f'打开文件失败: {e}', 'negative')
                return
            loading_notify.dismiss()

            editor_state['files'][remote_path] = {
                'name': entry.get('name', basename(remote_path)),
                'content': content,
                'saved_content': content,
                'lang': detect_language(entry.get('name', remote_path)),
            }

        editor_state['active_path'] = remote_path

        if editor_state['dialog'] is not None:
            with client:
                switch_tab(remote_path)
            return

        with client:
            card_id = f'editor_card_{uuid.uuid4().hex[:8]}'
            header_id = f'editor_header_{uuid.uuid4().hex[:8]}'
            container_id = f'monaco_{uuid.uuid4().hex[:8]}'

            with ui.dialog().props('seamless') as editor_d:
                editor_state['dialog'] = editor_d

                with ui.card().props(f'id="{card_id}"').classes(
                        'flex flex-col p-0 border').style(
                    'background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 20px 50px rgba(15,23,42,0.30);') \
                        .style(
                    'width: 900px; max-width: 95vw; height: 650px; max-height: 95vh; resize: both; overflow: hidden; position: fixed; top: 10vh; left: 15vw; margin: 0;'):

                    with ui.row().props(f'id="{header_id}"').classes(
                            'w-full items-center justify-between cursor-move select-none flex-nowrap no-wrap shrink-0 border-b').style(
                        'min-height: 38px; padding-right: 8px; background: var(--xf-code-bg); border-color: var(--xf-card-border);'):

                        with ui.row().classes(
                                'flex-grow flex-nowrap overflow-x-auto no-scrollbar gap-0 h-full items-end'):
                            @ui.refreshable
                            def render_editor_tabs():
                                for p, f in editor_state['files'].items():
                                    is_active = (p == editor_state['active_path'])
                                    bg_color = 'var(--xf-panel-bg)' if is_active else 'var(--xf-code-bg)'
                                    txt_color = 'var(--xf-accent)' if is_active else 'var(--xf-text-muted)'
                                    border = 'var(--xf-accent)' if is_active else 'transparent'

                                    with ui.row().classes(
                                            'px-3 py-2 items-center gap-2 cursor-pointer border-r transition-colors flex-nowrap group').style(
                                        f'height: 100%; background: {bg_color}; border-right-color: var(--xf-card-border); border-top: 2px solid {border};'):
                                        ui.icon('description', size='xs').style(f'color: {txt_color};')
                                        ui.label(f['name']).classes(
                                            'text-[12px] truncate max-w-[180px] font-mono select-none').style(
                                            f'color: {txt_color};').on(
                                            'click', lambda _, path=p: switch_tab(path))

                                        if f['content'] != f['saved_content']:
                                            ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0')

                                        ui.icon('close', size='xs').classes(
                                            'opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer shrink-0').style(
                                            'color: var(--xf-text-muted);').on(
                                            'click', lambda _, path=p: close_tab(path))

                            editor_state['refresh_tabs'] = render_editor_tabs.refresh
                            render_editor_tabs()

                        with ui.row().classes('gap-2 shrink-0 items-center pl-2'):
                            ui.button('保存 (Save)', icon='save', on_click=save_active_file).props(
                                'flat dense').classes(
                                'font-bold px-3 py-1 rounded text-[12px]').style(
                                'color: #22c55e; background: var(--xf-soft-bg);')
                            ui.button('关闭 (Close)', icon='close', on_click=close_all).props('flat dense').classes(
                                'px-3 py-1 rounded text-[12px]').style(
                                'color: var(--xf-text-muted); background: var(--xf-soft-bg);')

                    with ui.element('div').classes('w-full relative flex-grow').style(
                            'min-height: 0; flex: 1 1 auto; background: var(--xf-panel-bg);'):
                        ui.element('div').props(f'id="{container_id}"').classes('absolute inset-0')

                    def on_sync(e):
                        if editor_state['active_path']:
                            editor_state['files'][editor_state['active_path']]['content'] = e.value
                            if editor_state.get('refresh_tabs'):
                                editor_state['refresh_tabs']()

                    ui.textarea().props('id="hidden-editor-sync"').classes('hidden').on_value_change(on_sync)
                    ui.button('ready', on_click=lambda: switch_tab(editor_state['active_path'])).props(
                        'id="monaco-ready-btn"').classes('hidden')

            editor_d.open()

            ui.run_javascript(f'''
                setTimeout(() => {{
                    const card = document.getElementById("{card_id}");
                    const header = document.getElementById("{header_id}");
                    const monacoContainer = document.getElementById("{container_id}");

                    if (card && header) {{
                        let isDragging = false;
                        let currentX = 0, currentY = 0;
                        let startX, startY;

                        card.style.transition = 'none';

                        header.addEventListener('mousedown', (e) => {{
                            if (e.target.closest('button') || e.target.closest('.group')) return;
                            isDragging = true;

                            const rect = card.getBoundingClientRect();
                            if (card.style.transform) card.style.transform = 'none';
                            if (card.style.position !== 'fixed') {{
                                card.style.position = 'fixed';
                                card.style.margin = '0';
                            }}
                            card.style.left = rect.left + 'px';
                            card.style.top = rect.top + 'px';
                            card.style.width = rect.width + 'px';
                            card.style.height = rect.height + 'px';

                            startX = e.clientX;
                            startY = e.clientY;
                            initialLeft = rect.left;
                            initialTop = rect.top;
                        }});

                        document.addEventListener('mousemove', (e) => {{
                            if (!isDragging) return;
                            e.preventDefault();
                            const dx = e.clientX - startX;
                            const dy = e.clientY - startY;
                            card.style.left = (initialLeft + dx) + 'px';
                            card.style.top = (initialTop + dy) + 'px';
                        }});

                        document.addEventListener('mouseup', () => {{ isDragging = false; }});

                        if (monacoContainer) {{
                            const resizeObserver = new ResizeObserver(() => {{
                                if (window.editorInstance) {{
                                    window.requestAnimationFrame(() => window.editorInstance.layout());
                                }}
                            }});
                            resizeObserver.observe(card);
                        }}
                    }}

                    if(window.editorInstance) {{
                        document.getElementById("monaco-ready-btn").click();
                        return;
                    }}

                    const initMonaco = () => {{
                        require.config({{ paths: {{ 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' }}}});
                        require(['vs/editor/editor.main'], function() {{
                            window.editorInstance = monaco.editor.create(document.getElementById('{container_id}'), {{
                                value: '',
                                language: 'plaintext',
                                theme: document.documentElement.classList.contains('light') ? 'vs' : 'vs-dark',
                                automaticLayout: true,
                                fontSize: 14,
                                minimap: {{ enabled: false }},
                                scrollBeyondLastLine: false,
                                wordWrap: "on"
                            }});

                            window.editorInstance.onDidChangeModelContent(() => {{
                                if(window.isSwitchingTab) return;
                                const val = window.editorInstance.getValue();
                                const hiddenArea = document.getElementById("hidden-editor-sync");
                                if(hiddenArea) {{
                                    hiddenArea.value = val;
                                    hiddenArea.dispatchEvent(new Event("input"));
                                }}
                            }});

                            document.getElementById("monaco-ready-btn").click();
                        }});
                    }};

                    if (!window.require) {{
                        const script = document.createElement('script');
                        script.src = 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/loader.min.js';
                        script.onload = initMonaco;
                        document.head.appendChild(script);
                    }} else {{
                        initMonaco();
                    }}
                }}, 150);
            ''')

    async def download_entry(entry):
        remote_path = entry.get('path', '')
        try:
            data = await download_remote_file(server_conf, remote_path)
            ui.download(data, entry.get('name') or os.path.basename(remote_path) or 'download.bin')
            safe_notify('开始下载文件', 'positive')
        except Exception as e:
            safe_notify(f'下载失败: {e}', 'negative')

    async def confirm_delete_entry(entry):
        target_name = entry.get('name', '未知目标')
        target_path = entry.get('path', '')
        target_type = '目录' if entry.get('is_dir') else '文件'
        with ui.dialog() as d, ui.card().classes(
                'w-[460px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm border').style(
                'background: var(--xf-panel-bg); border-color: #fda4af; box-shadow: 0 10px 28px rgba(15,23,42,0.18);'):
            with ui.column().classes('w-full p-5 gap-3 border-b').style(
                    'background: linear-gradient(to right, color-mix(in srgb, #fb7185 14%, var(--xf-panel-bg)), var(--xf-panel-bg)); border-color: #fda4af;'):
                with ui.row().classes('items-center gap-3 text-rose-400'):
                    with ui.element('div').classes(
                            'w-9 h-9 rounded-sm flex items-center justify-center bg-[#14070b] border border-rose-900/60 shadow-[0_0_8px_rgba(0,0,0,0.7)] relative overflow-hidden'):
                        ui.element('div').classes('absolute inset-0 bg-rose-400/10')
                        ui.icon('delete_sweep').classes('text-[18px] drop-shadow-[0_0_5px_currentColor]')
                    with ui.column().classes('gap-0'):
                        ui.label('删除确认').classes('text-lg font-black tracking-wide')
                        ui.label('目录将递归删除，操作不可恢复。').classes('text-[10px] text-slate-400 tracking-wide')
            with ui.column().classes('w-full p-5 gap-3').style('background: var(--xf-bg-main);'):
                ui.label(f'确定删除{target_type} [{target_name}] 吗？').classes('text-sm font-bold').style(
                    'color: var(--xf-text-strong);')

            async def do_delete():
                try:
                    await delete_remote_path(server_conf, target_path)
                    safe_notify(f'{target_type}已删除', 'positive')
                    d.close()
                    parent = get_parent_remote_path(target_path)
                    await ensure_tree_children(parent, force=True)
                    await ensure_tree_children(file_state['current_path'], force=True)
                    await refresh_remote_dir(file_state['current_path'])
                except Exception as e:
                    safe_notify(f'删除失败: {e}', 'negative')

            with ui.row().classes('w-full justify-end gap-2 p-4 border-t').style(
                    'border-color: #fda4af; background: color-mix(in srgb, #fb7185 10%, var(--xf-panel-bg));'):
                ui.button('取消', on_click=d.close).props('outline color=grey').classes(
                    'text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-white text-xs font-bold tracking-wide rounded-sm')
                ui.button('删除', icon='delete', on_click=do_delete).props('flat').classes(
                    'bg-rose-950/45 text-rose-300 border border-rose-500/45 hover:bg-rose-900/55 hover:shadow-[0_0_12px_rgba(244,63,94,0.28)] px-5 font-black text-xs tracking-wide rounded-sm' if is_dark else 'bg-rose-100 text-rose-700 border border-rose-300 hover:bg-rose-200 px-5 font-black text-xs tracking-wide rounded-sm')
        d.open()

    def open_create_dialog(kind):
        label = '文件夹' if kind == 'dir' else '文件'
        with ui.dialog() as d, ui.card().classes(
                'w-[420px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm border').style(
                'background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 10px 28px rgba(15,23,42,0.18);'):
            with ui.column().classes('w-full p-5 gap-3 border-b').style(
                    'background: linear-gradient(to right, var(--xf-soft-bg), var(--xf-code-bg)); border-color: var(--xf-card-border);'):
                ui.label(f'新建{label}').classes('text-lg font-black tracking-wide').style(
                    'color: var(--xf-text-strong);')
            with ui.column().classes('w-full p-5 gap-4').style('background: var(--xf-bg-main);'):
                with ui.element('div').classes('w-full rounded-sm border px-3 py-2 transition-all').style(
                        'background: var(--xf-elevated-bg); border-color: var(--xf-card-border); box-shadow: 0 4px 12px rgba(15,23,42,0.10);'):
                    name_input = ui.input('名称').classes('w-full').props(
                        'dense outlined dark color=cyan standout' if is_dark else 'dense outlined color=blue')

            async def create_target():
                name = (name_input.value or '').strip()
                if not name:
                    safe_notify('名称不能为空', 'warning')
                    return
                target_path = join_remote_path(file_state['current_path'], name)
                try:
                    if kind == 'dir':
                        await make_remote_dir(server_conf, target_path)
                        await ensure_tree_children(file_state['current_path'], force=True)
                    else:
                        await create_empty_remote_file(server_conf, target_path)
                    safe_notify(f'{label}创建成功', 'positive')
                    d.close()
                    await refresh_remote_dir(file_state['current_path'])
                except Exception as e:
                    safe_notify(f'创建失败: {e}', 'negative')

            with ui.row().classes('w-full justify-end gap-2 p-4 border-t').style(
                    'border-color: var(--xf-card-border); background: linear-gradient(to right, var(--xf-soft-bg), var(--xf-code-bg));'):
                ui.button('取消', on_click=d.close).props('outline color=grey').classes(
                    'text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-white text-xs font-bold tracking-wide rounded-sm')
                ui.button('创建', icon='add', on_click=create_target).props('flat').classes(
                    'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 hover:shadow-[0_0_12px_rgba(34,211,238,0.32)] px-5 font-black text-xs tracking-wide rounded-sm' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 px-5 font-black text-xs tracking-wide rounded-sm')
        d.open()

    def open_rename_dialog(entry):
        old_name = entry.get('name', '')
        old_path = entry.get('path', '')
        with ui.dialog() as d, ui.card().classes(
                'w-[420px] max-w-[92vw] p-0 gap-0 overflow-hidden rounded-sm border').style(
                'background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 10px 28px rgba(15,23,42,0.18);'):
            with ui.column().classes(
                    'w-full p-5 gap-3 bg-gradient-to-r from-[#0a1526] to-[#050a14] border-b border-[#1e3a5f]/60' if is_dark else 'w-full p-5 gap-3 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] border-b border-slate-300/90'):
                ui.label('重命名').classes(
                    'text-lg font-black text-slate-100 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide')
            with ui.column().classes('w-full p-5 gap-4').style('background: var(--xf-bg-main);'):
                with ui.element('div').classes('w-full rounded-sm border px-3 py-2 transition-all').style(
                        'background: var(--xf-elevated-bg); border-color: var(--xf-card-border); box-shadow: 0 4px 12px rgba(15,23,42,0.10);'):
                    new_name_input = ui.input('新名称', value=old_name).classes('w-full').props(
                        'dense outlined dark color=cyan standout' if is_dark else 'dense outlined color=blue')

            async def do_rename():
                new_name = new_name_input.value.strip()
                if not new_name or new_name == old_name:
                    d.close()
                    return
                new_path = join_remote_path(get_parent_remote_path(old_path), new_name)
                try:
                    await rename_remote_path(server_conf, old_path, new_path)
                    safe_notify(f'重命名成功: {new_name}', 'positive')
                    d.close()
                    await refresh_remote_dir(file_state['current_path'])
                except Exception as e:
                    safe_notify(f'重命名失败: {e}', 'negative')

            with ui.row().classes('w-full justify-end gap-2 p-4 border-t').style(
                    'border-color: var(--xf-card-border); background: linear-gradient(to right, var(--xf-soft-bg), var(--xf-code-bg));'):
                ui.button('取消', on_click=d.close).props('outline color=grey').classes(
                    'text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-white text-xs font-bold tracking-wide rounded-sm')
                ui.button('确认', on_click=do_rename).props('flat').classes(
                    'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 hover:shadow-[0_0_12px_rgba(34,211,238,0.32)] px-5 font-black text-xs tracking-wide rounded-sm' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 px-5 font-black text-xs tracking-wide rounded-sm')
        d.open()

    def open_chmod_dialog(entry):
        target_path = entry.get('path', '')
        filename = entry.get('name', '')
        current_mode_str = entry.get('mode', '----------')

        owner_r = len(current_mode_str) > 1 and current_mode_str[1] == 'r'
        owner_w = len(current_mode_str) > 2 and current_mode_str[2] == 'w'
        owner_x = len(current_mode_str) > 3 and current_mode_str[3] in ('x', 's', 'S')

        group_r = len(current_mode_str) > 4 and current_mode_str[4] == 'r'
        group_w = len(current_mode_str) > 5 and current_mode_str[5] == 'w'
        group_x = len(current_mode_str) > 6 and current_mode_str[6] in ('x', 's', 'S')

        other_r = len(current_mode_str) > 7 and current_mode_str[7] == 'r'
        other_w = len(current_mode_str) > 8 and current_mode_str[8] == 'w'
        other_x = len(current_mode_str) > 9 and current_mode_str[9] in ('x', 't', 'T')

        with ui.dialog() as d, ui.card().classes(
                'w-[360px] p-0 border overflow-hidden rounded-sm').style(
            'background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 10px 28px rgba(15,23,42,0.18);'):
            with ui.row().classes(
                    'w-full items-center justify-between px-4 py-2 border-b').style(
                'background: linear-gradient(to right, var(--xf-soft-bg), var(--xf-code-bg)); border-color: var(--xf-card-border);'):
                with ui.row().classes('items-center gap-2'):
                    ui.element('div').classes('w-3 h-3 rounded-full bg-[#ff5f56]')
                    ui.element('div').classes('w-3 h-3 rounded-full bg-[#ffbd2e]')
                    ui.element('div').classes('w-3 h-3 rounded-full bg-[#27c93f]')
                    ui.label('修改文件权限').classes('text-xs font-bold ml-2 tracking-wide').style(
                        'color: var(--xf-accent);')
                ui.button(icon='close', on_click=d.close).props('flat round dense size=xs color=grey').classes(
                    'text-slate-400 hover:text-cyan-400 hover:bg-cyan-950/30' if is_dark else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100')

            with ui.column().classes('w-full p-5 gap-0').style('background: var(--xf-bg-main);'):
                ui.label(filename).classes(
                    'text-xl font-bold mb-4 truncate w-full border-b pb-2').style(
                    'color: var(--xf-text-strong); border-color: var(--xf-card-border);')

                state = {
                    'owner': {'r': owner_r, 'w': owner_w, 'x': owner_x},
                    'group': {'r': group_r, 'w': group_w, 'x': group_x},
                    'other': {'r': other_r, 'w': other_w, 'x': other_x},
                }

                def make_checkbox_group(title, key):
                    with ui.column().classes('w-full gap-1 mb-4'):
                        ui.label(title).classes('text-xs').style('color: var(--xf-text-muted);')
                        with ui.row().classes(
                                'w-full gap-6 px-3 py-2 rounded-md border items-center justify-start').style(
                            'background: var(--xf-elevated-bg); border-color: var(--xf-card-border);'):
                            state[key]['r_chk'] = ui.checkbox('读取', value=state[key]['r']).classes('text-sm').style(
                                'color: var(--xf-text-strong);')
                            state[key]['w_chk'] = ui.checkbox('写入', value=state[key]['w']).classes('text-sm').style(
                                'color: var(--xf-text-strong);')
                            state[key]['x_chk'] = ui.checkbox('执行', value=state[key]['x']).classes('text-sm').style(
                                'color: var(--xf-text-strong);')

                make_checkbox_group('所有者 (Owner)', 'owner')
                make_checkbox_group('组 (Group)', 'group')
                make_checkbox_group('其他 (Others)', 'other')

                async def do_chmod():
                    calc = lambda k: (4 if state[k]['r_chk'].value else 0) + (2 if state[k]['w_chk'].value else 0) + (
                        1 if state[k]['x_chk'].value else 0)

                    new_mode = f"{calc('owner')}{calc('group')}{calc('other')}"

                    s_notify = ui.notification(f'正在修改权限为 {new_mode}...', timeout=0, spinner=True)
                    try:
                        cmd = f"chmod {new_mode} '{target_path}'"
                        success, output = await _ssh_exec_wrapper(server_conf, cmd)
                        s_notify.dismiss()
                        if success:
                            safe_notify(f'权限已更新: {new_mode}', 'positive')
                            d.close()
                            await refresh_remote_dir(file_state['current_path'])
                        else:
                            safe_notify(f'修改失败: {output}', 'negative')
                    except Exception as e:
                        s_notify.dismiss()
                        safe_notify(f'修改报错: {e}', 'negative')

                with ui.row().classes('w-full justify-center gap-4 mt-2'):
                    ui.button('确定', on_click=do_chmod).props('flat').classes(
                        'bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 hover:shadow-[0_0_12px_rgba(34,211,238,0.32)] w-24 font-black text-xs tracking-wide rounded-sm' if is_dark else 'bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 w-24 font-black text-xs tracking-wide rounded-sm')
                    ui.button('取消', on_click=d.close).props('outline color=grey').classes(
                        'text-slate-300 border-slate-600 hover:bg-slate-800/40 w-24 font-black text-xs tracking-wide rounded-sm' if is_dark else 'text-slate-600 border-slate-300 hover:bg-slate-100 w-24 font-black text-xs tracking-wide rounded-sm')

        d.open()

    async def handle_direct_upload(e):
        try:
            remote_path = join_remote_path(file_state['current_path'], e.name)

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(e.content.read())
                tmp_path = tmp.name

            await upload_remote_file(server_conf, tmp_path, remote_path)

            os.remove(tmp_path)
            safe_notify(f'✅ {e.name} 上传成功', 'positive')
        except Exception as ex:
            safe_notify(f'❌ 上传失败: {ex}', 'negative')
        finally:
            await refresh_remote_dir(file_state['current_path'])

    def make_open_handler(entry):
        async def handler(e=None):
            await handle_entry_open(entry)

        return handler

    def make_edit_handler(entry):
        async def handler(e=None):
            await open_file_editor(entry)

        return handler

    def make_download_handler(entry):
        async def handler(e=None):
            await download_entry(entry)

        return handler

    def make_delete_handler(entry):
        async def handler(e=None):
            await confirm_delete_entry(entry)

        return handler

    def make_rename_handler(entry):
        async def handler(e=None):
            open_rename_dialog(entry)

        return handler

    def make_chmod_handler(entry):
        async def handler(e=None):
            open_chmod_dialog(entry)

        return handler

    @ui.refreshable
    def render_tree():
        def node(path, depth=0):
            display_name = basename(path)
            is_selected = tree_state['selected'] == path
            is_expanded = path in tree_state['expanded']
            children = tree_state['cache'].get(path, []) if is_expanded else []
            loading = path in tree_state['loading']

            row_classes = 'w-full items-center gap-1 px-2 py-2 rounded-sm cursor-pointer transition-colors no-wrap'
            row_classes += ' bg-transparent'

            with ui.column().classes('w-full gap-0'):
                with ui.row().classes(row_classes).style(
                        f'padding-left: {5 + depth * 16}px; background: {"var(--xf-soft-bg)" if is_selected else "transparent"};'):
                    ui.button(icon='expand_more' if is_expanded else 'chevron_right',
                              on_click=lambda _, p=path: toggle_tree_node(p)).props(
                        'flat dense round size=xs color=grey').classes('!min-w-0 !p-0 opacity-80 shrink-0')

                    ui.icon('folder_open' if is_expanded else 'folder').classes('text-amber-400 text-[18px] shrink-0')
                    ui.label(display_name).classes('text-[14px] cursor-pointer select-none truncate').style(
                        'color: var(--xf-text-strong);').on(
                        'click', lambda _, p=path: select_tree_node(p))

                if loading:
                    ui.label('加载中...').classes('text-[12px] ml-8 py-0.5').style('color: var(--xf-text-muted);')
                if is_expanded:
                    sorted_children = sorted(children, key=lambda x: x.get('name', '').lower())
                    for child in sorted_children:
                        node(child.get('path', '/'), depth + 1)

        with ui.column().classes('w-full gap-0 p-1 h-full overflow-hidden flex-nowrap').style(
                'background: var(--xf-code-bg);'):
            node('/')

    @ui.refreshable
    def render_file_list():
        entries = file_state.get('entries', [])
        sorted_entries = sorted(entries, key=lambda x: (not x.get('is_dir'), x.get('name', '').lower()))

        with ui.column().classes('w-full gap-0 h-full overflow-hidden flex-nowrap').style(
                'background: var(--xf-panel-bg);'):
            with ui.row().classes(
                    'w-full items-center px-2 py-2 text-[13px] border-b flex-nowrap no-wrap tracking-wider').style(
                'color: var(--xf-text-muted); border-color: var(--xf-card-border); background: var(--xf-soft-bg);'):
                ui.label('文件名').classes('w-[26%] border-r pl-2 truncate font-bold').style(
                    'border-color: var(--xf-card-border);')
                ui.label('大小').classes('w-[12%] border-r pl-2 truncate font-bold').style(
                    'border-color: var(--xf-card-border);')
                ui.label('类型').classes('w-[12%] border-r pl-2 truncate font-bold').style(
                    'border-color: var(--xf-card-border);')
                ui.label('修改时间').classes('w-[20%] border-r pl-2 truncate font-bold').style(
                    'border-color: var(--xf-card-border);')
                ui.label('权限').classes('w-[13%] border-r pl-2 truncate font-bold').style(
                    'border-color: var(--xf-card-border);')
                ui.label('用户/用户组').classes('w-[17%] pl-2 truncate font-bold')

            if file_state.get('loading'):
                with ui.column().classes('w-full items-center justify-center py-10 text-slate-500'):
                    ui.spinner('dots', size='2rem', color='primary')
                    ui.label('正在读取远程目录...').classes('text-sm')
                return

            if not sorted_entries:
                with ui.column().classes('w-full items-center justify-center py-10 text-slate-500'):
                    ui.icon('folder_off').classes('text-3xl')
                    ui.label('当前目录为空').classes('text-sm')
                return

            for index, item in enumerate(sorted_entries):
                is_dir = item.get('is_dir', False)
                row_classes = 'w-full items-center px-2 py-2.5 cursor-default transition-colors flex-nowrap no-wrap'

                with ui.row().classes(row_classes) as row:
                    with ui.context_menu().classes(
                            'text-[14px] font-bold min-w-[140px] border').style(
                        'background: var(--xf-panel-bg); color: var(--xf-text-strong); border-color: var(--xf-card-border);'):
                        if is_dir:
                            ui.menu_item('📂 打开 (Open)', on_click=make_open_handler(item)).classes(
                                'hover:bg-slate-700 py-1.5' if is_dark else 'hover:bg-sky-50 py-1.5')
                            ui.separator().classes('bg-slate-600')
                            ui.menu_item('✏️ 重命名 (Rename)', on_click=make_rename_handler(item)).classes(
                                'hover:bg-slate-700 py-1.5' if is_dark else 'hover:bg-sky-50 py-1.5')
                            ui.menu_item('🔑 权限 (Chmod)', on_click=make_chmod_handler(item)).classes(
                                'hover:bg-slate-700 py-1.5' if is_dark else 'hover:bg-sky-50 py-1.5')
                            ui.separator().classes('bg-slate-600')
                            ui.menu_item('🗑️ 删除 (Delete)', on_click=make_delete_handler(item)).classes(
                                'text-red-400 hover:bg-slate-700 py-1.5' if is_dark else 'text-rose-600 hover:bg-rose-50 py-1.5')
                        else:
                            ui.menu_item('📝 打开 / 编辑', on_click=make_edit_handler(item)).classes(
                                'hover:bg-slate-700 py-1.5' if is_dark else 'hover:bg-sky-50 py-1.5')
                            ui.menu_item('⬇️ 下载 (Download)', on_click=make_download_handler(item)).classes(
                                'hover:bg-slate-700 py-1.5' if is_dark else 'hover:bg-sky-50 py-1.5')
                            ui.separator().classes('bg-slate-600')
                            ui.menu_item('✏️ 重命名 (Rename)', on_click=make_rename_handler(item)).classes(
                                'hover:bg-slate-700 py-1.5' if is_dark else 'hover:bg-sky-50 py-1.5')
                            ui.menu_item('🔑 权限 (Chmod)', on_click=make_chmod_handler(item)).classes(
                                'hover:bg-slate-700 py-1.5' if is_dark else 'hover:bg-sky-50 py-1.5')
                            ui.separator().classes('bg-slate-600')
                            ui.menu_item('🗑️ 删除 (Delete)', on_click=make_delete_handler(item)).classes(
                                'text-red-400 hover:bg-slate-700 py-1.5' if is_dark else 'text-rose-600 hover:bg-rose-50 py-1.5')

                    with ui.row().classes('w-[26%] items-center gap-2 min-w-0 flex-nowrap no-wrap pl-2'):
                        icon_name = 'folder' if is_dir else 'description'
                        icon_color = 'text-amber-400' if is_dir else 'text-cyan-400'
                        ui.icon(icon_name).classes(f'{icon_color} text-[18px] shrink-0')
                        ui.label(item.get('name', '')).classes('truncate text-[14px]').style(
                            'color: var(--xf-text-strong);')

                    size_str = '' if is_dir else format_file_size(item.get('size', 0))
                    ui.label(size_str).classes('w-[12%] text-[13px] pl-2 truncate').style(
                        'color: var(--xf-text-muted);')

                    type_str = '文件夹' if is_dir else '文件'
                    ui.label(type_str).classes('w-[12%] text-[13px] pl-2 truncate').style(
                        'color: var(--xf-text-muted);')

                    ui.label(format_mtime(item.get('mtime', 0))).classes('w-[20%] text-[13px] pl-2 truncate').style(
                        'color: var(--xf-text-subtle);')

                    ui.label(item.get('mode', '--')).classes('w-[13%] text-[13px] font-mono pl-2 truncate').style(
                        'color: var(--xf-text-muted);')

                    owner_str = item.get('owner', 'root/root')
                    ui.label(owner_str).classes('w-[17%] text-[13px] pl-2 truncate').style(
                        'color: var(--xf-text-muted);')

                row.on('dblclick', make_open_handler(item))

    with content_container:
        # 外层容器：overflow-hidden 保证总高度不变
        with ui.column().classes(
                'w-full max-w-[1440px] mx-auto h-full flex flex-col gap-0 flex-nowrap overflow-hidden').props(
                f'id="{outer_column_id}"'):

            # ── SSH 终端卡片 ──────────────────────────────────────────
            with ui.card().classes(
                    'w-full p-0 rounded-sm border border-t-[3px] overflow-hidden flex flex-col flex-shrink-0').style(
                'background: var(--xf-panel-bg); border-color: var(--xf-card-border); border-top-color: var(--xf-accent); box-shadow: 0 10px 28px rgba(15,23,42,0.18);'):

                conn_state = {'connected': True}

                async def _do_reconnect():
                    cleanup_ssh_route_terminal(server_key)
                    safe_notify('⚡️ 正在重新连接 SSH...', 'ongoing')
                    await _start_terminal(terminal_box)

                def _do_disconnect():
                    cleanup_ssh_route_terminal(server_key)
                    terminal_box.clear()
                    with terminal_box:
                        with ui.column().classes('w-full h-full items-center justify-center text-slate-500 gap-2'):
                            ui.icon('link_off', size='3rem').classes('text-slate-600')
                            ui.label('SSH 已手动断开').classes('text-sm font-bold tracking-wider')
                    safe_notify('⛓️‍💥 SSH 连接已掐断', 'warning')

                def toggle_connection():
                    if conn_state['connected']:
                        _do_disconnect()
                        conn_state['connected'] = False
                    else:
                        asyncio.create_task(_do_reconnect())
                        conn_state['connected'] = True
                    render_conn_btn.refresh()

                @ui.refreshable
                def render_conn_btn():
                    if conn_state['connected']:
                        btn = ui.button(icon='bolt', on_click=toggle_connection).props(
                            'flat dense round size=sm color=positive').classes('p-1 m-0 min-h-0 min-w-0 transition-all')
                        btn.tooltip('点击断开 SSH')
                    else:
                        btn = ui.button(icon='link_off', on_click=toggle_connection).props(
                            'flat dense round size=sm color=negative').classes('p-1 m-0 min-h-0 min-w-0 transition-all')
                        btn.tooltip('点击重连 SSH')

                with ui.row().classes(
                        'w-full items-center justify-between px-4 py-3 border-b').style(
                    'border-color: var(--xf-card-border); background: linear-gradient(to right, var(--xf-soft-bg), var(--xf-code-bg));'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('terminal').classes('drop-shadow-[0_0_5px_rgba(34,211,238,0.8)]').style(
                            'color: var(--xf-accent);')
                        with ui.column().classes('gap-0'):
                            raw_host = server_conf.get('ssh_host') or \
                                       server_conf.get('url', '').replace('http://', '').replace('https://', '').split(
                                           ':')[0]
                            display_ip = raw_host
                            if raw_host and not (':' in raw_host or raw_host.replace('.', '').isdigit()):
                                try:
                                    display_ip = await asyncio.wait_for(run.io_bound(_sync_resolve_ip, raw_host),
                                                                        timeout=1.5)
                                except:
                                    display_ip = raw_host

                            ui.label(f"SSH Console · {server_conf.get('ssh_user', 'root')}@{display_ip}").classes(
                                'font-black tracking-wide').style('color: var(--xf-text-strong);')
                            ui.label(server_conf.get('name', '未命名服务器')).classes('text-xs').style(
                                'color: var(--xf-accent); opacity: 0.75;')

                    with ui.row().classes('items-center gap-2'):
                        render_conn_btn()
                        ui.button('返回详情', icon='arrow_back', on_click=_back_to_detail).props(
                            'flat size=sm').classes(
                            'px-4 py-1.5 font-bold text-[11px] rounded-sm transition-all border').style(
                            'background: var(--xf-soft-bg); border-color: var(--xf-card-border); color: var(--xf-accent);')

                # 终端容器：加上唯一 ID，供 JS 拖拽调整高度
                terminal_box = ui.element('div').classes('w-full overflow-hidden').props(
                    f'id="{terminal_box_id}"').style(
                    'height: 462px; min-height: 160px; position: relative; background: var(--xf-code-bg);')
                with terminal_box:
                    with ui.column().classes('w-full h-full items-center justify-center text-slate-500'):
                        ui.label('正在初始化 SSH 终端...').classes('text-sm')

            # ── 拖拽把手 ─────────────────────────────────────────────
            with ui.element('div').props(f'id="{resize_handle_id}"').classes(
                    'w-full flex items-center justify-center cursor-row-resize select-none flex-shrink-0').style(
                'height: 10px; margin-top: 4px; background: var(--xf-soft-bg); '
                'border-top: 1px solid var(--xf-card-border); border-bottom: 1px solid var(--xf-card-border);'):
                ui.element('div').classes('w-20 h-1 rounded-full').style(
                    'background: var(--xf-accent); opacity: 0.45;')

            # ── 快捷命令卡片 ──────────────────────────────────────────
            with ui.card().classes(
                    'w-full px-4 py-2 rounded-sm border overflow-hidden flex flex-col flex-shrink-0').style(
                'margin-top: 4px; background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 10px 28px rgba(15,23,42,0.18);'):
                render_quick_commands()

            # ── 文件浏览器卡片：JS 动态计算高度填满剩余空间 ───────────
            with ui.card().props(f'id="{file_panel_id}"').classes(
                    'w-full flex-1 min-h-0 p-0 rounded-sm border overflow-hidden flex flex-col').style(
                'min-height: 280px; margin-top: 4px; background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 10px 28px rgba(15,23,42,0.18);'):
                with ui.row().classes(
                        'w-full items-center justify-between px-3 py-2 border-b gap-2 flex-nowrap').style(
                    'background: linear-gradient(to right, var(--xf-soft-bg), var(--xf-code-bg)); border-color: var(--xf-card-border);'):
                    path_input = ui.input(value=file_state['current_path']).classes(
                        'flex-grow text-xs h-8 min-w-[200px]').props(
                        'dense outlined dark color=cyan standout' if is_dark else 'dense outlined color=blue')

                    with ui.row().classes('items-center gap-1 flex-nowrap no-wrap'):
                        ui.button('历史').props('outline dense size=sm color=grey').classes(
                            'h-7 hidden sm:block rounded-sm').style(
                            'color: var(--xf-text-muted); border-color: var(--xf-card-border);')
                        ui.button(icon='refresh',
                                  on_click=lambda: refresh_remote_dir(file_state['current_path'])).props(
                            'flat dense size=sm color=grey').classes('h-7 w-7 rounded-sm').style(
                            'color: var(--xf-text-muted);').tooltip('刷新')
                        ui.button(icon='arrow_upward', on_click=go_parent_dir).props(
                            'flat dense size=sm color=grey').classes('h-7 w-7 rounded-sm').style(
                            'color: var(--xf-text-muted);').tooltip('返回上级')

                        hidden_uploader = ui.upload(on_upload=handle_direct_upload, multiple=True).props(
                            'auto-upload').style('display: none;')
                        ui.button(icon='file_upload', on_click=lambda: ui.run_javascript(
                            f'document.getElementById("c{hidden_uploader.id}").querySelector("input[type=file]").click()')).props(
                            'flat dense size=sm color=grey').classes('h-7 w-7 rounded-sm').style(
                            'color: var(--xf-text-muted);').tooltip('上传文件')

                        ui.button(icon='create_new_folder', on_click=lambda: open_create_dialog('dir')).props(
                            'flat dense size=sm color=grey').classes('h-7 w-7 rounded-sm').style(
                            'color: #10b981;').tooltip('新建目录')
                        ui.button(icon='note_add', on_click=lambda: open_create_dialog('file')).props(
                            'flat dense size=sm color=grey').classes('h-7 w-7 rounded-sm').style(
                            'color: var(--xf-accent);').tooltip('新建文件')

                with ui.row().classes('w-full min-h-0 flex-grow flex-nowrap no-wrap gap-0'):
                    with ui.column().classes('w-[25%] min-w-[150px] h-full border-r').style(
                            'border-right-color: var(--xf-card-border); background: var(--xf-code-bg);'):
                        with ui.scroll_area().classes('w-full h-full'):
                            render_tree()

                    with ui.column().classes('w-[75%] h-full').style('background: var(--xf-panel-bg);'):
                        with ui.scroll_area().classes('w-full h-full'):
                            render_file_list()

    _server_dialog.logger.info(f"[SingleSSHRoute] page opened | key={server_key}")

    # ── 拖拽 + 高度锁定 JS（基于视口位置计算，彻底绕开 flex 链断裂问题）─
    ui.run_javascript(f'''
        setTimeout(() => {{
            const handle        = document.getElementById("{resize_handle_id}");
            const termBox       = document.getElementById("{terminal_box_id}");
            const outerCol      = document.getElementById("{outer_column_id}");
            const filePanelCard = document.getElementById("{file_panel_id}");

            if (!handle || !termBox || !filePanelCard || !outerCol) {{
                console.warn('XF-resize: element not found', {{handle, termBox, filePanelCard, outerCol}});
                return;
            }}

            // ① 锁定 outerCol 的高度 = 视口高度 - outerCol 距视口顶部的距离 - 底部留白
            //    底部留白 = 16px(padding) + 快捷命令卡片高度，让页面底部留出一段空余
            function lockOuterHeight() {{
                const top     = outerCol.getBoundingClientRect().top;
                const cmdCard = filePanelCard.previousElementSibling;
                const cmdH    = cmdCard ? cmdCard.getBoundingClientRect().height : 40;
                const h       = window.innerHeight - top - 16 - cmdH / 2;
                outerCol.style.height    = h + 'px';
                outerCol.style.maxHeight = h + 'px';
                outerCol.style.overflow  = 'hidden';
            }}

            // ② 在 outerCol 高度已知后，把剩余空间全部分给文件面板
            function recalcFilePanelHeight() {{
                // 用 outerCol 的实际高度（已被锁定）
                const outerH = outerCol.getBoundingClientRect().height;

                // SSH 终端卡片（termBox 的父级 q-card）
                const sshCard  = termBox.closest('.q-card') || termBox.parentElement;
                const sshCardH = sshCard  ? sshCard.getBoundingClientRect().height  : termBox.getBoundingClientRect().height;

                // 拖拽把手
                const handleH  = handle.getBoundingClientRect().height;

                // 快捷命令卡片（filePanelCard 往上数第一个兄弟）
                const cmdCard  = filePanelCard.previousElementSibling;
                const cmdH     = cmdCard ? cmdCard.getBoundingClientRect().height : 0;

                // 3 段 margin-top: 4px
                const gaps    = 4 * 3;
                const finalH  = Math.max(280, outerH - sshCardH - handleH - cmdH - gaps);

                filePanelCard.style.height    = finalH + 'px';
                filePanelCard.style.minHeight = finalH + 'px';
                filePanelCard.style.maxHeight = finalH + 'px';
            }}

            // 初始化：先锁外层再算内层
            lockOuterHeight();
            recalcFilePanelHeight();

            // ③ 拖拽逻辑
            let dragging = false, startY = 0, startH = 0;

            handle.addEventListener('mousedown', (e) => {{
                dragging = true;
                startY   = e.clientY;
                startH   = termBox.getBoundingClientRect().height;
                document.body.style.cursor     = 'row-resize';
                document.body.style.userSelect = 'none';
                e.preventDefault();
            }});

            document.addEventListener('mousemove', (e) => {{
                if (!dragging) return;
                // 拖拽时终端高度上限 = outerCol高度 - 最小文件面板(280) - 把手 - 快捷命令 - 间距
                const outerH  = outerCol.getBoundingClientRect().height;
                const cmdCard = filePanelCard.previousElementSibling;
                const cmdH    = cmdCard ? cmdCard.getBoundingClientRect().height : 0;
                const maxTerm = outerH - handle.getBoundingClientRect().height - cmdH - 280 - 4 * 3;
                const newH    = Math.max(160, Math.min(maxTerm, startH + (e.clientY - startY)));

                termBox.style.height    = newH + 'px';
                termBox.style.minHeight = newH + 'px';
                recalcFilePanelHeight();
                if (window.fitAddon) window.fitAddon.fit();
            }});

            document.addEventListener('mouseup', () => {{
                if (!dragging) return;
                dragging = false;
                document.body.style.cursor     = '';
                document.body.style.userSelect = '';
                recalcFilePanelHeight();
            }});

            // ④ 窗口大小变化时重新锁定并重算
            window.addEventListener('resize', () => {{
                lockOuterHeight();
                recalcFilePanelHeight();
            }});

        }}, 50);
    ''')
    
    ui.timer(0.05, lambda: _start_terminal(terminal_box), once=True)
    ui.timer(0.05, lambda: ensure_tree_children('/'), once=True)
    ui.timer(0.05, lambda: refresh_remote_dir('/'), once=True)


__all__ = ['cleanup_ssh_route_terminal', 'render_single_ssh_view']
