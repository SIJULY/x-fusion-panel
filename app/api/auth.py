from nicegui import ui

from app.ui.pages.login_page import login_page
from app.ui.pages.main_page import main_page


def register_auth_pages():
    ui.page('/login')(login_page)
    ui.page('/')(main_page)
