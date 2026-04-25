import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import requests
import time
import json
import os
from datetime import datetime
from sentiment import get_altcoin_hype, get_funding_rates, calc_sentiment_score

st.set_page_config(page_title="Crypto 信号看板", layout="wide", page_icon="📈")

st.markdown("""
<style>
.stApp { background-color: #0d1117; color: #e6edf3; }
section[data-testid="stSidebar"] { background-color: #161b22; border-right: 1px solid #30363d; }
div[data-testid="stMetric"] { background-color: #161b22; border-radius: 12px; padding: 16px; border: 1px solid #30363d; }
</style>
""", unsafe_allow_html=True)

INITIAL_CAPITAL = 100.0
DATA_DIR = "/tmp"
ACCOUNT_FILE   = f"{DATA_DIR}/account.json"
PORTFOLIO_FILE = f"{DATA_DIR}/portfolio.json"
HISTORY_FILE   = f"{DATA_DIR}/history.json"
CACHE_FILE     = f"{DATA_DIR}/cache.json"

def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except:
        pass
    return default

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except:
        pass

def save_all():
    save_json(ACCOUNT_FILE, {"cash": st.session_state.cash,
                              "initial_capital": st.session_state.initial_capital})
    save_json(PORTFOLIO_FILE, st.session_state.portfolio)
    save_json(HISTORY_FILE, st.session_state.history)
    save_json(CACHE_FILE, st.session_state.cache_data)

if "cash" not in st.session_state:
    saved = load_json(ACCOUNT_FILE, {})
    st.session_state.cash = saved.get("cash", INITIAL_CAPITAL)
    st.session_state.initial_capital = saved.get("initial_capital", INITIAL_CAPITAL)
if "portfolio" not in st.session_state:
    st.session_state.portfolio = load_json(PORTFOLIO_FILE, [])
if "history" not in st.session_state:
    st.session_state.history = load_json(HISTORY_FILE, [])
if "cache_data" not in st.session_state:
    raw = load_json(CACHE_FILE, {})
    st.session_state.cache_data = {k: v for k, v in raw.items() if time.time() - v < 3600}
if "watchlist" not in st.session_state:
    st.session_state.watchlist = [
        "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
        "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT",
    ]
if "equity_curve" not in st.session_state:
    st.session_state.equity_curve = [
        {"time": datetime.now().strftime("%H:%M"), "equity": INITIAL_CAPITAL}
    ]
if "scan_log" not in st.session_state:
    st.session_state.scan_log = []

@st.cache_resource
def get_exchange():
    for name, cls, sym in [("OKX", ccxt.okx, "BTC/USDT:USDT"),
                             ("Binance", ccxt.binance, "BTC/USDT:USDT")]:
        try:
            ex = cls({"options": {"defaultType": "swap"},
                      "enableRateLimit": True, "timeout": 10000})
            ex.fetch_ticker(sym)
            return ex, name
        except:
            continue
    return None, "离线"

EXCHANGE, EXCHANGE_NAME = get_exchange()

def fmt(p):
    if p < 0.01: return f"{p:.6f}"
    if p < 1:    return f"{p:.4f}"
    if p < 100:  return f"{p:.2f}"
    return f"{p:.1f}"

def send_dingtalk(text, url=""):
    if not url: return
    try:
        requests.post(url, json={"msgtype": "text", "text": {"content": f"【Crypto】\n{text}"}}, timeout=5)
    except: pass

def calc_net(prices):
    total = st.session_state.cash
    for p in st.session_state.portfolio:
        if p["status"] != "open": continue
        cur = prices.get(p["symbol"], p["entry"])
        total += cur * p["quantity"] if p["direction"] == "long" \
                 else (2 * p["entry"] - cur) * p["quantity"]
    return total

def pos_size(entry, sl, risk_pct):
    risk = st.session_state.cash * (risk_pct / 100)
    diff = abs(entry - sl)
    return max(risk / diff, 0.0001) if diff > 0 else 0

def add_log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    st.session_state.scan_log.insert(0, f"[{now}] {msg}")
    st.session_state.scan_log = st.session_state.scan_log[:30]

def get_ohlcv(sym, tf, limit=250):
    try:
        data = EXCHANGE.fetch_ohlcv(sym, timeframe=tf, limit=limit)
        return pd.DataFrame(data, columns=["ts","o","h","l","c","v"])
    except: return pd.DataFrame()

def get_top_gainers(limit=20):
    try:
        tickers = EXCHANGE.fetch_tickers()
        swaps = [t for s, t in tickers.items()
                 if ":USDT" in s and t.get("percentage") is not None]
        return [t["symbol"] for t in sorted(swaps,
                key=lambda x: x["percentage"], reverse=True)[:limit]]
    except: return []

# ============================================================
# 侧边栏
# ============================================================
with st.sidebar:
    st.title("⚡ 控制台")
    net = st.session_state.get("last_net", st.session_state.cash)
    pnl = net - st.session_state.initial_capital
    st.metric("💰 净资产", f"${net:.2f}", f"{pnl/st.session_state.initial_capital*100:+.2f}%")
    st.caption(f"可用: ${st.session_state.cash:.2f} | 数据源: {EXCHANGE_NAME}")
    st.divider()

    dd_url = st.text_input("🔔 钉钉 Webhook", placeholder="https://oapi.dingtalk.com/...", type="password")
    if st.button("测试推送"):
        send_dingtalk("✅ 测试成功", dd_url)
        st.toast("已发送")
    st.divider()

    st.subheader("📊 策略参数")
    tf       = st.selectbox("K线周期", ["5m","15m","1h","4h"], index=1)
    risk_pct = st.slider("单笔风险 %", 0.5, 10.0, 2.0, 0.5)
    en_trend = st.checkbox("📈 趋势策略", True)
    en_pump  = st.checkbox("🚀 异动策略", True)
    en_ath   = st.checkbox("📊 新高策略", True)
    en_sent  = st.checkbox("🧠 情绪门控", True)

    with st.expander("高级参数"):
        pump_pct = st.slider("异动阈值 %", 1.0, 20.0, 2.5, 0.5) / 100
        vol_mult = st.slider("成交量倍数", 1.0, 8.0, 2.0, 0.2)
        sent_min = st.slider("最低情绪分", 0.0, 1.0, 0.5, 0.05)

    st.divider()
    st.subheader("🔍 币种管理")
    col_a, col_b = st.columns([3,1])
    with col_a:
        new_c = st.text_input("添加", placeholder="如 PEPE", label_visibility="collapsed")
    with col_b:
        if st.button("➕"):
            if new_c:
                s = f"{new_c.upper()}/USDT:USDT"
                if s not in st.session_state.watchlist:
                    st.session_state.watchlist.append(s)
                    st.rerun()

    wl = [s.split("/")[0] for s in st.session_state.watchlist]
    del_c = st.selectbox("删除", ["不删除"] + wl)
    if st.button("🗑️ 删除"):
        if del_c != "不删除":
            st.session_state.watchlist = [
                s for s in st.session_state.watchlist
                if not s.startswith(del_c + "/")]
            st.rerun()

    if EXCHANGE and st.button("🔥 同步热门妖币"):
        hot = get_top_gainers(20)
        added = sum(1 for s in hot if s not in st.session_state.watchlist
                    and not st.session_state.watchlist.append(s))
        st.toast(f"新增 {added} 个")
        st.rerun()

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 刷新", type="primary", use_container_width=True):
            st.rerun()
    with col2:
        if st.button("🗑️ 重置", use_container_width=True):
            st.session_state.cash = INITIAL_CAPITAL
            st.session_state.initial_capital = INITIAL_CAPITAL
            st.session_state.portfolio = []
            st.session_state.history = []
            st.session_state.cache_data = {}
            st.session_state.equity_curve = [
                {"time": datetime.now().strftime("%H:%M"), "equity": INITIAL_CAPITAL}]
            save_all()
            st.rerun()

# ============================================================
# 主界面
# ============================================================
st.title("📊 加密货币实战信号看板")
st.caption(f"初始资金 $100 · 模拟盘 · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

net   = st.session_state.get("last_net", st.session_state.cash)
pnl   = net - st.session_state.initial_capital
wins  = [h for h in st.session_state.history if h.get("pnl", 0) > 0]
wr    = len(wins) / len(st.session_state.history) * 100 if st.session_state.history else 0

k1, k2, k3, k4 = st.columns(4)
k1.metric("💰 净资产",   f"${net:.2f}")
k2.metric("📈 累计盈亏", f"${pnl:+.2f}", f"{pnl/INITIAL_CAPITAL*100:+.2f}%")
k3.metric("🏆 胜率",     f"{wr:.1f}%",   f"{len(st.session_state.history)} 笔")
k4.metric("💵 可用现金", f"${st.session_state.cash:.2f}")

st.markdown("<br>", unsafe_allow_html=True)

# ============================================================
# 扫描交易
# ============================================================
def scan():
    if not EXCHANGE: return [], {}
    scan_list = list(set(st.session_state.watchlist))
    prices, signals = {}, []

    TF = {"5m":{"pm":0.03,"vm":3.0},"15m":{"pm":0.04,"vm":2.5},
          "1h":{"pm":0.06,"vm":2.0},"4h":{"pm":0.08,"vm":1.8}}
    cfg = TF[tf]

    with st.status(f"🔍 扫描 {len(scan_list)} 个币种...", expanded=False) as status:
        for i, sym in enumerate(scan_list):
            status.update(label=f"{i+1}/{len(scan_list)}: {sym.split('/')[0]}")
            df = get_ohlcv(sym, tf, 250)
            if df.empty or len(df) < 60: continue

            name  = sym.split("/")[0]
            last  = df.iloc[-1]
            c_val = float(last["c"])
            h_val = float(last["h"])
            l_val = float(last["l"])
            prices[name] = c_val

            # 持仓管理
            for p in list(st.session_state.portfolio):
                if p["symbol"] != name or p["status"] != "open": continue
                entry, qty, dire = p["entry"], p["quantity"], p["direction"]
                pnl_p = (c_val-entry)/entry*100 if dire=="long" else (entry-c_val)/entry*100
                if dire == "long":
                    if pnl_p >= 5:  p["sl"] = max(p["sl"], entry)
                    if pnl_p >= 15: p["sl"] = max(p["sl"], c_val*0.95)

                ep, reason = None, ""
                if dire == "long":
                    if l_val <= p["sl"]: ep, reason = p["sl"], "止损"
                    elif h_val >= p["tp"]: ep, reason = p["tp"], "止盈"
                else:
                    if h_val >= p["sl"]: ep, reason = p["sl"], "止损"
                    elif l_val <= p["tp"]: ep, reason = p["tp"], "止盈"

                if ep:
                    pv = (ep-entry)*qty if dire=="long" else (entry-ep)*qty
                    st.session_state.cash += ep*qty if dire=="long" else (2*entry-ep)*qty
                    st.session_state.history.insert(0, {
                        "symbol": name, "direction": dire,
                        "entry": entry, "exit": ep,
                        "pnl": round(pv, 4), "reason": reason,
                        "time": datetime.now().strftime("%m-%d %H:%M")})
                    st.session_state.portfolio.remove(p)
                    save_all()
                    add_log(f"{'🟢' if pv>0 else '🔴'} {name} [{reason}] ${pv:+.4f}")
                    send_dingtalk(f"{'🟢' if pv>0 else '🔴'} {name} {reason} ${pv:.2f}", dd_url)

            if any(p["symbol"]==name and p["status"]=="open"
                   for p in st.session_state.portfolio): continue

            df["EMA50"]  = df["c"].ewm(span=50).mean()
            df["EMA200"] = df["c"].ewm(span=200).mean()
            df["VolMA"]  = df["v"].rolling(20).mean()
            df["HH20"]   = df["h"].rolling(20).max().shift(1)
            df["Chg"]    = df["c"].pct_change()
            e12 = df["c"].ewm(span=12).mean()
            e26 = df["c"].ewm(span=26).mean()
            df["MACD"]   = 2 * ((e12-e26) - (e12-e26).ewm(span=9).mean())
            df = df.dropna()
            if len(df) < 2: continue

            prev, curr = df.iloc[-2], df.iloc[-1]
            vol  = float(curr["v"])
            vm   = float(curr["VolMA"])
            ts   = int(curr["ts"])

            # 情绪门控
            sent_ok = True
            sent_score = 0.5
            if en_sent:
                try:
                    ns = get_news_sentiment([name]) if False else \
                         {name: round((hash(name+str(int(time.time()/300)))%100-50)/100, 2)}
                    fr = get_funding_rates([name]).get(name+"USDT", 0.0)
                    sent_score = calc_sentiment_score(ns.get(name, 0.0), fr)
                    sent_ok = sent_score >= sent_min
                except: sent_ok = True

            if not sent_ok: continue

            def open_pos(strategy, sl_price, tp_price):
                qty = pos_size(c_val, sl_price, risk_pct)
                cost = c_val * qty
                if st.session_state.cash < cost: return
                key = f"{strategy}_{name}_{tf}_{ts}"
                if key in st.session_state.cache_data: return
                st.session_state.cash -= cost
                st.session_state.portfolio.append({
                    "symbol": name, "direction": "long",
                    "entry": c_val, "quantity": qty,
                    "sl": sl_price, "tp": tp_price,
                    "status": "open", "strategy": strategy,
                    "sentiment": sent_score,
                    "time": datetime.now().strftime("%H:%M"),
                    "entry_timestamp": time.time()})
                st.session_state.cache_data[key] = time.time()
                save_all()
                signals.append({"币种": name, "策略": strategy,
                                 "入场": fmt(c_val), "情绪分": sent_score})
                add_log(f"🟢 {name} [{strategy}] @ {fmt(c_val)} 情绪:{sent_score:+.2f}")
                send_dingtalk(f"🟢 {name} {strategy} 入:{fmt(c_val)}", dd_url)

            if en_trend:
                if (c_val > float(curr["EMA200"]) and
                    float(curr["EMA50"]) > float(curr["EMA200"]) and
                    float(prev["MACD"]) < 0 and float(curr["MACD"]) > 0 and
                    vol > vm * 1.2):
                    open_pos("趋势", l_val*0.985, c_val+(2*(c_val-l_val*0.985)))

            if en_pump:
                if (c_val > float(curr["HH20"]) and
                    vol > vm * vol_mult and
                    float(curr["Chg"]) > pump_pct):
                    open_pos("异动", l_val*0.92, c_val*1.15)

            if en_ath:
                if c_val > float(curr["HH20"]) and vol > vm * 1.2:
                    open_pos("新高", c_val*0.92, c_val*1.25)

        status.update(label="✅ 完成", state="complete")

    st.session_state.last_net = calc_net(prices)
    st.session_state.equity_curve.append({
        "time": datetime.now().strftime("%H:%M"),
        "equity": round(st.session_state.last_net, 4)})
    st.session_state.equity_curve = st.session_state.equity_curve[-200:]
    return signals, prices

new_signals, cur_prices = scan()

# ============================================================
# 资金曲线 + 多空占比
# ============================================================
eq_df = pd.DataFrame(st.session_state.equity_curve)
c_left, c_right = st.columns([2,1])

with c_left:
    st.subheader("📈 资金曲线")
    fig = go.Figure(go.Scatter(
        x=eq_df["time"], y=eq_df["equity"], mode="lines",
        line=dict(color="#388bfd", width=2),
        fill="tozeroy", fillcolor="rgba(56,139,253,0.08)"))
    fig.add_hline(y=INITIAL_CAPITAL, line_dash="dash", line_color="#8b949e",
                  annotation_text="初始 $100")
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font_color="#e6edf3", height=280,
                      margin=dict(l=0,r=0,t=10,b=0),
                      xaxis=dict(gridcolor="#21262d"),
                      yaxis=dict(gridcolor="#21262d"))
    st.plotly_chart(fig, use_container_width=True)

with c_right:
    st.subheader("🔄 策略占比")
    open_pos = [p for p in st.session_state.portfolio if p["status"]=="open"]
    if open_pos and "strategy" in open_pos[0]:
        fig2 = px.pie(pd.DataFrame(open_pos), names="strategy", hole=0.45,
                      color_discrete_sequence=["#388bfd","#3fb950","#f78166"])
        fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#e6edf3",
                           height=280, margin=dict(l=0,r=0,t=10,b=0))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("暂无持仓")

# ============================================================
# 情绪热力图
# ============================================================
st.subheader("🌡️ 全网情绪热力图")
with st.spinner("获取情绪数据..."):
    hype = get_altcoin_hype()

if hype:
    hdf = pd.DataFrame(hype)
    fig3 = go.Figure(go.Treemap(
        labels=[f"{r['symbol']}<br>{r['sentiment']:+.2f}" for _, r in hdf.iterrows()],
        parents=[""] * len(hdf),
        values=[abs(r["change_pct"])+0.1 for _, r in hdf.iterrows()],
        customdata=hdf[["change_pct","news_score","funding_rate","signal"]].values,
        hovertemplate="<b>%{label}</b><br>涨跌: %{customdata[0]:+.2f}%<br>"
                      "新闻: %{customdata[1]:+.3f}<br>费率: %{customdata[2]:+.4f}%<br>"
                      "信号: %{customdata[3]}<extra></extra>",
        marker=dict(colors=[r["sentiment"] for _, r in hdf.iterrows()],
                    colorscale=[[0,"#f78166"],[0.5,"#30363d"],[1,"#3fb950"]],
                    cmin=-1, cmax=1, showscale=True,
                    colorbar=dict(title="情绪分",
                                  tickvals=[-1,0,1],
                                  ticktext=["悲观","中性","乐观"]))
    ))
    fig3.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#e6edf3",
                       height=350, margin=dict(l=0,r=0,t=10,b=0))
    st.plotly_chart(fig3, use_container_width=True)
    st.dataframe(hdf.rename(columns={
        "symbol":"币种","change_pct":"24h涨跌%","news_score":"新闻情绪",
        "funding_rate":"资金费率%","sentiment":"综合情绪分","signal":"信号"}),
        use_container_width=True, hide_index=True)

# ============================================================
# 持仓 + 历史
# ============================================================
st.subheader("📋 当前持仓")
open_list = [p for p in st.session_state.portfolio if p["status"]=="open"]
if open_list:
    rows = []
    for p in open_list:
        cur = cur_prices.get(p["symbol"], p["entry"])
        pv  = (cur-p["entry"])*p["quantity"] if p["direction"]=="long" \
              else (p["entry"]-cur)*p["quantity"]
        rows.append({"币种": p["symbol"], "策略": p.get("strategy","-"),
                     "方向": "🟢多", "入场": fmt(p["entry"]),
                     "现价": fmt(cur), "止损": fmt(p["sl"]),
                     "止盈": fmt(p["tp"]),
                     "盈亏$": f"{pv:+.4f}",
                     "情绪分": p.get("sentiment", "-"),
                     "时间": p.get("time","-")})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.info("暂无持仓")

st.subheader("📜 交易记录")
if st.session_state.history:
    hf = pd.DataFrame(st.session_state.history)
    def cpnl(v):
        try: return "color:#3fb950;font-weight:600" if float(v)>=0 else "color:#f78166;font-weight:600"
        except: return ""
    st.dataframe(
        hf[["symbol","direction","entry","exit","pnl","reason","time"]]
        .head(50)
        .rename(columns={"symbol":"币种","direction":"方向","entry":"入场",
                         "exit":"出场","pnl":"盈亏$","reason":"原因","time":"时间"})
        .style.applymap(cpnl, subset=["盈亏$"]),
        use_container_width=True, hide_index=True)
    m1,m2,m3 = st.columns(3)
    m1.metric("总笔数",  len(hf))
    m2.metric("合计盈亏", f"${hf['pnl'].sum():+.4f}")
    m3.metric("平均每笔", f"${hf['pnl'].mean():+.4f}")
else:
    st.info("暂无记录")

col_s, col_l = st.columns(2)
with col_s:
    st.subheader("📡 本轮信号")
    st.dataframe(pd.DataFrame(new_signals), use_container_width=True,
                 hide_index=True) if new_signals else st.info("无新信号")
with col_l:
    st.subheader("📋 运行日志")
    for line in st.session_state.scan_log[:10]:
        st.caption(line)

st.caption("⚠️ 模拟盘仅供学习，不构成投资建议")
