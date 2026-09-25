import redis

from delta_ticker import TickerCache, TickerPayload
from test_payload import MESSAGE


class FakeRedis:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def set(self, key, value, ex=None):
        if self.fail:
            raise redis.ConnectionError("down")
        self.calls.append((key, value, ex))


def test_store_sets_latest_payload_with_ttl():
    fake = FakeRedis()
    payload = TickerPayload.from_message(MESSAGE)

    TickerCache(fake, ttl_seconds=10).store(payload)

    assert fake.calls == [("ticker:latest:C-BTC-79500-250926", payload.to_json(), 10)]


def test_store_swallows_redis_errors(capsys):
    TickerCache(FakeRedis(fail=True)).store(TickerPayload.from_message(MESSAGE))

    assert "Failed to cache C-BTC-79500-250926" in capsys.readouterr().out
