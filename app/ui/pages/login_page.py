import base64
import io
import json
import uuid

import pyotp
import qrcode
from fastapi import Request
from nicegui import app, ui, run

from app.core.config import ADMIN_PASS, ADMIN_USER
from app.core.state import ADMIN_CONFIG, SERVERS_CACHE
from app.storage.repositories import save_admin_config
from app.ui.common.notifications import safe_copy_to_clipboard
from app.utils.geo import fetch_geo_from_ip, get_coords_from_name


def login_page(request: Request):
    ui.add_head_html('''
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@700;800;900&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            let fp = localStorage.getItem('fp_device_id');
            if (!fp) {
                fp = 'dev-' + Math.random().toString(36).substr(2, 9) + '-' + Date.now().toString(36);
                localStorage.setItem('fp_device_id', fp);
            }
            document.cookie = "fp_device_id=" + fp + "; path=/; max-age=315360000";
        });

        window.initXFusionLoginMap = async function(payload) {
            const container = document.getElementById('xf-login-map');
            if (!container || !window.echarts) return;

            const fallbackPools = {
                asia: [
                    {name: 'Tokyo', lon: 139.7, lat: 35.6}, {name: 'Singapore', lon: 103.8, lat: 1.35},
                    {name: 'Seoul', lon: 126.9, lat: 37.5}, {name: 'Hong Kong', lon: 114.1, lat: 22.3},
                    {name: 'Beijing', lon: 116.4, lat: 39.9}, {name: 'Mumbai', lon: 72.8, lat: 19.0},
                    {name: 'Dubai', lon: 55.3, lat: 25.2}
                ],
                europe: [
                    {name: 'London', lon: -0.1, lat: 51.5}, {name: 'Frankfurt', lon: 8.68, lat: 50.1},
                    {name: 'Paris', lon: 2.35, lat: 48.85}, {name: 'Amsterdam', lon: 4.9, lat: 52.37},
                    {name: 'Madrid', lon: -3.7, lat: 40.4}
                ],
                africa: [
                    {name: 'Johannesburg', lon: 28.0, lat: -26.2}, {name: 'Cape Town', lon: 18.4, lat: -33.9},
                    {name: 'Cairo', lon: 31.2, lat: 30.0}, {name: 'Lagos', lon: 3.37, lat: 6.52}
                ],
                north_america: [
                    {name: 'New York', lon: -74.0, lat: 40.7}, {name: 'Los Angeles', lon: -118.2, lat: 34.0},
                    {name: 'Toronto', lon: -79.3, lat: 43.7}, {name: 'Dallas', lon: -96.8, lat: 32.8}
                ],
                south_america: [
                    {name: 'Sao Paulo', lon: -46.6, lat: -23.5}, {name: 'Buenos Aires', lon: -58.4, lat: -34.6},
                    {name: 'Santiago', lon: -70.6, lat: -33.4}, {name: 'Bogota', lon: -74.1, lat: 4.7}
                ],
                oceania: [
                    {name: 'Sydney', lon: 151.2, lat: -33.8}, {name: 'Melbourne', lon: 144.9, lat: -37.8},
                    {name: 'Auckland', lon: 174.7, lat: -36.8}
                ]
            };
            const randomPick = (arr, count) => [...arr].sort(() => Math.random() - 0.5).slice(0, Math.min(count, arr.length));
            const target = payload && payload.target ? payload.target : { lon: 116.4, lat: 39.9 };
            const pools = payload && payload.pools ? payload.pools : fallbackPools;
            const nodes = [
                ...randomPick(pools.asia || [], 3),
                ...randomPick(pools.europe || [], 2),
                ...randomPick(pools.africa || [], 2),
                ...randomPick(pools.north_america || [], 2),
                ...randomPick(pools.south_america || [], 2),
                ...randomPick(pools.oceania || [], 1 + Math.floor(Math.random() * 2))
            ].slice(0, 10 + Math.floor(Math.random() * 6));
            const linePairs = nodes.map(from => ({ from, to: target }));

            const existing = echarts.getInstanceByDom(container);
            if (existing) existing.dispose();
            const chart = echarts.init(container);

            fetch('/static/world.json')
                .then(res => res.json())
                .then(worldJson => {
                    echarts.registerMap('world', worldJson);
                    const option = {
                        backgroundColor: 'transparent',
                        geo: {
                            map: 'world',
                            roam: false,
                            zoom: 1.15,
                            center: [15, 12],
                            itemStyle: {
                                areaColor: '#dbeafe',
                                borderColor: '#93c5fd',
                                borderWidth: 0.8
                            },
                            emphasis: {
                                label: { show: false },
                                itemStyle: { areaColor: '#bfdbfe' }
                            },
                            silent: true
                        },
                        series: [
                            {
                                type: 'lines',
                                coordinateSystem: 'geo',
                                zlevel: 2,
                                effect: {
                                    show: true,
                                    period: 4,
                                    trailLength: 0.5,
                                    color: '#00cfff',
                                    symbol: 'arrow',
                                    symbolSize: 6
                                },
                                lineStyle: {
                                    color: '#00cfff',
                                    width: 1,
                                    opacity: 0,
                                    curveness: 0.2
                                },
                                data: linePairs.map(item => ({
                                    coords: [[item.from.lon, item.from.lat], [item.to.lon, item.to.lat]]
                                }))
                            },
                            {
                                type: 'effectScatter',
                                coordinateSystem: 'geo',
                                zlevel: 3,
                                rippleEffect: { scale: 3.8, brushType: 'stroke' },
                                symbolSize: 7,
                                itemStyle: {
                                    color: '#0ea5e9',
                                    shadowBlur: 16,
                                    shadowColor: 'rgba(14,165,233,0.35)'
                                },
                                data: nodes.map(item => ({ name: item.name, value: [item.lon, item.lat] }))
                            },
                            {
                                type: 'effectScatter',
                                coordinateSystem: 'geo',
                                zlevel: 4,
                                rippleEffect: { scale: 5.2, brushType: 'stroke' },
                                symbolSize: 10,
                                itemStyle: {
                                    color: '#10b981',
                                    shadowBlur: 22,
                                    shadowColor: 'rgba(16,185,129,0.48)'
                                },
                                label: {
                                    show: true,
                                    position: 'top',
                                    formatter: params => params.data && params.data.name ? params.data.name : 'LOGIN SOURCE',
                                    color: '#059669',
                                    fontSize: 11,
                                    fontWeight: 'bold'
                                },
                                data: [{ name: target.name || 'LOGIN', value: [target.lon, target.lat] }]
                            }
                        ]
                    };
                    chart.setOption(option);
                    window.addEventListener('resize', () => chart.resize());
                });
        };
    </script>
    <style>
        body {
            background:
                radial-gradient(circle at 12% 18%, rgba(56,189,248,0.18), transparent 26%),
                radial-gradient(circle at 85% 16%, rgba(16,185,129,0.10), transparent 20%),
                radial-gradient(circle at 82% 78%, rgba(99,102,241,0.10), transparent 22%),
                linear-gradient(180deg, #f8fbff 0%, #eef4ff 46%, #eaf2ff 100%) !important;
        }
        .xf-watermark-main {
            font-family: 'Orbitron', sans-serif;
            font-weight: 900;
            letter-spacing: 0.08em;
            white-space: nowrap;
            line-height: 1;
            font-size: clamp(40px, 7.2vw, 96px);
            max-width: 92vw;
            background: linear-gradient(135deg, rgba(125,211,252,0.34), rgba(59,130,246,0.16), rgba(16,185,129,0.22));
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
            text-shadow: 0 0 22px rgba(125,211,252,0.10), 0 0 60px rgba(59,130,246,0.05);
        }
        .xf-watermark-sub {
            font-family: 'Orbitron', sans-serif;
            font-weight: 900;
            letter-spacing: 0.08em;
            white-space: nowrap;
            line-height: 1;
            font-size: clamp(24px, 4.6vw, 58px);
            max-width: 72vw;
            color: rgba(148,163,184,0.10);
            -webkit-text-stroke: 1px rgba(148,163,184,0.26);
            text-shadow: 0 0 18px rgba(148,163,184,0.06);
        }
    </style>
    ''')

    container_cls = 'absolute-center w-full max-w-md p-0 gap-0 overflow-hidden rounded-sm bg-white/95 border border-slate-300/90 shadow-[0_18px_48px_rgba(148,163,184,0.22)] backdrop-blur-sm'
    header_cls = 'w-full p-5 gap-2 bg-gradient-to-r from-[#ffffff] via-[#f8fbff] to-[#eef6ff] border-b border-slate-300/90'
    title_cls = 'text-[28px] font-black w-full text-center text-slate-800 tracking-wide'
    title_style = "font-family:'Orbitron',sans-serif; letter-spacing:0.06em;"
    subtitle_cls = 'text-sm text-slate-500 w-full text-center'
    body_cls = 'w-full p-5 gap-4 bg-gradient-to-b from-[#fbfdff] to-[#f4f8ff]'
    input_props = 'outlined dense color=blue'
    code_input_props = 'outlined dense input-class=text-center color=blue'
    otp_input_props = 'outlined input-class=text-center text-xl tracking-widest color=blue'
    primary_btn_cls = 'w-full bg-sky-100 text-sky-700 border border-sky-300 hover:bg-sky-200 shadow-[0_8px_18px_rgba(56,189,248,0.16)] h-10 font-black rounded-sm'
    success_btn_cls = 'w-full bg-emerald-100 text-emerald-700 border border-emerald-300 hover:bg-emerald-200 h-10 font-black rounded-sm shadow-[0_8px_18px_rgba(16,185,129,0.12)]'
    back_btn_cls = 'w-full text-slate-600 border-slate-300 hover:bg-slate-100 text-xs font-bold rounded-sm h-10'
    footer_cls = 'text-xs text-slate-500 mt-2 w-full text-center font-mono opacity-80 font-bold'
    secret_row_cls = 'w-full justify-center items-center gap-1 bg-sky-50 p-2 rounded-sm border border-slate-300/90 cursor-pointer hover:bg-sky-100 transition-colors'
    secret_text_cls = 'text-xs font-mono text-sky-700'
    icon_hint_cls = 'text-slate-500 text-xs'

    def get_admin_username():
        return str(ADMIN_CONFIG.get('admin_username') or ADMIN_USER)

    def get_admin_password():
        return str(ADMIN_CONFIG.get('admin_password') or ADMIN_PASS)

    continent_pools = {
        'asia': [
            {'name': 'Tokyo', 'lon': 139.7, 'lat': 35.6},
            {'name': 'Singapore', 'lon': 103.8, 'lat': 1.35},
            {'name': 'Seoul', 'lon': 126.9, 'lat': 37.5},
            {'name': 'Hong Kong', 'lon': 114.1, 'lat': 22.3},
            {'name': 'Beijing', 'lon': 116.4, 'lat': 39.9},
            {'name': 'Mumbai', 'lon': 72.8, 'lat': 19.0},
            {'name': 'Dubai', 'lon': 55.3, 'lat': 25.2},
        ],
        'europe': [
            {'name': 'London', 'lon': -0.1, 'lat': 51.5},
            {'name': 'Frankfurt', 'lon': 8.68, 'lat': 50.1},
            {'name': 'Paris', 'lon': 2.35, 'lat': 48.85},
            {'name': 'Amsterdam', 'lon': 4.9, 'lat': 52.37},
            {'name': 'Madrid', 'lon': -3.7, 'lat': 40.4},
        ],
        'africa': [
            {'name': 'Johannesburg', 'lon': 28.0, 'lat': -26.2},
            {'name': 'Cape Town', 'lon': 18.4, 'lat': -33.9},
            {'name': 'Cairo', 'lon': 31.2, 'lat': 30.0},
            {'name': 'Lagos', 'lon': 3.37, 'lat': 6.52},
        ],
        'north_america': [
            {'name': 'New York', 'lon': -74.0, 'lat': 40.7},
            {'name': 'Los Angeles', 'lon': -118.2, 'lat': 34.0},
            {'name': 'Toronto', 'lon': -79.3, 'lat': 43.7},
            {'name': 'Dallas', 'lon': -96.8, 'lat': 32.8},
        ],
        'south_america': [
            {'name': 'Sao Paulo', 'lon': -46.6, 'lat': -23.5},
            {'name': 'Buenos Aires', 'lon': -58.4, 'lat': -34.6},
            {'name': 'Santiago', 'lon': -70.6, 'lat': -33.4},
            {'name': 'Bogota', 'lon': -74.1, 'lat': 4.7},
        ],
        'oceania': [
            {'name': 'Sydney', 'lon': 151.2, 'lat': -33.8},
            {'name': 'Melbourne', 'lon': 144.9, 'lat': -37.8},
            {'name': 'Auckland', 'lon': 174.7, 'lat': -36.8},
        ],
    }

    for s in SERVERS_CACHE:
        lat, lon = None, None
        if 'lat' in s and 'lon' in s:
            lat, lon = s['lat'], s['lon']
        else:
            coords = get_coords_from_name(s.get('name', ''))
            if coords:
                lat, lon = coords[0], coords[1]
        if lat is None or lon is None:
            continue
        name_upper = str(s.get('name', '')).upper()
        node = {'lat': lat, 'lon': lon, 'name': s.get('name', 'NODE')[:12]}
        if any(k in name_upper for k in ['JP', 'JAPAN', 'TOKYO', 'SG', 'SINGAPORE', 'HK', 'HONG', 'KR', 'KOREA', 'CN', 'CHINA', 'IN', 'INDIA', 'DUBAI', 'AE']):
            continent_pools['asia'].append(node)
        elif any(k in name_upper for k in ['UK', 'LONDON', 'DE', 'GERMANY', 'FR', 'FRANCE', 'NL', 'AMSTERDAM', 'ES', 'SPAIN']):
            continent_pools['europe'].append(node)
        elif any(k in name_upper for k in ['US', 'USA', 'NEW YORK', 'LOS ANGELES', 'CA', 'CANADA', 'TORONTO', 'DALLAS']):
            continent_pools['north_america'].append(node)
        elif any(k in name_upper for k in ['BR', 'BRAZIL', 'SAO', 'ARGENTINA', 'BUENOS', 'CHILE', 'BOGOTA', 'COLOMBIA']):
            continent_pools['south_america'].append(node)
        elif any(k in name_upper for k in ['AU', 'AUSTRALIA', 'SYDNEY', 'MELBOURNE', 'NZ', 'AUCKLAND']):
            continent_pools['oceania'].append(node)
        elif any(k in name_upper for k in ['ZA', 'AFRICA', 'CAIRO', 'LAGOS', 'JOHANNESBURG', 'CAPE']):
            continent_pools['africa'].append(node)

    with ui.element('div').classes('fixed inset-0 overflow-hidden pointer-events-none'):
        ui.html('<div id="xf-login-map" style="position:absolute;inset:0;width:100%;height:100%;opacity:.98"></div>')
        ui.element('div').classes('absolute -top-24 -left-20 w-72 h-72 rounded-full bg-sky-300/20 blur-3xl')
        ui.element('div').classes('absolute top-10 right-0 w-80 h-80 rounded-full bg-emerald-300/10 blur-3xl')
        ui.element('div').classes('absolute bottom-0 left-1/2 -translate-x-1/2 w-[38rem] h-56 rounded-full bg-indigo-200/20 blur-3xl')
        ui.label('X-Fusion-Pro').classes('xf-watermark-main absolute top-[8%] left-1/2 -translate-x-1/2 select-none')
        ui.label('X-Fusion-Pro').classes('xf-watermark-sub absolute bottom-[9%] right-[7%] -rotate-12 select-none')
        with ui.column().classes('absolute left-[7%] top-[20%] gap-2'):
            ui.label('LIGHTWEIGHT').classes('text-[11px] font-black tracking-[0.35em] text-sky-500/75')
            ui.label('FAST · CLEAN · SECURE').classes('text-[11px] font-bold tracking-[0.25em] text-slate-400')
        with ui.row().classes('absolute left-[8%] bottom-[12%] items-center gap-2 rounded-sm border border-white/70 bg-white/55 px-3 py-2 shadow-[0_10px_24px_rgba(148,163,184,0.10)] backdrop-blur-sm'):
            ui.icon('shield').classes('text-emerald-500 text-lg')
            with ui.column().classes('gap-0'):
                ui.label('Secure Access').classes('text-[12px] font-black text-slate-700')
                ui.label('MFA · Session Guard · Device Fingerprint').classes('text-[10px] font-mono text-slate-400')
    login_map_payload = {'target': {'lon': 116.4, 'lat': 39.9}, 'pools': continent_pools}
    ui.timer(0.1, lambda: ui.run_javascript(f'window.initXFusionLoginMap && window.initXFusionLoginMap({json.dumps(login_map_payload, ensure_ascii=False)})'), once=True)

    container = ui.card().classes(container_cls)

    async def complete_first_run():
        new_user = setup_username.value.strip()
        new_pass = setup_password.value.strip()
        confirm_pass = setup_password_confirm.value.strip()

        if not new_user:
            ui.notify('请填写管理员账号', color='warning', position='top')
            return
        if len(new_pass) < 6:
            ui.notify('登录密码至少需要 6 位', color='warning', position='top')
            return
        if new_pass != confirm_pass:
            ui.notify('两次输入的密码不一致', color='negative', position='top')
            return

        ADMIN_CONFIG['admin_username'] = new_user
        ADMIN_CONFIG['admin_password'] = new_pass
        ADMIN_CONFIG['probe_enabled'] = bool(setup_probe_enabled.value)
        ADMIN_CONFIG['setup_completed'] = True
        ADMIN_CONFIG['session_version'] = str(uuid.uuid4())[:8]
        await save_admin_config()
        ui.notify('初始化设置已保存，请使用新账号登录', type='positive', position='top')
        render_step1()

    def render_first_run_setup():
        container.clear()
        with container:
            with ui.column().classes(header_cls):
                ui.label('首次运行设置').classes('text-xl font-black w-full text-center text-slate-800 tracking-wide')
                ui.label('请先修改默认账号密码，并选择是否启用探针').classes(subtitle_cls)

            with ui.column().classes(body_cls):
                global setup_username, setup_password, setup_password_confirm, setup_probe_enabled
                setup_username = ui.input('管理员账号', value=get_admin_username()).props(input_props).classes('w-full')
                setup_password = ui.input('登录密码', password=True, value='' if get_admin_password() == 'admin' else get_admin_password()).props(input_props).classes('w-full')
                setup_password_confirm = ui.input('确认密码', password=True).props(input_props).classes('w-full')
                setup_probe_enabled = ui.switch('启用探针功能（可采集 VPS 负载、流量、延迟，并尝试读取本机 x-ui 入站数据）', value=bool(ADMIN_CONFIG.get('probe_enabled', True))).classes('w-full text-slate-700 font-bold')
                ui.label('关闭后不会自动安装/启用探针；仍可通过 X-UI API/SSH 同步节点与订阅信息。之后可在配置文件中重新开启。').classes('text-xs text-slate-500 leading-relaxed')
                setup_password_confirm.on('keydown.enter', lambda: complete_first_run())
                ui.button('保存初始化设置', icon='save', on_click=complete_first_run).props('flat').classes(success_btn_cls)

    def render_step1():
        container.clear()
        with container:
            with ui.column().classes(header_cls):
                ui.label('X-Fusion-Pro').classes(title_cls).style(title_style)
                ui.label('欢迎回来，请登录以继续').classes(subtitle_cls)

            with ui.column().classes(body_cls):
                username = ui.input('账号').props(input_props).classes('w-full')
                password = ui.input('密码', password=True).props(input_props).classes('w-full')

                def check_cred():
                    if username.value == get_admin_username() and password.value == get_admin_password():
                        check_mfa()
                    else:
                        ui.notify('账号或密码错误', color='negative', position='top')

                password.on('keydown.enter', lambda: check_cred())
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

        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(name=get_admin_username(), issuer_name="X-Fusion-Pro")
        qr = qrcode.make(totp_uri)
        img_buffer = io.BytesIO()
        qr.save(img_buffer, format='PNG')
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')

        with container:
            with ui.column().classes(header_cls):
                ui.label('绑定二次验证 (MFA)').classes('text-xl font-black w-full text-center text-slate-800 tracking-wide')
                ui.label('请使用 Authenticator App 扫描').classes('text-xs text-slate-500 w-full text-center')

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
                        await finish()
                    else:
                        ui.notify('验证码错误', type='negative')

                code.on('keydown.enter', lambda: confirm())
                ui.button('确认绑定', on_click=confirm).props('flat').classes(success_btn_cls)

    def render_verify(secret):
        container.clear()
        with container:
            with ui.column().classes(header_cls):
                ui.label('安全验证').classes('text-xl font-black w-full text-center text-slate-800 tracking-wide')
            with ui.column().classes(body_cls):
                with ui.column().classes('w-full items-center gap-2'):
                    ui.icon('verified_user').classes('text-6xl text-sky-500 mb-1 drop-shadow-[0_6px_12px_rgba(56,189,248,0.18)]')
                    ui.label('请输入 Authenticator 动态码').classes('text-xs text-slate-500')

                code = ui.input(placeholder='------').props(otp_input_props).classes('w-full')

                async def verify():
                    totp = pyotp.TOTP(secret)
                    if totp.verify(code.value):
                        await finish()
                    else:
                        ui.notify('无效的验证码', type='negative', position='top')
                        code.value = ''

                code.on('keydown.enter', lambda: verify())
                ui.button('验证登录', on_click=verify).props('flat').classes(primary_btn_cls)
                ui.button('返回', on_click=render_step1).props('outline color=grey').classes(back_btn_cls)
            ui.timer(0.1, lambda: ui.run_javascript('document.querySelector(".q-field__native").focus()'), once=True)

    async def finish():
        app.storage.user['authenticated'] = True
        app.storage.user['is_dark'] = False

        if 'session_version' not in ADMIN_CONFIG:
            ADMIN_CONFIG['session_version'] = str(uuid.uuid4())[:8]
        app.storage.user['session_version'] = ADMIN_CONFIG['session_version']

        try:
            client_ip = request.headers.get('X-Forwarded-For', request.client.host).split(',')[0].strip()
            client_device_id = request.cookies.get('fp_device_id', 'Unknown_Device')
            app.storage.user['last_known_ip'] = client_ip
            app.storage.user['device_id'] = client_device_id
            geo = await run.io_bound(fetch_geo_from_ip, client_ip)
            if geo and len(geo) >= 4:
                app.storage.user['login_region'] = f"{geo[2]}-{geo[3]}"
            else:
                app.storage.user['login_region'] = '未知区域'
        except:
            pass

        next_path = str(request.query_params.get('next') or '/').strip()
        if not next_path.startswith('/') or next_path.startswith('//') or next_path.startswith('/login'):
            next_path = '/'
        ui.navigate.to(next_path)

    if not ADMIN_CONFIG.get('setup_completed'):
        render_first_run_setup()
    else:
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
