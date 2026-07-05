import sys


def file_manager_name() -> str:
    if sys.platform == "darwin":
        return "Finder"
    return "Explorer"


def view_in_file_manager_label() -> str:
    return f"View in {file_manager_name()}"
