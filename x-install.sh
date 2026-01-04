#!/bin/bash

# 获取参数
TOKEN="$1"
REGISTER_API="$2"

# 参数校验
if [ -z "$TOKEN" ] || [ -z "$REGISTER_API" ]; then
    echo "❌ 错误: 缺少参数"
    echo "用法: bash x-install.sh \"TOKEN\" \"REGISTER_API_URL\""
    exit 1
fi

# 从注册 API 提取 推送 API (将 /register 替换为 /push)
PUSH_API="${REGISTER_API/\/register/\/push}"

echo "🚀 开始安装 X-Fusion 全能探针 (v3.1 稳定版)..."
echo "🔑 Token: $TOKEN"
echo "📡 推送地址: $PUSH_API"

# 1. 向面板注册
curl -s -X POST -H "Content-Type: application/json" -d "{\"token\":\"$TOKEN\"}" "$REGISTER_API"
echo ""

# 2. 安装必要依赖 (Python3 和 Ping)
echo "📦 检查并安装依赖..."
if [ -f /etc/debian_version ]; then
    apt-get update -y
    command -v python3 >/dev/null 2>&1 || apt-get install -y python3
    command -v ping >/dev/null 2>&1 || apt-get install -y iputils-ping
elif [ -f /etc/redhat-release ]; then
    command -v python3 >/dev/null 2>&1 || yum install -y python3
    command -v ping >/dev/null 2>&1 || yum install -y iputils
elif [ -f /etc/alpine-release ]; then
    command -v python3 >/dev/null 2>&1 || apk add python3
    command -v ping >/dev/null 2>&1 || apk add iputils
fi

# 3. 写入 Python 推送脚本 (集成 SSL 修复与 IPv4 强制锁定)
cat > /root/x_fusion_agent.py << EOF
import time, json, os, socket, sys, subprocess, re
import urllib.request, urllib.error
import ssl

# 配置参数
MANAGER_URL = "$PUSH_API"
TOKEN = "$TOKEN"
SERVER_URL = "" 

# 默认测速目标
PING_TARGETS = {
    "电信": "202.102.192.68",
    "联通": "112.122.10.26",
    "移动": "211.138.180.2"
}

# ✨ 全局 SSL 上下文 (忽略证书错误)
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def get_ping_latency(ip_input):
    try:
        if not ip_input: return -1
        target = ip_input.replace("http://", "").replace("https://", "").split(":")[0]
        # Linux ping: -c 1 (一次), -W 1 (1秒超时)
        cmd = ["ping", "-c", "1", "-W", "1", target]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            match = re.search(r"time=([\d.]+)", result.stdout)
            if match:
                return int(float(match.group(1)))
    except: pass
    return -1

def get_network_stats():
    rx_bytes = 0; tx_bytes = 0
    try:
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()[2:]
            for line in lines:
                parts = line.split(":")
                if len(parts) < 2: continue
                interface = parts[0].strip()
                if interface == "lo": continue
                data = parts[1].split()
                rx_bytes += int(data[0])
                tx_bytes += int(data[8])
    except: pass
    return rx_bytes, tx_bytes

def get_sys_info():
    global SERVER_URL
    data = {"token": TOKEN}
    
    # ✨✨✨ 核心修复：强制使用 IPv4 接口 ✨✨✨
    if not SERVER_URL:
        try:
            # 使用 AWS 的 IPv4 专用接口 (它不返回 IPv6)
            url_v4 = "http://checkip.amazonaws.com"
            with urllib.request.urlopen(url_v4, timeout=5, context=ssl_ctx) as r:
                my_ip = r.read().decode().strip()
                SERVER_URL = f"http://{my_ip}:54322"
        except:
            try:
                # 备用接口: ipw.cn 的 IPv4 接口
                with urllib.request.urlopen("http://4.ipw.cn", timeout=5, context=ssl_ctx) as r:
                    my_ip = r.read().decode().strip()
                    SERVER_URL = f"http://{my_ip}:54322"
            except: pass
    
    data["server_url"] = SERVER_URL

    try:
        # 1. 读取初始状态
        net_rx1, net_tx1 = get_network_stats()
        with open("/proc/stat") as f: fields = [float(x) for x in f.readline().split()[1:5]]
        t1, i1 = sum(fields), fields[3]
        
        time.sleep(1) 
        
        # 2. 读取结束状态
        with open("/proc/stat") as f: fields = [float(x) for x in f.readline().split()[1:5]]
        t2, i2 = sum(fields), fields[3]
        net_rx2, net_tx2 = get_network_stats()
        
        # 3. 计算数据
        data["cpu_usage"] = round((1 - (i2-i1)/(t2-t1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        data["net_total_in"] = net_rx2
        data["net_total_out"] = net_tx2
        data["net_speed_in"] = net_rx2 - net_rx1
        data["net_speed_out"] = net_tx2 - net_tx1

        with open("/proc/loadavg") as f: data["load_1"] = float(f.read().split()[0])

        with open("/proc/meminfo") as f: lines = f.readlines()
        m = {}
        for line in lines[:5]:
            parts = line.split()
            if len(parts) >= 2: m[parts[0].rstrip(":")] = int(parts[1])
        total = m.get("MemTotal", 1); avail = m.get("MemAvailable", m.get("MemFree", 0))
        data["mem_total"] = round(total / 1024 / 1024, 2)
        data["mem_usage"] = round(((total - avail) / total) * 100, 1)

        st = os.statvfs("/")
        total_d = st.f_blocks * st.f_frsize
        free_d = st.f_bavail * st.f_frsize
        data["disk_total"] = round(total_d / 1024 / 1024 / 1024, 2)
        data["disk_usage"] = round(((total_d - free_d) / total_d) * 100, 1)

        with open("/proc/uptime") as f: u = float(f.read().split()[0])
        dy = int(u // 86400); hr = int((u % 86400) // 3600); mn = int((u % 3600) // 60)
        data["uptime"] = f"{dy}天 {hr}时 {mn}分"
        
        # 执行 Ping 测试
        ping_results = {}
        for name, ip in PING_TARGETS.items():
            ping_results[name] = get_ping_latency(ip)
        data["pings"] = ping_results

    except Exception as e: pass
    return data

def push_data():
    while True:
        try:
            payload = json.dumps(get_sys_info()).encode("utf-8")
            req = urllib.request.Request(MANAGER_URL, data=payload, headers={"Content-Type": "application/json"})
            # ✨ 加入 SSL Context
            with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as r: pass
        except: pass 
        time.sleep(2) 

if __name__ == "__main__":
    push_data()
EOF

# 4. 创建 Systemd 服务
cat > /etc/systemd/system/x-fusion-agent.service << SERVICE_EOF
[Unit]
Description=X-Fusion Probe Agent
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 /root/x_fusion_agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# 5. 启动服务
systemctl daemon-reload
systemctl enable x-fusion-agent
systemctl restart x-fusion-agent

echo "✅ 探针 Agent (v3.1) 已启动！正在向 $PUSH_API 推送数据..."
