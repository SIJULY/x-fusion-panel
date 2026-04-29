import requests
from nicegui import run

from app.core.config import AUTO_COUNTRY_MAP, LOCATION_COORDS, MATCH_MAP
from app.core.state import IP_GEO_CACHE


COUNTRY_GROUP_ALIASES = {
    '🇨🇳 中国': ['🇨🇳', '中国', 'china', 'cn', 'prc', 'beijing', '北京', 'shanghai', '上海', 'guangzhou', '广州', 'shenzhen', '深圳', 'hangzhou', '杭州'],
    '🇭🇰 香港': ['🇭🇰', '香港', 'hong kong', 'hongkong', 'hk', 'hkg'],
    '🇲🇴 澳门': ['🇲🇴', '澳门', 'macau', 'macao', 'mo'],
    '🇹🇼 台湾': ['🇹🇼', '台湾', 'taiwan', 'tw', 'taipei', '台北'],
    '🇯🇵 日本': ['🇯🇵', '日本', 'japan', 'jp', 'jpn', 'tokyo', '东京', 'osaka', '大阪'],
    '🇸🇬 新加坡': ['🇸🇬', '新加坡', 'singapore', 'sg', 'sgp'],
    '🇰🇷 韩国': ['🇰🇷', '韩国', 'korea', 'south korea', 'kr', 'kor', 'seoul', '首尔'],
    '🇰🇵 朝鲜': ['🇰🇵', '朝鲜', 'north korea', 'kp'],
    '🇮🇳 印度': ['🇮🇳', '印度', 'india', 'in', 'ind', 'mumbai', '孟买', 'delhi', '德里'],
    '🇮🇩 印度尼西亚': ['🇮🇩', '印度尼西亚', '印尼', 'indonesia', 'id', 'jakarta', '雅加达'],
    '🇲🇾 马来西亚': ['🇲🇾', '马来西亚', 'malaysia', 'my', 'kuala lumpur', '吉隆坡'],
    '🇹🇭 泰国': ['🇹🇭', '泰国', 'thailand', 'th', 'bangkok', '曼谷'],
    '🇻🇳 越南': ['🇻🇳', '越南', 'vietnam', 'viet nam', 'vn', 'hanoi', '河内', 'ho chi minh', '胡志明'],
    '🇵🇭 菲律宾': ['🇵🇭', '菲律宾', 'philippines', 'ph', 'manila', '马尼拉'],
    '🇦🇺 澳大利亚': ['🇦🇺', '澳大利亚', '澳洲', 'australia', 'au', 'aus', 'sydney', '悉尼', 'melbourne', '墨尔本'],
    '🇳🇿 新西兰': ['🇳🇿', '新西兰', 'new zealand', 'nz', 'auckland', '奥克兰'],
    '🇮🇱 以色列': ['🇮🇱', '以色列', 'israel', 'il', 'tel aviv', '特拉维夫'],
    '🇹🇷 土耳其': ['🇹🇷', '土耳其', 'turkey', 'tr', 'tur', 'istanbul', '伊斯坦布尔'],
    '🇦🇪 阿联酋': ['🇦🇪', '阿联酋', '阿拉伯联合酋长国', 'uae', 'united arab emirates', 'ae', 'dubai', '迪拜', 'abu dhabi', '阿布扎比'],
    '🇺🇸 美国': ['🇺🇸', '美国', 'united states', 'usa', 'america', 'us', 'ny', 'new york', '纽约', 'los angeles', '洛杉矶', 'san jose', '圣何塞', 'seattle', '西雅图', 'chicago', '芝加哥', 'dallas', '达拉斯', 'phoenix', '凤凰城'],
    '🇨🇦 加拿大': ['🇨🇦', '加拿大', 'canada', 'ca', 'can', 'toronto', '多伦多', 'vancouver', '温哥华', 'montreal', '蒙特利尔'],
    '🇲🇽 墨西哥': ['🇲🇽', '墨西哥', 'mexico', 'mx', 'mex', 'mexico city', '墨西哥城'],
    '🇧🇷 巴西': ['🇧🇷', '巴西', 'brazil', 'br', 'bra', 'sao paulo', '圣保罗', 'rio', '里约'],
    '🇨🇱 智利': ['🇨🇱', '智利', 'chile', 'cl', 'santiago', '圣地亚哥'],
    '🇦🇷 阿根廷': ['🇦🇷', '阿根廷', 'argentina', 'ar', 'arg', 'buenos aires', '布宜诺斯艾利斯'],
    '🇨🇴 哥伦比亚': ['🇨🇴', '哥伦比亚', 'colombia', 'co', 'col', 'bogota', '波哥大'],
    '🇵🇪 秘鲁': ['🇵🇪', '秘鲁', 'peru', 'pe', 'lima', '利马'],
    '🇬🇧 英国': ['🇬🇧', '英国', 'united kingdom', 'great britain', 'england', 'uk', 'gb', 'gbr', 'london', '伦敦', 'manchester', '曼彻斯特', 'birmingham', '伯明翰'],
    '🇩🇪 德国': ['🇩🇪', '德国', 'germany', 'de', 'deu', 'frankfurt', '法兰克福', 'berlin', '柏林', 'munich', '慕尼黑', 'dusseldorf', '杜塞尔多夫'],
    '🇫🇷 法国': ['🇫🇷', '法国', 'france', 'fr', 'fra', 'paris', '巴黎', 'lyon', '里昂', 'marseille', '马赛'],
    '🇳🇱 荷兰': ['🇳🇱', '荷兰', 'netherlands', 'the netherlands', 'nl', 'nld', 'amsterdam', '阿姆斯特丹', 'rotterdam', '鹿特丹'],
    '🇷🇺 俄罗斯': ['🇷🇺', '俄罗斯', 'russia', 'russian federation', 'ru', 'rus', 'moscow', '莫斯科'],
    '🇮🇹 意大利': ['🇮🇹', '意大利', 'italy', 'it', 'ita', 'milan', '米兰', 'rome', '罗马'],
    '🇪🇸 西班牙': ['🇪🇸', '西班牙', 'spain', 'es', 'esp', 'madrid', '马德里', 'barcelona', '巴塞罗那'],
    '🇸🇪 瑞典': ['🇸🇪', '瑞典', 'sweden', 'se', 'swe', 'stockholm', '斯德哥尔摩'],
    '🇨🇭 瑞士': ['🇨🇭', '瑞士', 'switzerland', 'ch', 'che', 'zurich', '苏黎世', 'geneva', '日内瓦'],
    '🇿🇦 南非': ['🇿🇦', '南非', 'south africa', 'za', 'johannesburg', '约翰内斯堡', 'cape town', '开普敦'],
}

ECHARTS_REGION_ALIASES = {
    alias: canonical.split(' ', 1)[1 if ' ' in canonical else 0]
    for canonical, aliases in COUNTRY_GROUP_ALIASES.items()
    for alias in aliases
}


def fetch_geo_from_ip(host):
    try:
        clean_host = host.split('://')[-1].split(':')[0]
        if clean_host.startswith('192.168.') or clean_host.startswith('10.') or clean_host == '127.0.0.1':
            return None
        if clean_host in IP_GEO_CACHE:
            return IP_GEO_CACHE[clean_host]

        with requests.Session() as s:
            url = f"http://ip-api.com/json/{clean_host}?lang=zh-CN&fields=status,lat,lon,country,regionName,city"
            r = s.get(url, timeout=3)
            if r.status_code == 200:
                data = r.json()
                if data.get('status') == 'success':
                    result = (data['lat'], data['lon'], data['country'], data.get('regionName', '未知'))
                    IP_GEO_CACHE[clean_host] = result
                    return result
    except:
        pass
    return None


def get_coords_from_name(name):
    for k in sorted(LOCATION_COORDS.keys(), key=len, reverse=True):
        if k in name:
            return LOCATION_COORDS[k]
    return None


def get_flag_for_country(country_name):
    if not country_name:
        return "🏳️ 未知"

    country_name_str = str(country_name).strip()
    country_name_lower = country_name_str.lower()

    for canonical, aliases in COUNTRY_GROUP_ALIASES.items():
        if country_name_str == canonical:
            return canonical
        if any(alias and alias.lower() in country_name_lower for alias in aliases):
            return canonical

    for k, v in AUTO_COUNTRY_MAP.items():
        if k.upper() == country_name_str.upper() or k in country_name_str:
            return v

    for v in AUTO_COUNTRY_MAP.values():
        if country_name_str in v:
            return v

    return f"🏳️ {country_name_str}"


async def auto_prepend_flag(name, url):
    """
    检查名字是否已经包含任意已知国旗。
    - 如果包含：直接返回原名（尊重用户填写或面板自带的国旗）。
    - 如果不包含：根据 IP 归属地自动添加。
    """
    if not name:
        return name

    for v in AUTO_COUNTRY_MAP.values():
        flag_icon = v.split(' ')[0]
        if flag_icon in name:
            return name

    try:
        geo_info = await run.io_bound(fetch_geo_from_ip, url)
        if not geo_info:
            return name

        country_name = geo_info[2]
        flag_group = get_flag_for_country(country_name)
        flag_icon = flag_group.split(' ')[0]

        if flag_icon in name:
            return name

        return f"{flag_icon} {name}"
    except Exception:
        return name


def detect_country_group(name, server_config=None):
    if server_config:
        saved_group = server_config.get('group')
        if saved_group and saved_group.strip() and saved_group not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区', '其他地区']:
            return get_flag_for_country(saved_group)

    import re

    name_str = str(name or '').strip()
    name_upper = name_str.upper()
    name_lower = name_str.lower()

    for canonical, aliases in COUNTRY_GROUP_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if not alias:
                continue
            alias_lower = alias.lower()
            if alias_lower not in name_lower:
                continue
            if len(alias) <= 3 and alias.isascii() and alias.isalpha():
                pattern = r'(?<![A-Z0-9])' + re.escape(alias.upper()) + r'(?![A-Z0-9])'
                if re.search(pattern, name_upper):
                    return canonical
            else:
                return canonical

    sorted_keys = sorted(AUTO_COUNTRY_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        val = AUTO_COUNTRY_MAP[key]
        if key in name_upper:
            if len(key) <= 3 and key.isalpha():
                pattern = r'(?<![A-Z0-9])' + re.escape(key) + r'(?![A-Z0-9])'
                if re.search(pattern, name_upper):
                    return val
            else:
                return val

    if server_config and server_config.get('_detected_region'):
        return get_flag_for_country(server_config['_detected_region'])

    return '🏳️ 其他地区'


def get_echarts_region_name(name_raw):
    if not name_raw:
        return None

    name_str = str(name_raw).strip()
    name_upper = name_str.upper()
    name_lower = name_str.lower()

    for alias in sorted(ECHARTS_REGION_ALIASES.keys(), key=len, reverse=True):
        if alias.lower() in name_lower:
            return ECHARTS_REGION_ALIASES[alias]

    sorted_keys = sorted(MATCH_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in name_upper:
            return MATCH_MAP[key]
    return None
