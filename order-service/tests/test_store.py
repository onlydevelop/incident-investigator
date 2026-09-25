from decimal import Decimal

import pytest

from order_service import Side, Status

CALL = "C-BTC-80000-091026"
PUT = "P-BTC-80000-091026"


def test_create_inserts_pending_position(store):
    p = store.create(CALL, Side.BUY, Decimal("2"))

    assert p.id == 1
    assert (p.symbol, p.side, p.qty, p.status) == (CALL, Side.BUY, Decimal("2"), Status.PENDING)
    assert p.entry_price is None and p.current_price is None
    assert p.time is not None
    assert store.get(p.id) == p


def test_create_rolls_back_if_before_commit_raises(store):
    def fail(position):
        raise RuntimeError("subscribe failed")

    with pytest.raises(RuntimeError):
        store.create(CALL, Side.BUY, Decimal("1"), before_commit=fail)

    assert store.list() == []


def test_before_commit_sees_the_new_position(store):
    seen = []
    p = store.create(CALL, Side.SELL, Decimal("1"), before_commit=seen.append)

    assert seen == [p]


def test_list_filters_by_symbol_and_status(store, later):
    a = store.create(CALL, Side.BUY, Decimal("1"))
    b = store.create(PUT, Side.BUY, Decimal("1"))
    store.apply_tick(PUT, later(1), bid=Decimal("10"), ask=Decimal("11"))

    assert [p.id for p in store.list()] == [a.id, b.id]
    assert [p.id for p in store.list(symbol=PUT)] == [b.id]
    assert [p.id for p in store.list(status=Status.PENDING)] == [a.id]
    assert store.list(symbol=CALL, status=Status.OPEN) == []


def test_delete(store):
    p = store.create(CALL, Side.BUY, Decimal("1"))

    assert store.delete(p.id) is True
    assert store.get(p.id) is None
    assert store.delete(p.id) is False


def test_first_tick_opens_buy_at_ask_and_sell_at_bid(store, later):
    buy = store.create(CALL, Side.BUY, Decimal("1"))
    sell = store.create(CALL, Side.SELL, Decimal("1"))

    assert store.apply_tick(CALL, later(1), bid=Decimal("5121"), ask=Decimal("5177")) == (2, 0)

    buy, sell = store.get(buy.id), store.get(sell.id)
    assert (buy.status, buy.entry_price, buy.entry_time) == (Status.OPEN, Decimal("5177"), later(1))
    assert (sell.status, sell.entry_price) == (Status.OPEN, Decimal("5121"))
    # The entry tick doesn't also set current_price.
    assert buy.current_price is None and sell.current_price is None


def test_next_ticks_set_current_price_at_closing_side(store, later):
    buy = store.create(CALL, Side.BUY, Decimal("1"))
    sell = store.create(CALL, Side.SELL, Decimal("1"))
    store.apply_tick(CALL, later(1), bid=Decimal("5121"), ask=Decimal("5177"))

    assert store.apply_tick(CALL, later(2), bid=Decimal("5200"), ask=Decimal("5250")) == (0, 2)
    store.apply_tick(CALL, later(3), bid=Decimal("5300"), ask=Decimal("5350"))

    buy, sell = store.get(buy.id), store.get(sell.id)
    assert (buy.current_price, buy.current_price_time) == (Decimal("5300"), later(3))
    assert (sell.current_price, sell.current_price_time) == (Decimal("5350"), later(3))
    # Entry is fixed once set.
    assert (buy.entry_price, sell.entry_price) == (Decimal("5177"), Decimal("5121"))


def test_tick_from_before_the_position_was_created_is_ignored(store, later):
    p = store.create(CALL, Side.BUY, Decimal("1"))

    assert store.apply_tick(CALL, later(-60), bid=Decimal("1"), ask=Decimal("2")) == (0, 0)
    assert store.get(p.id).status == Status.PENDING


def test_older_tick_never_rolls_back_current_price(store, later):
    p = store.create(CALL, Side.BUY, Decimal("1"))
    store.apply_tick(CALL, later(1), bid=Decimal("10"), ask=Decimal("11"))
    store.apply_tick(CALL, later(5), bid=Decimal("20"), ask=Decimal("21"))

    # Replayed or out-of-order ticks: one older than the current price, one as old as the entry.
    assert store.apply_tick(CALL, later(3), bid=Decimal("15"), ask=Decimal("16")) == (0, 0)
    assert store.apply_tick(CALL, later(1), bid=Decimal("15"), ask=Decimal("16")) == (0, 0)
    assert store.get(p.id).current_price == Decimal("20")


def test_missing_quote_skips_only_the_side_that_needs_it(store, later):
    buy = store.create(CALL, Side.BUY, Decimal("1"))
    sell = store.create(CALL, Side.SELL, Decimal("1"))

    # No ask: the buy can't enter yet, the sell can.
    assert store.apply_tick(CALL, later(1), bid=Decimal("10"), ask=None) == (1, 0)
    assert store.get(buy.id).status == Status.PENDING
    assert store.get(sell.id).entry_price == Decimal("10")

    assert store.apply_tick(CALL, later(2), bid=Decimal("12"), ask=Decimal("13")) == (1, 1)
    assert store.get(buy.id).entry_price == Decimal("13")
    assert store.get(sell.id).current_price == Decimal("13")


def test_tick_only_touches_its_own_symbol(store, later):
    call = store.create(CALL, Side.BUY, Decimal("1"))

    assert store.apply_tick(PUT, later(1), bid=Decimal("1"), ask=Decimal("2")) == (0, 0)
    assert store.get(call.id).status == Status.PENDING


def test_ping(store):
    assert store.ping() is True
