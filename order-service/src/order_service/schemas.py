"""Request and response models for the positions API (Pydantic v2).

Kept separate from the domain Position in models.py so the HTTP contract can change without
touching the store, and the store can grow columns without leaking them into responses.
"""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, computed_field

from order_service.config import OPTION_SYMBOL_PATTERN
from order_service.models import Side, Status

OptionSymbol = Annotated[
    str,
    Field(
        pattern=OPTION_SYMBOL_PATTERN,
        description="Option symbol, <C|P>-<underlying>-<strike>-<DDMMYY>",
        examples=["C-BTC-80000-091026"],
    ),
]

# Postgres numerics come back as Decimal; send them as JSON numbers rather than strings.
Number = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


# --- Requests ---

class PositionCreate(BaseModel):
    """Body of POST /positions."""

    # A misspelt field (e.g. "quantity") is a 422, not silently ignored.
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"symbol": "C-BTC-80000-091026", "side": "buy", "qty": 1}]},
    )

    symbol: OptionSymbol
    side: Side = Field(description="buy enters at the best ask, sell at the best bid")
    qty: Decimal = Field(gt=0, description="Number of contracts; decimals are allowed")


class PositionFilter(BaseModel):
    """Query parameters of GET /positions."""

    model_config = ConfigDict(extra="forbid")

    symbol: Optional[OptionSymbol] = None
    status: Optional[Status] = None


# --- Responses ---

class PositionResponse(BaseModel):
    """A position as the API returns it. Built from a domain Position with model_validate()."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    time: datetime = Field(description="When the position was created")
    symbol: str
    side: Side
    qty: Number
    status: Status = Field(description="pending until the first tick after `time`, then open")
    entry_price: Optional[Number] = Field(description="Ask (buy) or bid (sell) from the entry tick")
    entry_time: Optional[datetime] = Field(description="Exchange time of the entry tick")
    current_price: Optional[Number] = Field(description="Bid (buy) or ask (sell) from the latest tick after entry")
    current_price_time: Optional[datetime] = Field(description="Exchange time of that tick")


class PositionListResponse(BaseModel):
    positions: list[PositionResponse]

    @computed_field
    @property
    def count(self) -> int:
        return len(self.positions)


class ErrorResponse(BaseModel):
    """Shape of every non-422 error, as FastAPI's HTTPException renders it."""

    detail: str


class HealthResponse(BaseModel):
    status: str = "ok"
