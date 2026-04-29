from nicegui import ui

from app.utils.encoding import safe_base64
from app.utils.network import get_dynamic_origin


async def copy_group_link(group_name, target=None):
    try:
        origin = get_dynamic_origin()

        if "YOUR-DOMAIN" in origin:
            try:
                origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
            except:
                pass

        encoded_name = safe_base64(group_name)

        if target:
            final_link = f"{origin}/get/group/{target}/{encoded_name}"
            msg_prefix = "Surge" if target == 'surge' else "Clash"
        else:
            final_link = f"{origin}/sub/group/{encoded_name}"
            msg_prefix = "原始"

        from app.ui.common.notifications import safe_copy_to_clipboard, safe_notify

        await safe_copy_to_clipboard(final_link)
        safe_notify(f"已复制 [{group_name}] {msg_prefix} 订阅", "positive")
    except Exception as e:
        from app.ui.common.notifications import safe_notify

        safe_notify(f"生成失败: {e}", "negative")
