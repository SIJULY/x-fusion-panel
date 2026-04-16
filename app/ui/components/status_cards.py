from nicegui import app, ui


def render_status_card(label, value_str, sub_text, color_class='text-blue-600', icon='memory'):
    """渲染单个简易状态卡片 (用于负载、连接数等)"""
    with ui.card().classes('p-3 border flex-grow items-center justify-between min-w-[150px] rounded-sm').style('background: var(--xf-panel-bg); border-color: var(--xf-card-border); box-shadow: 0 6px 18px rgba(15,23,42,0.10);'):
        with ui.row().classes('items-center gap-3'):
            with ui.column().classes('justify-center items-center rounded-sm p-2 min-w-[40px] border').style('background: var(--xf-code-bg); border-color: var(--xf-card-border);'):
                ui.icon(icon).classes(f'{color_class} text-xl drop-shadow-[0_0_4px_currentColor]')
            with ui.column().classes('gap-0'):
                ui.label(label).classes('text-xs font-black uppercase tracking-wide').style('color: var(--xf-text-muted);')
                ui.label(value_str).classes('text-sm font-black').style('color: var(--xf-text-strong);')
                if sub_text:
                    ui.label(sub_text).classes('text-[10px] font-bold').style('color: var(--xf-text-muted);')
