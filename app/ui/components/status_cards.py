from nicegui import app, ui


def render_status_card(label, value_str, sub_text, color_class='text-blue-600', icon='memory'):
    """渲染单个简易状态卡片 (用于负载、连接数等)"""
    is_dark = bool(app.storage.user.get('is_dark', True))
    card_cls = 'p-3 shadow-[0_0_12px_rgba(0,0,0,0.2)] border border-[#1e3a5f]/45 flex-grow items-center justify-between min-w-[150px] rounded-sm bg-[#070b14]' if is_dark else 'p-3 shadow-[0_6px_18px_rgba(148,163,184,0.12)] border border-slate-300/90 flex-grow items-center justify-between min-w-[150px] rounded-sm bg-white'
    icon_box_cls = 'justify-center items-center bg-[#050b14] border border-[#1e3a5f]/45 rounded-sm p-2 min-w-[40px]' if is_dark else 'justify-center items-center bg-sky-50 border border-slate-300/90 rounded-sm p-2 min-w-[40px]'
    label_cls = 'text-xs text-slate-500 font-black uppercase tracking-wide' if is_dark else 'text-xs text-slate-600 font-black uppercase tracking-wide'
    value_cls = 'text-sm font-black text-slate-100' if is_dark else 'text-sm font-black text-slate-800'
    sub_cls = 'text-[10px] text-slate-500 font-bold' if is_dark else 'text-[10px] text-slate-500 font-bold'

    with ui.card().classes(card_cls):
        with ui.row().classes('items-center gap-3'):
            with ui.column().classes(icon_box_cls):
                ui.icon(icon).classes(f'{color_class} text-xl drop-shadow-[0_0_4px_currentColor]')
            with ui.column().classes('gap-0'):
                ui.label(label).classes(label_cls)
                ui.label(value_str).classes(value_cls)
                if sub_text:
                    ui.label(sub_text).classes(sub_cls)
