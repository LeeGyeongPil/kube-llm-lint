import pytest

from kube_llm_lint.quantity import parse_quantity


@pytest.mark.parametrize("text,expected", [
    ("2Gi", 2 * 1024**3),
    ("1G", 10**9),
    ("1536Mi", 1536 * 1024**2),
    ("512Mi", 512 * 1024**2),
    ("1.5Gi", int(1.5 * 1024**3)),
    ("64Ki", 65536),
    ("100", 100),
    (4096, 4096),
])
def test_parse_quantity(text, expected):
    assert parse_quantity(text) == expected


@pytest.mark.parametrize("bad", ["", "Gi", "2 Gi x", "abc", "1Xi", True])
def test_parse_quantity_rejects(bad):
    with pytest.raises(ValueError):
        parse_quantity(bad)
