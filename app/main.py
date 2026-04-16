from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STATIC_DIR = PROJECT_ROOT / 'static'

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from nicegui import app, ui


def register_static_files() -> None:
    if STATIC_DIR.exists():
        app.add_static_files('/static', str(STATIC_DIR))

from app.api.auth import register_auth_pages
from app.api.notifications import send_telegram_message
from app.api.status import register_status_page
from app.api.subscriptions import (
    group_sub_handler,
    short_group_handler,
    short_sub_handler,
    sub_handler,
)
from app.core.logging import logger
from app.jobs.startup import startup_sequence
from app.services.dashboard import get_dashboard_live_data
from app.services.probe import auto_register_node, probe_push_data, probe_register
from app.storage.bootstrap import init_data


register_static_files()


@app.post("/api/probe/push")
async def api_probe_push(request: Request):
    return await probe_push_data(request)


@app.post('/api/probe/register')
async def api_probe_register(request: Request):
    return await probe_register(request)


@app.post('/api/auto_register_node')
async def api_auto_register_node(request: Request):
    return await auto_register_node(request)


@app.get('/sub/{token}')
async def api_sub_handler(token: str):
    return await sub_handler(token)


@app.get('/sub/group/{group_b64}')
async def api_group_sub_handler(group_b64: str):
    return await group_sub_handler(group_b64)


@app.get('/get/sub/{target}/{token}')
async def api_short_sub_handler(target: str, token: str, request: Request):
    return await short_sub_handler(target, token, request)


@app.get('/get/group/{target}/{group_b64}')
async def api_short_group_handler(target: str, group_b64: str, request: Request):
    return await short_group_handler(target, group_b64, request)


@app.get('/api/dashboard/live_data')
def api_dashboard_live_data():
    return get_dashboard_live_data()


def bootstrap_app():
    logger.info('🚀 系统正在初始化...')
    init_data()
    register_auth_pages()
    register_status_page()
    app.on_startup(startup_sequence)


bootstrap_app()


if __name__ in {'__main__', '__mp_main__'}:
    ui.run(
        title='X-Fusion Panel',
        host='0.0.0.0',
        port=8080,
        language='zh-CN',
        storage_secret='sijuly_secret_key',
        reload=False,
        reconnect_timeout=600.0,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        timeout_keep_alive=60,
    )
