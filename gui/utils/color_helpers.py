from PySide6.QtGui import QColor


def make_qcolor(value: str) -> QColor:
    text = (value or "").strip()
    if not text:
        return QColor()

    lower = text.lower()
    if lower.startswith("rgba(") and lower.endswith(")"):
        body = text[text.find("(") + 1 : -1]
        parts = [p.strip() for p in body.split(",")]
        if len(parts) == 4:
            try:
                r = max(0, min(255, int(float(parts[0]))))
                g = max(0, min(255, int(float(parts[1]))))
                b = max(0, min(255, int(float(parts[2]))))
                alpha = float(parts[3])
                if 0.0 <= alpha <= 1.0:
                    alpha *= 255
                a = max(0, min(255, int(round(alpha))))
                return QColor(r, g, b, a)
            except ValueError:
                pass

    if lower.startswith("rgb(") and lower.endswith(")"):
        body = text[text.find("(") + 1 : -1]
        parts = [p.strip() for p in body.split(",")]
        if len(parts) == 3:
            try:
                r = max(0, min(255, int(float(parts[0]))))
                g = max(0, min(255, int(float(parts[1]))))
                b = max(0, min(255, int(float(parts[2]))))
                return QColor(r, g, b)
            except ValueError:
                pass

    return QColor(text)
