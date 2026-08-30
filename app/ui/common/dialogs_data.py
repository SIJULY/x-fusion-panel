import asyncio
import json
from urllib.parse import urlparse

from nicegui import app, ui


def _data_theme():
    is_dark = bool(app.storage.user.get('is_dark', True))
    return {
        'is_dark': is_dark,
        'card': 'w-full max-w-2xl p-0 gap-0 flex flex-col overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-full max-w-2xl p-0 gap-0 flex flex-col overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]',
        'big_card': 'w-full max-w-2xl max-h-[90vh] flex flex-col gap-0 p-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'w-full max-w-2xl max-h-[90vh] flex flex-col gap-0 p-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_28px_rgba(148,163,184,0.18)]',
        'header': 'justify-between items-center w-full px-5 py-4 border-b border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'justify-between items-center w-full px-5 py-4 border-b border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]',
        'body': 'w-full mt-2 p-5 bg-[#030712]' if is_dark else 'w-full mt-2 p-5 bg-[#f8fbff]',
        'tabs': 'w-full bg-gradient-to-r from-[#0a1526] to-[#050a14] flex-shrink-0 border-b border-[#1e3a5f]/60 text-slate-400' if is_dark else 'w-full bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] flex-shrink-0 border-b border-slate-300/90 text-slate-500',
        'panels': 'w-full p-6 overflow-y-auto flex-grow bg-[#030712] text-slate-200' if is_dark else 'w-full p-6 overflow-y-auto flex-grow bg-[#f8fbff] text-slate-700',
        'title': 'text-xl font-black text-slate-100 tracking-wide' if is_dark else 'text-xl font-black text-slate-800 tracking-wide',
        'sub': 'text-xs text-slate-500 mb-2',
        'accent': 'text-sm font-black text-cyan-300' if is_dark else 'text-sm font-black text-sky-700',
        'input': 'outlined dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined color=blue',
        'input_dense': 'dense outlined dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'dense outlined color=blue',
        'textarea': 'outlined rows=10 dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined rows=10 color=blue',
        'footer': 'w-full p-4 border-t border-[#1e3a5f]/60 bg-gradient-to-r from-[#0a1526] to-[#050a14]' if is_dark else 'w-full p-4 border-t border-slate-300/90 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff]',
        'primary': 'w-full bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 shadow-[0_0_12px_rgba(34,211,238,0.22)] h-12 font-black rounded-sm' if is_dark else 'w-full bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 shadow-[0_6px_16px_rgba(56,189,248,0.16)] h-12 font-black rounded-sm',
        'action': 'w-full bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 font-black rounded-sm' if is_dark else 'w-full bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 font-black rounded-sm',
        'panel_box': 'w-full border border-[#1e3a5f]/45 rounded-sm bg-[#0a1120]' if is_dark else 'w-full border border-slate-300/90 rounded-sm bg-white',
        'selector_bar': 'w-full justify-between items-center bg-[#050b14] p-2 rounded-sm border border-[#1e3a5f]/45' if is_dark else 'w-full justify-between items-center bg-sky-50 p-2 rounded-sm border border-slate-300/90',
    }

from app.core.state import ADMIN_CONFIG, NODES_DATA, SERVERS_CACHE, SUBS_CACHE
from app.services.probe import install_probe_on_server
from app.services.server_ops import fast_resolve_single_server, normalize_all_server_host_fields
from app.storage.repositories import (
    load_global_key,
    save_admin_config,
    save_global_key,
    save_servers,
    save_subs,
)
from app.ui.common.notifications import safe_copy_to_clipboard, safe_notify


async def open_global_settings_dialog():
    theme = _data_theme()
    with ui.dialog() as d, ui.card().classes(theme['card']):
        with ui.row().classes(theme['header']):
            ui.label('全局 SSH 密钥设置').classes(theme['title'])
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes('text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if theme['is_dark'] else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100')

        with ui.column().classes(theme['body']):
            ui.label('全局 SSH 私钥').classes(theme['accent'])
            ui.label('当服务器未单独配置密钥时，默认使用此密钥连接。').classes(theme['sub'])
            key_input = ui.textarea(placeholder='-----BEGIN OPENSSH PRIVATE KEY-----', value=await load_global_key()).classes('w-full font-mono text-xs').props(theme['textarea'])

        async def save_all():
            await save_global_key(key_input.value)
            safe_notify('✅ 全局密钥已保存', 'positive')
            d.close()

        with ui.row().classes(theme['footer']):
            ui.button('保存密钥', icon='save', on_click=save_all).props('flat').classes(theme['primary'])
    d.open()


async def open_data_mgmt_dialog():
    theme = _data_theme()

    # 导出前先把主机字段归位，保证备份里的 ssh_host 是 IP、域名落在 cf_primary_domain
    prep = ui.notification('正在校正服务器域名 / IP 字段...', timeout=None, spinner=True)
    try:
        fixed = await normalize_all_server_host_fields(budget=8.0)
        if fixed:
            await save_servers()
            safe_notify(f"已把 {fixed} 台服务器的域名/IP 字段归位", 'positive')
            from app.ui.components.sidebar import render_sidebar_content

            render_sidebar_content.refresh()
    except Exception as e:
        safe_notify(f"主机字段归位失败: {e}", 'warning')
    finally:
        try:
            prep.dismiss()
        except Exception:
            pass

    header_text_cls = 'text-slate-300' if theme['is_dark'] else 'text-slate-700'
    with ui.dialog() as d, ui.card().classes(theme['big_card']):
        with ui.row().classes(theme['header']):
            ui.label('数据备份 / 恢复').classes(theme['title'])
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes('text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if theme['is_dark'] else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100')

        with ui.tabs().classes(theme['tabs']) \
            .props('indicator-color=cyan active-color=cyan') as tabs:
            tab_export = ui.tab('完整备份 (导出)')
            tab_import = ui.tab('恢复 / 批量添加')

        with ui.tab_panels(tabs, value=tab_import).classes(theme['panels']):
            with ui.tab_panel(tab_export).classes('flex flex-col gap-8 items-center justify-center h-full'):
                full_backup = {
                    "version": "3.0", "timestamp": __import__('time').time(),
                    "servers": SERVERS_CACHE, "subscriptions": SUBS_CACHE,
                    "admin_config": ADMIN_CONFIG, "global_ssh_key": await load_global_key(), "cache": NODES_DATA
                }
                json_str = json.dumps(full_backup, indent=2, ensure_ascii=False)

                with ui.column().classes('items-center gap-2'):
                    ui.icon('cloud_download', size='5rem').classes('opacity-90 text-cyan-400 drop-shadow-[0_0_10px_rgba(34,211,238,0.5)]')
                    ui.label('备份数据已准备就绪').classes('text-xl font-black text-slate-200 tracking-wide' if theme['is_dark'] else 'text-xl font-black text-slate-800 tracking-wide')
                    ui.label(f'包含 {len(SERVERS_CACHE)} 个服务器配置').classes('text-xs text-cyan-500/70')

                with ui.column().classes('w-full max-w-md gap-4'):
                    ui.button('复制到剪贴板', icon='content_copy', on_click=lambda: safe_copy_to_clipboard(json_str)).props('flat').classes('w-full h-12 text-base font-black bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 rounded-sm' if theme['is_dark'] else 'w-full h-12 text-base font-black bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 rounded-sm')
                    ui.button('下载 .json 文件', icon='download', on_click=lambda: ui.download(json_str.encode('utf-8'), 'xui_backup.json')).props('flat').classes('w-full h-12 text-base font-black bg-emerald-950/45 text-emerald-300 border border-emerald-500/45 hover:bg-emerald-900/55 rounded-sm' if theme['is_dark'] else 'w-full h-12 text-base font-black bg-emerald-100 text-emerald-700 border border-emerald-300 hover:bg-emerald-200 rounded-sm')

            with ui.tab_panel(tab_import).classes('flex flex-col gap-6'):
                with ui.expansion('方式一：恢复 JSON 备份文件', icon='restore', value=False).classes(theme['panel_box']).props(f'header-class="{header_text_cls}"'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        import_text = ui.textarea(placeholder='粘贴备份 JSON...').classes('w-full h-32 font-mono text-xs').props(theme['input'])
                        with ui.row().classes('w-full gap-4 items-center'):
                            overwrite_chk = ui.checkbox('覆盖同名服务器', value=False).props('dense dark color=red' if theme['is_dark'] else 'dense color=red')
                            restore_key_chk = ui.checkbox('恢复 SSH 密钥', value=True).props('dense dark color=blue' if theme['is_dark'] else 'dense color=blue')
                            restore_sub_chk = ui.checkbox('恢复订阅设置', value=True).props('dense dark color=blue' if theme['is_dark'] else 'dense color=blue')

                        async def process_import():
                            try:
                                raw = import_text.value.strip()
                                data = json.loads(raw)
                                new_servers = data.get('servers', []) if isinstance(data, dict) else data
                                new_subs = data.get('subscriptions', [])
                                new_config = data.get('admin_config', {})
                                new_ssh_key = data.get('global_ssh_key', '')
                                new_cache = data.get('cache', {})

                                added = 0
                                updated = 0
                                touched = []
                                existing_map = {s['url']: i for i, s in enumerate(SERVERS_CACHE)}
                                for item in new_servers:
                                    url = item.get('url')
                                    if url in existing_map:
                                        if overwrite_chk.value:
                                            SERVERS_CACHE[existing_map[url]] = item
                                            touched.append(item)
                                            updated += 1
                                    else:
                                        SERVERS_CACHE.append(item)
                                        existing_map[url] = len(SERVERS_CACHE) - 1
                                        touched.append(item)
                                        added += 1

                                # 老备份里 ssh_host 可能存的是域名，恢复时一并归位
                                if touched:
                                    await normalize_all_server_host_fields(servers=touched)

                                if restore_key_chk.value and data.get('global_ssh_key'):
                                    await save_global_key(data['global_ssh_key'])
                                if restore_sub_chk.value and isinstance(data, dict):
                                    if isinstance(data.get('subscriptions'), list):
                                        SUBS_CACHE.clear()
                                        SUBS_CACHE.extend(data['subscriptions'])
                                    if data.get('admin_config'):
                                        ADMIN_CONFIG.update(data['admin_config'])

                                await save_servers()
                                await save_subs()
                                await save_admin_config()
                                from app.ui.components.sidebar import render_sidebar_content

                                render_sidebar_content.refresh()
                                safe_notify(f"恢复: +{added} / ~{updated}", 'positive')
                                d.close()
                            except Exception as e:
                                safe_notify(f"错误: {e}", 'negative')
                        ui.button('执行恢复', on_click=process_import).props('flat').classes(theme['action'])

                with ui.expansion('方式二：批量添加服务器', icon='playlist_add', value=True).classes(theme['panel_box']).props(f'header-class="{header_text_cls}"'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        ui.label('批量输入 (每行一个，支持 IP 或 URL)').classes('text-xs font-black text-cyan-500/70')
                        url_area = ui.textarea(placeholder='192.168.1.10\n...').classes('w-full h-32 font-mono text-sm').props(theme['input'])

                        with ui.grid().classes('w-full gap-2 grid-cols-2'):
                            def_ssh_user = ui.input('默认 SSH 用户', value=ADMIN_CONFIG.get('pref_ssh_user','root')).props(theme['input_dense'])
                            def_ssh_port = ui.input('默认 SSH 端口', value=ADMIN_CONFIG.get('pref_ssh_port','22')).props(theme['input_dense'])

                            def_auth = ui.select(['全局密钥', '独立密码'], value='全局密钥', label='认证').classes('col-span-2').props((theme['input_dense'] + ' options-dense'))
                            def_pwd = ui.input('SSH 密码').props(theme['input_dense']).classes('col-span-2').bind_visibility_from(def_auth, 'value', value='独立密码')

                            def_xui_port = ui.input('X-UI 端口', value=ADMIN_CONFIG.get('pref_xui_port','54321')).props(theme['input_dense'])
                            def_xui_user = ui.input('X-UI 账号', value=ADMIN_CONFIG.get('pref_xui_user','admin')).props(theme['input_dense'])
                            def_xui_pass = ui.input('X-UI 密码', value=ADMIN_CONFIG.get('pref_xui_pass','admin')).props(theme['input_dense'])

                        with ui.row().classes(theme['selector_bar']):
                            chk_xui = ui.checkbox('添加 X-UI 面板', value=True).props('dark dense' if theme['is_dark'] else 'dense').classes('text-cyan-300 font-bold' if theme['is_dark'] else 'text-sky-700 font-bold')
                            chk_probe = ui.checkbox('启用 Root 探针', value=False).props('dark dense' if theme['is_dark'] else 'dense').classes('text-emerald-300 font-bold' if theme['is_dark'] else 'text-emerald-700 font-bold')

                        async def run_batch_import():
                            ADMIN_CONFIG['pref_ssh_user'] = def_ssh_user.value
                            ADMIN_CONFIG['pref_ssh_port'] = def_ssh_port.value
                            ADMIN_CONFIG['pref_xui_port'] = def_xui_port.value
                            ADMIN_CONFIG['pref_xui_user'] = def_xui_user.value
                            ADMIN_CONFIG['pref_xui_pass'] = def_xui_pass.value
                            await save_admin_config()

                            raw_text = url_area.value.strip()
                            if not raw_text:
                                safe_notify("请输入内容", "warning")
                                return

                            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
                            count = 0
                            existing_urls = {s['url'] for s in SERVERS_CACHE}
                            post_tasks = []

                            should_add_xui = chk_xui.value
                            should_add_probe = chk_probe.value

                            for line in lines:
                                target_ssh_port = def_ssh_port.value
                                target_xui_port = def_xui_port.value

                                if '://' in line:
                                    final_url = line
                                    try:
                                        parsed = urlparse(line)
                                        name = parsed.hostname or line
                                    except:
                                        name = line
                                else:
                                    if ':' in line and not line.startswith('['):
                                        parts = line.split(':')
                                        host_ip = parts[0]
                                        target_xui_port = parts[1]
                                    else:
                                        host_ip = line
                                        target_xui_port = def_xui_port.value

                                    final_url = f"http://{host_ip}:{target_xui_port}"
                                    name = host_ip

                                if final_url in existing_urls:
                                    continue

                                final_xui_user = def_xui_user.value if should_add_xui else ""
                                final_xui_pass = def_xui_pass.value if should_add_xui else ""

                                new_server = {
                                    'name': name,
                                    'group': '',
                                    'url': final_url,
                                    'user': final_xui_user,
                                    'pass': final_xui_pass,
                                    'prefix': '',
                                    'ssh_user': def_ssh_user.value,
                                    'ssh_port': target_ssh_port,
                                    'ssh_auth_type': def_auth.value,
                                    'ssh_password': def_pwd.value,
                                    'ssh_key': '',
                                    'probe_installed': should_add_probe
                                }

                                SERVERS_CACHE.append(new_server)
                                existing_urls.add(final_url)
                                count += 1

                                post_tasks.append(fast_resolve_single_server(new_server))

                                if ADMIN_CONFIG.get('probe_enabled', False) and should_add_probe:
                                    post_tasks.append(install_probe_on_server(new_server))

                            if count > 0:
                                await save_servers()
                                from app.ui.components.sidebar import render_sidebar_content

                                render_sidebar_content.refresh()
                                safe_notify(f"成功添加 {count} 台服务器", 'positive')
                                d.close()

                                if post_tasks:
                                    safe_notify(f"正在后台处理 {len(post_tasks)} 个初始化任务...", "ongoing")

                                    async def _run_bg_tasks():
                                        await asyncio.gather(*post_tasks, return_exceptions=True)
                                    asyncio.create_task(_run_bg_tasks())

                            else:
                                safe_notify("未添加任何服务器 (可能已存在)", 'warning')

                        ui.button('确认批量添加', icon='add_box', on_click=run_batch_import).props('flat').classes(theme['action'] + (' h-10' if theme['is_dark'] else ' h-10'))
    d.open()
