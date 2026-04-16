def open_global_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl p-6 flex flex-col gap-4'):
        with ui.row().classes('justify-between items-center w-full border-b pb-2'):
            ui.label('🔐 全局 SSH 密钥设置').classes('text-xl font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
        
        with ui.column().classes('w-full mt-2'):
            ui.label('全局 SSH 私钥').classes('text-sm font-bold text-gray-700')
            ui.label('当服务器未单独配置密钥时，默认使用此密钥连接。').classes('text-xs text-gray-400 mb-2')
            key_input = ui.textarea(placeholder='-----BEGIN OPENSSH PRIVATE KEY-----', value=load_global_key()).classes('w-full font-mono text-xs').props('outlined rows=10')

        async def save_all():
            save_global_key(key_input.value)
            safe_notify('✅ 全局密钥已保存', 'positive')
            d.close()

        ui.button('保存密钥', icon='save', on_click=save_all).classes('w-full bg-slate-900 text-white shadow-lg h-12 mt-2')
    d.open()