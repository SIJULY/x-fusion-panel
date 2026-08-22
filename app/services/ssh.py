import asyncio
import base64
import socket
import uuid

import asyncssh
from nicegui import app, ui

from app.storage.repositories import load_global_key

ssh_instances = {}


async def get_ssh_client(server_data):
    """建立 SSH 连接 (AsyncSSH)"""
    raw_url = server_data.get('url', '')
    host = ''
    if raw_url:
        if '://' in raw_url:
            host = raw_url.split('://')[-1].split(':')[0]
        else:
            host = raw_url.split(':')[0]

    if server_data.get('ssh_host'):
        host = server_data['ssh_host']

    if not host:
        return None, '❌ 连接失败: 缺少 SSH 主机地址'

    port = int(server_data.get('ssh_port') or 22)
    user = server_data.get('ssh_user') or 'root'
    auth_type = server_data.get('ssh_auth_type', '全局密钥').strip()

    print(f"🔌 [SSH Debug] 连接目标: {host}, 用户: {user}, 认证方式: [{auth_type}]", flush=True)

    options = {
        'host': host,
        'port': port,
        'username': user,
        'known_hosts': None,
        'client_keys': [],
    }

    try:
        if auth_type == '独立密码':
            pwd = server_data.get('ssh_password', '')
            if not pwd:
                raise Exception("选择了独立密码，但密码为空")
            options['password'] = pwd

        elif auth_type == '独立密钥':
            key_content = server_data.get('ssh_key', '')
            if not key_content:
                raise Exception("选择了独立密钥，但密钥为空")
            try:
                options['client_keys'] = [asyncssh.import_private_key(key_content)]
            except Exception as e:
                raise Exception(f"无法识别的私钥格式: {e}")

        else:
            g_key = await load_global_key()
            if not g_key:
                raise Exception("全局密钥未配置")
            try:
                options['client_keys'] = [asyncssh.import_private_key(g_key)]
            except Exception as e:
                raise Exception(f"全局密钥格式无法识别: {e}")

        conn = await asyncio.wait_for(asyncssh.connect(**options), timeout=5.0)
        return conn, f"✅ 已连接 {user}@{host}"

    except asyncio.TimeoutError:
        return None, "❌ 连接失败: 连接超时，请检查服务器IP、端口及防火墙设置"
    except asyncssh.PermissionDenied:
        return None, "❌ 连接失败: 认证失败：密码或密钥错误"
    except asyncssh.KeyImportError as e:
        return None, f"❌ 连接失败: 密钥导入失败 ({e})"
    except Exception as e:
        detail = str(e).strip() or repr(e)
        if "Connection refused" in detail:
            detail = "连接被拒绝，请检查SSH端口是否正确"
        return None, f"❌ 连接失败: {detail}"


# 兼容之前的名字，防止其他地方报错，现在直接返回 await get_ssh_client
async def get_ssh_client_sync(server_data):
    return await get_ssh_client(server_data)


class WebSSH:
    def __init__(self, container, server_data, initial_command=None):
        self.container = container
        self.server_data = server_data
        self.initial_command = (initial_command or '').strip()
        self.client = None
        self.process = None
        self.active = False
        self.term_id = f'term_{uuid.uuid4().hex}'
        self._last_cols = None
        self._last_rows = None
        self._resize_debounce_task = None
        self._pending_resize = None

    def _schedule_resize_pty(self, cols, rows):
        try:
            cols = int(cols)
            rows = int(rows)
        except Exception:
            return

        if cols < 2 or rows < 1:
            return

        self._pending_resize = (cols, rows)
        if self._resize_debounce_task and not self._resize_debounce_task.done():
            self._resize_debounce_task.cancel()
        self._resize_debounce_task = asyncio.create_task(self._apply_resize_pty_debounced())

    async def _apply_resize_pty_debounced(self):
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            return

        pending = self._pending_resize
        if not pending:
            return

        cols, rows = pending
        if self._last_cols == cols and self._last_rows == rows:
            return

        self._last_cols = cols
        self._last_rows = rows

        if self.process and self.active:
            try:
                self.process.change_terminal_size(cols, rows)
            except Exception:
                pass

    def _apply_pending_resize_now(self):
        if not self._pending_resize:
            return
        cols, rows = self._pending_resize
        self._last_cols = cols
        self._last_rows = rows
        if self.process:
            try:
                self.process.change_terminal_size(cols, rows)
            except Exception:
                pass

    def _handle_resize_event(self, e):
        detail = e.args.get('detail') if isinstance(e.args, dict) and 'detail' in e.args else e.args
        if not isinstance(detail, dict):
            return
        self._schedule_resize_pty(detail.get('cols'), detail.get('rows'))

    async def connect(self):
        with self.container:
            try:
                is_dark = bool(app.storage.user.get('is_dark', True))
                
                term_bg = '#000000' if is_dark else '#ffffff'
                term_fg = '#ffffff' if is_dark else '#0f172a'
                term_cursor = '#22d3ee' if is_dark else '#2563eb'
                term_selection = 'rgba(34, 211, 238, 0.28)' if is_dark else 'rgba(37, 99, 235, 0.18)'

                term_container = ui.element('div').props(f'id={self.term_id}').classes(
                    'w-full h-full rounded overflow-hidden relative').style(
                    'min-height: 0; height: 100%; width: 100%; display: block; position: relative; background: transparent; color: inherit;')

                init_js = f"""
                try {{
                    if (window.{self.term_id}) {{
                        if (typeof window.{self.term_id}.dispose === 'function') {{
                            window.{self.term_id}.dispose();
                        }}
                        window.{self.term_id} = null;
                    }}

                    if (typeof Terminal === 'undefined') {{
                        throw new Error('xterm.js 库未加载');
                    }}

                    var el = document.getElementById('{self.term_id}');
                    if (!el) {{
                        throw new Error('终端挂载节点不存在');
                    }}
                    el.innerHTML = '';
                    el.style.width = '100%';
                    el.style.height = '100%';
                    el.style.minHeight = '0';
                    el.style.display = 'block';
                    el.style.position = 'relative';
                    el.style.paddingLeft = '14px';
                    el.style.boxSizing = 'border-box'; 

                    var darkTheme = {{
                        background: '#000000',
                        foreground: '#ffffff',
                        cursor: '#22d3ee',
                        cursorAccent: '#000000',
                        selectionBackground: 'rgba(34, 211, 238, 0.28)'
                    }};
                    var lightTheme = {{
                        background: '#ffffff',
                        foreground: '#0f172a',
                        cursor: '#2563eb',
                        cursorAccent: '#ffffff',
                        selectionBackground: 'rgba(37, 99, 235, 0.18)'
                    }};

                    var term = new Terminal({{
                        cursorBlink: true,
                        fontSize: 13,
                        lineHeight: 1.2,
                        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
                        theme: {str({'background': term_bg, 'foreground': term_fg, 'cursor': term_cursor, 'cursorAccent': term_bg, 'selectionBackground': term_selection}).replace("'", '"')},
                        convertEol: true,
                        scrollback: 5000
                    }});

                    var fitAddon = null;
                    if (typeof FitAddon !== 'undefined') {{
                        var FitAddonClass = FitAddon.FitAddon || FitAddon;
                        fitAddon = new FitAddonClass();
                        term.loadAddon(fitAddon);
                    }}

                    term.open(el);

                    var applyTermTheme = function(isDark) {{
                        var theme = isDark ? darkTheme : lightTheme;
                        var bg = theme.background;
                        var fg = theme.foreground;
                        var viewport = el.querySelector('.xterm-viewport');
                        var screen = el.querySelector('.xterm-screen');
                        var xtermRoot = el.querySelector('.xterm');

                        var paintElement = function(target) {{
                            if (!target) return;
                            target.style.background = bg;
                            target.style.backgroundColor = bg;
                            target.style.color = fg;
                        }};

                        try {{
                            if (term && term.options) term.options.theme = theme;
                            if (term && term._core && term._core._themeService && typeof term._core._themeService.setTheme === 'function') {{
                                term._core._themeService.setTheme(theme);
                            }}
                        }} catch (e) {{
                            console.warn('xterm theme apply failed', e);
                        }}

                        paintElement(el);
                        paintElement(el.parentElement);
                        paintElement(xtermRoot);
                        paintElement(viewport);
                        paintElement(screen);

                        try {{
                            var canvases = el.querySelectorAll('canvas');
                            canvases.forEach(function(canvas) {{
                                canvas.style.background = bg;
                                canvas.style.backgroundColor = bg;
                            }});
                        }} catch (e) {{}}

                        if (typeof term.refresh === 'function') {{
                            try {{ term.refresh(0, Math.max((term.rows || 1) - 1, 0)); }} catch (e) {{}}
                        }}
                    }};

                    window.{self.term_id}_applyTheme = applyTermTheme;
                    window.{self.term_id}_themeListener = function(event) {{
                        var isDark = !!(event && event.detail && event.detail.isDark);
                        applyTermTheme(isDark);
                        setTimeout(function() {{ applyTermTheme(isDark); }}, 30);
                        setTimeout(function() {{ applyTermTheme(isDark); }}, 160);
                    }};
                    window.addEventListener('xfusion-theme-change', window.{self.term_id}_themeListener);
                    applyTermTheme({str(is_dark).lower()});

                    var lastResizeSignature = null;
                    var emitResize = function() {{
                        try {{
                            var cols = term && term.cols ? term.cols : 0;
                            var rows = term && term.rows ? term.rows : 0;
                            if (!cols || !rows) return;
                            var signature = cols + 'x' + rows;
                            if (signature === lastResizeSignature) return;
                            lastResizeSignature = signature;
                            var node = document.getElementById('{self.term_id}');
                            if (node) {{
                                node.dispatchEvent(new CustomEvent('term_resize', {{
                                    detail: {{ cols: cols, rows: rows }}
                                }}));
                            }}
                        }} catch (e) {{}}
                    }};

                    var doFit = function() {{
                        try {{
                            if (fitAddon) fitAddon.fit();
                            emitResize();
                            term.scrollToBottom();
                            term.focus();
                        }} catch (e) {{}}
                    }};

                    setTimeout(doFit, 50);
                    setTimeout(doFit, 200);
                    setTimeout(doFit, 500);
                    setTimeout(doFit, 1000);
                    setTimeout(doFit, 1500);
                    requestAnimationFrame(doFit);

                    window.{self.term_id} = term;
                    term.focus();

                    term.onData(data => {{
                        var el = document.getElementById('{self.term_id}');
                        if (el) {{
                            el.dispatchEvent(new CustomEvent('term_input', {{ detail: data }}));
                        }}
                    }});

                    if (fitAddon) {{
                        new ResizeObserver(() => doFit()).observe(el);
                        if (el.parentElement) {{
                            new ResizeObserver(() => doFit()).observe(el.parentElement);
                        }}
                    }}
                    window.addEventListener('resize', doFit);
                }} catch(e) {{
                    console.error('Terminal Init Error:', e);
                    alert('终端启动失败: ' + e.message);
                }}
                """
                with self.container.client:
                    ui.run_javascript(init_js)

                def handle_input(e):
                    data = e.args.get('detail') if isinstance(e.args, dict) and 'detail' in e.args else e.args
                    self._write_to_ssh(data)
                
                term_container.on('term_input', handle_input)
                term_container.on('term_resize', self._handle_resize_event)

                self.client, msg = await get_ssh_client(self.server_data)

                if not self.client:
                    self._print_error(msg)
                    return

                async def pre_login_tasks():
                    last_login_msg = ""
                    try:
                        res = await asyncio.wait_for(self.client.run("touch ~/.hushlogin && last -n 2 -a | head -n 2 | tail -n 1"), timeout=5.0)
                        raw_log = res.stdout.strip()
                        if raw_log and "wtmp" not in raw_log:
                            parts = raw_log.split()
                            if len(parts) >= 7:
                                date_time = " ".join(parts[2:6])
                                ip_addr = parts[-1]
                                last_login_msg = f"Last login:  {date_time}   {ip_addr}"
                    except:
                        pass
                    return last_login_msg

                login_info = await pre_login_tasks()

                if login_info:
                    formatted_msg = f"\x1b[32m{login_info}\x1b[0m\r\n"
                    b64_msg = base64.b64encode(formatted_msg.encode('utf-8')).decode('utf-8')
                    ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.write(atob("{b64_msg}"));')

                initial_cols = self._pending_resize[0] if self._pending_resize else 100
                initial_rows = self._pending_resize[1] if self._pending_resize else 30
                
                self.process = await self.client.create_process(term_type='xterm', term_size=(initial_cols, initial_rows))
                
                self.active = True
                self._apply_pending_resize_now()

                if self.initial_command:
                    try:
                        self.process.stdin.write(self.initial_command + '\n')
                    except:
                        pass

                asyncio.create_task(self._read_loop())
                ui.notify(f"已连接到 {self.server_data['name']}", type='positive')

            except Exception as e:
                self._print_error(f"初始化异常: {e}")

    def _print_error(self, msg):
        try:
            js_cmd = f'if(window.{self.term_id}) window.{self.term_id}.write("\\r\\n\\x1b[31m[Error] {str(msg)}\\x1b[0m\\r\\n");'
            with self.container.client:
                ui.run_javascript(js_cmd)
        except:
            ui.notify(msg, type='negative')

    def _write_to_ssh(self, data):
        if self.process and self.active:
            try:
                self.process.stdin.write(data)
            except:
                pass

    async def _read_loop(self):
        while self.active and self.process:
            try:
                # asyncssh stdout.read is async
                data = await self.process.stdout.read(4096)
                if not data:
                    break

                # Encode string to bytes if returned as string
                if isinstance(data, str):
                    data = data.encode('utf-8', errors='ignore')

                b64_data = base64.b64encode(data).decode('utf-8')

                js_cmd = f"""
                if(window.{self.term_id}) {{
                    try {{
                        var binaryStr = atob("{b64_data}");
                        var bytes = new Uint8Array(binaryStr.length);
                        for (var i = 0; i < binaryStr.length; i++) {{
                            bytes[i] = binaryStr.charCodeAt(i);
                        }}
                        var decodedStr = new TextDecoder("utf-8").decode(bytes);

                        window.{self.term_id}.write(decodedStr);
                        if (typeof window.{self.term_id}.scrollToBottom === 'function') {{
                            window.{self.term_id}.scrollToBottom();
                        }}
                    }} catch(e) {{
                        console.error("Term Decode Error", e);
                    }}
                }}
                """
                with self.container.client:
                    ui.run_javascript(js_cmd)

            except asyncssh.BreakReceived:
                break
            except Exception as e:
                await asyncio.sleep(0.1)
                
        self.close()

    def close(self):
        self.active = False
        if self._resize_debounce_task and not self._resize_debounce_task.done():
            self._resize_debounce_task.cancel()
        if self.process:
            try:
                self.process.close()
            except:
                pass
        if self.client:
            try:
                self.client.close()
            except:
                pass
        try:
            with self.container.client:
                ui.run_javascript(f"""
                    if (window.{self.term_id}_themeListener) {{
                        window.removeEventListener('xfusion-theme-change', window.{self.term_id}_themeListener);
                        window.{self.term_id}_themeListener = null;
                    }}
                    if (window.{self.term_id}) window.{self.term_id}.dispose();
                """)
        except:
            pass


async def _ssh_exec_wrapper(server_conf, cmd, timeout=120):
    client, msg = await get_ssh_client(server_conf)
    if not client:
        return False, msg
    try:
        res = await asyncio.wait_for(client.run(cmd, check=False), timeout=timeout)
        out = res.stdout.strip() if isinstance(res.stdout, str) else (res.stdout.decode().strip() if res.stdout else "")
        err = res.stderr.strip() if isinstance(res.stderr, str) else (res.stderr.decode().strip() if res.stderr else "")
        return True, out + ("\n" + err if err else "")
    except asyncio.TimeoutError:
        return False, "命令执行超时"
    except Exception as e:
        return False, str(e).strip() or repr(e)
    finally:
        client.close()
        await client.wait_closed()


async def _exec(server_data, cmd, log_area):
    client, msg = await get_ssh_client(server_data)
    if not client:
        log_area.push(msg)
        return
    try:
        process = await client.create_process(command=cmd, term_type='xterm')
        
        async def read_output():
            while True:
                data = await process.stdout.read(4096)
                if not data:
                    break
                if isinstance(data, bytes):
                    data = data.decode('utf-8', errors='ignore')
                log_area.push(data.strip())
                
        async def read_error():
            while True:
                data = await process.stderr.read(4096)
                if not data:
                    break
                if isinstance(data, bytes):
                    data = data.decode('utf-8', errors='ignore')
                log_area.push(f"ERR: {data.strip()}")

        await asyncio.gather(read_output(), read_error())
        
        if process.exit_status is not None:
             log_area.push(f"✅ 退出码: {process.exit_status}")

    except asyncio.TimeoutError:
        log_area.push("❌ 执行超时: 命令执行时间过长或正在等待交互 (如 sudo/vim)")
    except Exception as e:
        log_area.push(f"系统错误: {repr(e)}")
    finally:
        client.close()
        await client.wait_closed()