from __future__ import annotations

import pytest

from google_ads_mcp.cursors import CursorStore
from google_ads_mcp.errors import CursorError


def test_cursor_pages_are_bound_and_expire() -> None:
    now = [100.0]
    store = CursorStore(ttl_seconds=10, clock=lambda: now[0])
    items = [{"id": index} for index in range(5)]
    token = store.create(profile="a", binding="query", items=items, offset=2)
    assert token is not None
    page = store.page(token, profile="a", binding="query", page_size=2)
    assert page.items == [{"id": 2}, {"id": 3}]
    assert page.next_cursor == token
    final = store.page(token, profile="a", binding="query", page_size=2)
    assert final.items == [{"id": 4}]
    assert final.next_cursor is None
    with pytest.raises(CursorError):
        store.page(token, profile="a", binding="query")


def test_cursor_rejects_wrong_binding_profile_page_and_expiry() -> None:
    now = [0.0]
    store = CursorStore(ttl_seconds=1, clock=lambda: now[0])
    token = store.create(profile="a", binding="b", items=[{}, {}], offset=1)
    assert token
    with pytest.raises(CursorError, match="match"):
        store.page(token, profile="wrong", binding="b")
    with pytest.raises(CursorError, match="match"):
        store.page(token, profile="a", binding="wrong")
    with pytest.raises(CursorError, match="pageSize"):
        store.page(token, profile="a", binding="b", page_size=0)
    now[0] = 2.0
    with pytest.raises(CursorError, match="expired"):
        store.page(token, profile="a", binding="b")


def test_cursor_limit_and_empty_cursor() -> None:
    store = CursorStore(max_per_profile=1)
    assert store.create(profile="a", binding="x", items=[{}], offset=1) is None
    assert store.create(profile="a", binding="x", items=[{}, {}], offset=1)
    with pytest.raises(CursorError, match="limit"):
        store.create(profile="a", binding="y", items=[{}, {}], offset=1)


def test_cursor_pages_preserve_partial_evidence() -> None:
    store = CursorStore()
    token = store.create(
        profile="a",
        binding="query",
        items=[{"id": 1}, {"id": 2}],
        offset=1,
        status="partial",
        partial=True,
        truncated=True,
        limitations=("narrow the filters",),
    )
    assert token is not None
    page = store.page(token, profile="a", binding="query")
    assert page.status == "partial"
    assert page.partial is True
    assert page.truncated is True
    assert page.limitations == ("narrow the filters",)
