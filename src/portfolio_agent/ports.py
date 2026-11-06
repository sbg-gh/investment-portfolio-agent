"""Future adapters implement these contracts; no runtime vendor dependencies."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

@dataclass(frozen=True)
class Observation:
    security_id: str
    value: Decimal
    observed_at: datetime  # timezone aware UTC
    source: str
    basis: str  # price or nav; mutual-fund NAV is not an intraday quote
    currency: str = "USD"
    fetched_at: datetime | None = None  # required by observation ingestion

class MarketData(Protocol):
    def latest(self, security_id: str) -> Observation: ...

class LocalModel(Protocol):
    def generate(self, prompt: str, *, max_tokens: int) -> str: ...

class Notifier(Protocol):
    def notify(self, user_id: str, message: str, *, deduplication_key: str) -> None: ...
