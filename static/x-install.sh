#!/usr/bin/env bash
set -u

# X-Fusion Probe Agent one-click installer.
# Usage:
#   curl -sL https://raw.githubusercontent.com/SIJULY/x-fusion-panel/main/static/x-install.sh | bash -s -- "TOKEN" "REGISTER_URL" "PING_CT" "PING_CU" "PING_CM"

TOKEN="${1:-}"
REGISTER_URL="${2:-}"
PING_CT="${3:-202.102.192.68}"
PING_CU="${4:-112.122.10.26}"
PING_CM="${5:-211.138.180.2}"

log() { printf '\033[32m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[WARN]\033[0m %s\n' "$*"; }
err() { printf '\033[31m[ERROR]\033[0m %s\n' "$*" >&2; }

if [ -z "$TOKEN" ] || [ -z "$REGISTER_URL" ]; then
  err "参数缺失：TOKEN 和 REGISTER_URL 必填"
  err "示例：bash x-install.sh TOKEN http://panel.example.com:8080/api/probe/register"
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo bash -s -- "$TOKEN" "$REGISTER_URL" "$PING_CT" "$PING_CU" "$PING_CM" < "$0"
  fi
  err "需要 root 权限运行"
  exit 1
fi

MANAGER_URL="${REGISTER_URL%/api/probe/register}"
if [ "$MANAGER_URL" = "$REGISTER_URL" ]; then
  MANAGER_URL="${REGISTER_URL%/}"
fi
PUSH_URL="${MANAGER_URL%/}/api/probe/push"

install_deps() {
  log "安装基础依赖..."
  if [ -f /etc/debian_version ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y >/dev/null 2>&1 || true
    apt-get install -y python3 curl iputils-ping util-linux sqlite3 ca-certificates >/dev/null 2>&1 || true
  elif [ -f /etc/redhat-release ]; then
    yum install -y python3 curl iputils util-linux sqlite sqlite ca-certificates >/dev/null 2>&1 || true
  elif [ -f /etc/alpine-release ]; then
    apk add --no-cache python3 curl iputils util-linux sqlite ca-certificates >/dev/null 2>&1 || true
  fi
}

detect_ip() {
  local ip=""
  for endpoint in \
    "https://api.ipify.org" \
    "https://ifconfig.me/ip" \
    "http://checkip.amazonaws.com"; do
    ip="$(curl -fsSL --max-time 5 "$endpoint" 2>/dev/null | tr -d '[:space:]' || true)"
    if printf '%s' "$ip" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
      printf '%s' "$ip"
      return 0
    fi
  done
  hostname -I 2>/dev/null | awk '{print $1}'
}

PUBLIC_IP="$(detect_ip)"
SERVER_URL="http://${PUBLIC_IP}:54321"

register_probe() {
  log "向主控注册探针：$REGISTER_URL"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --max-time 10 \
      -H 'Content-Type: application/json' \
      -d "{\"token\":\"$TOKEN\"}" \
      "$REGISTER_URL" >/tmp/x_fusion_register.log 2>&1 || {
        warn "注册接口请求失败，Agent 仍会继续安装并主动上报。详情：$(cat /tmp/x_fusion_register.log 2>/dev/null)"
        return 0
      }
  fi
}

write_agent() {
  log "写入 Agent：/root/x_fusion_agent.py"
  cat > /root/x_fusion_agent.py <<PYTHON_EOF
import time, json, os, subprocess, re, platform, sqlite3
import urllib.request, ssl

MANAGER_URL = "${PUSH_URL}"
TOKEN = "${TOKEN}"
SERVER_URL = "${SERVER_URL}"
PING_TARGETS = {"电信": "${PING_CT}", "联通": "${PING_CU}", "移动": "${PING_CM}"}

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def get_cpu_model():
    try:
        try:
            out = subprocess.check_output("lscpu", shell=True, stderr=subprocess.DEVNULL).decode(errors="ignore")
            for line in out.splitlines():
                if "Model name:" in line: return line.split(":", 1)[1].strip()
        except Exception: pass
        with open("/proc/cpuinfo", "r", errors="ignore") as f:
            for line in f:
                if "model name" in line or "Hardware" in line:
                    return line.split(":", 1)[1].strip()
    except Exception: pass
    return "Unknown"

def get_os_distro():
    try:
        with open("/etc/os-release", errors="ignore") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception: pass
    return platform.platform()

STATIC_CACHE = {"cpu_model": get_cpu_model(), "arch": platform.machine(), "os": get_os_distro(), "virt": "Unknown"}
try:
    v = subprocess.check_output("systemd-detect-virt", shell=True, stderr=subprocess.DEVNULL).decode().strip()
    if v and v != "none": STATIC_CACHE["virt"] = v
except Exception: pass

def get_ping(target):
    try:
        ip = target.split("://")[-1].split(":")[0]
        res = subprocess.run("ping -c 1 -W 1 " + ip, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0:
            match = re.search(r"time=([\d.]+)", res.stdout.decode(errors="ignore"))
            if match: return int(float(match.group(1)))
    except Exception: pass
    return -1

def get_network_bytes():
    r = t = 0
    try:
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                cols = line.split(":")
                if len(cols) < 2 or cols[0].strip() == "lo": continue
                parts = cols[1].split()
                if len(parts) >= 9:
                    r += int(parts[0]); t += int(parts[8])
    except Exception: pass
    return r, t

def get_xui_rows():
    for db_path in ["/etc/x-ui/x-ui.db", "/usr/local/x-ui/bin/x-ui.db", "/usr/local/x-ui/x-ui.db"]:
        if not os.path.exists(db_path): continue
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT id, up, down, total, remark, enable, protocol, port, settings, stream_settings, expiry_time, listen FROM inbounds")
            rows = cur.fetchall(); conn.close()
            return [{"id": x[0], "up": x[1], "down": x[2], "total": x[3], "remark": x[4], "enable": x[5] == 1, "protocol": x[6], "port": x[7], "settings": x[8], "streamSettings": x[9], "expiryTime": x[10], "listen": x[11]} for x in rows]
        except Exception: pass
    return None

def get_info():
    data = {"token": TOKEN, "server_url": SERVER_URL, "static": STATIC_CACHE}
    try:
        net_in_1, net_out_1 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]; tot1, idle1 = sum(fs), fs[3]
        time.sleep(1)
        net_in_2, net_out_2 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]; tot2, idle2 = sum(fs), fs[3]
        data["cpu_usage"] = round((1 - (idle2-idle1)/max(tot2-tot1, 1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        data["net_speed_in"] = net_in_2 - net_in_1; data["net_speed_out"] = net_out_2 - net_out_1
        data["net_total_in"] = net_in_2; data["net_total_out"] = net_out_2
        with open("/proc/loadavg") as f: data["load_1"] = float(f.read().split()[0])
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                p = line.split()
                if len(p) >= 2: mem[p[0].rstrip(":")] = int(p[1])
        total = mem.get("MemTotal", 1); avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        data["mem_total"] = round(total/1024/1024, 2); data["mem_usage"] = round(((total-avail)/total)*100, 1)
        data["swap_total"] = round(mem.get("SwapTotal", 0)/1024/1024, 2); data["swap_free"] = round(mem.get("SwapFree", 0)/1024/1024, 2)
        st = os.statvfs("/"); disk_total = st.f_blocks * st.f_frsize; disk_free = st.f_bavail * st.f_frsize
        data["disk_total"] = round(disk_total/1024/1024/1024, 2); data["disk_usage"] = round(((disk_total-disk_free)/disk_total)*100, 1)
        with open("/proc/uptime") as f: up = float(f.read().split()[0])
        d = int(up//86400); h = int((up%86400)//3600); m = int((up%3600)//60)
        data["uptime"] = f"{d}天 {h}时 {m}分"
        data["pings"] = {k: get_ping(v) for k, v in PING_TARGETS.items()}
        xui = get_xui_rows()
        if xui is not None: data["xui_data"] = xui
    except Exception: pass
    return data

def push():
    while True:
        try:
            body = json.dumps(get_info()).encode("utf-8")
            req = urllib.request.Request(MANAGER_URL, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10, context=ssl_ctx).read()
        except Exception:
            pass
        time.sleep(5)

if __name__ == "__main__":
    push()
PYTHON_EOF
}

install_service() {
  log "安装 systemd 服务..."
  cat > /etc/systemd/system/x-fusion-agent.service <<'SERVICE_EOF'
[Unit]
Description=X-Fusion Probe Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 /root/x_fusion_agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

  systemctl daemon-reload
  systemctl enable x-fusion-agent >/dev/null 2>&1 || true
  systemctl restart x-fusion-agent
}

install_deps
register_probe
write_agent
install_service

sleep 1
if systemctl is-active --quiet x-fusion-agent; then
  log "X-Fusion 探针安装完成，正在向 ${PUSH_URL} 上报"
else
  err "Agent 服务启动失败，请执行：journalctl -u x-fusion-agent -n 80 --no-pager"
  exit 1
fi