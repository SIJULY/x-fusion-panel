from nicegui import app, ui

from app.core.state import ADMIN_CONFIG, CURRENT_VIEW_STATE, SERVERS_CACHE
from app.services.probe import batch_install_all_probes
from app.storage.repositories import save_admin_config
from app.ui.common.notifications import safe_copy_to_clipboard, safe_notify
from app.ui.dialogs.group_dialogs import open_group_sort_dialog, open_unified_group_manager


async def render_probe_page():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'PROBE'
    CURRENT_VIEW_STATE['data'] = None
    CURRENT_VIEW_STATE['page'] = 1
    app.storage.user['last_view_scope'] = 'PROBE'
    app.storage.user['last_view_data'] = None
    app.storage.user['last_view_page'] = 1
    is_dark = bool(app.storage.user.get('is_dark', True))

    from app.ui.pages.content_router import content_container

    content_container.clear()
    content_container.classes(replace='w-full h-full overflow-y-auto p-6 relative flex flex-col justify-center items-center')
    content_container.style(f'background-color: {"#030712" if is_dark else "#eef4ff"};')

    if not ADMIN_CONFIG.get('probe_enabled'):
        ADMIN_CONFIG['probe_enabled'] = True
        await save_admin_config()

    with content_container:
        with ui.column().classes('w-full max-w-7xl gap-6'):
            card_style = 'w-full p-6 bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_0_16px_rgba(0,0,0,0.28)] rounded-sm' if is_dark else 'w-full p-6 bg-white border border-slate-300/90 shadow-[0_8px_24px_rgba(148,163,184,0.14)] rounded-sm'
            title_wrap_cls = 'w-full items-center gap-3 border-b border-[#1e3a5f]/60 pb-3' if is_dark else 'w-full items-center gap-3 border-b border-slate-300/90 pb-3'
            title_icon_cls = 'w-11 h-11 rounded-sm flex items-center justify-center bg-[#050b14] border border-[#1e3a5f] text-cyan-400 shadow-[0_0_10px_rgba(0,0,0,0.45)] relative overflow-hidden' if is_dark else 'w-11 h-11 rounded-sm flex items-center justify-center bg-sky-50 border border-slate-300 text-sky-600 shadow-[0_4px_12px_rgba(148,163,184,0.12)] relative overflow-hidden'
            page_title_cls = 'text-2xl font-black text-slate-100 tracking-wide' if is_dark else 'text-2xl font-black text-slate-800 tracking-wide'
            page_sub_cls = 'text-xs font-black text-cyan-500/70 uppercase tracking-[0.25em]' if is_dark else 'text-xs font-black text-sky-700/70 uppercase tracking-[0.25em]'
            section_header_cls = 'items-center gap-2 mb-4 border-b border-[#1e3a5f]/55 pb-2 w-full' if is_dark else 'items-center gap-2 mb-4 border-b border-slate-300/90 pb-2 w-full'
            section_title_cls = 'text-lg font-black text-slate-100 tracking-wide' if is_dark else 'text-lg font-black text-slate-800 tracking-wide'
            input_label_cls = 'text-sm font-bold text-slate-400' if is_dark else 'text-sm font-bold text-slate-700'
            hint_cls = 'text-xs text-slate-500' if is_dark else 'text-xs text-slate-500'
            input_props = 'outlined dense dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense color=blue'
            action_btn_cls = 'border border-[#1e3a5f]/45 text-slate-300 bg-[#0a1120] hover:bg-cyan-950/20 hover:text-cyan-300 rounded-sm font-black' if is_dark else 'border border-slate-300/90 text-slate-700 bg-white hover:bg-sky-50 hover:text-sky-700 rounded-sm font-black'

            with ui.row().classes(title_wrap_cls):
                with ui.element('div').classes(title_icon_cls):
                    ui.element('div').classes('absolute inset-0 bg-cyan-400/10' if is_dark else 'absolute inset-0 bg-sky-400/10')
                    ui.icon('tune').classes('text-[20px] drop-shadow-[0_0_5px_currentColor]')
                with ui.column().classes('gap-0'):
                    ui.label('探针管理与设置').classes(page_title_cls)
                    ui.label('Configuration & Management').classes(page_sub_cls)

            with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-7 gap-6 items-stretch'):
                with ui.column().classes('lg:col-span-4 w-full gap-6'):
                    with ui.card().classes(card_style):
                        with ui.row().classes(section_header_cls):
                            ui.icon('hub').classes('text-xl text-cyan-400' if is_dark else 'text-xl text-sky-600')
                            ui.label('基础连接设置').classes(section_title_cls)

                        with ui.column().classes('w-full gap-2'):
                            ui.label('📡 主控端地址 (Agent连接用)').classes(input_label_cls)
                            url_input = ui.input(value=ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')).props(input_props).classes('w-full')
                            ui.label('请填写公网 IP 或域名，带端口').classes(hint_cls)

                        async def save_url():
                            ADMIN_CONFIG['manager_base_url'] = url_input.value.strip().rstrip('/')
                            await save_admin_config()
                            safe_notify('已保存', 'positive')

                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存', icon='save', on_click=save_url).props('flat').classes(f'px-4 {action_btn_cls}')

                    with ui.card().classes(card_style):
                        with ui.row().classes(section_header_cls):
                            ui.icon('speed').classes('text-xl text-amber-400')
                            ui.label('三网延迟测速目标').classes(section_title_cls)

                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-3 gap-4'):
                            ping_ct = ui.input('电信 IP', value=ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')).props(input_props)
                            ping_cu = ui.input('联通 IP', value=ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')).props(input_props)
                            ping_cm = ui.input('移动 IP', value=ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')).props(input_props)

                        async def save_ping():
                            ADMIN_CONFIG['ping_target_ct'] = ping_ct.value
                            ADMIN_CONFIG['ping_target_cu'] = ping_cu.value
                            ADMIN_CONFIG['ping_target_cm'] = ping_cm.value
                            await save_admin_config()
                            safe_notify('已保存 (需更新探针生效)', 'positive')

                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存', icon='save', on_click=save_ping).props('flat').classes(f'px-4 {action_btn_cls}')

                    with ui.card().classes(card_style):
                        with ui.row().classes(section_header_cls):
                            ui.icon('notifications').classes('text-xl text-fuchsia-400')
                            ui.label('Telegram 通知').classes(section_title_cls)

                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-4'):
                            tg_token = ui.input('Bot Token', value=ADMIN_CONFIG.get('tg_bot_token', '')).props(input_props)
                            tg_id = ui.input('Chat ID', value=ADMIN_CONFIG.get('tg_chat_id', '')).props(input_props)

                        async def save_tg():
                            ADMIN_CONFIG['tg_bot_token'] = tg_token.value
                            ADMIN_CONFIG['tg_chat_id'] = tg_id.value
                            await save_admin_config()
                            safe_notify('已保存', 'positive')

                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存', icon='save', on_click=save_tg).props('flat').classes(f'px-4 {action_btn_cls}')

                with ui.column().classes('lg:col-span-3 w-full gap-6 h-full'):
                    with ui.card().classes(card_style + ' flex-shrink-0'):
                        ui.label('快捷操作').classes('text-lg font-black mb-4 border-l-4 border-cyan-500 pl-2 tracking-wide text-slate-100' if is_dark else 'text-lg font-black mb-4 border-l-4 border-sky-500 pl-2 tracking-wide text-slate-800')
                        with ui.column().classes('w-full gap-3'):
                            async def copy_cmd():
                                try:
                                    origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
                                except:
                                    safe_notify("获取地址失败", "negative")
                                    return

                                token = ADMIN_CONFIG.get('probe_token', 'default_token')
                                mgr_url = ADMIN_CONFIG.get('manager_base_url', origin).strip().rstrip('/')
                                reg_url = f"{mgr_url}/api/probe/register"

                                ct = ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')
                                cu = ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')
                                cm = ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')

                                cmd = f'curl -sL https://raw.githubusercontent.com/SIJULY/x-fusion-panel/main/static/x-install.sh | bash -s -- "{token}" "{reg_url}" "{ct}" "{cu}" "{cm}"'
                                await safe_copy_to_clipboard(cmd)
                                safe_notify("已复制安装命令", "positive")

                            ui.button('复制安装命令', icon='content_copy', on_click=copy_cmd).props('flat').classes(f'w-full shadow-sm align-left justify-start {action_btn_cls}')

                            with ui.row().classes('w-full gap-2'):
                                ui.button('分组管理', icon='settings', on_click=lambda: open_unified_group_manager('manage')).props('flat').classes(f'flex-1 {action_btn_cls}')
                                ui.button('排序视图', icon='sort', on_click=open_group_sort_dialog).props('flat').classes(f'flex-1 {action_btn_cls}')

                            ui.button('更新所有探针', icon='system_update_alt', on_click=batch_install_all_probes).props('flat').classes(f'w-full align-left justify-start {action_btn_cls}')

                    with ui.card().classes('w-full p-6 bg-gradient-to-br from-[#10203d] to-[#050b14] text-white rounded-sm shadow-[0_0_18px_rgba(0,0,0,0.35)] relative overflow-hidden group cursor-pointer flex-grow flex flex-col justify-center border border-cyan-500/25' if is_dark else 'w-full p-6 bg-gradient-to-br from-[#eff6ff] to-[#dbeafe] text-slate-800 rounded-sm shadow-[0_8px_24px_rgba(148,163,184,0.14)] relative overflow-hidden group cursor-pointer flex-grow flex flex-col justify-center border border-sky-300/80').on('click', lambda: ui.navigate.to('/status', new_tab=True)):
                        ui.icon('public', size='10rem').classes('absolute -right-8 -bottom-8 text-white opacity-5 group-hover:rotate-12 transition transform duration-500' if is_dark else 'absolute -right-8 -bottom-8 text-sky-300 opacity-20 group-hover:rotate-12 transition transform duration-500')
                        ui.label('公开监控墙').classes('text-2xl font-black mb-2 tracking-wide text-white' if is_dark else 'text-2xl font-black mb-2 tracking-wide text-slate-800')
                        ui.label('点击前往查看实时状态').classes('text-sm text-cyan-200/80 mb-6' if is_dark else 'text-sm text-sky-700/80 mb-6')
                        with ui.row().classes('items-center gap-2 text-cyan-300 font-black' if is_dark else 'items-center gap-2 text-sky-700 font-black'):
                            ui.label('立即前往')
                            ui.icon('arrow_forward')

                    online_count = len([s for s in SERVERS_CACHE if s.get('_status') == 'online'])
                    probe_count = len([s for s in SERVERS_CACHE if s.get('probe_installed')])

                    with ui.card().classes(card_style + ' flex-shrink-0'):
                        ui.label('数据概览').classes('text-lg font-black mb-4 border-l-4 border-emerald-500 pl-2 tracking-wide text-slate-100' if is_dark else 'text-lg font-black mb-4 border-l-4 border-emerald-500 pl-2 tracking-wide text-slate-800')

                        def stat_row(label, val, color):
                            with ui.row().classes('w-full justify-between items-center border-b border-[#1e3a5f]/45 pb-3 mb-3 last:border-0 last:mb-0' if is_dark else 'w-full justify-between items-center border-b border-slate-200 pb-3 mb-3 last:border-0 last:mb-0'):
                                ui.label(label).classes('text-slate-500 text-sm font-bold')
                                ui.label(str(val)).classes(f'font-bold text-xl {color}')

                        stat_row('总服务器', len(SERVERS_CACHE), 'text-slate-200' if is_dark else 'text-slate-800')
                        stat_row('当前在线', online_count, 'text-green-400')
                        stat_row('已装探针', probe_count, 'text-purple-400')
