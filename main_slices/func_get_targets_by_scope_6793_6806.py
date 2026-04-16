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