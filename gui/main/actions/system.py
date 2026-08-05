from .navigation import is_current_workspace


def refresh_system_menu(app) -> None:
    menu = app.custom_menu_bar.menu_system
    menu.clear()
    if not is_current_workspace(app, "system"):
        menu.addAction(app.custom_menu_bar.act_open_system)
