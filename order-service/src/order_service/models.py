from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Optional

from pydantic import BaseModel, PlainSerializer

# Postgres numerics come back as Decimal; send them as JSON numbers rather than strings.
Number = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Status(StrEnum):
    PENDING = "pending"
    OPEN = "open"


class Position(BaseModel):
    id: int
    time: datetime
    symbol: str
    side: Side
    qty: Number
    status: Status
    entry_price: Optional[Number]
    entry_time: Optional[datetime]
    current_price: Optional[Number]
    current_price_time: Optional[datetime]
