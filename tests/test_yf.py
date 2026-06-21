import yfinance as yf
print("Fetching NVDA...")
try:
    info = yf.Ticker("NVDA").info
    print("Success! Price:", info.get("currentPrice"))
except Exception as e:
    print("Failed:", e)
