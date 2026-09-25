import websocket
import json

# Public websocket endpoint (no authentication required for ticker channel)
WEBSOCKET_URL = "wss://socket.india.delta.exchange"

# Replace with your list of option symbols, or use an option-chain shorthand like "BTC-150426"
OPTION_SYMBOLS = [
    "C-BTC-79500-250926",
    "P-BTC-79500-250926",
]

def on_open(ws):
    print("Socket opened")
    subscribe(ws, "v2/ticker", OPTION_SYMBOLS)

def subscribe(ws, channel, symbols):
    payload = {
        "type": "subscribe",
        "payload": {
            "channels": [
                {
                    "name": channel,
                    "symbols": symbols
                }
            ]
        }
    }
    ws.send(json.dumps(payload))

def on_message(ws, message):
    try:
        data = json.loads(message)
        if data.get("type") == "v2/ticker":
            quotes = data.get("quotes") or {}
            greeks = data.get("greeks") or {}

            print(f"Symbol: {data.get('symbol')}")
            print(f"  Mark Price: {data.get('mark_price')}  Spot: {data.get('spot_price')}")
            print(f"  Bid Price: {quotes.get('best_bid')}, Bid Size: {quotes.get('bid_size')}")
            print(f"  Ask Price: {quotes.get('best_ask')}, Ask Size: {quotes.get('ask_size')}")
            if greeks:
                print(f"  Greeks -> Delta: {greeks.get('delta')}, Gamma: {greeks.get('gamma')}, "
                      f"Rho: {greeks.get('rho')}, Theta: {greeks.get('theta')}, Vega: {greeks.get('vega')}")
            print(f"  IV -> Ask IV: {quotes.get('ask_iv')}, Bid IV: {quotes.get('bid_iv')}, "
                  f"Mark IV: {quotes.get('mark_iv')}")
            print("-" * 40)
        else:
            print(json.dumps(data, indent=2))
    except json.JSONDecodeError as e:
        print(f"Failed to decode message: {e}")

def on_error(ws, error):
    print(f"Socket Error: {error!r}")

def on_close(ws, close_status_code, close_msg):
    print(f"Socket closed with status: {close_status_code} and message: {close_msg}")

if __name__ == "__main__":
    ws = websocket.WebSocketApp(
        WEBSOCKET_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()  # runs indefinitely, reconnect logic can be added if needed
