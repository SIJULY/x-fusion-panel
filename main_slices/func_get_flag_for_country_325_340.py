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