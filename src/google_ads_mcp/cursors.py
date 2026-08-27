from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google_ads_mcp.constants import MAX_PAGE_ITEMS
from google_ads_mcp.errors import CursorError
from google_ads_mcp.models import Status


@dataclass(slots=True)
class _Cursor:
    profile: str
    binding: str
    expires_at: float
    items: list[dict[str, Any]]
    offset: int
    status: Status
    partial: bool
    truncated: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CursorPage:
    items: list[dict[str, Any]]
    next_cursor: str | None
    status: Status
    partial: bool
    truncated: bool
    limitations: tuple[str, ...]


class CursorStore:
    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        max_per_profile: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_per_profile
        self._clock = clock
        self._items: dict[str, _Cursor] = {}
        self._by_profile: dict[str, set[str]] = defaultdict(set)
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for token, cursor in tuple(self._items.items()):
            if cursor.expires_at <= now:
                self._delete(token, cursor)

    def _delete(self, token: str, cursor: _Cursor) -> None:
        self._items.pop(token, None)
        profile_tokens = self._by_profile[cursor.profile]
        profile_tokens.discard(token)
        if not profile_tokens:
            self._by_profile.pop(cursor.profile, None)

    def create(
        self,
        *,
        profile: str,
        binding: str,
        items: list[dict[str, Any]],
        offset: int = MAX_PAGE_ITEMS,
        status: Status = "ok",
        partial: bool = False,
        truncated: bool = False,
        limitations: tuple[str, ...] = (),
    ) -> str | None:
        if offset >= len(items):
            return None
        now = self._clock()
        with self._lock:
            self._purge(now)
            if len(self._by_profile[profile]) >= self._max:
                raise CursorError(
                    "Profile has reached the limit of 20 active cursors", "cursor_limit"
                )
            token = secrets.token_urlsafe(32)
            self._items[token] = _Cursor(
                profile=profile,
                binding=binding,
                expires_at=now + self._ttl,
                items=items,
                offset=offset,
                status=status,
                partial=partial,
                truncated=truncated,
                limitations=limitations,
            )
            self._by_profile[profile].add(token)
            return token

    def page(
        self,
        token: str,
        *,
        profile: str,
        binding: str,
        page_size: int = MAX_PAGE_ITEMS,
    ) -> CursorPage:
        if not 1 <= page_size <= MAX_PAGE_ITEMS:
            raise CursorError(f"pageSize must be between 1 and {MAX_PAGE_ITEMS}")
        now = self._clock()
        with self._lock:
            self._purge(now)
            cursor = self._items.get(token)
            if cursor is None:
                raise CursorError("Cursor is invalid or expired")
            if not secrets.compare_digest(cursor.profile, profile) or not secrets.compare_digest(
                cursor.binding, binding
            ):
                raise CursorError("Cursor does not match this request")
            start = cursor.offset
            end = min(start + page_size, len(cursor.items))
            page = cursor.items[start:end]
            cursor.offset = end
            cursor.expires_at = now + self._ttl
            if end >= len(cursor.items):
                self._delete(token, cursor)
                next_cursor = None
            else:
                next_cursor = token
            return CursorPage(
                items=page,
                next_cursor=next_cursor,
                status=cursor.status,
                partial=cursor.partial,
                truncated=cursor.truncated,
                limitations=cursor.limitations,
            )
