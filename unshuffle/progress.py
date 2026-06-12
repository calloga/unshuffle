import sys

def print_progress(current, total, prefix='', suffix='', length=50, fill='#'):
    """Terminal progress bar."""
    if total == 0:
        return
    current = min(current, total)
    percent = f"{100 * current / total:.1f}"
    filledLength = int(length * current // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    sys.stdout.write(f'\r{prefix} |{bar}| {percent}% {suffix}')
    sys.stdout.flush()
    if current == total:
        print()
