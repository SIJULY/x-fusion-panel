def open_cloudflare_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-[500px] p-6 flex flex-col gap-4'):
        with ui.row().classes('items-center gap-2 text-orange-600 mb-2'):
            ui.icon('cloud', size='md')
            ui.label('Cloudflare API 配置').classes('text-lg font-bold')
            
        ui.label('用于自动解析域名、开启 CDN 和设置 SSL (Flexible)。').classes('text-xs text-gray-500')
        
        # 读取现有配置
        cf_token = ui.input('API Token', value=ADMIN_CONFIG.get('cf_api_token', '')).props('outlined dense type=password').classes('w-full')
        ui.label('权限要求: Zone.DNS (Edit), Zone.Settings (Edit)').classes('text-[10px] text-gray-400 ml-1')
        
        cf_domain_root = ui.input('根域名 (例如: example.com)', value=ADMIN_CONFIG.get('cf_root_domain', '')).props('outlined dense').classes('w-full')
        
        async def save_cf():
            ADMIN_CONFIG['cf_api_token'] = cf_token.value.strip()
            ADMIN_CONFIG['cf_root_domain'] = cf_domain_root.value.strip()
            await save_admin_config()
            safe_notify('✅ Cloudflare 配置已保存', 'positive')
            d.close()

        with ui.row().classes('w-full justify-end mt-4'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('保存配置', on_click=save_cf).classes('bg-orange-600 text-white shadow-md')
    d.open()