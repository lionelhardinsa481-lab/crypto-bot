"""
山寨币市场情绪监控模块
依赖: vaderSentiment, requests
"""

import time
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ============================================================
# 配置
# ============================================================
CRYPTOPANIC_TOKEN = ""        # 填入 https://cryptopanic.com/api 的免费 token
BINANCE_FAPI      = "https://fapi.binance.com"
CRYPTOPANIC_URL   = "https://cryptopanic.com/api/v1/posts/"

# 限流保护：记录最近一次请求时间
_last_call = {}

def _rate_limit(key: str, min_interval: float = 1.0):
    """简单限流，同一 key 两次调用间隔不低于 min_interval 秒"""
    now = time.time()
    if key in _last_call and now - _last_call[key] < min_interval:
        time.sleep(min_interval - (now - _last_call[key]))
    _last_call[key] = time.time()


# ============================================================
# 1. 新闻情绪打分
# ============================================================
analyzer = SentimentIntensityAnalyzer()

def _score_text(text: str) -> float:
    """VADER 情绪打分，返回 -1 ~ 1"""
    return analyzer.polarity_scores(text)["compound"]

def get_news_sentiment(symbols: list) -> dict:
    """
    从 CryptoPanic 获取指定币种新闻并打分
    返回: {"SOL": 0.32, "LINK": -0.15, ...}
    """
    result = {}
    if not CRYPTOPANIC_TOKEN:
        # 没有 token 时返回模拟数据，不影响系统运行
        return {s: round((hash(s + str(int(time.time()/300))) % 100 - 50) / 100, 2)
                for s in symbols}

    for sym in symbols:
        _rate_limit(f"cp_{sym}", 1.5)
        try:
            resp = requests.get(
                CRYPTOPANIC_URL,
                params={"auth_token": CRYPTOPANIC_TOKEN,
                        "currencies": sym,
                        "public": "true",
                        "kind": "news"},
                timeout=8
            )
            resp.raise_for_status()
            posts = resp.json().get("results", [])
            if not posts:
                result[sym] = 0.0
                continue
            scores = [_score_text(p.get("title", "")) for p in posts[:10]]
            result[sym] = round(sum(scores) / len(scores), 4)
        except Exception as e:
            result[sym] = 0.0
    return result


# ============================================================
# 2. 资金费率监控
# ============================================================
def get_funding_rates(symbols: list = None) -> dict:
    """
    获取币安 U 本位永续合约资金费率
    返回: {"BTCUSDT": 0.0001, "SOLUSDT": -0.0003, ...}
    """
    _rate_limit("funding", 2.0)
    try:
        resp = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/premiumIndex",
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        rates = {}
        for item in data:
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if symbols:
                base = sym.replace("USDT", "")
                if base not in symbols:
                    continue
            rates[sym] = float(item.get("lastFundingRate", 0))
        return rates
    except Exception:
        return {}

def funding_to_factor(rate: float) -> float:
    """
    资金费率 → 情绪调整因子
    费率为正（多头付费）→ 市场偏多头拥挤 → 轻微利空
    费率为负（空头付费）→ 市场悲观 → 轻微利多（反转机会）
    映射到 -1 ~ 1
    """
    clamped = max(-0.005, min(0.005, rate))
    return round(-clamped / 0.005, 4)


# ============================================================
# 3. 综合情绪分
# ============================================================
def calc_sentiment_score(news_score: float, funding_rate: float) -> float:
    """
    Sentiment_Score = 新闻打分 × 0.7 + 资金费率调整因子 × 0.3
    返回 -1 ~ 1
    """
    factor = funding_to_factor(funding_rate)
    score  = news_score * 0.7 + factor * 0.3
    return round(max(-1.0, min(1.0, score)), 4)


# ============================================================
# 4. get_altcoin_hype —— 涨幅榜前10情绪评分
# ============================================================
def get_altcoin_hype() -> list:
    """
    获取币安涨幅榜前10币种 + 综合情绪评分
    返回列表：[{"symbol":"SOL","change":5.2,"news":0.3,"funding":-0.0001,"score":0.61}, ...]
    """
    _rate_limit("hype", 3.0)
    try:
        resp = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/ticker/24hr",
            timeout=10
        )
        resp.raise_for_status()
        tickers = resp.json()

        # 筛选 USDT 永续，按涨幅排序取前10
        usdt = [t for t in tickers if t.get("symbol", "").endswith("USDT")]
        top10 = sorted(usdt,
                       key=lambda x: float(x.get("priceChangePercent", 0)),
                       reverse=True)[:10]

        symbols = [t["symbol"].replace("USDT", "") for t in top10]
        news_scores   = get_news_sentiment(symbols)
        funding_rates = get_funding_rates(symbols)

        result = []
        for t in top10:
            sym      = t["symbol"].replace("USDT", "")
            change   = float(t.get("priceChangePercent", 0))
            news_s   = news_scores.get(sym, 0.0)
            fund_r   = funding_rates.get(t["symbol"], 0.0)
            score    = calc_sentiment_score(news_s, fund_r)
            result.append({
                "symbol":       sym,
                "change_pct":   round(change, 2),
                "news_score":   news_s,
                "funding_rate": round(fund_r * 100, 4),
                "sentiment":    score,
                "signal":       "✅ 可做多" if score > 0.5 else
                                "❌ 回避"   if score < -0.3 else
                                "⏳ 观望"
            })
        return result

    except Exception:
        return []


# ============================================================
# 5. 单币种情绪门控（供交易逻辑调用）
# ============================================================
def sentiment_gate(symbol: str,
                   news_cache: dict = None,
                   funding_cache: dict = None) -> dict:
    """
    返回该币种是否通过情绪门控
    {
      "pass": True/False,   # True = 允许开多
      "score": 0.62,
      "news": 0.4,
      "funding": -0.0001
    }
    """
    sym_base = symbol.replace("USDT", "").replace("/USDT:USDT", "")

    news_s = (news_cache or {}).get(sym_base)
    if news_s is None:
        news_s = get_news_sentiment([sym_base]).get(sym_base, 0.0)

    fund_r = (funding_cache or {}).get(symbol + "USDT",
              (funding_cache or {}).get(symbol, 0.0))
    if not fund_r:
        fund_r = get_funding_rates([sym_base]).get(sym_base + "USDT", 0.0)

    score = calc_sentiment_score(news_s, fund_r)
    return {
        "pass":    score > 0.5,
        "score":   score,
        "news":    news_s,
        "funding": fund_r
    }
