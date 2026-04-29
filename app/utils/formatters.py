import re


CN_NUM_MAP = {'〇': 0, '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def format_bytes(size):
    if not size:
        return '0 B'
    power = 2**10
    n = 0
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"


def format_uptime(seconds):
    """将秒数转换为 天/小时/分钟"""
    if not seconds:
        return "未知"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return f"{d}天 {h}小时 {m}分"


def cn_to_arabic_str(match):
    s = match.group()
    if not s:
        return s
    if '十' in s:
        val = 0
        parts = s.split('十')
        if parts[0]:
            val += CN_NUM_MAP.get(parts[0], 0) * 10
        else:
            val += 10
        if len(parts) > 1 and parts[1]:
            val += CN_NUM_MAP.get(parts[1], 0)
        return str(val)
    return "".join(str(CN_NUM_MAP.get(c, 0)) for c in s)


def to_safe_sort_list(items):
    """确保列表可排序：[(权重, 值), ...]"""
    safe_list = []
    for item in items:
        if isinstance(item, int):
            safe_list.append((1, item))
        else:
            safe_list.append((0, str(item).lower()))
    return safe_list


def smart_sort_key(server_info):
    name = server_info.get('name', '')
    if not name:
        return []

    try:
        name_normalized = re.sub(r'[零一二三四五六七八九十]+', cn_to_arabic_str, name)
    except:
        name_normalized = name

    try:
        if '|' in name_normalized:
            parts = name_normalized.split('|', 1)
            p1 = parts[0].strip()
            rest = parts[1].strip()
        else:
            p1 = name_normalized
            rest = ""

        p2 = ""
        if ' ' in rest:
            parts = rest.split(' ', 1)
            p2 = parts[0].strip()
            rest = parts[1].strip()

        sub_parts = rest.split('-')
        p3 = sub_parts[0].strip()

        p3_num = 0
        p3_text = p3
        p3_match = re.search(r'(\d+)$', p3)
        if p3_match:
            p3_num = int(p3_match.group(1))
            p3_text = p3[:p3_match.start()]

        p4 = ""
        p5 = 0
        if len(sub_parts) >= 2:
            p4 = sub_parts[1].strip()
        if len(sub_parts) >= 3:
            last = sub_parts[-1].strip()
            if last.isdigit():
                p5 = int(last)
            else:
                p4 += f"-{last}"
        elif len(sub_parts) == 2 and sub_parts[1].strip().isdigit():
            p5 = int(sub_parts[1].strip())

        return to_safe_sort_list([p1, p2, p3_text, p3_num, p4, p5])

    except:
        parts = re.split(r'(\d+)', name_normalized)
        mixed_list = [int(text) if text.isdigit() else text for text in parts]
        return to_safe_sort_list(mixed_list)
