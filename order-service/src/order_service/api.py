"""HTTP API for paper positions.

POST /positions stores a pending position in Postgres, then subscribes its symbol in
market-data-service. The position-updater (updater.py) sets entry_price from the first
Kafka tick after that, and current_price from every tick after the entry.

Handlers are plain `def`: the store and the market-data client are blocking, so FastAPI runs
each request in its threadpool.
"""
from typing import Annotated, Optional

import psycopg
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse

from order_service.config import API_HOST, API_PORT
from order_service.market_data import MarketDataError, SymbolsClient
from order_service.schemas import (
    ErrorResponse,
    HealthResponse,
    PositionCreate,
    PositionFilter,
    PositionListResponse,
    PositionResponse,
)
from order_service.store import PositionStore

NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "No such position"}}
POSTGRES_DOWN = {status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse, "description": "Postgres unavailable"}}


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
        responses=POSTGRES_DOWN,
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
    def postgres_unavailable(request: Request, exc: psycopg.OperationalError) -> JSONResponse:
        body = ErrorResponse(detail=f"Postgres unavailable: {exc}")
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body.model_dump())

    @app.get("/health", response_model=HealthResponse)
    def health(store: Store) -> HealthResponse:
        store.ping()
        return HealthResponse()

    @app.post(
        "/positions",
        response_model=PositionResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            status.HTTP_502_BAD_GATEWAY: {
                "model": ErrorResponse,
                "description": "market-data-service couldn't subscribe the symbol; nothing was stored",
            },
        },
    )
    def create_position(body: PositionCreate, store: Store, symbols: Symbols) -> PositionResponse:
        """Two steps, all or nothing: insert the position in Postgres (status `pending`), then
        subscribe its symbol in market-data-service. If the subscribe fails the insert is rolled
        back and this returns 502. Entry is set by the first tick after this call; a newly
        subscribed symbol starts streaming on market-data-service's next refresh (up to 30s)."""
        try:
            position = store.create(
                body.symbol, body.side, body.qty,
                before_commit=lambda p: symbols.subscribe(p.symbol),
            )
        except MarketDataError as e:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Couldn't subscribe {body.symbol} in market-data-service, so no position was created: {e}",
            ) from e
        return PositionResponse.model_validate(position)

    @app.get("/positions", response_model=PositionListResponse)
    def list_positions(filters: Annotated[PositionFilter, Query()], store: Store) -> PositionListResponse:
        """All positions, oldest first. Filter with `?symbol=` and/or `?status=pending|open`."""
        positions = store.list(symbol=filters.symbol, status=filters.status)
        return PositionListResponse(positions=[PositionResponse.model_validate(p) for p in positions])

    @app.get("/positions/{position_id}", response_model=PositionResponse, responses=NOT_FOUND)
    def get_position(position_id: PositionId, store: Store) -> PositionResponse:
        position = store.get(position_id)
        if position is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Position {position_id} not found")
        return PositionResponse.model_validate(position)

    @app.delete("/positions/{position_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
    def delete_position(position_id: PositionId, store: Store) -> None:
        """Removes the position. Its symbol stays subscribed in market-data-service."""
        if not store.delete(position_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Position {position_id} not found")

    return app


app = create_app()


def main():
    uvicorn.run("order_service.api:app", host=API_HOST, port=API_PORT)


if __name__ == "__main__":
    main()
