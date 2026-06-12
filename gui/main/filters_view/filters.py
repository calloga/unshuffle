def add_saved_filter(app, name, query):
    app.filter_controller.add_saved_filter(name, query)


def prompt_save_filter(app, name, query):
    app.filter_controller.prompt_save_filter(name, query)


def remove_saved_filter(app, query):
    app.filter_controller.remove_saved_filter(query)


def handle_saved_filter(app, query, is_active, mode="replace"):
    app.filter_controller.handle_saved_filter(query, is_active, mode=mode)


def handle_quick_filter(app, query, mode="replace"):
    app.filter_controller.handle_quick_filter(query, mode=mode)


def apply_filter_query(app, query: str, is_active: bool, mode: str = "replace"):
    app.filter_controller.apply_filter_query(query, is_active, mode=mode)


def show_header_menu(app, col, pos):
    app.filter_controller.show_header_menu(col, pos)


def toggle_column_filter(app, col, val, checked):
    app.filter_controller.toggle_column_filter(col, val, checked)


def clear_header_filter(app, col):
    app.filter_controller.clear_header_filter(col)
