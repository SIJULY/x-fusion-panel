import base64
import json

from nicegui import run

from app.core.logging import logger
from app.services.ssh import _ssh_exec_wrapper


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
        indented_code = "\n".join(["    " + line for line in python_code.split("\n")])

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
    
{indented_code}
except Exception as e:
    import traceback
    print("ERROR_TRACE:", traceback.format_exc())
    print("ERROR:", e)
    sys.exit(1)
"""
        b64_code = base64.b64encode(wrapper.encode('utf-8')).decode()
        cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64_code}'))\""

        success, output = await run.io_bound(lambda: _ssh_exec_wrapper(self.server_conf, cmd))

        if not success:
            raise Exception(f"SSH 连接失败: {output}")
        if "Traceback" in output or "SyntaxError" in output or "ERROR:" in output:
            raise Exception(f"远程执行失败: {output}")

        return output.strip()

    async def get_inbounds(self):
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
        if d.get('stream_settings') and not d.get('streamSettings'):
            d['streamSettings'] = d.get('stream_settings')
        for k in ['settings', 'streamSettings', 'stream_settings', 'sniffing']:
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except:
                    pass
        if d.get('stream_settings') and not d.get('streamSettings'):
            d['streamSettings'] = d.get('stream_settings')
        d['expiryTime'] = int(d.get('expiryTime') or d.get('expiry_time') or 0)
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
            "enable": 1 if inbound_data.get('enable', True) else 0,
            "expiry_time": int(inbound_data.get('expiryTime') or inbound_data.get('expiry_time') or 0),
            "listen": inbound_data.get('listen', ''),
            "total": int(inbound_data.get('total') or 0),
            "up": int(inbound_data.get('up') or 0),
            "down": int(inbound_data.get('down') or 0),
            "tag": inbound_data.get('tag', ''),
            "sniffing": json.dumps(inbound_data.get('sniffing', {})),
        }
        payload_json = json.dumps(payload)

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

# 最小兼容修复：某些 x-ui / 3x-ui 数据库对 inbounds.tag 有唯一约束。
# 原始实现固定写入空字符串，遇到已有空 tag 时会插入失败。
# 这里仅在 SSH 直写数据库路径下，为空 tag 生成一个最小唯一值，避免 UNIQUE 冲突。
if 'tag' in columns:
    raw_tag = str(params.get('tag') or '').strip()
    if not raw_tag:
        proto = str(params.get('protocol') or 'inbound').strip() or 'inbound'
        port = str(params.get('port') or '0').strip() or '0'
        raw_tag = f"{{proto}}_{{port}}"
    candidate_tag = raw_tag
    suffix = 1
    while True:
        cur.execute("SELECT id FROM inbounds WHERE tag=?", (candidate_tag,))
        if not cur.fetchone():
            break
        candidate_tag = f"{{raw_tag}}_{{suffix}}"
        suffix += 1
    params['tag'] = candidate_tag

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
            "enable": 1 if inbound_data.get('enable', True) else 0,
            "total": int(inbound_data.get('total') or 0),
            "expiry_time": int(inbound_data.get('expiryTime') or inbound_data.get('expiry_time') or 0),
            "listen": inbound_data.get('listen', ''),
            "tag": inbound_data.get('tag', ''),
            "sniffing": json.dumps(inbound_data.get('sniffing', {})),
        }
        payload_json = json.dumps(payload)

        # 核心修复点：将 update_map 的单括号改为了双大括号 {{ }}，避免 f-string 语法解析崩溃
        script = f"""
params = json.loads(r'''{payload_json}''')

os.system(f"systemctl stop {{svc_name}}")
time.sleep(0.5)

con = sqlite3.connect(db_path)
cur = con.cursor()

cur.execute("PRAGMA table_info(inbounds)")
columns = [info[1] for info in cur.fetchall()]

update_map = {{
    'remark': params['remark'],
    'port': params['port'],
    'protocol': params['protocol'],
    'settings': params['settings'],
    'stream_settings': params['stream_settings'],
    'enable': params['enable'],
    'total': params['total'],
    'expiry_time': params['expiry_time'],
    'listen': params['listen'],
    'tag': params['tag'],
    'sniffing': params['sniffing'],
}}

assignments = []
values = []
for key, value in update_map.items():
    if key in columns:
        assignments.append(f"{{key}}=?")
        values.append(value)

sql = f"UPDATE inbounds SET {{', '.join(assignments)}} WHERE id=?"
values.append(params['id'])
cur.execute(sql, tuple(values))

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
