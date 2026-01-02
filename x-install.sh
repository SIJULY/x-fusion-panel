#!/bin/bash

# 获取参数
TOKEN=$1
REPORT_URL=$2

# 检查参数是否存在
if [ -z "$TOKEN" ] || [ -z "$REPORT_URL" ]; then
    echo "❌ 错误: 缺少参数。"
    echo "用法: bash install.sh <TOKEN> <REPORT_URL>"
    exit 1
fi

echo "🚀 开始安装简易探针..."

# 1. 环境准备
if ! command -v python3 >/dev/null 2>&1; then
    echo "📦 安装 Python3..."
    if [ -f /etc/debian_version ]; then apt-get update -y && apt-get install -y python3;
    elif [ -f /etc/redhat-release ]; then yum install -y python3;
    elif [ -f /etc/alpine-release ]; then apk add python3; fi
fi

# 2. 写入探针 (TOKEN 使用变量注入)
cat > /root/mini_probe.py << EOF
import http.server, json, subprocess, sys, os, time, socketserver
PORT = 54322
TOKEN = "$TOKEN"

def get_cpu():
    try:
        with open("/proc/stat") as f: fields = [float(x) for x in f.readline().split()[1:5]]
        t1, i1 = sum(fields), fields[3]
        time.sleep(0.5)
        with open("/proc/stat") as f: fields = [float(x) for x in f.readline().split()[1:5]]
        t2, i2 = sum(fields), fields[3]
        return round((1 - (i2-i1)/(t2-t1)) * 100, 1)
    except: return 0.0

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(s):
        if s.path != "/status?token=" + TOKEN: 
            s.send_response(403); s.end_headers(); return
        try:
            cpu_u = get_cpu()
            try: cores = os.cpu_count() or 1
            except: cores = 1
            try:
                with open("/proc/loadavg") as f: l = float(f.read().split()[0])
            except: l = 0.0
            mem_u = 0; mem_t = 0
            try:
                with open("/proc/meminfo") as f: lines = f.readlines()
                m = {}
                for line in lines[:5]:
                    parts = line.split()
                    if len(parts) >= 2: m[parts[0].rstrip(":")] = int(parts[1])
                total_kb = m.get("MemTotal", 1)
                avail_kb = m.get("MemAvailable", m.get("MemFree", 0))
                mem_u = round(((total_kb - avail_kb) / total_kb) * 100, 1)
                mem_t = round(total_kb / 1024 / 1024, 2)
            except: pass
            disk_u = 0; disk_t = 0
            try:
                out = subprocess.check_output(["df", "-k", "/"]).decode().splitlines()[1].split()
                total_kb = int(out[1])
                disk_t = round(total_kb / 1024 / 1024, 2)
                disk_u = int(out[-2].strip("%"))
            except: pass
            uptime_str = "-"
            try:
                with open("/proc/uptime") as f: u = float(f.read().split()[0])
                dy = int(u // 86400); hr = int((u % 86400) // 3600)
                uptime_str = f"{dy}d {hr}h"
            except: pass
            data = {
                "status": "online", "load": l, "cpu_usage": cpu_u, "cpu_cores": cores,
                "mem_usage": mem_u, "mem_total": mem_t, "disk_usage": disk_u, "disk_total": disk_t, "uptime": uptime_str
            }
            s.send_response(200); s.send_header("Content-Type", "application/json"); s.end_headers()
            s.wfile.write(json.dumps(data).encode())
        except: s.send_response(500)
    def log_message(s,f,*a): pass

if __name__ == "__main__":
    try: 
        socketserver.TCPServer.allow_reuse_address = True
        http.server.HTTPServer(("0.0.0.0", PORT), H).serve_forever()
    except: pass
EOF

# 3. 启动服务
pkill -f mini_probe.py || true
nohup python3 /root/mini_probe.py >/dev/null 2>&1 &

# 4. 放行端口
if command -v iptables >/dev/null; then iptables -I INPUT -p tcp --dport 54322 -j ACCEPT || true; fi
if command -v ufw >/dev/null; then ufw allow 54322/tcp || true; fi
if command -v firewall-cmd >/dev/null; then firewall-cmd --zone=public --add-port=54322/tcp --permanent && firewall-cmd --reload || true; fi

echo "📡 正在向面板注册..."
curl -s -X POST "$REPORT_URL" -H "Content-Type: application/json" -d "{\"token\": \"$TOKEN\"}"

echo -e "\n✅ 安装完成！请检查面板列表。"
sleep 1
ss -nltp | grep 54322
