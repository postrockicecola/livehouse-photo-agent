import pytest

from scripts.eval.relabel_qwen import _api_key


def test_api_key_rejects_unicode_placeholder() -> None:
    with pytest.raises(SystemExit, match="non-ASCII"):
        _api_key("你的 token")


def test_api_key_strips_outer_whitespace_but_rejects_internal_whitespace() -> None:
    assert _api_key("  sk-valid  ") == "sk-valid"
    with pytest.raises(SystemExit, match="whitespace"):
        _api_key("sk-invalid key")
