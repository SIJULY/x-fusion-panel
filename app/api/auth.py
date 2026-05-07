from nicegui import ui

from app.ui.pages.login_page import login_page
from app.ui.pages.main_page import main_page
from app.ui.pages.mobile_page import mobile_page


def register_auth_pages():
    ui.page('/login')(login_page)
    ui.page('/')(main_page)
    ui.page('/m')(mobile_page)
    ui.page('/mobile')(mobile_page)
