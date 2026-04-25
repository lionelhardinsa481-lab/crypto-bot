from sentiment import get_altcoin_hype, sentiment_gate, get_funding_rates
"""
加密货币实战信号监控看板
初始资金: 100 USDT | GitHub Codespaces 版
"""

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

# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="Crypto 信号监控",
    layout="wide",
    page_icon="📈",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
.stApp { background-color: #0d1117; color: #e6edf3; }
section[data-testid="stSidebar"] { background-color: #161b22; border-right: 1px solid #30363d; }
div[data-testid="stMetric"] { background-color: #161b22; border-radius: 12px;
    padding: 16px; border: 1px solid #30363d; }
.signal-card { background: linear-gradient(135deg, #1f6feb, #388bfd);
    padding: 14px 24px; border-radius: 16px; color: white; margin-bottom: 16px; }
div[data-testid="stDataFrame"] { border-radius: 12px; border: 1px solid #30363d; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# 常量配置
# ============================================================
INITIAL_CAPITAL  = 100.0   # 初始资金 100 USDT
DINGTALK_WEBHOOK = ""      # 填入钉钉 Webhook（可选）

# Codespaces 用相对路径存储数据
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

ACCOUNT_FILE   = os.path.join(DATA_DIR, "account.json")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
HISTORY_FILE   = os.path.join(DATA_DIR, "history.json")
CACHE_FILE     = os.path.join(DATA_DIR, "cache.json")

# 默认监控币种
DEFAULT_WATCHLIST = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT",
    "AVAX/USDT:USDT", "LINK/USDT:USDT", "DOT/USDT:USDT",
]

# ============================================================
# 数据持久化
# ============================================================
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.toast(f"保存失败: {e}", icon="⚠️")

def save_all():
    save_json(ACCOUNT_FILE,   {"cash": st.session_state.cash,
                                "initial_capital": st.session_state.initial_capital})
    save_json(PORTFOLIO_FILE, st.session_state.portfolio)
    save_json(HISTORY_FILE,   st.session_state.history)
    save_json(CACHE_FILE,     st.session_state.cache_data)

# ============================================================
# Session State 初始化
# ============================================================
def init_state():
    if "cash" not in st.session_state:
        saved = load_json(ACCOUNT_FILE, {})
        st.session_state.cash            = saved.get("cash", INITIAL_CAPITAL)
        st.session_state.initial_capital = saved.get("initial_capital", INITIAL_CAPITAL)
    if "portfolio"      not in st.session_state:
        st.session_state.portfolio       = load_json(PORTFOLIO_FILE, [])
    if "history"        not in st.session_state:
        st.session_state.history         = load_json(HISTORY_FILE, [])
    if "cache_data"     not in st.session_state:
        raw = load_json(CACHE_FILE, {})
        now = time.time()
        st.session_state.cache_data = {k: v for k, v in raw.items() if now - v < 3600}
    if "watchlist"      not in st.session_state:
        st.session_state.watchlist       = DEFAULT_WATCHLIST.copy()
    if "last_net_asset" not in st.session_state:
        st.session_state.last_net_asset  = st.session_state.cash
    if "equity_curve"   not in st.session_state:
        st.session_state.equity_curve    = [
            {"time": datetime.now().strftime("%H:%M"), "equity": INITIAL_CAPITAL}
        ]
    if "scan_log"       not in st.session_state:
        st.session_state.scan_log        = []

init_state()

# ============================================================
# 交易所连接（自动切换）
# ============================================================
@st.cache_resource
def get_exchange():
    for name, cls, symbol in [
        ("OKX",    ccxt.okx,    "BTC/USDT:USDT"),
        ("Binance", ccxt.binance, "BTC/USDT:USDT"),
    ]:
        try:
            ex = cls({"options": {"defaultType": "swap"},
                      "enableRateLimit": True, "timeout": 10000})
            ex.fetch_ticker(symbol)
            return ex, name
        except:
            continue
    return None, "连接失败"

EXCHANGE, EXCHANGE_NAME = get_exchange()

# ============================================================
# 工具函数
# ============================================================
def fmt_price(p):
    if   p < 0.01:  return f"{p:.6f}"
    elif p < 1:     return f"{p:.4f}"
    elif p < 100:   return f"{p:.2f}"
    else:           return f"{p:.1f}"

def send_dingtalk(text):
    if not DINGTALK_WEBHOOK:
        return
    try:
        requests.post(DINGTALK_WEBHOOK, json={
            "msgtype": "text",
            "text": {"content": f"【Crypto信号】\n{text}"}
        }, timeout=5)
    except:
        pass

def calc_net_asset(prices: dict) -> float:
    total = st.session_state.cash
    for p in st.session_state.portfolio:
        if p["status"] != "open":
            continue
        cur = prices.get(p["symbol"], p["entry"])
        if p["direction"] == "long":
            total += cur * p["quantity"]
        else:
            total += (2 * p["entry"] - cur) * p["quantity"]
    return total

def calc_position_size(entry, stop_loss, risk_pct):
    risk_amount   = st.session_state.cash * (risk_pct / 100)
    price_risk    = abs(entry - stop_loss)
    if price_risk == 0:
        return 0
    return max(risk_amount / price_risk, 0.0001)

def add_log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    st.session_state.scan_log.insert(0, f"[{now}] {msg}")
    st.session_state.scan_log = st.session_state.scan_log[:30]

# ============================================================
# 动态获取热门币种（妖币检测）
# ============================================================
def get_top_gainers(limit=20):
    if not EXCHANGE:
        return []
    try:
        tickers = EXCHANGE.fetch_tickers()
        usdt_perp = [
            t for sym, t in tickers.items()
            if ":USDT" in sym and t.get("percentage") is not None
        ]
        top = sorted(usdt_perp, key=lambda x: x["percentage"], reverse=True)[:limit]
        return [t["symbol"] for t in top]
    except:
        return []

# ============================================================
# K线数据
# ============================================================
def get_ohlcv(symbol, tf, limit=250):
    try:
        data = EXCHANGE.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
        return pd.DataFrame(data, columns=["ts", "o", "h", "l", "c", "v"])
    except:
        return pd.DataFrame()

# ============================================================
# 侧边栏
# ============================================================
with st.sidebar:
    st.title("⚡ 控制台")

    net = st.session_state.last_net_asset
    pnl = net - st.session_state.initial_capital
    pnl_pct = pnl / st.session_state.initial_capital * 100
    st.metric("💰 净资产", f"${net:.2f}", delta=f"{pnl_pct:+.2f}%")
    st.caption(f"可用现金: ${st.session_state.cash:.2f} | 初始: ${INITIAL_CAPITAL:.0f}")

    st.divider()

    # 钉钉配置
    with st.expander("🔔 钉钉推送"):
        dd_url = st.text_input("Webhook", value=DINGTALK_WEBHOOK,
                               placeholder="https://oapi.dingtalk.com/robot/...",
                               type="password")
        if st.button("测试推送"):
            send_dingtalk("✅ 测试成功！")
            st.toast("已发送", icon="✅")

    st.divider()

    # 策略参数
    st.subheader("📊 策略参数")
    tf = st.selectbox("K线周期", ["5m", "15m", "1h", "4h"], index=1)
    risk_pct = st.slider("单笔风险 %", 0.5, 10.0, 2.0, 0.5)

    enable_trend = st.checkbox("📈 趋势策略",     value=True)
    enable_pump  = st.checkbox("🚀 异动突破策略",  value=True)
    enable_ath   = st.checkbox("📊 新高突破策略",  value=True)

    with st.expander("🔧 高级参数"):
        pump_pct  = st.slider("异动涨幅阈值 %", 1.0, 20.0, 2.5, 0.5) / 100
        vol_mult  = st.slider("成交量倍数",     1.0,  8.0, 2.0, 0.2)

    st.divider()

    # ---- 动态币种管理 ----
    st.subheader("🔍 监控币种管理")

    # 显示当前列表
    wl_names = [s.split("/")[0] for s in st.session_state.watchlist]
    st.caption(f"当前监控 {len(st.session_state.watchlist)} 个币种")

    # 添加币种
    col_a, col_b = st.columns([3, 1])
    with col_a:
        new_coin = st.text_input("添加币种", placeholder="如 PEPE", label_visibility="collapsed")
    with col_b:
        if st.button("➕", use_container_width=True):
            if new_coin:
                sym = f"{new_coin.upper()}/USDT:USDT"
                if sym not in st.session_state.watchlist:
                    st.session_state.watchlist.append(sym)
                    st.toast(f"已添加 {new_coin.upper()}", icon="✅")
                    st.rerun()

    # 删除币种
    del_coin = st.selectbox("删除币种", ["不删除"] + wl_names)
    if st.button("🗑️ 删除选中", use_container_width=True):
        if del_coin != "不删除":
            st.session_state.watchlist = [
                s for s in st.session_state.watchlist
                if not s.startswith(del_coin + "/")
            ]
            st.toast(f"已删除 {del_coin}", icon="🗑️")
            st.rerun()

    # 一键同步热门币种
    if st.button("🔥 同步热门妖币", use_container_width=True):
        with st.spinner("获取中..."):
            hot = get_top_gainers(20)
            added = 0
            for s in hot:
                if s not in st.session_state.watchlist:
                    st.session_state.watchlist.append(s)
                    added += 1
        st.toast(f"新增 {added} 个热门币种", icon="🔥")
        st.rerun()

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 刷新", use_container_width=True, type="primary"):
            st.rerun()
    with col2:
        if st.button("🗑️ 重置模拟盘", use_container_width=True):
            st.session_state.cash            = INITIAL_CAPITAL
            st.session_state.initial_capital = INITIAL_CAPITAL
            st.session_state.portfolio       = []
            st.session_state.history         = []
            st.session_state.cache_data      = {}
            st.session_state.equity_curve    = [
                {"time": datetime.now().strftime("%H:%M"), "equity": INITIAL_CAPITAL}
            ]
            st.session_state.last_net_asset  = INITIAL_CAPITAL
            save_all()
            st.rerun()

    st.caption(f"📡 数据源: {EXCHANGE_NAME}")

# ============================================================
# 主界面
# ============================================================
st.markdown("""
<div class="signal-card">
    <h2 style="margin:0">📊 加密货币实战信号监控</h2>
    <p style="margin:4px 0 0; opacity:0.85">初始资金 $100 · 模拟盘 · 实时信号</p>
</div>
""", unsafe_allow_html=True)

# KPI 卡片
net   = st.session_state.last_net_asset
pnl   = net - st.session_state.initial_capital
pnl_p = pnl / st.session_state.initial_capital * 100
wins  = [h for h in st.session_state.history if h.get("pnl", 0) > 0]
wr    = len(wins) / len(st.session_state.history) * 100 if st.session_state.history else 0

k1, k2, k3, k4 = st.columns(4)
k1.metric("💰 净资产",   f"${net:.2f}")
k2.metric("📈 累计盈亏", f"${pnl:+.2f}", f"{pnl_p:+.2f}%")
k3.metric("🏆 胜率",     f"{wr:.1f}%",   f"{len(st.session_state.history)} 笔")
k4.metric("💵 可用现金", f"${st.session_state.cash:.2f}")

st.markdown("<br>", unsafe_allow_html=True)

# ============================================================
# 核心扫描逻辑
# ============================================================
def scan_and_trade():
    if not EXCHANGE:
        st.error("❌ 交易所连接失败")
        return

    # 合并扫描列表：自定义 + 热门
    scan_list = list(set(st.session_state.watchlist))
    current_prices = {}
    new_signals    = []

    TF_CFG = {
        "5m":  {"pump_pct": 0.03, "vol_mult": 3.0},
        "15m": {"pump_pct": 0.04, "vol_mult": 2.5},
        "1h":  {"pump_pct": 0.06, "vol_mult": 2.0},
        "4h":  {"pump_pct": 0.08, "vol_mult": 1.8},
    }
    cfg_tf = TF_CFG[tf]

    with st.status(f"🔍 扫描 {len(scan_list)} 个币种...", expanded=True) as status:
        for i, sym in enumerate(scan_list):
            status.update(label=f"扫描中 {i+1}/{len(scan_list)}: {sym.split('/')[0]}")
            df = get_ohlcv(sym, tf, 250)
            if df.empty or len(df) < 60:
                continue

            sym_name = sym.split("/")[0]
            last     = df.iloc[-1]
            c_val    = float(last["c"])
            h_val    = float(last["h"])
            l_val    = float(last["l"])
            current_prices[sym_name] = c_val

            # ---------- 持仓管理 ----------
            for p in list(st.session_state.portfolio):
                if p["symbol"] != sym_name or p["status"] != "open":
                    continue

                entry     = p["entry"]
                qty       = p["quantity"]
                direction = p["direction"]

                if direction == "long":
                    pnl_pct_pos = (c_val - entry) / entry * 100
                    if pnl_pct_pos >= 5:
                        p["sl"] = max(p["sl"], entry)
                    if pnl_pct_pos >= 15:
                        p["sl"] = max(p["sl"], c_val * 0.95)
                else:
                    pnl_pct_pos = (entry - c_val) / entry * 100

                # 止损 / 止盈
                exit_price = None
                reason     = ""
                if direction == "long":
                    if l_val <= p["sl"]: exit_price, reason = p["sl"], "止损"
                    elif h_val >= p["tp"]: exit_price, reason = p["tp"], "止盈"
                else:
                    if h_val >= p["sl"]: exit_price, reason = p["sl"], "止损"
                    elif l_val <= p["tp"]: exit_price, reason = p["tp"], "止盈"

                if exit_price:
                    pnl_val = (exit_price - entry) * qty if direction == "long" \
                              else (entry - exit_price) * qty
                    st.session_state.cash += exit_price * qty if direction == "long" \
                                             else (2 * entry - exit_price) * qty
                    st.session_state.history.insert(0, {
                        "symbol":    sym_name,
                        "direction": direction,
                        "entry":     entry,
                        "exit":      exit_price,
                        "pnl":       round(pnl_val, 4),
                        "reason":    reason,
                        "time":      datetime.now().strftime("%m-%d %H:%M")
                    })
                    st.session_state.portfolio.remove(p)
                    save_all()
                    emoji = "🟢" if pnl_val > 0 else "🔴"
                    add_log(f"{emoji} {sym_name} 平仓 [{reason}] 盈亏:${pnl_val:+.4f}")
                    send_dingtalk(f"{emoji} {sym_name} {direction}平仓 {reason} 盈亏:${pnl_val:.2f}")
                    continue

            # ---------- 开仓信号 ----------
            if any(p["symbol"] == sym_name and p["status"] == "open"
                   for p in st.session_state.portfolio):
                continue

            # 计算指标
            df["EMA50"]   = df["c"].ewm(span=50).mean()
            df["EMA200"]  = df["c"].ewm(span=200).mean()
            df["Vol_MA"]  = df["v"].rolling(20).mean()
            df["HH20"]    = df["h"].rolling(20).max().shift(1)
            df["Change"]  = df["c"].pct_change()
            e12 = df["c"].ewm(span=12).mean()
            e26 = df["c"].ewm(span=26).mean()
            macd  = e12 - e26
            sig   = macd.ewm(span=9).mean()
            df["MACD_H"]  = 2 * (macd - sig)

            df_c   = df.dropna()
            if len(df_c) < 2:
                continue
            prev   = df_c.iloc[-2]
            curr   = df_c.iloc[-1]
            vol_ma = float(curr["Vol_MA"])
            vol    = float(curr["v"])
            candle_ts = int(curr["ts"])

            # 1) 趋势策略
            if enable_trend:
                key = f"T_{sym_name}_{tf}_{candle_ts}"
                if key not in st.session_state.cache_data:
                    uptrend      = c_val > float(curr["EMA200"]) and \
                                   float(curr["EMA50"]) > float(curr["EMA200"])
                    macd_cross   = float(prev["MACD_H"]) < 0 and float(curr["MACD_H"]) > 0
                    vol_ok       = vol > vol_ma * 1.2
                    if uptrend and macd_cross and vol_ok:
                        sl  = l_val * 0.985
                        tp  = c_val + 2 * (c_val - sl)
                        qty = calc_position_size(c_val, sl, risk_pct)
                        cost = c_val * qty
                        if st.session_state.cash >= cost:
                            st.session_state.cash -= cost
                            st.session_state.portfolio.append({
                                "symbol": sym_name, "direction": "long",
                                "entry": c_val, "quantity": qty,
                                "sl": sl, "tp": tp,
                                "status": "open", "strategy": "趋势",
                                "time": datetime.now().strftime("%H:%M"),
                                "entry_timestamp": time.time()
                            })
                            st.session_state.cache_data[key] = time.time()
                            save_all()
                            new_signals.append({"币种": sym_name, "策略": "趋势",
                                                "方向": "多", "入场": fmt_price(c_val)})
                            add_log(f"🟢 {sym_name} 趋势开多 @ {fmt_price(c_val)}")
                            send_dingtalk(f"🟢 {sym_name} 趋势多 入:{fmt_price(c_val)} 仓:{qty:.4f}")

            # 2) 异动突破策略
            if enable_pump:
                key = f"P_{sym_name}_{tf}_{candle_ts}"
                if key not in st.session_state.cache_data:
                    breakout  = c_val > float(curr["HH20"])
                    vol_surge = vol > vol_ma * vol_mult
                    pump_ok   = float(curr["Change"]) > pump_pct
                    if breakout and vol_surge and pump_ok:
                        sl  = l_val * 0.92
                        tp  = c_val * 1.15
                        qty = calc_position_size(c_val, sl, risk_pct)
                        cost = c_val * qty
                        if st.session_state.cash >= cost:
                            st.session_state.cash -= cost
                            st.session_state.portfolio.append({
                                "symbol": sym_name, "direction": "long",
                                "entry": c_val, "quantity": qty,
                                "sl": sl, "tp": tp,
                                "status": "open", "strategy": "异动",
                                "time": datetime.now().strftime("%H:%M"),
                                "entry_timestamp": time.time()
                            })
                            st.session_state.cache_data[key] = time.time()
                            save_all()
                            new_signals.append({"币种": sym_name, "策略": "异动",
                                                "方向": "突破", "入场": fmt_price(c_val)})
                            add_log(f"🚀 {sym_name} 异动突破 @ {fmt_price(c_val)}")
                            send_dingtalk(f"🚀 {sym_name} 异动突破 入:{fmt_price(c_val)} 仓:{qty:.4f}")

            # 3) 新高突破策略
            if enable_ath:
                key = f"ATH_{sym_name}_{tf}_{candle_ts}"
                if key not in st.session_state.cache_data:
                    ath_break = c_val > float(curr["HH20"]) and vol > vol_ma * 1.2
                    if ath_break:
                        sl  = c_val * 0.92
                        tp  = c_val * 1.25
                        qty = calc_position_size(c_val, sl, risk_pct)
                        cost = c_val * qty
                        if st.session_state.cash >= cost:
                            st.session_state.cash -= cost
                            st.session_state.portfolio.append({
                                "symbol": sym_name, "direction": "long",
                                "entry": c_val, "quantity": qty,
                                "sl": sl, "tp": tp,
                                "status": "open", "strategy": "新高",
                                "time": datetime.now().strftime("%H:%M"),
                                "entry_timestamp": time.time()
                            })
                            st.session_state.cache_data[key] = time.time()
                            save_all()
                            new_signals.append({"币种": sym_name, "策略": "新高",
                                                "方向": "突破", "入场": fmt_price(c_val)})
                            add_log(f"📊 {sym_name} 新高突破 @ {fmt_price(c_val)}")
                            send_dingtalk(f"📊 {sym_name} 新高 入:{fmt_price(c_val)} 仓:{qty:.4f}")

        status.update(label="✅ 扫描完成", state="complete")

    # 更新净资产
    net_now = calc_net_asset(current_prices)
    st.session_state.last_net_asset = net_now
    st.session_state.equity_curve.append({
        "time": datetime.now().strftime("%H:%M"),
        "equity": round(net_now, 4)
    })
    st.session_state.equity_curve = st.session_state.equity_curve[-200:]
    return new_signals, current_prices

# ============================================================
# 执行扫描
# ============================================================
result = scan_and_trade()
new_signals   = result[0] if result else []
current_prices = result[1] if result else {}

# ============================================================
# 资金曲线图
# ============================================================
eq_df = pd.DataFrame(st.session_state.equity_curve)
col_chart, col_pie = st.columns([2, 1])

with col_chart:
    st.subheader("📈 资金曲线")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=eq_df["time"], y=eq_df["equity"],
        mode="lines", name="净资产",
        line=dict(color="#388bfd", width=2),
        fill="tozeroy", fillcolor="rgba(56,139,253,0.08)"
    ))
    fig.add_hline(y=INITIAL_CAPITAL, line_dash="dash",
                  line_color="#8b949e", annotation_text="初始 $100")
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e6edf3", height=280, margin=dict(l=0, r=0, t=10, b=0),
        xaxis=dict(gridcolor="#21262d"), yaxis=dict(gridcolor="#21262d")
    )
    st.plotly_chart(fig, use_container_width=True)

with col_pie:
    st.subheader("🔄 策略占比")
    if st.session_state.portfolio:
        strat_counts = pd.DataFrame(st.session_state.portfolio)
        strat_counts = strat_counts[strat_counts["status"] == "open"]
        if not strat_counts.empty and "strategy" in strat_counts.columns:
            fig2 = px.pie(strat_counts, names="strategy", hole=0.45,
                          color_discrete_sequence=["#388bfd", "#3fb950", "#f78166"])
            fig2.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", font_color="#e6edf3",
                height=280, margin=dict(l=0, r=0, t=10, b=0)
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("暂无持仓数据")
    else:
        st.info("暂无持仓数据")

# ============================================================
# 当前持仓
# ============================================================
st.subheader("📋 当前持仓")
if st.session_state.portfolio:
    pos_rows = []
    for p in st.session_state.portfolio:
        if p["status"] != "open":
            continue
        cur = current_prices.get(p["symbol"], p["entry"])
        pnl_val = (cur - p["entry"]) * p["quantity"] if p["direction"] == "long" \
                  else (p["entry"] - cur) * p["quantity"]
        pnl_pct_pos = pnl_val / (p["entry"] * p["quantity"]) * 100
        pos_rows.append({
            "币种":      p["symbol"],
            "策略":      p.get("strategy", "-"),
            "方向":      "🟢多" if p["direction"] == "long" else "🔴空",
            "入场价":    fmt_price(p["entry"]),
            "现价":      fmt_price(cur),
            "止损":      fmt_price(p["sl"]),
            "止盈":      fmt_price(p["tp"]),
            "浮动盈亏$": f"{pnl_val:+.4f}",
            "盈亏%":     f"{pnl_pct_pos:+.2f}%",
            "开仓时间":  p.get("time", "-"),
        })
    st.dataframe(pd.DataFrame(pos_rows), use_container_width=True, hide_index=True)
else:
    st.info("暂无持仓")

# ============================================================
# 交易记录
# ============================================================
st.subheader("📜 历史交易记录")
if st.session_state.history:
    col_f1, col_f2, col_f3 = st.columns([2, 2, 1])
    with col_f1:
        filter_dir = st.selectbox("筛选方向", ["全部", "long", "short"])
    with col_f2:
        filter_reason = st.selectbox(
            "筛选原因", ["全部"] + list({h["reason"] for h in st.session_state.history})
        )
    with col_f3:
        only_loss = st.checkbox("只看亏损")

    hist_df = pd.DataFrame(st.session_state.history)
    if filter_dir    != "全部": hist_df = hist_df[hist_df["direction"] == filter_dir]
    if filter_reason != "全部": hist_df = hist_df[hist_df["reason"]    == filter_reason]
    if only_loss:               hist_df = hist_df[hist_df["pnl"]       <  0]

    hist_df = hist_df.head(50)

    def color_pnl(val):
        try:
            return "color: #3fb950; font-weight:600" if float(val) >= 0 \
                   else "color: #f78166; font-weight:600"
        except:
            return ""

    st.dataframe(
        hist_df[["symbol", "direction", "entry", "exit", "pnl", "reason", "time"]]
        .rename(columns={"symbol":"币种","direction":"方向","entry":"入场",
                         "exit":"出场","pnl":"盈亏$","reason":"原因","time":"时间"})
        .style.applymap(color_pnl, subset=["盈亏$"]),
        use_container_width=True, hide_index=True
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("筛选笔数",  len(hist_df))
    m2.metric("合计盈亏",  f"${hist_df['pnl'].sum():+.4f}")
    m3.metric("平均每笔",  f"${hist_df['pnl'].mean():+.4f}" if len(hist_df) > 0 else "$0")
else:
    st.info("暂无交易记录")

# ============================================================
# 本轮新信号 + 运行日志
# ============================================================
col_sig, col_log = st.columns(2)
with col_sig:
    st.subheader("📡 本轮新信号")
    if new_signals:
        st.dataframe(pd.DataFrame(new_signals), use_container_width=True, hide_index=True)
    else:
        st.info("本轮无新信号")

with col_log:
    st.subheader("📋 运行日志")
    for log_line in st.session_state.scan_log[:10]:
        st.caption(log_line)
# ============================================================
# 全网情绪热力图
# ============================================================
st.subheader("🌡️ 全网情绪热力图")

with st.spinner("获取涨幅榜情绪数据..."):
    hype_data = get_altcoin_hype()

if hype_data:
    hype_df = pd.DataFrame(hype_data)

    # 热力图
    fig_heat = go.Figure(go.Treemap(
        labels=[f"{r['symbol']}<br>{r['sentiment']:+.2f}" for _, r in hype_df.iterrows()],
        parents=[""] * len(hype_df),
        values=[abs(r["change_pct"]) + 0.1 for _, r in hype_df.iterrows()],
        customdata=hype_df[["change_pct", "news_score", "funding_rate", "signal"]].values,
        hovertemplate=(
            "<b>%{label}</b><br>"
            "涨跌幅: %{customdata[0]:+.2f}%<br>"
            "新闻情绪: %{customdata[1]:+.3f}<br>"
            "资金费率: %{customdata[2]:+.4f}%<br>"
            "信号: %{customdata[3]}<extra></extra>"
        ),
        marker=dict(
            colors=[r["sentiment"] for _, r in hype_df.iterrows()],
            colorscale=[[0, "#f78166"], [0.5, "#30363d"], [1, "#3fb950"]],
            cmin=-1, cmax=1,
            showscale=True,
            colorbar=dict(title="情绪分", tickvals=[-1, 0, 1],
                          ticktext=["极度悲观", "中性", "极度乐观"])
        )
    ))
    fig_heat.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#e6edf3",
        height=380,
        margin=dict(l=0, r=0, t=10, b=0)
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    # 明细表
    display_cols = {
        "symbol": "币种", "change_pct": "24h涨跌%",
        "news_score": "新闻情绪", "funding_rate": "资金费率%",
        "sentiment": "综合情绪分", "signal": "信号"
    }
    st.dataframe(
        hype_df.rename(columns=display_cols),
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("暂无情绪数据，检查网络连接")
st.caption("⚠️ 模拟盘仅供学习测试，不构成投资建议")
