import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
import yfinance as yf
import pandas as pd
from aiohttp import web
from telegram import Update, ChatMemberUpdated
from telegram.ext import Application, CommandHandler, ContextTypes, ChatMemberHandler

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID      = os.getenv("GROUP_ID", "@yourgroupusername")       # paid group
FREE_GROUP_ID   = os.getenv("FREE_GROUP_ID", "@yourfreegroupusername")  # free group
ADMIN_ID        = int(os.getenv("ADMIN_ID", "0"))

STATS_FILE      = "stats.json"
SCAN_INTERVAL   = 3 * 60      # scan every 3 minutes (many API calls per pair)
STATS_INTERVAL  = 6 * 3600   # stats post every 6 hours

# ── Runtime controls ──────────────────────────────────────────────────────────
bot_paused            = False
expiry_mins           = 1
min_score             = 2
pair_cooldown         = 5 * 60
member_count          = 0
best_streak           = {"count": 0, "type": None}

# ── Money Management ──────────────────────────────────────────────────────────
daily_loss_limit      = 5       # auto pause after X losses in a day
martingale_enabled    = True    # suggest doubling after a loss
consecutive_losses    = 0       # tracks current loss run for martingale

# ── Smart Filter Settings ─────────────────────────────────────────────────────
trend_filter_enabled  = True    # M5 trend filter
time_filter_enabled   = False   # time filter off by default — enable with /filters time
news_filter_enabled   = False   # news filter off by default
candle_filter_enabled = True    # candlestick pattern confirmation

# Best hours per UTC (learned over time, starts with known good hours)
GOOD_HOURS_UTC = {7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20}

HISTORY_FILE     = "history.json"
PAIR_STATS_FILE  = "pair_stats.json"
SESSION_FILE     = "session_stats.json"

TRADING_SESSIONS   = [(7, 21)]
TRADE_MILESTONES   = {10, 25, 50, 100, 250, 500}
WINRATE_MILESTONES = {60, 65, 70, 75, 80}

OTC_PAIRS = {
    # Forex OTC
    "EUR/USD OTC":  "EURUSD=X",
    "GBP/USD OTC":  "GBPUSD=X",
    "USD/JPY OTC":  "USDJPY=X",
    "USD/CHF OTC":  "USDCHF=X",
    "USD/CAD OTC":  "USDCAD=X",
    "AUD/USD OTC":  "AUDUSD=X",
    "NZD/USD OTC":  "NZDUSD=X",
    "EUR/GBP OTC":  "EURGBP=X",
    "EUR/JPY OTC":  "EURJPY=X",
    "EUR/CHF OTC":  "EURCHF=X",
    "GBP/JPY OTC":  "GBPJPY=X",
    "AUD/JPY OTC":  "AUDJPY=X",
    "AUD/CAD OTC":  "AUDCAD=X",
    "EUR/AUD OTC":  "EURAUD=X",
    "GBP/CAD OTC":  "GBPCAD=X",
    # Crypto OTC
    "BTC/USD OTC":  "BTC-USD",
    "ETH/USD OTC":  "ETH-USD",
    "LTC/USD OTC":  "LTC-USD",
    "XRP/USD OTC":  "XRP-USD",
    # Commodities OTC
    "Gold OTC":     "GC=F",
    "Silver OTC":   "SI=F",
    "Oil OTC":      "CL=F",
}

# Must be defined after OTC_PAIRS
active_pairs = list(OTC_PAIRS.keys())

# Tracks last signal message ID for pinning
last_signal_message_id = None

MOTIVATIONAL_QUOTES = [
    ("The market rewards patience. Every signal is an opportunity — take it with discipline.", "💎"),
    ("Losses are tuition fees. You're not failing, you're learning.", "📚"),
    ("One bad day doesn't define a trader. Your win rate over 100 trades does.", "📊"),
    ("The best traders in the world lose trades. What separates them is how they respond.", "🏆"),
    ("Stay consistent. The edge plays out over time — not in one trade.", "⚡"),
    ("Risk management is not optional. It's the only reason traders survive long term.", "🛡"),
    ("Don't trade with money you can't afford to lose. Clear mind = better decisions.", "🧠"),
    ("Every professional was once a beginner. Keep showing up.", "🚀"),
    ("The signal doesn't guarantee a win. It gives you an edge. Play the edge.", "🎯"),
    ("Compounding works in trading too. Small consistent wins build life-changing accounts.", "💰"),
    ("You don't need to win every trade. You need to win more than you lose.", "✅"),
    ("Discipline is doing the right thing even when it's hard. That's what separates pros.", "🔥"),
]

MAX_SIGNALS_PER_HOUR = 5

LEADERBOARD_FILE = "leaderboard.json"

TRADING_TIPS = [
    ("💡 Never risk more than 1-2% of your account on a single trade. Protect your capital first.", "Risk Management"),
    ("💡 Don't chase losses. If you hit 3 losses in a row, step away and come back tomorrow.", "Discipline"),
    ("💡 The best signals come during high liquidity — London and New York overlap (12:00-16:00 UTC) is the sweet spot.", "Timing"),
    ("💡 Consistency beats big wins. A 70% win rate over 100 trades beats one lucky 10x every time.", "Mindset"),
    ("💡 Always wait for the signal — never trade out of boredom. Patience is your edge.", "Discipline"),
    ("💡 Binary options are about direction, not magnitude. Even 1 pip in your favour is a win.", "Education"),
    ("💡 Keep a trading journal. Write down every trade you take and why. Patterns will reveal themselves.", "Growth"),
    ("💡 Avoid trading during major news events like NFP, CPI, or Fed decisions — volatility makes signals unreliable.", "Risk Management"),
    ("💡 Your mindset after a loss matters more than the loss itself. Stay calm and trust the process.", "Mindset"),
    ("💡 Don't increase your stake after a loss trying to recover. Flat staking is the professional approach.", "Risk Management"),
    ("💡 OTC markets run 24/7 but the best price action happens when real forex markets are open.", "Education"),
    ("💡 A signal with HIGH confidence means 6+ of 8 indicators agree. Those are your best setups.", "Education"),
    ("💡 Win rate matters but so does consistency. 10 trades at 70% beats 2 trades at 100%.", "Mindset"),
    ("💡 Never trade money you can't afford to lose. Scared money makes bad decisions.", "Risk Management"),
    ("💡 The market doesn't owe you a win. Every trade is independent — stay humble.", "Mindset"),
    ("💡 RSI below 30 = oversold = potential bounce up. RSI above 70 = overbought = potential drop.", "Education"),
    ("💡 MACD crossing above the signal line is a bullish sign. Below = bearish.", "Education"),
    ("💡 Bollinger Bands squeezing together means a big move is coming — watch closely.", "Education"),
    ("💡 ADX above 25 means a strong trend is in play. Below 25 = choppy market = avoid.", "Education"),
    ("💡 The best traders aren't right all the time — they just manage risk better than everyone else.", "Mindset"),
]

# Tracks last signal time per pair {pair: datetime}
pair_cooldowns: dict = {}

# Hourly signal counter {hour_str: count}
hourly_counter: dict = {}

# Streak tracker
streak: dict = {"count": 0, "type": None}  # type = "win" or "loss"


# ── Trading Hours ─────────────────────────────────────────────────────────────
def is_trading_hours() -> bool:
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    for start, end in TRADING_SESSIONS:
        if start <= now.hour < end:
            return True
    return False

def next_session_open() -> str:
    now        = datetime.now(timezone.utc)
    days_ahead = 2 if now.weekday() == 5 else 1 if now.weekday() == 6 else 1 if now.hour >= 21 else 0
    return (now + timedelta(days=days_ahead)).replace(hour=7, minute=0, second=0).strftime("%a %d %b at %H:%M UTC")

def current_hour_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")

def signals_this_hour() -> int:
    return hourly_counter.get(current_hour_key(), 0)

def increment_hourly_counter():
    key = current_hour_key()
    hourly_counter[key] = hourly_counter.get(key, 0) + 1

def update_streak(is_win: bool) -> tuple:
    """Updates streak and returns (streak_count, streak_type, is_new_milestone)."""
    outcome = "win" if is_win else "loss"
    if streak["type"] == outcome:
        streak["count"] += 1
    else:
        streak["type"]  = outcome
        streak["count"] = 1
    milestones = {3, 5, 10}
    is_milestone = streak["count"] in milestones
    return streak["count"], streak["type"], is_milestone


def pair_on_cooldown(pair: str) -> bool:
    last = pair_cooldowns.get(pair)
    if last is None:
        return False
    return (datetime.now(timezone.utc) - last).total_seconds() < pair_cooldown

def set_cooldown(pair: str):
    pair_cooldowns[pair] = datetime.now(timezone.utc)

# Global last signal time — prevent spamming
last_signal_time: datetime = None
GLOBAL_SIGNAL_COOLDOWN = 5 * 60  # 1 signal every 5 minutes max

# Data cache — avoid re-fetching same ticker multiple times per scan
_data_cache: dict = {}
_cache_time: dict = {}
CACHE_TTL = 90  # seconds

def get_cached_df(ticker: str, period: str, interval: str):
    """Returns cached yfinance data or fetches fresh."""
    key = f"{ticker}_{period}_{interval}"
    now = datetime.now(timezone.utc).timestamp()
    if key in _data_cache and (now - _cache_time.get(key, 0)) < CACHE_TTL:
        return _data_cache[key]
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
    _data_cache[key] = df
    _cache_time[key] = now
    return df

# Live signal log for API — keeps last 50 scan decisions
signal_log: list = []

def log_signal_decision(pair: str, direction: str, score: int, confidence: float, fired: bool, reason: str = "", indicators: list = None):
    """Logs every signal decision to the live feed."""
    signal_log.append({
        "time":       datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "pair":       pair,
        "direction":  direction,
        "score":      score,
        "confidence": confidence,
        "fired":      fired,
        "reason":     reason,
        "indicators": indicators or [],
    })
    if len(signal_log) > 50:
        signal_log.pop(0)


# ── Stats ─────────────────────────────────────────────────────────────────────
def load_leaderboard() -> dict:
    if os.path.exists(LEADERBOARD_FILE):
        with open(LEADERBOARD_FILE) as f:
            return json.load(f)
    return {}

def save_leaderboard(lb: dict):
    with open(LEADERBOARD_FILE, "w") as f:
        json.dump(lb, f)

def update_leaderboard(user_id: str, username: str, is_win: bool) -> dict:
    lb = load_leaderboard()
    if user_id not in lb:
        lb[user_id] = {"name": username, "wins": 0, "losses": 0}
    lb[user_id]["name"] = username
    if is_win:
        lb[user_id]["wins"] += 1
    else:
        lb[user_id]["losses"] += 1
    save_leaderboard(lb)
    return lb

def build_leaderboard_msg(lb: dict) -> str:
    if not lb:
        return (
            f"🎿 *SKII PRO SIGNALS*\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            f"🏆 *LEADERBOARD*\n\n"
            f"_No entries yet! Use /win or /loss after each trade to get on the board._\n\n"
            f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
        )

    sorted_lb = sorted(lb.values(), key=lambda x: (x["wins"], -x.get("losses", 0)), reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    rows = ""
    for i, entry in enumerate(sorted_lb[:10]):
        total = entry["wins"] + entry["losses"]
        wr    = winrate(entry["wins"], entry["losses"])
        medal = medals[i] if i < 3 else f"{i+1}."
        rows += f"   {medal} *{entry['name']}*\n"
        rows += f"       ✅ {entry['wins']}W  ❌ {entry['losses']}L  🎯 {wr:.0f}%  ({total} trades)\n\n"

    return (
        f"🎿 *SKII PRO SIGNALS*\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
        f"🏆 *LEADERBOARD*\n"
        f"_Top traders this session_\n\n"
        f"{rows}"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"📌 _Use /win or /loss after each trade to track your results!_\n\n"
        f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
    )


    if os.path.exists(STATS_FILE):
        with open(STATS_FILE) as f:
            return json.load(f)
    return {
        "wins": 0, "losses": 0,
        "daily_wins": 0, "daily_losses": 0,
        "weekly_wins": 0, "weekly_losses": 0,
        "last_reset": today_str(), "last_week_reset": week_str(),
        "celebrated_trades": [], "celebrated_winrates": [],
    }

async def send_to_free_group(bot, text: str):
    """Safely send to free group — silently skips if not configured."""
    if not FREE_GROUP_ID or FREE_GROUP_ID == "@yourfreegroupusername":
        return
    try:
        await bot.send_message(chat_id=FREE_GROUP_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        print(f"⚠️ Free group send failed: {e}")


def load_history() -> list:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return []

def save_history(h: list):
    with open(HISTORY_FILE, "w") as f:
        json.dump(h, f)

WEIGHTS_FILE = "indicator_weights.json"

INDICATOR_NAMES = ["RSI", "BB", "Stochastic"]

def load_weights() -> dict:
    """Load indicator weights from file. Default weight 1.0 for each."""
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE) as f:
            return json.load(f)
    return {
        name: {"wins": 0, "losses": 0, "weight": 1.0}
        for name in INDICATOR_NAMES
    }

def save_weights(w: dict):
    with open(WEIGHTS_FILE, "w") as f:
        json.dump(w, f, indent=2)

def update_weights(indicators_fired: list, is_win: bool):
    """
    Updates indicator win/loss record and recalculates weights.
    indicators_fired = list of indicator names that voted correctly e.g. ["RSI", "BB"]
    """
    weights = load_weights()
    for name in indicators_fired:
        if name not in weights:
            weights[name] = {"wins": 0, "losses": 0, "weight": 1.0}
        if is_win:
            weights[name]["wins"] += 1
        else:
            weights[name]["losses"] += 1
        # Recalculate weight based on win rate (min 0.5, max 2.0)
        w = weights[name]
        total = w["wins"] + w["losses"]
        if total >= 5:  # need at least 5 trades to adjust weight
            wr = w["wins"] / total
            weights[name]["weight"] = round(max(0.5, min(2.0, wr * 2)), 2)
    save_weights(weights)
    print(f"🧠 Weights updated: {[(k, v['weight']) for k,v in weights.items()]}")

def get_weighted_score(reasons: list, score: int) -> float:
    """
    Applies learned weights to the raw indicator score.
    Returns a weighted confidence score.
    """
    weights = load_weights()
    weighted = 0.0
    for reason in reasons:
        if "Neutral" in reason:
            continue
        for name in INDICATOR_NAMES:
            if name.upper() in reason.upper() or name[:3].upper() in reason.upper():
                w = weights.get(name, {}).get("weight", 1.0)
                weighted += w if score > 0 else -w
                break
    return round(weighted, 2)


def log_trade(pair: str, direction: str, entry: float, exit_price: float, is_win: bool, indicators_fired: list = None):
    history = load_history()
    now     = datetime.now(timezone.utc)
    history.append({
        "time":       now.strftime("%H:%M UTC"),
        "hour":       now.hour,
        "date":       today_str(),
        "pair":       pair,
        "direction":  direction,
        "entry":      entry,
        "exit":       exit_price,
        "result":     "WIN" if is_win else "LOSS",
        "pips":       round(abs(exit_price - entry) * 10000, 1),
        "indicators": indicators_fired or [],
    })
    save_history(history[-500:])
    if indicators_fired:
        update_weights(indicators_fired, is_win)
    # Update per-pair stats
    update_pair_stats(pair, is_win)
    # Update session stats
    update_session_stats(now.hour, is_win)


# ── Per-Pair Stats ────────────────────────────────────────────────────────────
def load_pair_stats() -> dict:
    if os.path.exists(PAIR_STATS_FILE):
        with open(PAIR_STATS_FILE) as f:
            return json.load(f)
    return {}

def save_pair_stats(p: dict):
    with open(PAIR_STATS_FILE, "w") as f:
        json.dump(p, f)

def update_pair_stats(pair: str, is_win: bool):
    stats = load_pair_stats()
    if pair not in stats:
        stats[pair] = {"wins": 0, "losses": 0}
    if is_win:
        stats[pair]["wins"] += 1
    else:
        stats[pair]["losses"] += 1
    save_pair_stats(stats)

def get_best_pairs(top_n: int = 5) -> list:
    stats = load_pair_stats()
    ranked = []
    for pair, data in stats.items():
        total = data["wins"] + data["losses"]
        if total >= 5:
            wr = data["wins"] / total * 100
            ranked.append((pair, wr, total))
    return sorted(ranked, key=lambda x: x[1], reverse=True)[:top_n]


# ── Session Stats ─────────────────────────────────────────────────────────────
def load_session_stats() -> dict:
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE) as f:
            return json.load(f)
    return {}

def save_session_stats(s: dict):
    with open(SESSION_FILE, "w") as f:
        json.dump(s, f)

def update_session_stats(hour: int, is_win: bool):
    stats = load_session_stats()
    key   = str(hour)
    if key not in stats:
        stats[key] = {"wins": 0, "losses": 0}
    if is_win:
        stats[key]["wins"] += 1
    else:
        stats[key]["losses"] += 1
    save_session_stats(stats)

def get_best_hours() -> set:
    stats = load_session_stats()
    good  = set()
    for hour, data in stats.items():
        total = data["wins"] + data["losses"]
        if total >= 5 and data["wins"] / total >= 0.55:
            good.add(int(hour))
    return good if good else GOOD_HOURS_UTC


# ── Multi-Timeframe Confirmation ──────────────────────────────────────────────
def get_mtf_trend(ticker: str) -> dict:
    """
    Advanced MTF analysis:
    - M15: EMA trend (big picture) — highest weight, can override everything
    - M5:  MACD momentum (medium momentum)
    - M1:  RSI + Stochastic (entry timing)
    Each timeframe uses the best indicator for its purpose.
    """
    results = {}

    # ── M15 — Big picture EMA trend (highest weight) ──────────────────────────
    try:
        df = get_cached_df(ticker, "30d", "15m")
        if df.empty or len(df) < 30:
            results["M15"] = {"dir": "NEUTRAL", "strength": 0, "weight": 3}
        else:
            close  = df["Close"].squeeze()
            if hasattr(close, 'columns'): close = close.iloc[:, 0]
            ema8   = close.ewm(span=8,  adjust=False).mean()
            ema21  = close.ewm(span=21, adjust=False).mean()
            spread = (float(ema8.iloc[-1]) - float(ema21.iloc[-1])) / float(ema21.iloc[-1]) * 100
            # Check if it's a NEW crossover (last 3 candles)
            prev_spread = (float(ema8.iloc[-4]) - float(ema21.iloc[-4])) / float(ema21.iloc[-4]) * 100
            is_new_cross = (spread > 0) != (prev_spread > 0)  # sign changed recently
            if spread > 0.05:
                strength = 3 if spread > 0.15 else 2  # strong vs weak UP
                results["M15"] = {"dir": "UP",   "strength": strength, "weight": 3, "new_cross": is_new_cross}
            elif spread < -0.05:
                strength = 3 if spread < -0.15 else 2
                results["M15"] = {"dir": "DOWN", "strength": strength, "weight": 3, "new_cross": is_new_cross}
            else:
                results["M15"] = {"dir": "NEUTRAL", "strength": 0, "weight": 3, "new_cross": False}
    except Exception:
        results["M15"] = {"dir": "NEUTRAL", "strength": 0, "weight": 3, "new_cross": False}

    # ── M5 — MACD momentum ────────────────────────────────────────────────────
    try:
        df = get_cached_df(ticker, "5d", "5m")
        if df.empty or len(df) < 30:
            results["M5"] = {"dir": "NEUTRAL", "strength": 0, "weight": 2}
        else:
            close    = df["Close"].squeeze()
            if hasattr(close, 'columns'): close = close.iloc[:, 0]
            macd     = close.ewm(span=3,  adjust=False).mean() - close.ewm(span=10, adjust=False).mean()
            signal   = macd.ewm(span=16, adjust=False).mean()
            hist     = macd - signal
            curr     = float(hist.iloc[-1])
            prev     = float(hist.iloc[-2])
            # Trend age — how many consecutive bars same direction
            age = 1
            direction = "UP" if curr > 0 else "DOWN"
            for i in range(-2, -15, -1):
                try:
                    if (float(hist.iloc[i]) > 0) == (curr > 0):
                        age += 1
                    else:
                        break
                except: break
            # Fresh momentum (hist increasing) = stronger
            is_increasing = abs(curr) > abs(prev)
            strength = 2 if (age <= 5 and is_increasing) else 1
            results["M5"] = {"dir": direction, "strength": strength, "weight": 2, "age": age}
    except Exception:
        results["M5"] = {"dir": "NEUTRAL", "strength": 0, "weight": 2, "age": 0}

    # ── M1 — RSI + Stochastic entry timing ───────────────────────────────────
    try:
        df = get_cached_df(ticker, "2d", "1m")
        if df.empty or len(df) < 20:
            results["M1"] = {"dir": "NEUTRAL", "strength": 0, "weight": 1}
        else:
            close   = df["Close"].squeeze()
            high    = df["High"].squeeze()
            low     = df["Low"].squeeze()
            if hasattr(close, 'columns'):
                close = close.iloc[:, 0]; high = high.iloc[:, 0]; low = low.iloc[:, 0]

            # RSI(7)
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(7).mean()
            loss  = (-delta.clip(upper=0)).rolling(7).mean()
            rsi   = float((100 - 100 / (1 + gain / loss)).iloc[-1])

            # Stochastic(5,3,3)
            k_raw   = 100 * (close - low.rolling(5).min()) / (high.rolling(5).max() - low.rolling(5).min())
            stoch_k = float(k_raw.rolling(3).mean().iloc[-1])

            if rsi < 30 and stoch_k < 30:
                results["M1"] = {"dir": "UP", "strength": 2, "weight": 1, "rsi": rsi, "stoch": stoch_k}
            elif rsi > 70 and stoch_k > 70:
                results["M1"] = {"dir": "DOWN", "strength": 2, "weight": 1, "rsi": rsi, "stoch": stoch_k}
            elif rsi < 40 or stoch_k < 40:
                results["M1"] = {"dir": "UP", "strength": 1, "weight": 1, "rsi": rsi, "stoch": stoch_k}
            elif rsi > 60 or stoch_k > 60:
                results["M1"] = {"dir": "DOWN", "strength": 1, "weight": 1, "rsi": rsi, "stoch": stoch_k}
            else:
                results["M1"] = {"dir": "NEUTRAL", "strength": 0, "weight": 1, "rsi": rsi, "stoch": stoch_k}
    except Exception:
        results["M1"] = {"dir": "NEUTRAL", "strength": 0, "weight": 1}

    # ── Weighted scoring ──────────────────────────────────────────────────────
    score = 0
    for tf, data in results.items():
        if tf in ("overall", "strength", "weighted_score", "m15_override"): continue
        w = data.get("weight", 1)
        s = data.get("strength", 1)
        if data["dir"] == "UP":
            score += w * s
        elif data["dir"] == "DOWN":
            score -= w * s

    # ── M15 override — if M15 is STRONGLY against, always block ──────────────
    m15 = results["M15"]
    m15_override = m15["dir"] != "NEUTRAL" and m15["strength"] >= 3

    overall = "UP" if score > 0 else "DOWN" if score < 0 else "NEUTRAL"
    strength = min(abs(score), 9)  # max 9 weighted points

    results["overall"]        = overall
    results["strength"]       = strength
    results["weighted_score"] = score
    results["m15_override"]   = m15_override
    results["m15_dir"]        = m15["dir"]
    results["m15_strength"]   = m15["strength"]

    return results


# ── Volatility Filter ─────────────────────────────────────────────────────────
def check_volatility(ticker: str) -> dict:
    """
    Measures current market volatility using ATR.
    Returns volatility level and whether it's suitable for M1 trading.
    """
    try:
        df = get_cached_df(ticker, "1d", "1m")
        if df.empty or len(df) < 15:
            return {"level": "UNKNOWN", "tradeable": True, "atr": 0}

        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()
        close = df["Close"].squeeze()
        if hasattr(close, 'columns'):
            high  = high.iloc[:, 0]
            low   = low.iloc[:, 0]
            close = close.iloc[:, 0]

        tr  = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])

        price     = float(close.iloc[-1])
        atr_pct   = (atr / price) * 100 if price > 0 else 0

        if atr_pct < 0.002:
            level, tradeable = "TOO_LOW", False    # completely dead market
        elif atr_pct < 0.05:
            level, tradeable = "LOW", True
        elif atr_pct < 0.3:
            level, tradeable = "NORMAL", True      # ideal
        elif atr_pct < 0.6:
            level, tradeable = "HIGH", True
        else:
            level, tradeable = "TOO_HIGH", False   # extremely chaotic

        return {"level": level, "tradeable": tradeable, "atr_pct": round(atr_pct, 4)}
    except Exception:
        return {"level": "UNKNOWN", "tradeable": True, "atr": 0}


# ── Advanced Support & Resistance ────────────────────────────────────────────
def get_support_resistance(ticker: str) -> dict:
    """
    Advanced S&R detection:
    1. Multi-timeframe (M5, M15, H1)
    2. Real swing high/low points
    3. Level strength by touch count
    4. Dynamic threshold based on ATR
    5. Zone detection (clustered levels)
    """
    try:
        all_supports    = []
        all_resistances = []

        configs = [
            ("5d",  "5m",  1.0),   # M5  — weight 1x
            ("30d", "15m", 1.5),   # M15 — weight 1.5x
            ("60d", "1h",  2.0),   # H1  — weight 2x (strongest)
        ]

        for period, interval, weight in configs:
            df = get_cached_df(ticker, period, interval)
            if df.empty or len(df) < 10:
                continue

            high  = df["High"].squeeze()
            low   = df["Low"].squeeze()
            close = df["Close"].squeeze()
            if hasattr(close, 'columns'):
                high  = high.iloc[:, 0]
                low   = low.iloc[:, 0]
                close = close.iloc[:, 0]

            # Dynamic ATR threshold for this timeframe
            tr  = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low  - close.shift()).abs()
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])

            # Find real swing highs — candle higher than 2 neighbours each side
            highs_list = high.tolist()
            lows_list  = low.tolist()
            n = len(highs_list)

            for i in range(2, n - 2):
                # Swing high
                if all(highs_list[i] > highs_list[i+j] for j in [-2,-1,1,2]):
                    all_resistances.append((highs_list[i], weight, atr))
                # Swing low
                if all(lows_list[i] < lows_list[i+j] for j in [-2,-1,1,2]):
                    all_supports.append((lows_list[i], weight, atr))

        if not all_supports and not all_resistances:
            return {"near_support": False, "near_resistance": False, "signal": "NONE", "strength": 0}

        # Get current price
        df_curr = get_cached_df(ticker, "1d", "1m")
        close_c = df_curr["Close"].squeeze()
        if hasattr(close_c, 'columns'): close_c = close_c.iloc[:, 0]
        price   = float(close_c.iloc[-1])
        atr_now = float(tr.rolling(14).mean().iloc[-1]) if 'tr' in dir() else price * 0.001

        # ── Zone detection — cluster nearby levels ────────────────────────────
        def cluster_levels(levels, tolerance):
            if not levels: return []
            sorted_l = sorted(levels, key=lambda x: x[0])
            clusters = []
            current  = [sorted_l[0]]
            for lvl in sorted_l[1:]:
                if abs(lvl[0] - current[-1][0]) < tolerance:
                    current.append(lvl)
                else:
                    clusters.append(current)
                    current = [lvl]
            clusters.append(current)
            # Return cluster center weighted by strength and touch count
            result = []
            for cluster in clusters:
                avg_price   = sum(l[0] * l[1] for l in cluster) / sum(l[1] for l in cluster)
                total_weight = sum(l[1] for l in cluster)
                touch_count  = len(cluster)
                result.append({"price": avg_price, "weight": total_weight, "touches": touch_count})
            return sorted(result, key=lambda x: x["weight"], reverse=True)

        tolerance   = atr_now * 2
        sup_zones   = cluster_levels(all_supports,    tolerance)
        res_zones   = cluster_levels(all_resistances, tolerance)

        # Find nearest zones to current price
        near_sup = None
        near_res = None
        sup_dist = float('inf')
        res_dist = float('inf')

        for zone in sup_zones:
            dist = price - zone["price"]
            if 0 <= dist < sup_dist and dist < atr_now * 3:
                sup_dist = dist
                near_sup = zone

        for zone in res_zones:
            dist = zone["price"] - price
            if 0 <= dist < res_dist and dist < atr_now * 3:
                res_dist = dist
                near_res = zone

        near_support    = near_sup is not None
        near_resistance = near_res is not None

        # Determine signal and strength
        signal   = "NONE"
        strength = 0

        if near_support and near_resistance:
            # Between levels — pick closer one
            if sup_dist <= res_dist:
                signal   = "CALL"
                strength = min(3, near_sup["touches"])
            else:
                signal   = "PUT"
                strength = min(3, near_res["touches"])
        elif near_support:
            signal   = "CALL"
            strength = min(3, near_sup["touches"])
        elif near_resistance:
            signal   = "PUT"
            strength = min(3, near_res["touches"])

        return {
            "near_support":    near_support,
            "near_resistance": near_resistance,
            "signal":          signal,
            "strength":        strength,
            "sup_price":       round(near_sup["price"], 5) if near_sup else None,
            "res_price":       round(near_res["price"], 5) if near_res else None,
            "sup_touches":     near_sup["touches"] if near_sup else 0,
            "res_touches":     near_res["touches"] if near_res else 0,
        }

    except Exception as e:
        print(f"⚠️ S&R error: {e}")
        return {"near_support": False, "near_resistance": False, "signal": "NONE", "strength": 0}


# ── RSI Divergence ────────────────────────────────────────────────────────────
def detect_divergence(ticker: str) -> str:
    """
    Detects bullish/bearish RSI divergence on M1.
    Bullish: price makes lower low but RSI makes higher low → CALL
    Bearish: price makes higher high but RSI makes lower high → PUT
    """
    try:
        df = get_cached_df(ticker, "1d", "1m")
        if df.empty or len(df) < 20:
            return "NONE"

        close = df["Close"].squeeze()
        if hasattr(close, 'columns'): close = close.iloc[:, 0]

        # Calculate RSI(7)
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(7).mean()
        loss  = (-delta.clip(upper=0)).rolling(7).mean()
        rsi   = 100 - 100 / (1 + gain / loss)

        # Compare last two swing points (last 10 vs 5 candles)
        price_recent = float(close.iloc[-5:].mean())
        price_prev   = float(close.iloc[-15:-10].mean())
        rsi_recent   = float(rsi.iloc[-5:].mean())
        rsi_prev     = float(rsi.iloc[-15:-10].mean())

        # Bullish divergence: price lower, RSI higher
        if price_recent < price_prev and rsi_recent > rsi_prev and rsi_recent < 40:
            return "CALL"

        # Bearish divergence: price higher, RSI lower
        if price_recent > price_prev and rsi_recent < rsi_prev and rsi_recent > 60:
            return "PUT"

        return "NONE"
    except Exception:
        return "NONE"


# ── News Filter ───────────────────────────────────────────────────────────────
HIGH_IMPACT_MINUTES = {
    # UTC times of common high impact news (approximate)
    # NFP first Friday 12:30, FOMC varies, CPI varies
    # We use a simple window approach — skip 5 min before/after round hours
}

def is_news_time() -> bool:
    """Skip trading during the first 5 minutes of each hour (news release window)."""
    now = datetime.now(timezone.utc)
    return now.minute < 5  # avoid first 5 min of every hour


# ── M5 Trend Filter (kept for backward compat) ───────────────────────────────

# ── Candlestick Pattern Detection ─────────────────────────────────────────────
def detect_order_blocks(ticker: str) -> dict:
    """
    Detects Order Blocks on M1 — zones where institutional orders were placed.
    Bullish OB: last bearish candle before a significant bullish move
    Bearish OB: last bullish candle before a significant bearish move
    Returns nearest OB and whether price is currently retesting it.
    """
    try:
        df = get_cached_df(ticker, "2d", "1m")
        if df.empty or len(df) < 20:
            return {"signal": "NONE", "type": None, "zone_high": None, "zone_low": None, "strength": 0}

        o = df["Open"].squeeze()
        h = df["High"].squeeze()
        l = df["Low"].squeeze()
        c = df["Close"].squeeze()
        if hasattr(c, 'columns'):
            o = o.iloc[:,0]; h = h.iloc[:,0]; l = l.iloc[:,0]; c = c.iloc[:,0]

        price   = float(c.iloc[-1])
        n       = len(c)
        obs     = []  # list of (type, zone_high, zone_low, strength, index)

        # Scan last 50 candles for OB patterns
        lookback = min(50, n - 5)
        for i in range(n - lookback, n - 3):
            # Bullish OB: bearish candle followed by strong bullish move
            if float(c.iloc[i]) < float(o.iloc[i]):  # bearish candle
                # Check if next 3 candles move significantly up
                move_up = float(h.iloc[i+1:i+4].max()) - float(h.iloc[i])
                candle_size = abs(float(c.iloc[i]) - float(o.iloc[i]))
                if move_up > candle_size * 1.5:  # move > 1.5x candle size
                    strength = min(3, int(move_up / candle_size))
                    obs.append({
                        "type":      "BULLISH",
                        "zone_high": float(o.iloc[i]),   # top of bearish candle
                        "zone_low":  float(c.iloc[i]),   # bottom of bearish candle
                        "strength":  strength,
                        "index":     i,
                    })

            # Bearish OB: bullish candle followed by strong bearish move
            if float(c.iloc[i]) > float(o.iloc[i]):  # bullish candle
                move_down = float(l.iloc[i]) - float(l.iloc[i+1:i+4].min())
                candle_size = abs(float(c.iloc[i]) - float(o.iloc[i]))
                if move_down > candle_size * 1.5:
                    strength = min(3, int(move_down / candle_size))
                    obs.append({
                        "type":      "BEARISH",
                        "zone_high": float(c.iloc[i]),
                        "zone_low":  float(o.iloc[i]),
                        "strength":  strength,
                        "index":     i,
                    })

        if not obs:
            return {"signal": "NONE", "type": None, "zone_high": None, "zone_low": None, "strength": 0}

        # Find OBs price is currently retesting (price inside the zone)
        retesting = []
        for ob in obs:
            zone_mid   = (ob["zone_high"] + ob["zone_low"]) / 2
            zone_range = ob["zone_high"] - ob["zone_low"]
            # Price within zone or very close (within 50% of zone size)
            if ob["zone_low"] - zone_range * 0.5 <= price <= ob["zone_high"] + zone_range * 0.5:
                retesting.append(ob)

        if not retesting:
            return {"signal": "NONE", "type": None, "zone_high": None, "zone_low": None, "strength": 0}

        # Pick strongest retesting OB
        best = max(retesting, key=lambda x: x["strength"])
        signal = "CALL" if best["type"] == "BULLISH" else "PUT"

        return {
            "signal":     signal,
            "type":       best["type"],
            "zone_high":  round(best["zone_high"], 5),
            "zone_low":   round(best["zone_low"], 5),
            "strength":   best["strength"],
            "retesting":  True,
        }

    except Exception as e:
        print(f"⚠️ OB error: {e}")
        return {"signal": "NONE", "type": None, "zone_high": None, "zone_low": None, "strength": 0}


def detect_fvg(ticker: str) -> dict:
    """
    Detects Fair Value Gaps (FVG) on M1 — price imbalances that tend to get filled.
    Bullish FVG: gap between candle 1 high and candle 3 low (price skipped up)
    Bearish FVG: gap between candle 1 low and candle 3 high (price skipped down)
    Returns whether price is currently inside an unfilled FVG.
    """
    try:
        df = get_cached_df(ticker, "2d", "1m")
        if df.empty or len(df) < 10:
            return {"signal": "NONE", "gap_high": None, "gap_low": None, "filled_pct": 0}

        h = df["High"].squeeze()
        l = df["Low"].squeeze()
        c = df["Close"].squeeze()
        if hasattr(c, 'columns'):
            h = h.iloc[:,0]; l = l.iloc[:,0]; c = c.iloc[:,0]

        price    = float(c.iloc[-1])
        n        = len(c)
        fvgs     = []
        lookback = min(30, n - 3)

        for i in range(n - lookback, n - 2):
            c1_high = float(h.iloc[i])
            c1_low  = float(l.iloc[i])
            c3_high = float(h.iloc[i+2])
            c3_low  = float(l.iloc[i+2])

            # Bullish FVG: c3 low > c1 high (gap above)
            if c3_low > c1_high:
                gap_size = c3_low - c1_high
                if gap_size > 0.00005:  # minimum gap size
                    fvgs.append({
                        "type":      "BULLISH",
                        "gap_high":  c3_low,
                        "gap_low":   c1_high,
                        "gap_size":  gap_size,
                        "index":     i,
                    })

            # Bearish FVG: c3 high < c1 low (gap below)
            if c3_high < c1_low:
                gap_size = c1_low - c3_high
                if gap_size > 0.00005:
                    fvgs.append({
                        "type":      "BEARISH",
                        "gap_high":  c1_low,
                        "gap_low":   c3_high,
                        "gap_size":  gap_size,
                        "index":     i,
                    })

        if not fvgs:
            return {"signal": "NONE", "gap_high": None, "gap_low": None, "filled_pct": 0}

        # Find FVGs price is currently inside
        inside = []
        for fvg in fvgs:
            if fvg["gap_low"] <= price <= fvg["gap_high"]:
                # How far into the gap is price? (fill percentage)
                if fvg["type"] == "BULLISH":
                    filled_pct = (price - fvg["gap_low"]) / fvg["gap_size"] * 100
                else:
                    filled_pct = (fvg["gap_high"] - price) / fvg["gap_size"] * 100
                inside.append({**fvg, "filled_pct": round(filled_pct, 1)})

        if not inside:
            return {"signal": "NONE", "gap_high": None, "gap_low": None, "filled_pct": 0}

        # Pick most recent FVG inside
        best = max(inside, key=lambda x: x["index"])
        signal = "PUT" if best["type"] == "BULLISH" else "CALL"
        # Price inside bullish FVG = gap filling downward = PUT
        # Price inside bearish FVG = gap filling upward = CALL

        return {
            "signal":     signal,
            "type":       best["type"],
            "gap_high":   round(best["gap_high"], 5),
            "gap_low":    round(best["gap_low"], 5),
            "gap_size":   round(best["gap_size"], 5),
            "filled_pct": best["filled_pct"],
        }

    except Exception as e:
        print(f"⚠️ FVG error: {e}")
        return {"signal": "NONE", "gap_high": None, "gap_low": None, "filled_pct": 0}


def detect_candle_pattern(ticker: str) -> str:
    try:
        df = get_cached_df(ticker, "1d", "1m")
        if df.empty or len(df) < 3:
            return "NONE"
        o = df["Open"].squeeze().iloc[-1]
        h = df["High"].squeeze().iloc[-1]
        l = df["Low"].squeeze().iloc[-1]
        c = df["Close"].squeeze().iloc[-1]
        if hasattr(o, '__len__'): o, h, l, c = float(o.iloc[0]), float(h.iloc[0]), float(l.iloc[0]), float(c.iloc[0])

        body   = abs(c - o)
        range_ = h - l
        if range_ == 0: return "NONE"

        lower_wick = min(o, c) - l
        upper_wick = h - max(o, c)

        if lower_wick > body * 2 and upper_wick < body * 0.5: return "CALL"   # Hammer
        if upper_wick > body * 2 and lower_wick < body * 0.5: return "PUT"    # Shooting star

        prev_o = float(df["Open"].squeeze().iloc[-2])
        prev_c = float(df["Close"].squeeze().iloc[-2])
        if prev_c < prev_o and c > o and c > prev_o and o < prev_c: return "CALL"  # Bullish engulf
        if prev_c > prev_o and c < o and c < prev_o and o > prev_c: return "PUT"   # Bearish engulf

        return "NONE"
    except Exception:
        return "NONE"


# ── Drawdown Protection ───────────────────────────────────────────────────────
def check_drawdown_protection(stats: dict) -> bool:
    daily_losses = stats.get("daily_losses", 0)
    if daily_losses >= daily_loss_limit:
        print(f"🛑 Drawdown protection — {daily_losses} losses today")
        return True
    return False



def load_stats() -> dict:
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE) as f:
            return json.load(f)
    return {
        "wins": 0, "losses": 0,
        "daily_wins": 0, "daily_losses": 0,
        "weekly_wins": 0, "weekly_losses": 0,
        "last_reset": today_str(), "last_week_reset": week_str(),
        "celebrated_trades": [], "celebrated_winrates": [],
    }
    return {
        "wins": 0, "losses": 0,
        "daily_wins": 0, "daily_losses": 0,
        "weekly_wins": 0, "weekly_losses": 0,
        "last_reset": today_str(), "last_week_reset": week_str(),
        "celebrated_trades": [], "celebrated_winrates": [],
    }

def save_stats(s: dict):
    with open(STATS_FILE, "w") as f:
        json.dump(s, f)

def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def week_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-W%W")

def maybe_reset_daily(s: dict) -> dict:
    if s.get("last_reset") != today_str():
        s["daily_wins"] = 0; s["daily_losses"] = 0; s["last_reset"] = today_str()
    return s

def maybe_reset_weekly(s: dict) -> dict:
    if s.get("last_week_reset") != week_str():
        s["weekly_wins"] = 0; s["weekly_losses"] = 0; s["last_week_reset"] = week_str()
    return s

def winrate(wins: int, losses: int) -> float:
    total = wins + losses
    return (wins / total * 100) if total > 0 else 0.0

def win_bar(wr: float) -> str:
    filled = round(wr / 10)
    return "🟩" * filled + "⬜" * (10 - filled)


# ── Indicators ────────────────────────────────────────────────────────────────

# ── Signal Engine (M1 Optimized — 3 indicators, 2/3 threshold) ───────────────
def generate_signal(pair: str) -> dict:
    ticker = OTC_PAIRS[pair]
    df = get_cached_df(ticker, "2d", "1m")

    if df.empty or len(df) < 20:
        raise ValueError(f"Not enough data for {pair}")

    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()

    if hasattr(close, 'columns'): close = close.iloc[:, 0]
    if hasattr(high,  'columns'): high  = high.iloc[:, 0]
    if hasattr(low,   'columns'): low   = low.iloc[:, 0]

    price = round(float(close.iloc[-1]), 5)

    # 1. RSI (7 period)
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(7).mean()
    loss  = (-delta.clip(upper=0)).rolling(7).mean()
    rsi   = round(float((100 - 100 / (1 + gain / loss)).iloc[-1]), 2)

    # 2. Bollinger Bands (10 period)
    sma      = close.rolling(10).mean()
    std      = close.rolling(10).std()
    bb_upper = float((sma + 2 * std).iloc[-1])
    bb_lower = float((sma - 2 * std).iloc[-1])

    # 3. Stochastic (5,3,3)
    low5    = low.rolling(5).min()
    high5   = high.rolling(5).max()
    k_raw   = 100 * (close - low5) / (high5 - low5)
    stoch_k = float(k_raw.rolling(3).mean().iloc[-1])
    stoch_d = float(k_raw.rolling(3).mean().rolling(3).mean().iloc[-1])

    score   = 0
    reasons = []

    # 1. RSI (7) — oversold/overbought
    if rsi < 25:
        score += 1; reasons.append(f"RSI {rsi:.1f} — Oversold")
    elif rsi > 75:
        score -= 1; reasons.append(f"RSI {rsi:.1f} — Overbought")
    else:
        reasons.append(f"RSI {rsi:.1f} — Neutral")

    # 2. Bollinger Bands (10)
    if price < bb_lower:
        score += 1; reasons.append("Price Below BB — Bounce Expected")
    elif price > bb_upper:
        score -= 1; reasons.append("Price Above BB — Reversal Expected")
    else:
        reasons.append("Price Inside BB — Neutral")

    # 3. Stochastic (5,3,3)
    if stoch_k < 20 and stoch_k > stoch_d:
        score += 1; reasons.append(f"Stoch {stoch_k:.1f} — Oversold Crossover")
    elif stoch_k > 80 and stoch_k < stoch_d:
        score -= 1; reasons.append(f"Stoch {stoch_k:.1f} — Overbought Crossover")
    else:
        reasons.append(f"Stoch {stoch_k:.1f} — Neutral")

    direction  = "CALL" if score >= 0 else "PUT"
    abs_score  = abs(score)
    confidence = "HIGH" if abs_score == 3 else "MEDIUM" if abs_score == 2 else "LOW"

    # Get which indicators actually fired (not neutral)
    indicators_fired = []
    for r in reasons:
        if "Neutral" not in r:
            if "RSI" in r:       indicators_fired.append("RSI")
            elif "BB" in r:      indicators_fired.append("BB")
            elif "Stoch" in r:   indicators_fired.append("Stochastic")

    # Apply learned weights
    weighted_score = get_weighted_score(reasons, score)

    return {
        "direction":        direction,
        "confidence":       confidence,
        "score":            score,
        "weighted_score":   weighted_score,
        "indicators_fired": indicators_fired,
        "reasons":          reasons,
        "rsi":              rsi,
        "stoch_k":          round(stoch_k, 1),
        "bb_upper":         round(bb_upper, 5),
        "bb_lower":         round(bb_lower, 5),
        "entry_price":      price,
    }


def get_current_price(pair: str) -> float:
    ticker = OTC_PAIRS[pair]
    df = get_cached_df(ticker, "1d", "1m")
    if df.empty:
        raise ValueError("Price fetch failed")
    close = df["Close"]
    if hasattr(close, "squeeze"):
        close = close.squeeze()
    return round(float(close.iloc[-1]), 5)


# ── Milestone Check ───────────────────────────────────────────────────────────
async def check_milestones(context, stats: dict):
    total = stats["wins"] + stats["losses"]
    wr    = winrate(stats["wins"], stats["losses"])
    ct    = stats.get("celebrated_trades", [])
    cw    = stats.get("celebrated_winrates", [])

    for m in TRADE_MILESTONES:
        if total >= m and m not in ct:
            ct.append(m); stats["celebrated_trades"] = ct; save_stats(stats)
            await context.bot.send_message(chat_id=CHANNEL_ID, parse_mode="Markdown", text=build_msg(
                "🎉 MILESTONE UNLOCKED!",
                f"   🏆  *{m} Trades Completed!*\n\n"
                f"   ✅  Wins      »  *{stats['wins']}*\n"
                f"   ❌  Losses   »  *{stats['losses']}*\n"
                f"   🎯  Win Rate »  *{wr:.1f}%*",
                f"💪 {m} trades and still going strong. Let's keep it up!"
            ))

    if total >= 10:
        for m in WINRATE_MILESTONES:
            if wr >= m and m not in cw:
                cw.append(m); stats["celebrated_winrates"] = cw; save_stats(stats)
                await context.bot.send_message(chat_id=CHANNEL_ID, parse_mode="Markdown", text=build_msg(
                    "🔥 WIN RATE MILESTONE!",
                    f"   🎯  We just hit *{m}% Win Rate!*\n\n"
                    f"   ✅  Wins    »  *{stats['wins']}*\n"
                    f"   ❌  Losses »  *{stats['losses']}*\n"
                    f"   📊  Total  »  *{total} trades*",
                    f"🚀 {m}% win rate. Skii Pro is delivering!"
                ))


# ── Universal Message Template ────────────────────────────────────────────────
HEADER  = "🎿 *SKII PRO SIGNALS*"
DIVIDER = "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰"
FOOTER  = "_🎿 Skii Pro Signals — Premium OTC Alerts_"

def build_msg(title: str, body: str, cta: str = "") -> str:
    """Universal message template — every bot message uses this."""
    cta_block = f"{DIVIDER}\n{cta}\n\n" if cta else ""
    return (
        f"{HEADER}\n"
        f"{DIVIDER}\n"
        f"{title}\n\n"
        f"{DIVIDER}\n"
        f"{body}\n\n"
        f"{cta_block}"
        f"{FOOTER}"
    )


def monte_carlo_confidence(win_rate: float, indicator_score: int, streak_type: str, streak_count: int, simulations: int = 1000) -> float:
    """
    Runs a quick Monte Carlo simulation to estimate confidence of the next signal winning.
    Returns a confidence percentage (0-100).
    """
    import random

    # Adjust base win rate based on indicator score (3/5 vs 5/5)
    score_boost = (indicator_score - 3) * 2.5  # +2.5% per extra indicator above minimum

    # Adjust for current streak
    streak_boost = 0
    if streak_type == "win" and streak_count >= 3:
        streak_boost = min(streak_count * 1.5, 8)   # winning streak = slight boost
    elif streak_type == "loss" and streak_count >= 3:
        streak_boost = -min(streak_count * 2, 10)   # losing streak = reduce confidence

    adjusted_wr = min(95, max(40, win_rate + score_boost + streak_boost))
    p = adjusted_wr / 100

    # Run simulations — check probability next 5 trades are net positive
    wins_count = 0
    for _ in range(simulations):
        wins = sum(1 for _ in range(5) if random.random() < p)
        if wins >= 3:  # majority of next 5 win
            wins_count += 1

    return round((wins_count / simulations) * 100, 1)


def get_payout(pair: str) -> str:
    """Returns typical Pocket Option payout % for each asset type."""
    if "BTC" in pair or "ETH" in pair or "LTC" in pair or "XRP" in pair:
        return "82%"
    elif "Gold" in pair or "Silver" in pair:
        return "80%"
    elif "Oil" in pair:
        return "79%"
    elif "GBP/JPY" in pair or "AUD/JPY" in pair:
        return "76%"
    else:
        return "85%"


def build_signal_msg(pair: str, signal: dict, time_str: str) -> str:
    direction  = signal["direction"]
    score      = signal["score"]
    votes      = f"+{score}" if score > 0 else str(score)
    dir_block  = (
        "╔══════════════════╗\n║   📈  C A L L    ║\n╚══════════════════╝" if direction == "CALL"
        else "╔══════════════════╗\n║   📉   P U T    ║\n╚══════════════════╝"
    )
    payout      = get_payout(pair)
    stats       = load_stats()
    total       = stats["wins"] + stats["losses"]
    accuracy    = f"{winrate(stats['wins'], stats['losses']):.1f}%" if total >= 5 else "Building..."
    confidence  = signal.get("confidence_pct", None)
    conf_str    = f"{confidence}%" if confidence else "—"
    now         = datetime.now(timezone.utc)
    expiry_time = (now + timedelta(minutes=expiry_mins)).strftime("%H:%M:%S")

    # Get decisive reasons
    reasons     = signal.get("reasons", [])
    decisive    = [r for r in reasons if "Neutral" not in r and "Weak" not in r][:4]
    reasons_txt = "\n".join([f"   ✦ {r}" for r in decisive]) if decisive else "   ✦ Multiple indicators confirmed"

    mtf         = signal.get("mtf", {})
    mtf_overall = mtf.get("overall", "—")
    m15_data    = mtf.get("M15", {})
    m5_data     = mtf.get("M5", {})
    m15_str     = f"{m15_data.get('dir','—')} (strength {m15_data.get('strength',0)}/3)"
    m5_str      = f"{m5_data.get('dir','—')} age:{m5_data.get('age',0)} bars"
    mtf_emoji   = "📈" if mtf_overall == "UP" else "📉" if mtf_overall == "DOWN" else "➡️"
    m15_new_str = " 🆕" if m15_data.get("new_cross") else ""
    vol_level   = signal.get("volatility", "—")
    vol_emoji   = "✅" if vol_level == "NORMAL" else "⚡" if vol_level == "HIGH" else "😴" if vol_level == "LOW" else "❓"
    sr          = signal.get("sr", {})
    sr_signal   = sr.get("signal", "NONE")
    sup_price   = sr.get("sup_price")
    res_price   = sr.get("res_price")
    sup_touches = sr.get("sup_touches", 0)
    res_touches = sr.get("res_touches", 0)
    if sr.get("near_support"):
        sr_str = f"Support {sup_price} ({sup_touches} touches) 🟢"
    elif sr.get("near_resistance"):
        sr_str = f"Resistance {res_price} ({res_touches} touches) 🔴"
    else:
        sr_str = "No Key Level"
    div         = signal.get("divergence", "NONE")
    div_str     = f"🔄 {div}" if div != "NONE" else "None"
    martingale  = f"\n⚠️ _Consider 2x stake — previous signal lost_" if consecutive_losses > 0 else ""

    ob          = signal.get("ob", {})
    fvg         = signal.get("fvg", {})
    ob_str      = f"{ob.get('type','—')} zone {ob.get('zone_low','—')}–{ob.get('zone_high','—')}" if ob.get("signal") != "NONE" else "None"
    fvg_str     = f"{fvg.get('type','—')} gap {fvg.get('filled_pct',0)}% filled" if fvg.get("signal") != "NONE" else "None"

    return (
        f"🎿 *SKII PRO SIGNALS*\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
        f"`{dir_block}`\n\n"
        f"🪙 *Pair*       »  `{pair}`\n"
        f"⏱ *Expiry*    »  `{expiry_mins} Minute`\n"
        f"🕐 *Place At*  »  `{time_str}`\n"
        f"🔒 *Closes At* »  `{expiry_time} UTC`\n"
        f"💵 *Payout*    »  `{payout}`\n"
        f"🎯 *Accuracy*  »  `{accuracy}`\n"
        f"🧠 *Sim Conf*  »  `{conf_str}`\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"📊 *ANALYSIS*\n\n"
        f"{reasons_txt}\n\n"
        f"🔬 *SMART FILTERS*\n"
        f"   {mtf_emoji} M15 Trend  : `{m15_str}{m15_new_str}`\n"
        f"   ⚡ M5 MACD   : `{m5_str}`\n"
        f"   {vol_emoji} Volatility : `{vol_level}`\n"
        f"   📍 S&R Level : `{sr_str}`\n"
        f"   🔄 Divergence: `{div_str}`\n"
        f"   🏦 Order Block: `{ob_str}`\n"
        f"   📊 FVG        : `{fvg_str}`\n\n"
        f"📉 *Key Values*\n"
        f"   • RSI (7)      : `{signal.get('rsi', 'N/A')}`\n"
        f"   • Stoch (5,3,3): `{signal.get('stoch_k', 'N/A')}`\n"
        f"   • BB Lower     : `{signal.get('bb_lower', 'N/A')}`\n\n"
        f"{'🔥🔥🔥' if confidence and confidence >= 80 else '🔥🔥' if confidence and confidence >= 70 else '🔥'} *{'HIGH' if confidence and confidence >= 80 else 'GOOD' if confidence and confidence >= 70 else 'MODERATE'} CONFIDENCE* — {conf_str} sim\n"
        f"{martingale}\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"⏳ _Result posts automatically at {expiry_time} UTC_\n\n"
        f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
    )



def build_result_msg(pair, direction, entry_price, exit_price, pips_label, stats) -> str:
    is_win = (direction == "CALL" and exit_price > entry_price) or \
             (direction == "PUT"  and exit_price < entry_price)
    wr     = winrate(stats["wins"], stats["losses"])
    total  = stats["wins"] + stats["losses"]
    arrow  = "📈" if exit_price > entry_price else "📉"
    header = (
        "╔══════════════════╗\n║  ✅  W I N  🏆   ║\n╚══════════════════╝" if is_win
        else "╔══════════════════╗\n║  ❌  L O S S     ║\n╚══════════════════╝"
    )
    footer = (
        "💪 *Another one! Keep following the signals.*" if is_win
        else "📊 *Losses are part of the game. Stay disciplined.*"
    )
    return (
        f"🎿 *SKII PRO SIGNALS*\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
        f"`{header}`\n\n"
        f"🪙 *Pair*        »  `{pair}`\n"
        f"📌 *Direction*  »  `{'📈 CALL' if direction == 'CALL' else '📉 PUT'}`\n"
        f"🔓 *Entry*      »  `{entry_price}`\n"
        f"🔒 *Exit*       »  `{exit_price}` {arrow}\n"
        f"📏 *Movement*  »  `{pips_label}`\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"📈 *SESSION STATS*\n\n"
        f"   ✅  Wins      »  *{stats['wins']}*\n"
        f"   ❌  Losses   »  *{stats['losses']}*\n"
        f"   🎯  Win Rate »  *{wr:.1f}%*  ({total} trades)\n\n"
        f"   {win_bar(wr)}\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"{footer}\n\n"
        f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
    )


def build_stats_msg(stats: dict) -> str:
    total    = stats["wins"] + stats["losses"]
    wr       = winrate(stats["wins"], stats["losses"])
    d_wr     = winrate(stats["daily_wins"], stats["daily_losses"])
    d_tot    = stats["daily_wins"] + stats["daily_losses"]
    time_str = datetime.now(timezone.utc).strftime("%d %b %Y  •  %H:%M UTC")
    verdict  = (
        "🔥 *Exceptional run! Skii Pro is delivering.*"        if wr >= 75 else
        "⚡ *Strong performance. Keep following the signals.*"  if wr >= 60 else
        "📊 *Solid. Consistency is everything.*"               if wr >= 50 else
        "💪 *Variance is normal. The edge plays out over time.*"
    )
    return (
        f"🎿 *SKII PRO SIGNALS*\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"📊 *PERFORMANCE REPORT*\n"
        f"🕐 _{time_str}_\n\n"
        f"📅 *TODAY*\n"
        f"   ✅  Wins      »  *{stats['daily_wins']}*\n"
        f"   ❌  Losses   »  *{stats['daily_losses']}*\n"
        f"   🎯  Win Rate »  *{d_wr:.1f}%*  ({d_tot} trades)\n"
        f"   {win_bar(d_wr)}\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"📆 *ALL TIME*\n"
        f"   ✅  Wins      »  *{stats['wins']}*\n"
        f"   ❌  Losses   »  *{stats['losses']}*\n"
        f"   🎯  Win Rate »  *{wr:.1f}%*  ({total} trades)\n"
        f"   {win_bar(wr)}\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"{verdict}\n\n"
        f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
    )


def build_weekly_msg(stats: dict) -> str:
    w_wins   = stats.get("weekly_wins", 0)
    w_losses = stats.get("weekly_losses", 0)
    w_total  = w_wins + w_losses
    w_wr     = winrate(w_wins, w_losses)
    all_wr   = winrate(stats["wins"], stats["losses"])
    week     = datetime.now(timezone.utc).strftime("Week %W, %Y")
    verdict  = (
        "🔥 *What a week! Absolutely elite performance.*"              if w_wr >= 75 else
        "⚡ *Solid week. The signals are working.*"                     if w_wr >= 60 else
        "📊 *Decent week. More to come.*"                              if w_wr >= 50 else
        "💪 *Tough week but we stay consistent. New week, new pips.*"
    )
    return (
        f"🎿 *SKII PRO SIGNALS*\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"📆 *WEEKLY RECAP*  |  _{week}_\n\n"
        f"   ✅  Wins      »  *{w_wins}*\n"
        f"   ❌  Losses   »  *{w_losses}*\n"
        f"   📊  Total    »  *{w_total} trades*\n"
        f"   🎯  Win Rate »  *{w_wr:.1f}%*\n\n"
        f"   {win_bar(w_wr)}\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"📈 *All Time Win Rate »  {all_wr:.1f}%*\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"{verdict}\n\n"
        f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
    )


# ── Continuous Scanner Job ────────────────────────────────────────────────────
async def scanner_job(context: ContextTypes.DEFAULT_TYPE):
    """Full smart scanner: MTF + Volatility + S&R + Divergence + News + Drawdown."""
    if bot_paused:
        return

    global last_signal_time, last_signal_message_id, consecutive_losses

    # ── Drawdown protection ───────────────────────────────────────────────────
    stats = load_stats()
    if check_drawdown_protection(stats):
        return

    # ── News filter ───────────────────────────────────────────────────────────
    if news_filter_enabled and is_news_time():
        print("⏰ News window — skipping scan")
        return

    # ── Time filter ───────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    if time_filter_enabled:
        good_hours = get_best_hours()
        if now.hour not in good_hours:
            return

    # ── Global cooldown ───────────────────────────────────────────────────────
    if last_signal_time and (now - last_signal_time).total_seconds() < GLOBAL_SIGNAL_COOLDOWN:
        return

    loop        = asyncio.get_event_loop()
    best_signal = None
    best_score  = 0

    for pair in active_pairs:
        if pair_on_cooldown(pair):
            continue

        ticker = OTC_PAIRS[pair]

        # ── Volatility filter ─────────────────────────────────────────────────
        vol = await loop.run_in_executor(None, check_volatility, ticker)
        if not vol["tradeable"]:
            print(f"⚡ {pair} skipped — volatility {vol['level']}")
            continue

        # ── Generate M1 signal ────────────────────────────────────────────────
        try:
            signal = await loop.run_in_executor(None, generate_signal, pair)
        except Exception as e:
            print(f"⚠️ Data fetch failed for {pair}: {e}")
            continue

        if abs(signal["score"]) < 2:
            log_signal_decision(pair, signal["direction"], abs(signal["score"]), 0, False, "Score too low", signal.get("reasons", []))
            continue

        direction = signal["direction"]

        # ── Multi-timeframe confirmation ──────────────────────────────────────
        mtf = await loop.run_in_executor(None, get_mtf_trend, ticker)
        signal["mtf"] = mtf

        # M15 strongly against = always block (highest weight timeframe)
        if mtf.get("m15_override") and mtf["m15_dir"] != mtf.get("overall"):
            if (direction == "CALL" and mtf["m15_dir"] == "DOWN") or \
               (direction == "PUT"  and mtf["m15_dir"] == "UP"):
                log_signal_decision(pair, direction, abs(signal["score"]), 0, False, f"M15 strong override ({mtf['m15_dir']})", signal.get("reasons", []))
                print(f"⛔ {pair} {direction} hard blocked — M15 strongly {mtf['m15_dir']}")
                continue

        if mtf["overall"] != "NEUTRAL":
            if (direction == "CALL" and mtf["overall"] == "DOWN") or \
               (direction == "PUT"  and mtf["overall"] == "UP"):
                log_signal_decision(pair, direction, abs(signal["score"]), 0, False, f"MTF against ({mtf['overall']})", signal.get("reasons", []))
                print(f"⛔ {pair} {direction} blocked — MTF says {mtf['overall']}")
                continue
            # MTF agrees — boost score based on weighted strength
            if (direction == "CALL" and mtf["overall"] == "UP") or \
               (direction == "PUT"  and mtf["overall"] == "DOWN"):
                boost = min(2, mtf["strength"] // 3)
                signal["score"] += boost
                m15_new = "🆕 New crossover!" if mtf.get("M15", {}).get("new_cross") else ""
                signal["reasons"].append(f"MTF Aligned {mtf['overall']} (score:{mtf['weighted_score']}) {m15_new}")

        # ── Support & Resistance ──────────────────────────────────────────────
        sr = await loop.run_in_executor(None, get_support_resistance, ticker)
        signal["sr"] = sr
        if sr["signal"] != "NONE":
            if sr["signal"] == direction:
                boost   = sr.get("strength", 1)
                signal["score"] += boost
                touches = sr.get("sup_touches", 0) if sr["near_support"] else sr.get("res_touches", 0)
                label   = f"Near Support ({touches} touches)" if sr["near_support"] else f"Near Resistance ({touches} touches)"
                signal["reasons"].append(f"S&R: {label} ✅")
            elif sr["signal"] != direction:
                signal["score"] -= 1
                signal["reasons"].append(f"S&R: Against level ⚠️")

        # ── RSI Divergence ────────────────────────────────────────────────────
        divergence = await loop.run_in_executor(None, detect_divergence, ticker)
        signal["divergence"] = divergence
        if divergence == direction:
            signal["score"] += 1
            signal["reasons"].append(f"RSI Divergence: {divergence} confirmed 🔄")
        elif divergence != "NONE" and divergence != direction:
            log_signal_decision(pair, direction, abs(signal["score"]), 0, False, "Divergence against", signal.get("reasons", []))
            continue

        # ── Order Block ───────────────────────────────────────────────────────
        ob = await loop.run_in_executor(None, detect_order_blocks, ticker)
        signal["ob"] = ob
        if ob["signal"] != "NONE":
            if ob["signal"] == direction:
                signal["score"] += ob["strength"]
                signal["reasons"].append(f"OB Retest: {ob['type']} zone ({ob['zone_low']}–{ob['zone_high']}) 🏦")
            elif ob["signal"] != direction:
                signal["score"] -= 1
                signal["reasons"].append(f"OB Against: {ob['type']} zone ⚠️")

        # ── Fair Value Gap ────────────────────────────────────────────────────
        fvg = await loop.run_in_executor(None, detect_fvg, ticker)
        signal["fvg"] = fvg
        if fvg["signal"] != "NONE":
            if fvg["signal"] == direction:
                signal["score"] += 1
                signal["reasons"].append(f"FVG Fill: {fvg['type']} gap ({fvg['filled_pct']}% filled) 📊")
            elif fvg["signal"] != direction:
                signal["score"] -= 1
                signal["reasons"].append(f"FVG Against ⚠️")

        # ── Candlestick filter ────────────────────────────────────────────────
        if candle_filter_enabled:
            pattern = await loop.run_in_executor(None, detect_candle_pattern, ticker)
            if pattern != "NONE" and pattern == direction:
                signal["score"] += 1
                signal["reasons"].append(f"Candle Pattern: {pattern} ✅")
            elif pattern != "NONE" and pattern != direction:
                log_signal_decision(pair, direction, abs(signal["score"]), 0, False, "Candle against", signal.get("reasons", []))
                continue

        # Store volatility info
        signal["volatility"] = vol["level"]

        if abs(signal["score"]) > best_score:
            best_score  = abs(signal["score"])
            best_signal = (pair, signal)

    if not best_signal:
        print("🔍 Scan complete — no signal passed all filters.")
        return

    pair, signal  = best_signal
    direction     = signal["direction"]
    entry_price   = signal["entry_price"]

    # ── Monte Carlo confidence ────────────────────────────────────────────────
    live_wr    = winrate(stats["wins"], stats["losses"]) if (stats["wins"] + stats["losses"]) >= 5 else 65.0
    confidence = monte_carlo_confidence(
        win_rate        = live_wr,
        indicator_score = abs(signal["score"]),
        streak_type     = streak["type"] or "none",
        streak_count    = streak["count"],
    )

    if confidence < 65:
        log_signal_decision(pair, direction, abs(signal["score"]), confidence, False, "Low MC confidence", signal.get("reasons", []))
        print(f"🧠 Skipped — MC confidence: {confidence}% ({pair} {direction})")
        return

    signal["confidence_pct"] = confidence
    log_signal_decision(pair, direction, abs(signal["score"]), confidence, True, "Fired", signal.get("reasons", []))
    mtf_str = signal.get("mtf", {}).get("overall", "—")
    print(f"✅ Signal fired: {pair} {direction} | Score:{signal['score']} | MC:{confidence}% | MTF:{mtf_str} | Vol:{signal.get('volatility','—')}")

    signal["confidence_pct"] = confidence
    log_signal_decision(pair, direction, abs(signal["score"]), confidence, True, "Fired", signal.get("reasons", []))
    print(f"🧠 Monte Carlo confidence: {confidence}% — firing signal ({pair} {direction})")

    # Set cooldowns
    set_cooldown(pair)
    last_signal_time = datetime.now(timezone.utc)

    # 🎮 Prediction game in free group
    if FREE_GROUP_ID and FREE_GROUP_ID != "@yourfreegroupusername":
        try:
            await context.bot.send_poll(
                chat_id=FREE_GROUP_ID,
                question=f"🎯 PREDICTION GAME — {pair}\nCALL or PUT? Signal drops to paid members in 2 min!",
                options=["📈 CALL", "📉 PUT"],
                is_anonymous=False,
                open_period=120,
            )
            context.job_queue.run_once(
                reveal_prediction_job,
                when=120,
                data={"pair": pair, "direction": direction},
            )
        except Exception as e:
            print(f"⚠️ Prediction poll failed: {e}")

    time_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    text     = build_signal_msg(pair, signal, time_str)

    try:
        sent_msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
        last_signal_message_id = sent_msg.message_id

        await asyncio.sleep(1)
        await context.bot.send_poll(
            chat_id=CHANNEL_ID,
            question=f"Did you take this trade? ({pair} {direction})",
            options=["✅ Yes, I'm in!", "❌ Missed it", "👀 Watching"],
            is_anonymous=False,
        )

        context.job_queue.run_once(
            result_job,
            when=expiry_mins * 60,
            data={"pair": pair, "direction": direction, "entry_price": entry_price, "indicators_fired": signal.get("indicators_fired", [])},
        )

        print(f"✅ Signal fired: {pair} {direction} ({signal['score']}/8)")

    except Exception as e:
        print(f"⚠️ Failed to send signal: {e}")



async def reveal_prediction_job(context: ContextTypes.DEFAULT_TYPE):
    data      = context.job.data
    pair      = data["pair"]
    direction = data["direction"]
    emoji     = "📈 CALL" if direction == "CALL" else "📉 PUT"
    text      = build_msg(
        f"🎯 PREDICTION REVEAL — {pair}",
        f"The signal was: *{emoji}*\n\nDid you get it right? 👀",
        "🔒 Paid members already placed this trade!\nGet signals before the reveal 👇\n👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)


async def result_job(context: ContextTypes.DEFAULT_TYPE):
    data             = context.job.data
    pair             = data["pair"]
    direction        = data["direction"]
    entry_price      = data["entry_price"]
    indicators_fired = data.get("indicators_fired", [])

    try:
        loop       = asyncio.get_event_loop()
        exit_price = await loop.run_in_executor(None, get_current_price, pair)
    except Exception as exc:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=f"⚠️ Result error: `{exc}`", parse_mode="Markdown")
        return

    # Smart movement calculation based on asset type
    diff = abs(exit_price - entry_price)
    is_win = (exit_price - entry_price > 0) if direction == "CALL" else (exit_price - entry_price < 0)
    if "BTC" in pair or "ETH" in pair:
        pips = round(diff, 2)
        pips_label = f"${pips}"
    elif "Gold" in pair or "Silver" in pair or "Oil" in pair:
        pips = round(diff, 3)
        pips_label = f"${pips}"
    else:
        pips = round(diff * 10000, 1)
        pips_label = f"{pips} pips"

    # Track consecutive losses for martingale
    global consecutive_losses
    if is_win:
        consecutive_losses = 0
    else:
        consecutive_losses += 1

    stats = load_stats()
    stats = maybe_reset_daily(stats)
    stats = maybe_reset_weekly(stats)
    if is_win:
        stats["wins"] += 1; stats["daily_wins"] += 1; stats["weekly_wins"] += 1
    else:
        stats["losses"] += 1; stats["daily_losses"] += 1; stats["weekly_losses"] += 1
    save_stats(stats)

    result_text = build_result_msg(pair, direction, entry_price, exit_price, pips_label, stats)

    # Post to paid group
    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=result_text,
        parse_mode="Markdown"
    )

    # Add 🔥 reaction to WIN in paid group
    if is_win:
        try:
            from telegram import ReactionTypeEmoji
            await context.bot.set_message_reaction(
                chat_id=CHANNEL_ID,
                message_id=(await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text="🔥",
                )).message_id - 1,
                reaction=[ReactionTypeEmoji("🔥")]
            )
        except Exception:
            pass

    # Post to free group — FOMO for wins, standard for losses
    if is_win:
        free_text = (
            f"🎿 *SKII PRO SIGNALS*\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            f"💰 *PAID MEMBERS JUST WON!*\n\n"
            f"   ✅  Another WIN in the paid group!\n"
            f"   😤  You missed it...\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"📊 *Current Win Rate: {winrate(stats['wins'], stats['losses']):.1f}%*\n"
            f"✅ Wins: *{stats['wins']}*  ❌ Losses: *{stats['losses']}*\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"⏰ *Stop missing wins. Join now!*\n\n"
            f"👉 *whop.com/skiiprosignals*\n\n"
            f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
        )
    else:
        free_text = (
            f"🎿 *SKII PRO SIGNALS*\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            f"❌ *Result just posted in the paid group!*\n\n"
            f"   🎯  Win Rate »  *{winrate(stats['wins'], stats['losses']):.1f}%*\n"
            f"   ✅  Wins     »  *{stats['wins']}*\n"
            f"   ❌  Losses  »  *{stats['losses']}*\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"🔒 *Want the signals before results?*\n"
            f"👉 *whop.com/skiiprosignals*\n\n"
            f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
        )
    try:
        await send_to_free_group(context.bot, free_text)
    except Exception:
        pass

    # Streak tracker
    streak_count, streak_type, is_milestone = update_streak(is_win)
    if is_milestone:
        if streak_type == "win":
            streak_emoji = "🔥" * min(streak_count, 5)
            streak_msg = build_msg(
                f"{streak_emoji} {streak_count} WINS IN A ROW!",
                f"The signals are on fire right now!\nDon't miss the next one! 💰\n\n"
                f"   🎯  Win Rate »  *{winrate(stats['wins'], stats['losses']):.1f}%*",
                "🔒 Paid members are cashing in. Stay locked in!"
            )
        else:
            streak_msg = build_msg(
                f"📊 {streak_count} LOSSES IN A ROW",
                f"Variance happens — every signal service goes through it.\n"
                f"Stay patient and trust the process. 💪\n\n"
                f"   🎯  Win Rate »  *{winrate(stats['wins'], stats['losses']):.1f}%*",
                "📈 The edge plays out over time. Keep following the signals."
            )
        await context.bot.send_message(chat_id=CHANNEL_ID, text=streak_msg, parse_mode="Markdown")

    await check_milestones(context, stats)

    # Log trade to history and update indicator weights
    log_trade(pair, direction, entry_price, exit_price, is_win, indicators_fired)
    # Update best streak ever
    if streak["count"] > best_streak["count"]:
        best_streak["count"] = streak["count"]
        best_streak["type"]  = streak["type"]


async def stats_job(context: ContextTypes.DEFAULT_TYPE):
    stats = load_stats(); stats = maybe_reset_daily(stats); save_stats(stats)
    text  = build_stats_msg(stats)
    # Post to both groups
    await context.bot.send_message(chat_id=CHANNEL_ID,    text=text, parse_mode="Markdown")
    await send_to_free_group(context.bot, text)


async def daily_tip_job(context: ContextTypes.DEFAULT_TYPE):
    """Posts a trading tip every weekday morning at 07:00 UTC."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5 or now.hour != 7:
        return

    tip, category = TRADING_TIPS[now.timetuple().tm_yday % len(TRADING_TIPS)]
    text = (
        f"🎿 *SKII PRO SIGNALS*\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
        f"📚 *DAILY TRADING TIP*\n"
        f"🏷 _{category}_\n\n"
        f"{tip}\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"💬 _Study the craft. The signals do the work, but knowledge keeps you in the game._\n\n"
        f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
    )
    # Post to both groups
    await context.bot.send_message(chat_id=CHANNEL_ID,    text=text, parse_mode="Markdown")
    await send_to_free_group(context.bot, text)


async def leaderboard_job(context: ContextTypes.DEFAULT_TYPE):
    """Posts leaderboard every day at 20:00 UTC."""
    now = datetime.now(timezone.utc)
    if now.hour != 20:
        return
    lb   = load_leaderboard()
    text = build_leaderboard_msg(lb)
    # Post to both groups
    await context.bot.send_message(chat_id=CHANNEL_ID,    text=text, parse_mode="Markdown")
    await send_to_free_group(context.bot, text)



    if datetime.now(timezone.utc).weekday() != 0:
        return
    stats = load_stats(); stats = maybe_reset_weekly(stats); save_stats(stats)
    await context.bot.send_message(chat_id=CHANNEL_ID, text=build_weekly_msg(stats), parse_mode="Markdown")


async def weekly_recap_job(context: ContextTypes.DEFAULT_TYPE):
    """Posts weekly recap every Monday at 08:00 UTC."""
    now = datetime.now(timezone.utc)
    if now.weekday() != 0 or now.hour != 8:
        return
    stats = load_stats()
    stats = maybe_reset_weekly(stats)
    save_stats(stats)
    await context.bot.send_message(chat_id=CHANNEL_ID, text=build_weekly_msg(stats), parse_mode="Markdown")


async def market_open_job(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return
    if now.hour == 7:
        session_name = "🇬🇧 LONDON SESSION OPEN"
        hype = "London is live — liquidity is high and the scanner is running hot. Let's get to work! 💼"
    elif now.hour == 12:
        session_name = "🇺🇸 NEW YORK SESSION OPEN"
        hype = "New York just opened — maximum volatility, maximum opportunity! 🗽"
    else:
        return

    stats = load_stats()
    wr    = winrate(stats["wins"], stats["losses"])
    text  = build_msg(
        f"🔔 {session_name}",
        f"   🔍  Scanner *ACTIVE* on all 22 pairs\n"
        f"   ⏱  Expiry: *{expiry_mins} minutes*\n"
        f"   🎯  Win Rate: *{wr:.1f}%*\n\n"
        f"_{hype}_",
    )
    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")


# ── Welcome Handler ───────────────────────────────────────────────────────────
async def welcome_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result: ChatMemberUpdated = update.chat_member
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    if not (old_status in ("left", "kicked", "restricted") and new_status in ("member", "administrator")):
        return

    user    = result.new_chat_member.user
    name    = user.first_name or "Trader"
    mention = f"[{name}](tg://user?id={user.id})"
    stats   = load_stats()
    total   = stats["wins"] + stats["losses"]
    wr      = winrate(stats["wins"], stats["losses"])
    chat_id = str(result.chat.id)
    is_free = str(FREE_GROUP_ID).replace("@", "") in str(chat_id).replace("@", "")

    if is_free:
        # Free group welcome
        await context.bot.send_message(chat_id=result.chat.id, parse_mode="Markdown", text=(
            f"🎿 *SKII PRO SIGNALS*\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            f"👋 *Welcome,* {mention}*!*\n\n"
            f"You just joined the *Skii Pro Free Group* — where we post WIN/LOSS results, daily tips, stats and leaderboard.\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"📈 *CURRENT RECORD*\n\n"
            f"   ✅  Wins      »  *{stats['wins']}*\n"
            f"   ❌  Losses   »  *{stats['losses']}*\n"
            f"   🎯  Win Rate »  *{wr:.1f}%*  ({total} trades)\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"🔒 *Want the actual CALL & PUT signals?*\n\n"
            f"Upgrade to Skii Pro and get every signal live before the result drops!\n\n"
            f"👉 *whop.com/skiiprosignals*\n\n"
            f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
        ))
    else:
        # Paid group welcome
        await context.bot.send_message(chat_id=result.chat.id, parse_mode="Markdown", text=(
            f"🎿 *SKII PRO SIGNALS*\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            f"👋 *Welcome to the group,* {mention}*!*\n\n"
            f"You just joined *Skii Pro Signals* — a premium OTC signal community powered by an 8-indicator live market scanner.\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"📌 *WHAT TO EXPECT*\n\n"
            f"   🔍  Bot scans *22 OTC pairs* constantly\n"
            f"   ⚡  Signals fire every *5 minutes*\n"
            f"   ⏱  Expiry: *5 minutes* per trade\n"
            f"   ✅  Auto WIN/LOSS result posted\n"
            f"   📊  Stats report every *6 hours*\n"
            f"   📚  Daily trading tips\n"
            f"   🏆  Leaderboard & streak tracker\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"📈 *CURRENT RECORD*\n\n"
            f"   ✅  Wins      »  *{stats['wins']}*\n"
            f"   ❌  Losses   »  *{stats['losses']}*\n"
            f"   🎯  Win Rate »  *{wr:.1f}%*  ({total} trades)\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"💡 *HOW TO TRADE*\n\n"
            f"   1️⃣  Signal drops — open Pocket Option\n"
            f"   2️⃣  Select the pair shown\n"
            f"   3️⃣  Place CALL 📈 or PUT 📉\n"
            f"   4️⃣  Set expiry to *5 minutes*\n"
            f"   5️⃣  Wait for the result ✅❌\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"🔥 *Welcome to the team. Let's get these pips!* 🎿\n\n"
            f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
        ))

        # Auto DM the new free group member
        try:
            await context.bot.send_message(
                chat_id=user.id,
                parse_mode="Markdown",
                text=(
                    f"👋 Hey {name}! Welcome to Skii Pro Signals free group!\n\n"
                    f"You're seeing our WIN/LOSS results, tips and stats — but the actual CALL & PUT signals go to paid members first. 🔒\n\n"
                    f"🎿 *SKII PRO SIGNALS*\n"
                    f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
                    f"   ✅  {wr:.1f}% Win Rate\n"
                    f"   📡  22 OTC Pairs Scanned\n"
                    f"   ⚡  Signal Every 5 Minutes\n"
                    f"   🤖  Fully Automated 24/7\n\n"
                    f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                    f"💰 *Plans*\n\n"
                    f"   📅  Monthly  »  *$25/month*\n"
                    f"   ♾️  Lifetime »  *$150 one-time*\n\n"
                    f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                    f"Ready to get the signals? 👇\n"
                    f"👉 *whop.com/skiiprosignals*\n\n"
                    f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
                )
            )
        except Exception:
            pass  # User may have DMs disabled


# ── Member Commands (work inside the group) ───────────────────────────────────
async def cmd_mywin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    uid      = str(user.id)
    name     = user.first_name or "Trader"
    lb       = update_leaderboard(uid, name, is_win=True)
    entry    = lb[uid]
    wr       = winrate(entry["wins"], entry["losses"])
    total    = entry["wins"] + entry["losses"]
    await update.message.reply_text(
        f"✅ *WIN logged, {name}!*\n\n"
        f"Your record: ✅ {entry['wins']}W  ❌ {entry['losses']}L  🎯 {wr:.0f}%  ({total} trades)\n\n"
        f"_Keep it up! Use /leaderboard to see the rankings._",
        parse_mode="Markdown"
    )

async def cmd_myloss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    uid      = str(user.id)
    name     = user.first_name or "Trader"
    lb       = update_leaderboard(uid, name, is_win=False)
    entry    = lb[uid]
    wr       = winrate(entry["wins"], entry["losses"])
    total    = entry["wins"] + entry["losses"]
    await update.message.reply_text(
        f"❌ *LOSS logged, {name}.*\n\n"
        f"Your record: ✅ {entry['wins']}W  ❌ {entry['losses']}L  🎯 {wr:.0f}%  ({total} trades)\n\n"
        f"_Stay disciplined. Use /leaderboard to see the rankings._",
        parse_mode="Markdown"
    )

async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lb   = load_leaderboard()
    text = build_leaderboard_msg(lb)
    await update.message.reply_text(text, parse_mode="Markdown")



def log_admin(cmd: str, args: str = ""):
    """Prints admin command to Railway deploy logs."""
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    extra = f" {args}" if args else ""
    print(f"👤 ADMIN  [{now}]  /{cmd}{extra}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("start", "")
    await update.message.reply_text(
        "🎿 *SKII PRO SIGNALS*\n"
        "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        "🔧 *ADMIN PANEL*\n\n"
        "🔴 *TikTok Live*\n"
        "/live [link] — Go live announcement 🔴\n"
        "/endlive — End live + recap ⚫\n"
        "/countdown [min] — Signal hype timer ⏳\n"
        "/scoreboard — Live stats board 📊\n"
        "/lastwins — Show last 5 wins 🏆\n"
        "/promo — Sales message 💎\n"
        "/slots [n] — Limited spots urgency ⚠️\n"
        "/discount [%] — Live discount 🔥\n"
        "/giveaway [prize] — Giveaway post 🎁\n"
        "/challenge [n] — Trade challenge 🎯\n"
        "/shoutout [@user] — Member shoutout 🏆\n"
        "/members [n] — Member count flex 🔥\n"
        "/link — Post Whop link 🔗\n\n"
        "📡 *Scanner Control*\n"
        "/pause — Pause all signals ⏸\n"
        "/resume — Resume signals ▶️\n"
        "/signal — Force scan now 🔍\n"
        "/expiry [min] — Change expiry ⏱\n"
        "/cooldown [min] — Pair cooldown ⏳\n"
        "/setpairs — Choose pairs to scan 🎯\n"
        "/resetpairs — Restore all 22 pairs 🔄\n\n"
        "📊 *Stats & Info*\n"
        "/status — Full live bot status\n"
        "/today — Today's trade history 📅\n"
        "/winstreak — Current & best streak 🔥\n"
        "/drawdown — Performance check 📉\n"
        "/revenue [n] — Revenue tracker 💰\n"
        "/weights — Indicator learning weights 🧠\n"
        "/bestpairs — Best performing pairs 📊\n"
        "/sessions — Win rate by hour 🕐\n"
        "/filters — Toggle smart filters 🔧\n"
        "/losslimit [n] — Set daily loss limit 🛑\n"
        "/stats — Post stats to group\n"
        "/weekly — Post weekly recap 📆\n\n"
        "📣 *Communication*\n"
        "/broadcast [msg] — Announcement 📣\n"
        "/warn [msg] — Market warning ⚠️\n"
        "/maintenance — Maintenance mode 🔧\n"
        "/tip [msg] — Post custom tip 📚\n"
        "/motivate — Motivation message 💪\n\n"
        "🎮 *Fun & Engagement*\n"
        "/pin — Pin last signal 📌\n\n"
        "🛠 *Manual Overrides*\n"
        "/forceresult WIN/LOSS — Manual result ✅\n"
        "/reset — Reset all stats to zero 🔄",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("status", "")
    stats   = load_stats()
    total   = stats["wins"] + stats["losses"]
    wr      = winrate(stats["wins"], stats["losses"])
    on_cd   = [p for p in active_pairs if pair_on_cooldown(p)]
    ready   = [p for p in active_pairs if not pair_on_cooldown(p)]
    paused_status = "⏸ PAUSED" if bot_paused else "✅ RUNNING"

    # Format pair lists
    ready_str  = "\n".join([f"   🟢 {p}" for p in ready])   or "   None"
    on_cd_str  = "\n".join([f"   🔴 {p}" for p in on_cd])   or "   None"

    await update.message.reply_text(
        f"🎿 *Skii Pro Signals — Live Status*\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *Bot*          : {paused_status}\n"
        f"📡 *Scanner*      : every 2 min\n"
        f"🎯 *Min score*    : {min_score}/8\n"
        f"⏱ *Expiry*       : {expiry_mins} min\n"
        f"⏳ *Cooldown*     : {pair_cooldown//60} min\n"
        f"🔢 *Signals/hr*   : {signals_this_hour()}/{MAX_SIGNALS_PER_HOUR}\n"
        f"🔥 *Streak*       : {streak['count']} {streak['type'] or 'none'}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Performance*\n"
        f"   Trades    : {total}\n"
        f"   Win Rate  : {wr:.1f}%\n"
        f"   Today     : {stats['daily_wins']}W / {stats['daily_losses']}L\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🟢 *Ready to scan* ({len(ready)}/{len(active_pairs)}):\n{ready_str}\n\n"
        f"🔴 *On cooldown* ({len(on_cd)}):\n{on_cd_str}",
        parse_mode="Markdown"
    )

async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("signal", "")
    await scanner_job(context)
    await update.message.reply_text("🔍 Scanner triggered — signal posted if setup found.")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("stats", "")
    await stats_job(context)
    await update.message.reply_text("📊 Stats posted to group.")

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("weekly", "")
    stats = load_stats()
    await context.bot.send_message(chat_id=CHANNEL_ID, text=build_weekly_msg(stats), parse_mode="Markdown")
    await update.message.reply_text("📆 Weekly recap posted.")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("reset", "")
    save_stats({
        "wins": 0, "losses": 0, "daily_wins": 0, "daily_losses": 0,
        "weekly_wins": 0, "weekly_losses": 0,
        "last_reset": today_str(), "last_week_reset": week_str(),
        "celebrated_trades": [], "celebrated_winrates": [],
    })
    await update.message.reply_text("🔄 All stats reset to zero.")


# ── TikTok Live Commands ──────────────────────────────────────────────────────
async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("live", " ".join(context.args) if context.args else "")
    tiktok_link = " ".join(context.args) if context.args else "tiktok.com/@skii"
    text = build_msg(
        "🔴 SKII IS LIVE ON TIKTOK!",
        "   📱  Jump on the stream — signals dropping live!\n"
        "   💰  Real trades, real results, in real time\n"
        "   👀  Watch the wins happen live",
        f"👉 *{tiktok_link}*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text("🔴 Live announcement posted!")


async def cmd_endlive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("endlive", "")
    stats = load_stats()
    d_wr  = winrate(stats["daily_wins"], stats["daily_losses"])
    text  = build_msg(
        "⚫ LIVE SESSION ENDED",
        f"Thanks for watching! Today's recap:\n\n"
        f"   ✅  Wins      »  *{stats['daily_wins']}*\n"
        f"   ❌  Losses   »  *{stats['daily_losses']}*\n"
        f"   🎯  Win Rate »  *{d_wr:.1f}%*",
        "🔒 Want signals on the next live?\n👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text("⚫ End live posted!")


async def cmd_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("countdown", " ".join(context.args) if context.args else "")
    mins = int(context.args[0]) if context.args else 5
    text = build_msg(
        f"⏳ SIGNAL DROPPING IN {mins} MINUTES!",
        "   📱  Open Pocket Option and get ready\n"
        "   🔒  Paid members get it first\n"
        "   👀  Free group gets the result after",
        "🔥 Don't miss it — join Skii Pro now!\n👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text(f"⏳ Countdown posted!")


async def cmd_lastwins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("lastwins", "")
    history = load_history()
    wins    = [t for t in history if t["result"] == "WIN"][-5:]
    if not wins:
        await update.message.reply_text("No wins recorded yet.")
        return
    rows  = "\n".join([f"   ✅  {t['pair']} {t['direction']} — {t['time']}" for t in reversed(wins)])
    stats = load_stats()
    wr    = winrate(stats["wins"], stats["losses"])
    text  = build_msg(
        "🏆 LAST 5 WINS",
        f"{rows}\n\n   🎯  Win Rate »  *{wr:.1f}%*",
        "🔒 Want these signals live?\n👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text("🏆 Last 5 wins posted!")


async def cmd_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("promo", "")
    stats = load_stats()
    total = stats["wins"] + stats["losses"]
    wr    = winrate(stats["wins"], stats["losses"])
    text  = build_msg(
        "💎 PREMIUM OTC SIGNAL SERVICE",
        f"   ✅  *{wr:.1f}%* Win Rate ({total} trades)\n"
        f"   ✅  22 OTC Pairs Scanned\n"
        f"   ✅  8 Independent Indicators\n"
        f"   ✅  Signal Every 5 Minutes\n"
        f"   ✅  Auto WIN/LOSS Detection\n"
        f"   ✅  24/7 Automated\n\n"
        f"💰 *Plans*\n"
        f"   📅  Monthly  »  *$25/month*\n"
        f"   ♾️  Lifetime »  *$150 one-time*",
        "👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text("📣 Promo posted!")


async def cmd_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("slots", " ".join(context.args) if context.args else "")
    slots = int(context.args[0]) if context.args else 10
    text  = build_msg(
        f"⚠️ ONLY {slots} SPOTS LEFT!",
        "   🔥  This price won't last long\n"
        "   ⏰  Limited availability\n"
        "   💎  Lock in your spot now",
        "👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text(f"⚠️ {slots} slots posted!")


async def cmd_discount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("discount", " ".join(context.args) if context.args else "")
    pct  = context.args[0] if context.args else "20"
    text = build_msg(
        f"🔥 LIVE SPECIAL — {pct}% OFF!",
        f"   ⏰  Today only — live viewers exclusive\n"
        f"   💰  Monthly now *${int(25*(1-int(pct)/100))}*\n"
        f"   ♾️  Lifetime now *${int(150*(1-int(pct)/100))}*",
        "👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text(f"🔥 {pct}% discount posted!")


async def cmd_scoreboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("scoreboard", "")
    stats = load_stats()
    total = stats["wins"] + stats["losses"]
    wr    = winrate(stats["wins"], stats["losses"])
    d_wr  = winrate(stats["daily_wins"], stats["daily_losses"])
    d_tot = stats["daily_wins"] + stats["daily_losses"]
    text  = build_msg(
        "📊 LIVE SCOREBOARD",
        f"📅 *Today*\n"
        f"   ✅  Wins      »  *{stats['daily_wins']}*\n"
        f"   ❌  Losses   »  *{stats['daily_losses']}*\n"
        f"   🎯  Win Rate »  *{d_wr:.1f}%*  ({d_tot} trades)\n\n"
        f"📆 *All Time*\n"
        f"   ✅  Wins      »  *{stats['wins']}*\n"
        f"   ❌  Losses   »  *{stats['losses']}*\n"
        f"   🎯  Win Rate »  *{wr:.1f}%*  ({total} trades)\n\n"
        f"   {win_bar(wr)}\n\n"
        f"🔥 *Streak: {streak['count']} {streak['type'] or 'none'}*",
        "🔒 Get the signals that made this happen!\n👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text("📊 Scoreboard posted!")


async def cmd_shoutout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("shoutout", " ".join(context.args) if context.args else "")
    if not context.args:
        await update.message.reply_text("Usage: `/shoutout @username great trading today!`", parse_mode="Markdown")
        return
    mention = context.args[0]
    msg     = " ".join(context.args[1:]) if len(context.args) > 1 else "killing it in the signals! 🔥"
    text    = build_msg(
        "🏆 MEMBER SHOUTOUT",
        f"Big up to *{mention}* — {msg}",
        "_This is what Skii Pro members are doing. Want in?_\n👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text("🏆 Shoutout posted!")


async def cmd_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("giveaway", " ".join(context.args) if context.args else "")
    prize = " ".join(context.args) if context.args else "1 week free access to Skii Pro"
    text  = build_msg(
        "🎁 GIVEAWAY — LIVE EXCLUSIVE!",
        f"   🏆  Prize: *{prize}*\n\n"
        f"   To enter:\n"
        f"   1️⃣  Follow on TikTok\n"
        f"   2️⃣  Comment 'SKII' on the live\n"
        f"   3️⃣  DM to claim if you win!",
        "🔥 Good luck everyone!"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text("🎁 Giveaway posted!")


async def cmd_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("challenge", " ".join(context.args) if context.args else "")
    trades = context.args[0] if context.args else "10"
    text   = build_msg(
        f"🎯 LIVE CHALLENGE — {trades} TRADES!",
        f"Skii just called *{trades} trades live* on TikTok.\n"
        f"Watch the results drop in real time!",
        "🔒 Want to trade along? Join Skii Pro!\n👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text("🎯 Challenge posted!")


async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("link", "")
    text = build_msg(
        "🔗 JOIN SKII PRO NOW",
        "   💎  Premium OTC Signals\n"
        "   📅  Monthly: *$25/month*\n"
        "   ♾️  Lifetime: *$150*",
        "👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text("🔗 Link posted!")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("pause", "")
    global bot_paused
    bot_paused = True
    await update.message.reply_text("⏸ Signals paused. Use /resume to restart.")
    await context.bot.send_message(
        chat_id=CHANNEL_ID, parse_mode="Markdown",
        text=build_msg("⏸ SIGNALS TEMPORARILY PAUSED", "_We'll be back shortly. Stay tuned!_")
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("resume", "")
    global bot_paused
    bot_paused = False
    await update.message.reply_text("▶️ Signals resumed!")
    await context.bot.send_message(
        chat_id=CHANNEL_ID, parse_mode="Markdown",
        text=build_msg(
            "▶️ SIGNALS BACK LIVE!",
            f"   🔍  Scanner active on all 22 pairs\n"
            f"   ⏱  Expiry: *{expiry_mins} minutes*\n"
            f"   🔥  Signals firing every 5 minutes"
        )
    )


async def cmd_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("expiry", " ".join(context.args) if context.args else "")
    global expiry_mins
    if not context.args:
        await update.message.reply_text(
            f"⏱ Current expiry is *{expiry_mins} minutes*.\n\nUsage: `/expiry 1` `/expiry 3` `/expiry 5`",
            parse_mode="Markdown"
        )
        return
    try:
        new_expiry = int(context.args[0])
        if new_expiry not in [1, 2, 3, 5, 10, 15]:
            await update.message.reply_text("⚠️ Use one of: 1, 2, 3, 5, 10, 15 minutes.", parse_mode="Markdown")
            return
        expiry_mins = new_expiry
        await update.message.reply_text(f"✅ Expiry updated to *{expiry_mins} minutes!*", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid. Example: `/expiry 5`", parse_mode="Markdown")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("broadcast", " ".join(context.args) if context.args else "")
    if not context.args:
        await update.message.reply_text("Usage: `/broadcast Your message here`", parse_mode="Markdown")
        return
    message = " ".join(context.args)
    text    = build_msg("📣 ANNOUNCEMENT", message)
    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
    await update.message.reply_text("✅ Broadcast sent!")


async def cmd_setpairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("setpairs", " ".join(context.args) if context.args else "")
    global active_pairs
    if not context.args:
        current = ", ".join(active_pairs)
        pairs_list = "\n".join([f"• `{p}`" for p in OTC_PAIRS.keys()])
        await update.message.reply_text(
            f"📡 *Active pairs:*\n{current}\n\n*All available pairs:*\n{pairs_list}\n\n"
            f"Usage: `/setpairs EUR/USD OTC GBP/USD OTC`",
            parse_mode="Markdown"
        )
        return
    requested = " ".join(context.args)
    new_pairs = [p for p in OTC_PAIRS.keys() if p.replace(" OTC","").replace("/","") in requested.replace("/","").replace(" ","").upper() or p in requested]
    if not new_pairs:
        await update.message.reply_text("⚠️ No valid pairs found. Use full names like `EUR/USD OTC`", parse_mode="Markdown")
        return
    active_pairs = new_pairs
    await update.message.reply_text(f"✅ *Active pairs updated!*\n\n" + "\n".join([f"• {p}" for p in active_pairs]), parse_mode="Markdown")


async def cmd_resetpairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("resetpairs", "")
    global active_pairs
    active_pairs = list(OTC_PAIRS.keys())
    await update.message.reply_text("✅ *All 9 pairs restored!*", parse_mode="Markdown")


async def cmd_setscore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("setscore", " ".join(context.args) if context.args else "")
    global min_score
    if not context.args:
        await update.message.reply_text(f"🎯 Current min score: *{min_score}/8*\n\nUsage: `/setscore 5`", parse_mode="Markdown")
        return
    try:
        val = int(context.args[0])
        if val < 1 or val > 8:
            await update.message.reply_text("⚠️ Score must be between 1 and 8.", parse_mode="Markdown")
            return
        min_score = val
        await update.message.reply_text(f"✅ *Min score updated to {min_score}/8!*\n\nSignals now fire when {min_score}+ indicators agree.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid number. Example: `/setscore 5`", parse_mode="Markdown")


async def cmd_setcooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("cooldown", " ".join(context.args) if context.args else "")
    global pair_cooldown
    if not context.args:
        await update.message.reply_text(f"⏱ Current cooldown: *{pair_cooldown//60} min*\n\nUsage: `/cooldown 10`", parse_mode="Markdown")
        return
    try:
        mins = int(context.args[0])
        if mins < 1 or mins > 60:
            await update.message.reply_text("⚠️ Cooldown must be between 1 and 60 minutes.", parse_mode="Markdown")
            return
        pair_cooldown = mins * 60
        await update.message.reply_text(f"✅ *Pair cooldown updated to {mins} minutes!*", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid number. Example: `/cooldown 10`", parse_mode="Markdown")


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("today", "")
    history = load_history()
    today   = today_str()
    trades  = [t for t in history if t.get("date") == today]
    if not trades:
        await update.message.reply_text("📅 No trades recorded today yet.", parse_mode="Markdown")
        return
    wins   = sum(1 for t in trades if t["result"] == "WIN")
    losses = len(trades) - wins
    wr     = winrate(wins, losses)
    rows   = ""
    for t in trades[-20:]:  # show last 20
        emoji = "✅" if t["result"] == "WIN" else "❌"
        rows += f"{emoji} `{t['time']}` — {t['pair']} {t['direction']} ({t['pips']} pips)\n"
    await update.message.reply_text(
        f"📅 *Today's Trades*\n\n{rows}\n"
        f"✅ {wins}W  ❌ {losses}L  🎯 {wr:.1f}%  ({len(trades)} total)",
        parse_mode="Markdown"
    )


async def cmd_winstreak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("winstreak", "")
    await update.message.reply_text(
        f"🔥 *Streak Info*\n\n"
        f"Current streak : *{streak['count']} {streak['type'] or 'none'}*\n"
        f"Best streak    : *{best_streak['count']} {best_streak['type'] or 'none'}*",
        parse_mode="Markdown"
    )


async def cmd_revenue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("revenue", " ".join(context.args) if context.args else "")
    global member_count
    if context.args:
        try:
            member_count = int(context.args[0])
        except ValueError:
            pass
    monthly_rev  = member_count * 25  # estimate $25/mo per member
    lifetime_rev = member_count * 150
    await update.message.reply_text(
        f"💰 *Revenue Tracker*\n\n"
        f"Members       : *{member_count}*\n\n"
        f"Est. Monthly  : *${monthly_rev}*  (@ $25/mo)\n"
        f"Est. Lifetime : *${lifetime_rev}*  (@ $150)\n\n"
        f"_Update member count: `/revenue 50`_",
        parse_mode="Markdown"
    )


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("warn", " ".join(context.args) if context.args else "")
    msg  = " ".join(context.args) if context.args else "Market conditions are unfavourable right now. Trade with caution."
    text = build_msg("⚠️ MARKET WARNING", msg)
    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
    await update.message.reply_text("⚠️ Warning posted!")


async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("maintenance", "")
    global bot_paused
    bot_paused = True
    text = build_msg(
        "🔧 MAINTENANCE MODE",
        "Signals are temporarily offline.\nWe'll be back shortly — thanks for your patience! 🙏"
    )
    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
    await update.message.reply_text("🔧 Maintenance mode on. Use /resume when ready.")


async def cmd_manualtip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("tip", " ".join(context.args) if context.args else "")
    if not context.args:
        await update.message.reply_text("Usage: `/tip Your tip here`", parse_mode="Markdown")
        return
    tip_text = " ".join(context.args)
    text     = build_msg("📚 TRADING TIP", f"💡 {tip_text}")
    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
    await update.message.reply_text("📚 Tip posted!")


async def cmd_drawdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("drawdown", "")
    stats   = load_stats()
    all_wr  = winrate(stats["wins"], stats["losses"])
    day_wr  = winrate(stats["daily_wins"], stats["daily_losses"])
    diff    = all_wr - day_wr
    d_total = stats["daily_wins"] + stats["daily_losses"]
    if d_total == 0:
        await update.message.reply_text("📊 No trades today yet.")
        return
    status = (
        f"⚠️ *Significant drawdown!* Down {diff:.1f}% from average." if diff > 15 else
        f"📉 *Slight drawdown.* Down {diff:.1f}% from average."      if diff > 5  else
        f"✅ *Performing normally.* Within {abs(diff):.1f}% of average."
    )
    await update.message.reply_text(
        f"📊 *Drawdown Report*\n\n"
        f"All time : *{all_wr:.1f}%*\n"
        f"Today    : *{day_wr:.1f}%*\n"
        f"Diff     : *{diff:+.1f}%*\n\n{status}",
        parse_mode="Markdown"
    )


async def cmd_forceresult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("forceresult", " ".join(context.args) if context.args else "")
    if not context.args:
        await update.message.reply_text("Usage: `/forceresult WIN` or `/forceresult LOSS`", parse_mode="Markdown")
        return
    outcome = context.args[0].upper()
    if outcome not in ["WIN", "LOSS"]:
        await update.message.reply_text("⚠️ Must be WIN or LOSS.")
        return
    is_win = outcome == "WIN"
    stats  = load_stats()
    stats  = maybe_reset_daily(stats); stats = maybe_reset_weekly(stats)
    if is_win:
        stats["wins"] += 1; stats["daily_wins"] += 1; stats["weekly_wins"] += 1
    else:
        stats["losses"] += 1; stats["daily_losses"] += 1; stats["weekly_losses"] += 1
    save_stats(stats)
    wr    = winrate(stats["wins"], stats["losses"])
    total = stats["wins"] + stats["losses"]
    emoji = "✅" if is_win else "❌"
    text  = build_msg(
        f"{emoji} RESULT UPDATE — {outcome}",
        f"   ✅  Wins      »  *{stats['wins']}*\n"
        f"   ❌  Losses   »  *{stats['losses']}*\n"
        f"   🎯  Win Rate »  *{wr:.1f}%*  ({total} trades)\n\n"
        f"   {win_bar(wr)}"
    )
    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
    await update.message.reply_text(f"{emoji} Manual {outcome} recorded and posted!")


async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("pin", "")
    if not last_signal_message_id:
        await update.message.reply_text("⚠️ No signal posted yet to pin.")
        return
    try:
        await context.bot.pin_chat_message(chat_id=CHANNEL_ID, message_id=last_signal_message_id, disable_notification=False)
        await update.message.reply_text("📌 Last signal pinned!")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to pin: `{e}`\n\nMake sure bot has pin permission.", parse_mode="Markdown")


async def cmd_weights(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("weights")
    weights = load_weights()
    lines = []
    for name, data in weights.items():
        total = data["wins"] + data["losses"]
        wr    = round(data["wins"] / total * 100, 1) if total > 0 else 0
        w     = data["weight"]
        bar   = "🟢" if w >= 1.5 else "🟡" if w >= 1.0 else "🔴"
        lines.append(
            f"{bar} *{name}*\n"
            f"   W/L: {data['wins']}W / {data['losses']}L  ({wr}%)\n"
            f"   Weight: `{w}x`  ({total} trades)"
        )
    body = "\n\n".join(lines) if lines else "No data yet — weights update after first trades."
    await update.message.reply_text(
        f"🧠 *INDICATOR LEARNING WEIGHTS*\n\n{body}\n\n"
        f"_Range: 0.5x (weak) → 2.0x (strong)_",
        parse_mode="Markdown"
    )


async def cmd_bestpairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("bestpairs")
    pairs = get_best_pairs(10)
    if not pairs:
        await update.message.reply_text("📊 Not enough data yet — need 5+ trades per pair.")
        return
    lines = "\n".join([f"   {'🥇' if i==0 else '🥈' if i==1 else '🥉' if i==2 else '  '} *{p}* — {wr:.1f}% ({n} trades)" for i,(p,wr,n) in enumerate(pairs)])
    await update.message.reply_text(
        f"📊 *BEST PERFORMING PAIRS*\n\n{lines}\n\n_Minimum 5 trades to qualify._",
        parse_mode="Markdown"
    )


async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("sessions")
    stats = load_session_stats()
    if not stats:
        await update.message.reply_text("📊 Not enough data yet.")
        return
    lines = []
    for hour in sorted(stats.keys(), key=int):
        data  = stats[hour]
        total = data["wins"] + data["losses"]
        if total < 3: continue
        wr    = data["wins"] / total * 100
        bar   = "🟢" if wr >= 60 else "🟡" if wr >= 50 else "🔴"
        lines.append(f"   {bar} *{hour}:00 UTC* — {wr:.1f}%  ({total} trades)")
    body = "\n".join(lines) if lines else "No session data yet."
    await update.message.reply_text(
        f"🕐 *SESSION WIN RATES*\n\n{body}\n\n_Green = good hours, Red = avoid_",
        parse_mode="Markdown"
    )


async def cmd_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("filters")
    global trend_filter_enabled, time_filter_enabled, candle_filter_enabled, news_filter_enabled
    if context.args:
        arg = context.args[0].lower()
        if arg == "trend":    trend_filter_enabled  = not trend_filter_enabled
        elif arg == "time":   time_filter_enabled   = not time_filter_enabled
        elif arg == "candle": candle_filter_enabled = not candle_filter_enabled
        elif arg == "news":   news_filter_enabled   = not news_filter_enabled
    await update.message.reply_text(
        f"🔧 *SMART FILTERS*\n\n"
        f"   {'✅' if trend_filter_enabled  else '❌'} MTF trend filter\n"
        f"   {'✅' if time_filter_enabled   else '❌'} Time filter\n"
        f"   {'✅' if candle_filter_enabled else '❌'} Candle pattern filter\n"
        f"   {'✅' if news_filter_enabled   else '❌'} News filter\n\n"
        f"_Toggle: `/filters trend` `/filters time` `/filters candle` `/filters news`_",
        parse_mode="Markdown"
    )


async def cmd_setlosslimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("losslimit", " ".join(context.args) if context.args else "")
    global daily_loss_limit
    if not context.args:
        await update.message.reply_text(f"🛑 Daily loss limit: *{daily_loss_limit}*\n\nUsage: `/losslimit 5`", parse_mode="Markdown")
        return
    try:
        daily_loss_limit = int(context.args[0])
        await update.message.reply_text(f"✅ Daily loss limit set to *{daily_loss_limit}* losses.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid number.")


async def cmd_motivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("motivate", "")
    import random as _random
    quote, emoji = _random.choice(MOTIVATIONAL_QUOTES)
    text = build_msg(
        f"{emoji} MOTIVATION",
        f"_{quote}_",
        "💪 *Keep grinding. Skii Pro is with you every step.*"
    )
    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
    await update.message.reply_text("💪 Motivation posted!")


async def cmd_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    log_admin("members", " ".join(context.args) if context.args else "")
    global member_count
    if context.args:
        try:
            member_count = int(context.args[0])
        except ValueError:
            pass
    s    = load_stats()
    wr   = winrate(s["wins"], s["losses"])
    text = build_msg(
        f"🔥 {member_count} TRADERS ALREADY IN SKII PRO!",
        f"   💎  Premium signals every 5 minutes\n"
        f"   📊  {wr:.1f}% Win Rate\n"
        f"   🤖  Fully automated 24/7",
        "⏰ Don't miss out — join the team!\n👉 *whop.com/skiiprosignals*"
    )
    await send_to_free_group(context.bot, text)
    await update.message.reply_text(f"🔥 Member count ({member_count}) posted!")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    import traceback
    from telegram.error import Conflict, NetworkError, TimedOut
    err = context.error
    if isinstance(err, Conflict):
        print("⚠️ Conflict: another instance detected. Waiting 5s...")
        await asyncio.sleep(5)
    elif isinstance(err, (NetworkError, TimedOut)):
        print(f"⚠️ Network error: {err}. Will retry.")
    else:
        print(f"❌ Error: {traceback.format_exc()}")


# ── Main ──────────────────────────────────────────────────────────────────────
# ── Stats API Server ──────────────────────────────────────────────────────────
async def handle_stats(request):
    stats = load_stats()
    total = stats["wins"] + stats["losses"]
    wr    = winrate(stats["wins"], stats["losses"])
    data  = {
        "wins": stats["wins"], "losses": stats["losses"], "total": total,
        "win_rate": round(wr, 2),
        "daily_wins": stats["daily_wins"], "daily_losses": stats["daily_losses"],
        "weekly_wins": stats.get("weekly_wins", 0), "weekly_losses": stats.get("weekly_losses", 0),
        "streak_count": streak["count"], "streak_type": streak["type"] or "none",
    }
    return web.Response(text=json.dumps(data), content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})

async def handle_feed(request):
    return web.Response(text=json.dumps(list(reversed(signal_log))), content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})

async def handle_status(request):
    """Full bot status for dashboard."""
    stats = load_stats()
    wr    = winrate(stats["wins"], stats["losses"])
    now   = datetime.now(timezone.utc)
    data  = {
        "paused":               bot_paused,
        "expiry_mins":          expiry_mins,
        "min_score":            min_score,
        "pair_cooldown_mins":   pair_cooldown // 60,
        "global_cooldown_mins": GLOBAL_SIGNAL_COOLDOWN // 60,
        "scan_interval_secs":   SCAN_INTERVAL,
        "active_pairs":         len(active_pairs),
        "total_pairs":          len(OTC_PAIRS),
        "daily_loss_limit":     daily_loss_limit,
        "consecutive_losses":   consecutive_losses,
        "last_signal_ago":      round((now - last_signal_time).total_seconds() / 60, 1) if last_signal_time else None,
        "filters": {
            "trend":  trend_filter_enabled,
            "time":   time_filter_enabled,
            "candle": candle_filter_enabled,
            "news":   news_filter_enabled,
        },
        "streak":    {"count": streak["count"], "type": streak["type"] or "none"},
        "best_streak": best_streak,
        "win_rate":  round(wr, 2),
        "daily_wins":   stats["daily_wins"],
        "daily_losses": stats["daily_losses"],
        "total_trades": stats["wins"] + stats["losses"],
        "server_time":  now.strftime("%H:%M:%S UTC"),
    }
    return web.Response(text=json.dumps(data), content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})

async def handle_weights(request):
    """Indicator learning weights."""
    weights = load_weights()
    result  = {}
    for name, data in weights.items():
        total = data["wins"] + data["losses"]
        result[name] = {
            "wins":    data["wins"],
            "losses":  data["losses"],
            "total":   total,
            "win_rate": round(data["wins"] / total * 100, 1) if total > 0 else 0,
            "weight":  data["weight"],
        }
    return web.Response(text=json.dumps(result), content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})

async def handle_pairs(request):
    """Per-pair win rates."""
    stats  = load_pair_stats()
    result = []
    for pair, data in stats.items():
        total = data["wins"] + data["losses"]
        result.append({
            "pair":     pair,
            "wins":     data["wins"],
            "losses":   data["losses"],
            "total":    total,
            "win_rate": round(data["wins"] / total * 100, 1) if total > 0 else 0,
        })
    result.sort(key=lambda x: x["win_rate"], reverse=True)
    return web.Response(text=json.dumps(result), content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})

async def handle_sessions(request):
    """Win rate by hour."""
    stats  = load_session_stats()
    result = []
    for hour, data in stats.items():
        total = data["wins"] + data["losses"]
        result.append({
            "hour":     int(hour),
            "wins":     data["wins"],
            "losses":   data["losses"],
            "total":    total,
            "win_rate": round(data["wins"] / total * 100, 1) if total > 0 else 0,
        })
    result.sort(key=lambda x: x["hour"])
    return web.Response(text=json.dumps(result), content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})

async def handle_history(request):
    """Last 50 trades."""
    history = load_history()
    return web.Response(text=json.dumps(history[-50:]), content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})

async def start_api_server():
    app_web = web.Application()
    app_web.router.add_get("/api/stats",    handle_stats)
    app_web.router.add_get("/api/feed",     handle_feed)
    app_web.router.add_get("/api/status",   handle_status)
    app_web.router.add_get("/api/weights",  handle_weights)
    app_web.router.add_get("/api/pairs",    handle_pairs)
    app_web.router.add_get("/api/sessions", handle_sessions)
    app_web.router.add_get("/api/history",  handle_history)
    runner = web.AppRunner(app_web)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    try:
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"🌐 Dashboard API running on port {port} — 7 endpoints")
    except OSError:
        print(f"⚠️ Port {port} busy — API skipped")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.job_queue.run_repeating(scanner_job,      interval=SCAN_INTERVAL,  first=10)
    app.job_queue.run_repeating(stats_job,        interval=STATS_INTERVAL, first=30)
    app.job_queue.run_repeating(market_open_job,  interval=3600,           first=60)
    app.job_queue.run_repeating(weekly_recap_job, interval=3600,           first=90)
    app.job_queue.run_repeating(daily_tip_job,    interval=3600,           first=120)
    app.job_queue.run_repeating(leaderboard_job,  interval=3600,           first=150)

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("signal",      cmd_signal))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("weekly",      cmd_weekly))
    app.add_handler(CommandHandler("reset",       cmd_reset))
    app.add_handler(CommandHandler("pause",       cmd_pause))
    app.add_handler(CommandHandler("resume",      cmd_resume))
    app.add_handler(CommandHandler("expiry",      cmd_expiry))
    app.add_handler(CommandHandler("broadcast",   cmd_broadcast))
    app.add_handler(CommandHandler("setpairs",    cmd_setpairs))
    app.add_handler(CommandHandler("resetpairs",  cmd_resetpairs))
    app.add_handler(CommandHandler("setscore",    cmd_setscore))
    app.add_handler(CommandHandler("cooldown",    cmd_setcooldown))
    app.add_handler(CommandHandler("today",       cmd_today))
    app.add_handler(CommandHandler("winstreak",   cmd_winstreak))
    app.add_handler(CommandHandler("revenue",     cmd_revenue))
    app.add_handler(CommandHandler("warn",        cmd_warn))
    app.add_handler(CommandHandler("maintenance", cmd_maintenance))
    app.add_handler(CommandHandler("tip",         cmd_manualtip))
    app.add_handler(CommandHandler("drawdown",    cmd_drawdown))
    app.add_handler(CommandHandler("forceresult", cmd_forceresult))
    app.add_handler(CommandHandler("pin",         cmd_pin))
    app.add_handler(CommandHandler("motivate",    cmd_motivate))
    app.add_handler(CommandHandler("weights",     cmd_weights))
    app.add_handler(CommandHandler("bestpairs",   cmd_bestpairs))
    app.add_handler(CommandHandler("sessions",    cmd_sessions))
    app.add_handler(CommandHandler("filters",     cmd_filters))
    app.add_handler(CommandHandler("losslimit",   cmd_setlosslimit))
    # TikTok Live commands
    app.add_handler(CommandHandler("live",        cmd_live))
    app.add_handler(CommandHandler("endlive",     cmd_endlive))
    app.add_handler(CommandHandler("countdown",   cmd_countdown))
    app.add_handler(CommandHandler("scoreboard",  cmd_scoreboard))
    app.add_handler(CommandHandler("lastwins",    cmd_lastwins))
    app.add_handler(CommandHandler("promo",       cmd_promo))
    app.add_handler(CommandHandler("slots",       cmd_slots))
    app.add_handler(CommandHandler("discount",    cmd_discount))
    app.add_handler(CommandHandler("giveaway",    cmd_giveaway))
    app.add_handler(CommandHandler("challenge",   cmd_challenge))
    app.add_handler(CommandHandler("shoutout",    cmd_shoutout))
    app.add_handler(CommandHandler("link",        cmd_link))
    app.add_handler(CommandHandler("members",     cmd_members))
    app.add_handler(CommandHandler("win",         cmd_mywin))
    app.add_handler(CommandHandler("loss",        cmd_myloss))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(ChatMemberHandler(welcome_member, ChatMemberHandler.CHAT_MEMBER))

    print("🎿 Skii Pro Signals — Continuous scanner running 24/7...")

    async def api_starter(context):
        await start_api_server()

    app.job_queue.run_once(api_starter, when=1)

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "chat_member"],
    )

if __name__ == "__main__":
    main()
