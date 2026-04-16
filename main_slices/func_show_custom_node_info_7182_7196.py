def show_custom_node_info(node):
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm'):
        ui.label(node.get('remark', '节点详情')).classes('text-lg font-bold mb-2')
        
        # 获取链接
        link = node.get('_raw_link') or node.get('link') or "无法获取链接"
        
        # 显示链接区域
        with ui.row().classes('w-full bg-gray-100 p-3 rounded break-all font-mono text-xs mb-4'):
            ui.label(link)
            
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('复制', icon='content_copy', on_click=lambda: [safe_copy_to_clipboard(link), d.close()])
            ui.button('关闭', on_click=d.close).props('flat')
    d.open()