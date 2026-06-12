import re


def get_pack_prefix(pack_name: str, _category: str = "", _audio_type: str = "") -> str:
    if not pack_name:
        return ""

    clean = re.sub(r"[^a-zA-Z0-9\s\-_]", "", pack_name)
    clean = re.sub(r"[\s\-_]+", "_", clean).strip("_")

    if len(clean) <= 30:
        return clean.upper()

    words = [word for word in re.split(r"[_]", clean) if word]
    if not words:
        return clean[:10].upper()

    prefix_parts = [words[0][:5].upper()]
    for word in words[1:]:
        if word:
            prefix_parts.append(word[0].upper())

    return "".join(prefix_parts)
