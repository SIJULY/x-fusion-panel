import json
import os
import uuid
import base64
import asyncio
import logging
import requests
import urllib3
import shutil
import re
import sys
import socket
import random
import pyotp
import qrcode
import time
import io
import paramiko
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from urllib.parse import urlparse, quote
from nicegui import ui, run, app, Client
from fastapi import Response, Request
from fastapi.responses import RedirectResponse
from collections import Counter




# ================= 配置区域 (Docker 强制版) =================
DATA_DIR = '/app/data'
print(f"🔒 [System] 强制锁定数据目录: {DATA_DIR}")
# 定义文件路径
CONFIG_FILE = os.path.join(DATA_DIR, 'servers.json')
SUBS_FILE = os.path.join(DATA_DIR, 'subscriptions.json')
NODES_CACHE_FILE = os.path.join(DATA_DIR, 'nodes_cache.json')
ADMIN_CONFIG_FILE = os.path.join(DATA_DIR, 'admin_config.json')
GLOBAL_SSH_KEY_FILE = os.path.join(DATA_DIR, 'global_ssh_key')
# 环境变量
AUTO_REGISTER_SECRET = os.getenv('XUI_SECRET_KEY', 'sijuly_secret_key_default')
ADMIN_USER = os.getenv('XUI_USERNAME', 'admin')
ADMIN_PASS = os.getenv('XUI_PASSWORD', 'admin')

SERVERS_CACHE = []
SUBS_CACHE = []
NODES_DATA = {}
ADMIN_CONFIG = {}
GLOBAL_UI_VERSION = time.time()

# ================= 定义全局进程池变量  =================
PROCESS_POOL = None 

# ================= 全局 同步 Ping 函数 =================
def sync_ping_worker(host, port):
    try:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3) # 3秒超时
        sock.connect((host, int(port)))
        sock.close()
        return int((time.time() - start) * 1000)
    except:
        return -1

# ================= 辅助：全局 GeoIP 和 智能命名逻辑 (已升级：增加省份识别) =================
def fetch_geo_from_ip(host):
    try:
        clean_host = host.split('://')[-1].split(':')[0]
        if clean_host.startswith('192.168.') or clean_host.startswith('10.') or clean_host == '127.0.0.1':
            return None
        if clean_host in IP_GEO_CACHE:
            return IP_GEO_CACHE[clean_host]
        
        with requests.Session() as s:
            # ✨ 修改点：fields 里增加了 regionName (省份/州) 和 city (城市)
            url = f"http://ip-api.com/json/{clean_host}?lang=zh-CN&fields=status,lat,lon,country,regionName,city"
            r = s.get(url, timeout=3)
            if r.status_code == 200:
                data = r.json()
                if data.get('status') == 'success':
                    # ✨ 修改点：返回结果包含了 国家 和 省份
                    result = (data['lat'], data['lon'], data['country'], data.get('regionName', '未知'))
                    IP_GEO_CACHE[clean_host] = result
                    return result
    except: pass
    return None

# =================强制 GeoIP 命名与分组任务  =================
async def force_geoip_naming_task(server_conf, max_retries=10):
    """
    强制执行 GeoIP 解析，直到成功或达到最大重试次数。
    成功后：
    1. 命名格式：🇺🇸 美国-1, 🇭🇰 香港-2
    2. 分组：自动分入对应国家组
    """
    url = server_conf['url']
    logger.info(f"🌍 [强制修正] 开始处理: {url} (目标: 国旗+国家+序号)")
    
    for i in range(max_retries):
        try:
            # 1. 查询 GeoIP
            geo_info = await run.io_bound(fetch_geo_from_ip, url)
            
            if geo_info:
                # geo_info 格式: (lat, lon, 'United States')
                country_raw = geo_info[2]
                
                # 2. 获取标准化的 "国旗+国家" 字符串，例如 "🇺🇸 美国"
                flag_group = get_flag_for_country(country_raw)
                
                # 3. 计算序号 (查找现有多少个同类服务器)
                # 逻辑：遍历所有服务器，看有多少个名字是以 "🇺🇸 美国" 开头的
                count = 1
                for s in SERVERS_CACHE:
                    # 排除自己 (如果是刚加进去的，可能已经存在于列表中，需要注意去重逻辑，这里简单处理)
                    if s is not server_conf and s.get('name', '').startswith(flag_group):
                        count += 1
                
                # 4. 生成最终名称
                final_name = f"{flag_group}-{count}"
                
                # 5. 应用更改
                old_name = server_conf.get('name', '')
                if old_name != final_name:
                    server_conf['name'] = final_name
                    server_conf['group'] = flag_group # 自动分组
                    server_conf['_detected_region'] = country_raw # 记录原始地区信息
                    
                    # 保存并刷新
                    await save_servers()
                    await refresh_dashboard_ui()
                    try: render_sidebar_content.refresh()
                    except: pass
                    
                    logger.info(f"✅ [强制修正] 成功: {old_name} -> {final_name} (第 {i+1} 次尝试)")
                    return # 成功退出
            
            # 如果没查到，打印日志
            logger.warning(f"⏳ [强制修正] 第 {i+1} 次解析 IP 归属地失败，3秒后重试...")
            
        except Exception as e:
            logger.error(f"❌ [强制修正] 异常: {e}")

        # 等待后重试
        await asyncio.sleep(3)

    logger.warning(f"⚠️ [强制修正] 最终失败: 达到最大重试次数，保持原名 {server_conf.get('name')}")

# ================= 智能域名自适应侦测核心 =================
def get_dynamic_origin():
    """
    智能侦测当前面板的真实访问地址（适配开源分发）。
    侦测优先级：
    1. 用户在后台手动设置的 `manager_base_url`
    2. Cloudflare / Nginx 传递的真实协议和域名 (X-Forwarded-Proto / Host)
    3. 默认的 Request Host
    """
    # 1. 优先读取数据库里的配置（如果有）
    saved_url = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
    if saved_url and not ('127.0.0.1' in saved_url or 'localhost' in saved_url):
        # 顺便排除一下旧的硬编码域名，防止残留
        if 'sijuly.nyc.mn' not in saved_url:
            return saved_url

    # 2. 从当前 HTTP 请求中动态提取 (穿透反代)
    try:
        from nicegui import ui
        # 获取当前客户端的原始请求
        req = ui.context.client.request
        
        # 尝试获取经过 Nginx/CF 转发的真实域名和协议
        real_host = req.headers.get('X-Forwarded-Host') or req.headers.get('host')
        real_proto = req.headers.get('X-Forwarded-Proto') or req.url.scheme
        
        if real_host:
            # 自动构建当前网址
            detected_url = f"{real_proto}://{real_host}"
            return detected_url
    except Exception:
        pass

    # 3. 终极兜底（提示性占位，绝对不使用特定域名）
    return "http://{YOUR-DOMAIN-OR-IP}"

# ================= 全局辅助：超级坐标库 =================
LOCATION_COORDS = {
    '🇨🇳': (35.86, 104.19), 'China': (35.86, 104.19), '中国': (35.86, 104.19),
    '🇭🇰': (22.31, 114.16), 'HK': (22.31, 114.16), 'Hong Kong': (22.31, 114.16), '香港': (22.31, 114.16),
    '🇹🇼': (23.69, 120.96), 'TW': (23.69, 120.96), 'Taiwan': (23.69, 120.96), '台湾': (23.69, 120.96),
    '🇯🇵': (36.20, 138.25), 'JP': (36.20, 138.25), 'Japan': (36.20, 138.25), '日本': (36.20, 138.25),
    'Tokyo': (35.68, 139.76), '东京': (35.68, 139.76), 'Osaka': (34.69, 135.50), '大阪': (34.69, 135.50),
    '🇸🇬': (1.35, 103.81), 'SG': (1.35, 103.81), 'Singapore': (1.35, 103.81), '新加坡': (1.35, 103.81),
    '🇰🇷': (35.90, 127.76), 'KR': (35.90, 127.76), 'Korea': (35.90, 127.76), '韩国': (35.90, 127.76),
    'Seoul': (37.56, 126.97), '首尔': (37.56, 126.97),
    '🇮🇳': (20.59, 78.96), 'IN': (20.59, 78.96), 'India': (20.59, 78.96), '印度': (20.59, 78.96),
    '🇮🇩': (-0.78, 113.92), 'ID': (-0.78, 113.92), 'Indonesia': (-0.78, 113.92), '印尼': (-0.78, 113.92),
    '🇲🇾': (4.21, 101.97), 'MY': (4.21, 101.97), 'Malaysia': (4.21, 101.97), '马来西亚': (4.21, 101.97),
    '🇹🇭': (15.87, 100.99), 'TH': (15.87, 100.99), 'Thailand': (15.87, 100.99), '泰国': (15.87, 100.99),
    'Bangkok': (13.75, 100.50), '曼谷': (13.75, 100.50),
    '🇻🇳': (14.05, 108.27), 'VN': (14.05, 108.27), 'Vietnam': (14.05, 108.27), '越南': (14.05, 108.27),
    '🇵🇭': (12.87, 121.77), 'PH': (12.87, 121.77), 'Philippines': (12.87, 121.77), '菲律宾': (12.87, 121.77),
    '🇮🇱': (31.04, 34.85), 'IL': (31.04, 34.85), 'Israel': (31.04, 34.85), '以色列': (31.04, 34.85),
    '🇹🇷': (38.96, 35.24), 'TR': (38.96, 35.24), 'Turkey': (38.96, 35.24), '土耳其': (38.96, 35.24),
    '🇦🇪': (23.42, 53.84), 'AE': (23.42, 53.84), 'UAE': (23.42, 53.84), '阿联酋': (23.42, 53.84),
    'Dubai': (25.20, 55.27), '迪拜': (25.20, 55.27),
    '🇺🇸': (37.09, -95.71), 'US': (37.09, -95.71), 'USA': (37.09, -95.71), 'United States': (37.09, -95.71), '美国': (37.09, -95.71),
    'San Jose': (37.33, -121.88), '圣何塞': (37.33, -121.88), 'Los Angeles': (34.05, -118.24), '洛杉矶': (34.05, -118.24),
    'Phoenix': (33.44, -112.07), '凤凰城': (33.44, -112.07),
    '🇨🇦': (56.13, -106.34), 'CA': (56.13, -106.34), 'Canada': (56.13, -106.34), '加拿大': (56.13, -106.34),
    '🇧🇷': (-14.23, -51.92), 'BR': (-14.23, -51.92), 'Brazil': (-14.23, -51.92), '巴西': (-14.23, -51.92),
    '🇲🇽': (23.63, -102.55), 'MX': (23.63, -102.55), 'Mexico': (23.63, -102.55), '墨西哥': (23.63, -102.55),
    '🇨🇱': (-35.67, -71.54), 'CL': (-35.67, -71.54), 'Chile': (-35.67, -71.54), '智利': (-35.67, -71.54),
    '🇦🇷': (-38.41, -63.61), 'AR': (-38.41, -63.61), 'Argentina': (-38.41, -63.61), '阿根廷': (-38.41, -63.61),
    '🇬🇧': (55.37, -3.43), 'UK': (55.37, -3.43), 'United Kingdom': (55.37, -3.43), '英国': (55.37, -3.43),
    'London': (51.50, -0.12), '伦敦': (51.50, -0.12),
    '🇩🇪': (51.16, 10.45), 'DE': (51.16, 10.45), 'Germany': (51.16, 10.45), '德国': (51.16, 10.45),
    'Frankfurt': (50.11, 8.68), '法兰克福': (50.11, 8.68),
    '🇫🇷': (46.22, 2.21), 'FR': (46.22, 2.21), 'France': (46.22, 2.21), '法国': (46.22, 2.21),
    'Paris': (48.85, 2.35), '巴黎': (48.85, 2.35),
    '🇳🇱': (52.13, 5.29), 'NL': (52.13, 5.29), 'Netherlands': (52.13, 5.29), '荷兰': (52.13, 5.29),
    'Amsterdam': (52.36, 4.90), '阿姆斯特丹': (52.36, 4.90),
    '🇷🇺': (61.52, 105.31), 'RU': (61.52, 105.31), 'Russia': (61.52, 105.31), '俄罗斯': (61.52, 105.31),
    'Moscow': (55.75, 37.61), '莫斯科': (55.75, 37.61),
    '🇮🇹': (41.87, 12.56), 'IT': (41.87, 12.56), 'Italy': (41.87, 12.56), '意大利': (41.87, 12.56),
    'Milan': (45.46, 9.19), '米兰': (45.46, 9.19),
    '🇪🇸': (40.46, -3.74), 'ES': (40.46, -3.74), 'Spain': (40.46, -3.74), '西班牙': (40.46, -3.74),
    'Madrid': (40.41, -3.70), '马德里': (40.41, -3.70),
    '🇸🇪': (60.12, 18.64), 'SE': (60.12, 18.64), 'Sweden': (60.12, 18.64), '瑞典': (60.12, 18.64),
    'Stockholm': (59.32, 18.06), '斯德哥尔摩': (59.32, 18.06),
    '🇨🇭': (46.81, 8.22), 'CH': (46.81, 8.22), 'Switzerland': (46.81, 8.22), '瑞士': (46.81, 8.22),
    'Zurich': (47.37, 8.54), '苏黎世': (47.37, 8.54),
    '🇦🇺': (-25.27, 133.77), 'AU': (-25.27, 133.77), 'Australia': (-25.27, 133.77), '澳大利亚': (-25.27, 133.77), '澳洲': (-25.27, 133.77),
    'Sydney': (-33.86, 151.20), '悉尼': (-33.86, 151.20),
    '🇿🇦': (-30.55, 22.93), 'ZA': (-30.55, 22.93), 'South Africa': (-30.55, 22.93), '南非': (-30.55, 22.93),
    'Johannesburg': (-26.20, 28.04), '约翰内斯堡': (-26.20, 28.04),
}

def get_coords_from_name(name):
    for k in sorted(LOCATION_COORDS.keys(), key=len, reverse=True):
        if k in name: return LOCATION_COORDS[k]
    return None

# ================= 全局变量区 =================
IP_GEO_CACHE = {}
# ✨ 新增：存储仪表盘 UI 元素的引用，让后台能控制前台
DASHBOARD_REFS = {
    'servers': None, 'nodes': None, 'traffic': None, 'subs': None,
    'bar_chart': None, 'pie_chart': None, 'stat_up': None, 'stat_down': None, 'stat_avg': None,
    'map': None, 'map_info': None
}


# ================= 全局 DNS 缓存  ======================
DNS_CACHE = {}
DNS_WAITING_LABELS = {} # 

async def _resolve_dns_bg(host):
    """后台线程池解析 DNS，解析完自动刷新所有绑定的 UI 标签"""
    try:
        # 放到后台线程去跑，绝对不卡主界面
        ip = await run.io_bound(socket.gethostbyname, host)
        DNS_CACHE[host] = ip
        
        #  核心逻辑：解析完成了，通知前台变身！
        if host in DNS_WAITING_LABELS:
            for label in DNS_WAITING_LABELS[host]:
                try:
                    # 检查元素是否还活着 (防止切页后报错)
                    if not label.is_deleted:
                        label.set_text(ip) # 瞬间变成 IP
                except: pass
            
            # 通知完了就清空，释放内存
            del DNS_WAITING_LABELS[host]
            
    except: 
        DNS_CACHE[host] = "failed" # 标记失败，防止反复解析

def get_real_ip_display(url):
    """
    非阻塞获取 IP：
    1. 有缓存 -> 直接返回 IP
    2. 没缓存 -> 先返回域名，同时偷偷启动后台解析任务
    """
    try:
        # 提取域名/IP
        host = url.split('://')[-1].split(':')[0]
        
        # 1. 如果本身就是 IP，直接返回
        import re
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host):
            return host

        # 2. 查缓存
        if host in DNS_CACHE:
            val = DNS_CACHE[host]
            return val if val != "failed" else host
        
        # 3. 没缓存？(系统刚启动)
        # 启动后台任务，并立即返回域名占位
        asyncio.create_task(_resolve_dns_bg(host))
        return host 
        
    except:
        return url

def bind_ip_label(url, label):
    """
    ✨ 新增辅助函数：将 UI Label 绑定到 DNS 监听列表
    用法：在创建 ui.label 后调用 bind_ip_label(url, label)
    """
    try:
        host = url.split('://')[-1].split(':')[0]
        # 如果已经解析过，或者本身是 IP，就不需要监听了
        import re
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host): return
        if host in DNS_CACHE: return
        
        # 加入监听列表
        if host not in DNS_WAITING_LABELS: DNS_WAITING_LABELS[host] = []
        DNS_WAITING_LABELS[host].append(label)
    except: pass

# ================= 获取国旗  =================
def get_flag_for_country(country_name):
    if not country_name: return "🏳️ 未知"
    
    # 1. 正向匹配：检查 Key (例如 API返回 'Singapore', Key 有 'Singapore')
    for k, v in AUTO_COUNTRY_MAP.items():
        if k.upper() == country_name.upper() or k in country_name:
            return v 
    
    # 2. 反向匹配：检查 Value (解决中文匹配问题) 
    # API返回 '新加坡'，虽然 Key 里没有，但 Value '🇸🇬 新加坡' 里包含它！
    for v in AUTO_COUNTRY_MAP.values():
        if country_name in v:
            return v

    # 3. 实在找不到，返回白旗
    return f"🏳️ {country_name}"

# 自动给名称添加国旗
async def auto_prepend_flag(name, url):
    """
    检查名字是否已经包含任意已知国旗。
    - 如果包含：直接返回原名（尊重用户填写或面板自带的国旗）。
    - 如果不包含：根据 IP 归属地自动添加。
    """
    if not name: return name

    # 1. 遍历所有已知国旗，检查名称中是否已存在
    # AUTO_COUNTRY_MAP 的值格式如 "🇺🇸 美国", 我们只取空格前的 emoji
    for v in AUTO_COUNTRY_MAP.values():
        flag_icon = v.split(' ')[0] # 提取 🇺🇸
        if flag_icon in name:
            # logger.info(f"名称 '{name}' 已包含国旗 {flag_icon}，跳过自动添加")
            return name

    # 2. 如果没有国旗，则进行 GeoIP 查询
    try:
        geo_info = await run.io_bound(fetch_geo_from_ip, url)
        if not geo_info: 
            return name # 查不到 IP 信息，原样返回
        
        country_name = geo_info[2]
        flag_group = get_flag_for_country(country_name) 
        flag_icon = flag_group.split(' ')[0] 
        
        # 再次确认（防止 GeoIP 返回的国旗就是名字里有的，虽然上面已经过滤过一次）
        if flag_icon in name:
            return name
            
        return f"{flag_icon} {name}"
    except Exception as e:
        return name

#  智能命名核心逻辑
async def generate_smart_name(server_conf):
    """尝试获取面板节点名，获取不到则用 GeoIP+序号"""
    # 1. 尝试连接面板获取节点名
    try:
        mgr = get_manager(server_conf)
        inbounds = await run_in_bg_executor(mgr.get_inbounds)
        if inbounds and len(inbounds) > 0:
            # 优先找一个有备注的节点
            for node in inbounds:
                if node.get('remark'):
                    # 注意：这里直接返回面板的 remark，不加处理
                    # 后续会交给 auto_prepend_flag 统一处理国旗
                    return node['remark'] 
    except: pass

    # 2. 尝试 GeoIP 命名 
    try:
        geo_info = await run.io_bound(fetch_geo_from_ip, server_conf['url'])
        if geo_info:
            country_name = geo_info[2]
            flag_prefix = get_flag_for_country(country_name) # 这里自带国旗，如 "🇺🇸 美国"
            
            # 计算序号
            count = 1
            for s in SERVERS_CACHE:
                if s.get('name', '').startswith(flag_prefix):
                    count += 1
            return f"{flag_prefix}-{count}"
    except: pass

    # 3. 兜底
    return f"Server-{len(SERVERS_CACHE) + 1}"


# =================  Cloudflare 设置弹窗 =================
def open_cloudflare_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-[500px] p-6 flex flex-col gap-4'):
        with ui.row().classes('items-center gap-2 text-orange-600 mb-2'):
            ui.icon('cloud', size='md')
            ui.label('Cloudflare API 配置').classes('text-lg font-bold')
            
        ui.label('用于自动解析域名、开启 CDN 和设置 SSL (Flexible)。').classes('text-xs text-gray-500')
        
        # 读取现有配置
        cf_token = ui.input('API Token', value=ADMIN_CONFIG.get('cf_api_token', '')).props('outlined dense type=password').classes('w-full')
        ui.label('权限要求: Zone.DNS (Edit), Zone.Settings (Edit)').classes('text-[10px] text-gray-400 ml-1')
        
        cf_domain_root = ui.input('根域名 (例如: example.com)', value=ADMIN_CONFIG.get('cf_root_domain', '')).props('outlined dense').classes('w-full')
        
        async def save_cf():
            ADMIN_CONFIG['cf_api_token'] = cf_token.value.strip()
            ADMIN_CONFIG['cf_root_domain'] = cf_domain_root.value.strip()
            await save_admin_config()
            safe_notify('✅ Cloudflare 配置已保存', 'positive')
            d.close()

        with ui.row().classes('w-full justify-end mt-4'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('保存配置', on_click=save_cf).classes('bg-orange-600 text-white shadow-md')
    d.open()


# ================= SSH 全局配置区域  =================
GLOBAL_SSH_KEY_FILE = 'data/global_ssh_key'

def load_global_key():
    if os.path.exists(GLOBAL_SSH_KEY_FILE):
        with open(GLOBAL_SSH_KEY_FILE, 'r') as f: return f.read()
    return ""

def save_global_key(content):
    with open(GLOBAL_SSH_KEY_FILE, 'w') as f: f.write(content)

# =================  全局SSH密钥设置弹窗  =================
def open_global_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl p-6 flex flex-col gap-4'):
        with ui.row().classes('justify-between items-center w-full border-b pb-2'):
            ui.label('🔐 全局 SSH 密钥设置').classes('text-xl font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
        
        with ui.column().classes('w-full mt-2'):
            ui.label('全局 SSH 私钥').classes('text-sm font-bold text-gray-700')
            ui.label('当服务器未单独配置密钥时，默认使用此密钥连接。').classes('text-xs text-gray-400 mb-2')
            key_input = ui.textarea(placeholder='-----BEGIN OPENSSH PRIVATE KEY-----', value=load_global_key()).classes('w-full font-mono text-xs').props('outlined rows=10')

        async def save_all():
            save_global_key(key_input.value)
            safe_notify('✅ 全局密钥已保存', 'positive')
            d.close()

        ui.button('保存密钥', icon='save', on_click=save_all).classes('w-full bg-slate-900 text-white shadow-lg h-12 mt-2')
    d.open()



# ================= XHTTP-Reality 部署脚本 =================
# 改进点：使用 Here-Doc 处理 JSON，增加换行符清洗，增加 DNS 检查
XHTTP_INSTALL_SCRIPT_TEMPLATE = r"""
#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
export PATH=$PATH:/usr/local/bin

# 0. 自我清洗 (防止 Windows 换行符 \r 导致脚本执行异常)
sed -i 's/\r$//' "$0"

# 1. 基础环境检查与依赖安装
if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y net-tools lsof curl unzip jq uuid-runtime openssl psmisc dnsutils >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y net-tools lsof curl unzip jq psmisc bind-utils >/dev/null 2>&1
fi

# 定义日志
log() { echo -e "\033[32m[DEBUG]\033[0m $1"; }
err() { echo -e "\033[31m[ERROR]\033[0m $1"; }

DOMAIN="$1"
if [ -z "$DOMAIN" ]; then err "域名参数缺失"; exit 1; fi

log "========== 开始部署 XHTTP (V76 稳定版) =========="
log "目标域名: $DOMAIN"

# 2. 端口强制清理 (霸道模式)
if netstat -tlpn | grep -q ":80 "; then
    log "⚠️ 清理 80 端口..."
    fuser -k 80/tcp >/dev/null 2>&1; killall -9 nginx >/dev/null 2>&1; killall -9 xray >/dev/null 2>&1
    sleep 1
fi
if netstat -tlpn | grep -q ":443 "; then
    log "⚠️ 清理 443 端口..."
    fuser -k 443/tcp >/dev/null 2>&1
    sleep 1
fi

PORT_REALITY=443
PORT_XHTTP=80

# 3. 安装/更新 Xray
log "安装最新版 Xray..."
xray_bin="/usr/local/bin/xray"
rm -f "$xray_bin"
arch=$(uname -m); case "$arch" in x86_64) a="64";; aarch64) a="arm64-v8a";; esac
curl -fsSL https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${a}.zip -o /tmp/xray.zip
unzip -qo /tmp/xray.zip -d /tmp/xray
install -m 755 /tmp/xray/xray "$xray_bin"

# 4. 生成密钥与ID
KEYS=$($xray_bin x25519)
PRI_KEY=$(echo "$KEYS" | grep -i "Private" | awk '{print $NF}')
PUB_KEY=$(echo "$KEYS" | grep -i "Public" | awk '{print $NF}')
[ -z "$PUB_KEY" ] && { PRI_KEY=$(echo "$KEYS" | head -n1 | awk '{print $NF}'); PUB_KEY=$(echo "$KEYS" | tail -n1 | awk '{print $NF}'); }

UUID_XHTTP=$(cat /proc/sys/kernel/random/uuid)
UUID_REALITY=$(cat /proc/sys/kernel/random/uuid)
XHTTP_PATH="/$(echo "$UUID_XHTTP" | cut -d- -f1 | tr -d '\n')"
SHORT_ID=$(openssl rand -hex 4)

REALITY_SNI="www.icloud.com"
YOUXUAN_DOMAIN="www.visa.com.hk"

mkdir -p /usr/local/etc/xray
CONFIG_FILE="/usr/local/etc/xray/config.json"

# 5. 写入配置文件 (使用 EOF 块，避免转义错误)
cat > $CONFIG_FILE <<EOF
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "port": $PORT_XHTTP,
      "protocol": "vless",
      "settings": { "clients": [{ "id": "$UUID_XHTTP" }], "decryption": "none" },
      "streamSettings": { "network": "xhttp", "security": "none", "xhttpSettings": { "path": "$XHTTP_PATH", "mode": "auto" } }
    },
    {
      "port": $PORT_REALITY,
      "protocol": "vless",
      "settings": {
        "clients": [{ "id": "$UUID_REALITY", "flow": "xtls-rprx-vision" }],
        "decryption": "none",
        "fallbacks": [{ "dest": $PORT_XHTTP }]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": { "privateKey": "$PRI_KEY", "serverNames": ["$REALITY_SNI"], "shortIds": ["$SHORT_ID"], "target": "$REALITY_SNI:443" }
      }
    }
  ],
  "outbounds": [{ "protocol": "freedom" }]
}
EOF

# 6. 启动服务
cat > /etc/systemd/system/xray.service <<EOF
[Unit]
Description=Xray Service
After=network.target
[Service]
ExecStart=$xray_bin run -c $CONFIG_FILE
Restart=on-failure
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xray >/dev/null 2>&1
systemctl restart xray
sleep 2

# 7. 检查 DNS (诊断)
log "正在检查域名解析: $DOMAIN"
nslookup $DOMAIN 8.8.8.8 >/dev/null 2>&1
if [ $? -ne 0 ]; then
    log "⚠️ 警告: 域名 $DOMAIN 尚未在全球 DNS 生效，连接可能会失败。请稍等几分钟。"
else
    log "✅ 域名解析正常。"
fi

# 8. 生成链接 (JSON 构造优化)
VPS_IP=$(curl -fsSL https://api.ipify.org)

# 使用 cat 生成 JSON，避免 Python 字符串转义干扰
EXTRA_JSON_RAW=$(cat <<EOF
{
  "downloadSettings": {
    "address": "$VPS_IP",
    "port": $PORT_REALITY,
    "network": "xhttp",
    "xhttpSettings": { "path": "$XHTTP_PATH", "mode": "auto" },
    "security": "reality",
    "realitySettings": {
      "serverName": "$REALITY_SNI",
      "fingerprint": "chrome",
      "show": false,
      "publicKey": "$PUB_KEY",
      "shortId": "$SHORT_ID",
      "spiderX": "/",
      "mldsa65Verify": ""
    }
  }
}
EOF
)

# 压缩并编码 JSON
ENC_EXTRA=$(echo "$EXTRA_JSON_RAW" | jq -c . | jq -sRr @uri)
ENC_PATH=$(printf '%s' "$XHTTP_PATH" | jq -sRr @uri)

LINK="vless://${UUID_XHTTP}@${YOUXUAN_DOMAIN}:443?encryption=none&security=tls&sni=${DOMAIN}&type=xhttp&host=${DOMAIN}&path=${ENC_PATH}&mode=auto&extra=${ENC_EXTRA}#XHTTP-Reality"

echo "DEPLOY_SUCCESS_LINK: $LINK"
"""
# ================= VLESS 链接解析器 =================
def parse_vless_link_to_node(link, remark_override=None):
    """将 vless:// 链接解析为面板节点格式的字典"""
    try:
        if not link.startswith("vless://"): return None
        
        # 局部引入依赖，防止报错
        import urllib.parse
        
        # 1. 基础解析：移除协议头
        main_part = link.replace("vless://", "")
        
        # 处理 fragment (#备注)
        remark = "XHTTP-Reality"
        if "#" in main_part:
            main_part, remark = main_part.split("#", 1)
            remark = urllib.parse.unquote(remark)
        
        # 如果传入了强制备注（用户输入的），覆盖原备注
        if remark_override: 
            remark = remark_override

        # 处理 query parameters (?)
        params = {}
        if "?" in main_part:
            main_part, query_str = main_part.split("?", 1)
            params = dict(urllib.parse.parse_qsl(query_str))
        
        # 处理 user@host:port
        if "@" in main_part:
            user_info, host_port = main_part.split("@", 1)
            uuid = user_info
        else:
            return None # 格式不正确

        if ":" in host_port:
            # 使用 rsplit 确保正确处理 host:port
            host, port = host_port.rsplit(":", 1)
        else:
            host = host_port
            port = 443

        # ================= 更新原始链接中的备注 =================
        final_link = link
        if remark_override:
            # 1. 如果原链接里有 #，先去掉旧的
            if "#" in final_link:
                final_link = final_link.split("#")[0]
            # 2. 拼接新的备注 (进行 URL 编码)
            final_link = f"{final_link}#{urllib.parse.quote(remark)}"
        # ==========================================================

        # 2. 构建符合 Panel 格式的 Node 字典
        node = {
            "id": uuid, 
            "remark": remark,
            "port": int(port),
            "protocol": "vless",
            "settings": {
                "clients": [{"id": uuid, "flow": params.get("flow", "")}],
                "decryption": "none"
            },
            "streamSettings": {
                "network": params.get("type", "tcp"),
                "security": params.get("security", "none"),
                "xhttpSettings": {
                    "path": params.get("path", ""),
                    "mode": params.get("mode", "auto"),
                    "host": params.get("host", "")
                },
                "realitySettings": {
                    "serverName": params.get("sni", ""),
                    "shortId": params.get("sid", ""), 
                    "publicKey": params.get("pbk", "") 
                }
            },
            "enable": True,
            "_is_custom": True, 
            "_raw_link": final_link  # 使用更新后的链接
        }
        return node

    except Exception as e:
        # 必须要有 except 块来捕获潜在错误
        print(f"[Error] 解析 VLESS 链接失败: {e}")
        return None

# ================= 部署 XHTTP 弹窗逻辑 (深色完整版 + 端口检测) =================
async def open_deploy_xhttp_dialog(server_conf, callback):
    # 1. 准备 IP
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    real_ip = target_host
    import re, socket
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try: real_ip = await run.io_bound(socket.gethostbyname, target_host)
        except: safe_notify(f"❌ 无法解析 IP: {target_host}", "negative"); return

    # 2. 检查 CF
    cf_handler = CloudflareHandler()
    if not cf_handler.token or not cf_handler.root_domain:
        safe_notify("❌ 请先配置 Cloudflare API", "negative"); return

    # 3. 生成域名
    import random, string
    rand_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    sub_prefix = f"node-{real_ip.replace('.', '-')}-{rand_suffix}"
    target_domain = f"{sub_prefix}.{cf_handler.root_domain}"

    # === 构建弹窗 (深色) ===
    # bg-[#1e293b] border-slate-700
    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # 顶部
        with ui.column().classes('w-full bg-[#0f172a] p-6 gap-2 border-b border-slate-700'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('rocket_launch', size='md').classes('text-blue-400')
                ui.label('部署 XHTTP-Reality (V76)').classes('text-lg font-bold')
            ui.label(f"部署目标: {target_domain}").classes('text-xs text-green-400 font-mono')

        # 内容区
        with ui.column().classes('w-full p-6 gap-4'):
            ui.label('节点备注名称').classes('text-xs font-bold text-slate-500 mb-[-8px]')
            # 输入框适配深色
            remark_input = ui.input(placeholder=f'默认: Reality-{target_domain}').props('outlined dense clearable dark').classes('w-full')
            
            # 日志区 (纯黑背景)
            log_area = ui.log().classes('w-full h-48 bg-black text-green-400 text-[11px] font-mono p-3 rounded border border-slate-700 hidden transition-all')

        # 底部按钮
        with ui.row().classes('w-full p-4 bg-[#0f172a] border-t border-slate-700 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')
            
            # --- 逻辑 A: 执行部署脚本 ---
            async def run_deploy_script():
                try:
                    log_area.push(f"🔄 [CF] 添加解析: {target_domain} -> {real_ip}...")
                    success, msg = await cf_handler.auto_configure(real_ip, sub_prefix)
                    if not success: raise Exception(f"CF配置失败: {msg}")
                    
                    log_area.push(f"🚀 [SSH] 开始执行安装脚本...")
                    
                    deploy_cmd = f"""
cat > /tmp/install_xhttp.sh << 'EOF_SCRIPT'
{XHTTP_INSTALL_SCRIPT_TEMPLATE}
EOF_SCRIPT
bash /tmp/install_xhttp.sh "{target_domain}"
"""
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, deploy_cmd))
                    
                    if success:
                        match = re.search(r'DEPLOY_SUCCESS_LINK: (vless://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("✅ 部署成功！正在保存...")
                            
                            custom_name = remark_input.value.strip()
                            final_remark = custom_name if custom_name else f"Reality-{target_domain}"
                            node_data = parse_vless_link_to_node(link, remark_override=final_remark)
                            
                            if node_data:
                                if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                                server_conf['custom_nodes'].append(node_data)
                                await save_servers()
                                safe_notify(f"✅ 节点已添加", "positive")
                                await asyncio.sleep(1)
                                d.close()
                                if callback: await callback()
                            else: log_area.push("❌ 链接解析失败")
                        else:
                            log_area.push("❌ 未捕获链接，请检查日志")
                            log_area.push(output[-500:])
                    else:
                        log_area.push(f"❌ SSH 执行出错: {output}")
                except Exception as e:
                    log_area.push(f"❌ 异常: {str(e)}")
                finally:
                    btn_deploy.props(remove='loading')
                    btn_cancel.enable()

            # --- 逻辑 B: 端口检测与启动 ---
            async def start_process():
                btn_cancel.disable()
                btn_deploy.props('loading')
                log_area.classes(remove='hidden')
                
                log_area.push("🔍 正在检查端口占用 (80/443)...")
                
                check_cmd = "netstat -tlpn | grep -E ':80 |:443 ' || lsof -i :80 -i :443"
                is_occupied = False
                check_output = ""
                
                try:
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, check_cmd))
                    if success and output.strip():
                        is_occupied = True
                        check_output = output.strip()
                except: pass

                if is_occupied:
                    log_area.push("⚠️ 端口被占用！等待确认...")
                    
                    # 深色确认弹窗
                    with ui.dialog() as confirm_d, ui.card().classes('w-96 p-5 border-t-4 border-red-500 shadow-xl bg-[#1e293b]'):
                        with ui.row().classes('items-center gap-2 text-red-500 mb-2'):
                            ui.icon('warning', size='md')
                            ui.label('端口冲突警告').classes('font-bold text-lg')
                        
                        ui.label('检测到 80 或 443 端口被占用：').classes('text-sm text-slate-300 mb-2')
                        
                        # 显示占用详情
                        short_log = "\n".join(check_output.split("\n")[:5])
                        ui.code(short_log).classes('w-full text-xs bg-black text-slate-300 p-2 rounded mb-3')
                        
                        ui.label('继续部署将【强制杀掉】这些进程。').classes('text-xs font-bold text-red-400')

                        with ui.row().classes('w-full justify-end gap-2 mt-4'):
                            ui.button('取消', on_click=lambda: [confirm_d.close(), d.close()]).props('flat color=grey')
                            
                            async def confirm_force():
                                confirm_d.close()
                                log_area.push("⚔️ 用户确认强制霸占，继续...")
                                await run_deploy_script()
                                
                            ui.button('强制部署', color='red', on_click=confirm_force).props('unelevated')
                    
                    confirm_d.open()
                    
                else:
                    log_area.push("✅ 端口空闲，开始部署...")
                    await run_deploy_script()

            btn_deploy = ui.button('开始部署', on_click=start_process).classes('bg-blue-600 text-white shadow-lg font-bold')

    d.open()

# SSH 执行辅助函数 
def _ssh_exec_wrapper(server_conf, cmd):
    client, msg = get_ssh_client_sync(server_conf)
    if not client: return False, msg
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        client.close()
        return True, out + "\n" + err
    except Exception as e:
        return False, str(e)


# ================= XHTTP 卸载脚本 =================
# 修正：只停止服务和删除配置，保留 xray 二进制文件，防止误杀 X-UI
XHTTP_UNINSTALL_SCRIPT = r"""
#!/bin/bash
# 1. 停止服务
systemctl stop xray
systemctl disable xray

# 2. 删除服务文件
rm -f /etc/systemd/system/xray.service
systemctl daemon-reload

# 3. 删除配置文件 (保留 bin 文件以防 X-UI 共用)
rm -rf /usr/local/etc/xray

echo "Xray Service Uninstalled (Binary kept safe)"
"""


# ================= Hysteria 2 安装脚本 =================
HYSTERIA_INSTALL_SCRIPT_TEMPLATE = r"""
#!/bin/bash
# 1. 接收参数
PASSWORD="{password}"
SNI="{sni}"
ENABLE_PORT_HOPPING="{enable_hopping}"
PORT_RANGE_START="{port_range_start}"
PORT_RANGE_END="{port_range_end}"

# 2. 环境清理与依赖安装 (修复核心：同时安装 iptables 和 net-tools)
if [ -f /etc/debian_version ]; then
    apt-get update -y
    # net-tools 包含 netstat，iptables 用于端口跳跃
    apt-get install -y iptables net-tools
elif [ -f /etc/redhat-release ]; then
    yum install -y iptables net-tools
fi

systemctl stop hysteria-server.service 2>/dev/null
rm -rf /etc/hysteria
bash <(curl -fsSL https://get.hy2.sh/)

# 3. 证书生成 (自签)
mkdir -p /etc/hysteria
openssl req -x509 -nodes -newkey ec:<(openssl ecparam -name prime256v1) \
  -keyout /etc/hysteria/server.key \
  -out /etc/hysteria/server.crt \
  -subj "/CN=$SNI" \
  -days 3650
chown hysteria /etc/hysteria/server.key
chown hysteria /etc/hysteria/server.crt

# 4. 端口检测 (现在有了 net-tools，这里就不会报错了)
HY2_PORT=443
if netstat -ulpn | grep -q ":443 "; then
    echo "⚠️ UDP 443 占用，切换至 8443"
    HY2_PORT=8443
fi

# 5. 写入配置
cat << EOF > /etc/hysteria/config.yaml
listen: :$HY2_PORT
tls:
  cert: /etc/hysteria/server.crt
  key: /etc/hysteria/server.key
auth:
  type: password
  password: $PASSWORD
masquerade:
  type: proxy
  proxy:
    url: https://$SNI
    rewriteHost: true
quic:
  initStreamReceiveWindow: 26843545
  maxStreamReceiveWindow: 26843545
  initConnReceiveWindow: 67108864
  maxConnReceiveWindow: 67108864
EOF

# 6. 端口跳跃 (iptables 转发)
if [ "$ENABLE_PORT_HOPPING" == "true" ]; then
    # 清理旧规则
    iptables -t nat -D PREROUTING -p udp --dport $PORT_RANGE_START:$PORT_RANGE_END -j REDIRECT --to-ports $HY2_PORT 2>/dev/null || true
    # 添加新规则 (不限制网卡，强制生效)
    iptables -t nat -A PREROUTING -p udp --dport $PORT_RANGE_START:$PORT_RANGE_END -j REDIRECT --to-ports $HY2_PORT
    
    mkdir -p /etc/iptables
    if command -v iptables-save >/dev/null; then
        iptables-save > /etc/iptables/rules.v4
    fi
fi

# 7. 启动
systemctl enable --now hysteria-server.service
sleep 2

# 8. 输出链接
if systemctl is-active --quiet hysteria-server.service; then
    PUBLIC_IP=$(curl -s https://api.ipify.org)
    
    CLIENT_PORT=$HY2_PORT
    MPORT_PARAM=""
    
    if [ "$ENABLE_PORT_HOPPING" == "true" ]; then
        if command -v shuf > /dev/null; then
            CLIENT_PORT=$(shuf -i $PORT_RANGE_START-$PORT_RANGE_END -n 1)
        else
            RANGE=$(($PORT_RANGE_END - $PORT_RANGE_START + 1))
            CLIENT_PORT=$(($PORT_RANGE_START + $RANDOM % $RANGE))
        fi
        MPORT_PARAM="&mport=$PORT_RANGE_START-$PORT_RANGE_END"
    fi

    LINK="hy2://$PASSWORD@$PUBLIC_IP:$CLIENT_PORT?peer=$SNI&insecure=1&sni=$SNI$MPORT_PARAM#Hy2-Node"
    echo "HYSTERIA_DEPLOY_SUCCESS_LINK: $LINK"
else
    echo "HYSTERIA_DEPLOY_FAILED"
fi
"""
# ================= 一键部署 Hysteria 2 (深色适配版 - Surge 兼容) =================
async def open_deploy_hysteria_dialog(server_conf, callback):
    # --- 1. IP 获取逻辑 ---
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    real_ip = target_host
    import re, socket, urllib.parse, uuid, asyncio
    
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try: real_ip = await run.io_bound(socket.gethostbyname, target_host)
        except: safe_notify(f"❌ 无法解析 IP: {target_host}", "negative"); return

    # --- 2. 构建 UI (全深色适配) ---
    # 主卡片：bg-[#1e293b] border-slate-700
    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # 顶部标题栏：bg-[#0f172a] border-b border-slate-700
        with ui.column().classes('w-full bg-[#0f172a] border-b border-slate-700 p-6 gap-2'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('bolt', size='md').classes('text-blue-400')
                ui.label('部署 Hysteria 2 (Surge 兼容版)').classes('text-lg font-bold')
            ui.label(f"服务器 IP: {real_ip}").classes('text-xs text-gray-400 font-mono')

        # 内容区
        with ui.column().classes('w-full p-6 gap-4'):
            name_input = ui.input('节点备注 (可选)', placeholder='例如: 狮城 Hy2').props('outlined dense dark').classes('w-full')
            sni_input = ui.input('伪装域名 (SNI)', value='www.bing.com').props('outlined dense dark').classes('w-full')
            
            # 复选框和数字输入框增加 dark 属性和浅色文字
            enable_hopping = ui.checkbox('启用端口跳跃', value=True).props('dark').classes('text-sm font-bold text-slate-300')
            with ui.row().classes('w-full items-center gap-2'):
                hop_start = ui.number('起始端口', value=20000, format='%.0f').props('outlined dense dark').classes('flex-1').bind_visibility_from(enable_hopping, 'value')
                ui.label('-').classes('text-slate-400').bind_visibility_from(enable_hopping, 'value')
                hop_end = ui.number('结束端口', value=50000, format='%.0f').props('outlined dense dark').classes('flex-1').bind_visibility_from(enable_hopping, 'value')

            # 日志区：纯黑背景更显眼
            log_area = ui.log().classes('w-full h-48 bg-black text-green-400 text-[11px] font-mono p-3 rounded border border-slate-700 hidden transition-all')

        # 底部操作栏：bg-[#0f172a] border-t border-slate-700
        with ui.row().classes('w-full p-4 bg-[#0f172a] border-t border-slate-700 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')
            
            async def start_process():
                btn_cancel.disable(); btn_deploy.props('loading'); log_area.classes(remove='hidden')
                try:
                    hy2_password = str(uuid.uuid4()).replace('-', '')[:16]
                    params = {
                        "password": hy2_password,
                        "sni": sni_input.value,
                        "enable_hopping": "true" if enable_hopping.value else "false",
                        "port_range_start": int(hop_start.value),
                        "port_range_end": int(hop_end.value)
                    }
                    
                    script_content = HYSTERIA_INSTALL_SCRIPT_TEMPLATE.format(**params)
                    deploy_cmd = f"cat > /tmp/install_hy2.sh << 'EOF_SCRIPT'\n{script_content}\nEOF_SCRIPT\nbash /tmp/install_hy2.sh"
                    
                    log_area.push(f"🚀 [SSH] 连接到 {real_ip} 开始安装...")
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, deploy_cmd))
                    
                    if success:
                        match = re.search(r'HYSTERIA_DEPLOY_SUCCESS_LINK: (hy2://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("🎉 部署成功！")
                            
                            custom_name = name_input.value.strip()
                            node_name = custom_name if custom_name else f"Hy2-{real_ip[-3:]}"
                            
                            # --- 强制使用端口范围字符串 ---
                            if enable_hopping.value:
                                final_port_display = f"{int(hop_start.value)}-{int(hop_end.value)}"
                            else:
                                try: final_port_display = int(link.split('@')[1].split(':')[1].split('?')[0])
                                except: final_port_display = 443

                            # 处理 Raw Link
                            if '#' in link: link = link.split('#')[0]
                            final_raw_link = f"{link}#{urllib.parse.quote(node_name)}"

                            new_node = {
                                "id": str(uuid.uuid4()), 
                                "remark": node_name, 
                                "port": final_port_display, 
                                "protocol": "hysteria2",
                                "settings": {}, 
                                "streamSettings": {}, 
                                "enable": True, 
                                "_is_custom": True, 
                                "_raw_link": final_raw_link 
                            }
                            if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                            server_conf['custom_nodes'].append(new_node)
                            await save_servers()
                            
                            safe_notify(f"✅ 节点 {node_name} 已添加", "positive")
                            await asyncio.sleep(1); d.close()
                            if callback: await callback()
                        else: 
                            log_area.push("❌ 未捕获链接"); log_area.push(output[-500:])
                    else: 
                        log_area.push(f"❌ SSH 失败: {output}")
                except Exception as e: 
                    log_area.push(f"❌ 异常: {e}"); print(e)
                finally:
                    btn_cancel.enable(); btn_deploy.props(remove='loading')

            btn_deploy = ui.button('开始部署', on_click=start_process).props('unelevated').classes('bg-blue-600 text-white')
    d.open()

# ================= Snell v5 安装脚本模板 (极致校验修复版) =================
SNELL_INSTALL_SCRIPT_TEMPLATE = r"""
#!/bin/bash
export DEBIAN_FRONTEND=noninteractive

# 接收参数
PORT="{port}"
PSK="{psk}"
TARGET_IP="{target_ip}"

# 1. 安装基础依赖
if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y curl unzip iptables >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y curl unzip iptables >/dev/null 2>&1
fi

# 2. 检测系统架构
ARCH=$(uname -m)
case "$ARCH" in
    x86_64) S_ARCH="amd64" ;;
    aarch64|arm64) S_ARCH="aarch64" ;;
    *) echo "不支持的架构: $ARCH"; exit 1 ;;
esac

# 3. 停止旧服务并清理环境
systemctl stop snell 2>/dev/null
rm -rf /usr/local/bin/snell-server

# 4. 安全下载 (使用 curl -f 遇到 404 直接失败)
DOWNLOAD_URL="https://dl.nssurge.com/snell/snell-server-v5.0.1-linux-${{S_ARCH}}.zip"

echo "正在下载: $DOWNLOAD_URL"
curl -fsSL "$DOWNLOAD_URL" -o /tmp/snell.zip
if [ $? -ne 0 ]; then echo "❌ 下载失败，请检查网络或版本是否已更新"; exit 1; fi

unzip -o /tmp/snell.zip -d /usr/local/bin/ >/dev/null 2>&1
if [ ! -f /usr/local/bin/snell-server ]; then echo "❌ 解压失败，未找到二进制文件"; exit 1; fi

chmod +x /usr/local/bin/snell-server
rm -f /tmp/snell.zip

# 5. 生成配置文件
mkdir -p /etc/snell
cat > /etc/snell/snell-server.conf << EOF
[snell-server]
listen = 0.0.0.0:$PORT
psk = $PSK
ipv6 = false
EOF

# 6. 配置防火墙放行
if command -v ufw >/dev/null 2>&1; then
    ufw allow $PORT/tcp >/dev/null 2>&1
    ufw allow $PORT/udp >/dev/null 2>&1
fi
if command -v iptables >/dev/null 2>&1; then
    iptables -I INPUT -p tcp --dport $PORT -j ACCEPT
    iptables -I INPUT -p udp --dport $PORT -j ACCEPT
    netfilter-persistent save >/dev/null 2>&1 || service iptables save >/dev/null 2>&1
fi

# 7. 配置 Systemd 守护进程
cat > /etc/systemd/system/snell.service << EOF
[Unit]
Description=Snell Proxy Service
After=network.target

[Service]
Type=simple
LimitNOFILE=32768
ExecStart=/usr/local/bin/snell-server -c /etc/snell/snell-server.conf
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

# 8. 启动服务并验证
systemctl daemon-reload
systemctl enable --now snell >/dev/null 2>&1
sleep 1.5

# 9. 核心修复：检查进程是否真的在运行
if systemctl is-active --quiet snell; then
    # 尝试获取公网IP，如果超时失败，则使用Python传入的兜底IP
    PUBLIC_IP=$(curl -s --max-time 3 https://api.ipify.org)
    [ -z "$PUBLIC_IP" ] && PUBLIC_IP="$TARGET_IP"
    
    echo "SNELL_DEPLOY_SUCCESS_LINK: snell://$PSK@$PUBLIC_IP:$PORT?version=5#Snell-Node"
else
    echo "❌ 服务启动失败！可能端口被占用，请执行 journalctl -u snell 查看原因。"
    exit 1
fi
"""

# ================= 一键部署 Snell (Surge 专用 - V5 纯净深色版) =================
async def open_deploy_snell_dialog(server_conf, callback):
    # 解析目标 IP 作为兜底
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    import random, string, uuid, urllib.parse

    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl bg-[#1e293b] border border-slate-700 shadow-2xl'):
        # 顶部标题栏
        with ui.column().classes('w-full bg-[#0f172a] border-b border-slate-700 p-6 gap-2'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('security', size='md').classes('text-blue-400')
                ui.label('部署 Snell 节点 (v5 最新版)').classes('text-lg font-bold')
            ui.label(f"目标服务器: {target_host}").classes('text-xs text-gray-400 font-mono')

        # 内容区
        with ui.column().classes('w-full p-6 gap-4'):
            name_input = ui.input('节点备注', placeholder='例如: HK-Snell-v5').props('outlined dense dark').classes('w-full')
            
            with ui.row().classes('w-full gap-2 items-center'):
                rand_port = random.randint(30000, 60000)
                rand_psk = ''.join(random.choices(string.ascii_letters + string.digits, k=20))
                
                port_input = ui.number('端口', value=rand_port, format='%.0f').props('outlined dense dark').classes('w-1/3')
                psk_input = ui.input('密钥 (PSK)', value=rand_psk).props('outlined dense dark').classes('flex-grow')
                
            # 日志区
            log_area = ui.log().classes('w-full h-48 bg-black text-green-400 text-[11px] font-mono p-3 rounded border border-slate-700 hidden transition-all')

        # 底部操作栏
        with ui.row().classes('w-full p-4 bg-[#0f172a] border-t border-slate-700 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')
            
            async def start_process():
                btn_cancel.disable(); btn_deploy.props('loading'); log_area.classes(remove='hidden')
                try:
                    params = {
                        "port": int(port_input.value),
                        "psk": psk_input.value,
                        "target_ip": target_host # 传入兜底 IP
                    }
                    script_content = SNELL_INSTALL_SCRIPT_TEMPLATE.format(**params)
                    deploy_cmd = f"cat > /tmp/install_snell.sh << 'EOF_SCRIPT'\n{script_content}\nEOF_SCRIPT\nbash /tmp/install_snell.sh"
                    
                    log_area.push(f"🚀 [SSH] 开始在 {target_host} 安装 Snell v5 ...")
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, deploy_cmd))
                    
                    if success:
                        import re
                        match = re.search(r'SNELL_DEPLOY_SUCCESS_LINK: (snell://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("🎉 Snell v5 部署成功并已启动！")
                            
                            custom_name = name_input.value.strip() or f"Snell-v5-{target_host[-3:]}"
                            
                            # 组装节点数据
                            if '#' in link: link = link.split('#')[0]
                            final_raw_link = f"{link}#{urllib.parse.quote(custom_name)}"
                            
                            new_node = {
                                "id": str(uuid.uuid4()), 
                                "remark": custom_name, 
                                "port": params['port'], 
                                "protocol": "snell",
                                "settings": {}, 
                                "streamSettings": {}, 
                                "enable": True, 
                                "_is_custom": True, 
                                "_raw_link": final_raw_link 
                            }
                            if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                            server_conf['custom_nodes'].append(new_node)
                            await save_servers()
                            
                            safe_notify(f"✅ 节点 {custom_name} 已添加", "positive")
                            await asyncio.sleep(1); d.close()
                            if callback: await callback()
                        else: 
                            log_area.push("❌ 部署失败：未能成功启动服务。")
                            log_area.push(output[-500:]) # 打印最后的错误日志
                    else: 
                        log_area.push(f"❌ SSH 连接失败: {output}")
                finally:
                    btn_cancel.enable(); btn_deploy.props(remove='loading')

            btn_deploy = ui.button('开始部署', on_click=start_process).props('unelevated').classes('bg-blue-600 text-white')
    d.open()



 
# ================= 全局变量区 (缓存) =================
PROBE_DATA_CACHE = {} 
PING_TREND_CACHE = {} 

# ================= 全局记录历史数据的函数 (V61：强制每分钟只记录一次) =================
def record_ping_history(url, pings_dict):
    """
    后台收到数据调用此函数记录历史。
    ✨ 新增逻辑：同一服务器，至少间隔 60 秒才记录一次数据 (防抖)。
    """
    if not url or not pings_dict: return
    
    current_ts = time.time()
    
    # 1. 初始化
    if url not in PING_TREND_CACHE: 
        PING_TREND_CACHE[url] = []
    
    # 2.  核心防抖逻辑 
    # 如果该服务器已有数据，且最后一条数据的时间距离现在不足 60 秒，则跳过不录
    if PING_TREND_CACHE[url]:
        last_record = PING_TREND_CACHE[url][-1]
        if current_ts - last_record['ts'] < 60: 
            return # <--- 没到1分钟，直接忽略，不记录

    # 3. 只有超过 60 秒才执行下面的追加逻辑
    import datetime
    time_str = datetime.datetime.fromtimestamp(current_ts).strftime('%m/%d %H:%M') # 格式化为 "01/06 19:46"
    
    ct = pings_dict.get('电信', 0); ct = ct if ct > 0 else 0
    cu = pings_dict.get('联通', 0); cu = cu if cu > 0 else 0
    cm = pings_dict.get('移动', 0); cm = cm if cm > 0 else 0
    
    PING_TREND_CACHE[url].append({
        'ts': current_ts, 
        'time_str': time_str, 
        'ct': ct, 
        'cu': cu, 
        'cm': cm
    })
    
    # 限制长度：保留最近 1000 条 (足够存放 6小时 甚至 24小时 的分钟级数据)
    # 6小时 * 60分 = 360条，设置 1000 很安全
    if len(PING_TREND_CACHE[url]) > 1000:
        PING_TREND_CACHE[url] = PING_TREND_CACHE[url][-1000:]

        
# ================= 探针安装脚本 (升级版：含X-UI数据库读取) =================
PROBE_INSTALL_SCRIPT = r"""
bash -c '
# 1. 提升权限
[ "$(id -u)" -eq 0 ] || { command -v sudo >/dev/null && exec sudo bash "$0" "$@"; echo "Root required"; exit 1; }

# 2. 安装基础依赖
if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y python3 iputils-ping util-linux sqlite3 >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y python3 iputils util-linux sqlite3 >/dev/null 2>&1
elif [ -f /etc/alpine-release ]; then
    apk add python3 iputils util-linux sqlite3 >/dev/null 2>&1
fi

# 3. 写入 Python 脚本
cat > /root/x_fusion_agent.py << "PYTHON_EOF"
import time, json, os, socket, sys, subprocess, re, platform, sqlite3
import urllib.request, urllib.error
import ssl

MANAGER_URL = "__MANAGER_URL__/api/probe/push"
TOKEN = "__TOKEN__"
SERVER_URL = "__SERVER_URL__"

PING_TARGETS = {
"电信": "__PING_CT__",
"联通": "__PING_CU__",
"移动": "__PING_CM__"
}

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def get_cpu_model():
    model = "Unknown"
    try:
        try:
            out = subprocess.check_output("lscpu", shell=True).decode()
            for line in out.split("\n"):
                if "Model name:" in line: return line.split(":")[1].strip()
        except: pass
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line: return line.split(":")[1].strip()
                if "Hardware" in line: return line.split(":")[1].strip()
    except: pass
    return model

def get_os_distro():
    try:
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=")[1].strip().strip("\"")
    except: pass
    try: return platform.platform()
    except: return "Linux (Unknown)"

STATIC_CACHE = {
    "cpu_model": get_cpu_model(),
    "arch": platform.machine(),
    "os": get_os_distro(),
    "virt": "Unknown"
}
try:
    v = subprocess.check_output("systemd-detect-virt", shell=True).decode().strip()
    if v and v != "none": STATIC_CACHE["virt"] = v
except: pass

def get_ping(target):
    try:
        ip = target.split("://")[-1].split(":")[0]
        cmd = "ping -c 1 -W 1 " + ip
        res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0:
            match = re.search(r"time=([\d.]+)", res.stdout.decode())
            if match: return int(float(match.group(1)))
    except: pass
    return -1

def get_network_bytes():
    r, t = 0, 0
    try:
        with open("/proc/net/dev") as f:
            lines = f.readlines()[2:]
            for l in lines:
                cols = l.split(":")
                if len(cols)<2: continue
                parts = cols[1].split()
                if len(parts)>=9 and cols[0].strip() != "lo":
                    r += int(parts[0])
                    t += int(parts[8])
    except: pass
    return r, t

# 读取 X-UI 数据库 
def get_xui_rows():
    db_path = "/etc/x-ui/x-ui.db"
    if not os.path.exists(db_path): return None
    
    try:
        # 使用 URI 模式打开，mode=ro (只读)，防止锁死数据库影响面板写入
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        # 查询关键字段，尽可能匹配 API 返回的格式
        cursor.execute("SELECT id, up, down, total, remark, enable, protocol, port, settings, stream_settings, expiry_time, listen FROM inbounds")
        rows = cursor.fetchall()
        
        inbounds = []
        for row in rows:
            # 数据清洗与组装
            node = {
                "id": row[0],
                "up": row[1],
                "down": row[2],
                "total": row[3],
                "remark": row[4],
                "enable": True if row[5] == 1 else False,
                "protocol": row[6],
                "port": row[7],
                "settings": row[8],         # 数据库里存的是 JSON 字符串，直接传给后端
                "streamSettings": row[9],   # 同上
                "expiryTime": row[10],
                "listen": row[11]
            }
            inbounds.append(node)
        conn.close()
        return inbounds
    except:
        return None

def get_info():
    global SERVER_URL
    data = {"token": TOKEN, "static": STATIC_CACHE}
    
    if not SERVER_URL:
        try:
            with urllib.request.urlopen("http://checkip.amazonaws.com", timeout=5, context=ssl_ctx) as r:
                my_ip = r.read().decode().strip()
                SERVER_URL = "http://" + my_ip + ":54322"
        except: pass
    data["server_url"] = SERVER_URL

    try:
        net_in_1, net_out_1 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot1, idle1 = sum(fs), fs[3]
        
        # 采集等待
        time.sleep(1)
        
        net_in_2, net_out_2 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot2, idle2 = sum(fs), fs[3]
            
        data["cpu_usage"] = round((1 - (idle2-idle1)/(tot2-tot1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        
        data["net_speed_in"] = net_in_2 - net_in_1
        data["net_speed_out"] = net_out_2 - net_out_1
        data["net_total_in"] = net_in_2
        data["net_total_out"] = net_out_2

        with open("/proc/loadavg") as f: data["load_1"] = float(f.read().split()[0])
        
        with open("/proc/meminfo") as f:
            m = {}
            for l in f:
                p = l.split()
                if len(p)>=2: m[p[0].rstrip(":")] = int(p[1])
        
        tot = m.get("MemTotal", 1)
        avail = m.get("MemAvailable", m.get("MemFree", 0))
        data["mem_total"] = round(tot/1024/1024, 2)
        data["mem_usage"] = round(((tot-avail)/tot)*100, 1)
        data["swap_total"] = round(m.get("SwapTotal", 0)/1024/1024, 2)
        data["swap_free"] = round(m.get("SwapFree", 0)/1024/1024, 2)

        st = os.statvfs("/")
        data["disk_total"] = round((st.f_blocks * st.f_frsize)/1024/1024/1024, 2)
        free = st.f_bavail * st.f_frsize
        total = st.f_blocks * st.f_frsize
        data["disk_usage"] = round(((total-free)/total)*100, 1)

        with open("/proc/uptime") as f: u = float(f.read().split()[0])
        d = int(u // 86400); h = int((u % 86400) // 3600); m = int((u % 3600) // 60)
        data["uptime"] = "%d天 %d时 %d分" % (d, h, m)

        data["pings"] = {k: get_ping(v) for k, v in PING_TARGETS.items()}
        
        #  获取 X-UI 本地数据并随包推送 
        xui = get_xui_rows()
        if xui is not None:
            data["xui_data"] = xui

    except: pass
    return data

def push():
    while True:
        try:
            js = json.dumps(get_info()).encode("utf-8")
            req = urllib.request.Request(MANAGER_URL, data=js, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as r: pass
        except: pass
        time.sleep(5) # 降低频率，5秒推送一次

if __name__ == "__main__":
    push()
PYTHON_EOF

# 4. 创建服务
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

# 5. 启动
systemctl daemon-reload
systemctl enable x-fusion-agent
systemctl restart x-fusion-agent
exit 0
'
"""
# ================= 强制日志实时输出 =================
sys.stdout.reconfigure(line_buffering=True)
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s] %(message)s', 
    datefmt='%H:%M:%S',
    force=True,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("XUI_Manager")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("nicegui").setLevel(logging.INFO)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 性能调优配置 =================
# 1. 后台专用线程池 (处理 90+ 服务器同步)
BG_EXECUTOR = ThreadPoolExecutor(max_workers=20)
# 2. 限制后台并发数
SYNC_SEMAPHORE = asyncio.Semaphore(50) 


LAST_AUTO_SYNC_TIME = 0
SYNC_COOLDOWN_SECONDS = 300  # 冷却时间：300秒（5分钟）

# ================= 智能分组配置 (增强版) =================
AUTO_COUNTRY_MAP = {
    # --- 亚太地区 ---
    '🇨🇳': '🇨🇳 中国', 'China': '🇨🇳 中国', '中国': '🇨🇳 中国', 'CN': '🇨🇳 中国', 'PRC': '🇨🇳 中国',
    '🇭🇰': '🇭🇰 香港', 'HK': '🇭🇰 香港', 'Hong Kong': '🇭🇰 香港', 'Hong Kong SAR': '🇭🇰 香港',
    '🇲🇴': '🇲🇴 澳门', 'MO': '🇲🇴 澳门', 'Macau': '🇲🇴 澳门', 'Macao': '🇲🇴 澳门',
    '🇹🇼': '🇹🇼 台湾', 'TW': '🇹🇼 台湾', 'Taiwan': '🇹🇼 台湾', 'Republic of China': '🇹🇼 台湾',
    '🇯🇵': '🇯🇵 日本', 'JP': '🇯🇵 日本', 'Japan': '🇯🇵 日本', 'Tokyo': '🇯🇵 日本', 'Osaka': '🇯🇵 日本',
    '🇸🇬': '🇸🇬 新加坡', 'SG': '🇸🇬 新加坡', 'Singapore': '🇸🇬 新加坡',
    '🇰🇷': '🇰🇷 韩国', 'KR': '🇰🇷 韩国', 'Korea': '🇰🇷 韩国', 'South Korea': '🇰🇷 韩国', 'Republic of Korea': '🇰🇷 韩国', '韩国': '🇰🇷 韩国', 'Seoul': '🇰🇷 韩国',
    '🇰🇵': '🇰🇵 朝鲜', 'KP': '🇰🇵 朝鲜', 'North Korea': '🇰🇵 朝鲜', '朝鲜': '🇰🇵 朝鲜',
    '🇮🇳': '🇮🇳 印度', 'IN': '🇮🇳 印度', 'India': '🇮🇳 印度', 'Mumbai': '🇮🇳 印度',
    '🇮🇩': '🇮🇩 印度尼西亚', 'ID': '🇮🇩 印度尼西亚', 'Indonesia': '🇮🇩 印度尼西亚', '印尼': '🇮🇩 印度尼西亚', '印度尼西亚': '🇮🇩 印度尼西亚', 'Jakarta': '🇮🇩 印度尼西亚',
    '🇲🇾': '🇲🇾 马来西亚', 'MY': '🇲🇾 马来西亚', 'Malaysia': '🇲🇾 马来西亚', '马来西亚': '🇲🇾 马来西亚',
    '🇹🇭': '🇹🇭 泰国', 'TH': '🇹🇭 泰国', 'Thailand': '🇹🇭 泰国', '泰国': '🇹🇭 泰国', 'Bangkok': '🇹🇭 泰国',
    '🇻🇳': '🇻🇳 越南', 'VN': '🇻🇳 越南', 'Vietnam': '🇻🇳 越南', 'Viet Nam': '🇻🇳 越南', '越南': '🇻🇳 越南',
    '🇵🇭': '🇵🇭 菲律宾', 'PH': '🇵🇭 菲律宾', 'Philippines': '🇵🇭 菲律宾', '菲律宾': '🇵🇭 菲律宾',
    '🇦🇺': '🇦🇺 澳大利亚', 'AU': '🇦🇺 澳大利亚', 'Australia': '🇦🇺 澳大利亚', '澳大利亚': '🇦🇺 澳大利亚', '澳洲': '🇦🇺 澳大利亚', 'Sydney': '🇦🇺 澳大利亚',
    '🇳🇿': '🇳🇿 新西兰', 'NZ': '🇳🇿 新西兰', 'New Zealand': '🇳🇿 新西兰', '新西兰': '🇳🇿 新西兰',

    # --- 北美地区 ---
    '🇺🇸': '🇺🇸 美国', 'USA': '🇺🇸 美国', 'US': '🇺🇸 美国', 'United States': '🇺🇸 美国', 'America': '🇺🇸 美国', '美国': '🇺🇸 美国',
    '🇨🇦': '🇨🇦 加拿大', 'CA': '🇨🇦 加拿大', 'Canada': '🇨🇦 加拿大', '加拿大': '🇨🇦 加拿大', 'Toronto': '🇨🇦 加拿大',
    '🇲🇽': '🇲🇽 墨西哥', 'MX': '🇲🇽 墨西哥', 'Mexico': '🇲🇽 墨西哥', '墨西哥': '🇲🇽 墨西哥',

    # --- 南美地区 ---
    '🇧🇷': '🇧🇷 巴西', 'BR': '🇧🇷 巴西', 'Brazil': '🇧🇷 巴西', '巴西': '🇧🇷 巴西', 'Sao Paulo': '🇧🇷 巴西',
    '🇨🇱': '🇨🇱 智利', 'CL': '🇨🇱 智利', 'Chile': '🇨🇱 智利', '智利': '🇨🇱 智利',
    '🇦🇷': '🇦🇷 阿根廷', 'AR': '🇦🇷 阿根廷', 'Argentina': '🇦🇷 阿根廷', '阿根廷': '🇦🇷 阿根廷',
    '🇨🇴': '🇨🇴 哥伦比亚', 'CO': '🇨🇴 哥伦比亚', 'Colombia': '🇨🇴 哥伦比亚', '哥伦比亚': '🇨🇴 哥伦比亚',
    '🇵🇪': '🇵🇪 秘鲁', 'PE': '🇵🇪 秘鲁', 'Peru': '🇵🇪 秘鲁', '秘鲁': '🇵🇪 秘鲁',

    # --- 欧洲地区 ---
    '🇬🇧': '🇬🇧 英国', 'UK': '🇬🇧 英国', 'GB': '🇬🇧 英国', 'United Kingdom': '🇬🇧 英国', 'Great Britain': '🇬🇧 英国', 'England': '🇬🇧 英国', '英国': '🇬🇧 英国', 'London': '🇬🇧 英国',
    '🇩🇪': '🇩🇪 德国', 'DE': '🇩🇪 德国', 'Germany': '🇩🇪 德国', '德国': '🇩🇪 德国', 'Frankfurt': '🇩🇪 德国',
    '🇫🇷': '🇫🇷 法国', 'FR': '🇫🇷 法国', 'France': '🇫🇷 法国', '法国': '🇫🇷 法国', 'Paris': '🇫🇷 法国',
    '🇳🇱': '🇳🇱 荷兰', 'NL': '🇳🇱 荷兰', 'Netherlands': '🇳🇱 荷兰', 'The Netherlands': '🇳🇱 荷兰', '荷兰': '🇳🇱 荷兰', 'Amsterdam': '🇳🇱 荷兰',
    '🇷🇺': '🇷🇺 俄罗斯', 'RU': '🇷🇺 俄罗斯', 'Russia': '🇷🇺 俄罗斯', 'Russian Federation': '🇷🇺 俄罗斯', '俄罗斯': '🇷🇺 俄罗斯', 'Moscow': '🇷🇺 俄罗斯',
    '🇮🇹': '🇮🇹 意大利', 'IT': '🇮🇹 意大利', 'Italy': '🇮🇹 意大利', '意大利': '🇮🇹 意大利', 'Milan': '🇮🇹 意大利',
    '🇪🇸': '🇪🇸 西班牙', 'ES': '🇪🇸 西班牙', 'Spain': '🇪🇸 西班牙', '西班牙': '🇪🇸 西班牙', 'Madrid': '🇪🇸 西班牙',
    '🇸🇪': '🇸🇪 瑞典', 'SE': '🇸🇪 瑞典', 'Sweden': '🇸🇪 瑞典', '瑞典': '🇸🇪 瑞典', 'Stockholm': '🇸🇪 瑞典',
    '🇨🇭': '🇨🇭 瑞士', 'CH': '🇨🇭 瑞士', 'Switzerland': '🇨🇭 瑞士', '瑞士': '🇨🇭 瑞士', 'Zurich': '🇨🇭 瑞士',
    '🇵🇱': '🇵🇱 波兰', 'PL': '🇵🇱 波兰', 'Poland': '🇵🇱 波兰', '波兰': '🇵🇱 波兰',
    '🇮🇪': '🇮🇪 爱尔兰', 'IE': '🇮🇪 爱尔兰', 'Ireland': '🇮🇪 爱尔兰', '爱尔兰': '🇮🇪 爱尔兰',
    '🇺🇦': '🇺🇦 乌克兰', 'UA': '🇺🇦 乌克兰', 'Ukraine': '🇺🇦 乌克兰', '乌克兰': '🇺🇦 乌克兰',
    '🇹🇷': '🇹🇷 土耳其', 'TR': '🇹🇷 土耳其', 'Turkey': '🇹🇷 土耳其', '土耳其': '🇹🇷 土耳其', 'Istanbul': '🇹🇷 土耳其',

    # --- 中东与非洲 ---
    '🇦🇪': '🇦🇪 阿联酋', 'AE': '🇦🇪 阿联酋', 'UAE': '🇦🇪 阿联酋', 'United Arab Emirates': '🇦🇪 阿联酋', '阿联酋': '🇦🇪 阿联酋', '阿拉伯联合酋长国': '🇦🇪 阿联酋', 'Dubai': '🇦🇪 阿联酋',
    '🇮🇱': '🇮🇱 以色列', 'IL': '🇮🇱 以色列', 'Israel': '🇮🇱 以色列', '以色列': '🇮🇱 以色列',
    '🇿🇦': '🇿🇦 南非', 'ZA': '🇿🇦 南非', 'South Africa': '🇿🇦 南非', '南非': '🇿🇦 南非', 'Johannesburg': '🇿🇦 南非',
    '🇸🇦': '🇸🇦 沙特', 'SA': '🇸🇦 沙特', 'Saudi Arabia': '🇸🇦 沙特', 'Kingdom of Saudi Arabia': '🇸🇦 沙特', '沙特': '🇸🇦 沙特', '沙特阿拉伯': '🇸🇦 沙特',
    '🇮🇷': '🇮🇷 伊朗', 'IR': '🇮🇷 伊朗', 'Iran': '🇮🇷 伊朗', '伊朗': '🇮🇷 伊朗',
    '🇪🇬': '🇪🇬 埃及', 'EG': '🇪🇬 埃及', 'Egypt': '🇪🇬 埃及', '埃及': '🇪🇬 埃及',
    '🇳🇬': '🇳🇬 尼日利亚', 'NG': '🇳🇬 尼日利亚', 'Nigeria': '🇳🇬 尼日利亚', '尼日利亚': '🇳🇬 尼日利亚',
}

# ================= 智能分组核心  =================
def detect_country_group(name, server_config=None):
    # 1. ✨ 最高优先级：手动设置的分组 ✨
    if server_config:
        saved_group = server_config.get('group')
        # 排除无效分组
        if saved_group and saved_group.strip() and saved_group not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区', '其他地区']:
            # 尝试标准化 (如输入 "美国" -> "🇺🇸 美国")
            for v in AUTO_COUNTRY_MAP.values():
                if saved_group in v or v in saved_group:
                    return v 
            return saved_group

    # 2. ✨✨✨ 第二优先级：看图识字 + 智能关键字匹配 ✨✨✨
    name_upper = name.upper()
    
    # 🌟 关键优化：按长度倒序匹配 (优先匹配 "United States" 而非 "US")
    # 这样可以防止长词被短词截胡
    sorted_keys = sorted(AUTO_COUNTRY_MAP.keys(), key=len, reverse=True)
    
    import re
    
    for key in sorted_keys:
        val = AUTO_COUNTRY_MAP[key]
        
        if key in name_upper:
            # 🌟 核心修复：针对 2-3 位短字母缩写 (如 CL, US, SG, ID)
            # 必须前后是符号或边界，不能夹在单词里 (防止 Oracle 匹配到 CL)
            if len(key) <= 3 and key.isalpha():
                # 正则：(?<![A-Z0-9]) 表示前面不能是字母数字
                #       (?![A-Z0-9])  表示后面不能是字母数字
                pattern = r'(?<![A-Z0-9])' + re.escape(key) + r'(?![A-Z0-9])'
                if re.search(pattern, name_upper):
                    return val
            else:
                # 长关键字 (Japan) 或 Emoji (🇯🇵) 或带符号的 (HK-)，直接匹配
                return val

    # 3. 第三优先级：IP 检测的隐藏字段
    if server_config and server_config.get('_detected_region'):
        detected = server_config['_detected_region'].upper()
        for key, val in AUTO_COUNTRY_MAP.items():
            if key.upper() == detected or key.upper() in detected:
                return val
            
    return '🏳️ 其他地区'

# ================= 2D 平面地图：结构与样式  =================
GLOBE_STRUCTURE = r"""
<style>
    /* 容器填满父级 */
    #earth-container {
        width: 100%;
        height: 100%;
        position: relative;
        overflow: hidden;
        border-radius: 12px;
        background-color: #100C2A; /* 深色背景 */
    }
    
    /* 统计面板 */
    .earth-stats {
        position: absolute;
        top: 20px;
        left: 20px;
        color: rgba(255, 255, 255, 0.8);
        font-family: 'Consolas', monospace;
        font-size: 12px;
        z-index: 10;
        background: rgba(0, 20, 40, 0.6);
        padding: 10px 15px;
        border: 1px solid rgba(0, 255, 255, 0.3);
        border-radius: 6px;
        backdrop-filter: blur(4px);
        pointer-events: none;
    }
    .earth-stats span { color: #00ffff; font-weight: bold; }
</style>

<div id="earth-container">
    <div class="earth-stats">
        <div>ACTIVE NODES: <span id="node-count">0</span></div>
        <div>REGIONS: <span id="region-count">0</span></div>
    </div>
    <div id="earth-render-area" style="width:100%; height:100%;"></div>
</div>
"""

# ================= 2D 平面地图：JS 逻辑 =================
GLOBE_JS_LOGIC = r"""
(function() {
    // 1. 获取仪表盘专用容器
    var container = document.getElementById('earth-render-area');
    if (!container) return;
    
    // 2. 初始化数据
    var serverData = window.DASHBOARD_DATA || [];
    
    // 3. 定义默认坐标 (北京)，如果定位成功会被覆盖
    var myLat = 39.9;
    var myLon = 116.4;

    // ✨✨✨ 修复核心：定义国旗字体 ✨✨✨
    var emojiFont = '"Twemoji Country Flags", "Noto Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", sans-serif';

    // 更新统计数字
    var nodeCountEl = document.getElementById('node-count');
    var regionCountEl = document.getElementById('region-count');
    function updateStats(data) {
        if(nodeCountEl) nodeCountEl.textContent = data.length;
        const uniqueRegions = new Set(data.map(s => s.name));
        if(regionCountEl) regionCountEl.textContent = uniqueRegions.size;
    }
    updateStats(serverData);

    // 初始化 ECharts
    var existing = echarts.getInstanceByDom(container);
    if (existing) existing.dispose();
    var myChart = echarts.init(container);

    // 4. 获取浏览器定位
    if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(function(position) {
            myLat = position.coords.latitude;
            myLon = position.coords.longitude;
            var option = buildOption(window.cachedWorldJson, serverData, myLat, myLon);
            myChart.setOption(option);
        });
    }

    // 5. 定义仪表盘专用的更新函数
    window.updateDashboardMap = function(newData) {
        if (!window.cachedWorldJson || !myChart) return;
        serverData = newData;
        updateStats(newData);
        var option = buildOption(window.cachedWorldJson, newData, myLat, myLon);
        myChart.setOption(option);
    };

    // 定义高亮区域
    const searchKeys = {
        '🇺🇸': 'United States', '🇨🇳': 'China', '🇭🇰': 'China', '🇹🇼': 'China', '🇯🇵': 'Japan', '🇰🇷': 'Korea',
        '🇸🇬': 'Singapore', '🇬🇧': 'United Kingdom', '🇩🇪': 'Germany', '🇫🇷': 'France', '🇷🇺': 'Russia',
        '🇨🇦': 'Canada', '🇦🇺': 'Australia', '🇮🇳': 'India', '🇧🇷': 'Brazil'
    };

    function buildOption(mapGeoJSON, data, userLat, userLon) {
        const mapFeatureNames = mapGeoJSON.features.map(f => f.properties.name);
        const activeMapNames = new Set();

        data.forEach(s => {
            let keyword = null;
            for (let key in searchKeys) {
                if ((s.name && s.name.includes(key))) {
                    keyword = searchKeys[key];
                    break;
                }
            }
            if (keyword && mapFeatureNames.includes(keyword)) {
                activeMapNames.add(keyword);
            }
        });

        const highlightRegions = Array.from(activeMapNames).map(name => ({
            name: name,
            itemStyle: { areaColor: '#0055ff', borderColor: '#00ffff', borderWidth: 1.5, opacity: 0.9 }
        }));

        const scatterData = data.map(s => ({
            name: s.name, value: [s.lon, s.lat], itemStyle: { color: '#00ffff' }
        }));
        
        scatterData.push({
            name: "ME", value: [userLon, userLat], itemStyle: { color: '#FFD700' },
            symbolSize: 15, label: { show: true, position: 'top', formatter: 'My PC', color: '#FFD700' }
        });

        const linesData = data.map(s => ({
            coords: [[s.lon, s.lat], [userLon, userLat]]
        }));

        return {
            backgroundColor: '#100C2A', 
            geo: {
                map: 'world', roam: false, zoom: 1.2, center: [15, 10],
                label: { show: false },
                itemStyle: { areaColor: '#1B2631', borderColor: '#404a59', borderWidth: 1 },
                emphasis: { itemStyle: { areaColor: '#2a333d' }, label: { show: false } },
                regions: highlightRegions 
            },
            series: [
                {
                    type: 'lines', coordinateSystem: 'geo', zlevel: 2,
                    effect: { show: true, period: 4, trailLength: 0.5, color: '#00ffff', symbol: 'arrow', symbolSize: 6 },
                    lineStyle: { color: '#00ffff', width: 1, opacity: 0, curveness: 0.2 },
                    data: linesData
                },
                {
                    type: 'scatter', coordinateSystem: 'geo', zlevel: 3, symbol: 'circle', symbolSize: 12,
                    itemStyle: { color: '#00ffff', shadowBlur: 10, shadowColor: '#333' },
                    
                    // ✨✨✨ 重点：在这里应用了字体 ✨✨✨
                    label: { 
                        show: true, 
                        position: 'right', 
                        formatter: '{b}', 
                        color: '#fff', 
                        fontSize: 16, 
                        fontWeight: 'bold',
                        fontFamily: emojiFont  // <--- 修复这一行
                    },
                    
                    data: scatterData
                }
            ]
        };
    }

    fetch('/static/world.json')
        .then(response => response.json())
        .then(worldJson => {
            echarts.registerMap('world', worldJson);
            window.cachedWorldJson = worldJson;
            var option = buildOption(worldJson, serverData, myLat, myLon);
            myChart.setOption(option);
            
            window.addEventListener('resize', () => myChart.resize());
            new ResizeObserver(() => myChart.resize()).observe(container);
        });
})();
"""

# ================= 全局地图名称映射表 (用于 Status 页面) =================
MATCH_MAP = {
    # --- 南美 ---
    '🇨🇱': 'Chile', 'CHILE': 'Chile',
    '🇧🇷': 'Brazil', 'BRAZIL': 'Brazil', 'BRA': 'Brazil', 'SAO PAULO': 'Brazil',
    '🇦🇷': 'Argentina', 'ARGENTINA': 'Argentina', 'ARG': 'Argentina',
    '🇨🇴': 'Colombia', 'COLOMBIA': 'Colombia', 'COL': 'Colombia',
    '🇵🇪': 'Peru', 'PERU': 'Peru',
    # --- 北美 ---
    '🇺🇸': 'United States', 'USA': 'United States', 'UNITED STATES': 'United States', 'AMERICA': 'United States',
    '🇨🇦': 'Canada', 'CANADA': 'Canada', 'CAN': 'Canada',
    '🇲🇽': 'Mexico', 'MEXICO': 'Mexico', 'MEX': 'Mexico',
    # --- 欧洲 ---
    '🇬🇧': 'United Kingdom', 'UK': 'United Kingdom', 'GB': 'United Kingdom', 'UNITED KINGDOM': 'United Kingdom', 'LONDON': 'United Kingdom',
    '🇩🇪': 'Germany', 'GERMANY': 'Germany', 'DEU': 'Germany', 'FRANKFURT': 'Germany',
    '🇫🇷': 'France', 'FRANCE': 'France', 'FRA': 'France', 'PARIS': 'France',
    '🇳🇱': 'Netherlands', 'NETHERLANDS': 'Netherlands', 'NLD': 'Netherlands', 'AMSTERDAM': 'Netherlands',
    '🇷🇺': 'Russia', 'RUSSIA': 'Russia', 'RUS': 'Russia',
    '🇮🇹': 'Italy', 'ITALY': 'Italy', 'ITA': 'Italy', 'MILAN': 'Italy',
    '🇪🇸': 'Spain', 'SPAIN': 'Spain', 'ESP': 'Spain', 'MADRID': 'Spain',
    '🇵🇱': 'Poland', 'POLAND': 'Poland', 'POL': 'Poland',
    '🇺🇦': 'Ukraine', 'UKRAINE': 'Ukraine', 'UKR': 'Ukraine',
    '🇸🇪': 'Sweden', 'SWEDEN': 'Sweden', 'SWE': 'Sweden',
    '🇨🇭': 'Switzerland', 'SWITZERLAND': 'Switzerland', 'CHE': 'Switzerland',
    '🇹🇷': 'Turkey', 'TURKEY': 'Turkey', 'TUR': 'Turkey',
    '🇮🇪': 'Ireland', 'IRELAND': 'Ireland', 'IRL': 'Ireland',
    '🇫🇮': 'Finland', 'FINLAND': 'Finland', 'FIN': 'Finland',
    '🇳🇴': 'Norway', 'NORWAY': 'Norway', 'NOR': 'Norway',
    '🇦🇹': 'Austria', 'AUSTRIA': 'Austria', 'AUT': 'Austria',
    '🇧🇪': 'Belgium', 'BELGIUM': 'Belgium', 'BEL': 'Belgium',
    '🇵🇹': 'Portugal', 'PORTUGAL': 'Portugal', 'PRT': 'Portugal',
    '🇬🇷': 'Greece', 'GREECE': 'Greece', 'GRC': 'Greece',
    # --- 亚太 ---
    '🇨🇳': 'China', 'CHINA': 'China', 'CHN': 'China', 'CN': 'China',
    '🇭🇰': 'China', 'HONG KONG': 'China', 'HK': 'China',
    '🇲🇴': 'China', 'MACAU': 'China', 'MO': 'China',
    '🇹🇼': 'China', 'TAIWAN': 'China', 'TW': 'China',
    '🇯🇵': 'Japan', 'JAPAN': 'Japan', 'JPN': 'Japan', 'TOKYO': 'Japan', 'OSAKA': 'Japan',
    '🇰🇷': 'South Korea', 'KOREA': 'South Korea', 'KOR': 'South Korea', 'SEOUL': 'South Korea',
    '🇸🇬': 'Singapore', 'SINGAPORE': 'Singapore', 'SGP': 'Singapore', 'SG': 'Singapore',
    '🇮🇳': 'India', 'INDIA': 'India', 'IND': 'India', 'MUMBAI': 'India',
    '🇦🇺': 'Australia', 'AUSTRALIA': 'Australia', 'AUS': 'Australia', 'SYDNEY': 'Australia',
    '🇳🇿': 'New Zealand', 'NEW ZEALAND': 'New Zealand', 'NZL': 'New Zealand',
    '🇻🇳': 'Vietnam', 'VIETNAM': 'Vietnam', 'VNM': 'Vietnam',
    '🇹🇭': 'Thailand', 'THAILAND': 'Thailand', 'THA': 'Thailand', 'BANGKOK': 'Thailand',
    '🇲🇾': 'Malaysia', 'MALAYSIA': 'Malaysia', 'MYS': 'Malaysia',
    '🇮🇩': 'Indonesia', 'INDONESIA': 'Indonesia', 'IDN': 'Indonesia', 'JAKARTA': 'Indonesia',
    '🇵🇭': 'Philippines', 'PHILIPPINES': 'Philippines', 'PHL': 'Philippines',
    '🇰🇭': 'Cambodia', 'CAMBODIA': 'Cambodia', 'KHM': 'Cambodia',
    # --- 中东/非洲 ---
    '🇦🇪': 'United Arab Emirates', 'UAE': 'United Arab Emirates', 'DUBAI': 'United Arab Emirates',
    '🇿🇦': 'South Africa', 'SOUTH AFRICA': 'South Africa', 'ZAF': 'South Africa',
    '🇸🇦': 'Saudi Arabia', 'SAUDI ARABIA': 'Saudi Arabia', 'SAU': 'Saudi Arabia',
    '🇮🇱': 'Israel', 'ISRAEL': 'Israel', 'ISR': 'Israel',
    '🇪🇬': 'Egypt', 'EGYPT': 'Egypt', 'EGY': 'Egypt',
    '🇮🇷': 'Iran', 'IRAN': 'Iran', 'IRN': 'Iran',
    '🇳🇬': 'Nigeria', 'NIGERIA': 'Nigeria', 'NGA': 'Nigeria'
}

def get_echarts_region_name(name_raw):
    if not name_raw: return None
    name = name_raw.upper()
    sorted_keys = sorted(MATCH_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in name: return MATCH_MAP[key]
    return None

# ================= 全局地图数据准备 (修复版：强制关联探针实时状态) =================
def prepare_map_data():
    try:
        city_points_map = {} 
        flag_points_map = {} 
        unique_deployed_countries = set() 
        region_stats = {} 
        active_regions_for_highlight = set()

        # 1. 国旗 -> 标准地图名映射 
        FLAG_TO_MAP_NAME = {
            '🇨🇳': 'China', '🇭🇰': 'China', '🇲🇴': 'China', '🇹🇼': 'China',
            '🇺🇸': 'United States', '🇨🇦': 'Canada', '🇲🇽': 'Mexico',
            '🇬🇧': 'United Kingdom', '🇩🇪': 'Germany', '🇫🇷': 'France', '🇳🇱': 'Netherlands',
            '🇷🇺': 'Russia', '🇯🇵': 'Japan', '🇰🇷': 'South Korea', '🇸🇬': 'Singapore',
            '🇮🇳': 'India', '🇦🇺': 'Australia', '🇧🇷': 'Brazil', '🇦🇷': 'Argentina',
            '🇹🇷': 'Turkey', '🇮🇹': 'Italy', '🇪🇸': 'Spain', '🇵🇹': 'Portugal',
            '🇨🇭': 'Switzerland', '🇸🇪': 'Sweden', '🇳🇴': 'Norway', '🇫🇮': 'Finland',
            '🇵🇱': 'Poland', '🇺🇦': 'Ukraine', '🇮🇪': 'Ireland', '🇦🇹': 'Austria',
            '🇧🇪': 'Belgium', '🇩🇰': 'Denmark', '🇨🇿': 'Czech Republic', '🇬🇷': 'Greece',
            '🇿🇦': 'South Africa', '🇪🇬': 'Egypt', '🇸🇦': 'Saudi Arabia', '🇦🇪': 'United Arab Emirates',
            '🇮🇱': 'Israel', '🇮🇷': 'Iran', '🇮🇩': 'Indonesia', '🇲🇾': 'Malaysia',
            '🇹🇭': 'Thailand', '🇻🇳': 'Vietnam', '🇵🇭': 'Philippines', '🇨🇱': 'Chile',
            '🇨🇴': 'Colombia', '🇵🇪': 'Peru'
        }

        # 2. 地图名别名库
        MAP_NAME_ALIASES = {
            'United States': ['United States of America', 'USA'],
            'United Kingdom': ['United Kingdom', 'UK', 'Great Britain'],
            'China': ['People\'s Republic of China'],
            'Russia': ['Russian Federation'],
            'South Korea': ['Korea', 'Republic of Korea'],
            'Vietnam': ['Viet Nam']
        }

        # 3. 中心点坐标库
        COUNTRY_CENTROIDS = {
            'China': [104.19, 35.86], 'United States': [-95.71, 37.09], 'United Kingdom': [-3.43, 55.37],
            'Germany': [10.45, 51.16], 'France': [2.21, 46.22], 'Netherlands': [5.29, 52.13],
            'Russia': [105.31, 61.52], 'Canada': [-106.34, 56.13], 'Brazil': [-51.92, -14.23],
            'Australia': [133.77, -25.27], 'India': [78.96, 20.59], 'Japan': [138.25, 36.20],
            'South Korea': [127.76, 35.90], 'Singapore': [103.81, 1.35], 'Turkey': [35.24, 38.96]
        }
        
        CITY_COORDS_FIX = { 
            'Dubai': (25.20, 55.27), 'Frankfurt': (50.11, 8.68), 'Amsterdam': (52.36, 4.90), 
            'San Jose': (37.33, -121.88), 'Phoenix': (33.44, -112.07), 'Tokyo': (35.68, 139.76),
            'Seoul': (37.56, 126.97), 'London': (51.50, -0.12), 'Singapore': (1.35, 103.81)
        }
        
        from collections import Counter
        country_counter = Counter()
        snapshot = list(SERVERS_CACHE)
        import time 
        now_ts = time.time()
        
        # 临时存储结构
        temp_stats_storage = {}

        for s in snapshot:
            s_name = s.get('name', '')
            
            # --- A. 确定国旗与标准名 ---
            flag_icon = "📍"
            map_name_standard = None
            
            for f, m_name in FLAG_TO_MAP_NAME.items():
                if f in s_name:
                    flag_icon = f
                    map_name_standard = m_name
                    break
            
            if not map_name_standard:
                try:
                    group_str = detect_country_group(s_name, s)
                    if group_str:
                        flag_part = group_str.split(' ')[0]
                        if flag_part in FLAG_TO_MAP_NAME:
                            flag_icon = flag_part
                            map_name_standard = FLAG_TO_MAP_NAME[flag_part]
                except: pass

            try: country_counter[detect_country_group(s_name, s)] += 1
            except: pass

            # --- B. 确定坐标 ---
            lat, lon = None, None
            for city_key, (c_lat, c_lon) in CITY_COORDS_FIX.items():
                if city_key.lower() in s_name.lower(): lat, lon = c_lat, c_lon; break
            if not lat:
                if 'lat' in s and 'lon' in s: lat, lon = s['lat'], s['lon']
                else: 
                    coords = get_coords_from_name(s_name)
                    if coords: lat, lon = coords[0], coords[1]
            
            # --- C. 生成数据点 ---
            if lat and lon and map_name_standard:
                coord_key = f"{lat},{lon}"
                if coord_key not in city_points_map: 
                    city_points_map[coord_key] = {'name': s_name, 'value': [lon, lat], 'country_key': map_name_standard}
                
                if flag_icon != "📍" and flag_icon not in flag_points_map:
                    flag_points_map[flag_icon] = {'name': flag_icon, 'value': [lon, lat], 'country_key': map_name_standard}

            # --- D. 聚合统计数据 (🛑 核心修复位置) ---
            if map_name_standard:
                unique_deployed_countries.add(map_name_standard)
                
                if map_name_standard not in temp_stats_storage:
                    cn_name = map_name_standard
                    try: 
                        full_g = detect_country_group(s_name, s)
                        if full_g and ' ' in full_g: cn_name = full_g.split(' ')[1]
                    except: pass

                    temp_stats_storage[map_name_standard] = {
                        'flag': flag_icon, 'cn': cn_name,
                        'total': 0, 'online': 0, 'servers': []
                    }
                
                rs = temp_stats_storage[map_name_standard]
                rs['total'] += 1
                
                # 🛑 优先检查探针缓存是否在线
                is_on = False
                
                # 1. 检查探针缓存
                probe_cache = PROBE_DATA_CACHE.get(s['url'])
                if probe_cache:
                    # 如果探针数据在 20秒内更新过，视为在线
                    if now_ts - probe_cache.get('last_updated', 0) < 20:
                        is_on = True
                
                # 2. 如果探针不在线，再检查旧的标记 (兼容其他节点)
                if not is_on and s.get('_status') == 'online':
                    is_on = True

                if is_on: rs['online'] += 1
                
                rs['servers'].append({
                    'name': s_name,
                    'status': 'online' if is_on else 'offline'
                })

                if map_name_standard not in COUNTRY_CENTROIDS and lat and lon:
                    COUNTRY_CENTROIDS[map_name_standard] = [lon, lat]

        # --- E. 数据后处理 ---
        for std_name, stats in temp_stats_storage.items():
            stats['servers'].sort(key=lambda x: 0 if x['status'] == 'online' else 1)
            region_stats[std_name] = stats
            active_regions_for_highlight.add(std_name)
            
            if std_name in MAP_NAME_ALIASES:
                for alias in MAP_NAME_ALIASES[std_name]:
                    region_stats[alias] = stats
                    active_regions_for_highlight.add(alias)

        # --- F. 生成饼图数据 ---
        pie_data = []
        if country_counter:
            sorted_counts = country_counter.most_common(5)
            for k, v in sorted_counts: pie_data.append({'name': f"{k} ({v})", 'value': v})
            others = sum(country_counter.values()) - sum(x[1] for x in sorted_counts)
            if others > 0: pie_data.append({'name': f"🏳️ 其他 ({others})", 'value': others})
        else: pie_data.append({'name': '暂无数据', 'value': 0})

        city_list = list(city_points_map.values())
        flag_list = list(flag_points_map.values())
        
        return (
            json.dumps({'cities': city_list, 'flags': flag_list, 'regions': list(active_regions_for_highlight)}, ensure_ascii=False), 
            pie_data, 
            len(unique_deployed_countries), 
            json.dumps(region_stats, ensure_ascii=False),
            json.dumps(COUNTRY_CENTROIDS, ensure_ascii=False)
        )
    except Exception as e:
        print(f"[ERROR] prepare_map_data failed: {e}")
        import traceback; traceback.print_exc()
        return (json.dumps({'cities': [], 'flags': [], 'regions': []}), [], 0, "{}", "{}")


# ==========================================
# 👇全局变量定义 👇
# ==========================================
FILE_LOCK = asyncio.Lock()
EXPANDED_GROUPS = set()
SERVER_UI_MAP = {}
# ==========================================

def init_data():
    # 如果强制路径不存在，说明 Docker 挂载失败，必须报错提醒
    if not os.path.exists(DATA_DIR):
        logger.error(f"❌ 严重错误: 找不到数据目录 {DATA_DIR}！请检查 docker-compose volumes 挂载！")
        # 尝试创建以免程序崩溃，但大概率读不到旧数据
        os.makedirs(DATA_DIR)
    
    global SERVERS_CACHE, SUBS_CACHE, NODES_DATA, ADMIN_CONFIG
    
    logger.info(f"正在读取数据... (目标: {DATA_DIR})")
    
    # 1. 加载服务器
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f: 
                raw_data = json.load(f)
                SERVERS_CACHE = [s for s in raw_data if isinstance(s, dict)]
            logger.info(f"✅ 成功加载服务器: {len(SERVERS_CACHE)} 台")
        except Exception as e:
            logger.error(f"❌ 读取 servers.json 失败: {e}")
            SERVERS_CACHE = []
    else:
        logger.warning(f"⚠️ 未找到服务器配置文件: {CONFIG_FILE}")

    # 2. 加载订阅
    if os.path.exists(SUBS_FILE):
        try:
            with open(SUBS_FILE, 'r', encoding='utf-8') as f: SUBS_CACHE = json.load(f)
        except: SUBS_CACHE = []

    # 3. 加载缓存
    if os.path.exists(NODES_CACHE_FILE):
        # 处理之前误生成的文件夹
        if os.path.isdir(NODES_CACHE_FILE):
             try: 
                import shutil
                shutil.rmtree(NODES_CACHE_FILE)
                logger.info("♻️ 已自动删除错误的缓存文件夹")
             except: pass
             NODES_DATA = {}
        else:
            try:
                with open(NODES_CACHE_FILE, 'r', encoding='utf-8') as f: NODES_DATA = json.load(f)
                count = sum([len(v) for v in NODES_DATA.values() if isinstance(v, list)])
                logger.info(f"✅ 加载缓存节点: {count} 个")
            except: NODES_DATA = {}
    else:
        NODES_DATA = {}
        
    # 4. 加载配置
    if os.path.exists(ADMIN_CONFIG_FILE):
        try:
            with open(ADMIN_CONFIG_FILE, 'r', encoding='utf-8') as f: ADMIN_CONFIG = json.load(f)
        except: ADMIN_CONFIG = {}

    # 初始化设置
    if 'probe_enabled' not in ADMIN_CONFIG:
        ADMIN_CONFIG['probe_enabled'] = True
    if 'probe_token' not in ADMIN_CONFIG:
        ADMIN_CONFIG['probe_token'] = uuid.uuid4().hex

    # 保存一次配置确保持久化
    try:
        with open(ADMIN_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(ADMIN_CONFIG, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"❌ 配置保存失败: {e}")
    # ==========================================================

def _save_file_sync_internal(filename, data):
    # 使用绝对路径生成临时文件
    temp_file = f"{filename}.{uuid.uuid4()}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        shutil.move(temp_file, filename)
    except Exception as e:
        if os.path.exists(temp_file): os.remove(temp_file)
        raise e

async def safe_save(filename, data):
    async with FILE_LOCK:
        try: await run.io_bound(_save_file_sync_internal, filename, data)
        except Exception as e: logger.error(f"❌ 保存 {filename} 失败: {e}")

# ================= 数据保存函数 =================

# 1. 保存服务器列表
async def save_servers(): 
    global GLOBAL_UI_VERSION # ✨ 关键：引入全局版本变量
    
    # 执行保存
    await safe_save(CONFIG_FILE, SERVERS_CACHE)
    
    # ✨ 关键：更新版本号，通知前台 /status 页面进行结构重绘
    GLOBAL_UI_VERSION = time.time() 
    
    # 触发后台仪表盘数据的静默刷新
    await refresh_dashboard_ui()

# 2. 保存管理配置 (分组/设置)
async def save_admin_config(): 
    global GLOBAL_UI_VERSION # ✨ 关键：引入全局版本变量
    
    # 执行保存
    await safe_save(ADMIN_CONFIG_FILE, ADMIN_CONFIG)
    
    # ✨ 关键：更新版本号，通知前台 /status 页面进行结构重绘 (例如分组变化)
    GLOBAL_UI_VERSION = time.time()

async def save_subs(): await safe_save(SUBS_FILE, SUBS_CACHE)

async def save_nodes_cache():
    try:
        # 直接保存所有内存数据，不做任何过滤
        data_snapshot = NODES_DATA.copy()
        await safe_save(NODES_CACHE_FILE, data_snapshot)
        
        # 触发静默更新 (流量变化/节点增删)
        await refresh_dashboard_ui()
    except Exception as e:
        logger.error(f"❌ 保存缓存失败: {e}")

init_data()
managers = {}

def safe_notify(message, type='info', timeout=3000):
    try: ui.notify(message, type=type, timeout=timeout)
    except: logger.info(f"[Notify] {message}")

# ================= SSH 连接核心逻辑 (完全隔离版) =================
def get_ssh_client(server_data):
    """建立 SSH 连接"""
    import paramiko # 确保导入
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # 解析 IP
    raw_url = server_data['url']
    if '://' in raw_url: host = raw_url.split('://')[-1].split(':')[0]
    else: host = raw_url.split(':')[0]
    
    # 优先使用 ssh_host
    if server_data.get('ssh_host'): host = server_data['ssh_host']
    
    port = int(server_data.get('ssh_port') or 22)
    user = server_data.get('ssh_user') or 'root'
    
    # 获取认证类型
    auth_type = server_data.get('ssh_auth_type', '全局密钥').strip()
    
    print(f"🔌 [SSH Debug] 连接目标: {host}, 用户: {user}, 认证方式: [{auth_type}]", flush=True)
    
    try:
        if auth_type == '独立密码':
            pwd = server_data.get('ssh_password', '')
            if not pwd: raise Exception("选择了独立密码，但密码为空")
            
            # ✨ 强制只用密码，不找密钥，不找Agent
            client.connect(host, port, username=user, password=pwd, timeout=5, 
                           look_for_keys=False, allow_agent=False)
                           
        elif auth_type == '独立密钥':
            key_content = server_data.get('ssh_key', '')
            if not key_content: raise Exception("选择了独立密钥，但密钥为空")
            
            key_file = io.StringIO(key_content)
            try: pkey = paramiko.RSAKey.from_private_key(key_file)
            except: 
                key_file.seek(0)
                try: pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except: raise Exception("无法识别的私钥格式")
            
            # ✨✨✨ [此处已修改] 同样强制禁止 Agent 和本地其他密钥 ✨✨✨
            client.connect(host, port, username=user, pkey=pkey, timeout=5,
                           look_for_keys=False, allow_agent=False)
            
        else: # 默认：全局密钥
            g_key = load_global_key()
            if not g_key: raise Exception("全局密钥未配置")
            
            key_file = io.StringIO(g_key)
            try: pkey = paramiko.RSAKey.from_private_key(key_file)
            except: 
                key_file.seek(0)
                try: pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except: raise Exception("全局密钥格式无法识别")
            
            # 全局密钥也加上限制，防止它私自去读你电脑本身的 id_rsa
            client.connect(host, port, username=user, pkey=pkey, timeout=5,
                           look_for_keys=False, allow_agent=False)
            
        return client, f"✅ 已连接 {user}@{host}"
        
    except Exception as e:
        return None, f"❌ 连接失败: {str(e)}"

# =================  交互式 WebSSH 类  =================
def get_ssh_client_sync(server_data):
    return get_ssh_client(server_data)

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
        
# ================= SSH 界面入口  =================
ssh_instances = {} 

def open_ssh_interface(server_data):
    # 1. 清理内容
    content_container.clear()
    
    # h-full: 容器高度占满屏幕，为垂直居中做准备
    # p-6: 保持四周留白，不贴边
    # flex flex-col justify-center: 让内部的灰色大卡片在垂直方向居中！
    content_container.classes(remove='p-0 pl-0 block', add='h-full p-6 flex flex-col justify-center overflow-hidden')
    
    old_ssh = ssh_instances.get('current')
    if old_ssh: old_ssh.close()

    with content_container:
        # ✨ 灰色背景大容器 (Wrapper)
        # w-full: 宽度占满 (满足你的要求)
        # h-[85vh]: 高度固定为视口的 85%，这样上下就会留出空隙，实现“悬浮感”
        with ui.column().classes('w-full h-[85vh] bg-gray-100 rounded-2xl p-4 shadow-2xl border border-gray-200 gap-3 relative'):
            
            # === 1. 顶部大标题栏 (居中) ===
            # relative: 为了让关闭按钮绝对定位
            # justify-center: 让标题文字居中
            with ui.row().classes('w-full items-center justify-center relative mb-1'):
                 
                 # 居中的标题文字
                 with ui.row().classes('items-center gap-3'):
                    ui.icon('dns').classes('text-2xl text-blue-600')
                    ui.label('VPS SSH 客户端连接').classes('text-xl font-extrabold text-gray-800 tracking-wide')
                 
                 # 绝对定位在右侧的关闭按钮
                 with ui.element('div').classes('absolute right-0 top-1/2 -translate-y-1/2'):
                     ui.button(icon='close', on_click=lambda: [close_ssh(), load_dashboard_stats()]) \
                        .props('flat round dense color=grey-7').tooltip('关闭')

            # === 2. 终端卡片 ===
            # flex-grow: 自动填满灰色容器剩余的高度
            with ui.card().classes('w-full flex-grow p-0 gap-0 border border-gray-300 rounded-xl flex flex-col flex-nowrap overflow-hidden shadow-inner min-w-0 relative'):
                
                # --- 内部信息栏 (白色) ---
                with ui.row().classes('w-full h-10 bg-white items-center justify-between px-4 border-b border-gray-200 flex-shrink-0'):
                    
                    # 左侧：服务器信息
                    with ui.row().classes('items-center gap-3 overflow-hidden'):
                        ui.element('div').classes('w-2 h-2 rounded-full bg-green-500 shadow-sm animate-pulse')
                        ui.icon('terminal').classes('text-slate-500')
                        with ui.row().classes('gap-2 items-baseline'):
                             ui.label(server_data['name']).classes('text-sm font-bold text-gray-800 truncate')
                             host_name = server_data.get('url', '').replace('http://', '').split(':')[0]
                             ui.label(f"{server_data.get('ssh_user','root')}@{host_name}").classes('text-xs font-mono text-gray-400 hidden sm:block truncate')

                    # 右侧：断开按钮
                    async def close_and_restore():
                        close_ssh()
                        await load_dashboard_stats()

                    ui.button(icon='link_off', on_click=close_and_restore) \
                        .props('round unelevated dense size=sm color=red-1 text-color=red shadow-none') \
                        .tooltip('断开连接')

                # --- 黑色终端区域 + 底部命令栏区域 ---
                terminal_box = ui.column().classes('w-full flex-grow p-0 overflow-hidden relative min-h-0 min-w-0 flex flex-col')
                
                # 启动 WebSSH
                ssh = WebSSH(terminal_box, server_data)
                ssh_instances['current'] = ssh
                ui.timer(0.1, lambda: asyncio.create_task(ssh.connect()), once=True)

    def close_ssh():
        if ssh_instances.get('current'):
            ssh_instances['current'].close()
            ssh_instances['current'] = None
        # 关闭时恢复布局
        content_container.clear()
        content_container.classes(remove='h-full flex flex-col justify-center overflow-hidden', add='block overflow-y-auto')
            
def _exec(server_data, cmd, log_area):
    client, msg = get_ssh_client(server_data)
    if not client:
        log_area.push(msg)
        return
    try:
        # get_pty=True 模拟伪终端，能获取更好的输出格式
        # timeout=10 设置 10 秒超时，防止卡死
        stdin, stdout, stderr = client.exec_command(cmd, timeout=10, get_pty=True)
        
        # 读取输出 (二进制转字符串)
        out = stdout.read().decode('utf-8', errors='ignore').strip()
        err = stderr.read().decode('utf-8', errors='ignore').strip()
        
        if out: log_area.push(out)
        if err: log_area.push(f"ERR: {err}")
        
        # 如果都没有输出且没有报错
        if not out and not err:
            log_area.push("✅ 命令已执行 (无返回内容)")
            
    except  paramiko.SSHException as e:
         log_area.push(f"SSH Error: {str(e)}")
    except socket.timeout:
         log_area.push("❌ 执行超时: 命令执行时间过长或正在等待交互 (如 sudo/vim)")
    except Exception as e:
        log_area.push(f"系统错误: {repr(e)}") # 使用 repr 显示详细错误类型
    finally:
        client.close()
# ================= Cloudflare API 工具类 =================
class CloudflareHandler:
    def __init__(self):
        self.token = ADMIN_CONFIG.get('cf_api_token', '')
        self.email = ADMIN_CONFIG.get('cf_email', '')
        self.root_domain = ADMIN_CONFIG.get('cf_root_domain', '')
        self.base_url = "https://api.cloudflare.com/client/v4"
        
    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.email and "global" in self.token.lower():
            h["X-Auth-Email"] = self.email
            h["X-Auth-Key"] = self.token
        else:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get_zone_id(self, domain_name=None):
        target = self.root_domain
        if domain_name:
            if self.root_domain and domain_name.endswith(self.root_domain):
                target = self.root_domain
            else:
                parts = domain_name.split('.')
                if len(parts) >= 2: target = f"{parts[-2]}.{parts[-1]}"

        url = f"{self.base_url}/zones?name={target}"
        try:
            r = requests.get(url, headers=self._headers(), timeout=10)
            data = r.json()
            if data.get('success') and len(data['result']) > 0:
                return data['result'][0]['id'], None
            return None, f"未找到 Zone: {target}"
        except Exception as e: return None, str(e)

    def set_ssl_flexible(self, zone_id):
        url = f"{self.base_url}/zones/{zone_id}/settings/ssl"
        try:
            payload = {"value": "flexible"}
            r = requests.patch(url, headers=self._headers(), json=payload, timeout=10)
            if r.json().get('success'): return True, "SSL 已强制设为 Flexible"
            return True, "SSL 设置指令已发送" 
        except Exception as e: return False, str(e)

    async def auto_configure(self, ip, sub_prefix):
        if not self.token: return False, "未配置 API Token"
        def _task():
            zone_id, err = self.get_zone_id()
            if not zone_id: return False, err
            
            self.set_ssl_flexible(zone_id)
            
            full_domain = f"{sub_prefix}.{self.root_domain}"
            url = f"{self.base_url}/zones/{zone_id}/dns_records"
            payload = {"type": "A", "name": full_domain, "content": ip, "ttl": 1, "proxied": True}
            try: 
                r = requests.post(url, headers=self._headers(), json=payload, timeout=10)
                if r.json().get('success'): return True, f"解析成功: {full_domain}"
                else: return False, f"CF API 报错: {r.text}"
            except Exception as e: return False, str(e)
            
        return await run.io_bound(_task)

    #删除指定域名的解析记录
    async def delete_record_by_domain(self, domain_to_delete):
        if not self.token: return False, "未配置 Cloudflare Token"
        if not domain_to_delete: return False, "域名为空"
        
        # 安全检查：只允许删除属于当前配置根域名的子域名
        # 防止误删 www.visa.com.hk 或 www.icloud.com
        if self.root_domain not in domain_to_delete:
            return False, f"安全拦截: {domain_to_delete} 不属于根域名 {self.root_domain}"

        def _task():
            # 1. 获取 Zone ID
            zone_id, err = self.get_zone_id(domain_to_delete)
            if not zone_id: return False, f"找不到 Zone: {err}"

            # 2. 搜索该域名的记录 ID
            search_url = f"{self.base_url}/zones/{zone_id}/dns_records?name={domain_to_delete}"
            try:
                r = requests.get(search_url, headers=self._headers(), timeout=10)
                data = r.json()
                if not data.get('success'): return False, "查询记录失败"
                
                records = data.get('result', [])
                if not records: return True, "记录不存在，无需删除" # 没找到也算成功
                
                # 3. 执行删除 (如果有多个同名记录，全部删除)
                deleted_count = 0
                for rec in records:
                    rec_id = rec['id']
                    del_url = f"{self.base_url}/zones/{zone_id}/dns_records/{rec_id}"
                    requests.delete(del_url, headers=self._headers(), timeout=5)
                    deleted_count += 1
                
                return True, f"已清理 {deleted_count} 条 DNS 记录"

            except Exception as e: return False, str(e)

        return await run.io_bound(_task)


# ================= 核心网络类 =================
class XUIManager:
    def __init__(self, url, username, password, api_prefix=None):
        self.original_url = str(url).strip().rstrip('/')
        self.url = self.original_url
        self.username = str(username).strip()
        self.password = str(password).strip()
        self.api_prefix = f"/{api_prefix.strip('/')}" if api_prefix else None
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0', 'Connection': 'close'})
        self.session.verify = False 
        self.login_path = None

    def _request(self, method, path, **kwargs):
        target_url = f"{self.url}{path}"
        for attempt in range(2):
            try:
                # ✨ 终极修复 1：将超时时间延长到 30 秒，彻底治愈甲骨文 AMD 卡顿
                if method == 'POST': return self.session.post(target_url, timeout=30, allow_redirects=False, **kwargs)
                else: return self.session.get(target_url, timeout=30, allow_redirects=False, **kwargs)
            except Exception as e:
                if attempt == 1: return None

    def login(self):
        if self.login_path:
            if self._try_login_at(self.login_path): return True
            self.login_path = None 
        paths = ['/login', '/xui/login', '/panel/login']
        if self.api_prefix: paths.insert(0, f"{self.api_prefix}/login")
        protocols = [self.original_url]
        if '://' not in self.original_url: protocols = [f"http://{self.original_url}", f"https://{self.original_url}"]
        elif self.original_url.startswith('http://'): protocols.append(self.original_url.replace('http://', 'https://'))
        elif self.original_url.startswith('https://'): protocols.append(self.original_url.replace('https://', 'http://'))
        for proto_url in protocols:
            self.url = proto_url
            for path in paths:
                if self._try_login_at(path):
                    self.login_path = path
                    return True
        return False

    def _try_login_at(self, path):
        try:
            r = self._request('POST', path, data={'username': self.username, 'password': self.password})
            if r and r.status_code == 200 and r.json().get('success') == True: return True
            return False
        except: return False

    def get_inbounds(self):
        if not self.login(): return None
        candidates = []
        if self.login_path: candidates.append(self.login_path.replace('login', 'inbound/list'))
        defaults = ['/xui/inbound/list', '/panel/inbound/list', '/inbound/list']
        if self.api_prefix: defaults.insert(0, f"{self.api_prefix}/inbound/list")
        for d in defaults: 
            if d not in candidates: candidates.append(d)
        for path in candidates:
            r = self._request('POST', path)
            if r and r.status_code == 200:
                try: 
                    res = r.json()
                    if res.get('success'): return res.get('obj')
                except: pass
        return None


    def get_server_status(self):
        """获取服务器系统状态 (CPU, 内存, 硬盘, Uptime)"""
        if not self.login(): return None
        
        # 适配不同版本的 X-UI API 路径
        candidates = []
        if self.login_path: candidates.append(self.login_path.replace('login', 'server/status'))
        defaults = ['/xui/server/status', '/panel/server/status', '/server/status']
        if self.api_prefix: defaults.insert(0, f"{self.api_prefix}/server/status")
        
        for d in defaults: 
            if d not in candidates: candidates.append(d)
            
        for path in candidates:
            try:
                # server/status 通常是 POST 请求
                r = self._request('POST', path)
                if r and r.status_code == 200:
                    res = r.json()
                    if res.get('success'): return res.get('obj')
            except: pass
        return None

    def add_inbound(self, data): return self._action('/add', data)
    def update_inbound(self, iid, data): return self._action(f'/update/{iid}', data)
    def delete_inbound(self, iid): return self._action(f'/del/{iid}', {})
    
    def _action(self, suffix, data):
        if not self.login(): 
            logger.error("❌ [API] 未登录，无法执行操作")
            return False, "登录失败"
            
        # ✨ 终极修复：如果是在根目录登录的，节点管理的真实路径应该在 /xui/inbound 下
        if self.login_path == '/login':
            base = '/xui/inbound'
        else:
            base = self.login_path.replace('/login', '/inbound')
        path = f"{base}{suffix}"
        
        # ✨ 1. 深度清洗并转换数据 (修复 Python/Go 布尔值与 JSON 兼容陷阱)
        import json
        payload = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                payload[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, bool):
                payload[k] = "true" if v else "false" # Go 语言只认小写 true
            else:
                payload[k] = str(v)

        # ✨ 2. 极限调试日志
        target_url = f"{self.url}{path}"
        logger.info(f"🔍 [API Debug] 发送目标: {target_url}")
        logger.info(f"📦 [API Debug] 原始数据: {data}")
        logger.info(f"🚀 [API Debug] 序列化后: {payload}")

        try:
            logger.info("⏳ [API Debug] 等待 X-UI 后端响应 (最长30秒)...")
            
            # 强制指定 Accept 接受 JSON
            headers = {'Accept': 'application/json'}
            r = self.session.post(target_url, data=payload, headers=headers, timeout=30, allow_redirects=False)
            
            # ✨ 3. 打印极详尽的响应结果
            logger.info(f"✅ [API Debug] 响应状态码: {r.status_code}")
            logger.info(f"📄 [API Debug] 原始返回内容: {r.text[:1000]}") 
            
            if r.status_code == 200:
                resp = r.json()
                if resp.get('success'): 
                    return True, resp.get('msg')
                else: 
                    return False, f"后端拒绝: {resp.get('msg')}"
            else:
                return False, f"HTTP错误: {r.status_code}"
                
        except Exception as e:
            import traceback
            logger.error(f"❌ [API Debug] 网络底层错误: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"请求异常: {str(e)}"

def get_manager(server_conf):
    # --- 优先级 1：SSH / Root 探针模式 (上帝模式) ---
    # 只要检测到安装了探针且配置了 SSH Host，无论有没有 API 账号，都优先走 SSH 通道。
    # 优点：操作数据库更稳，无需担心 API 端口被封或 API 特征检测。
    if server_conf.get('probe_installed') and server_conf.get('ssh_host'):
        url = server_conf.get('url')
        # 使用特殊前缀作为 key 缓存 SSH 管理器实例
        mgr_key = f"ssh_{url}"
        if mgr_key not in managers:
            managers[mgr_key] = SSHXUIManager(server_conf)
        return managers[mgr_key]

    # --- 优先级 2：标准 API 模式 ---
    # 只有当 SSH 不可用（未配置探针/SSH）时，才尝试使用 API 账号登录。
    url = server_conf.get('url')
    if url and server_conf.get('user') and server_conf.get('pass'):
        if url not in managers:
            managers[url] = XUIManager(url, server_conf['user'], server_conf['pass'], server_conf.get('prefix'))
        return managers[url]
    
    # --- 兜底 ---
    raise Exception("无法创建管理器：未配置 SSH 且缺少 X-UI 账号信息")

# ================== 核心扩展：SSH 数据库直连管理器 ==================
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
              
# ================= 即时存档 + 顺序修正 =================

# 1. 辅助函数：后台线程执行
async def run_in_bg_executor(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(BG_EXECUTOR, func, *args)

# 2. 单个服务器同步逻辑 
async def fetch_inbounds_safe(server_conf, force_refresh=False, sync_name=False):
    url = server_conf['url']
    
    # 探针机器处理：除非强制刷新，否则直接信任推送的缓存
    if server_conf.get('probe_installed', False) and not force_refresh:
        return NODES_DATA.get(url, [])
    
    # 如果不是强制刷新且已有数据，直接返回
    if not force_refresh and url in NODES_DATA and NODES_DATA[url]: 
        return NODES_DATA[url]
    
    async with SYNC_SEMAPHORE:
        try:
            mgr = get_manager(server_conf)
            # 增加超时判断
            inbounds = await asyncio.wait_for(run_in_bg_executor(mgr.get_inbounds), timeout=15)
            
            if inbounds is not None:
                NODES_DATA[url] = inbounds
                server_conf['_status'] = 'online'
                # ... (保持原有的同步名称逻辑)
                return inbounds
            
            # --- 关键修复：同步失败时，不要设置为空列表，保留之前的缓存 ---
            # 仅在完全没有旧数据时才标记离线
            if url not in NODES_DATA:
                NODES_DATA[url] = []
                server_conf['_status'] = 'offline'
            return NODES_DATA.get(url, [])
            
        except Exception as e:
            logger.warning(f"⚠️ {server_conf.get('name')} 同步跳过: {e}")
            # 发生异常时保留现场，不更新 _status 为 offline，防止误报
            return NODES_DATA.get(url, [])

# 3. 批量静默刷新逻辑 (防抖 + 空缓存穿透)
async def silent_refresh_all(is_auto_trigger=False):
    # 1. 读取上次时间
    last_time = ADMIN_CONFIG.get('last_sync_time', 0)
    
    if is_auto_trigger:
        current_time = time.time()
        
        # === 检查缓存节点数 ===
        total_nodes = 0
        try:
            for nodes in NODES_DATA.values():
                if isinstance(nodes, list): total_nodes += len(nodes)
        except: pass

        # 穿透条件：有服务器配置 但 缓存里完全没数据 (说明之前可能还没来得及存就崩了)
        if len(SERVERS_CACHE) > 0 and total_nodes == 0:
            logger.warning(f"⚠️ [防抖穿透] 缓存为空 (节点数0)，强制触发首次修复同步！")
            # 继续向下执行同步...
        
        # 冷却条件
        elif current_time - last_time < SYNC_COOLDOWN_SECONDS:
            remaining = int(SYNC_COOLDOWN_SECONDS - (current_time - last_time))
            logger.info(f"⏳ [防抖生效] 距离上次同步不足 {SYNC_COOLDOWN_SECONDS}秒，跳过 (剩余: {remaining}s)")
                      
            return

    # 2. 执行同步流程
    safe_notify(f'🚀 开始后台静默刷新 ({len(SERVERS_CACHE)} 个服务器)...')
    
    # 只要开始跑了，就标记为"已更新"，防止重启后重复触发
    ADMIN_CONFIG['last_sync_time'] = time.time()
    await save_admin_config() 
    
    tasks = []
    for srv in SERVERS_CACHE:
        # 使用之前那个带即时保存功能的 fetch 函数
        tasks.append(fetch_inbounds_safe(srv, force_refresh=True))
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # 跑完再保存一次兜底（双保险）
    await save_nodes_cache() 
    
    safe_notify('✅ 后台刷新完成', 'positive')
    try: 
        render_sidebar_content.refresh()
        await load_dashboard_stats() 
    except: pass


# ================= 探针与监控设置弹窗 =================
def open_probe_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl p-6 flex flex-col gap-4'):
        with ui.row().classes('justify-between items-center w-full border-b pb-2'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('tune', color='primary').classes('text-xl')
                ui.label('探针与监控设置').classes('text-lg font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        with ui.scroll_area().classes('w-full h-[60vh] pr-4'):
            with ui.column().classes('w-full gap-6'):
                
                # 1. 主控端地址 (从全局 SSH 设置移入)
                with ui.column().classes('w-full bg-blue-50 p-4 rounded-lg border border-blue-100'):
                    ui.label('📡 主控端外部地址 (Agent连接地址)').classes('text-sm font-bold text-blue-900')
                    ui.label('Agent 将向此地址推送数据。请填写 http://公网IP:端口 或 https://域名').classes('text-xs text-blue-700 mb-2')
                    default_url = ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')
                    url_input = ui.input(value=default_url, placeholder='http://1.2.3.4:8080').classes('w-full bg-white').props('outlined dense')

                # 2. 三网测速目标
                with ui.column().classes('w-full'):
                    ui.label('🚀 三网延迟测速目标 (Ping)').classes('text-sm font-bold text-gray-700')
                    ui.label('修改后需点击“更新探针”才能在服务器上生效。').classes('text-xs text-gray-400 mb-2')
                    
                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-3 gap-3'):
                        ping_ct = ui.input('电信目标 IP', value=ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')).props('outlined dense')
                        ping_cu = ui.input('联通目标 IP', value=ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')).props('outlined dense')
                        ping_cm = ui.input('移动目标 IP', value=ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')).props('outlined dense')

                # 3. 通知设置 (预留功能)
                with ui.column().classes('w-full'):
                    ui.label('🤖 Telegram 通知 ').classes('text-sm font-bold text-gray-700')
                    ui.label('用于掉线报警等通知 (当前版本尚未实装)').classes('text-xs text-gray-400 mb-2')
                    
                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-3'):
                        tg_token = ui.input('Bot Token', value=ADMIN_CONFIG.get('tg_bot_token', '')).props('outlined dense')
                        tg_id = ui.input('Chat ID', value=ADMIN_CONFIG.get('tg_chat_id', '')).props('outlined dense')

        # 保存按钮
        async def save_settings():
            # 保存 URL
            url_val = url_input.value.strip().rstrip('/')
            if url_val: ADMIN_CONFIG['manager_base_url'] = url_val
            
            # 保存 Ping 目标
            ADMIN_CONFIG['ping_target_ct'] = ping_ct.value.strip()
            ADMIN_CONFIG['ping_target_cu'] = ping_cu.value.strip()
            ADMIN_CONFIG['ping_target_cm'] = ping_cm.value.strip()
            
            # 保存 TG
            ADMIN_CONFIG['tg_bot_token'] = tg_token.value.strip()
            ADMIN_CONFIG['tg_chat_id'] = tg_id.value.strip()
            
            await save_admin_config()
            safe_notify('✅ 设置已保存 (请记得重新安装/更新探针以应用新配置)', 'positive')
            d.close()

        ui.button('保存设置', icon='save', on_click=save_settings).classes('w-full bg-slate-900 text-white shadow-lg h-12')
    d.open()

 
# =================  单台安装探针 =================
async def install_probe_on_server(server_conf):
    name = server_conf.get('name', 'Unknown')
    auth_type = server_conf.get('ssh_auth_type', '全局密钥')
    if auth_type == '独立密码' and not server_conf.get('ssh_password'): return False
    if auth_type == '独立密钥' and not server_conf.get('ssh_key'): return False
    
    my_token = ADMIN_CONFIG.get('probe_token', 'default_token')
    
    # 1. 获取主控端地址
    manager_url = ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080') 
    
    # 2. 获取自定义测速点 (如果没有设置，使用默认值)
    ping_ct = ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68') # 电信
    ping_cu = ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')  # 联通
    ping_cm = ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')  # 移动

    # 3. 替换脚本中的变量
    real_script = PROBE_INSTALL_SCRIPT \
        .replace("__MANAGER_URL__", manager_url) \
        .replace("__TOKEN__", my_token) \
        .replace("__SERVER_URL__", server_conf['url']) \
        .replace("__PING_CT__", ping_ct) \
        .replace("__PING_CU__", ping_cu) \
        .replace("__PING_CM__", ping_cm)

    # 4. 执行安装 (保持原有 Paramiko 逻辑)
    def _do_install():
        client = None
        try:
            client, msg = get_ssh_client_sync(server_conf)
            if not client: return False, f"SSH连接失败: {msg}"
            stdin, stdout, stderr = client.exec_command(real_script, timeout=60)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status == 0: return True, "Agent 安装成功并启动"
            return False, f"安装脚本错误 (Exit {exit_status})"
        except Exception as e:
            return False, f"异常: {str(e)}"
        finally:
            if client: client.close()

    success, msg = await run.io_bound(_do_install)
    if success:
        server_conf['probe_installed'] = True
        await save_servers()
        logger.info(f"✅ [Push Agent] {name} 部署成功")
    else:
        logger.warning(f"⚠️ [Push Agent] {name} 部署失败: {msg}")
    return success

# ================= 批量安装所有探针  =================
async def batch_install_all_probes():
    if not SERVERS_CACHE:
        safe_notify("没有服务器可安装", "warning")
        return

    safe_notify(f"正在后台为 {len(SERVERS_CACHE)} 台服务器安装/更新探针...", "ongoing")
    
    # ✨ 限制并发数：同时只允许 10 台服务器进行 SSH 连接，防止卡死
    sema = asyncio.Semaphore(10)

    async def _worker(server_conf):
        name = server_conf.get('name', 'Unknown')
        async with sema:
            # 1. 打印开始日志
            logger.info(f"🚀 [AutoInstall] {name} 开始安装...")
            
            # 2. 执行安装 (复用已有的单台安装函数)
            success = await install_probe_on_server(server_conf)
            
            # 3. 这里的日志会在 install_probe_on_server 内部打印，或者我们可以补充
            # (原函数 install_probe_on_server 内部已经有成功/失败的日志了)

    # 创建任务列表
    tasks = [_worker(s) for s in SERVERS_CACHE]
    
    # 并发执行
    if tasks:
        await asyncio.gather(*tasks)
    
    safe_notify("✅ 所有探针安装/更新任务已完成", "positive")
    
# =================  获取服务器状态 (纯探针模式：拒绝一切 API 登录) =================
async def get_server_status(server_conf):
    raw_url = server_conf['url']
    
    # 只有当服务器安装了 Python 探针脚本，才从缓存读取数据
    if server_conf.get('probe_installed', False) or raw_url in PROBE_DATA_CACHE:
        cache = PROBE_DATA_CACHE.get(raw_url)
        if cache:
            # 检查数据新鲜度 (15秒超时)
            if time.time() - cache.get('last_updated', 0) < 15:
                return cache 
            else:
                return {'status': 'offline', 'msg': '探针离线 (超时)'}
    
    # 🛑 对于 X-UI 面板账号，直接返回离线，不尝试登录
    return {'status': 'offline', 'msg': '未安装探针'}
# ================= 使用 URL 安全的 Base64 =================
def safe_base64(s): 
    # 使用 urlsafe_b64encode 避免出现 + 和 /
    return base64.urlsafe_b64encode(s.encode('utf-8')).decode('utf-8')

def decode_base64_safe(s): 
    try: 
        # 兼容标准 Base64 和 URL Safe Base64
        # 补全 padding
        missing_padding = len(s) % 4
        if missing_padding: s += '=' * (4 - missing_padding)
        return base64.urlsafe_b64decode(s).decode('utf-8')
    except: 
        try: return base64.b64decode(s).decode('utf-8')
        except: return ""

# ================= 生成 SubConverter 转换链接 =================
def generate_converted_link(raw_link, target, domain_prefix):
    """
    生成经过 SubConverter 转换的订阅链接
    target: surge, clash
    """
    if not raw_link or not domain_prefix: return ""
    
    converter_base = f"{domain_prefix}/convert"
    encoded_url = quote(raw_link)

    params = f"target={target}&url={encoded_url}&insert=false&list=true&ver=4&udp=true&scv=true"
    
    return f"{converter_base}?{params}"

# ================= 生成节点链接 =================
def generate_node_link(node, server_host):
    try:
        # 清洗 server_host，只保留纯 IP/域名 
        clean_host = server_host
        # 1. 去掉协议头 (http:// 或 https://)
        if '://' in clean_host:
            clean_host = clean_host.split('://')[-1]
        # 2. 去掉端口 (例如 :54321)
        # 注意：排除 IPv6 ([...]) 的情况，这里简单处理 IPv4 和域名
        if ':' in clean_host and not clean_host.startswith('['):
            clean_host = clean_host.split(':')[0]

        p = node['protocol']; remark = node['remark']; port = node['port']
        # 使用清洗后的 clean_host 作为默认地址
        add = node.get('listen') or clean_host
        
        s = json.loads(node['settings']) if isinstance(node['settings'], str) else node['settings']
        st = json.loads(node['streamSettings']) if isinstance(node['streamSettings'], str) else node['streamSettings']
        net = st.get('network', 'tcp'); tls = st.get('security', 'none'); path = ""; host = ""
        
        if net == 'ws': 
            path = st.get('wsSettings',{}).get('path','/')
            host = st.get('wsSettings',{}).get('headers',{}).get('Host','')
        elif net == 'grpc': 
            path = st.get('grpcSettings',{}).get('serviceName','')
        
        if p == 'vmess':
            # 构建标准的 v2 VMess json
            v = {
                "v": "2",
                "ps": remark,
                "add": add,      # 这里现在是纯 IP 了
                "port": port,    # 这里的端口才是节点端口 (如 14789)
                "id": s['clients'][0]['id'],
                "aid": "0",
                "scy": "auto",
                "net": net,
                "type": "none",
                "host": host,
                "path": path,
                "tls": tls
            }
            return "vmess://" + safe_base64(json.dumps(v))
            
        elif p == 'vless':
            params = f"type={net}&security={tls}"
            if path: params += f"&path={path}" if net != 'grpc' else f"&serviceName={path}"
            if host: params += f"&host={host}"
            return f"vless://{s['clients'][0]['id']}@{add}:{port}?{params}#{remark}"
            
        elif p == 'trojan': 
            return f"trojan://{s['clients'][0]['password']}@{add}:{port}?type={net}&security={tls}#{remark}"
            
        elif p == 'shadowsocks': 
            cred = f"{s['method']}:{s['password']}"
            return f"ss://{safe_base64(cred)}@{add}:{port}#{remark}"
            
    except Exception as e: 
        # print(f"Generate Link Error: {e}")
        return ""
    return ""
# ================= 生成 Surge/Loon 格式明文配置 =================
def generate_detail_config(node, server_host):
    try:
        # 1. 基础信息清洗
        clean_host = server_host.replace('http://', '').replace('https://', '')
        if ':' in clean_host and not clean_host.startswith('['):
            clean_host = clean_host.split(':')[0]

        remark = node.get('remark', 'Unnamed').replace(',', '_').replace('=', '_').strip()
        address = node.get('listen') or clean_host
        port = node['port'] # 数据库里存的是 "20000-50000"
        
        # === A. 自定义节点 (Snell / Hy2 / XHTTP) ===
        if node.get('_is_custom'):
            raw_link = node.get('_raw_link', '')
            
            # [新增] Snell 解析逻辑
            if raw_link.startswith('snell://'):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(raw_link)
                psk = parsed.username
                s_host = parsed.hostname or address 
                s_port = parsed.port or port
                
                params = parse_qs(parsed.query)
                version = params.get('version', ['4'])[0]
                
                return f"{remark} = snell, {s_host}, {s_port}, psk={psk}, version={version}, tfo=true, reuse=true"

            # [原有] Hy2 解析逻辑
            elif raw_link.startswith('hy2://'):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(raw_link)
                password = parsed.username
                h_host = parsed.hostname or address 
                
                # ✨✨✨ 修复核心：优先使用范围字符串 ✨✨✨
                if str(port) and '-' in str(port):
                    h_port = port
                else:
                    h_port = parsed.port or port
                
                params = parse_qs(parsed.query)
                sni = params.get('sni', [''])[0] or params.get('peer', [''])[0]
                
                line = f"{remark} = hysteria2, {h_host}, {h_port}, password={password}"
                if sni: line += f", sni={sni}"
                line += ", skip-cert-verify=true, download-bandwidth=500, udp-relay=true"
                return line
                
            elif raw_link.startswith('vless://'):
                 return f"// Surge 暂未原生支持 XHTTP: {remark}"

        # === B. 面板标准节点 (VMess / Trojan) ===
        protocol = node['protocol']
        settings = json.loads(node['settings']) if isinstance(node['settings'], str) else node['settings']
        stream = json.loads(node['streamSettings']) if isinstance(node['streamSettings'], str) else node['streamSettings']
        net = stream.get('network', 'tcp')
        security = stream.get('security', 'none')
        tls = (security == 'tls') or (security == 'reality')
        
        if protocol == 'vmess':
            uuid = settings['clients'][0]['id']
            line = f"{remark} = vmess, {address}, {port}, username={uuid}"
            if net == 'ws':
                ws_set = stream.get('wsSettings', {})
                path = ws_set.get('path', '/')
                panel_host = ws_set.get('headers', {}).get('Host', '')
                sni = ""
                if tls:
                    tls_set = stream.get('tlsSettings', {})
                    sni = tls_set.get('serverName', '')
                line += f", ws=true, ws-path={path}"
                if tls and sni: pass 
                elif panel_host: line += f", ws-headers=Host:{panel_host}"
            if tls:
                line += ", tls=true"
                tls_set = stream.get('tlsSettings', {})
                sni = tls_set.get('serverName', '')
                if sni: line += f", sni={sni}"
                line += ", skip-cert-verify=true"
            line += ", tfo=true, udp-relay=true"
            return line

        elif protocol == 'trojan':
            password = settings['clients'][0]['password']
            line = f"{remark} = trojan, {address}, {port}, password={password}"
            if tls:
                line += ", tls=true"
                sni = stream.get('tlsSettings', {}).get('serverName', '')
                if sni: line += f", sni={sni}"
                line += ", skip-cert-verify=true"
            line += ", tfo=true, udp-relay=true"
            return line

    except Exception as e:
        return f"// Config Error: {str(e)}"
    
    return ""
# ================= 延迟测试核心逻辑  =================
PING_CACHE = {}

async def batch_ping_nodes(nodes, raw_host):
    """
    使用多进程池并行 Ping，彻底解放主线程。
    """
    # 如果进程池还没启动（比如刚开机），直接返回，防止报错
    if not PROCESS_POOL: return 

    loop = asyncio.get_running_loop()
    
    # 1. 准备任务列表
    targets = []
    for n in nodes:
        # 获取真实地址
        host = n.get('listen')
        if not host or host == '0.0.0.0': host = raw_host
        port = n.get('port')
        key = f"{host}:{port}"
        targets.append((host, port, key))

    # 2. 定义回调处理 (将子进程的结果更新到主进程缓存)
    async def run_single_ping(t_host, t_port, t_key):
        try:
            # ✨ 核心：将同步的 ping 扔给进程池执行
            # 这行代码会在另一个进程里跑，绝对不会卡住你的网页
            latency = await loop.run_in_executor(PROCESS_POOL, sync_ping_worker, t_host, t_port)
            PING_CACHE[t_key] = latency
        except:
            PING_CACHE[t_key] = -1

    # 3. 并发分发任务
    # 虽然这里用了 await gather，但这只是在等待结果，计算压力全在 ProcessPool
    tasks = [run_single_ping(h, p, k) for h, p, k in targets]
    if tasks:
        await asyncio.gather(*tasks)

# ================= 探针数据被动接收接口 =================
@app.post('/api/probe/push')
async def probe_push_data(request: Request):
    try:
        data = await request.json()
        token = data.get('token')
        server_url = data.get('server_url')
        
        # 1. 校验 Token
        correct_token = ADMIN_CONFIG.get('probe_token')
        if not token or token != correct_token:
            return Response("Invalid Token", 403)
            
        # 2. 查找服务器 (精准匹配 -> IP匹配)
        target_server = next((s for s in SERVERS_CACHE if s['url'] == server_url), None)
        if not target_server:
            try:
                push_ip = server_url.split('://')[-1].split(':')[0]
                for s in SERVERS_CACHE:
                    cache_ip = s['url'].split('://')[-1].split(':')[0]
                    if cache_ip == push_ip:
                        target_server = s
                        break
            except: pass

        if target_server:
            # 激活探针状态
            if not target_server.get('probe_installed'):
                 target_server['probe_installed'] = True
            
            # 3. 写入基础监控数据缓存
            data['status'] = 'online'
            data['last_updated'] = time.time()
            PROBE_DATA_CACHE[target_server['url']] = data
            
            # ✨✨✨ 核心逻辑：处理 X-UI 数据 & 自动命名 ✨✨✨
            if 'xui_data' in data and isinstance(data['xui_data'], list):
                # 解析节点
                raw_nodes = data['xui_data']
                parsed_nodes = []
                for n in raw_nodes:
                    try:
                        if isinstance(n.get('settings'), str): 
                            n['settings'] = json.loads(n['settings'])
                        if isinstance(n.get('streamSettings'), str): 
                            n['streamSettings'] = json.loads(n['streamSettings'])
                        parsed_nodes.append(n)
                    except: 
                        parsed_nodes.append(n)
                
                # 更新节点缓存
                NODES_DATA[target_server['url']] = parsed_nodes
                target_server['_status'] = 'online'

                # 🟢自动同步名称逻辑 (当端口不通时依赖此逻辑)
                # 只有当有节点，且当前名字看起来像默认IP时，才尝试修改
                if parsed_nodes:
                    first_remark = parsed_nodes[0].get('remark', '').strip()
                    current_name = target_server.get('name', '').strip()
                    
                    # 简单的判断：如果名字里没有这个备注
                    if first_remark and (first_remark not in current_name):
                        
                        # ：先检查备注里是否自带了国旗 
                        has_own_flag = False
                        # 遍历全局配置中的所有已知国旗
                        for v in AUTO_COUNTRY_MAP.values():
                            known_flag = v.split(' ')[0] # 提取 "🇺🇸"
                            if known_flag in first_remark:
                                has_own_flag = True
                                break
                        
                        if has_own_flag:
                            # 情况 A：备注自带国旗 (如 "Oracle|🇺🇸凤凰城") -> 直接用，不加前缀
                            new_name_candidate = first_remark
                        else:
                            # 情况 B：备注没国旗 -> 尝试继承旧国旗或查询 GeoIP 加上
                            flag = "🏳️"
                            # 1. 尝试沿用当前名字里的国旗
                            if ' ' in current_name:
                                parts = current_name.split(' ', 1)
                                if len(parts[0]) < 10: 
                                    flag = parts[0]
                            else:
                                # 2. 尝试重新获取国旗 (GeoIP)
                                try:
                                    ip_key = target_server['url'].split('://')[-1].split(':')[0]
                                    geo_info = IP_GEO_CACHE.get(ip_key)
                                    if geo_info: 
                                        flag = get_flag_for_country(geo_info[2]).split(' ')[0]
                                except: pass

                            new_name_candidate = f"{flag} {first_remark}"
                        
                        # 执行改名并保存
                        if target_server['name'] != new_name_candidate:
                            target_server['name'] = new_name_candidate
                            asyncio.create_task(save_servers())
                            logger.info(f"🏷️ [探针同步] 根据节点备注自动改名: {new_name_candidate}")

            # 记录历史
            record_ping_history(target_server['url'], data.get('pings', {}))
            
        return Response("OK", 200)
    except Exception as e:
        return Response("Error", 500)

# =================  订阅接口：严格遵循自定义顺序 =================
@app.get('/sub/{token}')
async def sub_handler(token: str, request: Request):
    sub = next((s for s in SUBS_CACHE if s['token'] == token), None)
    if not sub: return Response("Invalid Token", 404)
    
    links = []
    
    # 1. 构建快速查找字典 (Map)
    # 格式: { 'url|id': (node_data, server_host) }
    node_lookup = {}
    
    for srv in SERVERS_CACHE:
        # 获取 Host
        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url)
            host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except: host = raw_url

        # 收集面板节点
        panel_nodes = NODES_DATA.get(srv['url'], []) or []
        for n in panel_nodes:
            key = f"{srv['url']}|{n['id']}"
            node_lookup[key] = (n, host)
            
        # 收集自定义节点
        custom_nodes = srv.get('custom_nodes', []) or []
        for n in custom_nodes:
            key = f"{srv['url']}|{n['id']}"
            node_lookup[key] = (n, host)

    # 2. 按照订阅中保存的顺序生成链接
    # sub['nodes'] 是你在管理面板里排好序的 ID 列表
    ordered_ids = sub.get('nodes', [])
    
    for key in ordered_ids:
        if key in node_lookup:
            node, host = node_lookup[key]
            
            # A. 优先使用原始链接
            if node.get('_raw_link'):
                links.append(node['_raw_link'])
            # B. 生成标准链接
            else:
                l = generate_node_link(node, host)
                if l: links.append(l)
                    
    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")
    
# ================= 分组订阅接口：支持 Tag 和 主分组 =================
@app.get('/sub/group/{group_b64}')
async def group_sub_handler(group_b64: str, request: Request):
    group_name = decode_base64_safe(group_b64)
    if not group_name: return Response("Invalid Group Name", 400)
    
    links = []
    
    # 筛选符合分组的服务器
    target_servers = [
        s for s in SERVERS_CACHE 
        if s.get('group', '默认分组') == group_name or group_name in s.get('tags', [])
    ]
    
    logger.info(f"正在生成分组订阅: [{group_name}]，匹配到 {len(target_servers)} 个服务器")

    for srv in target_servers:
        # 1. 获取面板节点
        panel_nodes = NODES_DATA.get(srv['url'], []) or []
        # 2. 获取自定义节点
        custom_nodes = srv.get('custom_nodes', []) or []
        # === 合并 ===
        all_nodes = panel_nodes + custom_nodes
        
        if not all_nodes: continue
        
        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url); host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except: host = raw_url
        
        for n in all_nodes:
            if n.get('enable'): 
                # A. 优先使用原始链接
                if n.get('_raw_link'):
                    links.append(n['_raw_link'])
                # B. 生成面板节点链接
                else:
                    l = generate_node_link(n, host)
                    if l: links.append(l)
    
    if not links:
        return Response(f"// Group [{group_name}] is empty or not found", media_type="text/plain; charset=utf-8")
        
    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")

# ================= 短链接接口：分组 (完美混合版) =================
@app.get('/get/group/{target}/{group_b64}')
async def short_group_handler(target: str, group_b64: str, request: Request):
    try:
        group_name = decode_base64_safe(group_b64)
        if not group_name: return Response("Invalid Group Name", 400)

        # -------------------------------------------------------------
        # 策略 A: 针对 Surge / Loon -> 使用 Python 原生生成 (解决 Hy2 无法转换 + VMess 格式问题)
        # -------------------------------------------------------------
        if target == 'surge':
            links = []
            
            # 1. 筛选服务器
            target_servers = [
                s for s in SERVERS_CACHE 
                if s.get('group', '默认分组') == group_name or group_name in s.get('tags', [])
            ]
            
            # 2. 遍历服务器生成配置
            for srv in target_servers:
                panel_nodes = NODES_DATA.get(srv['url'], []) or []
                custom_nodes = srv.get('custom_nodes', []) or []
                
                # 获取干净的 Host
                raw_url = srv['url']
                try:
                    if '://' not in raw_url: raw_url = f'http://{raw_url}'
                    parsed = urlparse(raw_url)
                    host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
                except: host = raw_url

                # 合并处理面板节点和自定义节点
                for n in (panel_nodes + custom_nodes):
                    if n.get('enable'):
                        # 调用我们修复后的 generate_detail_config
                        line = generate_detail_config(n, host)
                        if line and not line.startswith('//') and not line.startswith('None'):
                            links.append(line)
            
            if not links:
                return Response(f"// Group [{group_name}] is empty", media_type="text/plain; charset=utf-8")
                
            return Response("\n".join(links), media_type="text/plain; charset=utf-8")

        # -------------------------------------------------------------
        # 策略 B: 针对 Clash / 其他 -> 继续使用 SubConverter
        # (注意：SubConverter 可能依然无法解析 Hy2，但能正常解析 VMess)
        # -------------------------------------------------------------
        custom_base = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
        if custom_base: 
            base_url = custom_base
        else:
            host = request.headers.get('host')
            scheme = request.url.scheme
            base_url = f"{scheme}://{host}"

        internal_api = f"{base_url}/sub/group/{group_b64}"
        
        # 关键参数：scv=true (跳过证书验证), udp=true
        params = { 
            "target": target, 
            "url": internal_api, 
            "insert": "false", 
            "list": "true", 
            "ver": "4", 
            "udp": "true", 
            "scv": "true" 
        }
        
        converter_api = "http://subconverter:25500/sub"

        def _fetch_sync():
            try: return requests.get(converter_api, params=params, timeout=10)
            except: return None

        response = await run.io_bound(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            return Response(f"SubConverter Error (Code: {getattr(response, 'status_code', 'Unk')})", status_code=502)

    except Exception as e: return Response(f"Error: {str(e)}", status_code=500)
    
# ================= 短链接接口：严格遵循自定义顺序 =================
@app.get('/get/sub/{target}/{token}')
async def short_sub_handler(target: str, token: str, request: Request):
    try:
        sub_obj = next((s for s in SUBS_CACHE if s['token'] == token), None)
        if not sub_obj: return Response("Subscription Not Found", 404)
        
        # -------------------------------------------------------------
        # 策略 A: 针对 Surge -> Python 原生生成 (严格顺序版)
        # -------------------------------------------------------------
        if target == 'surge':
            links = []
            
            # 1. 构建查找字典
            node_lookup = {}
            for srv in SERVERS_CACHE:
                # 解析 Host
                raw_url = srv['url']
                try:
                    if '://' not in raw_url: raw_url = f'http://{raw_url}'
                    parsed = urlparse(raw_url)
                    host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
                except: host = raw_url
                
                # 收集所有节点
                all_nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
                for n in all_nodes:
                    key = f"{srv['url']}|{n['id']}"
                    node_lookup[key] = (n, host)

            # 2. 按顺序生成配置
            ordered_ids = sub_obj.get('nodes', [])
            
            for key in ordered_ids:
                if key in node_lookup:
                    node, host = node_lookup[key]
                    # 生成 Surge 配置行
                    line = generate_detail_config(node, host)
                    if line and not line.startswith('//') and not line.startswith('None'):
                        links.append(line)
                            
            return Response("\n".join(links), media_type="text/plain; charset=utf-8")

        # -------------------------------------------------------------
        # 策略 B: Clash / 其他 -> SubConverter
        # -------------------------------------------------------------
        # SubConverter 会读取上一步 sub_handler 生成的原始订阅
        # 只要 sub_handler 是有序的，SubConverter 输出也就是有序的
        
        custom_base = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
        if custom_base: 
            base_url = custom_base
        else:
            host = request.headers.get('host')
            scheme = request.url.scheme
            base_url = f"{scheme}://{host}"
            
        internal_api = f"{base_url}/sub/{token}"
        opt = sub_obj.get('options', {})
        
        params = {
            "target": target, "url": internal_api, 
            "insert": "false", "list": "true", "ver": "4",
            "emoji": str(opt.get('emoji', True)).lower(), 
            "udp": str(opt.get('udp', True)).lower(),
            "tfo": str(opt.get('tfo', False)).lower(), 
            "scv": str(opt.get('skip_cert', True)).lower(),
            "fdn": "false", # 强制不过滤域名
            "sort": "false", # ✨✨✨ 关键：告诉 SubConverter 不要再次排序，保持原样
        }
        
        # 处理正则过滤 (保持原样)
        regions = opt.get('regions', [])
        includes = []
        if opt.get('include_regex'): includes.append(opt['include_regex'])
        if regions:
            region_keywords = []
            for r in regions:
                parts = r.split(' '); k = parts[1] if len(parts)>1 else r
                region_keywords.append(k)
                for c, v in AUTO_COUNTRY_MAP.items(): 
                    if v == r and len(c) == 2: region_keywords.append(c)
            if region_keywords: includes.append(f"({'|'.join(region_keywords)})")
        
        if includes: params['include'] = "|".join(includes)
        if opt.get('exclude_regex'): params['exclude'] = opt['exclude_regex']
        
        ren_pat = opt.get('rename_pattern', '')
        if ren_pat: params['rename'] = f"{ren_pat}@{opt.get('rename_replacement', '')}"

        converter_api = "http://subconverter:25500/sub"
        
        def _fetch_sync():
            try: return requests.get(converter_api, params=params, timeout=10)
            except: return None

        response = await run.io_bound(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            return Response(f"SubConverter Error (Code: {getattr(response, 'status_code', 'Unk')})", status_code=502)

    except Exception as e: return Response(f"Error: {str(e)}", status_code=500)

# ================= 探针主动注册接口=================
@app.post('/api/probe/register')
async def probe_register(request: Request):
    try:
        data = await request.json()
        
        # 1. 安全校验
        submitted_token = data.get('token')
        correct_token = ADMIN_CONFIG.get('probe_token')
        
        if not submitted_token or submitted_token != correct_token:
            return Response(json.dumps({"success": False, "msg": "Token 错误"}), status_code=403)

        # 2. 获取客户端真实 IP
        client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
        
        # 3. ✨✨✨ 智能查重逻辑 (核心修改) ✨✨✨
        target_server = None
        
        # 策略 A: 直接字符串匹配 (命中纯 IP 注册的情况)
        for s in SERVERS_CACHE:
            if client_ip in s['url']:
                target_server = s
                break
        
        # 策略 B: 如果没找到，尝试 DNS 反向解析 (命中域名注册的情况)
        if not target_server:
            logger.info(f"🔍 [探针注册] IP {client_ip} 未直接匹配，尝试解析现有域名...")
            for s in SERVERS_CACHE:
                try:
                    # 提取缓存中的 Host (可能是域名)
                    cached_host = s['url'].split('://')[-1].split(':')[0]
                    
                    # 跳过已经是 IP 的
                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", cached_host): continue
                    
                    # 解析域名为 IP (使用 run.io_bound 防止阻塞)
                    resolved_ip = await run.io_bound(socket.gethostbyname, cached_host)
                    
                    if resolved_ip == client_ip:
                        target_server = s
                        logger.info(f"✅ [探针注册] 域名 {cached_host} 解析为 {client_ip}，匹配成功！")
                        break
                except: pass

        # 4. 逻辑分支
        if target_server:
            # === 情况 1: 已存在，仅激活探针 ===
            if not target_server.get('probe_installed'):
                target_server['probe_installed'] = True
                await save_servers() # 保存状态
                await refresh_dashboard_ui() # 刷新UI
            
            return Response(json.dumps({"success": True, "msg": "已合并现有服务器"}), status_code=200)

        else:
            # === 情况 2: 完全陌生的机器，新建 ===
            # (之前的创建逻辑保持不变)
            new_server = {
                'name': f"🏳️ {client_ip}", 
                'group': '自动注册',
                'url': f"http://{client_ip}:54321",
                'user': 'admin',
                'pass': 'admin',
                'ssh_auth_type': '全局密钥',
                'probe_installed': True,
                '_status': 'online'
            }
            SERVERS_CACHE.append(new_server)
            await save_servers()
            
            # 触发强制重命名
            asyncio.create_task(force_geoip_naming_task(new_server))
            
            await refresh_dashboard_ui()
            try: render_sidebar_content.refresh()
            except: pass
            
            logger.info(f"✨ [主动注册] 新服务器上线: {client_ip}")
            return Response(json.dumps({"success": True, "msg": "注册成功"}), status_code=200)

    except Exception as e:
        logger.error(f"❌ 注册接口异常: {e}")
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500)
        
# ================= 辅助：单机极速修正  =================
async def fast_resolve_single_server(s):
    """
    后台全自动修正流程：
    1. 尝试连接面板，读取第一个节点的备注名 (Smart Name)
    2. 尝试查询 IP 归属地，获取国旗 (GeoIP)
    3. 自动组合名字 (防止国旗重复)
    4. 自动归类分组
    """
    await asyncio.sleep(1.5) # 稍微错峰
    
    raw_ip = s['url'].split('://')[-1].split(':')[0]
    logger.info(f"🔍 [智能修正] 正在处理: {raw_ip} ...")
    
    data_changed = False
    
    try:
        # --- 步骤 1: 尝试从面板获取真实备注 ---
        # 只有当名字看起来像默认 IP (或带白旗的IP) 时，才去面板读取
        # 这样防止覆盖用户手动修改过的名字
        current_pure_name = s['name'].replace('🏳️', '').strip()
        
        if current_pure_name == raw_ip:
            try:
                smart_name = await generate_smart_name(s)
                # 如果获取到了有效名字 (不是 IP，也不是默认的 Server-X)
                if smart_name and smart_name != raw_ip and not smart_name.startswith('Server-'):
                    s['name'] = smart_name
                    data_changed = True
                    logger.info(f"🏷️ [获取备注] 成功: {smart_name}")
            except Exception as e:
                logger.warning(f"⚠️ [获取备注] 失败: {e}")

        # --- 步骤 2: 查 IP 归属地并修正国旗/分组 ---
        geo = await run.io_bound(fetch_geo_from_ip, s['url'])
        
        if geo:
            # geo: (lat, lon, "CountryName")
            country_name = geo[2]
            s['lat'] = geo[0]; s['lon'] = geo[1]; s['_detected_region'] = country_name
            
            # 获取正确的国旗
            flag_group = get_flag_for_country(country_name)
            flag_icon = flag_group.split(' ')[0] # 提取 "🇸🇬"
            
            # ✨✨✨ [核心修复] 国旗防重复逻辑 ✨✨✨
            # 1. 先把白旗去掉，拿到干净的名字
            temp_name = s['name'].replace('🏳️', '').strip()
            
            # 2. 检查名字里是否已经包含了正确的国旗 (无论在什么位置)
            if flag_icon in temp_name:
                # 如果包含了 (例如 "微软云|🇸🇬新加坡")，我们只更新去掉白旗后的样子
                # 绝不强行加前缀
                if s['name'] != temp_name:
                    s['name'] = temp_name
                    data_changed = True
            else:
                # 3. 如果完全没包含，才加到最前面
                s['name'] = f"{flag_icon} {temp_name}"
                data_changed = True

            # --- 步骤 3: 强制自动分组 ---
            target_group = flag_group 
            
            # 尝试在配置里找精确匹配
            for k, v in AUTO_COUNTRY_MAP.items():
                if flag_icon in k or flag_icon in v:
                    target_group = v
                    break
            
            if s.get('group') != target_group:
                s['group'] = target_group
                data_changed = True
                
        else:
            logger.warning(f"⚠️ [GeoIP] 未获取到地理位置: {raw_ip}")

        # --- 步骤 4: 保存变更 ---
        if data_changed:
            await save_servers()
            await refresh_dashboard_ui()
            try: render_sidebar_content.refresh()
            except: pass
            logger.info(f"✅ [智能修正] 完毕: {s['name']} -> [{s['group']}]")
            
    except Exception as e:
        logger.error(f"❌ [智能修正] 严重错误: {e}")

# ================= 后台智能探测 SSH 用户名 =================
async def smart_detect_ssh_user_task(server_conf):
    """
    后台任务：尝试使用不同的用户名 (ubuntu -> root) 连接 SSH。
    连接成功后：
    1. 更新配置并保存。
    2. 自动触发探针安装。
    """
    # 待测试的用户名列表 (优先尝试 ubuntu，失败则尝试 root)
    # 你可以在这里添加更多，比如 'ec2-user', 'debian', 'opc'
    candidates = ['root', 'ubuntu']
    
    ip = server_conf['url'].split('://')[-1].split(':')[0]
    original_user = server_conf.get('ssh_user', '')
    
    logger.info(f"🕵️‍♂️ [智能探测] 开始探测 {server_conf['name']} ({ip}) 的 SSH 用户名...")

    found_user = None

    for user in candidates:
        # 1. 临时修改配置中的用户名
        server_conf['ssh_user'] = user
        
        # 2. 尝试连接 (复用现有的连接函数，自带全局密钥逻辑)
        # 注意：get_ssh_client_sync 内部有 5秒 超时，适合做探测
        client, msg = await run.io_bound(get_ssh_client_sync, server_conf)
        
        if client:
            # ✅ 连接成功！
            client.close()
            found_user = user
            logger.info(f"✅ [智能探测] 成功匹配用户名: {user}")
            break
        else:
            logger.warning(f"⚠️ [智能探测] 用户名 '{user}' 连接失败，尝试下一个...")

    # 3. 处理探测结果
    if found_user:
        # 保存正确的用户名
        server_conf['ssh_user'] = found_user
        # 标记探测成功，防止后续逻辑误判
        server_conf['_ssh_verified'] = True 
        await save_servers()
        
        # 🎉 探测成功后，立即触发探针安装 (如果开启了探针功能)
        if ADMIN_CONFIG.get('probe_enabled', False):
            logger.info(f"🚀 [自动部署] SSH 验证通过，开始安装探针...")
            # 稍作延迟，等待 SSH 服务稳定
            await asyncio.sleep(2) 
            await install_probe_on_server(server_conf)
            
    else:
        # ❌ 全部失败，恢复回默认 (或者保留最后一个尝试失败的)
        logger.error(f"❌ [智能探测] {server_conf['name']} 所有用户名均尝试失败 (请检查安全组或密钥)")
        # 可选：恢复为 root 或者保持原状
        if original_user: server_conf['ssh_user'] = original_user
        await save_servers()

    
# ================= 自动注册接口 =================
@app.post('/api/auto_register_node')
async def auto_register_node(request: Request):
    try:
        # 1. 获取并解析数据
        data = await request.json()
        
        # 2. 安全验证
        secret = data.get('secret')
        if secret != AUTO_REGISTER_SECRET:
            logger.warning(f"⚠️ [自动注册] 密钥错误: {secret}")
            return Response(json.dumps({"success": False, "msg": "密钥错误"}), status_code=403, media_type="application/json")

        # 3. 提取字段
        ip = data.get('ip')
        port = data.get('port')
        username = data.get('username')
        password = data.get('password')
        alias = data.get('alias', f'Auto-{ip}') 
        
        # 可选参数
        ssh_port = data.get('ssh_port', 22)

        if not all([ip, port, username, password]):
            return Response(json.dumps({"success": False, "msg": "参数不完整"}), status_code=400, media_type="application/json")

        target_url = f"http://{ip}:{port}"
        
        # 4. 构建配置字典
        new_server_config = {
            'name': alias,
            'group': '默认分组',
            'url': target_url,
            'user': username,
            'pass': password,
            'prefix': '',
            
            # SSH 配置
            'ssh_port': ssh_port,
            'ssh_auth_type': '全局密钥',
            'ssh_user': 'detecting...', # 初始占位符，稍后会被后台任务覆盖
            'probe_installed': False
        }

        # 5. 查重与更新逻辑
        existing_index = -1
        # 标准化 URL 进行比对
        for idx, srv in enumerate(SERVERS_CACHE):
            cache_url = srv['url'].replace('http://', '').replace('https://', '')
            new_url_clean = target_url.replace('http://', '').replace('https://', '')
            if cache_url == new_url_clean:
                existing_index = idx
                break

        action_msg = ""
        target_server_ref = None 

        if existing_index != -1:
            # 更新现有节点
            SERVERS_CACHE[existing_index].update(new_server_config)
            target_server_ref = SERVERS_CACHE[existing_index]
            action_msg = f"🔄 更新节点: {alias}"
        else:
            # 新增节点
            SERVERS_CACHE.append(new_server_config)
            target_server_ref = new_server_config
            action_msg = f"✅ 新增节点: {alias}"

        # 6. 保存到硬盘
        await save_servers()
        
        # ================= ✨✨✨ 后台任务启动区 ✨✨✨ =================
        
        # 任务A: 启动 GeoIP 命名任务 (自动变国旗)
        asyncio.create_task(force_geoip_naming_task(target_server_ref))
        
        # 任务B: 启动智能 SSH 用户探测任务 (先试ubuntu，再试root，成功后装探针)
        asyncio.create_task(smart_detect_ssh_user_task(target_server_ref))
        
        # =============================================================

        try: render_sidebar_content.refresh()
        except: pass
        
        logger.info(f"[自动注册] {action_msg} ({ip}) - 已加入 SSH 探测与命名队列")
        return Response(json.dumps({"success": True, "msg": "注册成功，后台正在探测连接..."}), status_code=200, media_type="application/json")

    except Exception as e:
        logger.error(f"❌ [自动注册] 处理异常: {e}")
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500, media_type="application/json")

def show_loading(container):
    try:
        container.clear()
        with container:
            with ui.column().classes('w-full h-[60vh] justify-center items-center'):
                ui.spinner('dots', size='3rem', color='primary')
                ui.label('数据处理中...').classes('text-gray-500 mt-4')
    except: pass

def get_all_groups():
    groups = {'默认分组', '自动注册'}
    for s in SERVERS_CACHE:
        g = s.get('group')
        if g: groups.add(g)
    return sorted(list(groups))

async def safe_copy_to_clipboard(text):
    safe_text = json.dumps(text).replace('"', '\\"') 
    js_code = f"""
    (async () => {{
        const text = {json.dumps(text)};
        try {{
            await navigator.clipboard.writeText(text);
            return true;
        }} catch (err) {{
            const textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.position = "fixed";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {{
                document.execCommand('copy');
                document.body.removeChild(textArea);
                return true;
            }} catch (err2) {{
                document.body.removeChild(textArea);
                return false;
            }}
        }}
    }})()
    """
    try:
        result = await ui.run_javascript(js_code)
        if result: safe_notify('已复制到剪贴板', 'positive')
        else: safe_notify('复制失败', 'negative')
    except: safe_notify('复制功能不可用', 'negative')

# =================  支持格式转换的分组复制 (自适应版) =================
async def copy_group_link(group_name, target=None):
    try:
        # 1. 智能获取当前面板域名
        origin = get_dynamic_origin()
        
        # 2. 如果 Python 获取失败（极少数情况），尝试用 JS 补救
        if "YOUR-DOMAIN" in origin:
            try: origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
            except: pass
            
        encoded_name = safe_base64(group_name)
        
        if target:
            final_link = f"{origin}/get/group/{target}/{encoded_name}"
            msg_prefix = "Surge" if target == 'surge' else "Clash"
        else:
            final_link = f"{origin}/sub/group/{encoded_name}"
            msg_prefix = "原始"
            
        await safe_copy_to_clipboard(final_link)
        safe_notify(f"已复制 [{group_name}] {msg_prefix} 订阅", "positive")
    except Exception as e: safe_notify(f"生成失败: {e}", "negative")
    
# ================= UI 组件：节点编辑器 (修复版：适配 SSH/HTTP 双模式) =================
class InboundEditor:
    def __init__(self, mgr, data=None, on_success=None):
        self.mgr = mgr; self.cb = on_success; self.is_edit = data is not None
        if not data:
            random_port = random.randint(10000, 65000)
            self.d = {
                "enable": True, "remark": "", "port": random_port, "protocol": "vmess",
                "settings": {"clients": [{"id": str(uuid.uuid4()), "alterId": 0}], "disableInsecureEncryption": False},
                "streamSettings": {"network": "tcp", "security": "none"},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]}
            }
        else: self.d = data.copy()
        
        # 兼容处理 JSON 字符串
        for k in ['settings', 'streamSettings']:
            if isinstance(self.d.get(k), str):
                try: self.d[k] = json.loads(self.d[k])
                except: self.d[k] = {}

    def ui(self, dlg):
        with ui.card().classes('w-full max-w-4xl p-6 flex flex-col gap-4'):
            title = '编辑节点' if self.is_edit else '新建节点'
            with ui.row().classes('justify-between items-center'):
                ui.label(title).classes('text-xl font-bold')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense color=grey')
            
            with ui.row().classes('w-full gap-4'):
                self.rem = ui.input('备注', value=self.d.get('remark')).classes('flex-grow')
                self.ena = ui.switch('启用', value=self.d.get('enable', True)).classes('mt-2')
            
            with ui.row().classes('w-full gap-4'):
                self.pro = ui.select(['vmess', 'vless', 'trojan', 'shadowsocks', 'socks'], value=self.d['protocol'], label='协议', on_change=self.on_protocol_change).classes('w-1/3')
                self.prt = ui.number('端口', value=self.d['port'], format='%.0f').classes('w-1/3')
                ui.button(icon='shuffle', on_click=lambda: self.prt.set_value(int(run.io_bound(lambda: __import__('random').randint(10000, 60000))))).props('flat dense').tooltip('随机端口')
            
            ui.separator().classes('my-2'); self.auth_box = ui.column().classes('w-full gap-2'); self.refresh_auth_ui(); ui.separator().classes('my-2')
            
            with ui.row().classes('w-full gap-4'):
                st = self.d.get('streamSettings', {})
                self.net = ui.select(['tcp', 'ws', 'grpc'], value=st.get('network', 'tcp'), label='传输协议').classes('w-1/3')
                self.sec = ui.select(['none', 'tls'], value=st.get('security', 'none'), label='安全加密').classes('w-1/3')
            
            with ui.row().classes('w-full justify-end mt-6'): 
                ui.button('保存', on_click=lambda: self.save(dlg)).props('color=primary')

    def on_protocol_change(self, e):
        p = e.value; s = self.d.get('settings', {})
        if p in ['vmess', 'vless']:
            if 'clients' not in s: self.d['settings'] = {"clients": [{"id": str(uuid.uuid4()), "alterId": 0}], "disableInsecureEncryption": False}
        elif p == 'trojan':
            if 'clients' not in s or 'password' not in s.get('clients', [{}])[0]: self.d['settings'] = {"clients": [{"password": str(uuid.uuid4().hex[:8])}]}
        elif p == 'shadowsocks':
            if 'password' not in s: self.d['settings'] = {"method": "aes-256-gcm", "password": str(uuid.uuid4().hex[:10]), "network": "tcp,udp"}
        elif p == 'socks':
            if 'accounts' not in s: self.d['settings'] = {"auth": "password", "accounts": [{"user": "admin", "pass": "admin"}], "udp": False}
        self.d['protocol'] = p; self.refresh_auth_ui()

    def refresh_auth_ui(self):
        self.auth_box.clear(); p = self.pro.value; s = self.d.get('settings', {})
        with self.auth_box:
            if p in ['vmess', 'vless']:
                clients = s.get('clients', [{}]); cid = clients[0].get('id', str(uuid.uuid4()))
                ui.label('认证 (UUID)').classes('text-sm font-bold text-gray-500')
                uuid_inp = ui.input('UUID', value=cid).classes('w-full').on_value_change(lambda e: s['clients'][0].update({'id': e.value}))
                ui.button('生成 UUID', on_click=lambda: uuid_inp.set_value(str(uuid.uuid4()))).props('flat dense size=sm')
            elif p == 'trojan':
                clients = s.get('clients', [{}]); pwd = clients[0].get('password', '')
                ui.input('密码', value=pwd).classes('w-full').on_value_change(lambda e: s['clients'][0].update({'password': e.value}))
            elif p == 'shadowsocks':
                method = s.get('method', 'aes-256-gcm'); pwd = s.get('password', '')
                with ui.row().classes('w-full gap-4'):
                    ui.select(['aes-256-gcm', 'chacha20-ietf-poly1305', 'aes-128-gcm'], value=method, label='加密').classes('flex-1').on_value_change(lambda e: s.update({'method': e.value}))
                    ui.input('密码', value=pwd).classes('flex-1').on_value_change(lambda e: s.update({'password': e.value}))
            elif p == 'socks':
                accounts = s.get('accounts', [{}]); user = accounts[0].get('user', ''); pwd = accounts[0].get('pass', '')
                with ui.row().classes('w-full gap-4'):
                    ui.input('用户名', value=user).classes('flex-1').on_value_change(lambda e: s['accounts'][0].update({'user': e.value}))
                    ui.input('密码', value=pwd).classes('flex-1').on_value_change(lambda e: s['accounts'][0].update({'pass': e.value}))

    async def save(self, dlg):
        # 1. 收集表单数据
        self.d['remark'] = self.rem.value
        self.d['enable'] = self.ena.value
        try:
            port_val = int(self.prt.value)
            if port_val <= 0 or port_val > 65535: raise ValueError
            self.d['port'] = port_val
        except: safe_notify("请输入有效端口", "negative"); return
        self.d['protocol'] = self.pro.value
        
        if 'streamSettings' not in self.d: self.d['streamSettings'] = {}
        self.d['streamSettings']['network'] = self.net.value
        self.d['streamSettings']['security'] = self.sec.value
        
        if 'sniffing' not in self.d: 
            self.d['sniffing'] = {"enabled": True, "destOverride": ["http", "tls"]}

        # 2. ✨✨✨ 核心修复：自动判断是 SSH 还是 HTTP，HTTP需转字符串 ✨✨✨
        try:
            success, msg = False, ""
            
            # 判断是否是 SSH 管理器
            is_ssh_manager = hasattr(self.mgr, '_exec_remote_script')
            
            # ✨ 专为 X-UI HTTP API 准备的数据载荷 (将字典转为 JSON 字符串)
            api_payload = self.d.copy()
            if not is_ssh_manager:
                import json
                for k in ['settings', 'streamSettings', 'sniffing']:
                    if k in api_payload and isinstance(api_payload[k], dict):
                        api_payload[k] = json.dumps(api_payload[k])
            
            if is_ssh_manager:
                # SSH 模式 (异步调用，内部已处理 dumps)
                if self.is_edit:
                    success, msg = await self.mgr.update_inbound(self.d['id'], self.d)
                else:
                    success, msg = await self.mgr.add_inbound(self.d)
            else:
                # HTTP 模式 (使用转换后的 api_payload)
                if self.is_edit:
                    success, msg = await run.io_bound(self.mgr.update_inbound, api_payload['id'], api_payload)
                else:
                    success, msg = await run.io_bound(self.mgr.add_inbound, api_payload)

            if success:
                safe_notify(f"✅ {msg}", "positive")
                dlg.close()
                if self.cb:
                    res = self.cb()
                    if asyncio.iscoroutine(res): await res
            else:
                safe_notify(f"❌ 失败: {msg}", "negative", timeout=5000)
                
        except Exception as e:
            safe_notify(f"❌ 系统异常: {str(e)}", "negative")

async def open_inbound_dialog(mgr, data, cb):
    with ui.dialog() as d: InboundEditor(mgr, data, cb).ui(d); d.open()
    
# ================= 节点删除逻辑 (修复版：适配 SSH/HTTP 双模式) =================
async def delete_inbound(mgr, id, cb):
    try:
        success, msg = False, ""
        
        # 1. 判断是否是 SSH 管理器 (通过检查是否有 _exec_remote_script 方法)
        is_ssh_manager = hasattr(mgr, '_exec_remote_script')
        
        if is_ssh_manager:
            # SSH 模式 (我们在 SSHXUIManager 里定义的是 async 方法，直接 await)
            success, msg = await mgr.delete_inbound(id)
        else:
            # HTTP 模式 (XUIManager 里是同步方法，必须放入 io_bound 线程池防止卡顿)
            success, msg = await run.io_bound(mgr.delete_inbound, id)

        # 2. 处理结果
        if success:
            safe_notify(f"✅ {msg}", "positive")
            # 执行回调刷新 UI (例如刷新列表)
            if cb:
                res = cb()
                if asyncio.iscoroutine(res): await res
        else:
            safe_notify(f"❌ 删除失败: {msg}", "negative")
            
    except Exception as e:
        safe_notify(f"❌ 系统异常: {str(e)}", "negative")


# ================= 带二次确认的删除逻辑 =================
async def delete_inbound_with_confirm(mgr, inbound_id, inbound_remark, callback):
    with ui.dialog() as d, ui.card():
        ui.label('删除确认').classes('text-lg font-bold text-red-600')
        ui.label(f"您确定要永久删除节点 [{inbound_remark}] 吗？").classes('text-base mt-2')
        ui.label("此操作不可恢复。").classes('text-xs text-gray-400 mb-4')
        
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            
            async def do_delete():
                d.close()
                # 调用原有的删除逻辑
                await delete_inbound(mgr, inbound_id, callback)
                
            ui.button('确定删除', color='red', on_click=do_delete)
    d.open()
# ================= 订阅编辑器 (已增加搜索功能) =================
class SubEditor:
    def __init__(self, data=None):
        self.data = data
        if data:
            self.d = data.copy()
            if 'token' not in self.d: self.d['token'] = str(uuid.uuid4())
            if 'nodes' not in self.d: self.d['nodes'] = []
        else:
            self.d = {'name': '', 'token': str(uuid.uuid4()), 'nodes': []}
            
        self.sel = set(self.d.get('nodes', []))
        self.groups_data = {} 
        self.all_node_keys = set()
        
        # ✨ 新增：搜索相关状态
        self.search_term = "" 
        self.visible_node_keys = set() # 用于存储当前搜索结果显示的节点Key

        self.name_input = None 
        self.token_input = None 

    def ui(self, dlg):
        with ui.card().classes('w-[90vw] max-w-4xl p-0 bg-white').style('display: flex; flex-direction: column; height: 85vh;'):
            with ui.row().classes('w-full justify-between items-center p-4 border-b bg-gray-50'):
                ui.label('订阅编辑器').classes('text-xl font-bold')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense')
            
            with ui.element('div').classes('w-full flex-grow overflow-y-auto p-4').style('display: flex; flex-direction: column; gap: 1rem;'):
                self.name_input = ui.input('订阅名称', value=self.d.get('name', '')).classes('w-full').props('outlined')
                self.name_input.on_value_change(lambda e: self.d.update({'name': e.value}))
                
                with ui.row().classes('w-full items-center gap-2'):
                    self.token_input = ui.input('订阅路径 (Token)', value=self.d.get('token', ''), placeholder='例如: my-phone').classes('flex-grow').props('outlined')
                    self.token_input.on_value_change(lambda e: self.d.update({'token': e.value.strip()}))
                    ui.button(icon='refresh', on_click=lambda: self.token_input.set_value(str(uuid.uuid4()))).props('flat dense').tooltip('生成随机 UUID')

                # ✨ 修改：操作栏增加搜索框
                with ui.column().classes('w-full gap-2 bg-gray-100 p-3 rounded'):
                    # 第一行：标题和搜索框
                    with ui.row().classes('w-full items-center gap-4'):
                        ui.label('节点列表').classes('font-bold ml-2 flex-shrink-0')
                        # 搜索输入框
                        ui.input(placeholder='🔍 搜索节点或服务器...', on_change=self.on_search_change).props('outlined dense bg-white').classes('flex-grow')

                    # 第二行：全选/清空按钮 (针对当前搜索结果)
                    with ui.row().classes('w-full justify-end gap-2'):
                        ui.label('操作当前列表:').classes('text-xs text-gray-500 self-center')
                        ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense size=sm color=primary bg-white')
                        ui.button('清空', on_click=lambda: self.toggle_all(False)).props('flat dense size=sm color=red bg-white')

                self.cont = ui.column().classes('w-full').style('display: flex; flex-direction: column; gap: 10px;')
            
            with ui.row().classes('w-full p-4 border-t'):
                async def save():
                    if self.name_input: self.d['name'] = self.name_input.value
                    if self.token_input: 
                        new_token = self.token_input.value.strip()
                        if not new_token: return safe_notify("订阅路径不能为空", "negative")
                        if (not self.data) or (self.data.get('token') != new_token):
                            for s in SUBS_CACHE:
                                if s.get('token') == new_token: return safe_notify(f"路径 '{new_token}' 已被占用", "negative")
                        self.d['token'] = new_token
                        
                    self.d['nodes'] = list(self.sel)
                    if self.data: 
                        try: idx = SUBS_CACHE.index(self.data); SUBS_CACHE[idx] = self.d
                        except: SUBS_CACHE.append(self.d)
                    else: SUBS_CACHE.append(self.d)
                    
                    await save_subs()
                    await load_subs_view()
                    dlg.close()
                    ui.notify('订阅保存成功', color='positive')

                ui.button('保存', icon='save', on_click=save).classes('w-full h-12 bg-slate-900 text-white')

        asyncio.create_task(self.load_data())

    # ✨ 新增：搜索处理函数
    def on_search_change(self, e):
        self.search_term = str(e.value).lower().strip()
        self.render_list()

    async def load_data(self):
        with self.cont: 
            ui.spinner('dots').classes('self-center mt-10')

        current_servers_snapshot = list(SERVERS_CACHE)
        
        # 并发获取面板节点
        tasks = [fetch_inbounds_safe(s, force_refresh=False) for s in current_servers_snapshot]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        self.groups_data = {}
        self.all_node_keys = set()
        
        for i, srv in enumerate(current_servers_snapshot):
            # 1. 获取面板数据
            nodes = results[i]
            if not nodes or isinstance(nodes, Exception): 
                nodes = NODES_DATA.get(srv['url'], []) or []
            
            # 2. 获取自定义数据 (Hy2/XHTTP)
            custom = srv.get('custom_nodes', []) or []
            
            # === 合并显示 ===
            all_server_nodes = nodes + custom
            
            if all_server_nodes:
                for n in all_server_nodes:
                    # 注册 Key 用于全选功能
                    k = f"{srv['url']}|{n['id']}"
                    self.all_node_keys.add(k)
            
            g_name = srv.get('group', '默认分组') or '默认分组'
            if g_name not in self.groups_data: self.groups_data[g_name] = []
            
            # 将合并后的列表传给 UI 渲染
            self.groups_data[g_name].append({'server': srv, 'nodes': all_server_nodes})

        self.render_list()

    def render_list(self):
        self.cont.clear()
        self.visible_node_keys = set() # 重置可见节点集合

        with self.cont:
            if not self.groups_data:
                ui.label('暂无数据').classes('text-center w-full mt-4')
                return

            sorted_groups = sorted(self.groups_data.keys())
            has_match = False # 标记是否有匹配项

            for g_name in sorted_groups:
                # 预先筛选：检查该分组下是否有符合搜索条件的节点
                servers_in_group = self.groups_data[g_name]
                visible_servers_ui = []
                
                for item in servers_in_group:
                    srv = item['server']
                    nodes = item['nodes']
                    
                    # 筛选符合条件的节点
                    matched_nodes = []
                    for n in nodes:
                        # 搜索匹配逻辑：匹配 节点备注 或 服务器名称
                        if (not self.search_term) or \
                           (self.search_term in n['remark'].lower()) or \
                           (self.search_term in srv['name'].lower()):
                            matched_nodes.append(n)
                            self.visible_node_keys.add(f"{srv['url']}|{n['id']}")

                    if matched_nodes:
                        visible_servers_ui.append({'server': srv, 'nodes': matched_nodes})

                # 如果该分组下有匹配的节点，才渲染该分组
                if visible_servers_ui:
                    has_match = True
                    # 默认展开，如果是搜索状态
                    expand_value = True if self.search_term else True 
                    
                    with ui.expansion(g_name, icon='folder', value=expand_value).classes('w-full border rounded mb-2').style('width: 100%;'):
                        with ui.column().classes('w-full p-0').style('display: flex; flex-direction: column; width: 100%;'):
                            for item in visible_servers_ui:
                                srv = item['server']
                                nodes = item['nodes']
                                
                                with ui.column().classes('w-full p-2 border-b').style('display: flex; flex-direction: column; align-items: flex-start; width: 100%;'):
                                    with ui.row().classes('items-center gap-2 mb-2'):
                                        ui.icon('dns', size='xs')
                                        ui.label(srv['name']).classes('font-bold')
                                    
                                    if nodes:
                                        with ui.column().classes('w-full pl-4 gap-1').style('display: flex; flex-direction: column; width: 100%;'):
                                            for n in nodes:
                                                key = f"{srv['url']}|{n['id']}"
                                                cb = ui.checkbox(n['remark'], value=(key in self.sel))
                                                cb.classes('w-full text-sm dense').style('display: flex; width: 100%;')
                                                cb.on_value_change(lambda e, k=key: self.on_check(k, e.value))
            
            if not has_match:
                ui.label('未找到匹配的节点').classes('text-center w-full mt-4 text-gray-400')

    def on_check(self, key, value):
        if value: self.sel.add(key)
        else: self.sel.discard(key)

    # ✨ 修改：全选逻辑改为只选中/取消选中“当前可见”的节点
    def toggle_all(self, select_state):
        if select_state:
            # 全选：将所有可见节点加入选中集合
            self.sel.update(self.visible_node_keys)
        else:
            # 清空：从选中集合中移除所有可见节点
            self.sel.difference_update(self.visible_node_keys)
        self.render_list()

def open_sub_editor(d):
    with ui.dialog() as dlg: SubEditor(d).ui(dlg); dlg.open()

# ================= 全能订阅编辑器 (完整修复 + 深色适配版) =================
class AdvancedSubEditor:
    def __init__(self, sub_data=None):
        import copy
        if sub_data:
            self.sub = copy.deepcopy(sub_data)
        else:
            self.sub = {'name': '', 'token': str(uuid.uuid4()), 'nodes': [], 'options': {}}
            
        if 'options' not in self.sub: self.sub['options'] = {}
        
        # 核心数据：选中的节点ID (有序)
        self.selected_ids = list(self.sub.get('nodes', []))
        
        # 缓存映射与UI引用
        self.all_nodes_map = {} 
        self.ui_groups = {}
        self.server_expansions = {}
        self.server_items = {}
        self.search_text = ""    
        self.preview_container = None
        self.left_scroll = None
        self.list_container = None

    def ui(self, dlg):
        # 1. 加载数据
        self._preload_data()

        # 2. 渲染主弹窗 (深色背景: bg-[#1e293b], 边框: border-slate-700)
        with ui.card().classes('w-full max-w-6xl h-[90vh] flex flex-col p-0 overflow-hidden bg-[#1e293b] border border-slate-700 shadow-2xl'):
            
            # --- 顶部标题栏 ---
            with ui.row().classes('w-full p-4 border-b border-slate-700 bg-[#0f172a] justify-between items-center flex-shrink-0'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('tune', color='primary').classes('text-xl')
                    ui.label('订阅高级管理').classes('text-lg font-bold text-slate-200')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense color=grey')

            # --- 内容区 (三栏布局) ---
            with ui.row().classes('w-full flex-grow overflow-hidden gap-0'):
                
                # ================= 1. 左栏：节点仓库 (bg-[#0f172a]) =================
                with ui.column().classes('w-2/5 h-full border-r border-slate-700 flex flex-col bg-[#0f172a]'):
                    # 搜索栏
                    with ui.column().classes('w-full p-2 border-b border-slate-700 bg-[#1e293b] gap-2'):
                        ui.input(placeholder='🔍 搜索源节点...', on_change=self.on_search).props('outlined dense dark debounce="300"').classes('w-full')
                        
                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label('筛选结果操作:').classes('text-xs text-slate-500')
                            with ui.row().classes('gap-1'):
                                ui.button('全选', icon='add_circle', on_click=lambda: self.batch_select(True)) \
                                    .props('unelevated dense size=sm color=blue-7').tooltip('将搜索结果加入右侧')
                                
                                ui.button('清空', icon='remove_circle', on_click=lambda: self.batch_select(False)) \
                                    .props('flat dense size=sm color=grey-6').tooltip('从右侧移除搜索结果')

                    # 滚动列表
                    with ui.scroll_area().classes('w-full flex-grow p-2') as area:
                        self.left_scroll = area
                        self.list_container = ui.column().classes('w-full gap-2')
                        # 延迟渲染以避免卡顿
                        ui.timer(0.1, lambda: asyncio.create_task(self._render_node_tree()), once=True)

                # ================= 2. 中栏：功能区 (bg-[#1e293b]) =================
                with ui.column().classes('w-1/4 h-full border-r border-slate-700 flex flex-col bg-[#1e293b] overflow-y-auto'):
                    with ui.column().classes('w-full p-4 gap-4'):
                        
                        # A. 基础设置
                        ui.label('① 基础设置').classes('text-xs font-bold text-blue-400 uppercase')
                        ui.input('订阅名称', value=self.sub.get('name', '')) \
                            .bind_value_to(self.sub, 'name') \
                            .props('outlined dense dark').classes('w-full')
                        
                        with ui.row().classes('w-full gap-1'):
                            ui.input('Token', value=self.sub.get('token', '')) \
                                .bind_value_to(self.sub, 'token') \
                                .props('outlined dense dark').classes('flex-grow')
                            
                            ui.button(icon='refresh', on_click=lambda: self.sub.update({'token': str(uuid.uuid4())[:8]})).props('flat dense color=blue')

                        ui.separator().classes('bg-slate-700')

                        # B. 排序工具
                        ui.label('② 排序工具').classes('text-xs font-bold text-blue-400 uppercase')
                        with ui.grid().classes('w-full grid-cols-2 gap-2'):
                            ui.button('名称 A-Z', on_click=lambda: self.sort_nodes('name_asc')).props('outline dense size=sm color=slate-400')
                            ui.button('名称 Z-A', on_click=lambda: self.sort_nodes('name_desc')).props('outline dense size=sm color=slate-400')
                            ui.button('随机打乱', on_click=lambda: self.sort_nodes('random')).props('outline dense size=sm color=slate-400')
                            ui.button('列表倒序', on_click=lambda: self.sort_nodes('reverse')).props('outline dense size=sm color=slate-400')

                        ui.separator().classes('bg-slate-700')

                        # C. 批量重命名
                        ui.label('③ 批量重命名').classes('text-xs font-bold text-blue-400 uppercase')
                        with ui.column().classes('w-full gap-2 bg-[#0f172a] p-2 rounded border border-slate-700'):
                            opt = self.sub.get('options', {})
                            pat = ui.input('正则 (如: ^)', value=opt.get('rename_pattern', '')).props('outlined dense dark bg-color="slate-900"').classes('w-full')
                            rep = ui.input('替换 (如: VIP-)', value=opt.get('rename_replacement', '')).props('outlined dense dark bg-color="slate-900"').classes('w-full')
                            
                            def apply_regex():
                                self.sub['options']['rename_pattern'] = pat.value
                                self.sub['options']['rename_replacement'] = rep.value
                                self.update_preview()
                                safe_notify('预览已刷新', 'positive')

                            ui.button('刷新预览', on_click=apply_regex).props('unelevated dense size=sm color=blue').classes('w-full')

                # ================= 3. 右栏：已选清单 (bg-[#0f172a]) =================
                with ui.column().classes('w-[35%] h-full bg-[#0f172a] flex flex-col'):
                    with ui.row().classes('w-full p-3 border-b border-slate-700 bg-[#1e293b] items-center justify-between shadow-sm z-10'):
                        ui.label('已选节点清单').classes('font-bold text-slate-200')
                        with ui.row().classes('items-center gap-2'):
                            ui.label('').bind_text_from(self, 'selected_ids', lambda x: f"{len(x)}").classes('text-slate-400')
                            ui.button('清空全部', icon='delete_forever', on_click=self.clear_all_selected).props('flat dense size=sm color=red')

                    with ui.scroll_area().classes('w-full flex-grow p-2'):
                        self.preview_container = ui.column().classes('w-full gap-1')
                        self.update_preview() # 初始渲染

            # --- 底部保存 ---
            with ui.row().classes('w-full p-3 border-t border-slate-700 bg-[#0f172a] justify-end gap-3 flex-shrink-0'):
                async def save_all():
                    if not self.sub.get('name'): return safe_notify('名称不能为空', 'negative')
                    self.sub['nodes'] = self.selected_ids
                    
                    found = False
                    for i, s in enumerate(SUBS_CACHE):
                        if s.get('token') == self.sub['token']:
                            SUBS_CACHE[i] = self.sub; found = True; break
                    if not found: SUBS_CACHE.append(self.sub)
                    
                    await save_subs(); await load_subs_view(); dlg.close(); safe_notify('✅ 订阅保存成功', 'positive')

                ui.button('保存配置', icon='save', on_click=save_all).classes('bg-blue-600 text-white shadow-lg')

    # ================= 辅助方法 (完全补全) =================
    
    def _preload_data(self):
        """预加载所有节点数据并建立索引"""
        self.all_nodes_map = {}
        for srv in SERVERS_CACHE:
            # 兼容：如果有缓存数据则读取，否则读取自定义节点
            nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
            for n in nodes:
                key = f"{srv['url']}|{n['id']}"
                n['_server_name'] = srv['name']
                self.all_nodes_map[key] = n

    async def _render_node_tree(self):
        """渲染左侧服务器分组树 (耗时操作，需异步)"""
        self.list_container.clear()
        self.ui_groups = {}
        self.server_expansions = {}
        self.server_items = {}
        
        # 1. 整理分组数据
        grouped = {}
        for srv in SERVERS_CACHE:
            nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
            if not nodes: continue
            
            g_name = srv.get('group', '默认分组')
            # 尝试智能分组
            if g_name in ['默认分组', '自动注册', '未分组', '自动导入']:
                try: g_name = detect_country_group(srv.get('name'), srv)
                except: pass
            
            if g_name not in grouped: grouped[g_name] = []
            grouped[g_name].append({'server': srv, 'nodes': nodes})

        # 2. 渲染 UI
        sorted_groups = sorted(grouped.keys())
        with self.list_container:
            for i, g_name in enumerate(sorted_groups):
                # 防止一次性渲染过多卡死
                if i % 2 == 0: await asyncio.sleep(0.01)
                
                # 创建分组折叠面板 (深色)
                exp = ui.expansion(g_name, icon='folder', value=True).classes('w-full border border-slate-700 rounded bg-[#1e293b]').props('header-class="bg-[#172033] text-slate-300 text-sm font-bold p-2 min-h-0"')
                
                self.server_expansions[g_name] = exp
                self.server_items[g_name] = [] 
                
                with exp:
                    with ui.column().classes('w-full p-2 gap-2'):
                        for item in grouped[g_name]:
                            srv = item['server']
                            search_key = f"{srv['name']}".lower()
                            container = ui.column().classes('w-full gap-1')
                            
                            with container:
                                # 服务器标题
                                server_header = ui.row().classes('w-full items-center gap-1 mt-1 px-1')
                                with server_header:
                                    ui.icon('dns', size='xs').classes('text-blue-400')
                                    ui.label(srv['name']).classes('text-xs font-bold text-slate-500 truncate')

                                # 节点列表
                                for n in item['nodes']:
                                    key = f"{srv['url']}|{n['id']}"
                                    is_checked = key in self.selected_ids
                                    self.server_items[g_name].append(key)
                                    
                                    # 节点行：悬浮变亮
                                    with ui.row().classes('w-full items-center pl-2 py-1 hover:bg-slate-700 rounded cursor-pointer transition border border-transparent') as row:
                                        chk = ui.checkbox(value=is_checked).props('dense size=xs dark color=blue')
                                        chk.disable() 
                                        row.on('click', lambda _, k=key: self.toggle_node_from_left(k))
                                        
                                        ui.label(n.get('remark', '未命名')).classes('text-xs text-slate-300 truncate flex-grow')
                                        
                                        full_text = f"{search_key} {n.get('remark','')} {n.get('protocol','')}".lower()
                                        
                                        # 存储 UI 引用以便控制显隐
                                        self.ui_groups[key] = {
                                            'row': row, 'chk': chk, 'text': full_text, 
                                            'group_name': g_name, 'header': server_header,
                                            'container': container
                                        }

    def toggle_node_from_left(self, key):
        if key in self.selected_ids:
            self.remove_node(key) 
        else:
            self.selected_ids.append(key)
            self.update_preview()
            if key in self.ui_groups:
                self.ui_groups[key]['chk'].value = True
                self.ui_groups[key]['row'].classes(add='bg-blue-900/30 border-blue-500/30', remove='border-transparent')

    def remove_node(self, key):
        if key in self.selected_ids:
            self.selected_ids.remove(key)
            self.update_preview()
            if key in self.ui_groups:
                self.ui_groups[key]['chk'].value = False
                self.ui_groups[key]['row'].classes(remove='bg-blue-900/30 border-blue-500/30', add='border-transparent')

    def clear_all_selected(self):
        for key in list(self.selected_ids):
            self.remove_node(key)

    def update_preview(self):
        """刷新右侧已选列表"""
        self.preview_container.clear()
        pat = self.sub.get('options', {}).get('rename_pattern', '')
        rep = self.sub.get('options', {}).get('rename_replacement', '')
        
        with self.preview_container:
            if not self.selected_ids:
                with ui.column().classes('w-full items-center mt-10 text-slate-600 gap-2'):
                    ui.icon('shopping_cart', size='3rem')
                    ui.label('清单为空').classes('text-sm')
                return

            with ui.column().classes('w-full gap-1'):
                for idx, key in enumerate(self.selected_ids):
                    node = self.all_nodes_map.get(key)
                    if not node: continue
                    
                    original_name = node.get('remark', 'Unknown')
                    final_name = original_name
                    if pat:
                        try:
                            import re
                            final_name = re.sub(pat, rep, original_name)
                        except: pass
                    
                    # 预览行 (深色)
                    with ui.row().classes('w-full items-center p-1.5 bg-[#1e293b] border border-slate-700 rounded shadow-sm group hover:border-red-500 transition'):
                        ui.label(str(idx+1)).classes('text-[10px] text-slate-500 w-5 text-center')
                        chk = ui.checkbox(value=True).props('dense size=xs color=green dark')
                        chk.on_value_change(lambda e, k=key: self.remove_node(k) if not e.value else None)
                        
                        with ui.column().classes('gap-0 leading-none flex-grow ml-1'):
                            if final_name != original_name:
                                ui.label(final_name).classes('text-xs font-bold text-blue-400')
                                ui.label(original_name).classes('text-[9px] text-slate-500 line-through')
                            else:
                                ui.label(final_name).classes('text-xs font-bold text-slate-300')
                        
                        ui.button(icon='close', on_click=lambda _, k=key: self.remove_node(k)).props('flat dense size=xs color=red').classes('opacity-0 group-hover:opacity-100 transition')

    def sort_nodes(self, mode):
        if not self.selected_ids: return safe_notify('列表为空', 'warning')
        objs = []
        for k in self.selected_ids:
            n = self.all_nodes_map.get(k)
            if n: objs.append({'key': k, 'name': n.get('remark', '').lower()})
        
        if mode == 'name_asc': objs.sort(key=lambda x: x['name'])
        elif mode == 'name_desc': objs.sort(key=lambda x: x['name'], reverse=True)
        elif mode == 'random': import random; random.shuffle(objs)
        elif mode == 'reverse': objs.reverse()
        
        self.selected_ids = [x['key'] for x in objs]
        self.update_preview()
        safe_notify(f'已按 {mode} 重新排序', 'positive')

    def on_search(self, e):
        """左侧列表搜索过滤逻辑"""
        txt = str(e.value).lower().strip()
        
        visible_groups = set()
        visible_headers = set()
        
        # 1. 过滤具体节点行
        for key, item in self.ui_groups.items():
            visible = (not txt) or (txt in item['text'])
            item['row'].set_visibility(visible)
            if visible:
                visible_groups.add(item['group_name'])
                visible_headers.add(item['header'])
        
        # 2. 控制分组折叠面板
        for g_name, exp in self.server_expansions.items():
            is_group_visible = g_name in visible_groups
            exp.set_visibility(is_group_visible)
            if txt and is_group_visible:
                exp.value = True # 搜索时自动展开
        
        # 3. 控制服务器标题头
        all_headers = set(item['header'] for item in self.ui_groups.values())
        for header in all_headers:
            header.set_visibility(header in visible_headers)

    def batch_select(self, val):
        """批量全选/清空当前搜索结果"""
        count = 0
        for key, item in self.ui_groups.items():
            # 只操作当前可见的项
            if item['row'].visible:
                if val:
                    if key not in self.selected_ids:
                        self.selected_ids.append(key)
                        item['chk'].value = True
                        item['row'].classes(add='bg-blue-900/30 border-blue-500/30', remove='border-transparent')
                        count += 1
                else:
                    if key in self.selected_ids:
                        self.selected_ids.remove(key)
                        item['chk'].value = False
                        item['row'].classes(remove='bg-blue-900/30 border-blue-500/30', add='border-transparent')
                        count += 1
        
        if count > 0:
            self.update_preview()
            safe_notify(f"已{'添加' if val else '移除'} {count} 个节点", "positive")
        else:
            safe_notify("当前没有可操作的节点", "warning")
        
# 弹窗入口
def open_advanced_sub_editor(sub_data=None):
    with ui.dialog() as d: AdvancedSubEditor(sub_data).ui(d); d.open()
    
# ================= 全局变量 =================
# 用于记录当前探针页面选中的标签，防止刷新重置
CURRENT_PROBE_TAB = 'ALL' 

# ================= 快捷创建分组弹窗 (深色适配版) =================
def open_quick_group_create_dialog(callback=None):
    selection_map = {s['url']: False for s in SERVERS_CACHE}
    ui_rows = {} 

    # 弹窗背景: bg-[#1e293b], 边框: border-slate-700
    with ui.dialog() as d, ui.card().classes('w-full max-w-lg h-[85vh] flex flex-col p-0 bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # 1. 顶部区域
        with ui.column().classes('w-full p-4 border-b border-slate-700 bg-[#0f172a] gap-3 flex-shrink-0'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('新建分组 (标签模式)').classes('text-lg font-bold text-slate-200')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
            
            # 输入框
            name_input = ui.input('分组名称', placeholder='例如: 甲骨文云').props('outlined dense dark').classes('w-full')
            search_input = ui.input(placeholder='🔍 搜索筛选服务器...').props('outlined dense clearable dark').classes('w-full')
            
            def on_search(e):
                keyword = str(e.value).lower().strip()
                for url, item in ui_rows.items():
                    is_match = keyword in item['search_text']
                    item['row'].set_visibility(is_match)
            search_input.on_value_change(on_search)

        # 2. 中间：列表
        with ui.column().classes('w-full flex-grow overflow-hidden relative bg-[#1e293b]'):
            # 工具栏
            with ui.row().classes('w-full p-2 bg-[#172033] justify-between items-center border-b border-slate-700 flex-shrink-0'):
                ui.label('勾选加入该组:').classes('text-xs font-bold text-slate-400 ml-2')
                with ui.row().classes('gap-1'):
                    ui.button('全选', on_click=lambda: toggle_visible(True)).props('flat dense size=xs color=blue')
                    ui.button('清空', on_click=lambda: toggle_visible(False)).props('flat dense size=xs color=grey')

            with ui.scroll_area().classes('w-full flex-grow p-2'):
                with ui.column().classes('w-full gap-1'):
                    try: sorted_srv = sorted(SERVERS_CACHE, key=lambda x: str(x.get('name', '')))
                    except: sorted_srv = SERVERS_CACHE
                    
                    for s in sorted_srv:
                        search_key = f"{s['name']} {s['url']}".lower()
                        
                        # 列表项：悬浮变亮
                        with ui.row().classes('w-full items-center p-2 hover:bg-slate-700 rounded border border-transparent transition cursor-pointer group') as row:
                            chk = ui.checkbox(value=False).props('dense dark color=blue')
                            chk.on('click.stop', lambda: None)
                            chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                            row.on('click', lambda _, c=chk: c.set_value(not c.value))

                            ui.label(s['name']).classes('text-sm font-bold text-slate-300 ml-2 truncate flex-grow select-none group-hover:text-white')
                            
                            detected = "未知"
                            try: detected = detect_country_group(s['name'], s)
                            except: pass
                            ui.label(detected).classes('text-xs text-slate-500 font-mono')
                        
                        ui_rows[s['url']] = {'row': row, 'chk': chk, 'search_text': search_key}

            def toggle_visible(state):
                count = 0
                for item in ui_rows.values():
                    if item['row'].visible:
                        item['chk'].value = state; count += 1
                if state and count > 0: safe_notify(f"已选中 {count} 个", "positive")

        # 3. 底部
        async def save():
            new_name = name_input.value.strip()
            if not new_name: return safe_notify('名称不能为空', 'warning')
            existing = set(ADMIN_CONFIG.get('custom_groups', []))
            if new_name in existing: return safe_notify('分组已存在', 'warning')
            if 'custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['custom_groups'] = []
            ADMIN_CONFIG['custom_groups'].append(new_name)
            await save_admin_config()
            
            count = 0
            for s in SERVERS_CACHE:
                if selection_map.get(s['url'], False):
                    if 'tags' not in s: s['tags'] = []
                    if new_name not in s['tags']: s['tags'].append(new_name); count += 1
                    if s.get('group') == new_name: s['group'] = detect_country_group(s['name'], None)

            if count > 0: await save_servers()
            render_sidebar_content.refresh()
            safe_notify(f'✅ 分组 "{new_name}" 创建成功', 'positive')
            d.close()
            if callback: await callback(new_name)

        with ui.row().classes('w-full p-4 border-t border-slate-700 bg-[#0f172a] justify-end gap-2 flex-shrink-0'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('创建并保存', on_click=save).classes('bg-blue-600 text-white shadow-md')
    d.open()

# ================= 1.探针视图(分组)排序弹窗 (深色适配版) =================
def open_group_sort_dialog():
    # 读取当前分组
    current_groups = ADMIN_CONFIG.get('probe_custom_groups', [])
    if not current_groups:
        safe_notify("暂无自定义视图", "warning")
        return

    # 临时列表用于编辑
    temp_list = list(current_groups)

    # 弹窗容器：深色背景 + 边框
    with ui.dialog() as d, ui.card().classes('w-[400px] max-w-[95vw] h-[60vh] flex flex-col p-0 gap-0 bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # --- 顶部标题 (bg-[#0f172a]) ---
        with ui.row().classes('w-full p-4 border-b border-slate-700 justify-between items-center bg-[#0f172a] flex-shrink-0'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('sort', color='blue').classes('text-lg')
                ui.label('视图排序').classes('font-bold text-slate-200 text-base')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
        
        # --- 列表容器 (bg-[#1e293b]) ---
        with ui.scroll_area().classes('w-full flex-grow p-3'):
            list_container = ui.column().classes('w-full gap-2')

        def render_list():
            list_container.clear()
            with list_container:
                for i, name in enumerate(temp_list):
                    # 列表项：深色卡片风格
                    with ui.row().classes('w-full p-3 items-center gap-3 border border-slate-700 rounded-lg bg-[#0f172a] shadow-sm transition-all hover:border-blue-500'):
                        # 序号
                        ui.label(str(i+1)).classes('text-xs text-slate-500 font-mono w-4 text-center font-bold')
                        
                        # 组名
                        ui.label(name).classes('font-bold text-slate-300 flex-grow text-sm truncate')
                        
                        # 移动按钮
                        with ui.row().classes('gap-1'):
                            # 上移
                            if i > 0:
                                ui.button(icon='arrow_upward', on_click=lambda _, idx=i: move_item(idx, -1)) \
                                    .props('flat dense round size=xs color=blue-4').classes('hover:bg-slate-700')
                            else:
                                ui.element('div').classes('w-6') # 占位
                            
                            # 下移
                            if i < len(temp_list) - 1:
                                ui.button(icon='arrow_downward', on_click=lambda _, idx=i: move_item(idx, 1)) \
                                    .props('flat dense round size=xs color=blue-4').classes('hover:bg-slate-700')
                            else:
                                ui.element('div').classes('w-6')

        def move_item(index, direction):
            target = index + direction
            if 0 <= target < len(temp_list):
                temp_list[index], temp_list[target] = temp_list[target], temp_list[index]
                render_list()

        render_list()

        # --- 底部保存 (bg-[#0f172a]) ---
        async def save():
            ADMIN_CONFIG['probe_custom_groups'] = temp_list
            await save_admin_config()
            safe_notify("✅ 分组顺序已更新", "positive")
            d.close()
            # 尝试刷新探针设置页面的视图列表
            try: await render_probe_page()
            except: pass

        with ui.row().classes('w-full p-4 border-t border-slate-700 bg-[#0f172a] flex-shrink-0'):
            ui.button('保存顺序', icon='save', on_click=save).classes('w-full bg-blue-600 text-white shadow-lg hover:bg-blue-500')
    
    d.open()
import traceback # 引入用于打印报错堆栈

# ================= 探针自定义分组一体化管理器 (深色适配版) =================
def open_unified_group_manager(mode='manage'):
    if 'probe_custom_groups' not in ADMIN_CONFIG: 
        ADMIN_CONFIG['probe_custom_groups'] = []
    
    state = {
        'current_group': None,
        'selected_urls': set(),
        'checkboxes': {},
        'page': 1,
        'search_text': ''
    }

    view_list_container = None
    server_list_container = None
    title_input = None
    pagination_ref = None 

    # 弹窗容器: bg-[#1e293b] border-slate-700
    with ui.dialog() as d, ui.card().classes('w-full max-w-5xl h-[90vh] flex flex-col p-0 gap-0 bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # --- 1. 顶部：视图切换区 (bg-[#0f172a]) ---
        with ui.row().classes('w-full p-3 bg-[#0f172a] border-b border-slate-700 items-center gap-2 overflow-x-auto flex-shrink-0'):
            ui.label('视图列表:').classes('font-bold text-slate-400 mr-2 text-xs')
            ui.button('➕ 新建分组', on_click=lambda: load_group_data(None)).props('unelevated color=green text-color=white size=sm')
            ui.separator().props('vertical dark').classes('mx-2 h-6')
            view_list_container = ui.row().classes('gap-2 items-center flex-nowrap')
            ui.space()
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        # --- 2. 编辑区头部 (bg-[#1e293b]) ---
        with ui.row().classes('w-full p-4 bg-[#1e293b] border-b border-slate-700 items-center gap-4 flex-shrink-0 wrap'):
            title_input = ui.input('视图名称', placeholder='请输入分组名称...').props('outlined dense dark').classes('min-w-[200px] flex-grow font-bold')
            
            ui.input(placeholder='🔍 搜索服务器...', on_change=lambda e: update_search(e.value)).props('outlined dense dense dark').classes('w-48')

            with ui.row().classes('gap-2'):
                ui.button('全选本页', on_click=lambda: toggle_page_all(True)).props('flat dense size=sm color=blue')
                ui.button('清空本页', on_click=lambda: toggle_page_all(False)).props('flat dense size=sm color=grey')

        # --- 3. 服务器列表 (bg-[#0f172a]) ---
        with ui.scroll_area().classes('w-full flex-grow p-4 bg-[#0f172a]'):
            server_list_container = ui.column().classes('w-full gap-2')
            
        # --- 3.5 分页 (bg-[#1e293b]) ---
        with ui.row().classes('w-full p-2 justify-center bg-[#1e293b] border-t border-slate-700'):
            pagination_ref = ui.row() 

        # --- 4. 底部保存 (bg-[#0f172a]) ---
        with ui.row().classes('w-full p-4 bg-[#0f172a] border-t border-slate-700 justify-between items-center flex-shrink-0'):
            ui.button('删除此视图', icon='delete', color='red', on_click=lambda: delete_current_group()).props('flat')
            ui.button('保存当前配置', icon='save', on_click=lambda: save_current_group()).classes('bg-blue-600 text-white shadow-lg')

    # ================= 逻辑定义 =================

    def update_search(val):
        state['search_text'] = str(val).lower().strip()
        state['page'] = 1 
        render_servers()

    def render_views():
        view_list_container.clear()
        groups = ADMIN_CONFIG.get('probe_custom_groups', [])
        with view_list_container:
            for g in groups:
                is_active = (g == state['current_group'])
                # 按钮样式深色适配
                btn_props = 'unelevated color=blue' if is_active else 'outline color=grey-5 text-color=grey-4'
                ui.button(g, on_click=lambda _, name=g: load_group_data(name)).props(f'{btn_props} size=sm')

    def load_group_data(group_name):
        state['current_group'] = group_name
        state['page'] = 1
        state['selected_urls'] = set() 
        
        if group_name:
            for s in SERVERS_CACHE:
                if (group_name in s.get('tags', [])) or (s.get('group') == group_name):
                    state['selected_urls'].add(s['url'])
                    
        render_views()
        title_input.value = group_name if group_name else ''
        if not group_name: title_input.run_method('focus')
        render_servers()

    def render_servers():
        server_list_container.clear()
        pagination_ref.clear()
        state['checkboxes'] = {} 
        
        if not SERVERS_CACHE:
            with server_list_container: ui.label('暂无服务器').classes('text-center text-slate-500 mt-10 w-full')
            return

        all_srv = SERVERS_CACHE
        if state['search_text']:
            all_srv = [s for s in all_srv if state['search_text'] in s.get('name', '').lower() or state['search_text'] in s.get('url', '').lower()]
        
        try: sorted_servers = sorted(all_srv, key=lambda x: str(x.get('name', '')))
        except: sorted_servers = all_srv

        PAGE_SIZE = 48 
        total_items = len(sorted_servers)
        total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
        if state['page'] > total_pages: state['page'] = 1
        if state['page'] < 1: state['page'] = 1
        
        start_idx = (state['page'] - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_items = sorted_servers[start_idx:end_idx]

        with server_list_container:
            ui.label(f"共 {total_items} 台 (第 {state['page']}/{total_pages} 页)").classes('text-xs text-slate-400 mb-2')

            with ui.grid().classes('w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2'):
                for s in current_page_items:
                    url = s.get('url')
                    if not url: continue
                    is_checked = url in state['selected_urls']
                    
                    # 列表项背景：选中为深蓝，未选中为深灰
                    bg_cls = 'bg-blue-900/30 border-blue-500/50' if is_checked else 'bg-[#172033] border-slate-700'
                    
                    with ui.row().classes(f'items-center p-2 border rounded cursor-pointer hover:bg-slate-700 transition {bg_cls}') as row:
                        chk = ui.checkbox(value=is_checked).props('dense dark color=green')
                        state['checkboxes'][url] = chk
                        
                        def toggle_row(c=chk, r=row, u=url): 
                            c.value = not c.value
                            update_selection(u, c.value)
                            if c.value: r.classes(add='bg-blue-900/30 border-blue-500/50', remove='bg-[#172033] border-slate-700')
                            else: r.classes(remove='bg-blue-900/30 border-blue-500/50', add='bg-[#172033] border-slate-700')

                        row.on('click', toggle_row)
                        chk.on('click.stop', lambda _, c=chk, r=row, u=url: [update_selection(u, c.value), 
                            r.classes(add='bg-blue-900/30 border-blue-500/50', remove='bg-[#172033] border-slate-700') if c.value else r.classes(remove='bg-blue-900/30 border-blue-500/50', add='bg-[#172033] border-slate-700')])

                        with ui.column().classes('gap-0 ml-2 overflow-hidden'):
                            ui.label(s.get('name', 'Unknown')).classes('text-sm font-bold truncate text-slate-200')
                            if is_checked: ui.label('已选中').classes('text-[10px] text-green-400 font-bold')
                            else: ui.label(s.get('group','')).classes('text-[10px] text-slate-500')

        if total_pages > 1:
            with pagination_ref:
                p = ui.pagination(1, total_pages, direction_links=True).props('dense color=blue text-color=slate-400 active-text-color=white')
                p.value = state['page']
                p.on('update:model-value', lambda e: [state.update({'page': e.args}), render_servers()])

    def update_selection(url, checked):
        if checked: state['selected_urls'].add(url)
        else: state['selected_urls'].discard(url)

    def toggle_page_all(val):
        for url in state['checkboxes'].keys():
            if val: state['selected_urls'].add(url)
            else: state['selected_urls'].discard(url)
        render_servers() 

    async def save_current_group():
        old_name = state['current_group']
        new_name = title_input.value.strip()
        if not new_name: return safe_notify("名称不能为空", "warning")

        groups = ADMIN_CONFIG.get('probe_custom_groups', [])
        
        if not old_name: 
            if new_name in groups: return safe_notify("名称已存在", "negative")
            groups.append(new_name)
        elif new_name != old_name:
            if new_name in groups: return safe_notify("名称已存在", "negative")
            idx = groups.index(old_name)
            groups[idx] = new_name
            for s in SERVERS_CACHE:
                if 'tags' in s and old_name in s['tags']:
                    s['tags'].remove(old_name)
                    s['tags'].append(new_name)

        for s in SERVERS_CACHE:
            if 'tags' not in s: s['tags'] = []
            if s['url'] in state['selected_urls']:
                if new_name not in s['tags']: s['tags'].append(new_name)
            else:
                if new_name in s['tags']: s['tags'].remove(new_name)

        ADMIN_CONFIG['probe_custom_groups'] = groups
        await save_admin_config(); await save_servers()
        safe_notify(f"✅ 保存成功", "positive")
        load_group_data(new_name)
        try: await render_probe_page()
        except: pass

    async def delete_current_group():
        target = state['current_group']
        if not target: return
        if target in ADMIN_CONFIG.get('probe_custom_groups', []):
            ADMIN_CONFIG['probe_custom_groups'].remove(target)
            await save_admin_config()
        for s in SERVERS_CACHE:
            if 'tags' in s and target in s['tags']: s['tags'].remove(target)
        await save_servers()
        safe_notify("🗑️ 已删除", "positive")
        load_group_data(None)
        try: await render_probe_page()
        except: pass

    def init():
        render_views()
        load_group_data(None)
    
    ui.timer(0.1, init, once=True)
    d.open()


# ================= 探针设置页 (修复版) =================
async def render_probe_page():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'PROBE'
    content_container.clear()
    # 背景色统一为 Slate-900
    content_container.classes(replace='w-full h-full overflow-y-auto p-6 bg-[#0f172a] relative flex flex-col justify-center items-center')
    
    if not ADMIN_CONFIG.get('probe_enabled'):
        ADMIN_CONFIG['probe_enabled'] = True; await save_admin_config()

    with content_container:
        with ui.column().classes('w-full max-w-7xl gap-6'):
            
            # --- 标题 ---
            with ui.row().classes('w-full items-center gap-3'):
                 with ui.element('div').classes('p-2 bg-blue-600 rounded-lg shadow-lg shadow-blue-900/50'):
                     ui.icon('tune', color='white').classes('text-2xl')
                 with ui.column().classes('gap-0'):
                    ui.label('探针管理与设置').classes('text-2xl font-extrabold text-slate-100 tracking-tight')
                    ui.label('Configuration & Management').classes('text-xs font-bold text-slate-500 uppercase tracking-widest')

            # --- 布局 (左右分栏) ---
            with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-7 gap-6 items-stretch'):
                
                # ======================= 左侧：参数设置区 (4/7) =======================
                with ui.column().classes('lg:col-span-4 w-full gap-6'):
                    
                    # 通用卡片样式
                    card_style = 'w-full p-6 bg-[#1e293b] border border-slate-700 shadow-xl rounded-xl'
                    
                    # 1. 基础连接设置
                    with ui.card().classes(card_style):
                        with ui.row().classes('items-center gap-2 mb-4 border-b border-slate-700 pb-2 w-full'):
                            ui.icon('hub', color='blue').classes('text-xl')
                            ui.label('基础连接设置').classes('text-lg font-bold text-slate-200')
                        
                        with ui.column().classes('w-full gap-2'):
                            ui.label('📡 主控端地址 (Agent连接用)').classes('text-sm font-bold text-slate-400')
                            url_input = ui.input(value=ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')).props('outlined dense dark').classes('w-full')
                            ui.label('请填写公网 IP 或域名，带端口').classes('text-xs text-slate-500')

                        async def save_url():
                            ADMIN_CONFIG['manager_base_url'] = url_input.value.strip().rstrip('/')
                            await save_admin_config(); safe_notify('已保存', 'positive')
                        
                        # 🔥 修复：使用 with 替代 .add()
                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存', icon='save', on_click=save_url).props('unelevated color=blue-7')

                    # 2. 测速目标设置
                    with ui.card().classes(card_style):
                        with ui.row().classes('items-center gap-2 mb-4 border-b border-slate-700 pb-2 w-full'):
                            ui.icon('speed', color='orange').classes('text-xl')
                            ui.label('三网延迟测速目标').classes('text-lg font-bold text-slate-200')
                        
                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-3 gap-4'):
                            ping_ct = ui.input('电信 IP', value=ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')).props('outlined dense dark')
                            ping_cu = ui.input('联通 IP', value=ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')).props('outlined dense dark')
                            ping_cm = ui.input('移动 IP', value=ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')).props('outlined dense dark')
                        
                        async def save_ping():
                            ADMIN_CONFIG['ping_target_ct'] = ping_ct.value; ADMIN_CONFIG['ping_target_cu'] = ping_cu.value; ADMIN_CONFIG['ping_target_cm'] = ping_cm.value
                            await save_admin_config(); safe_notify('已保存 (需更新探针生效)', 'positive')
                        
                        # 🔥 修复：使用 with 替代 .add()
                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存', icon='save', on_click=save_ping).props('unelevated color=orange-7')

                    # 3. 通知设置
                    with ui.card().classes(card_style):
                        with ui.row().classes('items-center gap-2 mb-4 border-b border-slate-700 pb-2 w-full'):
                            ui.icon('notifications', color='purple').classes('text-xl')
                            ui.label('Telegram 通知').classes('text-lg font-bold text-slate-200')
                        
                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-4'):
                            tg_token = ui.input('Bot Token', value=ADMIN_CONFIG.get('tg_bot_token', '')).props('outlined dense dark')
                            tg_id = ui.input('Chat ID', value=ADMIN_CONFIG.get('tg_chat_id', '')).props('outlined dense dark')

                        async def save_tg():
                            ADMIN_CONFIG['tg_bot_token'] = tg_token.value; ADMIN_CONFIG['tg_chat_id'] = tg_id.value
                            await save_admin_config(); safe_notify('已保存', 'positive')
                        
                        # 🔥 修复：使用 with 替代 .add()
                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存', icon='save', on_click=save_tg).props('unelevated color=purple-7')

                # ======================= 右侧：快捷操作区 (3/7) =======================
                # 之前因为报错，这部分代码没执行到，所以你会觉得"少了很多功能"
                with ui.column().classes('lg:col-span-3 w-full gap-6 h-full'):
                    
                    # 卡片 A: 快捷操作
                    with ui.card().classes(card_style + ' flex-shrink-0'):
                        ui.label('快捷操作').classes('text-lg font-bold text-slate-200 mb-4 border-l-4 border-blue-500 pl-2')
                        with ui.column().classes('w-full gap-3'):
                            # 复制命令
                            async def copy_cmd():
                                try: origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
                                except: safe_notify("获取地址失败", "negative"); return
                                
                                token = ADMIN_CONFIG.get('probe_token', 'default_token')
                                mgr_url = ADMIN_CONFIG.get('manager_base_url', origin).strip().rstrip('/')
                                reg_url = f"{mgr_url}/api/probe/register"
                                
                                ct = ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')
                                cu = ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')
                                cm = ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')
                                
                                cmd = f'curl -sL https://raw.githubusercontent.com/SIJULY/x-fusion-panel/main/static/x-install.sh | bash -s -- "{token}" "{reg_url}" "{ct}" "{cu}" "{cm}"'
                                await safe_copy_to_clipboard(cmd); safe_notify("已复制安装命令", "positive")
                            
                            ui.button('复制安装命令', icon='content_copy', on_click=copy_cmd) \
                                .classes('w-full bg-[#172033] border border-slate-600 text-blue-400 shadow-sm hover:bg-[#334155] font-bold align-left')
                            
                            # 按钮组
                            with ui.row().classes('w-full gap-2'):
                                ui.button('分组管理', icon='settings', on_click=lambda: open_unified_group_manager('manage')) \
                                    .classes('flex-1 bg-[#172033] border border-slate-600 text-slate-300 hover:bg-[#334155]')
                                ui.button('排序视图', icon='sort', on_click=open_group_sort_dialog) \
                                    .classes('flex-1 bg-[#172033] border border-slate-600 text-slate-300 hover:bg-[#334155]')
                            
                            # 更新探针
                            ui.button('更新所有探针', icon='system_update_alt', on_click=batch_install_all_probes) \
                                .classes('w-full bg-orange-900/30 text-orange-400 border border-orange-800 hover:bg-orange-900/50 font-bold align-left')

                    # 卡片 B: 监控墙入口
                    with ui.card().classes('w-full p-6 bg-gradient-to-br from-indigo-900 to-slate-900 text-white rounded-xl shadow-lg relative overflow-hidden group cursor-pointer flex-grow flex flex-col justify-center border border-indigo-500/30') \
                        .on('click', lambda: ui.navigate.to('/status', new_tab=True)):
                        ui.icon('public', size='10rem').classes('absolute -right-8 -bottom-8 text-white opacity-5 group-hover:rotate-12 transition transform duration-500')
                        ui.label('公开监控墙').classes('text-2xl font-bold mb-2')
                        ui.label('点击前往查看实时状态').classes('text-sm text-indigo-200 mb-6')
                        with ui.row().classes('items-center gap-2 text-blue-300 font-bold'):
                            ui.label('立即前往'); ui.icon('arrow_forward')

                    # 卡片 C: 数据概览
                    online_count = len([s for s in SERVERS_CACHE if s.get('_status') == 'online'])
                    probe_count = len([s for s in SERVERS_CACHE if s.get('probe_installed')])
                    
                    with ui.card().classes(card_style + ' flex-shrink-0'):
                        ui.label('数据概览').classes('text-lg font-bold text-slate-200 mb-4 border-l-4 border-green-500 pl-2')
                        def stat_row(label, val, color):
                            with ui.row().classes('w-full justify-between items-center border-b border-slate-700 pb-3 mb-3 last:border-0 last:mb-0'):
                                ui.label(label).classes('text-slate-500 text-sm')
                                ui.label(str(val)).classes(f'font-bold text-xl {color}')
                        stat_row('总服务器', len(SERVERS_CACHE), 'text-slate-200')
                        stat_row('当前在线', online_count, 'text-green-400')
                        stat_row('已装探针', probe_count, 'text-purple-400')
    
# ================= 订阅管理视图 (全自动自适应终极修复版) ================
async def load_subs_view():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'SUBS'
    CURRENT_VIEW_STATE['data'] = None
    
    show_loading(content_container)
    
    # ✨✨✨ 终极域名获取逻辑：三层防线，绝不输出乱码 ✨✨✨
    origin = ""
    
    # [防线1] 优先读取数据库配置
    db_url = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
    if db_url and not ('127.0.0.1' in db_url or 'localhost' in db_url):
        origin = db_url

    # [防线2] 数据库没有？问浏览器拿最真实的地址 (JS)
    if not origin:
        try: 
            origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
        except: 
            pass # JS 超时也不怕，进入防线3

    # [防线3] JS 超时了？直接在 Python 后端扒 Nginx/Cloudflare 的请求头
    if not origin or origin == 'null':
        try:
            req = ui.context.client.request
            real_host = req.headers.get('X-Forwarded-Host') or req.headers.get('host')
            real_proto = req.headers.get('X-Forwarded-Proto') or req.url.scheme
            if real_host:
                origin = f"{real_proto}://{real_host}"
        except:
            pass
            
    # 绝对保底 (如果前三层全军覆没，使用标准 URL 格式防止崩溃)
    if not origin: origin = "http://x-fusion-panel" 

    # 将获取到的正确域名顺手存入数据库，下次秒开
    if origin and "x-fusion-panel" not in origin:
        if ADMIN_CONFIG.get('manager_base_url') != origin:
            ADMIN_CONFIG['manager_base_url'] = origin
            asyncio.create_task(save_admin_config())

    content_container.clear()
    
    # 容器背景：Slate-900 (#0f172a)
    content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='h-full overflow-y-auto p-4 pl-6 justify-start bg-[#0f172a]')
    
    # === 预统计有效节点 ===
    all_active_keys = set()
    for srv in SERVERS_CACHE:
        panel = NODES_DATA.get(srv['url'], []) or []
        custom = srv.get('custom_nodes', []) or []
        for n in (panel + custom):
            key = f"{srv['url']}|{n['id']}"
            all_active_keys.add(key)

    with content_container:
        # 标题栏
        with ui.row().classes('w-full mb-4 justify-between items-center border-b border-slate-700 pb-2'): 
            ui.label('订阅管理').classes('text-2xl font-bold text-slate-200')
            ui.button('新建订阅', icon='add', color='green', on_click=lambda: open_advanced_sub_editor(None)).props('unelevated')
        
        if not SUBS_CACHE:
            with ui.column().classes('w-full h-64 justify-center items-center text-slate-600'): 
                ui.icon('rss_feed', size='4rem'); ui.label('暂无订阅').classes('text-sm')

        for idx, sub in enumerate(SUBS_CACHE):
            # 卡片背景：Slate-800 (#1e293b)
            with ui.card().classes('w-full p-4 mb-3 shadow-lg hover:shadow-xl transition border border-slate-700 border-l-4 border-l-blue-500 rounded-lg bg-[#1e293b]'):
                
                # 顶部信息栏
                with ui.row().classes('justify-between w-full items-start'):
                    with ui.column().classes('gap-1'):
                        with ui.row().classes('items-center gap-2'):
                            ui.label(sub.get('name', '未命名订阅')).classes('font-bold text-lg text-slate-200')
                            ui.badge('普通', color='blue').props('outline size=xs') 
                        
                        # 有效性统计
                        saved_node_ids = set(sub.get('nodes', []))
                        valid_count = len(saved_node_ids.intersection(all_active_keys))
                        total_count = len(saved_node_ids)
                        
                        color_cls = 'text-green-400' if valid_count > 0 else 'text-slate-500'
                        ui.label(f"⚡ 包含节点: {valid_count} (有效) / {total_count} (总计)").classes(f'text-xs font-bold {color_cls} font-mono')
                    
                    # 操作按钮区
                    with ui.row().classes('gap-2'):
                        ui.button('管理订阅', icon='tune', on_click=lambda _, s=sub: open_advanced_sub_editor(s)) \
                            .props('unelevated dense size=sm color=blue-7') \
                            .tooltip('重命名 / 排序 / 筛选节点')
                        
                        async def dl(i=idx): 
                            with ui.dialog() as d, ui.card().classes('bg-[#1e293b] border border-slate-700'):
                                ui.label('确定删除此订阅？').classes('font-bold text-red-500')
                                with ui.row().classes('justify-end w-full mt-4'):
                                    ui.button('取消', on_click=d.close).props('flat color=grey')
                                    async def confirm():
                                        del SUBS_CACHE[i]; await save_subs(); await load_subs_view(); d.close()
                                        safe_notify('已删除', 'positive')
                                    ui.button('删除', color='red', on_click=confirm).props('unelevated')
                            d.open()

                        ui.button(icon='delete', color='red', on_click=dl).props('flat dense size=sm')
                        
                ui.separator().classes('my-3 bg-slate-600 opacity-50')
                
                # 链接显示区
                path = f"/sub/{sub['token']}"
                raw_url = f"{origin}{path}"
                
                # bg-[#0b1121] (纯黑背景) + text-green-400
                with ui.row().classes('w-full items-center gap-2 bg-[#0b1121] p-2.5 rounded-lg justify-between border border-slate-700'):
                    with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                        ui.icon('link').classes('text-blue-500 text-sm')
                        # 链接文字：绿色高亮，字体加粗
                        ui.label(raw_url).classes('text-xs font-mono text-green-400 font-bold truncate select-all')
                    
                    with ui.row().classes('gap-1'):
                        def btn_copy(icon, color, text, func):
                            ui.button(icon=icon, on_click=func).props(f'flat dense round size=xs text-color={color}').tooltip(text).classes('hover:bg-slate-800')

                        btn_copy('content_copy', 'grey-4', '复制原始链接', lambda u=raw_url: safe_copy_to_clipboard(u))
                        
                        surge_short = f"{origin}/get/sub/surge/{sub['token']}"
                        btn_copy('bolt', 'orange', '复制 Surge 订阅', lambda u=surge_short: safe_copy_to_clipboard(u))
                        
                        clash_short = f"{origin}/get/sub/clash/{sub['token']}"
                        btn_copy('cloud_queue', 'green', '复制 Clash 订阅', lambda u=clash_short: safe_copy_to_clipboard(u))
                        
# ================= 通用服务器保存函数 (UI 操控版：修改后强制刷新 + 重置冷却) =================
async def save_server_config(server_data, is_add=True, idx=None):
    # 1. 基础校验
    if not server_data.get('name') or not server_data.get('url'):
        safe_notify("名称和地址不能为空", "negative"); return False

    # 记录旧信息 (用于判断是否移动了分组)
    old_group = None
    if not is_add and idx is not None and 0 <= idx < len(SERVERS_CACHE):
        old_group = SERVERS_CACHE[idx].get('group')

    # 2. 逻辑处理
    if is_add:
        for s in SERVERS_CACHE:
            if s['url'] == server_data['url']: safe_notify(f"已存在！", "warning"); return False
        
        # 自动补全白旗 (如果没国旗的话)
        has_flag = False
        for v in AUTO_COUNTRY_MAP.values():
            if v.split(' ')[0] in server_data['name']: has_flag = True; break
        if not has_flag and '🏳️' not in server_data['name']: server_data['name'] = f"🏳️ {server_data['name']}"

        SERVERS_CACHE.append(server_data)
        safe_notify(f"已添加: {server_data['name']}", "positive")
    else:
        if idx is not None and 0 <= idx < len(SERVERS_CACHE):
            # 直接更新字典，UI 会自动响应（因为有 bind_text_from）
            SERVERS_CACHE[idx].update(server_data)
            safe_notify(f"已更新: {server_data['name']}", "positive")
        else:
            safe_notify("目标不存在", "negative"); return False

    # 3. 保存到硬盘
    await save_servers()

    # ================= ✨✨✨ 左侧侧边栏 UI 零闪烁操作区 ✨✨✨ =================
    # 获取新分组名称
    new_group = server_data.get('group', '默认分组')
    # 计算新分组对应的区域 (用于侧边栏归类)
    if new_group in ['默认分组', '自动注册', '未分组', '自动导入']:
        try: new_group = detect_country_group(server_data.get('name', ''), server_data)
        except: pass
        if not new_group: new_group = '🏳️ 其他地区'

    need_full_refresh = False

    try:
        if is_add:
            # === 新增 ===
            # 如果目标分组已展开，直接插入新行
            if new_group in SIDEBAR_UI_REFS['groups']:
                with SIDEBAR_UI_REFS['groups'][new_group]:
                    render_single_sidebar_row(server_data)
                EXPANDED_GROUPS.add(new_group)
            else:
                need_full_refresh = True # 分组还没渲染过，只能全刷
                
        elif old_group != new_group:
            # === 移动分组 ===
            # 尝试将旧行移动到新分组容器
            row_el = SIDEBAR_UI_REFS['rows'].get(server_data['url'])
            target_col = SIDEBAR_UI_REFS['groups'].get(new_group)
            
            if row_el and target_col:
                row_el.move(target_col)
                EXPANDED_GROUPS.add(new_group)
            else:
                need_full_refresh = True
        
    except Exception as e:
        logger.error(f"UI Move Error: {e}")
        need_full_refresh = True

    if need_full_refresh:
        try: render_sidebar_content.refresh()
        except: pass

    # =================  右侧主视图同步逻辑 =================
    current_scope = CURRENT_VIEW_STATE.get('scope')
    current_data = CURRENT_VIEW_STATE.get('data')
    
    # 情况1: 如果当前正在查看这台服务器的详情页 -> 立即刷新该单页
    if current_scope == 'SINGLE' and (current_data == server_data or (is_add and server_data == SERVERS_CACHE[-1])):
        try: await refresh_content('SINGLE', server_data, force_refresh=True)
        except: pass
        
    # 情况2: 如果当前在列表视图 (全部/分组/区域) -> 立即刷新列表并重置冷却
    elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
        # 强制置空 scope 以绕过 refresh_content 内部的状态判断 (确保 _render_ui 被调用)
        CURRENT_VIEW_STATE['scope'] = None 
        try: 
            # 🟢 [Trigger 2 生效点]：force_refresh=True
            # 这会：
            # 1. 忽略 30分钟 冷却
            # 2. 立即启动后台同步
            # 3. 同步完成后更新 LAST_SYNC_MAP，开启新的 30分钟 倒计时
            await refresh_content(current_scope, current_data, force_refresh=True) 
        except: pass
        
    elif current_scope == 'DASHBOARD':
        try: await refresh_dashboard_ui()
        except: pass

    # =================  后台任务 (GeoIP / 探针安装)  =================
    asyncio.create_task(fast_resolve_single_server(server_data))
    
    if ADMIN_CONFIG.get('probe_enabled', False) and server_data.get('probe_installed', False):
        async def delayed_install():
            await asyncio.sleep(1)
            await install_probe_on_server(server_data)
        asyncio.create_task(delayed_install())
        
    return True


                        
# ================= 小巧卡片式弹窗  =================
async def open_server_dialog(idx=None):
    is_edit = idx is not None
    original_data = SERVERS_CACHE[idx] if is_edit else {}
    data = original_data.copy()
    
    # --- 1. 智能检测初始状态 ---
    if is_edit:
        has_xui_conf = bool(data.get('url') and data.get('user') and data.get('pass'))
        raw_ssh_host = data.get('ssh_host')
        if not raw_ssh_host and not has_xui_conf: 
            raw_ssh_host = data.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
        
        has_ssh_conf = bool(
            raw_ssh_host or 
            data.get('ssh_user') or 
            data.get('ssh_key') or 
            data.get('ssh_password') or 
            data.get('probe_installed')
        )
        if not has_ssh_conf and not has_xui_conf: has_ssh_conf = True
    else:
        has_xui_conf = True; has_ssh_conf = True

    state = {'ssh_active': has_ssh_conf, 'xui_active': has_xui_conf}

    with ui.dialog() as d, ui.card().classes('w-full max-w-sm p-5 flex flex-col gap-4'):
        
        # --- 标题栏 ---
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('编辑服务器' if is_edit else '添加服务器').classes('text-lg font-bold')
            tabs = ui.tabs().classes('text-blue-600')
            with tabs:
                t_ssh = ui.tab('SSH / 探针', icon='terminal')
                t_xui = ui.tab('X-UI面板', icon='settings')

        # ================= 独立的基础信息保存逻辑 =================
        async def save_basic_info_only():
            if not is_edit: 
                safe_notify("新增服务器请使用下方的保存按钮", "warning")
                return

            new_name = name_input.value.strip()
            new_group = group_input.value
            
            if not new_name: new_name = await generate_smart_name(data)
            
            SERVERS_CACHE[idx]['name'] = new_name
            SERVERS_CACHE[idx]['group'] = new_group
            
            await save_servers()
            render_sidebar_content.refresh()
            
            # 基础信息修改同步刷新右侧
            current_scope = CURRENT_VIEW_STATE.get('scope')
            if current_scope == 'SINGLE' and CURRENT_VIEW_STATE.get('data') == SERVERS_CACHE[idx]:
                try: await refresh_content('SINGLE', SERVERS_CACHE[idx])
                except: pass
            elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
                # ⚠️ 关键修改：强制重绘
                CURRENT_VIEW_STATE['scope'] = None
                try: await refresh_content(current_scope, CURRENT_VIEW_STATE.get('data'), force_refresh=False)
                except: pass
            
            safe_notify("✅ 基础信息已更新", "positive")
            d.close()
            
        # --- 通用字段区域 ---
        with ui.column().classes('w-full gap-2'):
            name_input = ui.input(value=data.get('name',''), label='备注名称 (留空自动获取)').classes('w-full').props('outlined dense')
            
            with ui.row().classes('w-full items-center gap-2 no-wrap'):
                group_input = ui.select(options=get_all_groups(), value=data.get('group','默认分组'), new_value_mode='add-unique', label='分组').classes('flex-grow').props('outlined dense')
                
                if is_edit:
                    ui.button(icon='save', on_click=save_basic_info_only) \
                        .props('flat dense round color=primary') \
                        .tooltip('仅保存名称和分组 (不重新部署)')

        inputs = {}
        btn_keycap_blue = 'bg-white rounded-lg font-bold tracking-wide border-t border-x border-gray-100 border-b-4 border-blue-100 text-blue-600 px-4 py-1 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1 hover:bg-blue-50'
        btn_keycap_delete = 'bg-white rounded-xl font-bold tracking-wide w-full border-t border-x border-gray-100 border-b-4 border-red-100 text-red-500 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1 hover:bg-red-50'
        btn_keycap_red_confirm = 'rounded-lg font-bold tracking-wide text-white border-b-4 border-red-900 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1'

        # ==================== 保存逻辑 (完整保存) ====================
        async def save_panel_data(panel_type):
            final_name = name_input.value.strip()
            final_group = group_input.value
            new_server_data = data.copy()
            new_server_data['group'] = final_group

            if panel_type == 'ssh':
                if not inputs.get('ssh_host'): return
                s_host = inputs['ssh_host'].value.strip()
                if not s_host: safe_notify("SSH 主机 IP 不能为空", "negative"); return

                new_server_data.update({
                    'ssh_host': s_host,
                    'ssh_port': str(inputs['ssh_port'].value).strip(),
                    'ssh_user': inputs['ssh_user'].value.strip(),
                    'ssh_auth_type': inputs['auth_type'].value,
                    'ssh_password': inputs['ssh_pwd'].value if inputs['ssh_pwd'] else '',
                    'ssh_key': inputs['ssh_key'].value if inputs['ssh_key'] else '',
                    
                    # 🛑 [核心修改点]：只要点击保存SSH，就强制开启探针开关
                    'probe_installed': True 
                })
                
                # 同步更新 UI 上的复选框状态（如果界面上有显示的话）
                if 'probe_chk' in inputs: 
                    inputs['probe_chk'].value = True

                if not new_server_data.get('url'): new_server_data['url'] = f"http://{s_host}:22"

            elif panel_type == 'xui':
                if not inputs.get('xui_url'): return
                x_url_raw = inputs['xui_url'].value.strip()
                x_user = inputs['xui_user'].value.strip()
                x_pass = inputs['xui_pass'].value.strip()
                
                if not (x_url_raw and x_user and x_pass): 
                    safe_notify("必填项不能为空", "negative"); return

                # === ✨ 核心修改：智能 URL 解析逻辑 (支持自动提取路径) ===
                # 1. 补全协议头
                if '://' not in x_url_raw: x_url_raw = f"http://{x_url_raw}"
                
                from urllib.parse import urlparse
                try:
                    parsed = urlparse(x_url_raw)
                    
                    # 2. 处理端口 (如果 netloc 里没有冒号且不是IPv6，说明没端口，补全默认)
                    netloc = parsed.netloc
                    if ':' not in netloc and ']' not in netloc:
                        netloc = f"{netloc}:54321"
                        safe_notify(f"已自动添加默认端口: 54321", "positive")
                    
                    # 3. 重组纯净的 Base URL (Scheme + Host + Port，不带路径)
                    final_base_url = f"{parsed.scheme}://{netloc}"
                    
                    # 4. 智能提取路径作为 Prefix
                    # 逻辑：如果输入栏的 URL 自带了路径(如 /my-path)，则优先使用它作为前缀
                    # 否则使用下方 "API 前缀" 输入框的值
                    path_from_url = parsed.path.strip().strip('/')
                    
                    if path_from_url:
                        final_prefix = f"/{path_from_url}"
                        # 可选：更新 UI 上的前缀输入框显示，给用户反馈
                        if 'xui_prefix' in inputs:
                            inputs['xui_prefix'].value = final_prefix
                        safe_notify(f"已自动识别路径: {final_prefix}", "positive")
                    else:
                        # URL 里没路径，读取手动填写的
                        final_prefix = inputs['xui_prefix'].value.strip()
                        
                except Exception as e:
                    # 解析异常兜底
                    logger.error(f"URL Parse Error: {e}")
                    final_base_url = x_url_raw
                    final_prefix = inputs['xui_prefix'].value.strip()

                probe_val = inputs['probe_chk'].value
                new_server_data.update({
                    'url': final_base_url, 
                    'user': x_user, 
                    'pass': x_pass,
                    'prefix': final_prefix,
                    'probe_installed': probe_val
                })
                
                if probe_val:
                    if not new_server_data.get('ssh_host'):
                        # 从解析后的 URL 中提取 Host
                        try:
                            # 再次解析 final_base_url 提取纯 IP/域名
                            clean_host = urlparse(final_base_url).hostname or final_base_url.split('://')[-1].split(':')[0]
                            new_server_data['ssh_host'] = clean_host
                        except:
                            new_server_data['ssh_host'] = final_base_url.split('://')[-1].split(':')[0]
                    if not new_server_data.get('ssh_port'): new_server_data['ssh_port'] = '22'
                    if not new_server_data.get('ssh_user'): new_server_data['ssh_user'] = 'root'
                    if not new_server_data.get('ssh_auth_type'): new_server_data['ssh_auth_type'] = '全局密钥'

            if not final_name:
                safe_notify("正在生成名称...", "ongoing")
                final_name = await generate_smart_name(new_server_data)
            new_server_data['name'] = final_name

            success = await save_server_config(new_server_data, is_add=not is_edit, idx=idx)
            
            if success:
                data.update(new_server_data)
                if panel_type == 'ssh': state['ssh_active'] = True
                if panel_type == 'xui': state['xui_active'] = True
                
                if panel_type == 'xui' and new_server_data.get('probe_installed'):
                    state['ssh_active'] = True

                # 🛑 [判断逻辑]：这里检查 probe_installed 是否为 True
                # 因为上面我们在保存 SSH 时强制设为了 True，所以这里一定会触发安装
                if new_server_data.get('probe_installed'):
                     safe_notify(f"🚀 配置已保存，正在自动推送探针...", "ongoing")
                     # 立即触发安装任务
                     asyncio.create_task(install_probe_on_server(new_server_data))
                else:
                     safe_notify(f"✅ {panel_type.upper()} 已保存", "positive")

        # ==================== SSH 面板渲染 ====================
        @ui.refreshable
        def render_ssh_panel():
            if not state['ssh_active']:
                with ui.column().classes('w-full h-48 justify-center items-center bg-gray-50 rounded border border-dashed border-gray-300'):
                    ui.icon('terminal', color='grey').classes('text-4xl mb-2')
                    ui.label('SSH 功能未启用').classes('text-gray-500 font-bold mb-2')
                    ui.button('启用 SSH 配置', icon='add', on_click=lambda: _activate_panel('ssh')).props('flat bg-blue-50 text-blue-600')
            else:
                init_host = data.get('ssh_host')
                if not init_host and is_edit:
                     if '://' in data.get('url', ''): init_host = data.get('url', '').split('://')[-1].split(':')[0]
                     else: init_host = data.get('url', '').split(':')[0]

                inputs['ssh_host'] = ui.input(label='SSH 主机 IP', value=init_host).classes('w-full').props('outlined dense')
                
                with ui.column().classes('w-full gap-3'):
                    with ui.row().classes('w-full gap-2'):
                        inputs['ssh_user'] = ui.input(value=data.get('ssh_user','root'), label='SSH 用户').classes('flex-1').props('outlined dense')
                        inputs['ssh_port'] = ui.input(value=data.get('ssh_port','22'), label='端口').classes('w-1/3').props('outlined dense')
                    
                    valid_auth_options = ['全局密钥', '独立密码', '独立密钥']
                    current_auth = data.get('ssh_auth_type', '全局密钥')
                    if current_auth not in valid_auth_options: current_auth = '全局密钥'
                    
                    inputs['auth_type'] = ui.select(valid_auth_options, value=current_auth, label='认证方式').classes('w-full').props('outlined dense options-dense')

                    inputs['ssh_pwd'] = ui.input(label='SSH 密码', password=True, value=data.get('ssh_password','')).classes('w-full').props('outlined dense')
                    inputs['ssh_pwd'].bind_visibility_from(inputs['auth_type'], 'value', value='独立密码')
                    
                    inputs['ssh_key'] = ui.textarea(label='SSH 私钥', value=data.get('ssh_key','')).classes('w-full').props('outlined dense rows=3 input-class=font-mono text-xs')
                    inputs['ssh_key'].bind_visibility_from(inputs['auth_type'], 'value', value='独立密钥')
                
                ui.separator().classes('my-1')
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('✅ 自动使用全局私钥').bind_visibility_from(inputs['auth_type'], 'value', value='全局密钥').classes('text-green-600 text-xs font-bold')
                    ui.element('div').bind_visibility_from(inputs['auth_type'], 'value', value='独立密码') 
                    ui.element('div').bind_visibility_from(inputs['auth_type'], 'value', value='独立密钥') 
                    
                    ui.button('保存 SSH', icon='save', on_click=lambda: save_panel_data('ssh')).props('flat').classes(btn_keycap_blue)

        # ==================== X-UI 面板渲染 ====================
        @ui.refreshable
        def render_xui_panel():
            if not state['xui_active']:
                with ui.column().classes('w-full h-48 justify-center items-center bg-gray-50 rounded border border-dashed border-gray-300'):
                    ui.icon('settings_applications', color='grey').classes('text-4xl mb-2')
                    ui.label('X-UI 面板未配置').classes('text-gray-500 font-bold mb-2')
                    ui.button('配置 X-UI 信息', icon='add', on_click=lambda: _activate_panel('xui')).props('flat bg-purple-50 text-purple-600')
            else:
                inputs['xui_url'] = ui.input(value=data.get('url',''), label='面板 URL (http://ip:port)').classes('w-full').props('outlined dense')
                ui.label('默认端口 54321，如不填写将自动补全').classes('text-[10px] text-gray-400 ml-1 -mt-1 mb-1')
                
                with ui.row().classes('w-full gap-2'):
                    inputs['xui_user'] = ui.input(value=data.get('user',''), label='账号').classes('flex-1').props('outlined dense')
                    inputs['xui_pass'] = ui.input(value=data.get('pass',''), label='密码', password=True).classes('flex-1').props('outlined dense')
                inputs['xui_prefix'] = ui.input(value=data.get('prefix',''), label='面板根路径 (选填)').classes('w-full').props('outlined dense')

                ui.separator().classes('my-1')
                
                with ui.row().classes('w-full justify-between items-center'):
                    inputs['probe_chk'] = ui.checkbox('启用 Root 探针', value=data.get('probe_installed', False))
                    inputs['probe_chk'].classes('text-sm font-bold text-slate-700')
                    
                    ui.button('保存 X-UI', icon='save', on_click=lambda: save_panel_data('xui')).props('flat').classes(btn_keycap_blue)

                ui.label('提示: 启用探针需先配置 SSH 登录信息').classes('text-[10px] text-red-500 ml-8 -mt-2')

                def auto_fill_ssh():
                    if inputs['probe_chk'].value and state['ssh_active'] and inputs.get('ssh_host') and not inputs['ssh_host'].value:
                        p_url = inputs['xui_url'].value
                        if p_url:
                            clean_ip = p_url.split('://')[-1].split(':')[0]
                            if ':' in clean_ip: clean_ip = clean_ip.split(':')[0]
                            inputs['ssh_host'].set_value(clean_ip)
                inputs['probe_chk'].on_value_change(auto_fill_ssh)

        def _activate_panel(panel_type):
            state[f'{panel_type}_active'] = True
            if panel_type == 'ssh': render_ssh_panel.refresh()
            elif panel_type == 'xui': render_xui_panel.refresh()

        default_tab = t_ssh
        if is_edit and not state['ssh_active'] and state['xui_active']: default_tab = t_xui

        with ui.tab_panels(tabs, value=default_tab).classes('w-full animated fadeIn'):
            with ui.tab_panel(t_ssh).classes('p-0 flex flex-col gap-3'):
                render_ssh_panel()
            with ui.tab_panel(t_xui).classes('p-0 flex flex-col gap-3'):
                render_xui_panel()

        # ================= 5. 全局删除逻辑  =================
        if is_edit:
            with ui.row().classes('w-full justify-start mt-4 pt-2 border-t border-gray-100'):
                async def open_delete_confirm():
                    with ui.dialog() as del_d, ui.card().classes('w-80 p-4'):
                        ui.label('删除确认').classes('text-lg font-bold text-red-600')
                        ui.label('请选择要删除的内容：').classes('text-sm text-gray-600 mb-2')
                        
                        real_ssh_exists = bool(data.get('ssh_host') or data.get('ssh_user'))
                        real_xui_exists = bool(data.get('url') and data.get('user') and data.get('pass'))
                        has_probe = data.get('probe_installed', False)

                        if not real_ssh_exists and not real_xui_exists:
                            real_ssh_exists = True; real_xui_exists = True

                        chk_ssh = ui.checkbox('SSH 连接信息', value=real_ssh_exists).classes('text-sm font-bold')
                        chk_xui = ui.checkbox('X-UI 面板信息', value=real_xui_exists).classes('text-sm font-bold')
                        
                        chk_uninstall = ui.checkbox('同时卸载远程探针脚本', value=True).classes('text-sm font-bold text-red-500')
                        chk_uninstall.set_visibility(has_probe)
                        
                        if not real_ssh_exists: chk_ssh.value = False; chk_ssh.disable()
                        if not real_xui_exists: chk_xui.value = False; chk_xui.disable()
                        if real_ssh_exists and not real_xui_exists: chk_ssh.disable()
                        if real_xui_exists and not real_ssh_exists: chk_xui.disable()

                        async def confirm_execution():
                            if idx >= len(SERVERS_CACHE): return
                            target_srv = SERVERS_CACHE[idx]
                            
                            will_delete_ssh = chk_ssh.value
                            will_delete_xui = chk_xui.value
                            will_uninstall = chk_uninstall.value and chk_uninstall.visible
                            
                            remaining_ssh = real_ssh_exists and not will_delete_ssh
                            remaining_xui = real_xui_exists and not will_delete_xui
                            
                            is_full_delete = False

                            # 使用 notification + try/finally 
                            if will_uninstall:
                                # 1. 开启转圈通知
                                loading_notify = ui.notification('正在尝试连接并卸载探针...', timeout=None, spinner=True)
                                
                                try:
                                    uninstall_cmd = "systemctl stop x-fusion-agent && systemctl disable x-fusion-agent && rm -f /etc/systemd/system/x-fusion-agent.service && systemctl daemon-reload && rm -f /root/x_fusion_agent.py"
                                    
                                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(target_srv, uninstall_cmd))
                                    
                                    if success:
                                        ui.notify('✅ 远程探针已卸载清理', type='positive')
                                    else:
                                        ui.notify(f'⚠️ 远程卸载失败 (可能是连接超时)，将仅删除本地记录', type='warning')
                                finally:
                                    # 2. 无论结果如何，必须关闭转圈
                                    loading_notify.dismiss()

                            if not remaining_ssh and not remaining_xui:
                                SERVERS_CACHE.pop(idx)
                                u = target_srv.get('url'); p_u = target_srv.get('ssh_host') or u
                                for k in [u, p_u]:
                                    if k in PROBE_DATA_CACHE: del PROBE_DATA_CACHE[k]
                                    if k in NODES_DATA: del NODES_DATA[k]
                                    if k in PING_TREND_CACHE: del PING_TREND_CACHE[k]
                                safe_notify('✅ 服务器已彻底删除', 'positive')
                                is_full_delete = True
                            else:
                                if will_delete_ssh:
                                    for k in ['ssh_host', 'ssh_port', 'ssh_user', 'ssh_password', 'ssh_key', 'ssh_auth_type']: target_srv[k] = ''
                                    target_srv['probe_installed'] = False
                                    state['ssh_active'] = False
                                    data['ssh_host'] = ''
                                    safe_notify('✅ SSH 信息已清除', 'positive')
                                
                                if will_delete_xui:
                                    for k in ['url', 'user', 'pass', 'prefix']: target_srv[k] = ''
                                    state['xui_active'] = False
                                    data['url'] = '' 
                                    safe_notify('✅ X-UI 信息已清除', 'positive')

                            await save_servers()
                            del_d.close()
                            d.close()
                            
                            render_sidebar_content.refresh()
                            current_scope = CURRENT_VIEW_STATE.get('scope')
                            current_data = CURRENT_VIEW_STATE.get('data')

                            if is_full_delete:
                                if current_scope == 'SINGLE' and current_data == target_srv:
                                    content_container.clear()
                                    with content_container:
                                        ui.label('该服务器已删除').classes('text-gray-400 text-lg w-full text-center mt-20')
                                elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
                                    CURRENT_VIEW_STATE['scope'] = None
                                    await refresh_content(current_scope, current_data, force_refresh=False)
                            else:
                                if current_scope == 'SINGLE' and current_data == target_srv:
                                    await refresh_content('SINGLE', target_srv)

                        with ui.row().classes('w-full justify-end mt-4 gap-2'):
                            ui.button('取消', on_click=del_d.close).props('flat dense color=grey')
                            ui.button('确认执行', color='red', on_click=confirm_execution).props('unelevated').classes(btn_keycap_red_confirm)
                    del_d.open()

                ui.button('删除 / 卸载配置', icon='delete', on_click=open_delete_confirm).props('flat').classes(btn_keycap_delete)
    d.open()
    
# ================= 数据备份/恢复 (深色适配版) =================
async def open_data_mgmt_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl max-h-[90vh] flex flex-col gap-0 p-0 overflow-hidden bg-[#1e293b] border border-slate-700'):
        
        # Tabs
        with ui.tabs().classes('w-full bg-[#0f172a] flex-shrink-0 border-b border-slate-700 text-slate-400') \
            .props('indicator-color=blue active-color=blue') as tabs:
            tab_export = ui.tab('完整备份 (导出)')
            tab_import = ui.tab('恢复 / 批量添加')
            
        with ui.tab_panels(tabs, value=tab_import).classes('w-full p-6 overflow-y-auto flex-grow bg-[#1e293b] text-slate-200'):
            
            # --- 导出 ---
            with ui.tab_panel(tab_export).classes('flex flex-col gap-8 items-center justify-center h-full'):
                full_backup = {
                    "version": "3.0", "timestamp": __import__('time').time(),
                    "servers": SERVERS_CACHE, "subscriptions": SUBS_CACHE,
                    "admin_config": ADMIN_CONFIG, "global_ssh_key": load_global_key(), "cache": NODES_DATA
                }
                json_str = json.dumps(full_backup, indent=2, ensure_ascii=False)
                
                with ui.column().classes('items-center gap-2'):
                    ui.icon('cloud_download', size='5rem', color='primary').classes('opacity-90')
                    ui.label('备份数据已准备就绪').classes('text-xl font-bold text-slate-200 tracking-wide')
                    ui.label(f'包含 {len(SERVERS_CACHE)} 个服务器配置').classes('text-xs text-slate-500')

                with ui.column().classes('w-full max-w-md gap-4'):
                    ui.button('复制到剪贴板', icon='content_copy', on_click=lambda: safe_copy_to_clipboard(json_str)).classes('w-full h-12 text-base font-bold bg-blue-600 text-white shadow-lg rounded-lg hover:bg-blue-500')
                    ui.button('下载 .json 文件', icon='download', on_click=lambda: ui.download(json_str.encode('utf-8'), 'xui_backup.json')).classes('w-full h-12 text-base font-bold bg-green-600 text-white shadow-lg rounded-lg hover:bg-green-500')

            # --- 导入 ---
            with ui.tab_panel(tab_import).classes('flex flex-col gap-6'):
                # 恢复备份
                with ui.expansion('方式一：恢复 JSON 备份文件', icon='restore', value=False).classes('w-full border border-slate-700 rounded bg-[#172033]').props('header-class="text-slate-300"'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        import_text = ui.textarea(placeholder='粘贴备份 JSON...').classes('w-full h-32 font-mono text-xs').props('outlined dark bg-color="slate-900"')
                        with ui.row().classes('w-full gap-4 items-center'):
                            overwrite_chk = ui.checkbox('覆盖同名服务器', value=False).props('dense dark color=red')
                            restore_key_chk = ui.checkbox('恢复 SSH 密钥', value=True).props('dense dark color=blue')
                            restore_sub_chk = ui.checkbox('恢复订阅设置', value=True).props('dense dark color=blue')
                        
                        async def process_import():
                            try:
                                raw = import_text.value.strip(); data = json.loads(raw)
                                new_servers = data.get('servers', []) if isinstance(data, dict) else data
                                new_subs = data.get('subscriptions', []); new_config = data.get('admin_config', {})
                                new_ssh_key = data.get('global_ssh_key', ''); new_cache = data.get('cache', {})
                                
                                added=0; updated=0
                                existing_map = {s['url']: i for i, s in enumerate(SERVERS_CACHE)}
                                for item in new_servers:
                                    url = item.get('url')
                                    if url in existing_map:
                                        if overwrite_chk.value: SERVERS_CACHE[existing_map[url]] = item; updated+=1
                                    else: SERVERS_CACHE.append(item); existing_map[url]=len(SERVERS_CACHE)-1; added+=1
                                
                                if restore_key_chk.value and data.get('global_ssh_key'): save_global_key(data['global_ssh_key'])
                                if restore_sub_chk.value and isinstance(data, dict):
                                    global SUBS_CACHE, ADMIN_CONFIG
                                    if data.get('subscriptions'): SUBS_CACHE = data['subscriptions']
                                    if data.get('admin_config'): ADMIN_CONFIG.update(data['admin_config'])
                                
                                await save_servers(); await save_subs(); await save_admin_config()
                                render_sidebar_content.refresh()
                                safe_notify(f"恢复: +{added} / ~{updated}", 'positive'); d.close()
                            except Exception as e: safe_notify(f"错误: {e}", 'negative')
                        ui.button('执行恢复', on_click=process_import).classes('w-full bg-slate-700 text-white')

                # 批量添加
                with ui.expansion('方式二：批量添加服务器', icon='playlist_add', value=True).classes('w-full border border-slate-700 rounded bg-[#172033]').props('header-class="text-slate-300"'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        ui.label('批量输入 (每行一个，支持 IP 或 URL)').classes('text-xs font-bold text-slate-500')
                        url_area = ui.textarea(placeholder='192.168.1.10\n...').classes('w-full h-32 font-mono text-sm').props('outlined dark bg-color="slate-900"')
                        
                        # 默认设置
                        with ui.grid().classes('w-full gap-2 grid-cols-2'):
                            def_ssh_user = ui.input('默认 SSH 用户', value=ADMIN_CONFIG.get('pref_ssh_user','root')).props('dense outlined dark')
                            def_ssh_port = ui.input('默认 SSH 端口', value=ADMIN_CONFIG.get('pref_ssh_port','22')).props('dense outlined dark')
                            
                            def_auth = ui.select(['全局密钥', '独立密码'], value='全局密钥', label='认证').classes('col-span-2').props('dense outlined dark options-dense')
                            def_pwd = ui.input('SSH 密码').props('dense outlined dark').classes('col-span-2').bind_visibility_from(def_auth, 'value', value='独立密码')
                            
                            def_xui_port = ui.input('X-UI 端口', value=ADMIN_CONFIG.get('pref_xui_port','54321')).props('dense outlined dark')
                            def_xui_user = ui.input('X-UI 账号', value=ADMIN_CONFIG.get('pref_xui_user','admin')).props('dense outlined dark')
                            def_xui_pass = ui.input('X-UI 密码', value=ADMIN_CONFIG.get('pref_xui_pass','admin')).props('dense outlined dark')

                        # 开关
                        with ui.row().classes('w-full justify-between items-center bg-slate-800 p-2 rounded border border-slate-700'):
                            chk_xui = ui.checkbox('添加 X-UI 面板', value=True).props('dark dense').classes('text-blue-400 font-bold')
                            chk_probe = ui.checkbox('启用 Root 探针', value=False).props('dark dense').classes('text-green-400 font-bold')

                        # 执行
                        async def run_batch_import():
                            # 1. 先保存用户的偏好设置到 ADMIN_CONFIG
                            ADMIN_CONFIG['pref_ssh_user'] = def_ssh_user.value
                            ADMIN_CONFIG['pref_ssh_port'] = def_ssh_port.value
                            ADMIN_CONFIG['pref_xui_port'] = def_xui_port.value
                            ADMIN_CONFIG['pref_xui_user'] = def_xui_user.value
                            ADMIN_CONFIG['pref_xui_pass'] = def_xui_pass.value
                            await save_admin_config() # 立即写入磁盘
                            
                            # 2. 开始处理添加逻辑
                            raw_text = url_area.value.strip()
                            if not raw_text: safe_notify("请输入内容", "warning"); return
                            
                            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
                            count = 0
                            existing_urls = {s['url'] for s in SERVERS_CACHE}
                            post_tasks = []
                            
                            should_add_xui = chk_xui.value
                            should_add_probe = chk_probe.value

                            for line in lines:
                                target_ssh_port = def_ssh_port.value
                                target_xui_port = def_xui_port.value
                                
                                if '://' in line:
                                    final_url = line
                                    try: 
                                        parsed = urlparse(line)
                                        name = parsed.hostname or line
                                    except: name = line
                                else:
                                    if ':' in line and not line.startswith('['): 
                                        parts = line.split(':')
                                        host_ip = parts[0]
                                        target_xui_port = parts[1] 
                                    else: 
                                        host_ip = line
                                        target_xui_port = def_xui_port.value
                                    
                                    final_url = f"http://{host_ip}:{target_xui_port}"
                                    name = host_ip

                                if final_url in existing_urls: continue
                                
                                # 根据开关决定是否填入账号密码
                                final_xui_user = def_xui_user.value if should_add_xui else ""
                                final_xui_pass = def_xui_pass.value if should_add_xui else ""

                                new_server = {
                                    'name': name, 
                                    'group': '', 
                                    'url': final_url,
                                    'user': final_xui_user, 
                                    'pass': final_xui_pass, 
                                    'prefix': '',
                                    'ssh_user': def_ssh_user.value, 
                                    'ssh_port': target_ssh_port,
                                    'ssh_auth_type': def_auth.value,
                                    'ssh_password': def_pwd.value,
                                    'ssh_key': '',
                                    'probe_installed': should_add_probe
                                }

                                SERVERS_CACHE.append(new_server)
                                existing_urls.add(final_url)
                                count += 1
                                
                                post_tasks.append(fast_resolve_single_server(new_server))
                                
                                if ADMIN_CONFIG.get('probe_enabled', False) and should_add_probe:
                                    post_tasks.append(install_probe_on_server(new_server))

                            if count > 0:
                                await save_servers()
                                render_sidebar_content.refresh()
                                safe_notify(f"成功添加 {count} 台服务器", 'positive')
                                d.close()
                                
                                if post_tasks:
                                    safe_notify(f"正在后台处理 {len(post_tasks)} 个初始化任务...", "ongoing")
                                    async def _run_bg_tasks():
                                        await asyncio.gather(*post_tasks, return_exceptions=True)
                                    asyncio.create_task(_run_bg_tasks())
                                    
                            else: safe_notify("未添加任何服务器 (可能已存在)", 'warning')

                        ui.button('确认批量添加', icon='add_box', on_click=run_batch_import).classes('w-full bg-blue-600 text-white h-10')
    d.open()
# ================= 渲染逻辑 =================

# 辅助函数：格式化流量
def format_bytes(size):
    if not size: return '0 B'
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

# ================= 智能排序逻辑=================
import re

CN_NUM_MAP = {'〇':0, '零':0, '一':1, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9}

def cn_to_arabic_str(match):
    s = match.group()
    if not s: return s
    if '十' in s:
        val = 0
        parts = s.split('十')
        if parts[0]: val += CN_NUM_MAP.get(parts[0], 0) * 10
        else: val += 10
        if len(parts) > 1 and parts[1]: val += CN_NUM_MAP.get(parts[1], 0)
        return str(val)
    return "".join(str(CN_NUM_MAP.get(c, 0)) for c in s)

def to_safe_sort_list(items):
    """确保列表可排序：[(权重, 值), ...]"""
    safe_list = []
    for item in items:
        if isinstance(item, int):
            safe_list.append((1, item)) # 数字权重高
        else:
            safe_list.append((0, str(item).lower()))
    return safe_list

def smart_sort_key(server_info):
    name = server_info.get('name', '')
    if not name: return []

    # 1. 预处理：汉字转数字
    try: name_normalized = re.sub(r'[零一二三四五六七八九十]+', cn_to_arabic_str, name)
    except: name_normalized = name

    # 2. 尝试旧版特定逻辑拆分
    try:
        if '|' in name_normalized:
            parts = name_normalized.split('|', 1)
            p1 = parts[0].strip(); rest = parts[1].strip()
        else:
            p1 = name_normalized; rest = ""

        p2 = ""
        if ' ' in rest:
            parts = rest.split(' ', 1)
            p2 = parts[0].strip(); rest = parts[1].strip()
        
        sub_parts = rest.split('-')
        p3 = sub_parts[0].strip()
        
        p3_num = 0; p3_text = p3
        p3_match = re.search(r'(\d+)$', p3)
        if p3_match:
            p3_num = int(p3_match.group(1))
            p3_text = p3[:p3_match.start()]

        p4 = ""; p5 = 0
        if len(sub_parts) >= 2: p4 = sub_parts[1].strip()
        if len(sub_parts) >= 3:
            last = sub_parts[-1].strip()
            if last.isdigit(): p5 = int(last)
            else: p4 += f"-{last}"
        elif len(sub_parts) == 2 and sub_parts[1].strip().isdigit():
            p5 = int(sub_parts[1].strip())

        return to_safe_sort_list([p1, p2, p3_text, p3_num, p4, p5])

    except:
        parts = re.split(r'(\d+)', name_normalized)
        mixed_list = [int(text) if text.isdigit() else text for text in parts]
        return to_safe_sort_list(mixed_list)
    


# ================= 全局布局定义区域 (全响应式版) =================

# 1. 带延迟 (9列) - 用于: 区域分组(如显示Ping时)
# 布局: 服务器(2fr) 备注(2fr) 分组/IP(1.5fr) 流量(1fr) 协议(0.8fr) 端口(0.8fr) 延迟(0.8fr) 状态(0.5fr) 操作(1.5fr)
COLS_WITH_PING = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 0.8fr 0.5fr 1.5fr; align-items: center;'

# 2. 无延迟 (8列) - 用于: 所有服务器列表(默认), 自定义分组
# 布局: 服务器(2fr) 备注(2fr) 分组(1.5fr) 流量(1fr) 协议(0.8fr) 端口(0.8fr) 状态(0.5fr) 操作(1.5fr)
COLS_NO_PING   = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 0.5fr 1.5fr; align-items: center;'

# 3. 单机视图带延迟 (8列) - 用于: 单台服务器详情页 (如果显示延迟的话)
# 布局: 节点名称(3fr) 类型(1fr) 流量(1fr) 协议(0.8fr) 端口(0.8fr) 延迟(0.8fr) 状态(0.5fr) 操作(1.5fr)
# 注：这里给“节点名称”分配 3fr，因为它只有一列长文字，可以宽一点
SINGLE_COLS = 'grid-template-columns: 3fr 1fr 1fr 0.8fr 0.8fr 0.8fr 0.5fr 1.5fr; align-items: center;'

# 4. 所有服务器简略版 (7列) - 某些特殊视图使用
# 布局: 服务器(2fr) 备注(2fr) 在线状态(1.5fr) 流量(1fr) 协议(0.8fr) 端口(0.8fr) 操作(1.5fr)
COLS_ALL_SERVERS = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 1.5fr; align-items: center;'

# 5. 区域分组专用布局  ✨✨✨
# 格式: 服务器(150) 备注(200) 在线状态(1fr) 流量(100) 协议(80) 端口(80) 操作(150)
COLS_SPECIAL_WITH_PING = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 1.5fr; align-items: center;'

# 6. 单服务器专用布局 (移除延迟列 90px，格式与 All Servers 一致) ✨✨✨
# 格式: 备注(200) 所在组(1fr) 流量(100) 协议(80) 端口(80) 状态(100) 操作(150)
SINGLE_COLS_NO_PING = 'grid-template-columns: 3fr 1fr 1.5fr 1fr 1fr 1fr 1.5fr; align-items: center;'


# ================= 全局配置 =================
REFRESH_LOCKS = set()
LAST_SYNC_MAP = {} # 🕒 格式: {'TAG::香港::P1': timestamp, 'TAG::香港::P2': timestamp}
PAGE_SIZE = 30
SYNC_COOLDOWN = 1800 # 30分钟

# ================= 刷新逻辑 (智能提示版：区分探针实时与API缓存) =================
async def refresh_content(scope='ALL', data=None, force_refresh=False, sync_name_action=False, page_num=1, manual_client=None):
    # 1. 上下文获取
    client = manual_client
    if not client:
        try: client = ui.context.client
        except: pass
    if not client: return

    with client:
        global CURRENT_VIEW_STATE, REFRESH_LOCKS, LAST_SYNC_MAP
        import time
        
        # 唯一标识 key (精确到页)
        cache_key = f"{scope}::{data}::P{page_num}"
        lock_key = cache_key
        
        now = time.time()
        last_sync = LAST_SYNC_MAP.get(cache_key, 0)
        
        # --- 预计算当前页的服务器成分 ---
        targets = get_targets_by_scope(scope, data)
        start_idx = (page_num - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_servers = targets[start_idx:end_idx] if targets else []
        
        has_probe = any(s.get('probe_installed') for s in current_page_servers)
        has_api_only = any(not s.get('probe_installed') for s in current_page_servers)
        
        # 2. 🛑 冷却逻辑与缓存渲染
        # 如果不是强制刷新，且满足以下任一条件则直接使用缓存渲染：
        # A. 在 30分钟 冷却时间内
        # B. 这一页全是已安装探针的机器（数据本身就是最新的）
        is_all_probe = has_probe and not has_api_only
        
        if not force_refresh and ((now - last_sync < SYNC_COOLDOWN) or is_all_probe):
            CURRENT_VIEW_STATE.update({'scope': scope, 'data': data, 'page': page_num, 'render_token': now})
            
            # 执行初次渲染（从内存加载）
            await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)
            
            # 智能提示语
            if is_all_probe:
                safe_notify("⚡ 实时数据 (探针推送)", "positive", timeout=1000)
            else:
                mins_ago = int((now - last_sync) / 60)
                safe_notify(f"🕒 缓存数据 ({mins_ago}分前)", "ongoing", timeout=1000)
            return

        # 3. 锁机制：防止同一个页面同时触发多个同步任务
        if lock_key in REFRESH_LOCKS:
             return

        # 4. 状态锁定：记录当前的 render_token，用于后台任务完成后核对
        CURRENT_VIEW_STATE.update({'scope': scope, 'data': data, 'page': page_num, 'render_token': now})
        
        # 5. 立即渲染一次（显示当前内存中的“旧”数据，保证点击瞬间不白屏）
        await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)

        if not current_page_servers: return

        # 6. 启动后台抓取任务
        async def _background_fetch(token_at_start):
            REFRESH_LOCKS.add(lock_key)
            try:
                # 过滤出真正需要从接口拉取的机器（非探针机器）
                sync_targets = [s for s in current_page_servers if not s.get('probe_installed')]
                
                if sync_targets:
                    if force_refresh:
                        safe_notify(f"🔄 正在同步 {len(sync_targets)} 台 API 节点...", "ongoing")
                    
                    # 执行并发抓取
                    tasks = [fetch_inbounds_safe(s, force_refresh=True, sync_name=sync_name_action) for s in sync_targets]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # --- ✨ 关键点：核对身份 ---
                    # 只有当抓取结束时，用户还没点别的页面（token没变），才触发重绘
                    if CURRENT_VIEW_STATE.get('render_token') == token_at_start:
                        with client:
                            await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)
                            # 记录同步成功的时间戳
                            LAST_SYNC_MAP[cache_key] = time.time()
                            if force_refresh: safe_notify("✅ 同步完成", "positive")
                            try: render_sidebar_content.refresh()
                            except: pass
                else:
                    # 全是探针机器，直接标记当前页已更新
                    LAST_SYNC_MAP[cache_key] = time.time()
            finally:
                REFRESH_LOCKS.discard(lock_key)
            
        asyncio.create_task(_background_fetch(now))

# --- 辅助函数  ---
async def _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client):
    if content_container:
        content_container.clear()
        content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
        with content_container:
            targets = get_targets_by_scope(scope, data)
            if scope == 'SINGLE': 
                if targets: await render_single_server_view(targets[0]); return 
                else: ui.label('服务器未找到'); return 
            
            title = ""
            is_group_view = False
            show_ping = False
            if scope == 'ALL': title = f"🌍 所有服务器 ({len(targets)})"
            elif scope == 'TAG': title = f"🏷️ 自定义分组: {data} ({len(targets)})"; is_group_view = True
            elif scope == 'COUNTRY': title = f"🏳️ 区域: {data} ({len(targets)})"; is_group_view = True; show_ping = True 

            with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between'):
                with ui.row().classes('items-center gap-4'): ui.label(title).classes('text-2xl font-bold')
                with ui.row().classes('items-center gap-2'):
                    if is_group_view and targets:
                        with ui.row().classes('gap-1'):
                            ui.button(icon='content_copy', on_click=lambda: copy_group_link(data)).props('flat dense round size=sm color=grey')
                            ui.button(icon='bolt', on_click=lambda: copy_group_link(data, target='surge')).props('flat dense round size=sm text-color=orange')
                            ui.button(icon='cloud_queue', on_click=lambda: copy_group_link(data, target='clash')).props('flat dense round size=sm text-color=green')
                    if targets:
                            # 按钮点击 = 强制刷新 (绕过冷却)
                            ui.button('同步当前页', icon='sync', on_click=lambda: refresh_content(scope, data, force_refresh=True, sync_name_action=True, page_num=page_num, manual_client=client)).props('outline color=primary')

            if not targets:
                with ui.column().classes('w-full h-64 justify-center items-center text-gray-400'): ui.icon('inbox', size='4rem'); ui.label('列表为空')
            else: 
                try: targets.sort(key=smart_sort_key)
                except: pass
                await render_aggregated_view(targets, show_ping=show_ping, token=None, initial_page=page_num)

def get_targets_by_scope(scope, data):
    targets = []
    try:
        if scope == 'ALL': targets = list(SERVERS_CACHE)
        elif scope == 'TAG': targets = [s for s in SERVERS_CACHE if data in s.get('tags', [])]
        elif scope == 'COUNTRY':
            for s in SERVERS_CACHE:
                saved = s.get('group')
                real = saved if saved and saved not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区'] else detect_country_group(s.get('name', ''))
                if real == data: targets.append(s)
        elif scope == 'SINGLE':
             if data in SERVERS_CACHE: targets = [data]
    except: pass
    return targets
        
# ================= 状态面板辅助函数 =================

def format_uptime(seconds):
    """将秒数转换为 天/小时/分钟"""
    if not seconds: return "未知"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return f"{d}天 {h}小时 {m}分"

def render_status_card(label, value_str, sub_text, color_class='text-blue-600', icon='memory'):
    """渲染单个简易状态卡片 (用于负载、连接数等)"""
    with ui.card().classes('p-3 shadow-sm border flex-grow items-center justify-between min-w-[150px]'):
        with ui.row().classes('items-center gap-3'):
            with ui.column().classes('justify-center items-center bg-gray-100 rounded-full p-2'):
                ui.icon(icon).classes(f'{color_class} text-xl')
            with ui.column().classes('gap-0'):
                ui.label(label).classes('text-xs text-gray-400 font-bold')
                ui.label(value_str).classes('text-sm font-bold text-slate-700')
                if sub_text: ui.label(sub_text).classes('text-[10px] text-gray-400')

# 用于外部调用的刷新句柄 (例如给右上角"新建节点"按钮使用)
REFRESH_CURRENT_NODES = lambda: None


# =================  单服务器视图 (最终全功能版)  =================
async def render_single_server_view(server_conf, force_refresh=False):
    global REFRESH_CURRENT_NODES
    
    # 1. 布局初始化
    if content_container:
        content_container.clear()
        content_container.classes(remove='overflow-y-auto block', add='h-full overflow-hidden flex flex-col p-4')
    
    with content_container:
        has_manager_access = (server_conf.get('url') and server_conf.get('user') and server_conf.get('pass')) or \
                             (server_conf.get('probe_installed') and server_conf.get('ssh_host'))
        mgr = None
        if has_manager_access:
            try: mgr = get_manager(server_conf)
            except: pass

        @ui.refreshable
        async def render_node_list():
            xui_nodes = await fetch_inbounds_safe(server_conf, force_refresh=False)
            if xui_nodes is None: xui_nodes = []
            custom_nodes = server_conf.get('custom_nodes', [])
            all_nodes = xui_nodes + custom_nodes
            
            if not all_nodes:
                with ui.column().classes('w-full py-12 items-center justify-center opacity-50'):
                    msg = "暂无节点 (可直接新建)" if has_manager_access else "暂无数据"
                    ui.icon('inbox', size='4rem').classes('text-slate-600 mb-2')
                    ui.label(msg).classes('text-slate-500 text-sm')
            else:
                for n in all_nodes:
                    is_custom = n.get('_is_custom', False)
                    is_ssh_mode = (not is_custom) and (server_conf.get('probe_installed') and server_conf.get('ssh_host'))
                    
                    # 列表行样式: bg-[#1e293b], border-slate-700
                    row_3d_cls = 'grid w-full gap-4 py-3 px-2 mb-2 items-center group bg-[#1e293b] rounded-xl border border-slate-700 border-b-[3px] shadow-sm transition-all duration-150 ease-out hover:shadow-md hover:border-blue-500 hover:bg-[#252f45] active:border-b active:translate-y-[2px] active:shadow-none cursor-default'
                    
                    with ui.element('div').classes(row_3d_cls).style(SINGLE_COLS_NO_PING):
                        ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-2 text-slate-300 text-sm group-hover:text-white')
                        
                        if is_custom: ui.label("独立").classes('text-[10px] bg-purple-900/50 text-purple-300 font-bold px-2 py-0.5 rounded-full w-fit mx-auto border border-purple-700')
                        elif is_ssh_mode: ui.label("Root").classes('text-[10px] bg-teal-900/50 text-teal-300 font-bold px-2 py-0.5 rounded-full w-fit mx-auto border border-teal-700')
                        else: ui.label("API").classes('text-[10px] bg-slate-700 text-slate-300 font-bold px-2 py-0.5 rounded-full w-fit mx-auto border border-slate-600')
                        
                        traffic = format_bytes(n.get('up', 0) + n.get('down', 0)) if not is_custom else "--"
                        ui.label(traffic).classes('text-xs text-slate-400 w-full text-center font-mono font-bold')
                        
                        proto = str(n.get('protocol', 'unk')).upper()
                        ui.label(proto).classes(f'text-[11px] font-extrabold w-full text-center text-slate-500 tracking-wide')

                        ui.label(str(n.get('port', 0))).classes('text-blue-400 font-mono w-full text-center font-bold text-xs')
                        
                        is_enable = n.get('enable', True)
                        with ui.row().classes('w-full justify-center items-center gap-1'):
                            color = "green" if is_enable else "red"; text = "启用" if is_enable else "停止"
                            ui.element('div').classes(f'w-2 h-2 rounded-full bg-{color}-500 shadow-[0_0_5px_rgba(0,0,0,0.5)]')
                            ui.label(text).classes(f'text-[10px] font-bold text-{color}-400')
                        
                        # 操作按钮 (深色适配)
                        with ui.row().classes('gap-2 justify-center w-full no-wrap opacity-60 group-hover:opacity-100 transition'):
                            btn_props = 'flat dense size=sm round'
                            link = n.get('_raw_link', '') if is_custom else generate_node_link(n, server_conf['url'])
                            if link: ui.button(icon='content_copy', on_click=lambda u=link: safe_copy_to_clipboard(u)).props(btn_props).tooltip('复制链接').classes('text-slate-400 hover:bg-slate-600 hover:text-blue-400')
                            
                            async def copy_detail_action(node_item=n):
                                host = server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
                                text = generate_detail_config(node_item, host)
                                if text: await safe_copy_to_clipboard(text)
                                else: ui.notify('该协议不支持生成明文配置', type='warning')
                            ui.button(icon='description', on_click=copy_detail_action).props(btn_props).tooltip('复制明文配置').classes('text-slate-400 hover:bg-slate-600 hover:text-orange-400')

                            if is_custom:
                                ui.button(icon='edit', on_click=lambda node=n: open_edit_custom_node(node)).props(btn_props).classes('text-blue-400 hover:bg-slate-600')
                                ui.button(icon='delete', on_click=lambda node=n: uninstall_and_delete(node)).props(btn_props).classes('text-red-400 hover:bg-slate-600')
                            elif has_manager_access:
                                async def on_edit_success(): ui.notify('修改成功'); await reload_and_refresh_ui()
                                ui.button(icon='edit', on_click=lambda i=n: open_inbound_dialog(mgr, i, on_edit_success)).props(btn_props).classes('text-blue-400 hover:bg-slate-600')
                                async def on_del_success(): ui.notify('删除成功'); await reload_and_refresh_ui()
                                ui.button(icon='delete', on_click=lambda i=n: delete_inbound_with_confirm(mgr, i['id'], i.get('remark',''), on_del_success)).props(btn_props).classes('text-red-400 hover:bg-slate-600')
                            else: ui.icon('lock', size='xs').classes('text-slate-600').tooltip('无权限')

        async def reload_and_refresh_ui():
            if mgr and hasattr(mgr, '_exec_remote_script'):
                try:
                    new_inbounds = await run.io_bound(lambda: asyncio.run(mgr.get_inbounds())) if not asyncio.iscoroutinefunction(mgr.get_inbounds) else await mgr.get_inbounds()
                    if new_inbounds is not None:
                        NODES_DATA[server_conf['url']] = new_inbounds
                        server_conf['_status'] = 'online'
                        await save_nodes_cache()
                except Exception as e: logger.error(f"SSH 强制刷新失败: {e}")
            else:
                try: await fetch_inbounds_safe(server_conf, force_refresh=True)
                except: pass
            render_node_list.refresh()

        REFRESH_CURRENT_NODES = reload_and_refresh_ui

        # ================== 辅助函数 (完整逻辑) ==================
        def open_edit_custom_node(node_data):
            with ui.dialog() as d, ui.card().classes('w-96 p-4'):
                ui.label('编辑节点备注').classes('text-lg font-bold mb-4')
                name_input = ui.input('节点名称', value=node_data.get('remark', '')).classes('w-full')
                async def save():
                    node_data['remark'] = name_input.value.strip()
                    await save_servers()
                    safe_notify('修改已保存', 'positive')
                    d.close()
                    render_node_list.refresh()
                with ui.row().classes('w-full justify-end mt-4'):
                    ui.button('取消', on_click=d.close).props('flat')
                    ui.button('保存', on_click=save).classes('bg-blue-600 text-white')
            d.open()

        async def uninstall_and_delete(node_data):
            with ui.dialog() as d, ui.card().classes('w-96 p-6'):
                with ui.row().classes('items-center gap-2 text-red-600 mb-2'):
                    ui.icon('delete_forever', size='md'); ui.label('卸载并清理环境').classes('font-bold text-lg')
                ui.label(f"节点: {node_data.get('remark')}").classes('text-sm font-bold text-gray-800')
                ui.label("即将执行以下操作：").classes('text-xs text-gray-500 mt-2')
                
                domain_to_del = None
                raw_link = node_data.get('_raw_link', '')
                if raw_link and '://' in raw_link:
                    try:
                        from urllib.parse import urlparse, parse_qs
                        query = urlparse(raw_link).query; params = parse_qs(query)
                        if 'sni' in params: domain_to_del = params['sni'][0]
                        elif 'host' in params: domain_to_del = params['host'][0]
                    except: pass
                
                with ui.column().classes('ml-2 gap-1 mt-1'):
                    ui.label('1. 停止 Xray 服务并清除残留进程').classes('text-xs text-gray-600')
                    ui.label('2. 删除 Xray 配置文件').classes('text-xs text-gray-600')
                    if domain_to_del and ADMIN_CONFIG.get('cf_root_domain') in domain_to_del:
                        ui.label(f'3. 🗑️ 自动删除 CF 解析: {domain_to_del}').classes('text-xs text-red-500 font-bold')
                    else: ui.label('3. 跳过 DNS 清理 (非托管域名)').classes('text-xs text-gray-400')

                async def start_uninstall():
                    d.close(); notification = ui.notification(message='正在执行卸载与清理...', timeout=0, spinner=True)
                    if domain_to_del:
                        cf = CloudflareHandler()
                        if cf.token and cf.root_domain and (cf.root_domain in domain_to_del):
                            ok, msg = await cf.delete_record_by_domain(domain_to_del)
                            if ok: safe_notify(f"☁️ {msg}", "positive")
                            else: safe_notify(f"⚠️ DNS 删除失败: {msg}", "warning")
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, XHTTP_UNINSTALL_SCRIPT))
                    notification.dismiss()
                    if success: safe_notify('✅ 服务已卸载，进程已清理', 'positive')
                    else: safe_notify(f'⚠️ SSH 卸载可能有残留: {output}', 'warning')
                    if 'custom_nodes' in server_conf and node_data in server_conf['custom_nodes']:
                        server_conf['custom_nodes'].remove(node_data)
                        await save_servers()
                    await reload_and_refresh_ui()

                with ui.row().classes('w-full justify-end mt-6 gap-2'):
                    ui.button('取消', on_click=d.close).props('flat color=grey')
                    ui.button('确认执行', color='red', on_click=start_uninstall).props('unelevated')
            d.open()

        # ================= 布局构建 =================
        # --- 顶部卡片 (深色) ---
        btn_3d_base = 'text-xs font-bold text-white rounded-lg px-4 py-2 border-b-4 active:border-b-0 active:translate-y-[4px] transition-all duration-150 shadow-sm'
        btn_blue = f'bg-blue-600 border-blue-800 hover:bg-blue-500 {btn_3d_base}'
        btn_green = f'bg-green-600 border-green-800 hover:bg-green-500 {btn_3d_base}'

        with ui.row().classes('w-full justify-between items-center bg-[#1e293b] p-4 rounded-xl border border-slate-700 border-b-[4px] border-b-slate-800 shadow-sm flex-shrink-0'):
            with ui.row().classes('items-center gap-4'):
                sys_icon = 'computer' if 'Oracle' in server_conf.get('name', '') else 'dns'
                with ui.element('div').classes('p-3 bg-[#0f172a] rounded-lg border border-slate-600'):
                    ui.icon(sys_icon, size='md').classes('text-blue-400')
                with ui.column().classes('gap-1'):
                    ui.label(server_conf.get('name', '未命名服务器')).classes('text-xl font-black text-slate-200 leading-tight tracking-tight')
                    with ui.row().classes('items-center gap-2'):
                        ip_addr = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').split(':')[0]
                        ui.label(ip_addr).classes('text-xs font-mono font-bold text-slate-400 bg-[#0f172a] px-2 py-0.5 rounded border border-slate-700')
                        
                        # ✨✨✨ 核心修复：引入实时刷新机制，判断探针真实在线状态 ✨✨✨
                        @ui.refreshable
                        def live_status_badge():
                            import time
                            is_online = False
                            now_ts = time.time()
                            
                            # 1. 优先判断探针实时心跳 (20秒内有更新算在线)
                            probe_cache = PROBE_DATA_CACHE.get(server_conf['url'])
                            if probe_cache and (now_ts - probe_cache.get('last_updated', 0) < 20):
                                is_online = True
                            # 2. 其次判断 API 探测结果
                            elif server_conf.get('_status') == 'online':
                                is_online = True
                                
                            if is_online:
                                ui.badge('Online', color='green').props('rounded outline size=xs')
                            else:
                                ui.badge('Offline', color='grey').props('rounded outline size=xs')

                        live_status_badge()
                        # 每 3 秒静默刷新一次这个徽章，页面不会闪烁
                        ui.timer(3.0, live_status_badge.refresh)
            
            with ui.row().classes('gap-3'):
                ui.button('一键部署 XHTTP', icon='rocket_launch', on_click=lambda: open_deploy_xhttp_dialog(server_conf, reload_and_refresh_ui)).props('unelevated').classes(btn_blue)
                ui.button('一键部署 Hy2', icon='bolt', on_click=lambda: open_deploy_hysteria_dialog(server_conf, reload_and_refresh_ui)).props('unelevated').classes(btn_blue)
                ui.button('一键部署 Snell', icon='security', on_click=lambda: open_deploy_snell_dialog(server_conf, reload_and_refresh_ui)).props('unelevated').classes(btn_blue)
                if has_manager_access:
                    async def on_add_success(): 
                        ui.notify('添加节点成功'); await reload_and_refresh_ui()
                    ui.button('新建 XUI 节点', icon='add', on_click=lambda: open_inbound_dialog(mgr, None, on_add_success)).props('unelevated').classes(btn_green)
                else:
                    ui.button('探针只读', icon='visibility', on_click=None).props('unelevated disabled').classes('bg-slate-700 text-slate-400 rounded-lg px-4 py-2 border-b-4 border-slate-800 text-xs font-bold opacity-70')

        ui.element('div').classes('h-4 flex-shrink-0')

        # --- 中间列表 (深色) ---
        with ui.card().classes('w-full flex-grow flex flex-col p-0 rounded-xl border border-slate-700 border-b-[4px] border-b-slate-800 shadow-sm overflow-hidden bg-[#1e293b]'):
            with ui.row().classes('w-full items-center justify-between p-3 bg-[#0f172a] border-b border-slate-700'):
                 ui.label('节点列表').classes('text-sm font-black text-slate-400 uppercase tracking-wide ml-1')
                 if server_conf.get('probe_installed') and server_conf.get('ssh_host'):
                     ui.badge('Root 模式', color='teal').props('outline rounded size=xs')
                 elif server_conf.get('user'):
                     ui.badge('API 托管模式', color='blue').props('outline rounded size=xs')

            # 表头
            with ui.element('div').classes('grid w-full gap-4 font-bold text-slate-500 border-b border-slate-700 pb-2 pt-2 px-2 text-xs uppercase tracking-wider bg-[#1e293b]').style(SINGLE_COLS_NO_PING):
                ui.label('节点名称').classes('text-left pl-2')
                for h in ['类型', '流量', '协议', '端口', '状态', '操作']: ui.label(h).classes('text-center')

            # 滚动区
            with ui.scroll_area().classes('w-full flex-grow bg-[#0f172a] p-1'): 
                await render_node_list()

        ui.element('div').classes('h-6 flex-shrink-0') 


        # --- 第三段：SSH 窗口  ---
        with ui.card().classes('w-full h-[750px] flex-shrink-0 p-0 rounded-xl border border-gray-300 border-b-[4px] border-b-gray-400 shadow-lg overflow-hidden bg-slate-900 flex flex-col'):
            ssh_state = {'active': False, 'instance': None}

            def render_ssh_area():
                with ui.row().classes('w-full h-10 bg-slate-800 items-center justify-between px-4 flex-shrink-0 border-b border-slate-700'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('terminal').classes('text-white text-sm')
                        ui.label(f"SSH Console: {server_conf.get('ssh_user','root')}@{server_conf.get('ssh_host') or 'IP'}").classes('text-gray-300 text-xs font-mono font-bold')
                    if ssh_state['active']: ui.button(icon='link_off', on_click=stop_ssh).props('flat dense round color=red size=sm').tooltip('断开连接')
                    else: ui.label('Disconnected').classes('text-[10px] text-gray-500')

                box_cls = 'w-full flex-grow bg-[#0f0f0f] overflow-hidden'
                if not ssh_state['active']: box_cls += ' flex justify-center items-center'
                else: box_cls += ' relative block'

                terminal_box = ui.element('div').classes(box_cls)
                with terminal_box:
                    if not ssh_state['active']:
                        with ui.column().classes('items-center gap-4'):
                            ui.icon('dns', size='4rem').classes('text-gray-800')
                            ui.label('安全终端已就绪').classes('text-gray-600 text-sm font-bold')
                            ui.button('立即连接 SSH', icon='login', on_click=start_ssh).classes('bg-blue-600 text-white font-bold px-6 py-2 rounded-lg border-b-4 border-blue-800 active:border-b-0 active:translate-y-[2px] transition-all')
                    else:
                        ssh = WebSSH(terminal_box, server_conf)
                        ssh_state['instance'] = ssh
                        ui.timer(0.1, lambda: asyncio.create_task(ssh.connect()), once=True)

                # --- 快捷命令区 ---
                with ui.row().classes('w-full min-h-[60px] bg-slate-800 border-t border-slate-700 px-4 py-4 gap-3 items-center flex-wrap'):
                    ui.label('快捷命令:').classes('text-xs font-bold text-gray-400 mr-2')
                    
                    commands = ADMIN_CONFIG.get('quick_commands', [])
                    for cmd_obj in commands:
                        cmd_name = cmd_obj.get('name', '未命名')
                        cmd_text = cmd_obj.get('cmd', '')
                        with ui.element('div').classes('flex items-center bg-slate-700 rounded overflow-hidden border-b-2 border-slate-900 transition-all active:border-b-0 active:translate-y-[2px] hover:bg-slate-600'):
                            ui.button(cmd_name, on_click=lambda c=cmd_text: exec_quick_cmd(c)).props('unelevated').classes('bg-transparent text-[11px] font-bold text-slate-300 px-3 py-1.5 hover:text-white rounded-none')
                            ui.element('div').classes('w-[1px] h-4 bg-slate-500 opacity-50')
                            ui.button(icon='settings', on_click=lambda c=cmd_obj: open_cmd_editor(c)).props('flat dense size=xs').classes('text-slate-400 hover:text-white px-1 py-1.5 rounded-none')

                    ui.button(icon='add', on_click=lambda: open_cmd_editor(None)).props('flat dense round size=sm color=green').tooltip('添加常用命令')

            async def start_ssh():
                ssh_state['active'] = True
                render_card_content()

            async def stop_ssh():
                if ssh_state['instance']:
                    ssh_state['instance'].close()
                    ssh_state['instance'] = None
                ssh_state['active'] = False
                render_card_content()

            def exec_quick_cmd(cmd_text):
                if ssh_state['instance'] and ssh_state['instance'].active:
                    ssh_state['instance'].channel.send(cmd_text + "\n")
                    ui.notify(f"已发送: {cmd_text[:20]}...", type='positive', position='bottom')
                else:
                    ui.notify("请先连接 SSH", type='warning', position='bottom')

            def open_cmd_editor(existing_cmd=None):
                with ui.dialog() as d, ui.card().classes('w-96 p-5 bg-[#1e293b] border border-slate-600 shadow-2xl'):
                    with ui.row().classes('w-full justify-between items-center mb-4'):
                        ui.label('管理快捷命令').classes('text-lg font-bold text-white')
                        ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

                    name_input = ui.input('按钮名称', value=existing_cmd['name'] if existing_cmd else '').classes('w-full mb-3').props('outlined dense dark bg-color="slate-800"')
                    cmd_input = ui.textarea('执行命令', value=existing_cmd['cmd'] if existing_cmd else '').classes('w-full mb-4').props('outlined dense dark bg-color="slate-800" rows=4')
                    
                    async def save():
                        name = name_input.value.strip(); cmd = cmd_input.value.strip()
                        if not name or not cmd: return ui.notify("内容不能为空", type='negative')
                        if 'quick_commands' not in ADMIN_CONFIG: ADMIN_CONFIG['quick_commands'] = []
                        if existing_cmd: existing_cmd['name'] = name; existing_cmd['cmd'] = cmd
                        else: ADMIN_CONFIG['quick_commands'].append({'name': name, 'cmd': cmd, 'id': str(uuid.uuid4())[:8]})
                        await save_admin_config()
                        d.close()
                        render_card_content()
                        ui.notify("命令已保存", type='positive')

                    async def delete_current():
                        if existing_cmd and 'quick_commands' in ADMIN_CONFIG:
                            ADMIN_CONFIG['quick_commands'].remove(existing_cmd)
                            await save_admin_config()
                            d.close()
                            render_card_content()
                            ui.notify("命令已删除", type='positive')

                    with ui.row().classes('w-full justify-between mt-2'):
                        if existing_cmd: ui.button('删除', icon='delete', color='red', on_click=delete_current).props('flat dense')
                        else: ui.element('div')
                        ui.button('保存', icon='save', on_click=save).classes('bg-blue-600 text-white font-bold rounded-lg border-b-4 border-blue-800 active:border-b-0 active:translate-y-[2px]')

                d.open()

            def render_card_content():
                ssh_wrapper.clear()
                with ssh_wrapper:
                    render_ssh_area()

            ssh_wrapper = ui.column().classes('w-full h-full p-0 gap-0')
            render_card_content()

        # 如果配置了面板账号，且本地缓存为空，则在进入页面 0.2 秒后自动触发后台拉取
        if has_manager_access and not NODES_DATA.get(server_conf['url']):
            ui.timer(0.2, lambda: asyncio.create_task(reload_and_refresh_ui()), once=True)

            
# ================= 聚合视图 (局部静默刷新 + 自动状态更新) =================
# 全局字典，用于存储每行 UI 元素的引用，以便局部更新
# 结构: { 'server_url': { 'row_el': row_element, 'status_icon': icon, 'status_label': label, ... } }
UI_ROW_REFS = {} 
CURRENT_VIEW_STATE = {'scope': 'DASHBOARD', 'data': None}
# ================= 点击自定义节点显示详情 =================
def show_custom_node_info(node):
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm'):
        ui.label(node.get('remark', '节点详情')).classes('text-lg font-bold mb-2')
        
        # 获取链接
        link = node.get('_raw_link') or node.get('link') or "无法获取链接"
        
        # 显示链接区域
        with ui.row().classes('w-full bg-gray-100 p-3 rounded break-all font-mono text-xs mb-4'):
            ui.label(link)
            
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('复制', icon='content_copy', on_click=lambda: [safe_copy_to_clipboard(link), d.close()])
            ui.button('关闭', on_click=d.close).props('flat')
    d.open()
    

# ================= 1. 列表单行渲染 (✅ 深色修复版) =================
def draw_row(srv, node, css_style, use_special_mode, is_first=True):
    # ✨ 修改点：背景改为 bg-[#1e293b], 边框 border-slate-700
    card_cls = 'grid w-full gap-4 py-3 px-4 items-center group relative bg-[#1e293b] rounded-xl border border-slate-700 border-b-[3px] shadow-sm transition-all duration-150 ease-out hover:shadow-md hover:border-blue-500 hover:bg-[#252f45] hover:-translate-y-[1px] mb-2'
    
    with ui.element('div').classes(card_cls).style(css_style):
        # 1. 服务器名
        srv_name = srv.get('name', '未命名')
        if not is_first: ui.label(srv_name).classes('text-xs text-slate-700 truncate w-full text-left pl-2 font-mono')
        else: ui.label(srv_name).classes('text-xs text-slate-400 font-bold truncate w-full text-left pl-2 font-mono group-hover:text-white')

        # 无节点情况
        if not node:
            is_probe = srv.get('probe_installed', False)
            msg = '同步中...' if not is_probe else '无节点配置'
            ui.label(msg).classes('font-bold truncate text-slate-600 text-xs italic')
            ui.label('--').classes('text-center text-slate-700')
            ui.label('--').classes('text-center text-slate-700')
            ui.label('UNK').classes('text-center text-slate-700 font-bold text-[10px]')
            ui.label('--').classes('text-center text-slate-700')
            if not use_special_mode: ui.element('div')
            with ui.row().classes('gap-1 justify-center w-full no-wrap'):
                 ui.button(icon='settings', on_click=lambda _, s=srv: refresh_content('SINGLE', s)).props('flat dense size=sm round color=grey')
            return

        # 2. 备注 (Slate 文字)
        remark = node.get('ps') or node.get('remark') or '未命名节点'
        ui.label(remark).classes('font-bold truncate w-full text-left pl-2 text-slate-300 text-sm group-hover:text-blue-300')

        # 3. 分组/IP
        if use_special_mode:
            with ui.row().classes('w-full justify-center items-center gap-1.5 no-wrap'):
                is_online = srv.get('_status') == 'online'
                color = 'text-green-500' if is_online else 'text-red-500'
                if not srv.get('probe_installed') and not node.get('_is_custom'): color = 'text-orange-400'
                ui.icon('bolt').classes(f'{color} text-sm')
                display_ip = get_real_ip_display(srv['url'])
                # IP 背景: Slate-900
                ip_lbl = ui.label(display_ip).classes('text-[10px] font-mono text-slate-400 font-bold bg-[#0f172a] px-1.5 py-0.5 rounded select-all border border-slate-700')
                bind_ip_label(srv['url'], ip_lbl)
        else:
            group_display = srv.get('group', '默认分组')
            if group_display in ['默认分组', '自动注册', '未分组', '自动导入']:
                try:
                    detected = detect_country_group(srv.get('name', ''), None)
                    if detected: group_display = detected
                except: pass
            ui.label(group_display).classes('text-xs font-bold text-slate-400 w-full text-center truncate bg-[#0f172a] px-2 py-0.5 rounded-full border border-slate-700')

        # 4. 流量
        if node.get('_is_custom'): ui.label('-').classes('text-xs text-slate-600 w-full text-center font-mono')
        else:
            traffic = sum([node.get('up', 0), node.get('down', 0)])
            ui.label(format_bytes(traffic)).classes('text-xs text-blue-400 w-full text-center font-mono font-bold')

        # 5. 协议
        proto = str(node.get('protocol', 'unk')).upper()
        if 'HYSTERIA' in proto: proto = 'HY2'
        if 'SHADOWSOCKS' in proto: proto = 'SS'
        proto_color = 'text-slate-500'
        if 'HY2' in proto: proto_color = 'text-purple-400'
        elif 'VLESS' in proto: proto_color = 'text-blue-400'
        elif 'VMESS' in proto: proto_color = 'text-green-400'
        elif 'TROJAN' in proto: proto_color = 'text-orange-400'
        ui.label(proto).classes(f'text-[11px] font-extrabold w-full text-center {proto_color} tracking-wide')

        # 6. 端口
        port_val = str(node.get('port', 0))
        ui.label(port_val).classes('text-slate-400 font-mono w-full text-center font-bold text-xs')

        # 7. 状态
        if not use_special_mode:
            with ui.element('div').classes('flex justify-center w-full'):
                is_enable = node.get('enable', True)
                dot_cls = "bg-green-500 shadow-[0_0_6px_rgba(34,197,94,0.6)]" if is_enable else "bg-red-500 shadow-[0_0_6px_rgba(239,68,68,0.6)]"
                ui.element('div').classes(f'w-2 h-2 rounded-full {dot_cls}')

        # 8. 操作按钮
        with ui.row().classes('gap-1 justify-center w-full no-wrap'):
            async def copy_link(n=node, s=srv):
                link = n.get('_raw_link') or n.get('link')
                if not link: link = generate_node_link(n, s['url'])
                await safe_copy_to_clipboard(link)

            ui.button(icon='content_copy', on_click=copy_link).props('flat dense size=sm round').tooltip('复制链接').classes('text-slate-500 hover:text-blue-400 hover:bg-slate-700')

            async def copy_detail():
                host = srv['url'].split('://')[-1].split(':')[0]
                text = generate_detail_config(node, host)
                if text: await safe_copy_to_clipboard(text)
                else: ui.notify('不支持生成配置', type='warning')

            ui.button(icon='description', on_click=copy_detail).props('flat dense size=sm round').tooltip('复制明文配置').classes('text-slate-500 hover:text-orange-400 hover:bg-slate-700')

            ui.button(icon='settings', on_click=lambda _, s=srv: refresh_content('SINGLE', s)).props('flat dense size=sm round').tooltip('管理服务器').classes('text-slate-500 hover:text-white hover:bg-slate-700')


# ================= 2. 列表视图容器 (✅ 深色修复版) =================
async def render_aggregated_view(server_list, show_ping=False, token=None, initial_page=1):
    parent_client = ui.context.client
    list_container = ui.column().classes('w-full gap-3 p-1')
    
    cols_ping = 'grid-template-columns: 2fr 2fr 1.5fr 1.5fr 1fr 1fr 1.5fr' 
    cols_no_ping = 'grid-template-columns: 2fr 2fr 1.5fr 1.5fr 1fr 1fr 0.5fr 1.5fr'
    
    try:
        is_all_servers = (len(server_list) == len(SERVERS_CACHE) and not show_ping)
        use_special_mode = is_all_servers or show_ping
        current_css = COLS_SPECIAL_WITH_PING if use_special_mode else COLS_NO_PING
    except:
        current_css = cols_ping if show_ping else cols_no_ping

    PAGE_SIZE = 30
    total_items = len(server_list)
    total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
    if initial_page > total_pages: initial_page = 1
    if initial_page < 1: initial_page = 1

    def render_page(page_num):
        list_container.clear()
        if 'CURRENT_VIEW_STATE' in globals(): CURRENT_VIEW_STATE['page'] = page_num

        with list_container:
            # 顶部翻页
            with ui.row().classes('w-full justify-between items-center px-2 mb-2'):
                ui.label(f'共 {total_items} 台服务器 (第 {page_num}/{total_pages} 页)').classes('text-xs text-slate-400 font-bold')
                if total_pages > 1:
                    ui.pagination(1, total_pages, direction_links=True, value=page_num) \
                        .props('dense flat color=blue text-color=slate-400 active-text-color=white') \
                        .on_value_change(lambda e: handle_pagination_click(e.value))

            # ✨ 修改点：表头背景 bg-[#1e293b], 边框 border-slate-700
            with ui.element('div').classes('grid w-full gap-4 font-bold text-slate-500 border-b border-slate-700 pb-2 px-6 mb-1 uppercase tracking-wider text-xs bg-[#1e293b] rounded-t-lg pt-3').style(current_css):
                ui.label('服务器').classes('text-left pl-1')
                ui.label('节点名称').classes('text-left pl-1')
                if use_special_mode: ui.label('在线状态 / IP').classes('text-center')
                else: ui.label('所在组').classes('text-center')
                ui.label('已用流量').classes('text-center')
                ui.label('协议').classes('text-center')
                ui.label('端口').classes('text-center')
                if not use_special_mode: ui.label('状态').classes('text-center')
                ui.label('操作').classes('text-center')
            
            # 数据渲染
            start_idx = (page_num - 1) * PAGE_SIZE
            end_idx = start_idx + PAGE_SIZE
            current_page_data = server_list[start_idx:end_idx]

            for srv in current_page_data:
                panel_n = NODES_DATA.get(srv['url'], []) or []
                custom_n = srv.get('custom_nodes', []) or []
                for cn in custom_n: cn['_is_custom'] = True
                all_nodes = panel_n + custom_n
                
                if not all_nodes:
                    draw_row(srv, None, current_css, use_special_mode, is_first=True)
                    continue

                for index, node in enumerate(all_nodes):
                    draw_row(srv, node, current_css, use_special_mode, is_first=(index==0))
            
            # 底部翻页
            if total_pages > 1:
                with ui.row().classes('w-full justify-center mt-4'):
                    ui.pagination(1, total_pages, direction_links=True, value=page_num) \
                        .props('dense flat color=blue text-color=slate-400 active-text-color=white') \
                        .on_value_change(lambda e: handle_pagination_click(e.value))



    # ================= 🚀 核心逻辑：翻页事件处理 =================
    def handle_pagination_click(new_page):
        try: target_page = int(new_page)
        except: return 

        current_scope = CURRENT_VIEW_STATE.get('scope', 'ALL')
        current_data = CURRENT_VIEW_STATE.get('data', None)

        print(f"👉 [Debug] 翻页至: {target_page} (自然浏览)", flush=True)

        # 使用父级上下文包裹异步任务，防止 Context Lost
        with parent_client:
            asyncio.create_task(
                refresh_content(
                    scope=current_scope,
                    data=current_data,
                    # 🛑 [关键修改]：设置为 False
                    # 这告诉 refresh_content：“我是自然翻页，请先检查是否有缓存且未过期”
                    force_refresh=False, 
                    sync_name_action=True,
                    page_num=target_page,
                    manual_client=parent_client
                )
            )

    # 初次渲染
    render_page(initial_page)



# ================= 核心：前端轮询用的纯数据接口 (API) =================
@app.get('/api/dashboard/live_data')
def get_dashboard_live_data():
    data = calculate_dashboard_data()
    return data if data else {"error": "Calculation failed"}


# ================= 辅助：统一数据计算逻辑  =================
def calculate_dashboard_data():
    """
    计算并返回当前所有面板数据。
    逻辑调整：优先使用 Root 探针的流量和状态，没有探针才使用 X-UI 数据。
    """
    try:
        total_servers = len(SERVERS_CACHE)
        online_servers = 0
        total_nodes = 0
        total_traffic_bytes = 0
        
        server_traffic_map = {}
        from collections import Counter
        country_counter = Counter()
        
        import time
        now_ts = time.time()

        for s in SERVERS_CACHE:
            # 1. 获取基础数据
            res = NODES_DATA.get(s['url'], []) or []     # X-UI 节点数据
            custom = s.get('custom_nodes', []) or []     # 自定义节点
            probe_data = PROBE_DATA_CACHE.get(s['url'])  # 探针数据
            
            name = s.get('name', '未命名')
            
            # --- 统计区域 ---
            try:
                region_str = detect_country_group(name, s)
                if not region_str or region_str.strip() == "🏳️": region_str = "🏳️ 未知区域"
            except: region_str = "🏳️ 未知区域"
            country_counter[region_str] += 1

            # --- A. 计算流量 (优先探针) ---
            srv_traffic = 0
            use_probe_traffic = False
            
            if s.get('probe_installed') and probe_data:
                # 优先：读取网卡总流量 (入站+出站)
                # 注意：这里假设探针返回的是累积总量
                t_in = probe_data.get('net_total_in', 0)
                t_out = probe_data.get('net_total_out', 0)
                if t_in > 0 or t_out > 0:
                    srv_traffic = t_in + t_out
                    use_probe_traffic = True
            
            # 兜底：如果没有探针数据，则累加 X-UI 节点流量
            if not use_probe_traffic and res:
                for n in res:
                    srv_traffic += int(n.get('up', 0)) + int(n.get('down', 0))

            total_traffic_bytes += srv_traffic
            server_traffic_map[name] = srv_traffic

            # --- B. 判断在线状态 (优先探针心跳) ---
            is_online = False
            
            # 1. 探针判定 (心跳在 60秒内算在线)
            if s.get('probe_installed') and probe_data:
                if now_ts - probe_data.get('last_updated', 0) < 60:
                    is_online = True
            
            # 2. X-UI 判定 (如果探针没在线，看下 X-UI API 是否通了)
            if not is_online:
                # 如果缓存里有节点数据，或者状态标记为 online (由 fetch_inbounds_safe 设置)
                if res or s.get('_status') == 'online':
                    is_online = True
            
            if is_online:
                online_servers += 1

            # --- C. 统计节点数 (这个始终来自配置) ---
            if res: total_nodes += len(res)
            if custom: total_nodes += len(custom)

        # 构建图表数据
        sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:15]
        bar_names = [x[0] for x in sorted_traffic]
        bar_values = [round(x[1]/(1024**3), 2) for x in sorted_traffic]

        chart_data = []
        sorted_regions = country_counter.most_common()
        
        # 饼图逻辑 (Top 5 + 其他)
        if len(sorted_regions) > 5:
            top_5 = sorted_regions[:5]
            others_count = sum(item[1] for item in sorted_regions[5:])
            for k, v in top_5: chart_data.append({'name': f"{k} ({v})", 'value': v})
            if others_count > 0: chart_data.append({'name': f"🏳️ 其他 ({others_count})", 'value': others_count})
        else:
            for k, v in sorted_regions: chart_data.append({'name': f"{k} ({v})", 'value': v})

        if not chart_data: chart_data = [{'name': '暂无数据', 'value': 0}]

        return {
            "servers": f"{online_servers}/{total_servers}",
            "nodes": str(total_nodes),
            "traffic": f"{total_traffic_bytes/(1024**3):.2f} GB",
            "subs": str(len(SUBS_CACHE)),
            "bar_chart": {"names": bar_names, "values": bar_values},
            "pie_chart": chart_data
        }
    except Exception as e:
        print(f"Error calculating dashboard data: {e}")
        import traceback; traceback.print_exc()
        return None

# ================= 核心：静默刷新 UI 数据  =================
async def refresh_dashboard_ui():
    try:
        # 如果仪表盘还没打开（引用是空的），直接跳过
        if not DASHBOARD_REFS.get('servers'): return

        # ✨ 直接调用通用计算函数，确保与 API 逻辑绝对一致
        data = calculate_dashboard_data()
        if not data: return

        # --- 更新 UI 文字 ---
        if DASHBOARD_REFS.get('servers'): DASHBOARD_REFS['servers'].set_text(data['servers'])
        if DASHBOARD_REFS.get('nodes'): DASHBOARD_REFS['nodes'].set_text(data['nodes'])
        if DASHBOARD_REFS.get('traffic'): DASHBOARD_REFS['traffic'].set_text(data['traffic'])
        if DASHBOARD_REFS.get('subs'): DASHBOARD_REFS['subs'].set_text(data['subs'])

        # --- 更新 柱状图 ---
        if DASHBOARD_REFS.get('bar_chart'):
            DASHBOARD_REFS['bar_chart'].options['xAxis']['data'] = data['bar_chart']['names']
            DASHBOARD_REFS['bar_chart'].options['series'][0]['data'] = data['bar_chart']['values']
            DASHBOARD_REFS['bar_chart'].update()

        # --- 更新 饼图 ---
        if DASHBOARD_REFS.get('pie_chart'):
            DASHBOARD_REFS['pie_chart'].options['series'][0]['data'] = data['pie_chart']
            DASHBOARD_REFS['pie_chart'].update()

        # --- 更新地图数据 ---
        globe_data_list = []
        seen_locations = set()
        for s in SERVERS_CACHE:
            lat, lon = None, None
            if 'lat' in s and 'lon' in s: lat, lon = s['lat'], s['lon']
            else:
                coords = get_coords_from_name(s.get('name', ''))
                if coords: lat, lon = coords[0], coords[1]
            
            if lat is not None and lon is not None:
                coord_key = (round(lat, 2), round(lon, 2))
                if coord_key not in seen_locations:
                    seen_locations.add(coord_key)
                    flag_only = "📍"
                    try:
                        full_group = detect_country_group(s.get('name', ''), s)
                        flag_only = full_group.split(' ')[0]
                    except: pass
                    globe_data_list.append({'lat': lat, 'lon': lon, 'name': flag_only})
        
        if CURRENT_VIEW_STATE.get('scope') == 'DASHBOARD':
            import json
            json_data = json.dumps(globe_data_list, ensure_ascii=False)
            ui.run_javascript(f'if(window.updateDashboardMap) window.updateDashboardMap({json_data});')

    except Exception as e:
        logger.error(f"UI 更新失败: {e}")
        
# ================= 核心：仪表盘主视图渲染  =================
async def load_dashboard_stats():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'DASHBOARD'
    CURRENT_VIEW_STATE['data'] = None
    
    await asyncio.sleep(0.1)
    content_container.clear()
    content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
    
    # 1. 计算初始统计数据
    init_data = calculate_dashboard_data()
    if not init_data:
        init_data = {
            "servers": "0/0", "nodes": "0", "traffic": "0 GB", "subs": "0",
            "bar_chart": {"names": [], "values": []}, "pie_chart": []
        }

    # 强制重算区域数据 (Top 5 + 其他)
    # 这是页面加载时显示的正确数据
    group_buckets = {}
    for s in SERVERS_CACHE:
        # 优先使用保存的分组，如果是特殊分组则重新检测
        g_name = s.get('group')
        if not g_name or g_name in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区']:
            g_name = detect_country_group(s.get('name', ''))
        
        if g_name not in group_buckets: group_buckets[g_name] = 0
        group_buckets[g_name] += 1
    
    # 转为列表并排序
    all_regions = [{'name': k, 'value': v} for k, v in group_buckets.items()]
    all_regions.sort(key=lambda x: x['value'], reverse=True)
    
    # 只取前 5 名，剩下的合并为 "🏳️ 其他地区"
    if len(all_regions) > 5:
        top_5 = all_regions[:5]
        others_count = sum(item['value'] for item in all_regions[5:])
        top_5.append({'name': '🏳️ 其他地区', 'value': others_count})
        pie_data_final = top_5
    else:
        pie_data_final = all_regions

    # 覆盖 init_data，确保初始显示正确
    init_data['pie_chart'] = pie_data_final

    with content_container:
        ui.run_javascript("""
        if (window.dashInterval) clearInterval(window.dashInterval);
        window.dashInterval = setInterval(async () => {
            if (document.hidden) return;
            try {
                const res = await fetch('/api/dashboard/live_data');
                if (!res.ok) return;
                const data = await res.json();
                if (data.error) return;

                // 1. 刷新顶部数字 (保留)
                const ids = ['stat-servers', 'stat-nodes', 'stat-traffic', 'stat-subs'];
                const keys = ['servers', 'nodes', 'traffic', 'subs'];
                ids.forEach((id, i) => {
                    const el = document.getElementById(id);
                    if (el) el.innerText = data[keys[i]];
                });

                // 2. 刷新柱状图 (流量是实时变的，必须保留)
                const barDom = document.getElementById('chart-bar');
                if (barDom) {
                    const chart = echarts.getInstanceByDom(barDom);
                    if (chart) {
                        chart.setOption({
                            xAxis: { data: data.bar_chart.names },
                            series: [{ data: data.bar_chart.values }]
                        });
                    }
                }
                
                // ✂️ [已彻底删除] 饼图更新逻辑
                // 这里原本有 update chart-pie 的代码，现在删掉了。
                // 无论后台发来什么数据，饼图永远保持 Python 刚开始画的样子。
                
            } catch (e) {}
        }, 3000);
        """)

        ui.label('系统概览').classes('text-3xl font-bold mb-4 text-slate-200 tracking-tight')
        
        # === A. 顶部统计卡片 (渐变色保持不变，文字适配) ===
        with ui.row().classes('w-full gap-4 mb-6 items-stretch'):
            def create_stat_card(ref_key, dom_id, title, sub_text, icon, gradient, init_val):
                with ui.card().classes(f'flex-1 p-4 shadow-lg border border-white/10 text-white {gradient} rounded-xl relative overflow-hidden'):
                    ui.element('div').classes('absolute -right-4 -top-4 w-24 h-24 bg-white opacity-10 rounded-full blur-xl')
                    with ui.row().classes('items-center justify-between w-full relative z-10'):
                        with ui.column().classes('gap-0'):
                            ui.label(title).classes('opacity-90 text-[11px] font-bold uppercase tracking-wider')
                            DASHBOARD_REFS[ref_key] = ui.label(init_val).props(f'id={dom_id}').classes('text-3xl font-black tracking-tight my-1 drop-shadow-md')
                            ui.label(sub_text).classes('opacity-70 text-[10px] font-medium')
                        ui.icon(icon).classes('text-4xl opacity-80 drop-shadow-sm')

            create_stat_card('servers', 'stat-servers', '在线服务器', 'Online / Total', 'dns', 'bg-gradient-to-br from-blue-600 to-indigo-800', init_data['servers'])
            create_stat_card('nodes', 'stat-nodes', '节点总数', 'Active Nodes', 'hub', 'bg-gradient-to-br from-purple-600 to-fuchsia-800', init_data['nodes'])
            create_stat_card('traffic', 'stat-traffic', '总流量消耗', 'Total Usage', 'bolt', 'bg-gradient-to-br from-emerald-600 to-teal-800', init_data['traffic'])
            create_stat_card('subs', 'stat-subs', '订阅配置', 'Subscriptions', 'rss_feed', 'bg-gradient-to-br from-orange-500 to-red-700', init_data['subs'])

        # === B. 图表区域 (修改背景为深色) ===
        with ui.row().classes('w-full gap-6 mb-6 flex-wrap xl:flex-nowrap items-stretch'):
            # 深色卡片样式
            chart_card_cls = 'w-full p-5 shadow-xl border border-slate-700 rounded-xl bg-[#1e293b] flex flex-col'
            
            # 流量排行
            with ui.card().classes(f'xl:w-2/3 {chart_card_cls}'):
                with ui.row().classes('w-full justify-between items-center mb-4 border-b border-slate-700 pb-2'):
                    ui.label('📊 服务器流量排行 (GB)').classes('text-base font-bold text-slate-200')
                    with ui.row().classes('items-center gap-1 px-2 py-0.5 bg-green-900/30 rounded-full border border-green-500/30'):
                        ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse')
                        ui.label('Live').classes('text-[10px] font-bold text-green-400')
                
                # ECharts 配置：坐标轴颜色改为灰色，以适应深底
                DASHBOARD_REFS['bar_chart'] = ui.echart({
                    'tooltip': {'trigger': 'axis'},
                    'grid': {'left': '2%', 'right': '3%', 'bottom': '2%', 'top': '10%', 'containLabel': True},
                    'xAxis': {'type': 'category', 'data': init_data['bar_chart']['names'], 'axisLabel': {'interval': 0, 'rotate': 30, 'color': '#94a3b8', 'fontSize': 10}}, # text-slate-400
                    'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'type': 'dashed', 'color': '#334155'}}, 'axisLabel': {'color': '#94a3b8'}},
                    'series': [{'type': 'bar', 'data': init_data['bar_chart']['values'], 'barWidth': '40%', 'itemStyle': {'borderRadius': [3, 3, 0, 0], 'color': '#6366f1'}}]
                }).classes('w-full h-64').props('id=chart-bar')

            # 区域分布
            with ui.card().classes(f'xl:w-1/3 {chart_card_cls}'):
                ui.label('🌏 服务器分布').classes('text-base font-bold text-slate-200 mb-4 border-b border-slate-700 pb-2')
                color_palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#6366f1', '#ec4899', '#14b8a6', '#f97316']
                
                DASHBOARD_REFS['pie_chart'] = ui.echart({
                    'tooltip': {'trigger': 'item', 'formatter': '{b}: <br/><b>{c} 台</b> ({d}%)'},
                    'legend': {'bottom': '0%', 'left': 'center', 'icon': 'circle', 'itemGap': 10, 'textStyle': {'color': '#e2e8f0', 'fontSize': 11}}, # text-slate-200
                    'color': color_palette,
                    'series': [{
                        'name': '服务器分布', 
                        'type': 'pie', 
                        'radius': ['45%', '75%'],
                        'center': ['50%', '45%'],
                        'avoidLabelOverlap': False,
                        'itemStyle': {'borderRadius': 4, 'borderColor': '#1e293b', 'borderWidth': 2}, # 边框颜色配合背景
                        'label': { 'show': False, 'position': 'center' },
                        'emphasis': {'label': {'show': True, 'fontSize': 16, 'fontWeight': 'bold', 'color': '#fff'}, 'scale': True, 'scaleSize': 5},
                        'labelLine': { 'show': False },
                        'data': init_data['pie_chart']
                    }]
                }).classes('w-full h-64').props('id=chart-pie')

        # === C. 底部地图区域 ===
        with ui.row().classes('w-full gap-6 mb-6'):
            with ui.card().classes('w-full p-0 shadow-md border-none rounded-xl bg-slate-900 overflow-hidden relative'):
                with ui.row().classes('w-full px-6 py-3 bg-slate-800/50 border-b border-gray-700 justify-between items-center z-10 relative'):
                    with ui.row().classes('gap-2 items-center'):
                        ui.icon('public', color='blue-4').classes('text-xl')
                        ui.label('全球节点实景 (Global View)').classes('text-base font-bold text-white')
                    DASHBOARD_REFS['map_info'] = ui.label('Live Rendering').classes('text-[10px] text-gray-400')

                # 1. 准备旧版简单数据
                globe_data_list = []
                seen_locations = set()
                total_server_count = len(SERVERS_CACHE)
                
                for s in SERVERS_CACHE:
                    lat, lon = None, None
                    if 'lat' in s: lat, lon = s['lat'], s['lon']
                    else:
                        c = get_coords_from_name(s.get('name', ''))
                        if c: lat, lon = c[0], c[1]
                    if lat:
                        k = (round(lat,2), round(lon,2))
                        if k not in seen_locations:
                            seen_locations.add(k)
                            flag = "📍"
                            try: flag = detect_country_group(s['name']).split(' ')[0]
                            except: pass
                            globe_data_list.append({'lat': lat, 'lon': lon, 'name': flag})

                import json
                json_data = json.dumps(globe_data_list, ensure_ascii=False)
                
                # 2. 渲染容器
                ui.html(GLOBE_STRUCTURE, sanitize=False).classes('w-full h-[650px] overflow-hidden')
                
                # 3. 注入数据和 JS
                ui.run_javascript(f'window.DASHBOARD_DATA = {json_data};')
                ui.run_javascript(GLOBE_JS_LOGIC)
                
# ================= 全能批量编辑器 (完整修复 + 深色适配版) =================
class BulkEditor:
    def __init__(self, target_servers, title="批量管理"):
        self.all_servers = target_servers
        self.title = title
        self.selected_urls = set()
        self.ui_rows = {} 
        self.dialog = None

    def open(self):
        # 主弹窗容器: bg-[#1e293b] border-slate-700
        with ui.dialog() as d, ui.card().classes('w-full max-w-4xl h-[85vh] flex flex-col p-0 overflow-hidden bg-[#1e293b] border border-slate-700 shadow-2xl'):
            self.dialog = d
            
            # --- 1. 顶部标题 ---
            with ui.row().classes('w-full justify-between items-center p-4 bg-[#0f172a] border-b border-slate-700 flex-shrink-0'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('edit_note', color='primary').classes('text-xl')
                    ui.label(self.title).classes('text-lg font-bold text-slate-200')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

            # --- 2. 工具栏 ---
            with ui.column().classes('w-full p-4 gap-3 border-b border-slate-700 bg-[#1e293b] flex-shrink-0'):
                self.search_input = ui.input(placeholder='🔍 搜索服务器名称...').props('outlined dense clearable dark').classes('w-full')
                self.search_input.on_value_change(self.on_search)
                
                with ui.row().classes('w-full justify-between items-center'):
                    with ui.row().classes('gap-2'):
                        ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense size=sm color=blue')
                        ui.button('全不选', on_click=lambda: self.toggle_all(False)).props('flat dense size=sm color=grey')
                        self.count_label = ui.label('已选: 0').classes('text-xs font-bold text-slate-400 self-center ml-2')
            
            # --- 3. 列表区域 ---
            with ui.scroll_area().classes('w-full flex-grow p-2 bg-[#0f172a]'):
                with ui.column().classes('w-full gap-1') as self.list_container:
                    if not self.all_servers:
                        ui.label('当前组无服务器').classes('w-full text-center text-slate-500 mt-10')
                    
                    try: sorted_srv = sorted(self.all_servers, key=lambda x: smart_sort_key(x))
                    except: sorted_srv = self.all_servers

                    for s in sorted_srv:
                        # 列表行: bg-[#1e293b] -> hover:bg-slate-700
                        with ui.row().classes('w-full items-center p-2 bg-[#1e293b] rounded border border-slate-700 hover:border-blue-500 hover:bg-slate-700 transition') as row:
                            chk = ui.checkbox(value=False).props('dense dark color=green').classes('mr-2')
                            chk.on_value_change(lambda e, u=s['url']: self.on_check(u, e.value))
                            
                            with ui.column().classes('gap-0 flex-grow overflow-hidden'):
                                display_name = s['name']
                                try:
                                    country = detect_country_group(s['name'])
                                    flag = country.split(' ')[0]
                                    if flag not in s['name']: display_name = f"{flag} {s['name']}"
                                except: pass

                                ui.label(display_name).classes('text-sm font-bold text-slate-300 truncate')
                                ui.label(s['url']).classes('text-xs text-slate-600 font-mono truncate hidden')
                            
                            ip_addr = get_real_ip_display(s['url'])
                            status = s.get('_status')
                            stat_color, stat_icon = ('green-500', 'bolt') if status == 'online' else (('red-500', 'bolt') if status == 'offline' else ('grey-500', 'help_outline'))

                            with ui.row().classes('items-center gap-1'):
                                ui.icon(stat_icon).classes(f'text-{stat_color} text-sm')
                                ip_lbl = ui.label(ip_addr).classes('text-xs font-mono text-slate-500')
                                bind_ip_label(s['url'], ip_lbl)

                        self.ui_rows[s['url']] = {
                            'el': row, 
                            'search_text': f"{s['name']} {s['url']} {ip_addr}".lower(),
                            'checkbox': chk
                        }

            # --- 4. 底部操作栏 ---
            with ui.row().classes('w-full p-4 border-t border-slate-700 bg-[#0f172a] justify-between items-center flex-shrink-0'):
                with ui.row().classes('gap-2'):
                    ui.label('批量操作:').classes('text-sm font-bold text-slate-400 self-center')
                    
                    # === 移动分组 (子弹窗) ===
                    async def move_group():
                        if not self.selected_urls: return safe_notify('未选择服务器', 'warning')
                        
                        # 子弹窗背景深色
                        with ui.dialog() as sub_d, ui.card().classes('w-80 bg-[#1e293b] border border-slate-700'):
                            ui.label('移动到分组').classes('font-bold mb-2 text-slate-200')
                            groups = get_all_groups()
                            sel = ui.select(groups, label='选择或输入分组', with_input=True, new_value_mode='add-unique').classes('w-full').props('outlined dense dark')
                            
                            async def do_move(target_group):
                                if not target_group: return
                                count = 0
                                for s in SERVERS_CACHE:
                                    if s['url'] in self.selected_urls:
                                        s['group'] = target_group
                                        count += 1
                                if 'custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['custom_groups'] = []
                                if target_group not in ADMIN_CONFIG['custom_groups'] and target_group != '默认分组':
                                    ADMIN_CONFIG['custom_groups'].append(target_group)
                                    await save_admin_config()

                                await save_servers()
                                sub_d.close(); self.dialog.close()
                                render_sidebar_content.refresh()
                                try: await refresh_content('ALL') 
                                except: pass
                                safe_notify(f'已移动 {count} 个服务器到 [{target_group}]', 'positive')

                            ui.button('确定移动', on_click=lambda: do_move(sel.value)).classes('w-full mt-4 bg-blue-600 text-white')
                        sub_d.open()

                    ui.button('移动分组', icon='folder_open', on_click=move_group).props('flat dense color=blue')

                    # === 批量 SSH 设置 (子弹窗) ===
                    async def batch_ssh_config():
                        if not self.selected_urls: return safe_notify('未选择服务器', 'warning')

                        with ui.dialog() as d_ssh, ui.card().classes('w-96 p-5 flex flex-col gap-3 bg-[#1e293b] border border-slate-700'):
                            with ui.row().classes('items-center gap-2 mb-1'):
                                ui.icon('vpn_key', color='teal').classes('text-xl')
                                ui.label('批量 SSH 配置').classes('text-lg font-bold text-slate-200')
                            
                            ui.label(f'正在修改 {len(self.selected_urls)} 个服务器的连接信息').classes('text-xs text-slate-500')
                            
                            ui.label('SSH 用户名').classes('text-xs font-bold text-slate-400 mt-2')
                            user_input = ui.input(placeholder='留空则保持原样').props('outlined dense dark').classes('w-full')
                            
                            ui.label('认证方式').classes('text-xs font-bold text-slate-400 mt-2')
                            auth_opts = ['不修改', '全局密钥', '独立密码', '独立密钥']
                            auth_sel = ui.select(auth_opts, value='不修改').props('outlined dense options-dense dark').classes('w-full')
                            
                            pwd_input = ui.input('输入新密码', password=True).props('outlined dense dark').classes('w-full')
                            pwd_input.bind_visibility_from(auth_sel, 'value', value='独立密码')
                            
                            key_input = ui.textarea('输入新私钥', placeholder='-----BEGIN...').props('outlined dense rows=4 input-class="text-xs font-mono" dark').classes('w-full')
                            key_input.bind_visibility_from(auth_sel, 'value', value='独立密钥')
                            
                            global_hint = ui.label('✅ 将统一使用全局 SSH 密钥连接').classes('text-xs text-green-400 bg-green-900/30 p-2 rounded w-full text-center border border-green-800')
                            global_hint.bind_visibility_from(auth_sel, 'value', value='全局密钥')

                            async def save_ssh_changes():
                                count = 0; target_user = user_input.value.strip(); target_auth = auth_sel.value
                                for s in SERVERS_CACHE:
                                    if s['url'] in self.selected_urls:
                                        changed = False
                                        if target_user: s['ssh_user'] = target_user; changed = True
                                        if target_auth != '不修改':
                                            s['ssh_auth_type'] = target_auth; changed = True
                                            if target_auth == '独立密码': s['ssh_password'] = pwd_input.value
                                            elif target_auth == '独立密钥': s['ssh_key'] = key_input.value
                                        if changed: count += 1
                                if count > 0:
                                    await save_servers(); d_ssh.close()
                                    safe_notify(f'✅ 已更新 {count} 个 SSH 配置', 'positive')
                                else: d_ssh.close(); safe_notify('未做任何修改', 'warning')

                            with ui.row().classes('w-full justify-end mt-4 gap-2'):
                                ui.button('取消', on_click=d_ssh.close).props('flat color=grey')
                                ui.button('保存配置', icon='save', on_click=save_ssh_changes).classes('bg-teal-600 text-white shadow-md')
                        d_ssh.open()

                    ui.button('SSH 设置', icon='vpn_key', on_click=batch_ssh_config).props('flat dense color=teal')

                    # === 删除服务器 ===
                    async def delete_servers():
                        if not self.selected_urls: return safe_notify('未选择服务器', 'warning')
                        with ui.dialog() as sub_d, ui.card().classes('bg-[#1e293b] border border-slate-700'):
                            ui.label(f'确定删除 {len(self.selected_urls)} 个服务器?').classes('font-bold text-red-500')
                            with ui.row().classes('w-full justify-end mt-4'):
                                ui.button('取消', on_click=sub_d.close).props('flat color=grey')
                                async def confirm_del():
                                    global SERVERS_CACHE
                                    SERVERS_CACHE = [s for s in SERVERS_CACHE if s['url'] not in self.selected_urls]
                                    await save_servers()
                                    sub_d.close(); d.close()
                                    render_sidebar_content.refresh()
                                    if content_container: content_container.clear()
                                    safe_notify('删除成功', 'positive')
                                ui.button('确定删除', color='red', on_click=confirm_del).props('unelevated')
                        sub_d.open()

                    ui.button('删除', icon='delete', on_click=delete_servers).props('flat dense color=red')

                ui.button('关闭', on_click=d.close).props('outline color=grey')

        d.open()

    def on_search(self, e):
        keyword = str(e.value).lower().strip()
        for url, item in self.ui_rows.items():
            visible = keyword in item['search_text']
            item['el'].set_visibility(visible)

    def on_check(self, url, value):
        if value: self.selected_urls.add(url)
        else: self.selected_urls.discard(url)
        self.count_label.set_text(f'已选: {len(self.selected_urls)}')

    def toggle_all(self, state):
        visible_urls = [u for u, item in self.ui_rows.items() if item['el'].visible]
        for url in visible_urls:
            self.ui_rows[url]['checkbox'].value = state
        if not state:
            for url in visible_urls: self.selected_urls.discard(url)
        self.count_label.set_text(f'已选: {len(self.selected_urls)}')
def open_bulk_edit_dialog(servers, title="管理"):
    editor = BulkEditor(servers, title)
    editor.open()


# ================= 批量 SSH 执行逻辑 (深色适配版) =================
class BatchSSH:
    def __init__(self):
        self.selected_urls = set()
        self.log_element = None
        self.dialog = None

    def open_dialog(self):
        self.selected_urls = set()
        with ui.dialog() as d, ui.card().classes('w-full max-w-4xl h-[80vh] flex flex-col p-0 overflow-hidden bg-[#1e293b] border border-slate-700'):
            self.dialog = d
            # 标题栏
            with ui.row().classes('w-full justify-between items-center p-4 bg-[#0f172a] border-b border-slate-700'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('terminal', color='green').classes('text-xl')
                    ui.label('批量 SSH 执行').classes('text-lg font-bold text-slate-200')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

            self.content_box = ui.column().classes('w-full flex-grow overflow-hidden p-0 bg-[#1e293b]')
            self.render_selection_view()
        d.open()

    def render_selection_view(self):
        self.content_box.clear()
        with self.content_box:
            # 工具栏
            with ui.row().classes('w-full p-2 border-b border-slate-700 gap-2 bg-[#172033] items-center'):
                ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense color=blue')
                ui.button('全不选', on_click=lambda: self.toggle_all(False)).props('flat dense color=grey')
                self.count_label = ui.label('已选: 0').classes('ml-auto text-sm font-bold text-slate-400 mr-4')

            # 列表
            with ui.scroll_area().classes('w-full flex-grow p-4'):
                with ui.column().classes('w-full gap-1'):
                    groups = {}
                    for s in SERVERS_CACHE:
                        g = s.get('group', '默认分组')
                        if g not in groups: groups[g] = []
                        groups[g].append(s)

                    self.checks = {}
                    for g_name, servers in groups.items():
                        ui.label(g_name).classes('text-xs font-bold text-slate-500 mt-2 uppercase')
                        for s in servers:
                            with ui.row().classes('w-full items-center p-2 hover:bg-slate-700 rounded border border-transparent transition'):
                                chk = ui.checkbox(value=False, on_change=self.update_count).props('dense dark color=green')
                                self.checks[s['url']] = chk
                                with ui.column().classes('gap-0 ml-2'):
                                    ui.label(s['name']).classes('text-sm font-bold text-slate-300')
                                    ui.label(s['url']).classes('text-xs text-slate-600 font-mono')

            # 底部
            with ui.row().classes('w-full p-4 border-t border-slate-700 bg-[#0f172a] justify-end'):
                ui.button('下一步: 输入命令', on_click=self.go_to_execution, icon='arrow_forward').classes('bg-blue-600 text-white')

    def toggle_all(self, state):
        for chk in self.checks.values(): chk.value = state
        self.update_count()

    def update_count(self):
        count = sum(1 for c in self.checks.values() if c.value)
        self.count_label.set_text(f'已选: {count}')

    def go_to_execution(self):
        self.selected_urls = {url for url, chk in self.checks.items() if chk.value}
        if not self.selected_urls: return safe_notify('请至少选择一个服务器', 'warning')
        self.render_execution_view()

    def render_execution_view(self):
        self.content_box.clear()
        with self.content_box:
            # 输入区
            with ui.column().classes('w-full p-4 border-b border-slate-700 bg-[#172033] gap-2 flex-shrink-0'):
                ui.label(f'向 {len(self.selected_urls)} 台服务器发送命令:').classes('text-sm font-bold text-slate-400')
                self.cmd_input = ui.textarea(placeholder='例如: apt update -y').classes('w-full font-mono text-sm').props('outlined rows=3 dark bg-color="slate-900"')
                
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('提示: 后台并发执行，窗口可关闭。').classes('text-xs text-slate-500')
                    with ui.row().classes('gap-2'):
                        ui.button('上一步', on_click=self.render_selection_view).props('flat dense color=grey')
                        self.run_btn = ui.button('立即执行', on_click=self.run_batch, icon='play_arrow').classes('bg-green-600 text-white')

            # 日志区 (纯黑背景)
            self.log_container = ui.log().classes('w-full flex-grow font-mono text-xs bg-[#0b1121] text-green-400 p-4 overflow-y-auto')

    async def run_batch(self):
        cmd = self.cmd_input.value.strip()
        if not cmd: return safe_notify('请输入命令', 'warning')
        self.run_btn.disable(); self.cmd_input.disable()
        self.log_container.push(f"🚀 [Batch] Start: {cmd}")
        asyncio.create_task(self._process_batch(cmd, list(self.selected_urls)))

    async def _process_batch(self, cmd, urls):
        sem = asyncio.Semaphore(10)
        async def _worker(url):
            async with sem:
                server = next((s for s in SERVERS_CACHE if s['url'] == url), None)
                if not server: return
                name = server['name']
                
                def log_safe(msg):
                    try: 
                        if self.log_container and self.log_container.visible: self.log_container.push(msg)
                    except: pass

                log_safe(f"⏳ [{name}] Connecting...")
                try:
                    def ssh_exec():
                        client, msg = get_ssh_client_sync(server)
                        if not client: return False, msg
                        try:
                            stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
                            out = stdout.read().decode().strip()
                            err = stderr.read().decode().strip()
                            client.close(); return True, (out, err)
                        except Exception as e: return False, str(e)

                    success, result = await run.io_bound(ssh_exec)
                    if success:
                        out, err = result
                        if out: log_safe(f"✅ [{name}] OUT:\n{out}")
                        if err: log_safe(f"⚠️ [{name}] ERR:\n{err}")
                        if not out and not err: log_safe(f"✅ [{name}] Done (No Output)")
                    else: log_safe(f"❌ [{name}] Failed: {result}")
                except Exception as e: log_safe(f"❌ [{name}] Error: {e}")
                log_safe("-" * 30)

        tasks = [_worker(u) for u in urls]
        await asyncio.gather(*tasks)
        try: 
            self.log_container.push("🏁 All Done.")
            self.run_btn.enable(); self.cmd_input.enable()
        except: pass

batch_ssh_manager = BatchSSH()


# ================= 全能分组管理 (升级版：带搜索、智能全选、深色适配) =================
def open_combined_group_management(group_name):
    # 1. 准备数据结构
    ui_rows = {}
    
    # 弹窗容器: bg-[#1e293b], border-slate-700
    with ui.dialog() as d, ui.card().classes('w-[95vw] max-w-[600px] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden bg-[#1e293b] border border-slate-700 shadow-2xl'):
        
        # --- 标题栏 ---
        with ui.row().classes('w-full justify-between items-center p-4 bg-[#0f172a] border-b border-slate-700 flex-shrink-0'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('settings', color='primary').classes('text-xl')
                ui.label(f'管理分组: {group_name}').classes('text-lg font-bold text-slate-200')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        # --- 内容区域 ---
        with ui.column().classes('w-full flex-grow overflow-hidden p-0'):
            
            # --- A. 顶部设置区 ---
            with ui.column().classes('w-full p-4 border-b border-slate-700 bg-[#1e293b] gap-3 flex-shrink-0'):
                # 分组名称修改
                ui.label('分组名称').classes('text-xs font-bold text-slate-500 mb-[-5px]')
                name_input = ui.input(value=group_name).props('outlined dense dark').classes('w-full')
                
                # 搜索框
                ui.label('搜索筛选').classes('text-xs font-bold text-slate-500 mb-[-5px]')
                search_input = ui.input(placeholder='🔍 搜名称 / IP...').props('outlined dense clearable dark').classes('w-full')
                
                def on_search(e):
                    keyword = str(e.value).lower().strip()
                    for url, item in ui_rows.items():
                        is_match = keyword in item['search_text']
                        item['row'].set_visibility(is_match)
                
                search_input.on_value_change(on_search)

            # --- B. 成员选择区域 ---
            with ui.column().classes('w-full flex-grow overflow-hidden relative bg-[#0f172a]'):
                # 工具栏
                with ui.row().classes('w-full p-2 bg-[#172033] justify-between items-center border-b border-slate-700 flex-shrink-0'):
                    ui.label('成员选择:').classes('text-xs font-bold text-slate-400 ml-2')
                    with ui.row().classes('gap-1'):
                        ui.button('全选 (当前)', on_click=lambda: toggle_visible(True)).props('flat dense size=xs color=blue')
                        ui.button('清空', on_click=lambda: toggle_visible(False)).props('flat dense size=xs color=grey')

                with ui.scroll_area().classes('w-full flex-grow p-2'):
                    with ui.column().classes('w-full gap-1'):
                        
                        selection_map = {} 
                        
                        try: sorted_servers = sorted(SERVERS_CACHE, key=lambda x: str(x.get('name', '')))
                        except: sorted_servers = SERVERS_CACHE 

                        if not sorted_servers:
                            ui.label('暂无服务器数据').classes('w-full text-center text-slate-500 mt-4')

                        for s in sorted_servers:
                            tags = s.get('tags', [])
                            if not isinstance(tags, list): tags = []
                            is_in_group = group_name in tags
                            if s.get('group') == group_name: is_in_group = True
                            
                            selection_map[s['url']] = is_in_group
                            
                            ip_addr = s['url'].split('//')[-1].split(':')[0]
                            search_key = f"{s['name']} {ip_addr}".lower()

                            # 列表项渲染 (深色适配)
                            # 选中: bg-blue-900/30, 未选中: bg-[#1e293b]
                            bg_cls = 'bg-blue-900/30 border-blue-500/50' if is_in_group else 'bg-[#1e293b] border-slate-700'
                            
                            with ui.row().classes(f'w-full items-center p-2 hover:bg-slate-700 rounded border transition cursor-pointer {bg_cls}') as row:
                                chk = ui.checkbox(value=is_in_group).props('dense dark color=green')
                                
                                # 点击行触发勾选，点击checkbox不穿透
                                def toggle_row(c=chk, r=row, u=s['url']):
                                    c.set_value(not c.value)
                                    # 手动更新样式以获得即时反馈
                                    if c.value: r.classes(add='bg-blue-900/30 border-blue-500/50', remove='bg-[#1e293b] border-slate-700')
                                    else: r.classes(remove='bg-blue-900/30 border-blue-500/50', add='bg-[#1e293b] border-slate-700')

                                row.on('click', toggle_row)
                                chk.on('click.stop', lambda: None)
                                
                                chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                                
                                # 信息展示
                                with ui.column().classes('gap-0 ml-2 flex-grow overflow-hidden'):
                                    with ui.row().classes('items-center gap-2'):
                                        ui.label(s['name']).classes('text-sm font-bold truncate text-slate-300')
                                        
                                try:
                                    real_region = detect_country_group(s['name'], None)
                                    ui.label(real_region).classes('text-xs font-mono text-slate-500')
                                except: pass
                            
                            ui_rows[s['url']] = {
                                'row': row,
                                'chk': chk,
                                'search_text': search_key
                            }

                def toggle_visible(state):
                    count = 0
                    for item in ui_rows.values():
                        if item['row'].visible:
                            item['chk'].value = state
                            # 触发样式更新
                            if state: item['row'].classes(add='bg-blue-900/30 border-blue-500/50', remove='bg-[#1e293b] border-slate-700')
                            else: item['row'].classes(remove='bg-blue-900/30 border-blue-500/50', add='bg-[#1e293b] border-slate-700')
                            count += 1
                    if state and count > 0:
                        safe_notify(f"已选中当前显示的 {count} 个服务器", "positive")

        # 3. 底部按钮栏
        with ui.row().classes('w-full p-4 border-t border-slate-700 bg-[#0f172a] justify-between items-center flex-shrink-0'):
            
            # === 删除分组 ===
            async def delete_group():
                with ui.dialog() as confirm_d, ui.card().classes('bg-[#1e293b] border border-slate-700'):
                    ui.label(f'确定永久删除分组 "{group_name}"?').classes('font-bold text-red-500')
                    ui.label('服务器将保留，仅移除此标签。').classes('text-xs text-slate-400')
                    with ui.row().classes('w-full justify-end mt-4 gap-2'):
                        ui.button('取消', on_click=confirm_d.close).props('flat dense color=grey')
                        async def do_del():
                            if 'custom_groups' in ADMIN_CONFIG and group_name in ADMIN_CONFIG['custom_groups']:
                                ADMIN_CONFIG['custom_groups'].remove(group_name)
                            
                            for s in SERVERS_CACHE:
                                if 'tags' in s and group_name in s['tags']: s['tags'].remove(group_name)
                                if s.get('group') == group_name:
                                    try: s['group'] = detect_country_group(s['name'], None)
                                    except: s['group'] = '默认分组'

                            await save_admin_config(); await save_servers()
                            confirm_d.close(); d.close()
                            
                            render_sidebar_content.refresh()
                            if CURRENT_VIEW_STATE.get('scope') == 'TAG' and CURRENT_VIEW_STATE.get('data') == group_name:
                                await refresh_content('ALL')
                            else: safe_notify(f'分组 "{group_name}" 已删除', 'positive')
                                
                        ui.button('确认删除', color='red', on_click=do_del).props('unelevated')
                confirm_d.open()

            ui.button('删除分组', icon='delete', color='red', on_click=delete_group).props('flat')

            # === 保存修改 ===
            async def save_changes():
                new_name = name_input.value.strip()
                if not new_name: return safe_notify('分组名称不能为空', 'warning')
                
                # 1. 更新配置
                if new_name != group_name:
                    if 'custom_groups' in ADMIN_CONFIG:
                        if group_name in ADMIN_CONFIG['custom_groups']:
                            idx = ADMIN_CONFIG['custom_groups'].index(group_name)
                            ADMIN_CONFIG['custom_groups'][idx] = new_name
                        else:
                            ADMIN_CONFIG['custom_groups'].append(new_name)
                    await save_admin_config()

                # 2. 更新 Tags
                for s in SERVERS_CACHE:
                    if 'tags' not in s: s['tags'] = []
                    should_have_tag = selection_map.get(s['url'], False)
                    
                    if should_have_tag:
                        if new_name not in s['tags']: s['tags'].append(new_name)
                        if new_name != group_name and group_name in s['tags']: s['tags'].remove(group_name)
                    else:
                        if new_name in s['tags']: s['tags'].remove(new_name)
                        if group_name in s['tags']: s['tags'].remove(group_name)

                await save_servers()
                d.close()
                render_sidebar_content.refresh()
                
                if CURRENT_VIEW_STATE.get('scope') == 'TAG' and CURRENT_VIEW_STATE.get('data') == group_name:
                    await refresh_content('TAG', new_name, force_refresh=True)
                
                safe_notify('分组设置已保存', 'positive')

            ui.button('保存修改', icon='save', on_click=save_changes).classes('bg-blue-600 text-white shadow-lg')

    d.open()
    
# ================= 快捷创建分组弹窗 =================
def open_create_group_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm flex flex-col gap-4 p-6'):
        ui.label('新建自定义分组').classes('text-lg font-bold mb-2')
        
        name_input = ui.input('分组名称', placeholder='例如: 微软云 / 生产环境').classes('w-full').props('outlined')
        
        async def save_new_group():
            new_name = name_input.value.strip()
            if not new_name:
                safe_notify("分组名称不能为空", "warning")
                return
            
            # 检查是否重名
            existing_groups = set(get_all_groups())
            if new_name in existing_groups:
                safe_notify("该分组已存在", "warning")
                return

            if 'custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['custom_groups'] = []
            ADMIN_CONFIG['custom_groups'].append(new_name)
            await save_admin_config()
            
            d.close()
            render_sidebar_content.refresh()
            safe_notify(f"已创建分组: {new_name}", "positive")

        with ui.row().classes('w-full justify-end gap-2 mt-4'):
             ui.button('取消', on_click=d.close).props('flat color=grey')
             ui.button('保存', on_click=save_new_group).classes('bg-blue-600 text-white')
    d.open()


# ================= 全局 UI 索引 (用于实现 0 闪烁移动) =================
SIDEBAR_UI_REFS = {
    'groups': {},      # 存储格式: {'默认分组': ui_column_element, ...}
    'rows': {}         # 存储格式: {'http://1.2.3.4': ui_row_element, ...}
}

#侧边栏点击防抖处理函数
async def on_server_click_handler(server):
    # 1. 获取当前视图状态
    current_scope = CURRENT_VIEW_STATE.get('scope')
    current_data = CURRENT_VIEW_STATE.get('data')
    
    # 2. 判断是否点击了当前正在显示的服务器 (通过 URL 唯一标识判断)
    is_same_server = False
    if current_scope == 'SINGLE' and current_data:
        try:
            # 比较 URL 是最稳妥的唯一性判断
            if current_data.get('url') == server.get('url'):
                is_same_server = True
        except: pass

    if is_same_server:
        # 🛑 核心逻辑：如果是同一台机器，阻止重绘页面！
        # 这样右下角的 SSH 窗口就不会被销毁，连接得以保持。
        
        # 可选：如果希望点击时顺便刷新一下节点列表（不影响 SSH），可以调用这个
        if REFRESH_CURRENT_NODES:
            res = REFRESH_CURRENT_NODES()
            if res and asyncio.iscoroutine(res):
                await res
        return 

    # 3. 如果是不同的服务器，才执行正常的切换逻辑 (重绘页面)
    await refresh_content('SINGLE', server)


# ================= 提取出来的单行渲染函数 (深色适配) =================
def render_single_sidebar_row(s):
    # 样式定义
    # 按钮基底：背景 Slate-800 (#1e293b)，边框 Slate-600
    btn_keycap_base = 'bg-[#1e293b] border-t border-x border-slate-600 border-b-[3px] border-b-slate-800 rounded-lg transition-all active:border-b-0 active:border-t-[3px] active:translate-y-[3px]'
    
    # 名字按钮：普通文字 Slate-400，悬浮 Slate-300 / White
    btn_name_cls = f'{btn_keycap_base} flex-grow text-xs font-bold text-slate-400 truncate px-3 py-2.5 hover:bg-[#334155] hover:text-white hover:border-slate-500'
    
    # 设置按钮：
    btn_settings_cls = f'{btn_keycap_base} w-10 py-2.5 px-0 flex items-center justify-center text-slate-500 hover:text-white hover:bg-[#334155] hover:border-slate-500'

    # 创建行容器
    with ui.row().classes('w-full gap-2 no-wrap items-stretch') as row:
        # 1. 服务器名字按钮
        ui.button(on_click=lambda _, s=s: on_server_click_handler(s)) \
            .bind_text_from(s, 'name') \
            .props('no-caps align=left flat text-color=grey-4') \
            .classes(btn_name_cls)
        
        # 2. 设置按钮
        ui.button(icon='settings', on_click=lambda _, s=s: open_server_dialog(SERVERS_CACHE.index(s))) \
            .props('flat square size=sm text-color=grey-5') \
            .classes(btn_settings_cls).tooltip('配置 / 删除')
    
    # 注册到全局索引
    SIDEBAR_UI_REFS['rows'][s['url']] = row
    return row
    
# ================= 侧边栏渲染 (深色适配版) =================
_current_dragged_group = None 
@ui.refreshable
def render_sidebar_content():
    global _current_dragged_group
    
    # 清空索引
    SIDEBAR_UI_REFS['groups'].clear()
    SIDEBAR_UI_REFS['rows'].clear()

    # --- 1. 顶部固定区域 ---
    # 样式：深蓝灰背景，悬浮变亮
    btn_top_style = 'w-full bg-[#0f172a] border border-slate-700 rounded-lg shadow-sm text-slate-400 font-bold px-3 py-2 transition-all hover:bg-[#334155] hover:text-white hover:border-blue-500 active:translate-y-0 active:scale-[0.98]'
    
    # 顶部容器背景：Slate-800 (#1e293b)
    with ui.column().classes('w-full p-4 border-b border-slate-700 bg-[#1e293b] flex-shrink-0 relative overflow-hidden'):
        ui.label('X-Fusion').classes('absolute top-2 right-6 text-[3rem] font-black text-slate-800 opacity-20 pointer-events-none -rotate-12 select-none z-0 tracking-tighter leading-tight')
        
        with ui.row().classes('w-full items-center mb-4 z-10 relative'):
            ui.label('控制中心').classes('text-sm font-black text-blue-500 tracking-widest uppercase')

        with ui.column().classes('w-full gap-2 z-10 relative'):
            ui.button('仪表盘', icon='dashboard', on_click=lambda: asyncio.create_task(load_dashboard_stats())).props('flat align=left').classes(btn_top_style)
            ui.button('探针设置', icon='tune', on_click=render_probe_page).props('flat align=left').classes(btn_top_style)
            ui.button('订阅管理', icon='rss_feed', on_click=load_subs_view).props('flat align=left').classes(btn_top_style)
            
    # --- 2. 列表区域 ---
    # 滚动区背景：Slate-800 (#1e293b)
    with ui.column().props('id=sidebar-scroll-box').classes('w-full flex-grow overflow-y-auto p-2 gap-2 bg-[#1e293b]'):
        # 功能按钮
        with ui.row().classes('w-full gap-2 px-1 mb-2'):
            func_btn_base = 'flex-grow text-xs font-bold text-white rounded-lg border-b-4 border-black/20 active:border-b-0 active:translate-y-[4px] transition-all'
            ui.button('新建分组', icon='create_new_folder', on_click=open_quick_group_create_dialog).props('dense unelevated').classes(f'bg-blue-600 hover:bg-blue-500 {func_btn_base}')
            ui.button('添加服务器', icon='add', color='green', on_click=lambda: open_server_dialog(None)).props('dense unelevated').classes(f'bg-emerald-600 hover:bg-emerald-500 {func_btn_base}')
                
        # --- A. 全部服务器 ---
        # 列表卡片：背景更深 (#0f172a) 以突出内容
        list_item_3d = 'w-full items-center justify-between p-3 border border-slate-700 rounded-xl mb-1 bg-[#0f172a] shadow-md cursor-pointer group transition-all duration-200 hover:border-blue-500 hover:bg-[#172033] active:scale-[0.98]'
        with ui.row().classes(list_item_3d).on('click', lambda _: refresh_content('ALL')):
            with ui.row().classes('items-center gap-3'):
                with ui.column().classes('p-1.5 bg-slate-800 rounded-lg group-hover:bg-blue-900 transition-colors'):
                    ui.icon('dns', color='blue-4').classes('text-sm')
                ui.label('所有服务器').classes('font-bold text-slate-300 group-hover:text-white')
            ui.badge(str(len(SERVERS_CACHE)), color='blue-9').props('rounded outline').classes('text-blue-200')

        def on_drag_start(e, name): global _current_dragged_group; _current_dragged_group = name

        # --- B. 自定义分组  ---
        final_tags = ADMIN_CONFIG.get('custom_groups', [])
        async def on_tag_drop(e, target_name):
            global _current_dragged_group
            if not _current_dragged_group or _current_dragged_group == target_name: return
            try:
                current_list = list(final_tags)
                if _current_dragged_group in current_list and target_name in current_list:
                    old_idx = current_list.index(_current_dragged_group); item = current_list.pop(old_idx)
                    new_idx = current_list.index(target_name); current_list.insert(new_idx, item)
                    ADMIN_CONFIG['custom_groups'] = current_list; await save_admin_config()
                    _current_dragged_group = None; render_sidebar_content.refresh()
            except: pass

        if final_tags:
            ui.label('自定义分组').classes('text-xs font-bold text-slate-500 mt-4 mb-2 px-2 uppercase tracking-wider')
            for tag_group in final_tags:
                tag_servers = [s for s in SERVERS_CACHE if isinstance(s, dict) and (tag_group in s.get('tags', []) or s.get('group') == tag_group)]
                try: tag_servers.sort(key=smart_sort_key)
                except: tag_servers.sort(key=lambda x: x.get('name', ''))
                is_open = tag_group in EXPANDED_GROUPS
                
                with ui.element('div').classes('w-full').on('dragover.prevent', lambda _: None).on('drop', lambda e, n=tag_group: on_tag_drop(e, n)):
                    # 分组面板
                    with ui.expansion('', icon=None, value=is_open).classes('w-full border border-slate-700 rounded-xl mb-2 bg-[#0f172a] shadow-sm transition-all').props('expand-icon-toggle header-class="bg-[#0f172a] hover:bg-[#172033]"').on_value_change(lambda e, g=tag_group: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                        
                        # Header 布局
                        with exp.add_slot('header'):
                            with ui.row().classes('w-full h-full items-center justify-between no-wrap py-2 cursor-pointer group/header transition-all').on('click', lambda _, g=tag_group: refresh_content('TAG', g)):
                                
                                # 左侧区域
                                with ui.row().classes('items-center gap-3 flex-grow overflow-hidden no-wrap'):
                                    # 拖拽手柄
                                    ui.icon('drag_indicator').props('draggable="true"').classes('cursor-move text-slate-600 hover:text-slate-400 p-1 rounded transition-colors group-hover/header:text-slate-400').on('dragstart', lambda e, n=tag_group: on_drag_start(e, n)).on('click.stop').tooltip('按住拖拽')
                                    
                                    # 组名
                                    with ui.row().classes('items-center gap-2 flex-grow overflow-hidden no-wrap'):
                                        ui.label(tag_group).classes('font-bold text-slate-300 truncate group-hover/header:text-white text-sm')

                                # 右侧区域：设置按钮 + 数量
                                with ui.row().classes('items-center gap-2 pr-2 flex-shrink-0').on('mousedown.stop').on('click.stop'):
                                    ui.button(icon='settings', on_click=lambda _, g=tag_group: open_combined_group_management(g)).props('flat dense round size=xs color=grey-6').classes('hover:text-white').tooltip('管理分组')
                                    
                                    ui.badge(str(len(tag_servers)), color='green-9').props('rounded outline text-color=green-4')
                        
                        # 注册分组容器
                        with ui.column().classes('w-full gap-2 p-2 bg-[#172033] border-t border-slate-800') as col:
                            SIDEBAR_UI_REFS['groups'][tag_group] = col
                            for s in tag_servers:
                                render_single_sidebar_row(s)

        # --- C. 区域分组 ---
        ui.label('区域分组').classes('text-xs font-bold text-slate-500 mt-4 mb-2 px-2 uppercase tracking-wider')
        country_buckets = {}
        for s in SERVERS_CACHE:
            c_group = detect_country_group(s.get('name', ''), s)
            if c_group in ['默认分组', '自动注册', '自动导入', '未分组', '', None]: c_group = '🏳️ 其他地区'
            if c_group not in country_buckets: country_buckets[c_group] = []
            country_buckets[c_group].append(s)
        
        saved_order = ADMIN_CONFIG.get('group_order', [])
        def region_sort_key(name): return saved_order.index(name) if name in saved_order else 9999
        sorted_regions = sorted(country_buckets.keys(), key=region_sort_key)

        async def on_region_drop(e, target_name):
            global _current_dragged_group
            if not _current_dragged_group or _current_dragged_group == target_name: return
            try:
                current_list = list(sorted_regions)
                if _current_dragged_group in current_list and target_name in current_list:
                    old_idx = current_list.index(_current_dragged_group); item = current_list.pop(old_idx)
                    new_idx = current_list.index(target_name); current_list.insert(new_idx, item)
                    ADMIN_CONFIG['group_order'] = current_list; await save_admin_config()
                    _current_dragged_group = None; render_sidebar_content.refresh()
            except: pass

        with ui.column().classes('w-full gap-2 pb-4'):
            for c_name in sorted_regions:
                c_servers = country_buckets[c_name]
                try: c_servers.sort(key=smart_sort_key)
                except: c_servers.sort(key=lambda x: x.get('name', ''))
                is_open = c_name in EXPANDED_GROUPS

                with ui.element('div').classes('w-full').on('dragover.prevent', lambda _: None).on('drop', lambda e, n=c_name: on_region_drop(e, n)):
                    with ui.expansion('', icon=None, value=is_open).classes('w-full border border-slate-700 rounded-xl bg-[#0f172a] shadow-sm').props('expand-icon-toggle header-class="bg-[#0f172a] hover:bg-[#172033]"').on_value_change(lambda e, g=c_name: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                        with exp.add_slot('header'):
                            with ui.row().classes('w-full h-full items-center justify-between no-wrap py-2 cursor-pointer group/header transition-all').on('click', lambda _, g=c_name: refresh_content('COUNTRY', g)):
                                with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                                    ui.icon('drag_indicator').props('draggable="true"').classes('cursor-move text-slate-600 hover:text-slate-400 p-1').on('dragstart', lambda e, n=c_name: on_drag_start(e, n)).on('click.stop').tooltip('按住拖拽')
                                    with ui.row().classes('items-center gap-2 flex-grow'):
                                        flag = c_name.split(' ')[0] if ' ' in c_name else '🏳️'
                                        ui.label(flag).classes('text-lg filter drop-shadow-md')
                                        display_name = c_name.split(' ')[1] if ' ' in c_name else c_name
                                        ui.label(display_name).classes('font-bold text-slate-300 truncate group-hover/header:text-white')
                                with ui.row().classes('items-center gap-2 pr-2').on('mousedown.stop').on('click.stop'):
                                    ui.button(icon='edit_note', on_click=lambda _, s=c_servers, t=c_name: open_bulk_edit_dialog(s, f"区域: {t}")).props('flat dense round size=xs color=grey-6').classes('hover:text-white').tooltip('批量管理')
                                    ui.badge(str(len(c_servers)), color='green-9').props('rounded outline text-color=green-4')

                        # 注册区域容器
                        with ui.column().classes('w-full gap-2 p-2 bg-[#172033] border-t border-slate-800') as col:
                            SIDEBAR_UI_REFS['groups'][c_name] = col
                            for s in c_servers:
                                render_single_sidebar_row(s) # 使用提取的函数

    # JS 滚动记忆
    ui.run_javascript('''
        (function() {
            var el = document.getElementById("sidebar-scroll-box");
            if (el) {
                if (window.sidebarScroll) el.scrollTop = window.sidebarScroll;
                el.addEventListener("scroll", function() { window.sidebarScroll = el.scrollTop; });
            }
        })();
    ''')
    
    # 3. 底部 (深色化)
    with ui.column().classes('w-full p-2 border-t border-slate-700 mt-auto mb-4 gap-2 bg-[#1e293b] z-10'):
        bottom_btn_3d = 'w-full text-slate-400 text-xs font-bold bg-[#0f172a] border border-slate-700 rounded-lg px-3 py-2 transition-all hover:bg-[#334155] hover:text-white hover:border-blue-500 active:translate-y-[1px]'
        ui.button('批量 SSH 执行', icon='playlist_play', on_click=batch_ssh_manager.open_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('Cloudflare 设置', icon='cloud', on_click=open_cloudflare_settings_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('全局 SSH 设置', icon='vpn_key', on_click=open_global_settings_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('数据备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes(bottom_btn_3d)
        
# ================== 登录与 MFA 逻辑 (升级版：含设备指纹与地理围栏)  ==================
@ui.page('/login')
def login_page(request: Request): 
    # ✨ 核心注入：在页面加载时植入 JS，生成并保存永久设备指纹
    ui.add_head_html('''
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            // 尝试从本地存储获取指纹
            let fp = localStorage.getItem('fp_device_id');
            if (!fp) {
                // 如果没有，生成一个高随机性的唯一 ID
                fp = 'dev-' + Math.random().toString(36).substr(2, 9) + '-' + Date.now().toString(36);
                localStorage.setItem('fp_device_id', fp);
            }
            // 将指纹写入 Cookie，有效期设置为 10 年，供后端 Python 提取
            document.cookie = "fp_device_id=" + fp + "; path=/; max-age=315360000";
        });
    </script>
    ''')

    # 容器：用于切换登录步骤 (账号密码 -> MFA)
    container = ui.card().classes('absolute-center w-full max-w-sm p-8 shadow-2xl rounded-xl bg-white')

    # --- 步骤 1: 账号密码验证 ---
    def render_step1():
        container.clear()
        with container:
            ui.label('X-Fusion Panel').classes('text-2xl font-extrabold mb-2 w-full text-center text-slate-800')
            ui.label('请登录以继续').classes('text-sm text-gray-400 mb-6 w-full text-center')
            
            username = ui.input('账号').props('outlined dense').classes('w-full mb-3')
            password = ui.input('密码', password=True).props('outlined dense').classes('w-full mb-6').on('keydown.enter', lambda: check_cred())
            
            def check_cred():
                if username.value == ADMIN_USER and password.value == ADMIN_PASS:
                    # 账号密码正确，进入 MFA 流程
                    check_mfa()
                else:
                    ui.notify('账号或密码错误', color='negative', position='top')

            ui.button('下一步', on_click=check_cred).classes('w-full bg-slate-900 text-white shadow-lg h-10')

            ui.label('© Powered by 小龙女她爸').classes('text-xs text-gray-400 mt-6 w-full text-center font-mono opacity-80')

    # --- 步骤 2: MFA 验证或设置 ---
    def check_mfa():
        secret = ADMIN_CONFIG.get('mfa_secret')
        if not secret:
            # 如果没有密钥，进入初始化流程 (生成新密钥)
            new_secret = pyotp.random_base32()
            render_setup(new_secret)
        else:
            # 已有密钥，进入验证流程
            render_verify(secret)

    # 渲染 MFA 设置页面 (首次登录)
    def render_setup(secret):
        container.clear()
        
        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(name=ADMIN_USER, issuer_name="X-Fusion Panel")
        qr = qrcode.make(totp_uri)
        img_buffer = io.BytesIO()
        qr.save(img_buffer, format='PNG')
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')

        with container:
            ui.label('绑定二次验证 (MFA)').classes('text-xl font-bold mb-2 w-full text-center')
            ui.label('请使用 Authenticator App 扫描').classes('text-xs text-gray-400 mb-2 w-full text-center')
            
            with ui.row().classes('w-full justify-center mb-2'):
                ui.image(f'data:image/png;base64,{img_b64}').style('width: 180px; height: 180px')
            
            with ui.row().classes('w-full justify-center items-center gap-1 mb-4 bg-gray-100 p-1 rounded cursor-pointer').on('click', lambda: safe_copy_to_clipboard(secret)):
                ui.label(secret).classes('text-xs font-mono text-gray-600')
                ui.icon('content_copy').classes('text-gray-400 text-xs')

            code = ui.input('验证码', placeholder='6位数字').props('outlined dense input-class=text-center').classes('w-full mb-4')
            
            async def confirm():
                totp = pyotp.TOTP(secret)
                if totp.verify(code.value):
                    ADMIN_CONFIG['mfa_secret'] = secret
                    await save_admin_config()
                    ui.notify('绑定成功', type='positive')
                    finish()
                else:
                    ui.notify('验证码错误', type='negative')

            ui.button('确认绑定', on_click=confirm).classes('w-full bg-green-600 text-white h-10')

    # 渲染 MFA 验证页面 (日常登录)
    def render_verify(secret):
        container.clear()
        with container:
            ui.label('安全验证').classes('text-xl font-bold mb-6 w-full text-center')
            with ui.column().classes('w-full items-center mb-6'):
                ui.icon('verified_user').classes('text-6xl text-blue-600 mb-2')
                ui.label('请输入 Authenticator 动态码').classes('text-xs text-gray-400')

            code = ui.input(placeholder='------').props('outlined input-class=text-center text-xl tracking-widest').classes('w-full mb-6')
            code.on('keydown.enter', lambda: verify())
            ui.timer(0.1, lambda: ui.run_javascript(f'document.querySelector(".q-field__native").focus()'), once=True)

            def verify():
                totp = pyotp.TOTP(secret)
                if totp.verify(code.value):
                    finish()
                else:
                    ui.notify('无效的验证码', type='negative', position='top')
                    code.value = ''

            ui.button('验证登录', on_click=verify).classes('w-full bg-slate-900 text-white h-10')
            ui.button('返回', on_click=render_step1).props('flat dense').classes('w-full mt-2 text-gray-400 text-xs')

    def finish():
        # 1. 基础认证标记
        app.storage.user['authenticated'] = True
        
        # 2. 写入全局版本号 (防止被踢出)
        if 'session_version' not in ADMIN_CONFIG:
            ADMIN_CONFIG['session_version'] = str(uuid.uuid4())[:8]
        app.storage.user['session_version'] = ADMIN_CONFIG['session_version']
        
        # ✨✨✨ 3. 核心修改：记录 IP、指纹 以及 地理位置 ✨✨✨
        try:
            # 从请求头中提取真实 IP 和 Cookie 中的指纹
            client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
            client_device_id = request.cookies.get('fp_device_id', 'Unknown_Device')
            
            # 记录 IP 和 设备指纹
            app.storage.user['last_known_ip'] = client_ip
            app.storage.user['device_id'] = client_device_id 
            
            # 获取并记录登录地的省份/国家 (依赖之前升级的 fetch_geo_from_ip 函数)
            geo = fetch_geo_from_ip(client_ip)
            if geo and len(geo) >= 4:
                # geo[2] 是国家，geo[3] 是省份/地区
                app.storage.user['login_region'] = f"{geo[2]}-{geo[3]}" 
            else:
                app.storage.user['login_region'] = "未知区域"
        except: pass

        ui.navigate.to('/')

    render_step1()

# ================= 0. 认证检查辅助函数 =================
def check_auth(request: Request):
    """
    检查用户是否已登录，且会话版本是否有效
    """
    # 1. 基础认证：检查 Cookie 里有没有 authenticated 标记
    if not app.storage.user.get('authenticated', False):
        return False
    
    # 2. 全局会话版本校验 (实现一键踢人核心逻辑)
    # 获取当前系统要求的全局版本号 (如 v1)
    current_global_ver = ADMIN_CONFIG.get('session_version', 'init')
    # 获取用户 Cookie 里的版本号
    user_ver = app.storage.user.get('session_version', '')
    
    # 如果版本不匹配 (比如管理员刚刚重置了密钥)，视为未登录
    if current_global_ver != user_ver:
        return False
        
    return True

# ================= [本地化版] 主页入口 (含 IP 检测与强制下线) =================
@ui.page('/')
def main_page(request: Request):
    # ✨✨✨ 1. 强制开启现代暗色主题 ✨✨✨
    # 启用暗黑模式
    dark = ui.dark_mode()
    dark.enable()
    
    # 配置一套现代科技感配色 (Slate 深蓝灰风格)
    ui.colors(
        primary='#6366f1',    # Indigo-500 (主按钮/高亮)
        secondary='#475569',  # Slate-600
        accent='#8b5cf6',     # Violet-500
        dark='#0f172a',       # Slate-900 (网页主背景色)
        positive='#10b981',   # Emerald-500
        negative='#ef4444',   # Red-500
        info='#3b82f6',       # Blue-500
        warning='#f59e0b'     # Amber-500
    )

    # 注入全局 CSS 强制覆盖背景
    ui.add_head_html('''
        <link rel="stylesheet" href="/static/xterm.css" />
        <script src="/static/xterm.js"></script>
        <script src="/static/xterm-addon-fit.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
        <style>
            /* 1. 定义字体 */
            @font-face {
                font-family: 'Twemoji Country Flags';
                src: url('https://cdn.jsdelivr.net/npm/country-flag-emoji-polyfill@0.1/dist/TwemojiCountryFlags.woff2') format('woff2');
                unicode-range: U+1F1E6-1F1FF, U+1F3F4, U+E0062-E007F;
            }
            /* 2. 全局深色背景覆盖 */
            body { 
                background-color: #0f172a !important; 
                color: #e2e8f0 !important;
                font-family: 'Twemoji Country Flags', 'Noto Sans SC', "Roboto", "Helvetica", "Arial", sans-serif, "Noto Color Emoji"; 
            }
            .nicegui-connection-lost { display: none !important; }
            
            /* 3. 滚动条美化 (深色) */
            ::-webkit-scrollbar { width: 6px; height: 6px; }
            ::-webkit-scrollbar-track { background: #0f172a; }
            ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
            ::-webkit-scrollbar-thumb:hover { background: #475569; }
            
            /* 4. 强制卡片背景 */
            .q-card { background-color: #1e293b !important; border: 1px solid #334155 !important; }
            .q-drawer { background-color: #1e293b !important; }
        </style>
    ''')

    # ================= 2. 认证检查 =================
    if not check_auth(request): 
        return RedirectResponse('/login')

    # ================= 3. 高级安全风控 (IP 地理围栏 + 指纹核对) =================
    try:
        current_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
        current_device_id = request.cookies.get('fp_device_id', 'Unknown')
    except:
        current_ip = "Unknown"; current_device_id = "Unknown"
        
    display_ip = current_ip 
    last_ip = app.storage.user.get('last_known_ip', '')
    last_device_id = app.storage.user.get('device_id', '')
    login_region = app.storage.user.get('login_region', '未知区域')
    
    # 辅助函数：一键踢出
    async def reset_global_session(dialog_ref=None):
        new_ver = str(uuid.uuid4())[:8]
        ADMIN_CONFIG['session_version'] = new_ver
        await save_admin_config()
        if dialog_ref: dialog_ref.close()
        ui.notify('🔒 安全密钥已重置，正在强制所有设备下线...', type='warning', close_button=False)
        await asyncio.sleep(1.5)
        app.storage.user.clear()
        ui.navigate.to('/login')

    # 定义高危弹窗
    def trigger_geo_alert(new_ip, old_ip, old_loc, new_loc):
        app.storage.user['last_known_ip'] = new_ip # 暂时更新防止无限弹窗
        with ui.dialog() as d, ui.card().classes('w-[450px] p-5 border-t-4 border-red-500 shadow-2xl bg-[#1e293b]'):
            with ui.row().classes('items-center gap-2 text-red-500 mb-2'):
                ui.icon('gpp_bad', size='md')
                ui.label('⚠️ 安全拦截：异地/异常设备登录').classes('font-bold text-lg')
            ui.label('系统检测到您的会话出现了异常跳变，可能存在 Cookie 劫持风险：').classes('text-sm text-slate-300')
            
            with ui.grid().classes('grid-cols-1 gap-2 my-4 bg-red-900/30 p-3 rounded border border-red-500/50'):
                ui.label(f'🔒 原始登录地: {old_ip} ({old_loc})').classes('text-xs font-mono font-bold text-slate-400')
                ui.label(f'🚨 当前请求源: {new_ip} ({new_loc})').classes('text-xs font-mono font-bold text-red-400')
            
            ui.label('如果您正在使用代理节点访问面板，请忽略；如果不是您本人的操作，请立即强制下线所有设备！').classes('text-xs text-red-300 font-bold')
            
            with ui.row().classes('w-full justify-end gap-3 mt-4'):
                ui.button('是本人操作 (忽略)', on_click=d.close).props('outline color=grey')
                ui.button('冻结并强制下线', color='red', icon='block', on_click=lambda: reset_global_session(d)).props('unelevated')
        d.open()

    # ✨✨✨ 修复：将风控逻辑封装为异步函数 ✨✨✨
    async def run_security_check():
        if last_ip and last_ip != current_ip:
            # IP 变了，先看指纹对不对
            if last_device_id and last_device_id == current_device_id:
                # 指纹是对的，异步查询地理位置
                current_geo = await run.io_bound(fetch_geo_from_ip, current_ip)
                current_region = f"{current_geo[2]}-{current_geo[3]}" if current_geo else "未知区域"
                
                if current_region == login_region or "未知" in current_region:
                    # ✅ 同一省份，静默放行
                    app.storage.user['last_known_ip'] = current_ip
                else:
                    # ❌ 异地跳变，触发拦截
                    trigger_geo_alert(current_ip, last_ip, login_region, current_region)
            else:
                # ❌ 新设备新IP，触发拦截
                trigger_geo_alert(current_ip, last_ip, "旧设备", "未知新设备")

    # ✨✨✨ 修复：页面加载完成后 0.5 秒自动执行检查，不卡顿主界面 ✨✨✨
    ui.timer(0.5, run_security_check, once=True)

    # ================= 4. UI 构建 (深色布局核心) =================
    
    # ✨ 修改点：左侧抽屉背景色
    with ui.left_drawer(value=True, fixed=True).classes('bg-[#1e293b] border-r border-slate-700').props('width=360 bordered') as drawer:
        render_sidebar_content()

    # ✨ 恢复顶栏：移除 IP，保持简洁
    with ui.header().classes('bg-[#0f172a] text-white h-14 border-b border-slate-800 shadow-md'):
        with ui.row().classes('w-full items-center justify-between'):
            # 左侧：菜单与标题
            with ui.row().classes('items-center gap-2'):
                ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense color=white')
                ui.label('X-Fusion Panel').classes('text-lg font-bold ml-2 tracking-wide text-blue-400')

            # 右侧：操作按钮区
            with ui.row().classes('items-center gap-3 mr-2'):
                with ui.button(icon='gpp_bad', color='red', on_click=lambda: reset_global_session(None)).props('flat dense round size=sm').tooltip('安全重置'):
                     ui.badge('Reset', color='orange').props('floating rounded')

                with ui.button(icon='vpn_key', on_click=lambda: safe_copy_to_clipboard(AUTO_REGISTER_SECRET)).props('flat dense round size=sm color=grey-5').tooltip('复制通讯密钥'):
                    ui.badge('Key', color='red').props('floating rounded')
                
                ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat round dense color=grey-5').tooltip('退出登录')

    # ✨ 修改点：主内容区域背景色
    global content_container
    content_container = ui.column().classes('w-full h-full pl-4 pr-4 pt-4 overflow-y-auto bg-[#0f172a]')
    
    # ================= 5. 后台任务：自动初始化用户环境 =================
    async def auto_init_system_settings():
        try:
            # 1. 智能获取本次访问的真实域名
            real_origin = get_dynamic_origin()
            if "YOUR-DOMAIN" in real_origin:
                real_origin = await ui.run_javascript('return window.location.origin', timeout=3.0)

            if not real_origin: return

            stored_url = ADMIN_CONFIG.get('manager_base_url', '')
            need_save = False

            if 'session_version' not in ADMIN_CONFIG:
                ADMIN_CONFIG['session_version'] = 'init_v1'
                need_save = True

            # 2. 如果是第一次启动，或者之前存的是旧的/错误的IP，自动更新为用户的真实域名
            if not stored_url or 'sijuly.nyc.mn' in stored_url or '127.0.0.1' in stored_url:
                ADMIN_CONFIG['manager_base_url'] = real_origin
                need_save = True

            if not ADMIN_CONFIG.get('probe_enabled'):
                ADMIN_CONFIG['probe_enabled'] = True
                need_save = True

            if need_save: await save_admin_config()
        except: pass

    ui.timer(1.0, auto_init_system_settings, once=True)

    async def restore_last_view():
        last_scope = app.storage.user.get('last_view_scope', 'DASHBOARD')
        last_data_id = app.storage.user.get('last_view_data', None)
        target_data = last_data_id
        if last_scope == 'SINGLE' and last_data_id:
            target_data = next((s for s in SERVERS_CACHE if s['url'] == last_data_id), None)
            if not target_data: last_scope = 'DASHBOARD'

        if last_scope == 'DASHBOARD': await load_dashboard_stats()
        elif last_scope == 'PROBE': await render_probe_page()
        elif last_scope == 'SUBS': await load_subs_view()
        else: await refresh_content(last_scope, target_data)
        logger.info(f"♻️ 自动恢复视图: {last_scope}")

    ui.timer(0.1, lambda: asyncio.create_task(restore_last_view()), once=True)
    logger.info("✅ UI 已就绪")
    



# ================= TG 报警模块 =================
ALERT_CACHE = {}     # 记录服务器确认后的状态 (Online/Offline)
FAILURE_COUNTS = {}  # 记录连续失败次数

async def send_telegram_message(text):
    """发送 Telegram 消息"""
    token = ADMIN_CONFIG.get('tg_bot_token')
    chat_id = ADMIN_CONFIG.get('tg_chat_id')
    
    if not token or not chat_id: return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    def _do_req():
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"❌ TG 发送失败: {e}")

    await run.io_bound(_do_req)
    
# ================= 优化后的监控任务 (高性能版：仅监控已安装探针的机器) =================
async def job_monitor_status():
    """
    监控任务：每分钟检查一次服务器状态
    优化：将并发数从 5 提升至 50，以支持 1000 台服务器在 30-40秒内完成轮询
    修正：彻底跳过未安装探针的 X-UI 面板机器
    """
    # 50 并发
    sema = asyncio.Semaphore(50) 
    
    # 定义报警阈值：连续失败 3 次才报警
    FAILURE_THRESHOLD = 3 
    
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    async def _check_single_server(srv):
        # 🛑 如果未安装探针，直接跳过所有监控逻辑
        # 这样后台就不会去尝试获取这些机器的状态，也不会记录历史或报警
        if not srv.get('probe_installed', False):
            return

        async with sema:
            # 稍微让出一点 CPU 时间片，避免高并发瞬间卡顿 UI
            await asyncio.sleep(0.01) 
            
            res = await get_server_status(srv)
            name = srv.get('name', 'Unknown')
            url = srv['url']
            
            # 如果没配 TG，后面的报警逻辑就跳过
            if not ADMIN_CONFIG.get('tg_bot_token'): return

            # 清洗 IP，只显示纯 IP
            display_ip = url.split('://')[-1].split(':')[0]
            
            # 判断当前物理探测状态
            is_physically_online = False
            if isinstance(res, dict) and res.get('status') == 'online':
                is_physically_online = True
            
            # --- 核心防抖逻辑 ---
            if is_physically_online:
                # 1. 如果当前检测在线，直接重置失败计数器
                FAILURE_COUNTS[url] = 0
                
                # 2. 检查是否需要发“恢复通知”
                if ALERT_CACHE.get(url) == 'offline':
                    msg = (
                        f"🟢 **恢复：服务器已上线**\n\n"
                        f"🖥️ **名称**: `{name}`\n"
                        f"🔗 **地址**: `{display_ip}`\n"
                        f"🕒 **时间**: `{current_time}`"
                    )
                    logger.info(f"🔔 [恢复] {name} 已上线")
                    asyncio.create_task(send_telegram_message(msg))
                    ALERT_CACHE[url] = 'online'
            else:
                # 1. 如果当前检测离线，计数器 +1
                current_count = FAILURE_COUNTS.get(url, 0) + 1
                FAILURE_COUNTS[url] = current_count
                
                # 2. 只有计数器达到阈值，才报警
                if current_count >= FAILURE_THRESHOLD:
                    if ALERT_CACHE.get(url) != 'offline':
                        msg = (
                            f"🔴 **警告：服务器离线**\n\n"
                            f"🖥️ **名称**: `{name}`\n"
                            f"🔗 **地址**: `{display_ip}`\n"
                            f"🕒 **时间**: `{current_time}`\n"
                            f"⚠️ **提示**: 连续监测，无法连接"
                        )
                        logger.warning(f"🔔 [报警] {name} 确认离线 (重试{current_count}次)")
                        asyncio.create_task(send_telegram_message(msg))
                        ALERT_CACHE[url] = 'offline'

    # 创建所有任务并执行
    tasks = [_check_single_server(s) for s in SERVERS_CACHE]
    await asyncio.gather(*tasks)
# 注册本地静态文件目录 
app.add_static_files('/static', 'static')

# ================= 定义流量同步任务 (AI 动态自适应 + 探针跳过版) =================
async def job_sync_all_traffic():
    logger.info("🕒 [智能同步] 检查同步任务进度...")
    
    # 目标周期：23.5 小时
    TARGET_DURATION = 84600 
    
    start_ts = ADMIN_CONFIG.get('sync_job_start', 0)
    current_idx = ADMIN_CONFIG.get('sync_job_index', 0)
    now = time.time()

    # 重置逻辑
    if (now - start_ts > 86400) or start_ts == 0 or current_idx >= len(SERVERS_CACHE):
        logger.info("🔄 [智能同步] 启动新一轮 24h 周期任务")
        start_ts = now
        current_idx = 0
        ADMIN_CONFIG['sync_job_start'] = start_ts
        ADMIN_CONFIG['sync_job_index'] = 0
        await save_admin_config()
    else:
        logger.info(f"♻️ [智能同步] 恢复进度: 第 {current_idx+1} 台")

    i = current_idx
    
    while True:
        current_total = len(SERVERS_CACHE)
        if i >= current_total: break
            
        try:
            server = SERVERS_CACHE[i]
            
            # 如果是探针/双模，直接跳过轮询与休眠 
            if server.get('probe_installed', False):
                # 仅在调试时或者是第一台时打印一下，防止日志刷屏
                # logger.info(f"⏩ [跳过轮询] {server.get('name')} (探针实时推送)")
                
                # 依然保存进度，防止重启后回滚
                i += 1
                ADMIN_CONFIG['sync_job_index'] = i
                
                # 每处理 10 个探针保存一次磁盘，减少 IO
                if i % 10 == 0: await save_admin_config()
                
                # 极速通过，仅做极短休眠防止 CPU 独占
                await asyncio.sleep(0.05)
                continue
            
            # === 下面是仅针对【纯 API 模式】的逻辑 ===
            loop_step_start = time.time()
            
            # 执行主动拉取
            await fetch_inbounds_safe(server, force_refresh=True, sync_name=False)
            
            progress = (i + 1) / current_total
            logger.info(f"⏳ [API轮询] {server.get('name')} 同步完成 ({progress:.1%})")

            # 保存进度
            ADMIN_CONFIG['sync_job_index'] = i + 1
            await save_admin_config()

            # 动态计算休眠 (仅针对需要轮询的机器)
            remaining_items = current_total - (i + 1)
            if remaining_items > 0:
                elapsed_time = time.time() - start_ts
                time_left = TARGET_DURATION - elapsed_time
                
                if time_left <= 0:
                    sleep_seconds = 1
                else:
                    # 重新计算剩余列表中，可能还有多少个 API 机器？
                    # 这里简单处理，假设剩下的都是 API 机器来计算间隔，保证 24小时 兜底
                    base_interval = time_left / remaining_items
                    sleep_seconds = base_interval * random.uniform(0.9, 1.1)
                    cost_time = time.time() - loop_step_start
                    sleep_seconds = max(1, sleep_seconds - cost_time)

                logger.info(f"💤 API 轮询休眠: {int(sleep_seconds)}秒...")
                await asyncio.sleep(sleep_seconds)
                
        except Exception as e:
            logger.warning(f"⚠️ 同步异常: {server.get('name')} - {e}")
            await asyncio.sleep(10)

        i += 1

    await save_nodes_cache()
    await refresh_dashboard_ui()
    logger.info("✅ [智能同步] 本轮任务全部完成")

# 2.================= 定时任务：IP 地理位置检查 & 自动修正名称 =================
async def job_check_geo_ip():
    logger.info("🌍 [定时任务] 开始全量 IP 归属地检测与名称修正...")
    data_changed = False
    
    # 1.  动态生成所有已知国旗列表 (防止漏判)
    known_flags = []
    for val in AUTO_COUNTRY_MAP.values():
        icon = val.split(' ')[0] # 提取 "🇺🇸", "🇯🇵" 等
        if icon and icon not in known_flags:
            known_flags.append(icon)
    
    for s in SERVERS_CACHE:
        old_name = s.get('name', '')
        new_name = old_name

        # --- 🧹 步骤 A: 强力清洗白旗  ---
        # 如果名字以 "🏳️ " 开头，且后面还有内容，直接把白旗切掉
        if new_name.startswith('🏳️ ') or new_name.startswith('🏳️'):
            # 只有当名字里除了白旗还有别的东西时才删，防止名字被删空
            if len(new_name) > 2:
                new_name = new_name.replace('🏳️', '').strip()
                logger.info(f"🧹 [清洗白旗] {old_name} -> {new_name}")

        # --- 🔍 步骤 B: 正常的 GeoIP 修正逻辑 ---
        # 检查现在的名字里有没有国旗
        has_flag = any(flag in new_name for flag in known_flags)
        
        if not has_flag:
            try:
                # 只有没国旗的时候，才去查 IP
                geo = await run.io_bound(fetch_geo_from_ip, s['url'])
                if geo:
                    s['lat'] = geo[0]; s['lon'] = geo[1]; s['_detected_region'] = geo[2]
                    
                    flag_prefix = get_flag_for_country(geo[2])
                    flag_icon = flag_prefix.split(' ')[0]
                    
                    # 加上正确的国旗
                    if flag_icon and flag_icon not in new_name:
                        new_name = f"{flag_icon} {new_name}"
                        logger.info(f"✨ [自动修正] {old_name} -> {new_name}")
            except: pass
        
        # 如果名字变了，标记需要保存
        if new_name != old_name:
            s['name'] = new_name
            data_changed = True

    if data_changed:
        await save_servers()
        await refresh_dashboard_ui()
        try: render_sidebar_content.refresh()
        except: pass
        safe_notify("✅ 已清理白旗并修正服务器名称", "positive")
    else:
        logger.info("✅ 名称检查完毕，无需修正")
        
# 3. 初始化调度器
scheduler = AsyncIOScheduler()

# 4. 系统启动序列
async def startup_sequence():
    global PROCESS_POOL
    # ✨ 初始化进程池 (4核) - 专门处理 Ping 等 CPU/阻塞任务
    PROCESS_POOL = ProcessPoolExecutor(max_workers=4)
    logger.info("🚀 进程池已启动 (ProcessPoolExecutor)")

    # ✨ 添加定时任务
    # 1. 流量同步 (3小时一次)
    scheduler.add_job(job_sync_all_traffic, 'interval', hours=24, id='traffic_sync', replace_existing=True, max_instances=1)
    
    # 2. 服务器状态监控与报警 (120秒一次) ✨✨✨
    scheduler.add_job(job_monitor_status, 'interval', seconds=120, id='status_monitor', replace_existing=True, max_instances=1)
    
    scheduler.start()
    logger.info("🕒 APScheduler 定时任务已启动")

    # ✨ 开机立即执行一次 (作为初始化)
    asyncio.create_task(job_sync_all_traffic())
    asyncio.create_task(job_check_geo_ip())
    
    # 首次运行填充状态缓存，避免刚开机就疯狂报警
    async def init_alert_cache():
        await asyncio.sleep(5) # 等待几秒让系统稳一下
        if ADMIN_CONFIG.get('tg_bot_token'):
            logger.info("🛡️ 正在初始化监控状态缓存...")
            await job_monitor_status()
            
    asyncio.create_task(init_alert_cache())

# 注册启动与关闭事件
app.on_startup(startup_sequence)
app.on_shutdown(lambda: PROCESS_POOL.shutdown(wait=False) if PROCESS_POOL else None)




    
# ================= [手机端] 详情弹窗  =================
def open_mobile_server_detail(server_conf):
    # 注入 CSS
    ui.add_head_html('''
        <style>
            .full-height-dialog { height: 85vh !important; max-height: 95vh !important; }
            @media (orientation: landscape) { .full-height-dialog { height: 95vh !important; } }
            .q-tabs__arrow { display: none !important; }
            .q-tabs__content { overflow: hidden !important; flex-wrap: nowrap !important; }
            .q-tab { cursor: pointer !important; min-height: 32px !important; }
            .q-tab__content { padding: 0 8px !important; }
            .detail-scroll-area, .detail-scroll-area .q-scrollarea__container, 
            .detail-scroll-area .q-scrollarea__content { width: 100% !important; max-width: 100% !important; }
            .q-dialog__inner--minimized > div { max-width: 95vw !important; }
            
            /* 卡片样式 (无缩放，仅变色) */
            .ping-card-base { border-width: 2px; border-style: solid; transition: all 0.3s; }
            .ping-card-inactive { border-color: transparent !important; opacity: 0.4; filter: grayscale(100%); }
        </style>
    ''')

    try:
        LABEL_STYLE = 'text-gray-500 font-bold text-[9px] md:text-[10px] uppercase tracking-wider' 
        VALUE_STYLE = 'text-gray-200 font-mono text-xs md:text-sm truncate font-bold'
        BORDER_STYLE = 'border border-white/10'
        CARD_BG = 'bg-[#1e293b]/50'
        
        # 状态管理
        visible_series = {0: True, 1: True, 2: True}
        is_smooth = {'value': False}

        with ui.dialog() as d, ui.card().classes(
            'p-0 overflow-hidden flex flex-col bg-[#0f172a] border border-slate-700 shadow-2xl full-height-dialog'
        ).style('width: 95vw; max-width: 900px; border-radius: 20px;'): 
            d.props('backdrop-filter="blur(10px)"')
            
            # --- 1. 顶部标题栏 ---
            with ui.row().classes('w-full items-center justify-between p-3 md:p-6 bg-[#1e293b] border-b border-slate-700 flex-shrink-0 flex-nowrap'):
                with ui.row().classes('items-center gap-3 overflow-hidden flex-nowrap'):
                    flag = "🏳️"
                    try: flag = detect_country_group(server_conf['name'], server_conf).split(' ')[0]
                    except: pass
                    ui.label(flag).classes('text-xl md:text-3xl flex-shrink-0') 
                    ui.label(server_conf['name']).classes('text-base md:text-lg font-black text-white truncate flex-grow')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=white')

            # --- 2. 内容滚动区 ---
            with ui.scroll_area().classes('w-full flex-grow detail-scroll-area'):
                with ui.column().classes('p-4 md:p-8 gap-4 w-full'):
                    refs = {} 
                    
                    # A. 系统信息模块
                    with ui.card().classes(f'w-full p-0 rounded-xl {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                        ui.label('系统信息').classes('text-[10px] font-black text-blue-500 m-3 mb-1 tracking-widest')
                        with ui.row().classes('w-full flex-wrap md:flex-nowrap items-stretch p-0'):
                            def info_row(label, key, value_cls=VALUE_STYLE):
                                with ui.row().classes('w-full items-center justify-between border-b border-white/5 pb-1.5 mb-1.5 last:border-0 last:mb-0'):
                                    ui.label(label).classes(LABEL_STYLE)
                                    refs[key] = ui.label('Loading...').classes(value_cls)
                            with ui.column().classes('w-full md:w-1/2 p-3 md:p-6 border-b md:border-b-0 md:border-r border-white/10 gap-1'):
                                info_row('CPU 型号', 'cpu_model'); info_row('操作系统', 'os')
                                info_row('内存', 'mem_detail'); info_row('总流量', 'traffic_detail')
                            with ui.column().classes('w-full md:w-1/2 p-3 md:p-6 gap-1'):
                                info_row('架构/虚拟', 'arch_virt')
                                info_row('硬盘', 'disk_detail')
                                info_row('实时网速', 'speed_detail', value_cls='text-blue-400 font-mono text-xs font-bold text-right')
                                info_row('系统负载', 'load')

                    # B. 三网延迟模块 (修复：点击仅变色，不移位)
                    with ui.card().classes(f'w-full p-3 rounded-xl {CARD_BG} {BORDER_STYLE}'):
                        ui.label('三网延迟 (点击切换)').classes('text-[10px] font-black text-purple-500 mb-2 tracking-widest')
                        with ui.grid().classes('w-full grid-cols-3 gap-2'):
                            
                            def toggle_series(idx, card_el, color_cls):
                                visible_series[idx] = not visible_series[idx]
                                if visible_series[idx]:
                                    # 选中：恢复颜色边框，移除透明边框和灰色滤镜
                                    card_el.classes(add=color_cls, remove='ping-card-inactive')
                                else:
                                    # 取消：添加透明边框和灰色滤镜，移除颜色边框
                                    card_el.classes(add='ping-card-inactive', remove=color_cls)
                                
                            def ping_box(name, color, key, idx):
                                color_border_cls = f'border-{color}-500' # 激活时的边框颜色
                                # 默认状态：激活
                                base_cls = f'bg-[#0f172a]/60 ping-card-base rounded-xl p-1.5 items-center flex flex-col cursor-pointer {color_border_cls}'
                                
                                with ui.element('div').classes(base_cls) as card:
                                    card.on('click', lambda _, i=idx, c=card, col=color_border_cls: toggle_series(i, c, col))
                                    ui.label(name).classes(f'text-{color}-400 font-bold text-[8px] whitespace-nowrap')
                                    refs[key] = ui.label('--').classes('text-white font-bold text-xs font-mono tracking-tighter')
                            
                            ping_box('电信', 'blue', 'ping_ct', 0)
                            ping_box('联通', 'orange', 'ping_cu', 1)
                            ping_box('移动', 'green', 'ping_cm', 2)

                    # C. 网络趋势模块
                    with ui.card().classes(f'w-full p-0 mb-2 rounded-xl {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                        
                        # 工具栏
                        with ui.row().classes('w-full justify-between items-center p-3 border-b border-white/5'):
                            with ui.row().classes('items-center gap-2'):
                                ui.label('网络趋势').classes('text-[10px] font-black text-teal-500 tracking-widest')
                                # 平滑开关
                                with ui.row().classes('items-center gap-1 cursor-pointer bg-white/5 px-2 py-0.5 rounded-full').on('click', lambda: [smooth_sw.set_value(not smooth_sw.value)]):
                                    smooth_sw = ui.switch().props('dense size=xs color=teal').classes('scale-75')
                                    ui.label('平滑').classes('text-[9px] text-gray-400 select-none')
                                    smooth_sw.on_value_change(lambda e: is_smooth.update({'value': e.value}))

                            with ui.tabs().props('dense no-caps hide-arrows active-color=blue-400 indicator-color=transparent').classes('bg-white/5 rounded-lg p-0.5') as chart_tabs:
                                t_1h = ui.tab('1h', label='1小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                                t_3h = ui.tab('3h', label='3小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                                t_6h = ui.tab('6h', label='6小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                            chart_tabs.set_value('1h')

                        # EWMA 算法
                        def calculate_ewma(data, alpha=0.3):
                            if not data: return []
                            result = [data[0]]
                            for i in range(1, len(data)):
                                result.append(alpha * data[i] + (1 - alpha) * result[-1])
                            return [int(x) for x in result]

                        chart = ui.echart({
                            'backgroundColor': 'transparent',
                            'color': ['#3b82f6', '#f97316', '#22c55e'], 
                            'legend': { 'show': False },
                            'tooltip': {
                                'trigger': 'axis',
                                'backgroundColor': 'rgba(15, 23, 42, 0.9)',
                                'borderColor': '#334155',
                                'textStyle': {'color': '#f1f5f9', 'fontSize': 10},
                                'axisPointer': {'type': 'line', 'lineStyle': {'color': '#94a3b8', 'width': 1, 'type': 'dashed'}},
                                'formatter': '{b}<br/>{a0}: {c0}ms<br/>{a1}: {c1}ms<br/>{a2}: {c2}ms'
                            },
                            'dataZoom': [
                                {'type': 'inside', 'xAxisIndex': 0, 'zoomLock': False}
                            ],
                            'grid': { 'left': '2%', 'right': '4%', 'bottom': '5%', 'top': '10%', 'containLabel': True },
                            'xAxis': { 'type': 'category', 'boundaryGap': False, 'data': [], 'axisLabel': { 'fontSize': 8, 'color': '#64748b' } },
                            'yAxis': { 'type': 'value', 'splitLine': { 'lineStyle': { 'color': 'rgba(255,255,255,0.05)' } }, 'axisLabel': { 'fontSize': 8, 'color': '#64748b' } },
                            'series': [
                                {'name': '电信', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}},
                                {'name': '联通', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}},
                                {'name': '移动', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}}
                            ]
                        }).classes('w-full h-64 md:h-72')

                async def update_dark_detail():
                    if not d.value: return
                    try:
                        status = await get_server_status(server_conf)
                        if not status: return
                        raw_cache = PROBE_DATA_CACHE.get(server_conf['url'], {})
                        static = raw_cache.get('static', {})
                        
                        refs['cpu_model'].set_text(status.get('cpu_model', static.get('cpu_model', 'Generic CPU')))
                        refs['os'].set_text(static.get('os', 'Linux'))
                        refs['mem_detail'].set_text(f"{int(status.get('mem_usage', 0))}% / {status.get('mem_total', 0)}G")
                        refs['arch_virt'].set_text(f"{static.get('arch', 'x64')} / {static.get('virt', 'kvm')}")
                        refs['disk_detail'].set_text(f"{int(status.get('disk_usage', 0))}% / {status.get('disk_total', 0)}G")
                        
                        def fmt_b(b): return format_bytes(b)
                        refs['traffic_detail'].set_text(f"↑{fmt_b(status.get('net_total_out', 0))} ↓{fmt_b(status.get('net_total_in', 0))}")
                        refs['speed_detail'].set_text(f"↑{fmt_b(status.get('net_speed_out', 0))}/s ↓{fmt_b(status.get('net_speed_in', 0))}/s")
                        refs['load'].set_text(str(status.get('load_1', 0)))
                        
                        pings = status.get('pings', {})
                        def fmt_p(v): return str(v) if v > 0 else "N/A"
                        refs['ping_ct'].set_text(fmt_p(pings.get('电信', -1)))
                        refs['ping_cu'].set_text(fmt_p(pings.get('联通', -1)))
                        refs['ping_cm'].set_text(fmt_p(pings.get('移动', -1)))

                        history_data = PING_TREND_CACHE.get(server_conf['url'], [])
                        if history_data:
                            import time
                            current_mode = chart_tabs.value
                            if current_mode == '1h': duration = 3600
                            elif current_mode == '3h': duration = 10800
                            elif current_mode == '6h': duration = 21600 
                            else: duration = 3600
                            
                            cutoff = time.time() - duration
                            sliced = [p for p in history_data if p['ts'] > cutoff]
                            
                            if sliced:
                                raw_ct = [p['ct'] for p in sliced]
                                raw_cu = [p['cu'] for p in sliced]
                                raw_cm = [p['cm'] for p in sliced]
                                times = [p['time_str'] for p in sliced]

                                if is_smooth['value']:
                                    final_ct = calculate_ewma(raw_ct)
                                    final_cu = calculate_ewma(raw_cu)
                                    final_cm = calculate_ewma(raw_cm)
                                else:
                                    final_ct, final_cu, final_cm = raw_ct, raw_cu, raw_cm

                                chart.options['xAxis']['data'] = times
                                chart.options['series'][0]['data'] = final_ct if visible_series[0] else []
                                chart.options['series'][1]['data'] = final_cu if visible_series[1] else []
                                chart.options['series'][2]['data'] = final_cm if visible_series[2] else []
                                
                                chart.update()
                    except: pass

                chart_tabs.on_value_change(update_dark_detail)

            # 3. 底部状态栏
            with ui.row().classes('w-full justify-center p-2 bg-[#0f172a] border-t border-white/5 flex-shrink-0'):
                ui.label(f"已运行: {PROBE_DATA_CACHE.get(server_conf['url'], {}).get('uptime', '-') or '-'}").classes('text-[10px] text-gray-500 font-mono')

        d.open()
        asyncio.create_task(update_dark_detail())
        timer = ui.timer(2.0, update_dark_detail)
        d.on('hide', lambda: timer.cancel())

    except Exception as e:
        print(f"Mobile Detail error: {e}")
        
# ================= [电脑端] 详情弹窗 =================
def open_pc_server_detail(server_conf):
    try:
        # 1. 获取当前主题状态
        is_dark = app.storage.user.get('is_dark', True)
        
        # 2. 定义双模样式 
        LABEL_STYLE = 'text-slate-500 dark:text-gray-400 text-sm font-medium'
        VALUE_STYLE = 'text-[#1e293b] dark:text-gray-200 font-mono text-sm font-bold'
        SECTION_TITLE = 'text-[#1e293b] dark:text-gray-200 text-base font-black mb-4 flex items-center gap-2'
        DIALOG_BG = 'bg-white/85 backdrop-blur-xl dark:bg-[#0d1117] dark:backdrop-blur-none'
        CARD_BG   = 'bg-white/60 dark:bg-[#161b22]' 
        BORDER_STYLE = 'border border-white/50 dark:border-[#30363d]'
        SHADOW_STYLE = 'shadow-[0_8px_32px_0_rgba(31,38,135,0.15)] dark:shadow-2xl'
        TRACK_COLOR = 'blue-1' if not is_dark else 'grey-9'

        visible_series = {0: True, 1: True, 2: True}
        is_smooth = {'value': False}

        # 智能容量格式化
        def fmt_capacity(b):
            if b is None: return "0 B"
            try:
                if isinstance(b, str):
                    import re
                    nums = re.findall(r"[-+]?\d*\.\d+|\d+", b)
                    val = float(nums[0]) if nums else 0
                else:
                    val = float(b)
                if val > 1024 * 1024:
                    if val < 1024**3: return f"{val/1024**2:.1f} MB"
                    return f"{val/1024**3:.1f} GB"
                if val > 0: return f"{val:.1f} GB"
                return "0 B"
            except:
                return str(b)

        ui.add_head_html('''
            <style>
                .ping-card-base { border-width: 2px; border-style: solid; transition: all 0.3s; }
                .ping-card-inactive { border-color: transparent !important; opacity: 0.4; filter: grayscale(100%); }
            </style>
        ''')
        
        with ui.dialog() as d, ui.card().classes(f'p-0 overflow-hidden flex flex-col {DIALOG_BG} {SHADOW_STYLE}').style('width: 1000px; max-width: 95vw; border-radius: 12px;'):
            
            # --- 标题栏 ---
            with ui.row().classes(f'w-full items-center justify-between p-4 {CARD_BG} border-b border-white/50 dark:border-[#30363d] flex-shrink-0'):
                with ui.row().classes('items-center gap-3'):
                    flag = "🏳️"
                    try: flag = detect_country_group(server_conf['name'], server_conf).split(' ')[0]
                    except: pass
                    ui.label(flag).classes('text-2xl')
                    ui.label(server_conf['name']).classes(f'text-lg font-bold text-[#1e293b] dark:text-white')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey-5')

            # --- 内容区 ---
            with ui.scroll_area().classes('w-full flex-grow p-6').style('height: 65vh;'):
                refs = {}
                
                # 第一行：左右分栏
                with ui.row().classes('w-full gap-6 no-wrap items-stretch'):
                    # 左侧：资源
                    with ui.column().classes(f'flex-1 p-5 rounded-xl {CARD_BG} {BORDER_STYLE} justify-between'):
                        ui.label('资源使用情况').classes(SECTION_TITLE)
                        
                        def progress_block(label, key, icon, color_class):
                            with ui.column().classes('w-full gap-1'):
                                with ui.row().classes('w-full justify-between items-end'):
                                    with ui.row().classes('items-center gap-2'):
                                        ui.icon(icon).classes('text-gray-400 dark:text-gray-500 text-xs'); ui.label(label).classes(LABEL_STYLE)
                                    refs[f'{key}_pct'] = ui.label('0.0%').classes('text-gray-500 dark:text-gray-400 text-xs font-mono')
                                refs[f'{key}_bar'] = ui.linear_progress(value=0, show_value=False).props(f'color={color_class} track-color={TRACK_COLOR}').classes('h-1.5 rounded-full')
                                with ui.row().classes('w-full justify-end'):
                                    # ✨ 修改默认占位符，不再显示 "-- / --"
                                    refs[f'{key}_val'] = ui.label('--').classes('text-[11px] text-gray-500 font-mono mt-1')
                        
                        progress_block('CPU', 'cpu', 'settings_suggest', 'blue-5')
                        progress_block('RAM', 'mem', 'memory', 'green-5')
                        progress_block('DISK', 'disk', 'storage', 'purple-5')

                    # 右侧：系统
                    with ui.column().classes(f'w-[400px] p-5 rounded-xl {CARD_BG} {BORDER_STYLE} justify-between'):
                        ui.label('系统资讯').classes(SECTION_TITLE)
                        def info_line(label, icon, key):
                            with ui.row().classes('w-full items-center justify-between py-3 border-b border-white/50 dark:border-[#30363d] last:border-0'):
                                with ui.row().classes('items-center gap-2'):
                                    ui.icon(icon).classes('text-gray-400 dark:text-gray-500 text-sm'); ui.label(label).classes(LABEL_STYLE)
                                refs[key] = ui.label('Loading...').classes(VALUE_STYLE)
                        info_line('作业系统', 'laptop_windows', 'os')
                        info_line('架构', 'developer_board', 'arch')
                        info_line('虚拟化', 'cloud_queue', 'virt')
                        info_line('在线时长', 'timer', 'uptime')

                # 第二行：延迟卡片
                with ui.row().classes('w-full gap-4 mt-6'):
                    def toggle_series(idx, card_el, color_cls):
                        visible_series[idx] = not visible_series[idx]
                        if visible_series[idx]:
                            card_el.classes(add=color_cls, remove='ping-card-inactive')
                        else:
                            card_el.classes(add='ping-card-inactive', remove=color_cls)

                    def ping_card(name, color, key, idx):
                        color_border_cls = f'border-{color}-500'
                        base_cls = f'flex-1 p-4 rounded-xl {CARD_BG} ping-card-base cursor-pointer {color_border_cls}'
                        with ui.element('div').classes(base_cls) as card:
                            card.on('click', lambda _, i=idx, c=card, col=color_border_cls: toggle_series(i, c, col))
                            with ui.row().classes('w-full justify-between items-center mb-1'):
                                ui.label(name).classes(f'text-{color}-500 text-xs font-bold')
                            with ui.row().classes('items-baseline gap-1'):
                                refs[f'{key}_cur'] = ui.label('--').classes(f'text-2xl font-black font-mono text-[#1e293b] dark:text-white')
                                ui.label('ms').classes('text-gray-500 text-[10px]')
                    ping_card('电信', 'blue', 'ping_ct', 0)
                    ping_card('联通', 'orange', 'ping_cu', 1)
                    ping_card('移动', 'green', 'ping_cm', 2)

                # 第三行：趋势图
                with ui.column().classes(f'w-full mt-6 p-5 rounded-xl {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                    with ui.row().classes('w-full justify-between items-center mb-4'):
                        with ui.row().classes('items-center gap-4'):
                            ui.label('网络质量趋势').classes(f'text-sm font-bold text-[#1e293b] dark:text-gray-200')
                            switch_bg = 'bg-blue-50/50 dark:bg-[#0d1117]'
                            with ui.row().classes(f'items-center gap-2 cursor-pointer {switch_bg} px-3 py-1 rounded-full border border-white/50 dark:border-[#30363d]').on('click', lambda: smooth_sw.set_value(not smooth_sw.value)):
                                smooth_sw = ui.switch().props('dense size=sm color=blue')
                                ui.label('平滑曲线').classes('text-xs text-slate-500 dark:text-gray-400 select-none')
                                smooth_sw.on_value_change(lambda e: is_smooth.update({'value': e.value}))
                        tab_bg = 'bg-blue-50/50 dark:bg-[#0d1117]'
                        with ui.tabs().props('dense no-caps indicator-color=blue active-color=blue').classes(f'{tab_bg} rounded-lg p-1') as chart_tabs:
                            tab_cls = 'px-4 text-xs text-slate-500 dark:text-gray-400'
                            ui.tab('1h', label='1小时').classes(tab_cls)
                            ui.tab('3h', label='3小时').classes(tab_cls)
                            ui.tab('6h', label='6小时').classes(tab_cls)
                        chart_tabs.set_value('1h')

                    def calculate_ewma(data, alpha=0.3):
                        if not data: return []
                        result = [data[0]]
                        for i in range(1, len(data)):
                            result.append(alpha * data[i] + (1 - alpha) * result[-1])
                        return [int(x) for x in result]

                    chart_text = '#64748b' if not is_dark else '#94a3b8'
                    split_line = '#e2e8f0' if not is_dark else '#30363d'
                    tooltip_bg = 'rgba(255, 255, 255, 0.95)' if not is_dark else 'rgba(13, 17, 23, 0.95)'
                    tooltip_border = '#cbd5e1' if not is_dark else '#30363d'
                    tooltip_text = '#334155' if not is_dark else '#e6edf3'

                    chart = ui.echart({
                        'backgroundColor': 'transparent', 
                        'color': ['#3b82f6', '#f97316', '#22c55e'], 
                        'legend': { 'show': False },
                        'tooltip': {
                            'trigger': 'axis', 'backgroundColor': tooltip_bg, 'borderColor': tooltip_border, 'textStyle': {'color': tooltip_text},
                            'axisPointer': {'type': 'line', 'lineStyle': {'color': '#8b949e', 'type': 'dashed'}},
                            'formatter': '{b}<br/>{a0}: {c0}ms<br/>{a1}: {c1}ms<br/>{a2}: {c2}ms'
                        },
                        'dataZoom': [{'type': 'inside', 'xAxisIndex': 0, 'zoomLock': False}],
                        'grid': { 'left': '1%', 'right': '1%', 'bottom': '5%', 'top': '15%', 'containLabel': True },
                        'xAxis': { 'type': 'category', 'boundaryGap': False, 'axisLabel': { 'color': chart_text } },
                        'yAxis': { 'type': 'value', 'splitLine': { 'lineStyle': { 'color': split_line } }, 'axisLabel': { 'color': chart_text } },
                        'series': [
                            {'name': '电信', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}},
                            {'name': '联通', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}},
                            {'name': '移动', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}}
                        ]
                    }).classes('w-full h-64')

                async def update_dark_detail():
                    if not d.value: return
                    try:
                        status = await get_server_status(server_conf)
                        raw_cache = PROBE_DATA_CACHE.get(server_conf['url'], {})
                        static = raw_cache.get('static', {})

                        # ✨✨✨ CPU 更新逻辑：百分比 + 核心数 ✨✨✨
                        cpu_val = float(status.get('cpu_usage', 0))
                        refs['cpu_pct'].set_text(f"{cpu_val:.1f}%") 
                        refs['cpu_bar'].set_value(cpu_val / 100)
                        
                        # ✨ 核心修复：强制获取并显示核心数 (格式如 "2 C")
                        c_cores = status.get('cpu_cores')
                        if not c_cores:
                            c_cores = static.get('cpu_cores') # 备用：从静态缓存读取
                        
                        if c_cores:
                            refs['cpu_val'].set_text(f"{c_cores} C")
                        else:
                            refs['cpu_val'].set_text("--")

                        # ✨✨✨ 内存 百分比 + 容量 ✨✨✨
                        mem_p = float(status.get('mem_usage', 0))
                        refs['mem_pct'].set_text(f"{mem_p:.1f}%") 
                        refs['mem_bar'].set_value(mem_p / 100)
                        
                        mem_t_raw = status.get('mem_total', 0)
                        total_str = fmt_capacity(mem_t_raw)
                        used_str = "--"
                        if status.get('mem_used'):
                            used_str = fmt_capacity(status.get('mem_used'))
                        else:
                            # 估算已用
                            try:
                                val_t = float(re.findall(r"[-+]?\d*\.\d+|\d+", str(mem_t_raw))[0]) if isinstance(mem_t_raw, str) else float(mem_t_raw)
                                numeric_used = val_t * (mem_p / 100.0)
                                used_str = fmt_capacity(numeric_used)
                            except: pass
                        refs['mem_val'].set_text(f"{used_str} / {total_str}")

                        # ✨✨✨ 硬盘 百分比 + 容量 ✨✨✨
                        disk_p = float(status.get('disk_usage', 0))
                        refs['disk_pct'].set_text(f"{disk_p:.1f}%")
                        refs['disk_bar'].set_value(disk_p / 100)
                        
                        disk_t_raw = status.get('disk_total', 0)
                        disk_total_str = fmt_capacity(disk_t_raw)
                        disk_used_str = "--"
                        if status.get('disk_used'):
                            disk_used_str = fmt_capacity(status.get('disk_used'))
                        else:
                            # 估算已用
                            try:
                                val_d = float(re.findall(r"[-+]?\d*\.\d+|\d+", str(disk_t_raw))[0]) if isinstance(disk_t_raw, str) else float(disk_t_raw)
                                numeric_disk_used = val_d * (disk_p / 100.0)
                                disk_used_str = fmt_capacity(numeric_disk_used)
                            except: pass
                        refs['disk_val'].set_text(f"{disk_used_str} / {disk_total_str}")

                        # 系统信息
                        raw_arch = static.get('arch', '').lower()
                        display_arch = "AMD" if "x86" in raw_arch or "amd" in raw_arch else "ARM" if "arm" in raw_arch or "aarch" in raw_arch else raw_arch.upper()
                        refs['os'].set_text(static.get('os', 'Linux')); refs['arch'].set_text(display_arch); refs['virt'].set_text(static.get('virt', 'kvm'))
                        
                        uptime_str = str(status.get('uptime', '-')).replace('up ', '').replace('days', '天').replace('hours', '时').replace('minutes', '分')
                        refs['uptime'].set_text(uptime_str); refs['uptime'].classes('text-green-500')

                        # 延迟
                        pings = status.get('pings', {})
                        refs['ping_ct_cur'].set_text(str(pings.get('电信', 'N/A')))
                        refs['ping_cu_cur'].set_text(str(pings.get('联通', 'N/A')))
                        refs['ping_cm_cur'].set_text(str(pings.get('移动', 'N/A')))

                        # 图表
                        history_data = PING_TREND_CACHE.get(server_conf['url'], [])
                        if history_data:
                            import time
                            current_mode = chart_tabs.value
                            duration = 3600
                            if current_mode == '3h': duration = 10800
                            elif current_mode == '6h': duration = 21600 
                            
                            cutoff = time.time() - duration
                            sliced = [p for p in history_data if p['ts'] > cutoff]
                            if sliced:
                                raw_ct = [p['ct'] for p in sliced]
                                raw_cu = [p['cu'] for p in sliced]
                                raw_cm = [p['cm'] for p in sliced]
                                times = [p['time_str'] for p in sliced]
                                
                                final_ct = calculate_ewma(raw_ct) if is_smooth['value'] else raw_ct
                                final_cu = calculate_ewma(raw_cu) if is_smooth['value'] else raw_cu
                                final_cm = calculate_ewma(raw_cm) if is_smooth['value'] else raw_cm
                                
                                chart.options['xAxis']['data'] = times
                                chart.options['series'][0]['data'] = final_ct if visible_series[0] else []
                                chart.options['series'][1]['data'] = final_cu if visible_series[1] else []
                                chart.options['series'][2]['data'] = final_cm if visible_series[2] else []
                                chart.update()
                    except: pass

                chart_tabs.on_value_change(update_dark_detail)

            # --- 底部 ---
            with ui.row().classes(f'w-full justify-center p-2 {CARD_BG} border-t border-white/50 dark:border-[#30363d]'):
                ui.label('Powered by X-Fusion Monitor').classes('text-[10px] text-gray-500 dark:text-gray-600 font-mono italic')

        d.open()
        asyncio.create_task(update_dark_detail())
        timer = ui.timer(2.0, update_dark_detail)
        d.on('hide', lambda: timer.cancel())
    except Exception as e:
        print(f"PC Detail Error: {e}")
       
# ================= 全局变量 =================
# 用于记录当前探针页面选中的标签，防止刷新重置
CURRENT_PROBE_TAB = 'ALL' 

# ================= 移动端检测辅助函数 =================
def is_mobile_device(request: Request) -> bool:
    """通过 User-Agent 判断是否为移动设备"""
    user_agent = request.headers.get('user-agent', '').lower()
    mobile_keywords = [
        'android', 'iphone', 'ipad', 'iemobile', 
        'opera mini', 'mobile', 'harmonyos'
    ]
    return any(keyword in user_agent for keyword in mobile_keywords)

# ================= 核心：/status 统一入口 =================
@ui.page('/status')
async def status_page_router(request: Request):
    """
    路由分发器：
    1. 检测设备类型
    2. 手机端调用 render_mobile_status_page()
    3. 电脑端调用 render_desktop_status_page()
    """
    if is_mobile_device(request):
        # 针对手机进行极简渲染，防止硬件加速导致的浏览器崩溃
        await render_mobile_status_page()
    else:
        # 恢复 V30 版本的酷炫地图大屏显示
        await render_desktop_status_page()


# ================= 电脑端大屏辅助全局变量  =================        
import asyncio 
import traceback

# ================= 核心：/status 电脑端大屏显示 =================
async def render_desktop_status_page():
    global CURRENT_PROBE_TAB
    
    # 1. 启用 Dark Mode
    dark_mode = ui.dark_mode()
    if app.storage.user.get('is_dark') is None:
        app.storage.user['is_dark'] = True
    dark_mode.value = app.storage.user.get('is_dark')

    # 2. 资源注入
    ui.add_head_html('<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>')
    ui.add_head_html('<link href="https://use.fontawesome.com/releases/v6.4.0/css/all.css" rel="stylesheet">')
    
    # [CSS 样式注入] 集成 Twemoji 字体修复 Win 系统国旗显示 
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
        <style>
            @font-face {
                font-family: 'Twemoji Country Flags';
                src: url('https://cdn.jsdelivr.net/npm/country-flag-emoji-polyfill@0.1/dist/TwemojiCountryFlags.woff2') format('woff2');
                unicode-range: U+1F1E6-1F1FF, U+1F3F4, U+E0062-E007F;
            }
            body { 
                margin: 0; 
                font-family: "Twemoji Country Flags", "Noto Color Emoji", "Segoe UI Emoji", "Noto Sans SC", sans-serif; 
                transition: background-color 0.3s ease; 
            }
            body:not(.body--dark) { background: linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%); }
            body.body--dark { background-color: #0b1121; }
            .status-card { transition: all 0.3s ease; border-radius: 16px; }
            body:not(.body--dark) .status-card { background: rgba(255, 255, 255, 0.95); border: 1px solid rgba(255, 255, 255, 0.8); box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1); color: #1e293b; }
            body.body--dark .status-card { background: #1e293b; border: 1px solid rgba(255,255,255,0.05); box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3); color: #e2e8f0; }
            .status-card:hover { transform: translateY(-3px); }
            .offline-card { border-color: rgba(239, 68, 68, 0.6) !important; }
            body.body--dark .offline-card { background-image: repeating-linear-gradient(45deg, rgba(239, 68, 68, 0.05) 0px, rgba(239, 68, 68, 0.05) 10px, transparent 10px, transparent 20px) !important; }
            body:not(.body--dark) .offline-card { background: rgba(254, 226, 226, 0.95) !important; }
            .scrollbar-hide::-webkit-scrollbar { display: none; }
            .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
            .prog-bar { transition: width 0.5s ease-out; }
            #public-map-container { contain: strict; transform: translateZ(0); will-change: transform; z-index: 0; }
        </style>
    ''')
    
    RENDERED_CARDS = {} 
    tab_container = None
    grid_container = None
    header_refs = {}
    pie_chart_ref = None
    pagination_ref = None 
    local_ui_version = GLOBAL_UI_VERSION
    
    # 状态管理
    page_state = {
        'page': 1,
        'group': 'ALL'
    }

    def get_probe_groups():
        groups_list = ['ALL']
        customs = ADMIN_CONFIG.get('probe_custom_groups', [])
        groups_list.extend(customs) 
        return groups_list
    
    def fmt_capacity(b):
        if b is None: return "0 B"
        try:
            if isinstance(b, str):
                import re
                nums = re.findall(r"[-+]?\d*\.\d+|\d+", b)
                val = float(nums[0]) if nums else 0
            else:
                val = float(b)
            if val > 1024 * 1024:
                if val < 1024**3: return f"{val/1024**2:.1f} MB"
                return f"{val/1024**3:.1f} GB"
            if val > 0: return f"{val:.1f} GB"
            return "0 B"
        except: return str(b)

    def fmt_traffic(b): return f"{round(b/1024**3, 1)}G" if b > 1024**3 else f"{int(b/1024**2)}M"
    def fmt_speed(b): return f"{int(b)} B" if b < 1024 else (f"{int(b/1024)} K" if b < 1024**2 else f"{int(b/1024**2)} M")

    try:
        chart_data, pie_data, region_count, region_stats_json, centroids_json = prepare_map_data()
    except Exception as e:
        chart_data = '{"cities": [], "flags": [], "regions": []}'
        pie_data = []; region_count = 0; region_stats_json = "{}"; centroids_json = "{}"

    # ================= UI 布局 =================
    with ui.element('div').classes('fixed top-0 left-0 w-full h-[35vh] min-h-[300px] max-h-[500px] z-0').style('z-index: 0; contain: size layout paint;'): 
        ui.html('<div id="public-map-container" style="width:100%; height:100%;"></div>', sanitize=False).classes('w-full h-full')

    with ui.column().classes('w-full h-screen p-0 gap-0 overflow-hidden flex flex-col absolute top-0 left-0 pointer-events-none'):
        with ui.element('div').classes('w-full h-[35vh] min-h-[300px] max-h-[500px] relative p-0 shrink-0 pointer-events-none'):
            with ui.row().classes('absolute top-6 left-8 right-8 z-50 justify-between items-start pointer-events-auto'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('public', color='blue').classes('text-3xl drop-shadow-[0_0_10px_rgba(59,130,246,0.8)]')
                        ui.label('X-Fusion Status').classes('text-2xl font-black text-slate-800 dark:text-white drop-shadow-md')
                    with ui.row().classes('gap-4 text-sm font-bold font-mono pl-1'):
                        with ui.row().classes('items-center gap-1'):
                            ui.element('div').classes('w-2 h-2 rounded-full bg-green-500 shadow-[0_0_5px_rgba(34,197,94,0.8)]')
                            header_refs['online_count'] = ui.label('在线: --').classes('text-slate-600 dark:text-slate-300 drop-shadow-sm')
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('language').classes('text-blue-500 dark:text-blue-400 text-xs drop-shadow-sm')
                            header_refs['region_count'] = ui.label(f'分布区域: {region_count}').classes('text-slate-600 dark:text-slate-300 drop-shadow-sm')
                with ui.row().classes('items-center gap-2'):
                    def toggle_dark():
                        dark_mode.value = not dark_mode.value
                        app.storage.user['is_dark'] = dark_mode.value
                        if pie_chart_ref:
                            color = '#e2e8f0' if dark_mode.value else '#334155'
                            pie_chart_ref.options['legend']['textStyle']['color'] = color
                            pie_chart_ref.update()
                        ui.run_javascript(f'if(window.changeTheme) window.changeTheme({str(dark_mode.value).lower()});')
                    ui.button(icon='dark_mode', on_click=toggle_dark).props('flat round dense').classes('text-slate-700 dark:text-yellow-400 bg-white/50')
                    ui.button('后台管理', icon='login', on_click=lambda: ui.navigate.to('/login')).props('flat dense').classes('font-bold text-xs text-slate-700 dark:text-slate-300 bg-white/50 rounded px-2')
            with ui.element('div').classes('absolute left-4 bottom-4 z-40 pointer-events-auto'):
                text_color = '#e2e8f0' if dark_mode.value else '#334155'
                pie_chart_ref = ui.echart({'backgroundColor': 'transparent', 'tooltip': {'trigger': 'item'}, 'legend': {'bottom': '0%', 'left': 'center', 'icon': 'circle', 'itemGap': 15, 'textStyle': {'color': text_color, 'fontSize': 11}}, 'series': [{'type': 'pie', 'radius': ['35%', '60%'], 'center': ['50%', '35%'], 'avoidLabelOverlap': False, 'itemStyle': {'borderRadius': 4, 'borderColor': 'transparent', 'borderWidth': 2}, 'label': {'show': False}, 'emphasis': {'scale': True, 'scaleSize': 10, 'label': {'show': True, 'color': 'auto', 'fontWeight': 'bold'}, 'itemStyle': {'shadowBlur': 10, 'shadowOffsetX': 0, 'shadowColor': 'rgba(0, 0, 0, 0.5)'}}, 'data': pie_data}]}).classes('w-64 h-72')

        with ui.column().classes('w-full flex-grow relative gap-0 overflow-hidden flex flex-col bg-white/80 dark:bg-[#0f172a]/90 backdrop-blur-xl pointer-events-auto border-t border-white/10').style('z-index: 10; contain: content;'): 
            with ui.row().classes('w-full px-6 py-2 border-b border-gray-200/50 dark:border-gray-800 items-center shrink-0 justify-between'):
                with ui.element('div').classes('flex-grow overflow-x-auto whitespace-nowrap scrollbar-hide mr-4') as tab_container: pass 
                pagination_ref = ui.row().classes('items-center')

            with ui.scroll_area().classes('w-full flex-grow p-4 md:p-6'):
                grid_container = ui.grid().classes('w-full gap-4 md:gap-5 pb-20').style('grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))')

    # ================= 渲染逻辑 (含分页) =================
    
    def render_tabs():
        tab_container.clear()
        groups = get_probe_groups(); global CURRENT_PROBE_TAB 
        if CURRENT_PROBE_TAB not in groups: CURRENT_PROBE_TAB = 'ALL'
        page_state['group'] = CURRENT_PROBE_TAB
        
        with tab_container:
            with ui.tabs().props('dense no-caps align=left active-color=blue indicator-color=blue').classes('text-slate-600 dark:text-gray-500 bg-transparent') as tabs:
                ui.tab('ALL', label='全部').on('click', lambda: apply_filter('ALL'))
                for g in groups:
                    if g == 'ALL': continue
                    ui.tab(g).on('click', lambda _, g=g: apply_filter(g))
                tabs.set_value(CURRENT_PROBE_TAB)

    # ================= ✨✨✨ 优化后的卡片渲染与更新逻辑 ✨✨✨ =================

    # 1. 抽离出的通用 UI 更新函数 (用于：1.创建时立即回显缓存 2.定时任务更新)
    def update_card_ui(refs, status, static):
        if not status: return
        
        is_probe_online = (status.get('status') == 'online')
        
        if is_probe_online:
            refs['status_icon'].set_name('bolt'); refs['status_icon'].classes(replace='text-green-500', remove='text-gray-400 text-red-500 text-purple-400')
            refs['online_dot'].classes(replace='bg-green-500', remove='bg-gray-500 bg-red-500 bg-purple-500')
        else:
            if status.get('cpu_usage') is not None:
                refs['status_icon'].set_name('api'); refs['status_icon'].classes(replace='text-purple-400', remove='text-gray-400 text-red-500 text-green-500')
                refs['online_dot'].classes(replace='bg-purple-500', remove='bg-gray-500 bg-red-500 bg-green-500')
            else:
                refs['status_icon'].set_name('flash_off'); refs['status_icon'].classes(replace='text-red-500', remove='text-green-500 text-gray-400 text-purple-400')
                refs['online_dot'].classes(replace='bg-red-500', remove='bg-green-500 bg-orange-500 bg-purple-500')

        os_str = static.get('os', 'Linux')
        import re
        simple_os = re.sub(r' GNU/Linux', '', os_str, flags=re.I)
        refs['os_info'].set_text(f"{simple_os}")
        
        cores = status.get('cpu_cores')
        refs['summary_cores'].set_text(f"{cores} C" if cores else "N/A")
        refs['summary_ram'].set_text(fmt_capacity(status.get('mem_total', 0)))
        refs['summary_disk'].set_text(fmt_capacity(status.get('disk_total', 0)))
        
        refs['traf_up'].set_text(f"↑ {fmt_traffic(status.get('net_total_out', 0))}")
        refs['traf_down'].set_text(f"↓ {fmt_traffic(status.get('net_total_in', 0))}")

        cpu = float(status.get('cpu_usage', 0))
        refs['cpu_bar'].style(f'width: {cpu}%'); refs['cpu_pct'].set_text(f'{cpu:.1f}%')
        c_num = status.get('cpu_cores', 1); refs['cpu_sub'].set_text(f"{c_num} Cores")
        
        mem = float(status.get('mem_usage', 0))
        refs['mem_bar'].style(f'width: {mem}%'); refs['mem_pct'].set_text(f'{mem:.1f}%')
        mem_total = float(status.get('mem_total', 0))
        if mem_total > 0:
            mem_val_used = mem_total * (mem / 100.0)
            refs['mem_sub'].set_text(f"{fmt_capacity(mem_val_used)} / {fmt_capacity(mem_total)}")
        else: refs['mem_sub'].set_text(f"{mem:.1f}%")

        disk = float(status.get('disk_usage', 0))
        refs['disk_bar'].style(f'width: {disk}%'); refs['disk_pct'].set_text(f'{disk:.1f}%')
        disk_total = float(status.get('disk_total', 0))
        if disk_total > 0:
            disk_val_used = disk_total * (disk / 100.0)
            refs['disk_sub'].set_text(f"{fmt_capacity(disk_val_used)} / {fmt_capacity(disk_total)}")
        else: refs['disk_sub'].set_text(f"{disk:.1f}%")

        n_up = status.get('net_speed_out', 0); n_down = status.get('net_speed_in', 0)
        refs['net_up'].set_text(f"↑ {fmt_speed(n_up)}/s"); refs['net_down'].set_text(f"↓ {fmt_speed(n_down)}/s")

        up = str(status.get('uptime', '-'))
        colored_up = re.sub(r'(\d+)(\s*(?:days?|天))', r'<span class="text-green-500 font-bold text-sm">\1</span>\2', up, flags=re.IGNORECASE)
        refs['uptime'].set_content(colored_up)

# 2. 自动更新循环 
    async def card_autoupdate_loop(url):
        # 获取服务器配置
        current_server = next((s for s in SERVERS_CACHE if s['url'] == url), None)
        if not current_server: return

        # 判断是否安装了探针
        is_probe = current_server.get('probe_installed', False)

        # 🛑 如果没有安装探针，直接结束此协程！
        # 这样前端卡片就不会每分钟去骚扰后台了
        if not is_probe:
            return 

        # --- 首次启动延迟 ---
        await asyncio.sleep(random.uniform(0.5, 3.0))
        
        while True:
            # --- 基础检查 ---
            if url not in RENDERED_CARDS: break 
            if url not in [s['url'] for s in SERVERS_CACHE]: break
            
            item = RENDERED_CARDS.get(url)
            if not item: break 
            
            # 省流模式：标签页不可见时暂停
            if not item['card'].visible: 
                await asyncio.sleep(5.0) 
                continue 
                    
            # 执行获取数据
            current_server = next((s for s in SERVERS_CACHE if s['url'] == url), None)
            if current_server:
                res = None
                try: 
                    res = await asyncio.wait_for(get_server_status(current_server), timeout=5.0)
                except: res = None
                
                if res:
                    raw_cache = PROBE_DATA_CACHE.get(url, {})
                    static = raw_cache.get('static', {})
                    update_card_ui(item['refs'], res, static)
                    
                    is_online = (res.get('status') == 'online')
                    if is_online: item['card'].classes(remove='offline-card')
                    else: item['card'].classes(add='offline-card')

            # 探针刷新间隔
            await asyncio.sleep(random.uniform(2.0, 3.0))

    # 3. 创建卡片 (✨✨✨ 创建时立即回显 ✨✨✨)
    def create_server_card(s):
        url = s['url']; refs = {}
        
        cached_data = PROBE_DATA_CACHE.get(url, {})
        initial_status = None
        if cached_data:
            initial_status = cached_data.copy()
            if 'pings' not in initial_status: initial_status['pings'] = {}
        
        with grid_container:
            with ui.card().classes('status-card w-full p-4 md:p-5 flex flex-col gap-2 md:gap-3 relative overflow-hidden group').style('contain: content;') as card:
                refs['card'] = card
                with ui.row().classes('w-full items-center mb-1 gap-2 flex-nowrap'):
                    flag = "🏳️"; 
                    try: flag = detect_country_group(s['name'], s).split(' ')[0]
                    except: pass
                    ui.label(flag).classes('text-2xl md:text-3xl flex-shrink-0 leading-none') 
                    ui.label(s['name']).classes('text-base md:text-lg font-bold text-slate-800 dark:text-gray-100 truncate flex-grow min-w-0 cursor-pointer hover:text-blue-500 transition leading-tight').on('click', lambda _, s=s: open_pc_server_detail(s))
                    refs['status_icon'] = ui.icon('bolt').props('size=32px').classes('text-gray-400 flex-shrink-0')
                with ui.row().classes('w-full justify-between items-center px-1 mb-2'):
                    with ui.row().classes('items-center gap-1.5'):
                        ui.icon('dns').classes('text-xs text-gray-400'); ui.label('OS').classes('text-xs text-slate-500 dark:text-gray-400 font-bold')
                    with ui.row().classes('items-center gap-1.5'):
                        refs['os_icon'] = ui.icon('computer').classes('text-xs text-slate-400'); refs['os_info'] = ui.label('Loading...').classes('text-xs font-mono font-bold text-slate-700 dark:text-gray-300 whitespace-nowrap')
                ui.separator().classes('mb-3 opacity-50 dark:opacity-30')
                with ui.row().classes('w-full justify-between px-1 mb-1 md:mb-2'):
                    label_cls = 'text-xs font-mono text-slate-500 dark:text-gray-400 font-bold'
                    with ui.row().classes('items-center gap-1'): ui.icon('grid_view').classes('text-blue-500 dark:text-blue-400 text-xs'); refs['summary_cores'] = ui.label('--').classes(label_cls)
                    with ui.row().classes('items-center gap-1'): ui.icon('memory').classes('text-green-500 dark:text-green-400 text-xs'); refs['summary_ram'] = ui.label('--').classes(label_cls)
                    with ui.row().classes('items-center gap-1'): ui.icon('storage').classes('text-purple-500 dark:text-purple-400 text-xs'); refs['summary_disk'] = ui.label('--').classes(label_cls)
                with ui.column().classes('w-full gap-2 md:gap-3'):
                    def stat_row(label, color_cls, light_track_color):
                        with ui.column().classes('w-full gap-1'):
                            with ui.row().classes('w-full items-center justify-between'):
                                ui.label(label).classes('text-xs text-slate-500 dark:text-gray-500 font-bold w-8')
                                with ui.element('div').classes(f'flex-grow h-2 md:h-2.5 bg-{light_track_color} dark:bg-gray-700/50 rounded-full overflow-hidden mx-2 transition-colors'):
                                    bar = ui.element('div').classes(f'h-full {color_cls} prog-bar').style('width: 0%')
                                pct = ui.label('0%').classes('text-xs font-mono font-bold text-slate-700 dark:text-white w-8 text-right')
                            sub = ui.label('').classes('text-[10px] text-slate-400 dark:text-gray-500 font-mono text-right w-full pr-1')
                        return bar, pct, sub
                    refs['cpu_bar'], refs['cpu_pct'], refs['cpu_sub'] = stat_row('CPU', 'bg-blue-500', 'blue-100')
                    refs['mem_bar'], refs['mem_pct'], refs['mem_sub'] = stat_row('内存', 'bg-green-500', 'green-100')
                    refs['disk_bar'], refs['disk_pct'], refs['disk_sub'] = stat_row('硬盘', 'bg-purple-500', 'purple-100')
                ui.separator().classes('bg-slate-200 dark:bg-white/5 my-1')
                with ui.column().classes('w-full gap-1'):
                    label_sub_cls = 'text-xs text-slate-400 dark:text-gray-500'
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('网络').classes(label_sub_cls); 
                        with ui.row().classes('gap-2 font-mono whitespace-nowrap'): refs['net_up'] = ui.label('↑ 0B').classes('text-xs text-orange-500 dark:text-orange-400 font-bold'); refs['net_down'] = ui.label('↓ 0B').classes('text-xs text-green-600 dark:text-green-400 font-bold')
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('流量').classes(label_sub_cls)
                        with ui.row().classes('gap-2 font-mono whitespace-nowrap text-xs text-slate-600 dark:text-gray-300'): refs['traf_up'] = ui.label('↑ 0B'); refs['traf_down'] = ui.label('↓ 0B')
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('在线').classes(label_sub_cls)
                        with ui.row().classes('items-center gap-1'): refs['uptime'] = ui.html('--', sanitize=False).classes('text-xs font-mono text-slate-600 dark:text-gray-300 text-right'); refs['online_dot'] = ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-gray-400')

        # ✨✨✨ 立即应用缓存数据 (防止页面白屏闪烁) ✨✨✨
        if initial_status:
            static = cached_data.get('static', {})
            update_card_ui(refs, initial_status, static)
            is_cached_online = (initial_status.get('status') == 'online') or (initial_status.get('cpu_usage') is not None)
            if is_cached_online: card.classes(remove='offline-card')
            else: card.classes(add='offline-card')

        RENDERED_CARDS[url] = {'card': card, 'refs': refs, 'data': s}
        asyncio.create_task(card_autoupdate_loop(url))

    def apply_filter(group_name):
        global CURRENT_PROBE_TAB; CURRENT_PROBE_TAB = group_name
        page_state['group'] = group_name
        page_state['page'] = 1 
        render_grid_page()

    def change_page(new_page):
        page_state['page'] = new_page
        render_grid_page()

    # ================= 核心：分页渲染逻辑  =================
    def render_grid_page():
        grid_container.clear()
        pagination_ref.clear()
        RENDERED_CARDS.clear()

        group_name = page_state['group']
        filtered_servers = []
        try: sorted_all = sorted(SERVERS_CACHE, key=lambda x: x.get('name', ''))
        except: sorted_all = SERVERS_CACHE
        
        for s in sorted_all:
            if group_name == 'ALL' or (group_name in s.get('tags', [])):
                filtered_servers.append(s)

        PAGE_SIZE = 60
        total_items = len(filtered_servers)
        total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
        if page_state['page'] > total_pages: page_state['page'] = 1
        if page_state['page'] < 1: page_state['page'] = 1
        
        start_idx = (page_state['page'] - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_items = filtered_servers[start_idx:end_idx]

        if not current_page_items:
            with grid_container:
                ui.label('暂无服务器').classes('text-gray-500 dark:text-gray-400 col-span-full text-center mt-10')
        else:
            for s in current_page_items:
                create_server_card(s)

        if total_pages > 1:
            with pagination_ref:
                p = ui.pagination(1, total_pages, direction_links=True).props('dense color=blue outline rounded text-color=white active-color=blue active-text-color=white max-pages=7')
                p.value = page_state['page']
                p.on('update:model-value', lambda e: change_page(e.args))
                ui.label(f'共 {total_items} 台').classes('text-xs text-gray-400 ml-4 self-center')

    render_tabs()
    render_grid_page()
    
    # [JS 逻辑注入] 地图渲染 + 修复字体样式 + 调整悬浮窗宽度 + 区域高亮修复 
    ui.run_javascript(f'''
    (function() {{
        var mapData = {chart_data}; 
        window.regionStats = {region_stats_json}; 
        window.countryCentroids = {centroids_json}; 
        
        var defaultPt = [116.40, 39.90]; 
        var defaultZoom = 1.35; 
        var focusedZoom = 4.0; 
        var isZoomed = false; 
        var myChart = null;

        function tryIpLocation() {{
            fetch('https://ipapi.co/json/')
                .then(response => response.json())
                .then(data => {{
                    if(data.latitude && data.longitude) {{
                        defaultPt = [data.longitude, data.latitude];
                        if(!isZoomed && myChart) renderMap();
                    }}
                }})
                .catch(e => {{}});
        }}

        function checkAndRender() {{
            var chartDom = document.getElementById('public-map-container');
            if (!chartDom || typeof echarts === 'undefined') {{ setTimeout(checkAndRender, 100); return; }}
            
            fetch('/static/world.json').then(r => r.json()).then(w => {{
                echarts.registerMap('world', w); 
                myChart = echarts.init(chartDom); 
                window.publicMapChart = myChart; 
                
                if (navigator.geolocation) {{ 
                    navigator.geolocation.getCurrentPosition(
                        p => {{ 
                            defaultPt = [p.coords.longitude, p.coords.latitude]; 
                            if(!isZoomed) renderMap(); 
                        }},
                        e => {{ tryIpLocation(); }}
                    ); 
                }} else {{ tryIpLocation(); }}
                
                renderMap();
                
                function renderMap(center, zoomLevel, roamState) {{
                    var viewCenter = center || defaultPt;
                    var viewZoom = zoomLevel || defaultZoom;
                    var viewRoam = roamState !== undefined ? roamState : false;
                    var mapLeft = isZoomed ? 'center' : '55%'; 
                    var mapTop = '1%';

                    var lines = mapData.cities.map(pt => ({{ coords: [pt.value, defaultPt] }}));
                    
                    var isDark = document.body.classList.contains('body--dark');
                    var areaColor = isDark ? '#1B2631' : '#e0e7ff'; 
                    var borderColor = isDark ? '#404a59' : '#a5b4fc'; 
                    
                    // 双色主题定义
                    var ttBg = isDark ? 'rgba(23, 23, 23, 0.95)' : 'rgba(255, 255, 255, 0.95)';
                    var ttTextMain = isDark ? '#fff' : '#1e293b';
                    var ttTextSub = isDark ? 'rgba(255, 255, 255, 0.6)' : 'rgba(30, 41, 59, 0.6)';
                    var ttBorder = isDark ? '1px solid rgba(255,255,255,0.1)' : '1px solid #e2e8f0';

                    // 字体优化
                    var emojiFont = "'Twemoji Country Flags', 'Noto Sans SC', 'Roboto', 'Helvetica Neue', 'Arial', sans-serif";

                    // ✨✨✨ [核心修复]：构建高亮区域配置 ✨✨✨
                    var highlightFill = isDark ? 'rgba(37, 99, 235, 0.4)' : 'rgba(147, 197, 253, 0.5)'; // 蓝色半透明
                    var highlightStroke = isDark ? '#3b82f6' : '#2563eb'; // 边框颜色
                    
                    var activeRegions = mapData.regions || [];
                    var geoRegions = activeRegions.map(function(name) {{
                        return {{
                            name: name,
                            itemStyle: {{ 
                                areaColor: highlightFill, 
                                borderColor: highlightStroke,
                                borderWidth: 1.5,
                                opacity: 1
                            }},
                            emphasis: {{
                                itemStyle: {{
                                    areaColor: highlightFill,
                                    borderColor: '#60a5fa',
                                    borderWidth: 2
                                }}
                            }}
                        }};
                    }});

                    var option = {{
                        backgroundColor: 'transparent',
                        tooltip: {{
                            show: true, trigger: 'item', padding: 0, backgroundColor: 'transparent', borderColor: 'transparent',
                            formatter: function(params) {{
                                var searchKey = params.name;
                                if (params.data && params.data.country_key) searchKey = params.data.country_key;
                                var stats = window.regionStats[searchKey];
                                if (!stats) return; // 没有数据的区域不显示弹窗
                                
                                var serverListHtml = '';
                                var displayLimit = 5; 
                                var servers = stats.servers || []; 
                                
                                for (var i = 0; i < Math.min(servers.length, displayLimit); i++) {{
                                    var s = servers[i];
                                    var isOnline = s.status === 'online';
                                    var statusColor = isOnline ? '#22c55e' : '#ef4444'; 
                                    var statusText = isOnline ? '在线' : '离线';
                                    
                                    serverListHtml += `
                                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; line-height: 1.2;">
                                            <div style="display: flex; align-items: center; max-width: 170px;">
                                                <span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background-color: ${{statusColor}}; margin-right: 8px; flex-shrink: 0;"></span>
                                                <span style="font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${{s.name}}</span>
                                            </div>
                                            <span style="font-size: 12px; color: ${{ttTextSub}}; flex-shrink: 0; margin-left: 8px;">${{statusText}}</span>
                                        </div>
                                    `;
                                }}
                                
                                if (servers.length > displayLimit) {{
                                    serverListHtml += `<div style="font-size: 11px; color: ${{ttTextSub}}; margin-top: 8px; text-align: right; opacity: 0.8;">+${{servers.length - displayLimit}} 更多...</div>`;
                                }}
                                
                                return `<div style="background:${{ttBg}}; border:${{ttBorder}}; padding: 14px 16px; border-radius: 10px; color:${{ttTextMain}}; font-family: ${{emojiFont}}; box-shadow: 0 4px 16px rgba(0,0,0,0.3); min-width: 240px; max-width: 260px; pointer-events: none;">
                                    <div style="font-size: 16px; font-weight: 700; margin-bottom: 2px; display: flex; align-items: center; letter-spacing: 0.5px;">
                                        <span style="margin-right: 8px; font-size: 20px;">${{stats.flag}}</span>${{stats.cn}}
                                    </div>
                                    <div style="font-size: 12px; color: ${{ttTextSub}}; margin-bottom: 12px; font-weight: 400;">
                                        共 ${{stats.total}} 台服务器, ${{stats.online}} 台在线
                                    </div>
                                    <div style="border-top: 1px solid ${{isDark ? 'rgba(255,255,255,0.08)' : '#f1f5f9'}}; padding-top: 10px; margin-top: 4px;">
                                        ${{serverListHtml}}
                                    </div>
                                </div>`;
                            }}
                        }},
                        geo: {{
                            map: 'world', left: mapLeft, top: mapTop, roam: viewRoam, zoom: viewZoom, center: viewCenter,
                            aspectScale: 0.85, label: {{ show: false }},
                            itemStyle: {{ areaColor: areaColor, borderColor: borderColor, borderWidth: 1 }},
                            emphasis: {{ itemStyle: {{ areaColor: isDark ? '#1e3a8a' : '#bfdbfe' }} }},
                            
                            // 🛑 核心修复：注入区域高亮配置
                            regions: geoRegions 
                        }},
                        series: [
                            {{ type: 'lines', zlevel: 2, effect: {{ show: true, period: 4, trailLength: 0.5, color: '#00ffff', symbol: 'arrow', symbolSize: 6 }}, lineStyle: {{ color: '#00ffff', width: 0, curveness: 0.2, opacity: 0 }}, data: lines, silent: true }},
                            {{ type: 'effectScatter', coordinateSystem: 'geo', zlevel: 3, rippleEffect: {{ brushType: 'stroke', scale: 2.5 }}, itemStyle: {{ color: '#00ffff' }}, data: mapData.cities }},
                            
                            {{ 
                                type: 'scatter', coordinateSystem: 'geo', zlevel: 6, symbolSize: 0, 
                                label: {{ 
                                    show: true, position: 'top', formatter: '{{b}}', 
                                    color: isDark?'#fff':'#1e293b', fontSize: 16, offset: [0, -5],
                                    fontFamily: emojiFont 
                                }}, 
                                data: mapData.flags 
                            }},
                            
                            {{ type: 'effectScatter', coordinateSystem: 'geo', zlevel: 5, itemStyle: {{ color: '#f59e0b' }}, label: {{ show: true, position: 'bottom', formatter: 'My PC', color: '#f59e0b', fontWeight: 'bold' }}, data: [{{ value: defaultPt }}] }}
                        ]
                    }};
                    myChart.setOption(option, true);
                }}
                
                window.updatePublicMap = function(newData) {{ 
                    if (!newData) return; mapData = newData; 
                    renderMap(isZoomed ? myChart.getOption().geo[0].center : defaultPt, isZoomed ? myChart.getOption().geo[0].zoom : defaultZoom, isZoomed ? 'move' : false); 
                }};
                
                myChart.on('click', function(params) {{
                    var searchKey = params.name;
                    if (params.data && params.data.country_key) searchKey = params.data.country_key;
                    var targetCoord = window.countryCentroids[searchKey];
                    if (targetCoord) {{ isZoomed = true; renderMap(targetCoord, focusedZoom, 'move'); }}
                }});
                
                myChart.getZr().on('mousewheel', function() {{ if(isZoomed) {{ isZoomed = false; renderMap(defaultPt, defaultZoom, false); }} }});
                window.changeTheme = function(isDark) {{ renderMap(undefined, undefined, undefined); }}; 
                window.addEventListener('resize', () => myChart.resize());
            }});
        }}
        checkAndRender();
    }})();
    ''')
    # ================= 循环更新逻辑  =================
    async def loop_update():
        nonlocal local_ui_version
        try:
            # 1. 检查版本号，如果变动则重绘架构 
            if GLOBAL_UI_VERSION != local_ui_version:
                local_ui_version = GLOBAL_UI_VERSION
                render_tabs(); render_grid_page() 
                try: new_map, _, new_cnt, new_stats, new_centroids = prepare_map_data()
                except: new_map = "{}"; new_cnt = 0; new_stats = "{}"; new_centroids = "{}"
                if header_refs.get('region_count'): header_refs['region_count'].set_text(f'分布区域: {new_cnt}')
                ui.run_javascript(f'''if(window.updatePublicMap){{ window.regionStats = {new_stats}; window.countryCentroids = {new_centroids}; window.updatePublicMap({new_map}); }}''')
            
            # 2.实时统计在线数量
            real_online_count = 0
            now_ts = time.time()
            
            for s in SERVERS_CACHE:
                is_node_online = False
                
                # A. 优先检查探针心跳 (20秒内有更新算在线)
                probe_cache = PROBE_DATA_CACHE.get(s['url'])
                if probe_cache and (now_ts - probe_cache.get('last_updated', 0) < 20):
                    is_node_online = True
                
                # B. 兼容旧状态字段 (如果探针没在线，看下系统标记)
                elif s.get('_status') == 'online':
                    is_node_online = True
                
                if is_node_online:
                    real_online_count += 1

            # 3. 更新 UI 文字
            if header_refs.get('online_count'): 
                header_refs['online_count'].set_text(f'在线: {real_online_count}')
                
        except Exception as e: 
            pass # 忽略临时错误
            
        ui.timer(5.0, loop_update, once=True)

    ui.timer(0.1, loop_update, once=True)
    
# ================= 手机端专用：实时动效 Dashboard 最终完整版 =================
async def render_mobile_status_page():
    global CURRENT_PROBE_TAB
    # 用于存储 UI 组件引用的字典，实现局部刷新
    mobile_refs = {}

    # 1. 注入复刻样式的 CSS
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
        <style>
            body { background-color: #0d0d0d; color: #ffffff; margin: 0; padding: 0; overflow-x: hidden; }
            .mobile-header { background: #1a1a1a; border-bottom: 1px solid #333; position: sticky; top: 0; z-index: 100; padding: 12px 16px; }
            .mobile-card-container { display: flex; flex-direction: column; align-items: center; width: 100%; padding: 12px 0; }
            .mobile-card { 
                background: #1a1a1a; border-radius: 16px; padding: 18px; border: 1px solid #333;
                width: calc(100% - 24px); margin-bottom: 16px; box-sizing: border-box;
            }
            .inner-module {
                background: #242424; border-radius: 12px; padding: 12px; height: 95px;
                display: flex; flex-direction: column; justify-content: space-between;
            }
            .stat-header { display: flex; justify-content: space-between; align-items: center; }
            .stat-label-box { display: flex; align-items: center; gap: 4px; }
            .stat-icon { font-size: 14px !important; color: #888; }
            .stat-label { color: #888; font-size: 11px; font-weight: bold; }
            .stat-value { color: #fff; font-size: 17px; font-weight: 800; font-family: monospace; }
            .bar-bg { height: 5px; background: #333; border-radius: 3px; overflow: hidden; margin: 2px 0; }
            .bar-fill-cpu { height: 100%; background: #3b82f6; transition: width 0.6s; box-shadow: 0 0 5px #3b82f6; }
            .bar-fill-mem { height: 100%; background: #22c55e; transition: width 0.6s; box-shadow: 0 0 5px #22c55e; }
            .bar-fill-disk { height: 100%; background: #a855f7; }
            .stat-subtext { color: #555; font-size: 10px; font-family: monospace; font-weight: bold; }
            .speed-up { color: #22c55e; font-weight: bold; font-size: 11px; }
            .speed-down { color: #3b82f6; font-weight: bold; font-size: 11px; }
            .scrollbar-hide::-webkit-scrollbar { display: none; }
        </style>
    ''')

    # --- 2. 顶部与标签栏 ---
    with ui.column().classes('mobile-header w-full gap-1'):
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('X-Fusion Status').classes('text-lg font-black text-blue-400')
            ui.button(icon='login', on_click=lambda: ui.navigate.to('/login')).props('flat dense color=grey-5')
        online_count = len([s for s in SERVERS_CACHE if s.get('_status') == 'online'])
        ui.label(f'🟢 {online_count} ONLINE / {len(SERVERS_CACHE)} TOTAL').classes('text-[10px] font-bold text-gray-500 tracking-widest')

    with ui.row().classes('w-full px-2 py-1 bg-[#0d0d0d] border-b border-[#333] overflow-x-auto whitespace-nowrap scrollbar-hide'):
        groups = ['ALL'] + ADMIN_CONFIG.get('probe_custom_groups', [])
        with ui.tabs().props('dense no-caps active-color=blue-400 indicator-color=blue-400').classes('text-gray-500') as tabs:
            for g in groups:
                ui.tab(g, label='全部' if g=='ALL' else g).on('click', lambda _, group=g: update_mobile_tab(group))
            tabs.set_value(CURRENT_PROBE_TAB)

    list_container = ui.column().classes('mobile-card-container')

    # --- 3. 渲染函数 ---
    async def render_list(target_group):
        list_container.clear()
        mobile_refs.clear()
        
        filtered = [s for s in SERVERS_CACHE if target_group == 'ALL' or target_group in s.get('tags', [])]
        filtered.sort(key=lambda x: (0 if x.get('_status')=='online' else 1, x.get('name', '')))

        with list_container:
            for s in filtered:
                status = PROBE_DATA_CACHE.get(s['url'], {})
                static = status.get('static', {})
                is_online = s.get('_status') == 'online'
                srv_ref = {}
                
                with ui.column().classes('mobile-card').on('click', lambda _, srv=s: open_mobile_server_detail(srv)):
                    # 标题与描述
                    with ui.row().classes('items-center gap-3 mb-3'):
                        flag = "🏳️"
                        try: flag = detect_country_group(s['name'], s).split(' ')[0]
                        except: pass
                        ui.label(flag).classes('text-3xl')
                        ui.label(s['name']).classes('text-base font-bold truncate').style('max-width:200px')

                    # 2x2 宫格布局
                    with ui.grid().classes('w-full grid-cols-2 gap-3'):
                        # CPU 模块
                        cpu = status.get('cpu_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">settings_suggest</span><span class="stat-label">CPU</span></div>', sanitize=False)
                                srv_ref['cpu_text'] = ui.label(f'{cpu}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                srv_ref['cpu_bar'] = ui.element('div').classes('bar-fill-cpu').style(f'width: {cpu}%')
                            ui.label(f"{status.get('cpu_cores', 1)} Cores").classes('stat-subtext')

                        # RAM 模块
                        mem_p = status.get('mem_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">memory</span><span class="stat-label">RAM</span></div>', sanitize=False)
                                srv_ref['mem_text'] = ui.label(f'{int(mem_p)}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                srv_ref['mem_bar'] = ui.element('div').classes('bar-fill-mem').style(f'width: {mem_p}%')
                            srv_ref['mem_detail'] = ui.label('-- / --').classes('stat-subtext')

                        # DISK 模块
                        disk_p = status.get('disk_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">storage</span><span class="stat-label">DISK</span></div>', sanitize=False)
                                ui.label(f'{int(disk_p)}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                ui.element('div').classes('bar-fill-disk').style(f'width: {disk_p}%')
                            ui.label(f"{status.get('disk_total', 0)}G Total").classes('stat-subtext')

                        # SPEED 模块
                        with ui.element('div').classes('inner-module'):
                            ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">swap_calls</span><span class="stat-label">SPEED</span></div>', sanitize=False)
                            with ui.column().classes('w-full gap-0'):
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('↑').classes('speed-up')
                                    srv_ref['net_up'] = ui.label('--').classes('text-[12px] font-mono font-bold')
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('↓').classes('speed-down')
                                    srv_ref['net_down'] = ui.label('--').classes('text-[12px] font-mono font-bold')

                    # 底部状态
                    with ui.row().classes('w-full justify-between mt-3 pt-2 border-t border-[#333] items-center'):
                        # 修改点：左侧显示绿色加粗的在线时长
                        srv_ref['uptime'] = ui.label("在线时长：--").classes('text-[10px] font-bold text-green-500 font-mono')
                        with ui.row().classes('items-center gap-2'):
                            # 修改点：闪电图标引用 srv_ref['load']，动态展示 load_1 数据
                            srv_ref['load'] = ui.label(f"⚡ {status.get('load_1', '0.0')}").classes('text-[10px] text-gray-400 font-bold')
                            ui.label('ACTIVE' if is_online else 'DOWN').classes(f'text-[10px] font-black {"text-green-500" if is_online else "text-red-400"}')
                
                mobile_refs[s['url']] = srv_ref

    # --- 4. 实时同步逻辑 ---
    def fmt_speed(b):
        if b < 1024: return f"{int(b)}B"
        return f"{int(b/1024)}K" if b < 1024**2 else f"{round(b/1024**2,1)}M"

    async def mobile_sync_loop():
        for url, refs in mobile_refs.items():
            status = PROBE_DATA_CACHE.get(url, {})
            if not status: continue
            
            # 更新网速
            refs['net_up'].set_text(f"{fmt_speed(status.get('net_speed_out', 0))}/s")
            refs['net_down'].set_text(f"{fmt_speed(status.get('net_speed_in', 0))}/s")
            
            # 更新 CPU & RAM
            cpu = status.get('cpu_usage', 0)
            mem_p = status.get('mem_usage', 0)
            refs['cpu_text'].set_text(f"{cpu}%")
            refs['cpu_bar'].style(f"width: {cpu}%")
            refs['mem_text'].set_text(f"{int(mem_p)}%")
            refs['mem_bar'].style(f"width: {mem_p}%")
            
            # 内存详情
            mem_t = status.get('mem_total', 0)
            mem_u = round(float(mem_t or 0) * (float(mem_p or 0)/100), 2)
            refs['mem_detail'].set_text(f"{mem_u}G / {mem_t}G")
            
            # Uptime 格式化处理：将 "up 81 days, 11:08" 转换为 "在线时长：81天 11时 8分"
            raw_uptime = str(status.get('uptime', '-'))
            formatted_uptime = raw_uptime.replace('up ', '').replace(' days, ', '天 ').replace(' day, ', '天 ')
            if ':' in formatted_uptime:
                parts = formatted_uptime.split(' ')
                time_parts = parts[-1].split(':')
                h = time_parts[0]
                m = time_parts[1]
                # 重新拼接
                prefix = "".join(parts[:-1])
                formatted_uptime = f"{prefix}{h}时 {m}分"
            
            refs['uptime'].set_text(f"在线时长：{formatted_uptime}")
            
            # Load 更新：显示实时负载数据
            refs['load'].set_text(f"⚡ {status.get('load_1', '0.0')}")

    async def update_mobile_tab(val):
        global CURRENT_PROBE_TAB
        CURRENT_PROBE_TAB = val
        await render_list(val)

    await render_list(CURRENT_PROBE_TAB)
    ui.timer(5.0, mobile_sync_loop)
    
if __name__ in {"__main__", "__mp_main__"}:
    logger.info("🚀 系统正在初始化...")
    
    ui.run(
        title='X-Fusion Panel', 
        host='0.0.0.0', 
        port=8080, 
        language='zh-CN', 
        storage_secret='sijuly_secret_key', 
        reload=False, 
        reconnect_timeout=600.0,
        # 尝试直接传参（如果你的版本支持透传参数给 uvicorn）
        ws_ping_interval=20,
        ws_ping_timeout=20,
        timeout_keep_alive=60
    )
