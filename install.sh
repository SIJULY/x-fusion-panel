#!/bin/bash

# ==============================================================================
# X-UI Manager Pro 一键安装/管理脚本 (最终完美版)
# ==============================================================================

# --- 全局变量 ---
PROJECT_NAME="xui_manager"
INSTALL_DIR="/root/${PROJECT_NAME}"
REPO_URL="https://raw.githubusercontent.com/SIJULY/xui_manager/main"
CADDY_CONFIG_PATH="/etc/caddy/Caddyfile"
CADDY_MARK_START="# X-UI Manager Config Start"
CADDY_MARK_END="# X-UI Manager Config End"

# 颜色定义
RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
PLAIN="\033[0m"

# --- 辅助函数 ---
print_info() { echo -e "${BLUE}[信息]${PLAIN} $1"; }
print_success() { echo -e "${GREEN}[成功]${PLAIN} $1"; }
print_warning() { echo -e "${YELLOW}[警告]${PLAIN} $1"; }
print_error() { echo -e "${RED}[错误]${PLAIN} $1"; exit 1; }

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        print_error "此脚本必须以 root 用户身份运行。"
    fi
}

# 智能等待 APT 锁（带超时询问功能）
wait_for_apt_lock() {
    echo -e "${BLUE}[信息] 正在检查系统 APT 锁状态...${PLAIN}"
    
    local wait_time=0
    local timeout=60  # 设置超时时间为 60 秒
    
    while fuser /var/lib/dpkg/lock >/dev/null 2>&1 || \
          fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
          fuser /var/lib/apt/lists/lock >/dev/null 2>&1 ; do
        
        echo -e "${YELLOW}[警告] 系统后台正在运行更新进程 (apt/dpkg)，已等待 ${wait_time} 秒...${PLAIN}"
        sleep 10
        ((wait_time+=10))
        
        # 如果等待超过超时时间，询问用户
        if [ "$wait_time" -ge "$timeout" ]; then
            echo -e "${RED}[错误] APT 锁已被占用超过 ${timeout} 秒。${PLAIN}"
            echo -e "${YELLOW}这可能是后台更新正在进行，也可能是之前的进程卡死了。${PLAIN}"
            read -p "是否尝试强制结束占用进程并删除锁文件？(y/n) [推荐先选 n 继续等待]: " force_unlock
            
            if [ "$force_unlock" == "y" ]; then
                echo -e "${RED}[警告] 正在执行强制解锁...${PLAIN}"
                # 1. 杀进程
                killall apt apt-get dpkg 2>/dev/null
                # 2. 删锁
                rm -f /var/lib/apt/lists/lock
                rm -f /var/cache/apt/archives/lock
                rm -f /var/lib/dpkg/lock*
                # 3. 修复数据库
                dpkg --configure -a
                echo -e "${GREEN}[成功] 已执行强制清理，尝试继续安装...${PLAIN}"
                break
            else
                echo -e "${BLUE}[信息] 选择继续等待...${PLAIN}"
                wait_time=0 # 重置计时器
            fi
        fi
    done
    
    echo -e "${GREEN}[成功] APT 锁已释放，继续执行任务。${PLAIN}"
}
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_info "未检测到 Docker，正在安装..."
        wait_for_apt_lock
        curl -fsSL https://get.docker.com | bash
        systemctl enable docker
        systemctl start docker
    fi
    if ! docker compose version &> /dev/null; then
        print_info "未检测到 Docker Compose 插件，正在安装..."
        wait_for_apt_lock
        apt-get update && apt-get install -y docker-compose-plugin
    fi
}

# --- 核心功能函数 ---

deploy_base() {
    check_docker
    mkdir -p ${INSTALL_DIR}/app
    mkdir -p ${INSTALL_DIR}/data
    cd ${INSTALL_DIR}

    print_info "正在拉取最新代码..."
    # 下载 Dockerfile 和 requirements.txt (这两个通常变动不大，可以保留)
    curl -sS -O ${REPO_URL}/Dockerfile
    curl -sS -O ${REPO_URL}/requirements.txt

    # ⚠️⚠️⚠️ 注释掉下面这一行，防止它覆盖你已经上传好的 main.py ⚠️⚠️⚠️
    # curl -sS -o app/main.py ${REPO_URL}/app/main.py  <-- 加井号注释掉

    if [ ! -f "data/servers.json" ]; then echo "[]" > data/servers.json; fi
    if [ ! -f "data/subscriptions.json" ]; then echo "[]" > data/subscriptions.json; fi
}

# [核心修改] 动态生成 docker-compose，注入账号密码环境变量
generate_compose() {
    local BIND_IP=$1
    local PORT=$2
    local USER=$3
    local PASS=$4

    cat > ${INSTALL_DIR}/docker-compose.yml << EOF
version: '3.8'
services:
  xui-manager:
    build: .
    container_name: xui_manager
    restart: always
    ports:
      - "${BIND_IP}:${PORT}:8080"
    volumes:
      - ./data/servers.json:/app/servers.json
      - ./data/subscriptions.json:/app/subscriptions.json
      - ./data/nodes_cache.json:/app/nodes_cache.json
    environment:
      - TZ=Asia/Shanghai
      - ADMIN_USER=${USER}
      - ADMIN_PASS=${PASS}
EOF
}

install_caddy_if_needed() {
    if ! command -v caddy &> /dev/null; then
        print_info "未检测到 Caddy，正在安装..."
        wait_for_apt_lock
        apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
        apt-get update
        apt-get install -y caddy
        print_success "Caddy 安装完成。"
    fi
    if ! systemctl is-active --quiet caddy; then
        systemctl enable --now caddy || print_warning "Caddy 启动尝试失败。"
    fi
}

configure_caddy() {
    local DOMAIN=$1
    local PORT=$2
    if [ -f "$CADDY_CONFIG_PATH" ]; then
        sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$CADDY_CONFIG_PATH"
    fi
    if [ -s "$CADDY_CONFIG_PATH" ] && [ "$(tail -c 1 "$CADDY_CONFIG_PATH")" != "" ]; then
        echo "" >> "$CADDY_CONFIG_PATH"
    fi
    cat >> "$CADDY_CONFIG_PATH" << EOF
${CADDY_MARK_START}
${DOMAIN} {
    reverse_proxy 127.0.0.1:${PORT}
}
${CADDY_MARK_END}
EOF
    systemctl reload caddy
}

# --- 菜单动作 ---

install_panel() {
    wait_for_apt_lock
    deploy_base

    echo "------------------------------------------------"
    # [核心修改] 获取账号密码
    read -p "请设置面板登录账号 [admin]: " admin_user
    admin_user=${admin_user:-admin}
    read -p "请设置面板登录密码 [admin]: " admin_pass
    admin_pass=${admin_pass:-admin}
    echo "------------------------------------------------"

    echo "请选择访问方式："
    echo "  1) IP + 端口访问"
    echo "  2) 域名访问 (自动HTTPS)"
    read -p "请输入选项 [2]: " net_choice
    net_choice=${net_choice:-2}

    if [ "$net_choice" == "1" ]; then
        read -p "请输入开放端口 [8081]: " port
        port=${port:-8081}
        
        generate_compose "0.0.0.0" "$port" "$admin_user" "$admin_pass"
        
        print_info "正在启动容器..."
        docker compose up -d --build
        
        ip_addr=$(curl -s ifconfig.me)
        print_success "安装成功！"
        echo -e "登录地址: http://${ip_addr}:${port}"
        echo -e "账号: ${admin_user}"
        echo -e "密码: ${admin_pass}"

    else
        read -p "请输入您的域名 (例如 panel.test.com): " domain
        if [ -z "$domain" ]; then print_error "域名不能为空"; fi
        read -p "请输入内部运行端口 [8081]: " port
        port=${port:-8081}

        generate_compose "127.0.0.1" "$port" "$admin_user" "$admin_pass"
        
        print_info "正在启动容器..."
        docker compose up -d --build

        install_caddy_if_needed
        configure_caddy "$domain" "$port"

        print_success "安装成功！"
        echo -e "登录地址: https://${domain}"
        echo -e "账号: ${admin_user}"
        echo -e "密码: ${admin_pass}"
    fi
}

update_panel() {
    if [ ! -d "${INSTALL_DIR}" ]; then print_error "未检测到安装目录。"; fi
    print_info "正在重建容器..."
    cd ${INSTALL_DIR}
    docker compose down
    docker compose up -d --build
    print_success "更新完成！"
}

uninstall_panel() {
    read -p "确定要卸载吗？(y/n): " confirm
    if [ "$confirm" != "y" ]; then exit 0; fi
    if [ -d "${INSTALL_DIR}" ]; then
        cd ${INSTALL_DIR}
        docker compose down
        cd /root
        rm -rf ${INSTALL_DIR}
    fi
    if [ -f "$CADDY_CONFIG_PATH" ]; then
        sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$CADDY_CONFIG_PATH"
        systemctl reload caddy
    fi
    print_success "卸载完成。"
}

# --- 主菜单 ---
check_root
clear
echo -e "${GREEN} X-UI Manager Pro 一键管理脚本 ${PLAIN}"
echo -e "  1. 安装面板"
echo -e "  2. 重启/更新容器"
echo -e "  3. 卸载面板"
echo -e "  0. 退出"
read -p "请输入选项: " choice

case $choice in
    1) install_panel ;;
    2) update_panel ;;
    3) uninstall_panel ;;
    0) exit 0 ;;
    *) print_error "无效选项" ;;
esac
