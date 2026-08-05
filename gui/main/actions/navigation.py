def is_current_workspace(app, workspace: str) -> bool:
    current_page = getattr(app, "_current_page_key", None)
    if not callable(current_page):
        return False
    try:
        key = current_page()
    except (AttributeError, RuntimeError, TypeError):
        return False
    return bool(key) and str(key[0] or "").strip().lower() == workspace.strip().lower()
