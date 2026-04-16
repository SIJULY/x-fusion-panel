class WebSSH:
    def __init__(self, container, server_data):
        self.container = container
        self.server_data = server_data
        self.client = None
        self.channel = None
        self.active = False
        self.term_id = f'term_{uuid.uuid4().hex}'

    async def connect(self):
        with self.container:
            try:
                # 1. 渲染终端 UI 容器
                ui.element('div').props(f'id={self.term_id}').classes('w-full h-full bg-black rounded p-2 overflow-hidden relative')
                
                # 2. 注入 JS (xterm.js 初始化)
                init_js = f"""
                try {{
                    if (window.{self.term_id}) {{
                        if (typeof window.{self.term_id}.dispose === 'function') {{
                            window.{self.term_id}.dispose();
                        }}
                        window.{self.term_id} = null;
                    }}
                    
                    if (typeof Terminal === 'undefined') {{
                        throw new Error("xterm.js 库未加载");
                    }}
                    
                    var term = new Terminal({{
                        cursorBlink: true,
                        fontSize: 13,
                        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
                        theme: {{ background: '#000000', foreground: '#ffffff' }},
                        convertEol: true,
                        scrollback: 5000
                    }});
                    
                    var fitAddon;
                    if (typeof FitAddon !== 'undefined') {{
                        var FitAddonClass = FitAddon.FitAddon || FitAddon;
                        fitAddon = new FitAddonClass();
                        term.loadAddon(fitAddon);
                    }}
                    
                    var el = document.getElementById('{self.term_id}');
                    term.open(el);
                    
                    term.write('\\x1b[32m[Local] Terminal Ready. Connecting...\\x1b[0m\\r\\n');
                    
                    if (fitAddon) {{ setTimeout(() => {{ fitAddon.fit(); }}, 200); }}
                    
                    window.{self.term_id} = term;
                    term.focus();
                    
                    term.onData(data => {{
                        emitEvent('term_input_{self.term_id}', data);
                    }});
                    
                    if (fitAddon) {{ new ResizeObserver(() => fitAddon.fit()).observe(el); }}

                }} catch(e) {{
                    console.error("Terminal Init Error:", e);
                    alert("终端启动失败: " + e.message);
                }}
                """
                ui.run_javascript(init_js)

                ui.on(f'term_input_{self.term_id}', lambda e: self._write_to_ssh(e.args))

                # 3. 建立基础连接 (此时还不启动 Shell)
                self.client, msg = await run.io_bound(get_ssh_client_sync, self.server_data)
                
                if not self.client:
                    self._print_error(msg)
                    return

                # ================= ✨✨✨ 预处理阶段：定制信息格式 ✨✨✨ =================
                
                def pre_login_tasks():
                    last_login_msg = ""
                    try:
                        # 1. 屏蔽广告
                        self.client.exec_command("touch ~/.hushlogin")
                        
                        # 2. 获取原始日志
                        # raw_log 类似: root pts/0 Wed Jan 9 16:30 still logged in 167.234.xx.xx
                        stdin, stdout, stderr = self.client.exec_command("last -n 2 -a | head -n 2 | tail -n 1")
                        raw_log = stdout.read().decode().strip()
                        
                        if raw_log and "wtmp" not in raw_log:
                            # 3. ✂️ Python 字符串切割重组 ✂️
                            parts = raw_log.split()
                            # 确保长度足够防止报错
                            # parts[2:6] 是日期时间 (Wed Jan 9 16:30)
                            # parts[-1] 是 IP 地址 (167.234.xx.xx)
                            if len(parts) >= 7:
                                date_time = " ".join(parts[2:6])
                                ip_addr = parts[-1]
                                # 拼凑最终格式
                                last_login_msg = f"Last login:  {date_time}   {ip_addr}"
                    except: pass
                    return last_login_msg

                # 在后台线程执行
                login_info = await run.io_bound(pre_login_tasks)

                # 3.1 打印定制后的绿色信息
                if login_info:
                    # \x1b[32m 是绿色
                    formatted_msg = f"\r\n\x1b[32m{login_info}\x1b[0m\r\n"
                    b64_msg = base64.b64encode(formatted_msg.encode('utf-8')).decode('utf-8')
                    ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.write(atob("{b64_msg}"));')

                # =========================================================================

                # 4. 启动交互式 Shell
                self.channel = self.client.invoke_shell(term='xterm', width=100, height=30)
                self.channel.settimeout(0.0) 
                self.active = True

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
            try: self.channel.send(data)
            except: pass

    async def _read_loop(self):
        while self.active:
            try:
                if self.channel.recv_ready():
                    # 读取原始字节流
                    data = self.channel.recv(4096)
                    if not data: break 
                    
                    # 转为 Base64 以便在 JS 中传输
                    b64_data = base64.b64encode(data).decode('utf-8')
                    
                    # ✨✨✨ [修复核心]：JS 端使用 TextDecoder 正确解码 UTF-8 中文 ✨✨✨
                    js_cmd = f"""
                    if(window.{self.term_id}) {{
                        try {{
                            // 1. 解码 Base64 为二进制字符串
                            var binaryStr = atob("{b64_data}");
                            // 2. 转换为 Uint8Array 字节数组
                            var bytes = new Uint8Array(binaryStr.length);
                            for (var i = 0; i < binaryStr.length; i++) {{
                                bytes[i] = binaryStr.charCodeAt(i);
                            }}
                            // 3. 使用 TextDecoder 按 UTF-8 解码为正确字符
                            var decodedStr = new TextDecoder("utf-8").decode(bytes);
                            
                            // 4. 写入终端
                            window.{self.term_id}.write(decodedStr);
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
            try: self.client.close()
            except: pass
        try:
            with self.container.client:
                ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.dispose();')
        except: pass