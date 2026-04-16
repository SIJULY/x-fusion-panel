def get_echarts_region_name(name_raw):
    if not name_raw: return None
    name = name_raw.upper()
    sorted_keys = sorted(MATCH_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in name: return MATCH_MAP[key]
    return None