import json

import redis

from delta_ticker import DeltaTickerClient, SymbolRefresher, SymbolRegistry


class FakeRedis:
    def __init__(self, members=(), fail=False):
        self.members = {m.encode() for m in members}
        self.fail = fail

    def smembers(self, key):
        if self.fail:
            raise redis.ConnectionError("down")
        return set(self.members)


class FakeSocket:
    def __init__(self):
        self.sent = []

    def send(self, data):
        msg = json.loads(data)
        self.sent.append((msg["type"], msg["payload"]["channels"][0]["symbols"]))


def connected_client(symbols):
    client = DeltaTickerClient(symbols)
    client.ws = FakeSocket()
    client._on_open(client.ws)
    client.ws.sent.clear()
    return client


def test_registry_returns_sorted_decoded_symbols():
    registry = SymbolRegistry(FakeRedis(["P-BTC-1", "C-BTC-1"]))
    assert registry.get() == ["C-BTC-1", "P-BTC-1"]


def test_registry_returns_none_when_redis_is_down():
    assert SymbolRegistry(FakeRedis(fail=True)).get() is None


def test_refresher_skips_update_when_redis_is_down():
    received = []
    SymbolRefresher(SymbolRegistry(FakeRedis(fail=True)), received.append).refresh()
    assert received == []


def test_refresher_passes_symbols_to_callback():
    received = []
    SymbolRefresher(SymbolRegistry(FakeRedis(["A", "B"])), received.append).refresh()
    assert received == [["A", "B"]]


def test_update_sends_only_the_difference():
    client = connected_client(["A", "B"])
    client.update_symbols(["B", "C"])
    assert client.ws.sent == [("unsubscribe", ["A"]), ("subscribe", ["C"])]
    assert client.symbols == ["B", "C"]


def test_update_with_same_symbols_sends_nothing():
    client = connected_client(["A", "B"])
    client.update_symbols(["B", "A"])
    assert client.ws.sent == []


def test_removing_all_symbols_only_unsubscribes():
    client = connected_client(["A"])
    client.update_symbols([])
    assert client.ws.sent == [("unsubscribe", ["A"])]


def test_update_before_connect_is_used_on_open():
    client = DeltaTickerClient([])
    client.update_symbols(["A"])
    client.ws = FakeSocket()
    client._on_open(client.ws)
    assert client.ws.sent == [("subscribe", ["A"])]


def test_open_with_no_symbols_sends_nothing():
    client = DeltaTickerClient([])
    client.ws = FakeSocket()
    client._on_open(client.ws)
    assert client.ws.sent == []
