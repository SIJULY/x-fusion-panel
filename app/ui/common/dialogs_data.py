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
from app.services.github_backup import (
    GITHUB_OAUTH_CALLBACK_PATH,
    build_full_backup_payload,
    clear_github_auth,
    download_latest_backup_from_github,
    get_github_backup_dir,
    get_github_backup_repo,
    is_github_connected,
    is_github_oauth_configured,
    upload_backup_to_github,
)
from app.services.probe import install_probe_on_server
from app.services.server_ops import fast_resolve_single_server
from app.storage.repositories import (
    load_global_key,
    save_admin_config,
    save_global_key,
    save_nodes_cache,
    save_servers,
    save_subs,
)
from app.ui.common.notifications import safe_copy_to_clipboard, safe_notify


def open_global_settings_dialog():
    theme = _data_theme()
    with ui.dialog() as d, ui.card().classes(theme['card']):
        with ui.row().classes(theme['header']):
            ui.label('全局 SSH 密钥设置').classes(theme['title'])
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes('text-slate-400 hover:text-cyan-300 hover:bg-cyan-950/30' if theme['is_dark'] else 'text-slate-500 hover:text-sky-700 hover:bg-sky-100')

        with ui.column().classes(theme['body']):
            ui.label('全局 SSH 私钥').classes(theme['accent'])
            ui.label('当服务器未单独配置密钥时，默认使用此密钥连接。').classes(theme['sub'])
            key_input = ui.textarea(placeholder='-----BEGIN OPENSSH PRIVATE KEY-----', value=load_global_key()).classes('w-full font-mono text-xs').props(theme['textarea'])

        async def save_all():
            save_global_key(key_input.value)
            safe_notify('✅ 全局密钥已保存', 'positive')
            d.close()

        with ui.row().classes(theme['footer']):
            ui.button('保存密钥', icon='save', on_click=save_all).props('flat').classes(theme['primary'])
    d.open()


async def open_data_mgmt_dialog():
    theme = _data_theme()
    header_text_cls = 'text-slate-300' if theme['is_dark'] else 'text-slate-700'
    full_backup = build_full_backup_payload()
    json_str = json.dumps(full_backup, indent=2, ensure_ascii=False)

    with ui.dialog() as d, ui.card().classes(theme['big_card']):
        with ui.tabs().classes(theme['tabs']) \
            .props('indicator-color=cyan active-color=cyan') as tabs:
            tab_export = ui.tab('完整备份 (导出)')
            tab_import = ui.tab('恢复 / 批量添加')

        with ui.tab_panels(tabs, value=tab_import).classes(theme['panels']):
            with ui.tab_panel(tab_export).classes('flex flex-col gap-6'):
                with ui.column().classes('items-center gap-2'):
                    ui.icon('cloud_download', size='5rem').classes('opacity-90 text-cyan-400 drop-shadow-[0_0_10px_rgba(34,211,238,0.5)]')
                    ui.label('备份数据已准备就绪').classes('text-xl font-black text-slate-200 tracking-wide' if theme['is_dark'] else 'text-xl font-black text-slate-800 tracking-wide')
                    ui.label(f'包含 {len(SERVERS_CACHE)} 个服务器配置').classes('text-xs text-cyan-500/70')

                with ui.column().classes('w-full max-w-md gap-4 self-center'):
                    ui.button('复制到剪贴板', icon='content_copy', on_click=lambda: safe_copy_to_clipboard(json_str)).props('flat').classes('w-full h-12 text-base font-black bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 rounded-sm' if theme['is_dark'] else 'w-full h-12 text-base font-black bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 rounded-sm')
                    ui.button('下载 .json 文件', icon='download', on_click=lambda: ui.download(json_str.encode('utf-8'), 'xui_backup.json')).props('flat').classes('w-full h-12 text-base font-black bg-emerald-950/45 text-emerald-300 border border-emerald-500/45 hover:bg-emerald-900/55 rounded-sm' if theme['is_dark'] else 'w-full h-12 text-base font-black bg-emerald-100 text-emerald-700 border border-emerald-300 hover:bg-emerald-200 rounded-sm')

                with ui.column().classes('w-full gap-4 p-4 rounded-sm border border-[#1e3a5f]/45 bg-[#0a1120]' if theme['is_dark'] else 'w-full gap-4 p-4 rounded-sm border border-slate-300/90 bg-white'):
                    ui.label('GitHub 云备份（标准 OAuth 私有仓库）').classes(theme['accent'])
                    ui.label('请先填写 GitHub OAuth App 的 Client ID / Client Secret，授权时会跳转到 GitHub 官方页面登录。').classes(theme['sub'])

                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-3'):
                        github_client_id = ui.input('GitHub Client ID', value=ADMIN_CONFIG.get('github_client_id', '')).props(theme['input_dense'])
                        github_client_secret = ui.input('GitHub Client Secret', value=ADMIN_CONFIG.get('github_client_secret', '')).props(theme['input_dense'] + ' type=password')
                        repo_input = ui.input('私有仓库名', value=ADMIN_CONFIG.get('github_backup_repo', get_github_backup_repo())).props(theme['input_dense'])
                        dir_input = ui.input('仓库目录', value=ADMIN_CONFIG.get('github_backup_dir', get_github_backup_dir())).props(theme['input_dense'])

                    callback_base = (ADMIN_CONFIG.get('manager_base_url') or '当前面板访问地址').rstrip('/')
                    ui.label(f'GitHub OAuth App 回调地址请填写：{callback_base}{GITHUB_OAUTH_CALLBACK_PATH}').classes('text-[11px] text-slate-500 break-all')
                    github_status = ui.label().classes('text-xs font-bold text-cyan-500/80')

                    def update_github_status() -> None:
                        if not is_github_oauth_configured():
                            github_status.set_text('请先在这里保存 GitHub Client ID 和 Client Secret。')
                            return
                        if is_github_connected():
                            github_status.set_text(
                                f"已连接 GitHub：@{ADMIN_CONFIG.get('github_user_login', 'unknown')} ｜ 私有仓库：{repo_input.value.strip() or get_github_backup_repo()}"
                            )
                        else:
                            github_status.set_text('GitHub OAuth 配置已就绪，点击“连接 GitHub 授权”后会跳转到 GitHub 官方页面登录。')

                    async def persist_github_settings() -> None:
                        ADMIN_CONFIG['github_client_id'] = (github_client_id.value or '').strip()
                        ADMIN_CONFIG['github_client_secret'] = (github_client_secret.value or '').strip()
                        ADMIN_CONFIG['github_backup_repo'] = (repo_input.value or '').strip() or get_github_backup_repo()
                        ADMIN_CONFIG['github_backup_dir'] = (dir_input.value or '').strip() or get_github_backup_dir()
                        await save_admin_config()

                    async def connect_github() -> None:
                        await persist_github_settings()
                        if not is_github_oauth_configured():
                            safe_notify('请先填写 GitHub Client ID 和 Client Secret', 'warning')
                            return

                        last_success_at = float(ADMIN_CONFIG.get('github_oauth_last_success_at') or 0)
                        opened = await ui.run_javascript(
                            'return !!window.open("/api/github/oauth/start", "xfusion_github_oauth", "width=960,height=760,menubar=no,toolbar=no,location=yes,resizable=yes,scrollbars=yes");'
                        )
                        if not opened:
                            safe_notify('浏览器拦截了 GitHub 授权窗口，请允许弹窗后重试', 'warning', timeout=5000)
                            return

                        safe_notify('请在 GitHub 官方页面登录并确认授权...', 'ongoing', timeout=5000)
                        for _ in range(180):
                            await asyncio.sleep(1)
                            current_success_at = float(ADMIN_CONFIG.get('github_oauth_last_success_at') or 0)
                            if current_success_at > last_success_at and is_github_connected():
                                update_github_status()
                                safe_notify(f"✅ GitHub 授权成功：@{ADMIN_CONFIG.get('github_user_login', 'unknown')}", 'positive')
                                return
                        update_github_status()
                        safe_notify('等待 GitHub 授权超时，请确认是否已在新窗口完成授权', 'warning', timeout=5000)

                    async def disconnect_github() -> None:
                        clear_github_auth()
                        await save_admin_config()
                        update_github_status()
                        safe_notify('已断开 GitHub 授权', 'positive')

                    async def upload_github_backup() -> None:
                        try:
                            await persist_github_settings()
                            result = await upload_backup_to_github(build_full_backup_payload())
                            update_github_status()
                            safe_notify(
                                f"✅ 云备份完成：{result['owner']}/{result['repo']} / {result['history_path']}",
                                'positive',
                                timeout=5000,
                            )
                        except Exception as e:
                            safe_notify(f'GitHub 云备份失败: {e}', 'negative', timeout=5000)

                    update_github_status()
                    with ui.row().classes('w-full gap-3 max-sm:flex-col'):
                        ui.button('保存 GitHub OAuth 配置', icon='save', on_click=lambda: asyncio.create_task(persist_github_settings())).props('flat').classes(theme['action'] + ' flex-1')
                        ui.button('连接 GitHub 授权', icon='login', on_click=connect_github).props('flat').classes(theme['action'] + ' flex-1')
                        ui.button('上传到 GitHub 私有仓库', icon='cloud_upload', on_click=upload_github_backup).props('flat').classes(theme['action'] + ' flex-1')
                        ui.button('断开授权', icon='link_off', on_click=disconnect_github).props('flat').classes(theme['action'] + ' flex-1')

            with ui.tab_panel(tab_import).classes('flex flex-col gap-6'):
                with ui.expansion('方式一：恢复 JSON 备份文件', icon='restore', value=False).classes(theme['panel_box']).props(f'header-class="{header_text_cls}"'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        import_text = ui.textarea(placeholder='粘贴备份 JSON...').classes('w-full h-32 font-mono text-xs').props(theme['input'])
                        with ui.row().classes('w-full gap-4 items-center'):
                            overwrite_chk = ui.checkbox('覆盖同名服务器', value=False).props('dense dark color=red' if theme['is_dark'] else 'dense color=red')
                            restore_key_chk = ui.checkbox('恢复 SSH 密钥', value=True).props('dense dark color=blue' if theme['is_dark'] else 'dense color=blue')
                            restore_sub_chk = ui.checkbox('恢复订阅设置', value=True).props('dense dark color=blue' if theme['is_dark'] else 'dense color=blue')

                        async def apply_backup_data(data):
                            if not isinstance(data, (dict, list)):
                                raise ValueError('备份格式无效')

                            new_servers = data.get('servers', []) if isinstance(data, dict) else data
                            added = 0
                            updated = 0
                            existing_map = {s.get('url'): i for i, s in enumerate(SERVERS_CACHE) if s.get('url')}

                            for item in new_servers:
                                if not isinstance(item, dict):
                                    continue
                                url = item.get('url')
                                if not url:
                                    continue
                                if url in existing_map:
                                    if overwrite_chk.value:
                                        SERVERS_CACHE[existing_map[url]] = item
                                        updated += 1
                                else:
                                    SERVERS_CACHE.append(item)
                                    existing_map[url] = len(SERVERS_CACHE) - 1
                                    added += 1

                            if restore_key_chk.value and isinstance(data, dict) and data.get('global_ssh_key'):
                                save_global_key(data['global_ssh_key'])

                            if restore_sub_chk.value and isinstance(data, dict):
                                if isinstance(data.get('subscriptions'), list):
                                    SUBS_CACHE.clear()
                                    SUBS_CACHE.extend(data['subscriptions'])
                                if isinstance(data.get('admin_config'), dict):
                                    ADMIN_CONFIG.update(data['admin_config'])

                            if isinstance(data, dict) and isinstance(data.get('cache'), dict):
                                NODES_DATA.clear()
                                NODES_DATA.update(data['cache'])

                            await save_servers()
                            await save_subs()
                            await save_nodes_cache()
                            await save_admin_config()

                            from app.ui.components.sidebar import render_sidebar_content

                            render_sidebar_content.refresh()
                            safe_notify(f'恢复完成: +{added} / ~{updated}', 'positive')
                            d.close()

                        async def process_import():
                            try:
                                raw = import_text.value.strip()
                                data = json.loads(raw)
                                await apply_backup_data(data)
                            except Exception as e:
                                safe_notify(f'错误: {e}', 'negative')

                        ui.button('执行恢复', on_click=process_import).props('flat').classes(theme['action'])

                with ui.expansion('方式二：从 GitHub 私有仓库恢复', icon='cloud_download', value=True).classes(theme['panel_box']).props(f'header-class="{header_text_cls}"'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        ui.label('从已授权 GitHub 账号的私有仓库中拉取最新备份并恢复。').classes('text-xs font-black text-cyan-500/70')
                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-3'):
                            gh_repo_import = ui.input('私有仓库名', value=ADMIN_CONFIG.get('github_backup_repo', get_github_backup_repo())).props(theme['input_dense'])
                            gh_dir_import = ui.input('仓库目录', value=ADMIN_CONFIG.get('github_backup_dir', get_github_backup_dir())).props(theme['input_dense'])

                        async def restore_from_github():
                            try:
                                ADMIN_CONFIG['github_backup_repo'] = (gh_repo_import.value or '').strip() or get_github_backup_repo()
                                ADMIN_CONFIG['github_backup_dir'] = (gh_dir_import.value or '').strip() or get_github_backup_dir()
                                await save_admin_config()
                                data = await download_latest_backup_from_github()
                                await apply_backup_data(data)
                            except Exception as e:
                                safe_notify(f'GitHub 恢复失败: {e}', 'negative', timeout=5000)

                        ui.button('从 GitHub 下载并恢复最新备份', icon='download', on_click=restore_from_github).props('flat').classes(theme['action'])

                with ui.expansion('方式三：批量添加服务器', icon='playlist_add', value=False).classes(theme['panel_box']).props(f'header-class="{header_text_cls}"'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        ui.label('批量输入 (每行一个，支持 IP 或 URL)').classes('text-xs font-black text-cyan-500/70')
                        url_area = ui.textarea(placeholder='192.168.1.10\n...').classes('w-full h-32 font-mono text-sm').props(theme['input'])

                        with ui.grid().classes('w-full gap-2 grid-cols-2'):
                            def_ssh_user = ui.input('默认 SSH 用户', value=ADMIN_CONFIG.get('pref_ssh_user', 'root')).props(theme['input_dense'])
                            def_ssh_port = ui.input('默认 SSH 端口', value=ADMIN_CONFIG.get('pref_ssh_port', '22')).props(theme['input_dense'])

                            def_auth = ui.select(['全局密钥', '独立密码'], value='全局密钥', label='认证').classes('col-span-2').props(theme['input_dense'] + ' options-dense')
                            def_pwd = ui.input('SSH 密码').props(theme['input_dense']).classes('col-span-2').bind_visibility_from(def_auth, 'value', value='独立密码')

                            def_xui_port = ui.input('X-UI 端口', value=ADMIN_CONFIG.get('pref_xui_port', '54321')).props(theme['input_dense'])
                            def_xui_user = ui.input('X-UI 账号', value=ADMIN_CONFIG.get('pref_xui_user', 'admin')).props(theme['input_dense'])
                            def_xui_pass = ui.input('X-UI 密码', value=ADMIN_CONFIG.get('pref_xui_pass', 'admin')).props(theme['input_dense'])

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
                                safe_notify('请输入内容', 'warning')
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
                                    except Exception:
                                        name = line
                                else:
                                    if ':' in line and not line.startswith('['):
                                        parts = line.split(':')
                                        host_ip = parts[0]
                                        target_xui_port = parts[1]
                                    else:
                                        host_ip = line
                                        target_xui_port = def_xui_port.value

                                    final_url = f'http://{host_ip}:{target_xui_port}'
                                    name = host_ip

                                if final_url in existing_urls:
                                    continue

                                final_xui_user = def_xui_user.value if should_add_xui else ''
                                final_xui_pass = def_xui_pass.value if should_add_xui else ''

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
                                    'probe_installed': should_add_probe,
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
                                safe_notify(f'成功添加 {count} 台服务器', 'positive')
                                d.close()

                                if post_tasks:
                                    safe_notify(f'正在后台处理 {len(post_tasks)} 个初始化任务...', 'ongoing')

                                    async def _run_bg_tasks():
                                        await asyncio.gather(*post_tasks, return_exceptions=True)

                                    asyncio.create_task(_run_bg_tasks())
                            else:
                                safe_notify('未添加任何服务器 (可能已存在)', 'warning')

                        ui.button('确认批量添加', icon='add_box', on_click=run_batch_import).props('flat').classes(theme['action'] + (' h-10' if theme['is_dark'] else ' h-10'))
    d.open()
