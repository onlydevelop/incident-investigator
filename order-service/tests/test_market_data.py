import httpx
import pytest

from order_service.market_data import MarketDataError, SymbolsClient

CALL = "C-BTC-80000-091026"


def client_returning(handler):
    return SymbolsClient(client=httpx.Client(base_url="http://md", transport=httpx.MockTransport(handler)))


def test_subscribe_puts_the_symbol():
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(201, json={"symbol": CALL})

    assert client_returning(handler).subscribe(CALL) is True
    assert requests == [("PUT", f"/symbols/{CALL}")]


def test_already_subscribed_is_fine():
    assert client_returning(lambda r: httpx.Response(200, json={"symbol": CALL})).subscribe(CALL) is False


def test_error_status_raises():
    with pytest.raises(MarketDataError, match="returned 503"):
        client_returning(lambda r: httpx.Response(503, text="Redis unavailable")).subscribe(CALL)


def test_unreachable_raises():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(MarketDataError, match="unreachable"):
        client_returning(handler).subscribe(CALL)
