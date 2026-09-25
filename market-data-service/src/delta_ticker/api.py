"""HTTP API for managing the option symbols the ticker subscribes to.

Writes go to the SymbolRegistry Redis set; the ticker picks changes up on its next refresh.
"""
from typing import Annotated, Optional

import redis
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from delta_ticker.config import (
    API_HOST,
    API_PORT,
    OPTION_SYMBOL_PATTERN,
    SYMBOL_REFRESH_SECONDS,
)
from delta_ticker.symbols import SymbolRegistry

OptionSymbol = Annotated[
    str,
    Field(pattern=OPTION_SYMBOL_PATTERN, examples=["C-BTC-80000-091026"]),
]


class SymbolsIn(BaseModel):
    symbols: list[OptionSymbol] = Field(min_length=1)

    @field_validator("symbols")
    @classmethod
    def dedupe(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class SymbolsOut(BaseModel):
    symbols: list[str]
    count: int
    note: str = f"The ticker applies changes within {SYMBOL_REFRESH_SECONDS}s."

    @classmethod
    def of(cls, symbols: list[str]) -> "SymbolsOut":
        return cls(symbols=symbols, count=len(symbols))


class SymbolOut(BaseModel):
    symbol: str


def create_app(registry: Optional[SymbolRegistry] = None) -> FastAPI:
    app = FastAPI(
        title="Delta ticker symbols",
        description="CRUD for the option symbols the delta-ticker subscribes to (Redis set, no expiry).",
    )
    app.state.registry = registry

    def get_registry(request: Request) -> SymbolRegistry:
        if request.app.state.registry is None:
            request.app.state.registry = SymbolRegistry.from_url()
        return request.app.state.registry

    Registry = Annotated[SymbolRegistry, Depends(get_registry)]
    SymbolPath = Annotated[str, Path(pattern=OPTION_SYMBOL_PATTERN, examples=["C-BTC-80000-091026"])]

    @app.exception_handler(redis.RedisError)
    async def redis_unavailable(request: Request, exc: redis.RedisError):
        return JSONResponse(status_code=503, content={"detail": f"Redis unavailable: {exc}"})

    @app.get("/health")
    def health(registry: Registry):
        registry.ping()
        return {"status": "ok"}

    @app.get("/symbols", response_model=SymbolsOut)
    def list_symbols(registry: Registry):
        """List all subscribed symbols."""
        return SymbolsOut.of(registry.members())

    @app.get("/symbols/{symbol}", response_model=SymbolOut)
    def get_symbol(symbol: SymbolPath, registry: Registry):
        """404 if the symbol isn't subscribed."""
        if not registry.contains(symbol):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{symbol} is not subscribed")
        return SymbolOut(symbol=symbol)

    @app.post("/symbols", response_model=SymbolsOut, status_code=status.HTTP_201_CREATED)
    def add_symbols(body: SymbolsIn, registry: Registry):
        """Add symbols to the existing list. Symbols already present are ignored."""
        registry.add(body.symbols)
        return SymbolsOut.of(registry.members())

    @app.put("/symbols", response_model=SymbolsOut)
    def replace_symbols(body: SymbolsIn, registry: Registry):
        """Replace the whole list atomically."""
        registry.replace(body.symbols)
        return SymbolsOut.of(registry.members())

    @app.put("/symbols/{symbol}", response_model=SymbolOut)
    def put_symbol(symbol: SymbolPath, registry: Registry, response: Response):
        """Add one symbol. 201 if it was new, 200 if it was already there."""
        if registry.add([symbol]):
            response.status_code = status.HTTP_201_CREATED
        return SymbolOut(symbol=symbol)

    @app.delete("/symbols/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_symbol(symbol: SymbolPath, registry: Registry):
        """Remove one symbol. 404 if it wasn't subscribed."""
        if not registry.remove([symbol]):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{symbol} is not subscribed")

    @app.delete("/symbols", status_code=status.HTTP_204_NO_CONTENT)
    def clear_symbols(registry: Registry):
        """Remove all symbols. The ticker unsubscribes from everything on its next refresh."""
        registry.clear()

    return app


app = create_app()


def main():
    uvicorn.run("delta_ticker.api:app", host=API_HOST, port=API_PORT)


if __name__ == "__main__":
    main()
