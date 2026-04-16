import asyncio
import base64
import io
import socket
import uuid

import paramiko
from nicegui import run, ui

from app.storage.repositories import load_global_key


ssh_instances = {}


def get_ssh_client(server_data):
    """建立 SSH 连接"""
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    raw_url = server_data['url']
    if '://' in raw_url:
        host = raw_url.split('://')[-1].split(':')[0]
    else:
        host = raw_url.split(':')[0]

    if server_data.get('ssh_host'):
        host = server_data['ssh_host']

    port = int(server_data.get('ssh_port') or 22)
    user = server_data.get('ssh_user') or 'root'

    auth_type = server_data.get('ssh_auth_type', '全局密钥').strip()

    print(f"🔌 [SSH Debug] 连接目标: {host}, 用户: {user}, 认证方式: [{auth_type}]", flush=True)

    try:
        if auth_type == '独立密码':
            pwd = server_data.get('ssh_password', '')
            if not pwd:
                raise Exception("选择了独立密码，但密码为空")

            client.connect(host, port, username=user, password=pwd, timeout=5,
                           look_for_keys=False, allow_agent=False)

        elif auth_type == '独立密钥':
            key_content = server_data.get('ssh_key', '')
            if not key_content:
                raise Exception("选择了独立密钥，但密钥为空")

            key_file = io.StringIO(key_content)
            try:
                pkey = paramiko.RSAKey.from_private_key(key_file)
            except:
                key_file.seek(0)
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except:
                    raise Exception("无法识别的私钥格式")

            client.connect(host, port, username=user, pkey=pkey, timeout=5,
                           look_for_keys=False, allow_agent=False)

        else:
            g_key = load_global_key()
            if not g_key:
                raise Exception("全局密钥未配置")

            key_file = io.StringIO(g_key)
            try:
                pkey = paramiko.RSAKey.from_private_key(key_file)
            except:
                key_file.seek(0)
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except:
                    raise Exception("全局密钥格式无法识别")

            client.connect(host, port, username=user, pkey=pkey, timeout=5,
                           look_for_keys=False, allow_agent=False)

        return client, f"✅ 已连接 {user}@{host}"

    except Exception as e:
        return None, f"❌ 连接失败: {str(e)}"


def get_ssh_client_sync(server_data):
    return get_ssh_client(server_data)


class WebSSH:
    def __init__(self, container, server_data, initial_command=None):
        self.container = container
        self.server_data = server_data
        self.initial_command = (initial_command or '').strip()
        self.client = None
        self.channel = None
        self.active = False
        self.term_id = f'term_{uuid.uuid4().hex}'

    async def connect(self):
        with self.container:
            try:
                ui.element('div').props(f'id={self.term_id}').classes('w-full h-full bg-black rounded overflow-hidden relative').style('min-height: 420px; height: 100%; width: 100%; display: block; position: relative;')

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
                    el.style.minHeight = '420px';
                    el.style.display = 'block';
                    el.style.position = 'relative';

                    var term = new Terminal({{
                        cursorBlink: true,
                        fontSize: 13,
                        lineHeight: 1.2,
                        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
                        theme: {{ background: '#000000', foreground: '#ffffff' }},
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
                    term.write('\\x1b[32m[Local] Terminal Ready. Connecting...\\x1b[0m\\r\\n');

                    var doFit = function() {{
                        try {{
                            if (fitAddon) fitAddon.fit();
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
                        emitEvent('term_input_{self.term_id}', data);
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

                ui.on(f'term_input_{self.term_id}', lambda e: self._write_to_ssh(e.args))

                self.client, msg = await run.io_bound(get_ssh_client_sync, self.server_data)

                if not self.client:
                    self._print_error(msg)
                    return

                def pre_login_tasks():
                    last_login_msg = ""
                    try:
                        self.client.exec_command("touch ~/.hushlogin")

                        stdin, stdout, stderr = self.client.exec_command("last -n 2 -a | head -n 2 | tail -n 1")
                        raw_log = stdout.read().decode().strip()

                        if raw_log and "wtmp" not in raw_log:
                            parts = raw_log.split()
                            if len(parts) >= 7:
                                date_time = " ".join(parts[2:6])
                                ip_addr = parts[-1]
                                last_login_msg = f"Last login:  {date_time}   {ip_addr}"
                    except:
                        pass
                    return last_login_msg

                login_info = await run.io_bound(pre_login_tasks)

                if login_info:
                    formatted_msg = f"\r\n\x1b[32m{login_info}\x1b[0m\r\n"
                    b64_msg = base64.b64encode(formatted_msg.encode('utf-8')).decode('utf-8')
                    ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.write(atob("{b64_msg}"));')

                self.channel = self.client.invoke_shell(term='xterm', width=100, height=30)
                self.channel.settimeout(0.0)
                self.active = True

                if self.initial_command:
                    try:
                        self.channel.send(self.initial_command + '\n')
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
        if self.channel and self.active:
            try:
                self.channel.send(data)
            except:
                pass

    async def _read_loop(self):
        while self.active:
            try:
                if self.channel.recv_ready():
                    data = self.channel.recv(4096)
                    if not data:
                        break

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

                await asyncio.sleep(0.01)
            except Exception:
                await asyncio.sleep(0.1)

    def close(self):
        self.active = False
        if self.client:
            try:
                self.client.close()
            except:
                pass
        try:
            with self.container.client:
                ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.dispose();')
        except:
            pass


def _ssh_exec_wrapper(server_conf, cmd):
    client, msg = get_ssh_client_sync(server_conf)
    if not client:
        return False, msg
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        client.close()
        return True, out + "\n" + err
    except Exception as e:
        return False, str(e)


def _exec(server_data, cmd, log_area):
    client, msg = get_ssh_client(server_data)
    if not client:
        log_area.push(msg)
        return
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=10, get_pty=True)

        out = stdout.read().decode('utf-8', errors='ignore').strip()
        err = stderr.read().decode('utf-8', errors='ignore').strip()

        if out:
            log_area.push(out)
        if err:
            log_area.push(f"ERR: {err}")

        if not out and not err:
            log_area.push("✅ 命令已执行 (无返回内容)")

    except paramiko.SSHException as e:
        log_area.push(f"SSH Error: {str(e)}")
    except socket.timeout:
        log_area.push("❌ 执行超时: 命令执行时间过长或正在等待交互 (如 sudo/vim)")
    except Exception as e:
        log_area.push(f"系统错误: {repr(e)}")
    finally:
        client.close()
