import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
import yfinance as yf
import pandas as pd
from telegram import Update, ChatMemberUpdated
from telegram.ext import Application, CommandHandler, ContextTypes, ChatMemberHandler

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID      = os.getenv("GROUP_ID", "@yourgroupusername")
ADMIN_ID        = int(os.getenv("ADMIN_ID", "0"))

STATS_FILE      = "stats.json"
SCAN_INTERVAL   = 2 * 60     # scan all pairs every 2 minutes
PAIR_COOLDOWN   = 5 * 60     # don't resend same pair for 5 minutes
STATS_INTERVAL  = 6 * 3600   # stats post every 6 hours
EXPIRY_SECONDS  = 5 * 60     # 5 min trade expiry
MIN_SCORE       = 6           # out of 8 — only HIGH confidence fires

# ── Runtime controls (can be changed via admin commands) ──────────────────────
bot_paused    = False         # pause/resume signals
expiry_mins   = 5             # expiry time in minutes (changeable)

TRADING_SESSIONS   = [(7, 21)]
TRADE_MILESTONES   = {10, 25, 50, 100, 250, 500}
WINRATE_MILESTONES = {60, 65, 70, 75, 80}

OTC_PAIRS = {
    "EUR/USD OTC": "EURUSD=X",
    "GBP/USD OTC": "GBPUSD=X",
    "USD/JPY OTC": "USDJPY=X",
    "AUD/USD OTC": "AUDUSD=X",
    "EUR/GBP OTC": "EURGBP=X",
    "USD/CAD OTC": "USDCAD=X",
    "NZD/USD OTC": "NZDUSD=X",
    "USD/CHF OTC": "USDCHF=X",
    "EUR/JPY OTC": "EURJPY=X",
}

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



    last = pair_cooldowns.get(pair)
    if last is None:
        return False
    return (datetime.now(timezone.utc) - last).total_seconds() < PAIR_COOLDOWN

def pair_on_cooldown(pair: str) -> bool:
    last = pair_cooldowns.get(pair)
    if last is None:
        return False
    return (datetime.now(timezone.utc) - last).total_seconds() < PAIR_COOLDOWN

def set_cooldown(pair: str):
    pair_cooldowns[pair] = datetime.now(timezone.utc)


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
def calc_rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return round(float((100 - 100 / (1 + rs)).iloc[-1]), 2)

def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_macd(close):
    macd   = calc_ema(close, 12) - calc_ema(close, 26)
    signal = calc_ema(macd, 9)
    return float((macd - signal).iloc[-1])

def calc_bollinger(close, period=20):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return float((sma + 2*std).iloc[-1]), float((sma - 2*std).iloc[-1]), float(close.iloc[-1])

def calc_stochastic(high, low, close, k=14, d=3):
    k_val = 100 * (close - low.rolling(k).min()) / (high.rolling(k).max() - low.rolling(k).min())
    return float(k_val.iloc[-1]), float(k_val.rolling(d).mean().iloc[-1])

def calc_adx(high, low, close, period=14):
    tr       = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    dm_plus  = ((high - high.shift()) > (low.shift() - low)).astype(float) * (high - high.shift()).clip(lower=0)
    dm_minus = ((low.shift() - low) > (high - high.shift())).astype(float) * (low.shift() - low).clip(lower=0)
    atr      = tr.rolling(period).mean()
    di_plus  = 100 * dm_plus.rolling(period).mean() / atr
    di_minus = 100 * dm_minus.rolling(period).mean() / atr
    dx       = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
    adx      = dx.rolling(period).mean()
    return round(float(adx.iloc[-1]), 2), float(di_plus.iloc[-1]), float(di_minus.iloc[-1])

def calc_cci(high, low, close, period=20):
    typical = (high + low + close) / 3
    sma     = typical.rolling(period).mean()
    mad     = typical.rolling(period).apply(lambda x: abs(x - x.mean()).mean())
    cci     = (typical - sma) / (0.015 * mad)
    return round(float(cci.iloc[-1]), 2)

def calc_williams_r(high, low, close, period=14):
    highest = high.rolling(period).max()
    lowest  = low.rolling(period).min()
    wr      = -100 * (highest - close) / (highest - lowest)
    return round(float(wr.iloc[-1]), 2)


# ── Signal Engine ─────────────────────────────────────────────────────────────
def generate_signal(pair: str) -> dict:
    ticker = OTC_PAIRS[pair]
    df = yf.download(ticker, period="5d", interval="5m", progress=False, auto_adjust=True)

    if df.empty or len(df) < 30:
        raise ValueError(f"Not enough data for {pair}")

    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()

    rsi              = calc_rsi(close)
    ema9             = float(calc_ema(close, 9).iloc[-1])
    ema21            = float(calc_ema(close, 21).iloc[-1])
    macd_hist        = calc_macd(close)
    bb_upper, bb_lower, price = calc_bollinger(close)
    stoch_k, stoch_d = calc_stochastic(high, low, close)
    adx, di_plus, di_minus = calc_adx(high, low, close)
    cci              = calc_cci(high, low, close)
    williams_r       = calc_williams_r(high, low, close)

    score   = 0
    reasons = []

    if rsi < 30:
        score += 1; reasons.append(f"RSI {rsi:.1f} — Oversold")
    elif rsi > 70:
        score -= 1; reasons.append(f"RSI {rsi:.1f} — Overbought")
    else:
        reasons.append(f"RSI {rsi:.1f} — Neutral")

    if ema9 > ema21:
        score += 1; reasons.append("EMA Bullish Crossover")
    else:
        score -= 1; reasons.append("EMA Bearish Crossover")

    if macd_hist > 0:
        score += 1; reasons.append("MACD Momentum Positive")
    else:
        score -= 1; reasons.append("MACD Momentum Negative")

    if price < bb_lower:
        score += 1; reasons.append("Price Below Lower Band")
    elif price > bb_upper:
        score -= 1; reasons.append("Price Above Upper Band")
    else:
        reasons.append("Price Inside BB — Neutral")

    if stoch_k < 20 and stoch_k > stoch_d:
        score += 1; reasons.append(f"Stoch {stoch_k:.1f} — Oversold Reversal")
    elif stoch_k > 80 and stoch_k < stoch_d:
        score -= 1; reasons.append(f"Stoch {stoch_k:.1f} — Overbought Reversal")
    else:
        reasons.append(f"Stoch {stoch_k:.1f} — Neutral")

    if adx > 25:
        if di_plus > di_minus:
            score += 1; reasons.append(f"ADX {adx:.1f} — Strong Uptrend")
        else:
            score -= 1; reasons.append(f"ADX {adx:.1f} — Strong Downtrend")
    else:
        reasons.append(f"ADX {adx:.1f} — Weak Trend")

    if cci < -100:
        score += 1; reasons.append(f"CCI {cci:.0f} — Oversold")
    elif cci > 100:
        score -= 1; reasons.append(f"CCI {cci:.0f} — Overbought")
    else:
        reasons.append(f"CCI {cci:.0f} — Neutral")

    if williams_r < -80:
        score += 1; reasons.append(f"Williams %R {williams_r:.1f} — Oversold")
    elif williams_r > -20:
        score -= 1; reasons.append(f"Williams %R {williams_r:.1f} — Overbought")
    else:
        reasons.append(f"Williams %R {williams_r:.1f} — Neutral")

    direction  = "CALL" if score >= 0 else "PUT"
    abs_score  = abs(score)
    confidence = "HIGH" if abs_score >= 6 else "MEDIUM" if abs_score >= 3 else "LOW"

    return {
        "direction":   direction,
        "confidence":  confidence,
        "score":       score,
        "reasons":     reasons,
        "rsi":         rsi,
        "cci":         round(cci, 1),
        "williams_r":  williams_r,
        "adx":         adx,
        "stoch_k":     round(stoch_k, 1),
        "macd_hist":   round(macd_hist, 6),
        "entry_price": round(price, 5),
    }


def get_current_price(pair: str) -> float:
    ticker = OTC_PAIRS[pair]
    df = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError("Price fetch failed")
    return round(float(df["Close"].iloc[-1]), 5)


# ── Milestone Check ───────────────────────────────────────────────────────────
async def check_milestones(context, stats: dict):
    total = stats["wins"] + stats["losses"]
    wr    = winrate(stats["wins"], stats["losses"])
    ct    = stats.get("celebrated_trades", [])
    cw    = stats.get("celebrated_winrates", [])

    for m in TRADE_MILESTONES:
        if total >= m and m not in ct:
            ct.append(m); stats["celebrated_trades"] = ct; save_stats(stats)
            await context.bot.send_message(chat_id=CHANNEL_ID, parse_mode="Markdown", text=(
                f"🎿 *SKII PRO SIGNALS*\n▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
                f"🎉 *MILESTONE UNLOCKED!*\n\n"
                f"   🏆  *{m} Trades Completed!*\n\n"
                f"   ✅  Wins      »  *{stats['wins']}*\n"
                f"   ❌  Losses   »  *{stats['losses']}*\n"
                f"   🎯  Win Rate »  *{wr:.1f}%*\n\n"
                f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                f"💪 *{m} trades and still going strong!*\n\n"
                f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
            ))

    if total >= 10:
        for m in WINRATE_MILESTONES:
            if wr >= m and m not in cw:
                cw.append(m); stats["celebrated_winrates"] = cw; save_stats(stats)
                await context.bot.send_message(chat_id=CHANNEL_ID, parse_mode="Markdown", text=(
                    f"🎿 *SKII PRO SIGNALS*\n▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
                    f"🔥 *WIN RATE MILESTONE!*\n\n"
                    f"   🎯  We just hit *{m}% Win Rate!*\n\n"
                    f"   ✅  Wins    »  *{stats['wins']}*\n"
                    f"   ❌  Losses »  *{stats['losses']}*\n"
                    f"   📊  Total  »  *{total} trades*\n\n"
                    f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                    f"🚀 *{m}% win rate. Skii Pro is delivering!*\n\n"
                    f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
                ))


# ── Message Builders ──────────────────────────────────────────────────────────
def build_signal_msg(pair: str, signal: dict, time_str: str) -> str:
    direction  = signal["direction"]
    score      = signal["score"]
    votes      = f"+{score}" if score > 0 else str(score)
    dir_block  = (
        "╔══════════════════╗\n║   📈  C A L L    ║\n╚══════════════════╝" if direction == "CALL"
        else "╔══════════════════╗\n║   📉   P U T    ║\n╚══════════════════╝"
    )
    decisive = [r for r in signal["reasons"] if "Neutral" not in r][:4]
    reasons_text = "\n".join([f"   ✦ {r}" for r in decisive])

    # Calculate exact place time and expiry time
    now         = datetime.now(timezone.utc)
    expiry_time = (now + timedelta(minutes=5)).strftime("%H:%M:%S")

    return (
        f"🎿 *SKII PRO SIGNALS*\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
        f"`{dir_block}`\n\n"
        f"🪙 *Pair*       »  `{pair}`\n"
        f"⏱ *Expiry*    »  `{expiry_mins} Minutes`\n"
        f"💰 *Entry*     »  `{signal['entry_price']}`\n"
        f"🕐 *Place At*  »  `{time_str}`\n"
        f"🔒 *Closes At* »  `{expiry_time} UTC`\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"📊 *ANALYSIS*  ({votes}/8 indicators)\n\n"
        f"{reasons_text}\n\n"
        f"📉 *Key Values*\n"
        f"   • RSI         : `{signal['rsi']}`\n"
        f"   • CCI         : `{signal['cci']}`\n"
        f"   • Williams %R : `{signal['williams_r']}`\n"
        f"   • ADX         : `{signal['adx']}`\n"
        f"   • Stoch %K    : `{signal['stoch_k']}`\n\n"
        f"🎯 *Signal Strength*\n"
        f"   🔥🔥🔥  HIGH CONFIDENCE\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"⏳ _Result posts automatically at {expiry_time} UTC_\n\n"
        f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
    )


def build_result_msg(pair, direction, entry_price, exit_price, pips, stats) -> str:
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
        f"📏 *Movement*  »  `{pips} pips`\n\n"
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
    """Scans all 9 pairs every 2 min. Fires instantly on HIGH confidence."""
    if bot_paused:
        return
    if not is_trading_hours():
        return

    # Hourly cap check
    if signals_this_hour() >= MAX_SIGNALS_PER_HOUR:
        print(f"⛔ Hourly cap reached ({MAX_SIGNALS_PER_HOUR}/hr) — skipping scan.")
        return

    loop = asyncio.get_event_loop()

    for pair in OTC_PAIRS:
        if pair_on_cooldown(pair):
            continue

        try:
            signal = await loop.run_in_executor(None, generate_signal, pair)
        except Exception:
            continue

        # Only fire on HIGH confidence (6+/8 indicators agree)
        if abs(signal["score"]) < MIN_SCORE:
            continue

        # Fire the signal
        set_cooldown(pair)
        increment_hourly_counter()
        time_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        text     = build_signal_msg(pair, signal, time_str)

        await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")

        # Send poll after signal
        await context.bot.send_poll(
            chat_id=CHANNEL_ID,
            question=f"Did you take this trade? ({pair} {signal['direction']})",
            options=["✅ Yes, I'm in!", "❌ Missed it", "👀 Watching"],
            is_anonymous=False,
        )

        # Schedule result check after 5 min
        context.job_queue.run_once(
            result_job,
            when=expiry_mins * 60,
            data={"pair": pair, "direction": signal["direction"], "entry_price": signal["entry_price"]},
        )

        print(f"✅ Signal fired: {pair} {signal['direction']} ({signal['score']}/8) — {signals_this_hour()}/{MAX_SIGNALS_PER_HOUR} this hour")

        # Stop scanning if cap now reached
        if signals_this_hour() >= MAX_SIGNALS_PER_HOUR:
            print(f"⛔ Hourly cap reached — stopping scan cycle.")
            break


async def result_job(context: ContextTypes.DEFAULT_TYPE):
    data        = context.job.data
    pair        = data["pair"]
    direction   = data["direction"]
    entry_price = data["entry_price"]

    try:
        loop       = asyncio.get_event_loop()
        exit_price = await loop.run_in_executor(None, get_current_price, pair)
    except Exception as exc:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=f"⚠️ Result error: `{exc}`", parse_mode="Markdown")
        return

    is_win = (exit_price - entry_price > 0) if direction == "CALL" else (exit_price - entry_price < 0)
    pips   = round(abs(exit_price - entry_price) * 10000, 1)

    stats = load_stats()
    stats = maybe_reset_daily(stats)
    stats = maybe_reset_weekly(stats)
    if is_win:
        stats["wins"] += 1; stats["daily_wins"] += 1; stats["weekly_wins"] += 1
    else:
        stats["losses"] += 1; stats["daily_losses"] += 1; stats["weekly_losses"] += 1
    save_stats(stats)

    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=build_result_msg(pair, direction, entry_price, exit_price, pips, stats),
        parse_mode="Markdown"
    )

    # Streak tracker
    streak_count, streak_type, is_milestone = update_streak(is_win)
    if is_milestone:
        if streak_type == "win":
            streak_emoji = "🔥" * min(streak_count, 5)
            streak_msg = (
                f"🎿 *SKII PRO SIGNALS*\n"
                f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
                f"{streak_emoji} *{streak_count} WINS IN A ROW!*\n\n"
                f"The signals are on fire right now! "
                f"Don't miss the next one! 💰\n\n"
                f"   🎯  Win Rate »  *{winrate(stats['wins'], stats['losses']):.1f}%*\n\n"
                f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
            )
        else:
            streak_msg = (
                f"🎿 *SKII PRO SIGNALS*\n"
                f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
                f"📊 *{streak_count} LOSSES IN A ROW*\n\n"
                f"Variance happens — every signal service goes through it. "
                f"Stay patient and trust the process. The win rate speaks for itself. 💪\n\n"
                f"   🎯  Win Rate »  *{winrate(stats['wins'], stats['losses']):.1f}%*\n\n"
                f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
            )
        await context.bot.send_message(chat_id=CHANNEL_ID, text=streak_msg, parse_mode="Markdown")

    await check_milestones(context, stats)


async def stats_job(context: ContextTypes.DEFAULT_TYPE):
    stats = load_stats(); stats = maybe_reset_daily(stats); save_stats(stats)
    await context.bot.send_message(chat_id=CHANNEL_ID, text=build_stats_msg(stats), parse_mode="Markdown")


async def daily_tip_job(context: ContextTypes.DEFAULT_TYPE):
    """Posts a trading tip every weekday morning at 07:00 UTC."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5 or now.hour != 7:
        return

    tip, category = TRADING_TIPS[now.timetuple().tm_yday % len(TRADING_TIPS)]

    await context.bot.send_message(
        chat_id=CHANNEL_ID, parse_mode="Markdown",
        text=(
            f"🎿 *SKII PRO SIGNALS*\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            f"📚 *DAILY TRADING TIP*\n"
            f"🏷 _{category}_\n\n"
            f"{tip}\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"💬 _Study the craft. The signals do the work, but knowledge keeps you in the game._\n\n"
            f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
        )
    )


async def leaderboard_job(context: ContextTypes.DEFAULT_TYPE):
    """Posts leaderboard every day at 20:00 UTC."""
    now = datetime.now(timezone.utc)
    if now.hour != 20:
        return
    lb   = load_leaderboard()
    text = build_leaderboard_msg(lb)
    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")



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
        session_name = "🇬🇧 London Session"
        hype = "London is open — liquidity is high and the scanner is running hot. Let's get to work! 💼"
    elif now.hour == 12:
        session_name = "🇺🇸 New York Session"
        hype = "New York just opened — maximum volatility, maximum opportunity! 🗽"
    else:
        return

    stats = load_stats()
    wr    = winrate(stats["wins"], stats["losses"])
    await context.bot.send_message(chat_id=CHANNEL_ID, parse_mode="Markdown", text=(
        f"🎿 *SKII PRO SIGNALS*\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
        f"🔔 *{session_name} OPEN*\n\n"
        f"   🔍  Scanner is now *ACTIVE* on all 9 pairs\n"
        f"   ⏱  Expiry: *5 minutes*\n"
        f"   🎯  Current Win Rate: *{wr:.1f}%*\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"💬 _{hype}_\n\n"
        f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
    ))


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

    await context.bot.send_message(chat_id=result.chat.id, parse_mode="Markdown", text=(
        f"🎿 *SKII PRO SIGNALS*\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
        f"👋 *Welcome to the group,* {mention}*!*\n\n"
        f"You just joined *Skii Pro Signals* — a premium OTC signal community powered by an 8-indicator live market scanner.\n\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"📌 *WHAT TO EXPECT*\n\n"
        f"   🔍  Bot scans *all 9 OTC pairs* constantly\n"
        f"   ⚡  Signals fire *instantly* when setup is detected\n"
        f"   ⏱  Expiry: *5 minutes* per trade\n"
        f"   ✅  Auto WIN/LOSS result posted\n"
        f"   📊  Stats report every *6 hours*\n"
        f"   🔥  Only HIGH confidence signals posted\n"
        f"   🕐  Active during London & NY sessions\n\n"
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



async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"📩 /start from ID: {update.effective_user.id} | ADMIN_ID: {ADMIN_ID} | Match: {update.effective_user.id == ADMIN_ID}")
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text(
        "🎿 *Skii Pro Signals — Admin Panel*\n\n"
        "/status       — Bot status\n"
        "/signal       — Force post a signal\n"
        "/stats        — Force post stats\n"
        "/weekly       — Force post weekly recap\n"
        "/pause        — Pause all signals ⏸\n"
        "/resume       — Resume signals ▶️\n"
        "/expiry [min] — Change expiry time ⏱\n"
        "/broadcast    — Send message to group 📣\n"
        "/reset        — Reset all stats",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    stats   = load_stats()
    total   = stats["wins"] + stats["losses"]
    wr      = winrate(stats["wins"], stats["losses"])
    trading = "✅ Active" if is_trading_hours() else f"❌ Closed — next: {next_session_open()}"
    on_cd   = [p for p in OTC_PAIRS if pair_on_cooldown(p)]
    await update.message.reply_text(
        f"🎿 *Skii Pro Signals is live* ✅\n\n"
        f"Group         : `{CHANNEL_ID}`\n"
        f"Scanner       : every 2 min across 9 pairs\n"
        f"Min score     : {MIN_SCORE}/8 indicators\n"
        f"Signals/hour  : {signals_this_hour()}/{MAX_SIGNALS_PER_HOUR}\n"
        f"Pair cooldown : 5 min\n"
        f"Expiry        : 5 min\n"
        f"Trading hours : {trading}\n"
        f"On cooldown   : {len(on_cd)} pairs\n"
        f"Current streak: {streak['count']} {streak['type'] or 'none'}\n\n"
        f"Total trades  : {total}\n"
        f"Win rate      : {wr:.1f}%",
        parse_mode="Markdown"
    )

async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await scanner_job(context)
    await update.message.reply_text("🔍 Scanner triggered — signal posted if setup found.")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await stats_job(context)
    await update.message.reply_text("📊 Stats posted to group.")

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    stats = load_stats()
    await context.bot.send_message(chat_id=CHANNEL_ID, text=build_weekly_msg(stats), parse_mode="Markdown")
    await update.message.reply_text("📆 Weekly recap posted.")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    save_stats({
        "wins": 0, "losses": 0, "daily_wins": 0, "daily_losses": 0,
        "weekly_wins": 0, "weekly_losses": 0,
        "last_reset": today_str(), "last_week_reset": week_str(),
        "celebrated_trades": [], "celebrated_winrates": [],
    })
    await update.message.reply_text("🔄 All stats reset to zero.")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    global bot_paused
    bot_paused = True
    await update.message.reply_text(
        "⏸ *Signals paused.*\n\nThe scanner is stopped. Use /resume to turn signals back on.",
        parse_mode="Markdown"
    )
    await context.bot.send_message(
        chat_id=CHANNEL_ID, parse_mode="Markdown",
        text=(
            f"🎿 *SKII PRO SIGNALS*\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            f"⏸ *Signals are temporarily paused.*\n\n"
            f"_We'll be back shortly. Stay tuned!_\n\n"
            f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
        )
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    global bot_paused
    bot_paused = False
    await update.message.reply_text(
        "▶️ *Signals resumed!*\n\nScanner is back online.",
        parse_mode="Markdown"
    )
    await context.bot.send_message(
        chat_id=CHANNEL_ID, parse_mode="Markdown",
        text=(
            f"🎿 *SKII PRO SIGNALS*\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            f"▶️ *Signals are back LIVE!*\n\n"
            f"   🔍  Scanner active on all 9 pairs\n"
            f"   ⏱  Expiry: *{expiry_mins} minutes*\n\n"
            f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
        )
    )


async def cmd_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    global expiry_mins
    if not context.args:
        await update.message.reply_text(
            f"⏱ Current expiry is *{expiry_mins} minutes*.\n\nTo change it use:\n`/expiry 1` or `/expiry 3` or `/expiry 5`",
            parse_mode="Markdown"
        )
        return
    try:
        new_expiry = int(context.args[0])
        if new_expiry not in [1, 2, 3, 5, 10, 15]:
            await update.message.reply_text("⚠️ Use one of: 1, 2, 3, 5, 10, 15 minutes.", parse_mode="Markdown")
            return
        expiry_mins = new_expiry
        await update.message.reply_text(
            f"✅ *Expiry updated to {expiry_mins} minutes!*\n\nAll new signals will now use {expiry_mins} min expiry.",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("⚠️ Invalid number. Example: `/expiry 5`", parse_mode="Markdown")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text(
            "📣 To broadcast a message use:\n`/broadcast Your message here`",
            parse_mode="Markdown"
        )
        return
    message = " ".join(context.args)
    await context.bot.send_message(
        chat_id=CHANNEL_ID, parse_mode="Markdown",
        text=(
            f"🎿 *SKII PRO SIGNALS*\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            f"📣 *ANNOUNCEMENT*\n\n"
            f"{message}\n\n"
            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
            f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
        )
    )
    await update.message.reply_text("✅ Broadcast sent to group!")


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
    app.add_handler(CommandHandler("win",         cmd_mywin))
    app.add_handler(CommandHandler("loss",        cmd_myloss))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(ChatMemberHandler(welcome_member, ChatMemberHandler.CHAT_MEMBER))

    print("🎿 Skii Pro Signals — Continuous scanner running 24/7...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "chat_member"],
    )

if __name__ == "__main__":
    main()
