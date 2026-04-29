#!/bin/bash
set -euo pipefail


# ==============================================================================
# X-Fusion Panel 正式版 (全量同步 + 域名 HTTPS + ARM 适配)
# ==============================================================================

PROJECT_NAME="x-fusion-panel"
INSTALL_DIR="/root/${PROJECT_NAME}"
GIT_REPO_URL="https://github.com/SIJULY/x-fusion-panel.git"

# Caddy 配置标记
CADDY_MARK_START="# X-Fusion Panel Config Start"
CADDY_MARK_END="# X-Fusion Panel Config End"

RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
PLAIN="\033[0m"

print_info() { echo -e "${BLUE}[信息]${PLAIN} $1"; }
print_success() { echo -e "${GREEN}[成功]${PLAIN} $1"; }
print_error() { echo -e "${RED}[错误]${PLAIN} $1"; exit 1; }

get_compose_cmd() {
    if docker compose version &> /dev/null; then
        echo "docker compose"
    elif command -v docker-compose &> /dev/null; then
        echo "docker-compose"
    else
        return 1
    fi
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        print_info "正在安装 Docker..."
        curl -fsSL https://get.docker.com | bash
        systemctl enable docker && systemctl start docker
    fi

    if ! get_compose_cmd &> /dev/null; then
        print_info "正在安装 Docker Compose..."
        apt-get update
        apt-get install -y docker-compose-plugin || apt-get install -y docker-compose || true
    fi

    if ! get_compose_cmd &> /dev/null; then
        print_error "Docker Compose 安装失败，请手动安装 docker compose 或 docker-compose 后重试"
    fi
}

install_source_code() {
    if ! command -v git &> /dev/null; then
        apt-get update && apt-get install -y git
    fi
    print_info "正在拉取源码仓库..."
    rm -rf "${INSTALL_DIR}"
    git clone "${GIT_REPO_URL}" "${INSTALL_DIR}"
}

update_source_code() {
    if ! command -v git &> /dev/null; then
        apt-get update && apt-get install -y git
    fi

    [ ! -d "${INSTALL_DIR}/.git" ] && print_error "未检测到已安装面板，请先执行安装"

    print_info "正在更新程序文件（保留 data 与本地配置）..."
    local backup_dir
    backup_dir=$(mktemp -d)

    [ -f "${INSTALL_DIR}/docker-compose.yml" ] && cp "${INSTALL_DIR}/docker-compose.yml" "${backup_dir}/docker-compose.yml"
    [ -f "${INSTALL_DIR}/Caddyfile" ] && cp "${INSTALL_DIR}/Caddyfile" "${backup_dir}/Caddyfile"

    cd "${INSTALL_DIR}"
    git fetch origin
    git reset --hard origin/main

    [ -f "${backup_dir}/docker-compose.yml" ] && cp "${backup_dir}/docker-compose.yml" "${INSTALL_DIR}/docker-compose.yml"
    [ -f "${backup_dir}/Caddyfile" ] && cp "${backup_dir}/Caddyfile" "${INSTALL_DIR}/Caddyfile"

    rm -rf "${backup_dir}"
}

generate_compose() {
    local BIND_IP=$1
    local PORT=$2
    local USER=$3
    local PASS=$4
    local SECRET=$5
    local ENABLE_CADDY=$6

    cat > ${INSTALL_DIR}/docker-compose.yml << EOF
services:
  x-fusion-panel:
    build: 
      context: .
      dockerfile: Dockerfile
    image: x-fusion-panel:local
    container_name: x-fusion-panel
    restart: always
    ports:
      - "${BIND_IP}:${PORT}:8080"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
      - XUI_USERNAME=${USER}
      - XUI_PASSWORD=${PASS}
      - XUI_SECRET_KEY=${SECRET}

  subconverter:
    image: tindy2013/subconverter:latest
    container_name: subconverter
    restart: always
    expose:
      - "25500"
    environment:
      - TZ=Asia/Shanghai
EOF

    if [ "$ENABLE_CADDY" == "true" ]; then
        cat >> ${INSTALL_DIR}/docker-compose.yml << EOF

  caddy:
    image: caddy:latest
    container_name: caddy
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - ./caddy_data:/data
    depends_on:
      - x-fusion-panel
EOF
    fi
}

configure_caddy() {
    local DOMAIN=$1
    local DOCKER_CADDY_FILE="${INSTALL_DIR}/Caddyfile"
    # 清理旧配置
    [ -f "$DOCKER_CADDY_FILE" ] && sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$DOCKER_CADDY_FILE"
    
    cat >> "$DOCKER_CADDY_FILE" << EOF
${CADDY_MARK_START}
${DOMAIN} {
    encode gzip
    handle_path /convert* {
        rewrite * /sub
        reverse_proxy subconverter:25500 
    }
    handle {
        reverse_proxy x-fusion-panel:8080
    }
}
${CADDY_MARK_END}
EOF
}

install_panel() {
    [ "$(id -u)" -ne 0 ] && print_error "请用 root 运行"
    check_docker
    install_source_code
    mkdir -p ${INSTALL_DIR}/data

    # 账号配置
    echo -e "${YELLOW}--- 基础配置 ---${PLAIN}"
    read -p "设置账号 [admin]: " admin_user
    admin_user=${admin_user:-admin}
    read -p "设置密码 [admin]: " admin_pass
    admin_pass=${admin_pass:-admin}
    def_key=$(cat /proc/sys/kernel/random/uuid | tr -d '-')
    read -p "设置密钥 (回车随机): " secret_key
    secret_key=${secret_key:-$def_key}

    # 访问方式配置
    echo -e "\n${YELLOW}--- 访问方式 ---${PLAIN}"
    echo "1) IP + 端口 (直接访问)"
    echo "2) 域名 + 自动 HTTPS (Caddy)"
    read -p "请选择 [1]: " net_choice
    net_choice=${net_choice:-1}

    if [ "$net_choice" == "2" ]; then
        read -p "请输入域名: " domain
        [ -z "$domain" ] && print_error "域名不能为空"
        configure_caddy "$domain"
        generate_compose "127.0.0.1" "8081" "$admin_user" "$admin_pass" "$secret_key" "true"
        ACCESS_URL="https://${domain}"
    else
        read -p "开放端口 [8081]: " port
        port=${port:-8081}
        generate_compose "0.0.0.0" "$port" "$admin_user" "$admin_pass" "$secret_key" "false"
        IP=$(curl -s ifconfig.me)
        ACCESS_URL="http://${IP}:${port}"
    fi

    print_info "开始构建镜像并启动服务..."
    cd ${INSTALL_DIR}
    COMPOSE_CMD="$(get_compose_cmd)"
    ${COMPOSE_CMD} up -d --build

    print_success "X-Fusion Panel 安装完成！"
    echo -e "访问地址: ${GREEN}${ACCESS_URL}${PLAIN}"
    echo -e "初始账号: ${admin_user}"
    echo -e "初始密码: ${admin_pass}"
}

update_panel() {
    [ "$(id -u)" -ne 0 ] && print_error "请用 root 运行"
    check_docker
    update_source_code
    mkdir -p ${INSTALL_DIR}/data

    print_info "开始重建并更新服务..."
    cd ${INSTALL_DIR}
    COMPOSE_CMD="$(get_compose_cmd)"
    ${COMPOSE_CMD} up -d --build

    print_success "X-Fusion Panel 更新完成！"
    echo -e "数据目录已保留: ${GREEN}${INSTALL_DIR}/data${PLAIN}"
    echo -e "本地部署配置已保留: ${GREEN}${INSTALL_DIR}/docker-compose.yml${PLAIN}"
}

# --- 菜单逻辑 ---
clear
echo "========================================="
echo -e "${BLUE}    X-Fusion Panel 管理脚本 ${PLAIN}"
echo "========================================="
echo "  1. 新安装面板"
echo "  2. 更新面板（保留数据）"
echo "  3. 卸载面板"
echo "  0. 退出"
read -p "选择: " choice

case $choice in
    1) install_panel ;;
    2) update_panel ;;
    3)
        if [ -d "${INSTALL_DIR}" ]; then
            if get_compose_cmd &> /dev/null; then
                COMPOSE_CMD="$(get_compose_cmd)"
                (cd ${INSTALL_DIR} && ${COMPOSE_CMD} down) || true
            fi
            rm -rf ${INSTALL_DIR}
        fi
        print_success "已卸载"
        ;;
    0) exit 0 ;;
    *) print_error "无效选项" ;;
esac
