class SSHXUIManager:
    """
    通过 SSH 直接操作远程 X-UI 数据库。
    V4 修复：
    1. 彻底修复缩进问题 (IndentationError)，防止远程脚本语法报错。
    2. 增强错误捕获，现在能识别 SyntaxError 并弹窗报错，而不是误报成功。
    3. 保留了 V3 的自动路径探测 (兼容 3x-ui / x-ui)。
    """
    def __init__(self, server_conf):
        self.server_conf = server_conf

    async def _exec_remote_script(self, python_code):
        # ✨✨✨ 核心修复：给传入的代码每一行都加 4 个空格缩进 ✨✨✨
        # 这样才能正确放入 try: 块内部
        indented_code = "\n".join(["    " + line for line in python_code.split("\n")])

        # 包装脚本
        wrapper = f"""
import sqlite3, json, os, sys, time, subprocess

def detect_env():
    # 1. 探测数据库路径
    possible_dbs = [
        "/etc/x-ui/x-ui.db",
        "/usr/local/x-ui/bin/x-ui.db",
        "/usr/local/x-ui/x-ui.db"
    ]
    real_db = None
    for p in possible_dbs:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            real_db = p
            break
    
    if not real_db:
        raise Exception("无法在常见路径找到 x-ui.db，请确认面板已安装")

    # 2. 探测服务名称
    svc_name = "x-ui"
    if os.path.exists("/etc/systemd/system/3x-ui.service"):
        svc_name = "3x-ui"
    elif os.path.exists("/usr/lib/systemd/system/3x-ui.service"):
        svc_name = "3x-ui"
        
    return real_db, svc_name

try:
    db_path, svc_name = detect_env()
    # print(f"DEBUG: Detected {{db_path}} {{svc_name}}")
    
{indented_code}
except Exception as e:
    import traceback
    print("ERROR_TRACE:", traceback.format_exc())
    print("ERROR:", e)
    sys.exit(1)
"""
        # Base64 编码传输
        b64_code = base64.b64encode(wrapper.encode('utf-8')).decode()
        cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64_code}'))\""
        
        success, output = await run.io_bound(lambda: _ssh_exec_wrapper(self.server_conf, cmd))
        
        # ✨✨✨ 增强错误检查：捕获语法错误 ✨✨✨
        # 之前的版本因为脚本没跑起来，没有输出 ERROR: 字样，导致误判为成功
        # 现在增加对 Traceback 和 SyntaxError 的检测
        if not success: 
            raise Exception(f"SSH 连接失败: {output}")
        if "Traceback" in output or "SyntaxError" in output or "ERROR:" in output: 
            # 将远程报错抛出给 UI 显示
            raise Exception(f"远程执行失败: {output}")
            
        return output.strip()

    async def get_inbounds(self):
        # 注意：这里的代码不需要缩进，_exec_remote_script 会自动加
        script = f"""
if os.path.exists(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM inbounds")
    rows = cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        for k in ['settings', 'streamSettings', 'sniffing']:
            if d.get(k):
                try: d[k] = json.loads(d[k])
                except: pass
        d['enable'] = bool(d['enable'])
        result.append(d)
    print(json.dumps(result))
    con.close()
else:
    print("[]")
"""
        try:
            output = await self._exec_remote_script(script)
            return json.loads(output)
        except Exception as e: 
            logger.error(f"SSH Get Inbounds Error: {e}")
            return []

    async def add_inbound(self, inbound_data):
        payload = {
            "remark": inbound_data.get('remark', '未命名'),
            "port": inbound_data.get('port'),
            "protocol": inbound_data.get('protocol'),
            "settings": json.dumps(inbound_data.get('settings', {})),
            "stream_settings": json.dumps(inbound_data.get('streamSettings', {})),
            "enable": 1,
            "expiry_time": 0,
            "listen": "",
            "total": 0, "up": 0, "down": 0, "tag": "", "sniffing": "{}"
        }
        payload_json = json.dumps(payload)

        # 注意：f-string 中的 {{ }} 是为了在 python 脚本中保留 { }
        # 而 {svc_name} 是 Python 变量
        script = f"""
params = json.loads(r'''{payload_json}''')

# 1. 停止服务
os.system(f"systemctl stop {{svc_name}}") 
time.sleep(0.5)

con = sqlite3.connect(db_path)
cur = con.cursor()

# 2. 检查端口
cur.execute("SELECT id FROM inbounds WHERE port=?", (params['port'],))
if cur.fetchone(): 
    con.close()
    os.system(f"systemctl start {{svc_name}}")
    raise Exception(f"端口 {{params['port']}} 已被占用")

# 3. 动态插入
cur.execute("PRAGMA table_info(inbounds)")
columns = [info[1] for info in cur.fetchall()]
valid_keys = []
valid_vals = []
placeholders = []

for k, v in params.items():
    if k in columns:
        valid_keys.append(k)
        valid_vals.append(v)
        placeholders.append("?")

if 'user_id' in columns:
    valid_keys.append('user_id')
    valid_vals.append(1)
    placeholders.append("?")

sql = f"INSERT INTO inbounds ({{','.join(valid_keys)}}) VALUES ({{','.join(placeholders)}})"
cur.execute(sql, tuple(valid_vals))
con.commit()
con.close()

# 4. 重启服务
os.system(f"systemctl start {{svc_name}}")
print(f"SUCCESS (DB: {{db_path}})")
"""
        await self._exec_remote_script(script)
        return True, "添加成功 (Root模式)"

    async def update_inbound(self, inbound_id, inbound_data):
        payload = {
            "id": inbound_id,
            "remark": inbound_data.get('remark', ''),
            "port": inbound_data.get('port'),
            "protocol": inbound_data.get('protocol'),
            "settings": json.dumps(inbound_data.get('settings', {})),
            "stream_settings": json.dumps(inbound_data.get('streamSettings', {})),
            "enable": 1 if inbound_data.get('enable', True) else 0
        }
        payload_json = json.dumps(payload)

        script = f"""
params = json.loads(r'''{payload_json}''')

os.system(f"systemctl stop {{svc_name}}")
time.sleep(0.5)

con = sqlite3.connect(db_path)
cur = con.cursor()

sql = "UPDATE inbounds SET remark=?, port=?, protocol=?, settings=?, stream_settings=?, enable=? WHERE id=?"
cur.execute(sql, (
    params['remark'], params['port'], params['protocol'], 
    params['settings'], params['stream_settings'], params['enable'], 
    params['id']
))

if cur.rowcount == 0:
    con.close()
    os.system(f"systemctl start {{svc_name}}")
    raise Exception("ID未找到")

con.commit()
con.close()

os.system(f"systemctl start {{svc_name}}")
print(f"SUCCESS (DB: {{db_path}})")
"""
        await self._exec_remote_script(script)
        return True, "更新成功 (Root模式)"

    async def delete_inbound(self, inbound_id):
        script = f"""
os.system(f"systemctl stop {{svc_name}}")
time.sleep(0.5)

con = sqlite3.connect(db_path)
cur = con.cursor()
cur.execute("DELETE FROM inbounds WHERE id=?", ({inbound_id},))
con.commit()
con.close()

os.system(f"systemctl start {{svc_name}}")
print(f"SUCCESS (DB: {{db_path}})")
"""
        await self._exec_remote_script(script)
        return True, "删除成功 (Root模式)"