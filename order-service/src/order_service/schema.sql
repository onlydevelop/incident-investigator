-- Applied on startup by PositionStore.create_schema(); every statement must be idempotent.

CREATE TABLE IF NOT EXISTS positions (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    time                timestamptz NOT NULL DEFAULT now(),  -- when the position was created
    symbol              text        NOT NULL,
    side                text        NOT NULL CHECK (side IN ('buy', 'sell')),
    qty                 numeric     NOT NULL CHECK (qty > 0),
    -- pending: waiting for the first tick after `time`; open: entry_price is set.
    status              text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'open')),
    entry_price         numeric,
    entry_time          timestamptz,  -- exchange time of the tick that set entry_price
    current_price       numeric,
    current_price_time  timestamptz,  -- exchange time of the tick that set current_price
    CHECK ((status = 'pending') = (entry_price IS NULL))
);

-- Every tick updates the positions for one symbol.
CREATE INDEX IF NOT EXISTS positions_symbol_status ON positions (symbol, status);
