from delta_ticker.client import DeltaTickerClient

# Replace with your list of option symbols, or use an option-chain shorthand like "BTC-150426"
OPTION_SYMBOLS = [
    "C-BTC-79500-250926",
    "P-BTC-79500-250926",
]


def main():
    DeltaTickerClient(OPTION_SYMBOLS).run()


if __name__ == "__main__":
    main()
