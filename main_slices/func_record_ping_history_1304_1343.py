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