import re


def phase_slug(title: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", re.sub(r"\s", "-", title.lower()))
