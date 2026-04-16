from app.core.config import AUTO_COUNTRY_MAP
from app.core.logging import logger
from app.storage.repositories import save_servers
from app.utils.geo import fetch_geo_from_ip, get_flag_for_country


async def job_check_geo_ip():
    from nicegui import run
    from app.core.state import SERVERS_CACHE
    from app.ui.common.notifications import safe_notify
    from app.ui.components.dashboard import refresh_dashboard_ui
    from app.ui.components.sidebar import render_sidebar_content

    logger.info('🌍 [定时任务] 开始全量 IP 归属地检测与名称修正...')
    data_changed = False

    known_flags = []
    for val in AUTO_COUNTRY_MAP.values():
        icon = val.split(' ')[0]
        if icon and icon not in known_flags:
            known_flags.append(icon)

    for s in SERVERS_CACHE:
        old_name = s.get('name', '')
        new_name = old_name

        if new_name.startswith('🏳️ ') or new_name.startswith('🏳️'):
            if len(new_name) > 2:
                new_name = new_name.replace('🏳️', '').strip()
                logger.info(f'🧹 [清洗白旗] {old_name} -> {new_name}')

        has_flag = any(flag in new_name for flag in known_flags)

        if not has_flag:
            try:
                geo = await run.io_bound(fetch_geo_from_ip, s['url'])
                if geo:
                    s['lat'] = geo[0]
                    s['lon'] = geo[1]
                    s['_detected_region'] = geo[2]
                    flag_prefix = get_flag_for_country(geo[2])
                    flag_icon = flag_prefix.split(' ')[0]
                    if flag_icon and flag_icon not in new_name:
                        new_name = f'{flag_icon} {new_name}'
                        logger.info(f'✨ [自动修正] {old_name} -> {new_name}')
            except:
                pass

        if new_name != old_name:
            s['name'] = new_name
            data_changed = True

    if data_changed:
        await save_servers()
        await refresh_dashboard_ui()
        try:
            render_sidebar_content.refresh()
        except:
            pass
        safe_notify('✅ 已清理白旗并修正服务器名称', 'positive')
    else:
        logger.info('✅ 名称检查完毕，无需修正')
