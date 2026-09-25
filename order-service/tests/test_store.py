from decimal import Decimal

import pytest

from order_service import Side, Status

from fakes import CALL, PUT


class TestCrud:
    def test_create_inserts_pending_position(self, store):
        p = store.create(CALL, Side.BUY, Decimal("2"))

        assert p.id == 1
        assert (p.symbol, p.side, p.qty, p.status) == (CALL, Side.BUY, Decimal("2"), Status.PENDING)
        assert p.entry_price is None and p.current_price is None
        assert p.time.utcoffset().total_seconds() == 5.5 * 3600
        assert store.get(p.id) == p

    def test_create_rolls_back_if_before_commit_raises(self, store):
        def fail(position):
            raise RuntimeError("subscribe failed")

        with pytest.raises(RuntimeError):
            store.create(CALL, Side.BUY, Decimal("1"), before_commit=fail)

        assert store.list() == []

    def test_before_commit_sees_the_new_position(self, store):
        seen = []
        p = store.create(CALL, Side.SELL, Decimal("1"), before_commit=seen.append)

        assert seen == [p]

    def test_list_filters_by_symbol_and_status(self, store, make_position, later):
        a = make_position(symbol=CALL)
        b = make_position(symbol=PUT)
        store.apply_tick(PUT, later(1), bid=Decimal("10"), ask=Decimal("11"))

        assert [p.id for p in store.list()] == [a.id, b.id]
        assert [p.id for p in store.list(symbol=PUT)] == [b.id]
        assert [p.id for p in store.list(status=Status.PENDING)] == [a.id]
        assert store.list(symbol=CALL, status=Status.OPEN) == []

    def test_delete(self, store, make_position):
        p = make_position()

        assert store.delete(p.id) is True
        assert store.get(p.id) is None
        assert store.delete(p.id) is False

    def test_get_unknown_is_none(self, store):
        assert store.get(42) is None

    def test_ping(self, store):
        assert store.ping() is True


class TestEntry:
    @pytest.mark.parametrize("side, expected", [(Side.BUY, Decimal("5177")), (Side.SELL, Decimal("5121"))],
                             ids=["buy-at-ask", "sell-at-bid"])
    def test_first_tick_sets_entry_from_trading_side(self, store, make_position, later, side, expected):
        p = make_position(side=side)

        assert store.apply_tick(CALL, later(1), bid=Decimal("5121"), ask=Decimal("5177")) == (1, 0)

        p = store.get(p.id)
        assert (p.status, p.entry_price, p.entry_time) == (Status.OPEN, expected, later(1))
        # The entry tick doesn't also set current_price.
        assert p.current_price is None

    def test_tick_from_before_the_position_was_created_is_ignored(self, store, make_position, later):
        p = make_position()

        assert store.apply_tick(CALL, later(-60), bid=Decimal("1"), ask=Decimal("2")) == (0, 0)
        assert store.get(p.id).status == Status.PENDING

    def test_missing_quote_skips_only_the_side_that_needs_it(self, store, make_position, later):
        buy = make_position(side=Side.BUY)
        sell = make_position(side=Side.SELL)

        # No ask: the buy can't enter yet, the sell can.
        assert store.apply_tick(CALL, later(1), bid=Decimal("10"), ask=None) == (1, 0)
        assert store.get(buy.id).status == Status.PENDING
        assert store.get(sell.id).entry_price == Decimal("10")

        assert store.apply_tick(CALL, later(2), bid=Decimal("12"), ask=Decimal("13")) == (1, 1)
        assert store.get(buy.id).entry_price == Decimal("13")
        assert store.get(sell.id).current_price == Decimal("13")

    def test_tick_only_touches_its_own_symbol(self, store, make_position, later):
        call = make_position(symbol=CALL)

        assert store.apply_tick(PUT, later(1), bid=Decimal("1"), ask=Decimal("2")) == (0, 0)
        assert store.get(call.id).status == Status.PENDING


class TestCurrentPrice:
    @pytest.mark.parametrize("side, expected", [(Side.BUY, Decimal("5300")), (Side.SELL, Decimal("5350"))],
                             ids=["buy-closes-at-bid", "sell-closes-at-ask"])
    def test_next_ticks_set_current_price_from_closing_side(self, store, make_position, later, side, expected):
        p = make_position(side=side)
        store.apply_tick(CALL, later(1), bid=Decimal("5121"), ask=Decimal("5177"))
        entry = store.get(p.id).entry_price

        assert store.apply_tick(CALL, later(2), bid=Decimal("5200"), ask=Decimal("5250")) == (0, 1)
        store.apply_tick(CALL, later(3), bid=Decimal("5300"), ask=Decimal("5350"))

        p = store.get(p.id)
        assert (p.current_price, p.current_price_time) == (expected, later(3))
        assert p.entry_price == entry  # fixed once set

    def test_older_tick_never_rolls_back_current_price(self, store, make_position, later):
        p = make_position()
        store.apply_tick(CALL, later(1), bid=Decimal("10"), ask=Decimal("11"))
        store.apply_tick(CALL, later(5), bid=Decimal("20"), ask=Decimal("21"))

        # Replayed or out-of-order ticks: one older than the current price, one as old as the entry.
        assert store.apply_tick(CALL, later(3), bid=Decimal("15"), ask=Decimal("16")) == (0, 0)
        assert store.apply_tick(CALL, later(1), bid=Decimal("15"), ask=Decimal("16")) == (0, 0)
        assert store.get(p.id).current_price == Decimal("20")

    def test_same_tick_twice_is_a_no_op(self, store, make_position, later):
        make_position()
        store.apply_tick(CALL, later(1), bid=Decimal("10"), ask=Decimal("11"))

        assert store.apply_tick(CALL, later(2), bid=Decimal("12"), ask=Decimal("13")) == (0, 1)
        assert store.apply_tick(CALL, later(2), bid=Decimal("12"), ask=Decimal("13")) == (0, 0)
