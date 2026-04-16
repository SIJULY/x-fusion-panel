import base64
import io
import uuid

import pyotp
import qrcode
from fastapi import Request
from nicegui import app, ui

from app.core.config import ADMIN_PASS, ADMIN_USER
from app.core.state import ADMIN_CONFIG
from app.storage.repositories import save_admin_config
from app.ui.common.notifications import safe_copy_to_clipboard
from app.utils.geo import fetch_geo_from_ip


def login_page(request: Request):
    ui.add_head_html('''
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            let fp = localStorage.getItem('fp_device_id');
            if (!fp) {
                fp = 'dev-' + Math.random().toString(36).substr(2, 9) + '-' + Date.now().toString(36);
                localStorage.setItem('fp_device_id', fp);
            }
            document.cookie = "fp_device_id=" + fp + "; path=/; max-age=315360000";
        });
    </script>
    ''')

    is_dark = bool(app.storage.user.get('is_dark', True))
    container_cls = 'absolute-center w-full max-w-sm p-0 gap-0 overflow-hidden rounded-sm bg-[#070b14] border border-[#1e3a5f]/55 shadow-[0_18px_48px_rgba(0,0,0,0.78)]' if is_dark else 'absolute-center w-full max-w-sm p-0 gap-0 overflow-hidden rounded-sm bg-white border border-slate-300/90 shadow-[0_10px_32px_rgba(148,163,184,0.18)]'
    header_cls = 'w-full p-5 gap-2 bg-gradient-to-r from-[#0a1526] to-[#050a14] border-b border-[#1e3a5f]/60' if is_dark else 'w-full p-5 gap-2 bg-gradient-to-r from-[#f8fbff] to-[#eaf2ff] border-b border-slate-300/90'
    title_cls = 'text-2xl font-black w-full text-center text-slate-100 tracking-wide' if is_dark else 'text-2xl font-black w-full text-center text-slate-800 tracking-wide'
    subtitle_cls = 'text-sm text-slate-400 w-full text-center' if is_dark else 'text-sm text-slate-500 w-full text-center'
    body_cls = 'w-full p-5 gap-4 bg-[#030712]' if is_dark else 'w-full p-5 gap-4 bg-[#f8fbff]'
    input_props = 'outlined dense dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense color=blue'
    code_input_props = 'outlined dense input-class=text-center dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined dense input-class=text-center color=blue'
    otp_input_props = 'outlined input-class=text-center text-xl tracking-widest dark color=cyan standout bg-color="[#050b14]"' if is_dark else 'outlined input-class=text-center text-xl tracking-widest color=blue'
    primary_btn_cls = 'w-full bg-cyan-950/45 text-cyan-300 border border-cyan-500/45 hover:bg-cyan-900/55 shadow-[0_0_12px_rgba(34,211,238,0.22)] h-10 font-black rounded-sm' if is_dark else 'w-full bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 shadow-[0_6px_16px_rgba(56,189,248,0.16)] h-10 font-black rounded-sm'
    success_btn_cls = 'w-full bg-emerald-950/45 text-emerald-300 border border-emerald-500/45 hover:bg-emerald-900/55 h-10 font-black rounded-sm' if is_dark else 'w-full bg-emerald-100 text-emerald-700 border border-emerald-300 hover:bg-emerald-200 h-10 font-black rounded-sm'
    back_btn_cls = 'w-full text-slate-300 border-slate-600 hover:bg-slate-800/40 text-xs font-bold rounded-sm' if is_dark else 'w-full text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold rounded-sm'
    footer_cls = 'text-xs text-cyan-500/70 mt-2 w-full text-center font-mono opacity-80 font-bold' if is_dark else 'text-xs text-sky-700/70 mt-2 w-full text-center font-mono opacity-80 font-bold'
    secret_row_cls = 'w-full justify-center items-center gap-1 bg-[#050b14] p-2 rounded-sm border border-[#1e3a5f]/45 cursor-pointer' if is_dark else 'w-full justify-center items-center gap-1 bg-sky-50 p-2 rounded-sm border border-slate-300/90 cursor-pointer'
    secret_text_cls = 'text-xs font-mono text-cyan-300' if is_dark else 'text-xs font-mono text-sky-700'
    icon_hint_cls = 'text-slate-400 text-xs' if is_dark else 'text-slate-500 text-xs'

    container = ui.card().classes(container_cls)

    def render_step1():
        container.clear()
        with container:
            with ui.column().classes(header_cls):
                ui.label('X-Fusion Panel').classes(title_cls)
                ui.label('请登录以继续').classes(subtitle_cls)

            with ui.column().classes(body_cls):
                username = ui.input('账号').props(input_props).classes('w-full')
                password = ui.input('密码', password=True).props(input_props).classes('w-full').on('keydown.enter', lambda: check_cred())

            def check_cred():
                if username.value == ADMIN_USER and password.value == ADMIN_PASS:
                    check_mfa()
                else:
                    ui.notify('账号或密码错误', color='negative', position='top')

                ui.button('下一步', on_click=check_cred).props('flat').classes(primary_btn_cls)
                ui.label('© Powered by 小龙女她爸').classes(footer_cls)

    def check_mfa():
        secret = ADMIN_CONFIG.get('mfa_secret')
        if not secret:
            new_secret = pyotp.random_base32()
            render_setup(new_secret)
        else:
            render_verify(secret)

    def render_setup(secret):
        container.clear()

        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(name=ADMIN_USER, issuer_name="X-Fusion Panel")
        qr = qrcode.make(totp_uri)
        img_buffer = io.BytesIO()
        qr.save(img_buffer, format='PNG')
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')

        with container:
            with ui.column().classes(header_cls):
                ui.label('绑定二次验证 (MFA)').classes('text-xl font-black w-full text-center text-slate-100 tracking-wide' if is_dark else 'text-xl font-black w-full text-center text-slate-800 tracking-wide')
                ui.label('请使用 Authenticator App 扫描').classes('text-xs text-slate-400 w-full text-center' if is_dark else 'text-xs text-slate-500 w-full text-center')

            with ui.column().classes(body_cls):
                with ui.row().classes('w-full justify-center'):
                    ui.image(f'data:image/png;base64,{img_b64}').style('width: 180px; height: 180px').classes('border border-[#1e3a5f]/45 rounded-sm bg-white p-2')

                with ui.row().classes(secret_row_cls).on('click', lambda: safe_copy_to_clipboard(secret)):
                    ui.label(secret).classes(secret_text_cls)
                    ui.icon('content_copy').classes(icon_hint_cls)

                code = ui.input('验证码', placeholder='6位数字').props(code_input_props).classes('w-full')

            async def confirm():
                totp = pyotp.TOTP(secret)
                if totp.verify(code.value):
                    ADMIN_CONFIG['mfa_secret'] = secret
                    await save_admin_config()
                    ui.notify('绑定成功', type='positive')
                    finish()
                else:
                    ui.notify('验证码错误', type='negative')

                ui.button('确认绑定', on_click=confirm).props('flat').classes(success_btn_cls)

    def render_verify(secret):
        container.clear()
        with container:
            with ui.column().classes(header_cls):
                ui.label('安全验证').classes('text-xl font-black w-full text-center text-slate-100 tracking-wide' if is_dark else 'text-xl font-black w-full text-center text-slate-800 tracking-wide')
            with ui.column().classes(body_cls):
                with ui.column().classes('w-full items-center gap-2'):
                    ui.icon('verified_user').classes('text-6xl text-cyan-400 mb-1 drop-shadow-[0_0_8px_rgba(34,211,238,0.35)]')
                    ui.label('请输入 Authenticator 动态码').classes('text-xs text-slate-400' if is_dark else 'text-xs text-slate-500')

                code = ui.input(placeholder='------').props(otp_input_props).classes('w-full')
            code.on('keydown.enter', lambda: verify())
            ui.timer(0.1, lambda: ui.run_javascript('document.querySelector(".q-field__native").focus()'), once=True)

            def verify():
                totp = pyotp.TOTP(secret)
                if totp.verify(code.value):
                    finish()
                else:
                    ui.notify('无效的验证码', type='negative', position='top')
                    code.value = ''

                ui.button('验证登录', on_click=verify).props('flat').classes(primary_btn_cls)
                ui.button('返回', on_click=render_step1).props('outline dense color=grey').classes(back_btn_cls)

    def finish():
        app.storage.user['authenticated'] = True

        if 'session_version' not in ADMIN_CONFIG:
            ADMIN_CONFIG['session_version'] = str(uuid.uuid4())[:8]
        app.storage.user['session_version'] = ADMIN_CONFIG['session_version']

        try:
            client_ip = request.headers.get('X-Forwarded-For', request.client.host).split(',')[0].strip()
            client_device_id = request.cookies.get('fp_device_id', 'Unknown_Device')
            app.storage.user['last_known_ip'] = client_ip
            app.storage.user['device_id'] = client_device_id
            geo = fetch_geo_from_ip(client_ip)
            if geo and len(geo) >= 4:
                app.storage.user['login_region'] = f"{geo[2]}-{geo[3]}"
            else:
                app.storage.user['login_region'] = '未知区域'
        except:
            pass

        ui.navigate.to('/')

    render_step1()


def check_auth(request: Request):
    """
    检查用户是否已登录，且会话版本是否有效
    """
    if not app.storage.user.get('authenticated', False):
        return False

    current_global_ver = ADMIN_CONFIG.get('session_version', 'init')
    user_ver = app.storage.user.get('session_version', '')

    if current_global_ver != user_ver:
        return False

    return True
