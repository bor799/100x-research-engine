"""Fetcher contract for source adapters."""

from __future__ import annotations

from typing import Protocol

from ..models import FetchedContent, TypedError


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchedContent | TypedError:
        ...
