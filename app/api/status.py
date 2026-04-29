from nicegui import ui

from app.ui.pages.public_status import status_page_router


def register_status_page():
    ui.page('/status')(status_page_router)
