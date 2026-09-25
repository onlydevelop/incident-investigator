"""Domain types shared by the store, the updater and the API. HTTP shapes live in schemas.py."""
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Status(StrEnum):
    PENDING = "pending"
    OPEN = "open"


class Position(BaseModel):
    """One row of the `positions` table, as PositionStore returns it."""

    model_config = ConfigDict(frozen=True)

    id: int
    time: datetime
    symbol: str
    side: Side
    qty: Decimal
    status: Status
    entry_price: Optional[Decimal]
    entry_time: Optional[datetime]
    current_price: Optional[Decimal]
    current_price_time: Optional[datetime]
