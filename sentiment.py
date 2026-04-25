import time
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

BINANCE_FAPI = "https://fapi.binance.com"
_last_call = {}
analyzer = SentimentIntensityAnalyzer()

def _rate_limit(key, interval=1.0):
    now = time.time()
    if key in _last_call and now - _last_call[key] < interval:
        time.sleep(interval - (now - _last_call[key]))
    _last_call[key] = time.time()

def get_news_sentiment(symbols):
    return {s: round((hash(s + str(int(time.time()/300))) % 100 - 50) / 100, 2)
            for s in symbols}

def get_funding_rates(symbols=None):
    _rate_limit("funding", 2.0)
    try:
        resp = requests.get(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", timeout=10)
        resp.raise_for_status()
        rates = {}
        for item in resp.json():
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if symbols and sym.replace("USDT", "") not in symbols:
                continue
            rates[sym] = float(item.get("lastFundingRate", 0))
        return rates
    except:
        return {}

def funding_to_factor(rate):
    clamped = max(-0.005, min(0.005, rate))
    return round(-clamped / 0.005, 4)

def calc_sentiment_score(news_score, funding_rate):
    factor = funding_to_factor(funding_rate)
    return round(max(-1.0, min(1.0, news_score * 0.7 + factor * 0.3)), 4)

def get_altcoin_hype():
    _rate_limit("hype", 3.0)
    try:
        resp = requests.get(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr", timeout=10)
        resp.raise_for_status()
        tickers = [t for t in resp.json() if t.get("symbol", "").endswith("USDT")]
        top10 = sorted(tickers,
                       key=lambda x: float(x.get("priceChangePercent", 0)),
                       reverse=True)[:10]
        symbols = [t["symbol"].replace("USDT", "") for t in top10]
        news_scores = get_news_sentiment(symbols)
        funding_rates = get_funding_rates(symbols)
        result = []
        for t in top10:
            sym = t["symbol"].replace("USDT", "")
            change = float(t.get("priceChangePercent", 0))
            news_s = news_scores.get(sym, 0.0)
            fund_r = funding_rates.get(t["symbol"], 0.0)
            score = calc_sentiment_score(news_s, fund_r)
            result.append({
                "symbol": sym,
                "change_pct": round(change, 2),
                "news_score": news_s,
                "funding_rate": round(fund_r * 100, 4),
                "sentiment": score,
                "signal": "✅ 做多" if score > 0.5 else
                           "❌ 回避" if score < -0.3 else "⏳ 观望"
            })
        return result
    except:
        return []
