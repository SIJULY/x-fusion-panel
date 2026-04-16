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