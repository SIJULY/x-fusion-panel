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