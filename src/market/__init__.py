"""Market data layer — quotes, history, fundamentals, and news.

The rest of the app depends only on the abstract `MarketDataProvider` /
`NewsProvider` interfaces in `provider.py`, never on yfinance/Finnhub directly,
so data sources can be swapped (e.g. a paid BIST feed) without touching signals.
"""
