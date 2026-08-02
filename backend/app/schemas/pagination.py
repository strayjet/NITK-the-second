"""Generic pagination request/response helpers, shared by every `/dashboard/*` list endpoint."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageParams(BaseModel):
    """Common pagination query params."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    """A single page of results plus enough metadata for the frontend to paginate."""

    items: list[T]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool

    @classmethod
    def build(cls, items: list[T], page: int, page_size: int, total_items: int) -> "Page[T]":
        total_pages = (total_items + page_size - 1) // page_size if page_size else 0
        return cls(
            items=items,
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=max(total_pages, 0),
            has_next=page * page_size < total_items,
            has_previous=page > 1,
        )
