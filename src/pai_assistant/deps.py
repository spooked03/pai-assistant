from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .db import Store


@dataclass
class Deps:
    """Per-run dependencies, available to every tool as ``ctx.deps``."""

    settings: Settings
    store: Store
