"""Pagination helper utilities."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil


@dataclass
class Pagination:
    """Simple pagination container for list and query results."""

    items: list
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        if self.total == 0:
            return 0
        return ceil(self.total / self.per_page)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def prev_num(self) -> int | None:
        return self.page - 1 if self.has_prev else None

    @property
    def next_num(self) -> int | None:
        return self.page + 1 if self.has_next else None

    def iter_pages(
        self,
        left_edge: int = 2,
        left_current: int = 2,
        right_current: int = 3,
        right_edge: int = 2,
    ):
        """Yield page numbers with gaps represented as None."""

        last = 0
        for num in range(1, self.pages + 1):
            if (
                num <= left_edge
                or (self.page - left_current - 1 < num < self.page + right_current)
                or num > self.pages - right_edge
            ):
                if last + 1 != num:
                    yield None
                yield num
                last = num



def get_pagination_args(request):
    """Extract and sanitize page and per_page from request args."""

    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1

    try:
        per_page = int(request.args.get("per_page", 25))
    except (TypeError, ValueError):
        per_page = 25

    page = max(page, 1)
    per_page = max(1, min(per_page, 100))

    return page, per_page
