import json

from nicegui import ui

from app.core.logging import logger


def safe_notify(message, type='info', timeout=3000):
    try:
        ui.notify(message, type=type, timeout=timeout)
    except:
        logger.info(f"[Notify] {message}")


def show_loading(container):
    try:
        container.clear()
        with container:
            with ui.column().classes('w-full h-[60vh] justify-center items-center'):
                ui.spinner('dots', size='3rem', color='primary')
                ui.label('数据处理中...').classes('text-gray-500 mt-4')
    except:
        pass


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
        if result:
            safe_notify('已复制到剪贴板', 'positive')
        else:
            safe_notify('复制失败', 'negative')
    except:
        safe_notify('复制功能不可用', 'negative')
