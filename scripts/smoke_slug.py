import re


def phase_slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "", re.sub(r"\s", "-", title.lower()))
    if not slug:
        raise ValueError(f"slug가 비었다: {title!r}")
    return slug
