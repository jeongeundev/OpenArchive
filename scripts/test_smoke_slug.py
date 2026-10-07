import pytest
from smoke_slug import phase_slug


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Ship Smoke", "ship-smoke"),
        ("Phase-98 v2", "phase-98-v2"),
        ("Hello, World! (#217)", "hello-world-217"),
        ("한글ABC_123", "abc123"),
        ("A  B\tC\nD", "a--b-c-d"),
    ],
)
def test_phase_slug(title: str, expected: str) -> None:
    assert phase_slug(title) == expected


@pytest.mark.parametrize("title", ["", "한글!?"])
def test_phase_slug_empty_result_raises(title: str) -> None:
    with pytest.raises(ValueError):
        phase_slug(title)
