"""HTTP API for paper positions.

POST /positions stores a pending position in Postgres, then subscribes its symbol in
market-data-service. The position-updater (updater.py) sets entry_price from the first
Kafka tick after that, and current_price from every tick after the entry.
"""
from decimal import Decimal
from typing import Annotated, Optional

import psycopg
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from order_service.config import API_HOST, API_PORT, OPTION_SYMBOL_PATTERN
from order_service.market_data import MarketDataError, SymbolsClient
from order_service.models import Position, Side, Status
from order_service.store import PositionStore

OptionSymbol = Annotated[str, Field(pattern=OPTION_SYMBOL_PATTERN, examples=["C-BTC-80000-091026"])]


class PositionIn(BaseModel):
    symbol: OptionSymbol
    side: Side
    qty: Decimal = Field(gt=0, examples=[1])


class PositionsOut(BaseModel):
    positions: list[Position]
    count: int


def create_app(
    store: Optional[PositionStore] = None,
    symbols: Optional[SymbolsClient] = None,
) -> FastAPI:
    app = FastAPI(
        title="Order service",
        description=(
            "Paper positions. A new position is pending until the first ticker update after it was "
            "created: buys enter at the best ask, sells at the best bid. Every later update sets "
            "current_price to the price it would close at (bid for a buy, ask for a sell)."
        ),
    )
    app.state.store = store
    app.state.symbols = symbols

    def get_store(request: Request) -> PositionStore:
        if request.app.state.store is None:
            request.app.state.store = PositionStore.from_url()
        return request.app.state.store

    def get_symbols(request: Request) -> SymbolsClient:
        if request.app.state.symbols is None:
            request.app.state.symbols = SymbolsClient()
        return request.app.state.symbols

    Store = Annotated[PositionStore, Depends(get_store)]
    Symbols = Annotated[SymbolsClient, Depends(get_symbols)]
    PositionId = Annotated[int, Path(ge=1)]

    # Also covers psycopg_pool.PoolTimeout, raised when Postgres is down.
    @app.exception_handler(psycopg.OperationalError)
    async def postgres_unavailable(request: Request, exc: psycopg.OperationalError):
        return JSONResponse(status_code=503, content={"detail": f"Postgres unavailable: {exc}"})

    @app.get("/health")
    def health(store: Store):
        store.ping()
        return {"status": "ok"}

    @app.post("/positions", response_model=Position, status_code=status.HTTP_201_CREATED)
    def create_position(body: PositionIn, store: Store, symbols: Symbols):
        """Two steps, all or nothing: insert the position in Postgres (status `pending`), then
        subscribe its symbol in market-data-service. If the subscribe fails the insert is rolled
        back and this returns 502. Entry is set by the first tick after this call; a newly
        subscribed symbol starts streaming on market-data-service's next refresh (up to 30s)."""
        try:
            return store.create(
                body.symbol, body.side, body.qty,
                before_commit=lambda position: symbols.subscribe(position.symbol),
            )
        except MarketDataError as e:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Couldn't subscribe {body.symbol} in market-data-service, so no position was created: {e}",
            )

    @app.get("/positions", response_model=PositionsOut)
    def list_positions(
        store: Store,
        symbol: Annotated[Optional[str], Query(pattern=OPTION_SYMBOL_PATTERN)] = None,
        position_status: Annotated[Optional[Status], Query(alias="status")] = None,
    ):
        """All positions, oldest first. Filter with `?symbol=` and/or `?status=pending|open`."""
        positions = store.list(symbol=symbol, status=position_status)
        return PositionsOut(positions=positions, count=len(positions))

    @app.get("/positions/{position_id}", response_model=Position)
    def get_position(position_id: PositionId, store: Store):
        """404 if there's no such position."""
        position = store.get(position_id)
        if position is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Position {position_id} not found")
        return position

    @app.delete("/positions/{position_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_position(position_id: PositionId, store: Store):
        """Removes the position. Its symbol stays subscribed in market-data-service."""
        if not store.delete(position_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Position {position_id} not found")

    return app


app = create_app()


def main():
    uvicorn.run("order_service.api:app", host=API_HOST, port=API_PORT)


if __name__ == "__main__":
    main()
