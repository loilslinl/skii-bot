import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
import yfinance as yf
import pandas as pd
from telegram import Update, ChatMemberUpdated
from telegram.ext import Application, CommandHandler, ContextTypes, ChatMemberHandler

# ── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN  = os.getenv(“TELEGRAM_BOT_TOKEN”, “YOUR_BOT_TOKEN_HERE”)
CHANNEL_ID      = os.getenv(“GROUP_ID”, “@yourgroupusername”)       # paid group
FREE_GROUP_ID   = os.getenv(“FREE_GROUP_ID”, “@yourfreegroupusername”)  # free group
ADMIN_ID        = int(os.getenv(“ADMIN_ID”, “0”))

STATS_FILE      = “stats.json”
SCAN_INTERVAL   = 60          # scan all pairs every 60 seconds
STATS_INTERVAL  = 6 * 3600   # stats post every 6 hours

# ── Runtime controls (can be changed via admin commands) ──────────────────────

bot_paused       = False         # pause/resume signals
expiry_mins      = 5             # expiry time in minutes
min_score        = 3             # 3/5 indicators must agree to fire
pair_cooldown    = 5 * 60        # cooldown per pair in seconds
member_count     = 0             # manually tracked member count
best_streak      = {“count”: 0, “type”: None}  # best streak ever

HISTORY_FILE     = “history.json”

TRADING_SESSIONS   = [(7, 21)]
TRADE_MILESTONES   = {10, 25, 50, 100, 250, 500}
WINRATE_MILESTONES = {60, 65, 70, 75, 80}

OTC_PAIRS = {
# Forex OTC
“EUR/USD OTC”:  “EURUSD=X”,
“GBP/USD OTC”:  “GBPUSD=X”,
“USD/JPY OTC”:  “USDJPY=X”,
“USD/CHF OTC”:  “USDCHF=X”,
“USD/CAD OTC”:  “USDCAD=X”,
“AUD/USD OTC”:  “AUDUSD=X”,
“NZD/USD OTC”:  “NZDUSD=X”,
“EUR/GBP OTC”:  “EURGBP=X”,
“EUR/JPY OTC”:  “EURJPY=X”,
“EUR/CHF OTC”:  “EURCHF=X”,
“GBP/JPY OTC”:  “GBPJPY=X”,
“AUD/JPY OTC”:  “AUDJPY=X”,
“AUD/CAD OTC”:  “AUDCAD=X”,
“EUR/AUD OTC”:  “EURAUD=X”,
“GBP/CAD OTC”:  “GBPCAD=X”,
# Crypto OTC
“BTC/USD OTC”:  “BTC-USD”,
“ETH/USD OTC”:  “ETH-USD”,
“LTC/USD OTC”:  “LTC-USD”,
“XRP/USD OTC”:  “XRP-USD”,
# Commodities OTC
“Gold OTC”:     “GC=F”,
“Silver OTC”:   “SI=F”,
“Oil OTC”:      “CL=F”,
}

# Must be defined after OTC_PAIRS

active_pairs = list(OTC_PAIRS.keys())

# Tracks last signal message ID for pinning

last_signal_message_id = None

MOTIVATIONAL_QUOTES = [
(“The market rewards patience. Every signal is an opportunity — take it with discipline.”, “💎”),
(“Losses are tuition fees. You’re not failing, you’re learning.”, “📚”),
(“One bad day doesn’t define a trader. Your win rate over 100 trades does.”, “📊”),
(“The best traders in the world lose trades. What separates them is how they respond.”, “🏆”),
(“Stay consistent. The edge plays out over time — not in one trade.”, “⚡”),
(“Risk management is not optional. It’s the only reason traders survive long term.”, “🛡”),
(“Don’t trade with money you can’t afford to lose. Clear mind = better decisions.”, “🧠”),
(“Every professional was once a beginner. Keep showing up.”, “🚀”),
(“The signal doesn’t guarantee a win. It gives you an edge. Play the edge.”, “🎯”),
(“Compounding works in trading too. Small consistent wins build life-changing accounts.”, “💰”),
(“You don’t need to win every trade. You need to win more than you lose.”, “✅”),
(“Discipline is doing the right thing even when it’s hard. That’s what separates pros.”, “🔥”),
]

MAX_SIGNALS_PER_HOUR = 5

LEADERBOARD_FILE = “leaderboard.json”

TRADING_TIPS = [
(“💡 Never risk more than 1-2% of your account on a single trade. Protect your capital first.”, “Risk Management”),
(“💡 Don’t chase losses. If you hit 3 losses in a row, step away and come back tomorrow.”, “Discipline”),
(“💡 The best signals come during high liquidity — London and New York overlap (12:00-16:00 UTC) is the sweet spot.”, “Timing”),
(“💡 Consistency beats big wins. A 70% win rate over 100 trades beats one lucky 10x every time.”, “Mindset”),
(“💡 Always wait for the signal — never trade out of boredom. Patience is your edge.”, “Discipline”),
(“💡 Binary options are about direction, not magnitude. Even 1 pip in your favour is a win.”, “Education”),
(“💡 Keep a trading journal. Write down every trade you take and why. Patterns will reveal themselves.”, “Growth”),
(“💡 Avoid trading during major news events like NFP, CPI, or Fed decisions — volatility makes signals unreliable.”, “Risk Management”),
(“💡 Your mindset after a loss matters more than the loss itself. Stay calm and trust the process.”, “Mindset”),
(“💡 Don’t increase your stake after a loss trying to recover. Flat staking is the professional approach.”, “Risk Management”),
(“💡 OTC markets run 24/7 but the best price action happens when real forex markets are open.”, “Education”),
(“💡 A signal with HIGH confidence means 6+ of 8 indicators agree. Those are your best setups.”, “Education”),
(“💡 Win rate matters but so does consistency. 10 trades at 70% beats 2 trades at 100%.”, “Mindset”),
(“💡 Never trade money you can’t afford to lose. Scared money makes bad decisions.”, “Risk Management”),
(“💡 The market doesn’t owe you a win. Every trade is independent — stay humble.”, “Mindset”),
(“💡 RSI below 30 = oversold = potential bounce up. RSI above 70 = overbought = potential drop.”, “Education”),
(“💡 MACD crossing above the signal line is a bullish sign. Below = bearish.”, “Education”),
(“💡 Bollinger Bands squeezing together means a big move is coming — watch closely.”, “Education”),
(“💡 ADX above 25 means a strong trend is in play. Below 25 = choppy market = avoid.”, “Education”),
(“💡 The best traders aren’t right all the time — they just manage risk better than everyone else.”, “Mindset”),
]

# Tracks last signal time per pair {pair: datetime}

pair_cooldowns: dict = {}

# Hourly signal counter {hour_str: count}

hourly_counter: dict = {}

# Streak tracker

streak: dict = {“count”: 0, “type”: None}  # type = “win” or “loss”

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
return (now + timedelta(days=days_ahead)).replace(hour=7, minute=0, second=0).strftime(”%a %d %b at %H:%M UTC”)

def current_hour_key() -> str:
return datetime.now(timezone.utc).strftime(”%Y-%m-%d-%H”)

def signals_this_hour() -> int:
return hourly_counter.get(current_hour_key(), 0)

def increment_hourly_counter():
key = current_hour_key()
hourly_counter[key] = hourly_counter.get(key, 0) + 1

def update_streak(is_win: bool) -> tuple:
“”“Updates streak and returns (streak_count, streak_type, is_new_milestone).”””
outcome = “win” if is_win else “loss”
if streak[“type”] == outcome:
streak[“count”] += 1
else:
streak[“type”]  = outcome
streak[“count”] = 1
milestones = {3, 5, 10}
is_milestone = streak[“count”] in milestones
return streak[“count”], streak[“type”], is_milestone

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

# ── Stats ─────────────────────────────────────────────────────────────────────

def load_leaderboard() -> dict:
if os.path.exists(LEADERBOARD_FILE):
with open(LEADERBOARD_FILE) as f:
return json.load(f)
return {}

def save_leaderboard(lb: dict):
with open(LEADERBOARD_FILE, “w”) as f:
json.dump(lb, f)

def update_leaderboard(user_id: str, username: str, is_win: bool) -> dict:
lb = load_leaderboard()
if user_id not in lb:
lb[user_id] = {“name”: username, “wins”: 0, “losses”: 0}
lb[user_id][“name”] = username
if is_win:
lb[user_id][“wins”] += 1
else:
lb[user_id][“losses”] += 1
save_leaderboard(lb)
return lb

def build_leaderboard_msg(lb: dict) -> str:
if not lb:
return (
f”🎿 *SKII PRO SIGNALS*\n”
f”▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n”
f”🏆 *LEADERBOARD*\n\n”
f”*No entries yet! Use /win or /loss after each trade to get on the board.*\n\n”
f”*🎿 Skii Pro Signals — Premium OTC Alerts*”
)

```
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
```

async def send_to_free_group(bot, text: str):
“”“Safely send to free group — silently skips if not configured.”””
if not FREE_GROUP_ID or FREE_GROUP_ID == “@yourfreegroupusername”:
return
try:
await bot.send_message(chat_id=FREE_GROUP_ID, text=text, parse_mode=“Markdown”)
except Exception as e:
print(f”⚠️ Free group send failed: {e}”)

def load_history() -> list:
if os.path.exists(HISTORY_FILE):
with open(HISTORY_FILE) as f:
return json.load(f)
return []

def save_history(h: list):
with open(HISTORY_FILE, “w”) as f:
json.dump(h, f)

def log_trade(pair: str, direction: str, entry: float, exit_price: float, is_win: bool):
history = load_history()
history.append({
“time”:      datetime.now(timezone.utc).strftime(”%H:%M UTC”),
“date”:      today_str(),
“pair”:      pair,
“direction”: direction,
“entry”:     entry,
“exit”:      exit_price,
“result”:    “WIN” if is_win else “LOSS”,
“pips”:      round(abs(exit_price - entry) * 10000, 1),
})
save_history(history[-200:])  # keep last 200 trades

```
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
```

def load_stats() -> dict:
if os.path.exists(STATS_FILE):
with open(STATS_FILE) as f:
return json.load(f)
return {
“wins”: 0, “losses”: 0,
“daily_wins”: 0, “daily_losses”: 0,
“weekly_wins”: 0, “weekly_losses”: 0,
“last_reset”: today_str(), “last_week_reset”: week_str(),
“celebrated_trades”: [], “celebrated_winrates”: [],
}

def save_stats(s: dict):
with open(STATS_FILE, “w”) as f:
json.dump(s, f)

def today_str() -> str:
return datetime.now(timezone.utc).strftime(”%Y-%m-%d”)

def week_str() -> str:
return datetime.now(timezone.utc).strftime(”%Y-W%W”)

def maybe_reset_daily(s: dict) -> dict:
if s.get(“last_reset”) != today_str():
s[“daily_wins”] = 0; s[“daily_losses”] = 0; s[“last_reset”] = today_str()
return s

def maybe_reset_weekly(s: dict) -> dict:
if s.get(“last_week_reset”) != week_str():
s[“weekly_wins”] = 0; s[“weekly_losses”] = 0; s[“last_week_reset”] = week_str()
return s

def winrate(wins: int, losses: int) -> float:
total = wins + losses
return (wins / total * 100) if total > 0 else 0.0

def win_bar(wr: float) -> str:
filled = round(wr / 10)
return “🟩” * filled + “⬜” * (10 - filled)

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
df = yf.download(ticker, period=“5d”, interval=“5m”, progress=False, auto_adjust=True)

```
if df.empty or len(df) < 30:
    raise ValueError(f"Not enough data for {pair}")

close = df["Close"].squeeze()
high  = df["High"].squeeze()
low   = df["Low"].squeeze()

# Ensure all are 1D Series
if hasattr(close, 'columns'): close = close.iloc[:, 0]
if hasattr(high,  'columns'): high  = high.iloc[:, 0]
if hasattr(low,   'columns'): low   = low.iloc[:, 0]

# ── 5 Core Indicators ────────────────────────────────────────────────────
rsi              = calc_rsi(close)
ema9             = float(calc_ema(close, 9).iloc[-1])
ema21            = float(calc_ema(close, 21).iloc[-1])
macd_hist        = calc_macd(close)
bb_upper, bb_lower, price = calc_bollinger(close)
stoch_k, stoch_d = calc_stochastic(high, low, close)

score   = 0
reasons = []

# 1. RSI
if rsi < 30:
    score += 1; reasons.append(f"RSI {rsi:.1f} — Oversold")
elif rsi > 70:
    score -= 1; reasons.append(f"RSI {rsi:.1f} — Overbought")
else:
    reasons.append(f"RSI {rsi:.1f} — Neutral")

# 2. EMA Crossover
if ema9 > ema21:
    score += 1; reasons.append("EMA Bullish Crossover")
else:
    score -= 1; reasons.append("EMA Bearish Crossover")

# 3. MACD
if macd_hist > 0:
    score += 1; reasons.append("MACD Momentum Positive")
else:
    score -= 1; reasons.append("MACD Momentum Negative")

# 4. Bollinger Bands
if price < bb_lower:
    score += 1; reasons.append("Price Below Lower Band")
elif price > bb_upper:
    score -= 1; reasons.append("Price Above Upper Band")
else:
    reasons.append("Price Inside BB — Neutral")

# 5. Stochastic
if stoch_k < 20 and stoch_k > stoch_d:
    score += 1; reasons.append(f"Stoch {stoch_k:.1f} — Oversold Reversal")
elif stoch_k > 80 and stoch_k < stoch_d:
    score -= 1; reasons.append(f"Stoch {stoch_k:.1f} — Overbought Reversal")
else:
    reasons.append(f"Stoch {stoch_k:.1f} — Neutral")

direction  = "CALL" if score >= 0 else "PUT"
abs_score  = abs(score)
confidence = "HIGH" if abs_score >= 4 else "MEDIUM" if abs_score >= 3 else "LOW"

return {
    "direction":   direction,
    "confidence":  confidence,
    "score":       score,
    "reasons":     reasons,
    "rsi":         rsi,
    "stoch_k":     round(stoch_k, 1),
    "macd_hist":   round(macd_hist, 6),
    "entry_price": round(price, 5),
}
```

def get_current_price(pair: str) -> float:
ticker = OTC_PAIRS[pair]
df = yf.download(ticker, period=“1d”, interval=“1m”, progress=False, auto_adjust=True)
if df.empty:
raise ValueError(“Price fetch failed”)
close = df[“Close”]
if hasattr(close, “squeeze”):
close = close.squeeze()
return round(float(close.iloc[-1]), 5)

# ── Milestone Check ───────────────────────────────────────────────────────────

async def check_milestones(context, stats: dict):
total = stats[“wins”] + stats[“losses”]
wr    = winrate(stats[“wins”], stats[“losses”])
ct    = stats.get(“celebrated_trades”, [])
cw    = stats.get(“celebrated_winrates”, [])

```
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
```

# ── Universal Message Template ────────────────────────────────────────────────

HEADER  = “🎿 *SKII PRO SIGNALS*”
DIVIDER = “▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰”
FOOTER  = “*🎿 Skii Pro Signals — Premium OTC Alerts*”

def build_msg(title: str, body: str, cta: str = “”) -> str:
“”“Universal message template — every bot message uses this.”””
cta_block = f”{DIVIDER}\n{cta}\n\n” if cta else “”
return (
f”{HEADER}\n”
f”{DIVIDER}\n”
f”{title}\n\n”
f”{DIVIDER}\n”
f”{body}\n\n”
f”{cta_block}”
f”{FOOTER}”
)

def get_payout(pair: str) -> str:
“”“Returns typical Pocket Option payout % for each asset type.”””
if “BTC” in pair or “ETH” in pair or “LTC” in pair or “XRP” in pair:
return “82%”
elif “Gold” in pair or “Silver” in pair:
return “80%”
elif “Oil” in pair:
return “79%”
elif “GBP/JPY” in pair or “AUD/JPY” in pair:
return “76%”
else:
return “85%”

def build_signal_msg(pair: str, signal: dict, time_str: str) -> str:
direction  = signal[“direction”]
score      = signal[“score”]
votes      = f”+{score}” if score > 0 else str(score)
dir_block  = (
“╔══════════════════╗\n║   📈  C A L L    ║\n╚══════════════════╝” if direction == “CALL”
else “╔══════════════════╗\n║   📉   P U T    ║\n╚══════════════════╝”
)
payout      = get_payout(pair)
stats       = load_stats()
total       = stats[“wins”] + stats[“losses”]
accuracy    = f”{winrate(stats[‘wins’], stats[‘losses’]):.1f}%” if total >= 5 else “Building…”
now         = datetime.now(timezone.utc)
expiry_time = (now + timedelta(minutes=expiry_mins)).strftime(”%H:%M:%S”)

```
# Get decisive reasons
reasons     = signal.get("reasons", [])
decisive    = [r for r in reasons if "Neutral" not in r and "Weak" not in r][:4]
reasons_txt = "\n".join([f"   ✦ {r}" for r in decisive]) if decisive else "   ✦ Multiple indicators confirmed"

return (
    f"🎿 *SKII PRO SIGNALS*\n"
    f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
    f"`{dir_block}`\n\n"
    f"🪙 *Pair*       »  `{pair}`\n"
    f"⏱ *Expiry*    »  `{expiry_mins} Minutes`\n"
    f"🕐 *Place At*  »  `{time_str}`\n"
    f"🔒 *Closes At* »  `{expiry_time} UTC`\n"
    f"💵 *Payout*    »  `{payout}`\n"
    f"🎯 *Accuracy*  »  `{accuracy}`\n\n"
    f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
    f"📊 *ANALYSIS* ({votes}/5 indicators)\n\n"
    f"{reasons_txt}\n\n"
    f"📉 *Key Values*\n"
    f"   • RSI      : `{signal.get('rsi', 'N/A')}`\n"
    f"   • Stoch %K : `{signal.get('stoch_k', 'N/A')}`\n"
    f"   • MACD     : `{signal.get('macd_hist', 'N/A')}`\n\n"
    f"🔥🔥🔥 *HIGH CONFIDENCE* — {votes}/5\n\n"
    f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
    f"⏳ _Result posts automatically at {expiry_time} UTC_\n\n"
    f"_🎿 Skii Pro Signals — Premium OTC Alerts_"
)
```

def build_result_msg(pair, direction, entry_price, exit_price, pips_label, stats) -> str:
is_win = (direction == “CALL” and exit_price > entry_price) or   
(direction == “PUT”  and exit_price < entry_price)
wr     = winrate(stats[“wins”], stats[“losses”])
total  = stats[“wins”] + stats[“losses”]
arrow  = “📈” if exit_price > entry_price else “📉”
header = (
“╔══════════════════╗\n║  ✅  W I N  🏆   ║\n╚══════════════════╝” if is_win
else “╔══════════════════╗\n║  ❌  L O S S     ║\n╚══════════════════╝”
)
footer = (
“💪 *Another one! Keep following the signals.*” if is_win
else “📊 *Losses are part of the game. Stay disciplined.*”
)
return (
f”🎿 *SKII PRO SIGNALS*\n”
f”▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n”
f”`{header}`\n\n”
f”🪙 *Pair*        »  `{pair}`\n”
f”📌 *Direction*  »  `{'📈 CALL' if direction == 'CALL' else '📉 PUT'}`\n”
f”🔓 *Entry*      »  `{entry_price}`\n”
f”🔒 *Exit*       »  `{exit_price}` {arrow}\n”
f”📏 *Movement*  »  `{pips_label}`\n\n”
f”▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n”
f”📈 *SESSION STATS*\n\n”
f”   ✅  Wins      »  *{stats[‘wins’]}*\n”
f”   ❌  Losses   »  *{stats[‘losses’]}*\n”
f”   🎯  Win Rate »  *{wr:.1f}%*  ({total} trades)\n\n”
f”   {win_bar(wr)}\n\n”
f”▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n”
f”{footer}\n\n”
f”*🎿 Skii Pro Signals — Premium OTC Alerts*”
)

def build_stats_msg(stats: dict) -> str:
total    = stats[“wins”] + stats[“losses”]
wr       = winrate(stats[“wins”], stats[“losses”])
d_wr     = winrate(stats[“daily_wins”], stats[“daily_losses”])
d_tot    = stats[“daily_wins”] + stats[“daily_losses”]
time_str = datetime.now(timezone.utc).strftime(”%d %b %Y  •  %H:%M UTC”)
verdict  = (
“🔥 *Exceptional run! Skii Pro is delivering.*”        if wr >= 75 else
“⚡ *Strong performance. Keep following the signals.*”  if wr >= 60 else
“📊 *Solid. Consistency is everything.*”               if wr >= 50 else
“💪 *Variance is normal. The edge plays out over time.*”
)
return (
f”🎿 *SKII PRO SIGNALS*\n”
f”▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n”
f”📊 *PERFORMANCE REPORT*\n”
f”🕐 *{time_str}*\n\n”
f”📅 *TODAY*\n”
f”   ✅  Wins      »  *{stats[‘daily_wins’]}*\n”
f”   ❌  Losses   »  *{stats[‘daily_losses’]}*\n”
f”   🎯  Win Rate »  *{d_wr:.1f}%*  ({d_tot} trades)\n”
f”   {win_bar(d_wr)}\n\n”
f”▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n”
f”📆 *ALL TIME*\n”
f”   ✅  Wins      »  *{stats[‘wins’]}*\n”
f”   ❌  Losses   »  *{stats[‘losses’]}*\n”
f”   🎯  Win Rate »  *{wr:.1f}%*  ({total} trades)\n”
f”   {win_bar(wr)}\n\n”
f”▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n”
f”{verdict}\n\n”
f”*🎿 Skii Pro Signals — Premium OTC Alerts*”
)

def build_weekly_msg(stats: dict) -> str:
w_wins   = stats.get(“weekly_wins”, 0)
w_losses = stats.get(“weekly_losses”, 0)
w_total  = w_wins + w_losses
w_wr     = winrate(w_wins, w_losses)
all_wr   = winrate(stats[“wins”], stats[“losses”])
week     = datetime.now(timezone.utc).strftime(“Week %W, %Y”)
verdict  = (
“🔥 *What a week! Absolutely elite performance.*”              if w_wr >= 75 else
“⚡ *Solid week. The signals are working.*”                     if w_wr >= 60 else
“📊 *Decent week. More to come.*”                              if w_wr >= 50 else
“💪 *Tough week but we stay consistent. New week, new pips.*”
)
return (
f”🎿 *SKII PRO SIGNALS*\n”
f”▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n”
f”📆 *WEEKLY RECAP*  |  *{week}*\n\n”
f”   ✅  Wins      »  *{w_wins}*\n”
f”   ❌  Losses   »  *{w_losses}*\n”
f”   📊  Total    »  *{w_total} trades*\n”
f”   🎯  Win Rate »  *{w_wr:.1f}%*\n\n”
f”   {win_bar(w_wr)}\n\n”
f”▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n”
f”📈 *All Time Win Rate »  {all_wr:.1f}%*\n\n”
f”▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n”
f”{verdict}\n\n”
f”*🎿 Skii Pro Signals — Premium OTC Alerts*”
)

# ── Continuous Scanner Job ────────────────────────────────────────────────────

async def scanner_job(context: ContextTypes.DEFAULT_TYPE):
“”“Scans all pairs every 2 min. Fires when 6+/8 indicators agree.”””
if bot_paused:
return

```
global last_signal_time, last_signal_message_id

# Global cooldown — only 1 signal every 5 minutes
now = datetime.now(timezone.utc)
if last_signal_time and (now - last_signal_time).total_seconds() < GLOBAL_SIGNAL_COOLDOWN:
    return

loop        = asyncio.get_event_loop()
best_signal = None  # (pair, signal_dict)
best_score  = 0

for pair in active_pairs:
    if pair_on_cooldown(pair):
        continue
    try:
        signal = await loop.run_in_executor(None, generate_signal, pair)
    except Exception as e:
        print(f"⚠️ Data fetch failed for {pair}: {e}")
        continue

    # Only consider signals with 3+ indicators agreeing (out of 5)
    if abs(signal["score"]) < 3:
        continue

    # Pick the strongest signal this cycle
    if abs(signal["score"]) > best_score:
        best_score  = abs(signal["score"])
        best_signal = (pair, signal)

if not best_signal:
    print("🔍 Scan complete — no signal met 3/5 threshold.")
    return

pair, signal = best_signal
direction    = signal["direction"]
entry_price  = signal["entry_price"]

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
        data={"pair": pair, "direction": direction, "entry_price": entry_price},
    )

    print(f"✅ Signal fired: {pair} {direction} ({signal['score']}/8)")

except Exception as e:
    print(f"⚠️ Failed to send signal: {e}")
```

async def reveal_prediction_job(context: ContextTypes.DEFAULT_TYPE):
data      = context.job.data
pair      = data[“pair”]
direction = data[“direction”]
emoji     = “📈 CALL” if direction == “CALL” else “📉 PUT”
text      = build_msg(
f”🎯 PREDICTION REVEAL — {pair}”,
f”The signal was: *{emoji}*\n\nDid you get it right? 👀”,
“🔒 Paid members already placed this trade!\nGet signals before the reveal 👇\n👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)

async def result_job(context: ContextTypes.DEFAULT_TYPE):
data        = context.job.data
pair        = data[“pair”]
direction   = data[“direction”]
entry_price = data[“entry_price”]

```
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

# Log trade to history
log_trade(pair, direction, entry_price, exit_price, is_win)
# Update best streak ever
if streak["count"] > best_streak["count"]:
    best_streak["count"] = streak["count"]
    best_streak["type"]  = streak["type"]
```

async def stats_job(context: ContextTypes.DEFAULT_TYPE):
stats = load_stats(); stats = maybe_reset_daily(stats); save_stats(stats)
text  = build_stats_msg(stats)
# Post to both groups
await context.bot.send_message(chat_id=CHANNEL_ID,    text=text, parse_mode=“Markdown”)
await send_to_free_group(context.bot, text)

async def daily_tip_job(context: ContextTypes.DEFAULT_TYPE):
“”“Posts a trading tip every weekday morning at 07:00 UTC.”””
now = datetime.now(timezone.utc)
if now.weekday() >= 5 or now.hour != 7:
return

```
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
```

async def leaderboard_job(context: ContextTypes.DEFAULT_TYPE):
“”“Posts leaderboard every day at 20:00 UTC.”””
now = datetime.now(timezone.utc)
if now.hour != 20:
return
lb   = load_leaderboard()
text = build_leaderboard_msg(lb)
# Post to both groups
await context.bot.send_message(chat_id=CHANNEL_ID,    text=text, parse_mode=“Markdown”)
await send_to_free_group(context.bot, text)

```
if datetime.now(timezone.utc).weekday() != 0:
    return
stats = load_stats(); stats = maybe_reset_weekly(stats); save_stats(stats)
await context.bot.send_message(chat_id=CHANNEL_ID, text=build_weekly_msg(stats), parse_mode="Markdown")
```

async def weekly_recap_job(context: ContextTypes.DEFAULT_TYPE):
“”“Posts weekly recap every Monday at 08:00 UTC.”””
now = datetime.now(timezone.utc)
if now.weekday() != 0 or now.hour != 8:
return
stats = load_stats()
stats = maybe_reset_weekly(stats)
save_stats(stats)
await context.bot.send_message(chat_id=CHANNEL_ID, text=build_weekly_msg(stats), parse_mode=“Markdown”)

async def market_open_job(context: ContextTypes.DEFAULT_TYPE):
now = datetime.now(timezone.utc)
if now.weekday() >= 5:
return
if now.hour == 7:
session_name = “🇬🇧 LONDON SESSION OPEN”
hype = “London is live — liquidity is high and the scanner is running hot. Let’s get to work! 💼”
elif now.hour == 12:
session_name = “🇺🇸 NEW YORK SESSION OPEN”
hype = “New York just opened — maximum volatility, maximum opportunity! 🗽”
else:
return

```
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
```

# ── Welcome Handler ───────────────────────────────────────────────────────────

async def welcome_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
result: ChatMemberUpdated = update.chat_member
old_status = result.old_chat_member.status
new_status = result.new_chat_member.status
if not (old_status in (“left”, “kicked”, “restricted”) and new_status in (“member”, “administrator”)):
return

```
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
```

# ── Member Commands (work inside the group) ───────────────────────────────────

async def cmd_mywin(update: Update, context: ContextTypes.DEFAULT_TYPE):
user     = update.effective_user
uid      = str(user.id)
name     = user.first_name or “Trader”
lb       = update_leaderboard(uid, name, is_win=True)
entry    = lb[uid]
wr       = winrate(entry[“wins”], entry[“losses”])
total    = entry[“wins”] + entry[“losses”]
await update.message.reply_text(
f”✅ *WIN logged, {name}!*\n\n”
f”Your record: ✅ {entry[‘wins’]}W  ❌ {entry[‘losses’]}L  🎯 {wr:.0f}%  ({total} trades)\n\n”
f”*Keep it up! Use /leaderboard to see the rankings.*”,
parse_mode=“Markdown”
)

async def cmd_myloss(update: Update, context: ContextTypes.DEFAULT_TYPE):
user     = update.effective_user
uid      = str(user.id)
name     = user.first_name or “Trader”
lb       = update_leaderboard(uid, name, is_win=False)
entry    = lb[uid]
wr       = winrate(entry[“wins”], entry[“losses”])
total    = entry[“wins”] + entry[“losses”]
await update.message.reply_text(
f”❌ *LOSS logged, {name}.*\n\n”
f”Your record: ✅ {entry[‘wins’]}W  ❌ {entry[‘losses’]}L  🎯 {wr:.0f}%  ({total} trades)\n\n”
f”*Stay disciplined. Use /leaderboard to see the rankings.*”,
parse_mode=“Markdown”
)

async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
lb   = load_leaderboard()
text = build_leaderboard_msg(lb)
await update.message.reply_text(text, parse_mode=“Markdown”)

def log_admin(cmd: str, args: str = “”):
“”“Prints admin command to Railway deploy logs.”””
now = datetime.now(timezone.utc).strftime(”%H:%M:%S UTC”)
extra = f” {args}” if args else “”
print(f”👤 ADMIN  [{now}]  /{cmd}{extra}”)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“start”, “”)
await update.message.reply_text(
“🎿 *SKII PRO SIGNALS*\n”
“▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n”
“🔧 *ADMIN PANEL*\n\n”
“🔴 *TikTok Live*\n”
“/live [link] — Go live announcement 🔴\n”
“/endlive — End live + recap ⚫\n”
“/countdown [min] — Signal hype timer ⏳\n”
“/scoreboard — Live stats board 📊\n”
“/lastwins — Show last 5 wins 🏆\n”
“/promo — Sales message 💎\n”
“/slots [n] — Limited spots urgency ⚠️\n”
“/discount [%] — Live discount 🔥\n”
“/giveaway [prize] — Giveaway post 🎁\n”
“/challenge [n] — Trade challenge 🎯\n”
“/shoutout [@user] — Member shoutout 🏆\n”
“/members [n] — Member count flex 🔥\n”
“/link — Post Whop link 🔗\n\n”
“📡 *Scanner Control*\n”
“/pause — Pause all signals ⏸\n”
“/resume — Resume signals ▶️\n”
“/signal — Force scan now 🔍\n”
“/expiry [min] — Change expiry ⏱\n”
“/cooldown [min] — Pair cooldown ⏳\n”
“/setpairs — Choose pairs to scan 🎯\n”
“/resetpairs — Restore all 22 pairs 🔄\n\n”
“📊 *Stats & Info*\n”
“/status — Full live bot status\n”
“/today — Today’s trade history 📅\n”
“/winstreak — Current & best streak 🔥\n”
“/drawdown — Performance check 📉\n”
“/revenue [n] — Revenue tracker 💰\n”
“/stats — Post stats to group\n”
“/weekly — Post weekly recap 📆\n\n”
“📣 *Communication*\n”
“/broadcast [msg] — Announcement 📣\n”
“/warn [msg] — Market warning ⚠️\n”
“/maintenance — Maintenance mode 🔧\n”
“/tip [msg] — Post custom tip 📚\n”
“/motivate — Motivation message 💪\n\n”
“🎮 *Fun & Engagement*\n”
“/pin — Pin last signal 📌\n\n”
“🛠 *Manual Overrides*\n”
“/forceresult WIN/LOSS — Manual result ✅\n”
“/reset — Reset all stats to zero 🔄”,
parse_mode=“Markdown”
)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“status”, “”)
stats   = load_stats()
total   = stats[“wins”] + stats[“losses”]
wr      = winrate(stats[“wins”], stats[“losses”])
on_cd   = [p for p in active_pairs if pair_on_cooldown(p)]
ready   = [p for p in active_pairs if not pair_on_cooldown(p)]
paused_status = “⏸ PAUSED” if bot_paused else “✅ RUNNING”

```
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
```

async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“signal”, “”)
await scanner_job(context)
await update.message.reply_text(“🔍 Scanner triggered — signal posted if setup found.”)

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“stats”, “”)
await stats_job(context)
await update.message.reply_text(“📊 Stats posted to group.”)

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“weekly”, “”)
stats = load_stats()
await context.bot.send_message(chat_id=CHANNEL_ID, text=build_weekly_msg(stats), parse_mode=“Markdown”)
await update.message.reply_text(“📆 Weekly recap posted.”)

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“reset”, “”)
save_stats({
“wins”: 0, “losses”: 0, “daily_wins”: 0, “daily_losses”: 0,
“weekly_wins”: 0, “weekly_losses”: 0,
“last_reset”: today_str(), “last_week_reset”: week_str(),
“celebrated_trades”: [], “celebrated_winrates”: [],
})
await update.message.reply_text(“🔄 All stats reset to zero.”)

# ── TikTok Live Commands ──────────────────────────────────────────────────────

async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“live”, “ “.join(context.args) if context.args else “”)
tiktok_link = “ “.join(context.args) if context.args else “tiktok.com/@skii”
text = build_msg(
“🔴 SKII IS LIVE ON TIKTOK!”,
“   📱  Jump on the stream — signals dropping live!\n”
“   💰  Real trades, real results, in real time\n”
“   👀  Watch the wins happen live”,
f”👉 *{tiktok_link}*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(“🔴 Live announcement posted!”)

async def cmd_endlive(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“endlive”, “”)
stats = load_stats()
d_wr  = winrate(stats[“daily_wins”], stats[“daily_losses”])
text  = build_msg(
“⚫ LIVE SESSION ENDED”,
f”Thanks for watching! Today’s recap:\n\n”
f”   ✅  Wins      »  *{stats[‘daily_wins’]}*\n”
f”   ❌  Losses   »  *{stats[‘daily_losses’]}*\n”
f”   🎯  Win Rate »  *{d_wr:.1f}%*”,
“🔒 Want signals on the next live?\n👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(“⚫ End live posted!”)

async def cmd_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“countdown”, “ “.join(context.args) if context.args else “”)
mins = int(context.args[0]) if context.args else 5
text = build_msg(
f”⏳ SIGNAL DROPPING IN {mins} MINUTES!”,
“   📱  Open Pocket Option and get ready\n”
“   🔒  Paid members get it first\n”
“   👀  Free group gets the result after”,
“🔥 Don’t miss it — join Skii Pro now!\n👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(f”⏳ Countdown posted!”)

async def cmd_lastwins(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“lastwins”, “”)
history = load_history()
wins    = [t for t in history if t[“result”] == “WIN”][-5:]
if not wins:
await update.message.reply_text(“No wins recorded yet.”)
return
rows  = “\n”.join([f”   ✅  {t[‘pair’]} {t[‘direction’]} — {t[‘time’]}” for t in reversed(wins)])
stats = load_stats()
wr    = winrate(stats[“wins”], stats[“losses”])
text  = build_msg(
“🏆 LAST 5 WINS”,
f”{rows}\n\n   🎯  Win Rate »  *{wr:.1f}%*”,
“🔒 Want these signals live?\n👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(“🏆 Last 5 wins posted!”)

async def cmd_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“promo”, “”)
stats = load_stats()
total = stats[“wins”] + stats[“losses”]
wr    = winrate(stats[“wins”], stats[“losses”])
text  = build_msg(
“💎 PREMIUM OTC SIGNAL SERVICE”,
f”   ✅  *{wr:.1f}%* Win Rate ({total} trades)\n”
f”   ✅  22 OTC Pairs Scanned\n”
f”   ✅  8 Independent Indicators\n”
f”   ✅  Signal Every 5 Minutes\n”
f”   ✅  Auto WIN/LOSS Detection\n”
f”   ✅  24/7 Automated\n\n”
f”💰 *Plans*\n”
f”   📅  Monthly  »  *$25/month*\n”
f”   ♾️  Lifetime »  *$150 one-time*”,
“👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(“📣 Promo posted!”)

async def cmd_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“slots”, “ “.join(context.args) if context.args else “”)
slots = int(context.args[0]) if context.args else 10
text  = build_msg(
f”⚠️ ONLY {slots} SPOTS LEFT!”,
“   🔥  This price won’t last long\n”
“   ⏰  Limited availability\n”
“   💎  Lock in your spot now”,
“👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(f”⚠️ {slots} slots posted!”)

async def cmd_discount(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“discount”, “ “.join(context.args) if context.args else “”)
pct  = context.args[0] if context.args else “20”
text = build_msg(
f”🔥 LIVE SPECIAL — {pct}% OFF!”,
f”   ⏰  Today only — live viewers exclusive\n”
f”   💰  Monthly now *${int(25*(1-int(pct)/100))}*\n”
f”   ♾️  Lifetime now *${int(150*(1-int(pct)/100))}*”,
“👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(f”🔥 {pct}% discount posted!”)

async def cmd_scoreboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“scoreboard”, “”)
stats = load_stats()
total = stats[“wins”] + stats[“losses”]
wr    = winrate(stats[“wins”], stats[“losses”])
d_wr  = winrate(stats[“daily_wins”], stats[“daily_losses”])
d_tot = stats[“daily_wins”] + stats[“daily_losses”]
text  = build_msg(
“📊 LIVE SCOREBOARD”,
f”📅 *Today*\n”
f”   ✅  Wins      »  *{stats[‘daily_wins’]}*\n”
f”   ❌  Losses   »  *{stats[‘daily_losses’]}*\n”
f”   🎯  Win Rate »  *{d_wr:.1f}%*  ({d_tot} trades)\n\n”
f”📆 *All Time*\n”
f”   ✅  Wins      »  *{stats[‘wins’]}*\n”
f”   ❌  Losses   »  *{stats[‘losses’]}*\n”
f”   🎯  Win Rate »  *{wr:.1f}%*  ({total} trades)\n\n”
f”   {win_bar(wr)}\n\n”
f”🔥 *Streak: {streak[‘count’]} {streak[‘type’] or ‘none’}*”,
“🔒 Get the signals that made this happen!\n👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(“📊 Scoreboard posted!”)

async def cmd_shoutout(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“shoutout”, “ “.join(context.args) if context.args else “”)
if not context.args:
await update.message.reply_text(“Usage: `/shoutout @username great trading today!`”, parse_mode=“Markdown”)
return
mention = context.args[0]
msg     = “ “.join(context.args[1:]) if len(context.args) > 1 else “killing it in the signals! 🔥”
text    = build_msg(
“🏆 MEMBER SHOUTOUT”,
f”Big up to *{mention}* — {msg}”,
“*This is what Skii Pro members are doing. Want in?*\n👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(“🏆 Shoutout posted!”)

async def cmd_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“giveaway”, “ “.join(context.args) if context.args else “”)
prize = “ “.join(context.args) if context.args else “1 week free access to Skii Pro”
text  = build_msg(
“🎁 GIVEAWAY — LIVE EXCLUSIVE!”,
f”   🏆  Prize: *{prize}*\n\n”
f”   To enter:\n”
f”   1️⃣  Follow on TikTok\n”
f”   2️⃣  Comment ‘SKII’ on the live\n”
f”   3️⃣  DM to claim if you win!”,
“🔥 Good luck everyone!”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(“🎁 Giveaway posted!”)

async def cmd_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“challenge”, “ “.join(context.args) if context.args else “”)
trades = context.args[0] if context.args else “10”
text   = build_msg(
f”🎯 LIVE CHALLENGE — {trades} TRADES!”,
f”Skii just called *{trades} trades live* on TikTok.\n”
f”Watch the results drop in real time!”,
“🔒 Want to trade along? Join Skii Pro!\n👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(“🎯 Challenge posted!”)

async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“link”, “”)
text = build_msg(
“🔗 JOIN SKII PRO NOW”,
“   💎  Premium OTC Signals\n”
“   📅  Monthly: *$25/month*\n”
“   ♾️  Lifetime: *$150*”,
“👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(“🔗 Link posted!”)

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“pause”, “”)
global bot_paused
bot_paused = True
await update.message.reply_text(“⏸ Signals paused. Use /resume to restart.”)
await context.bot.send_message(
chat_id=CHANNEL_ID, parse_mode=“Markdown”,
text=build_msg(“⏸ SIGNALS TEMPORARILY PAUSED”, “*We’ll be back shortly. Stay tuned!*”)
)

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“resume”, “”)
global bot_paused
bot_paused = False
await update.message.reply_text(“▶️ Signals resumed!”)
await context.bot.send_message(
chat_id=CHANNEL_ID, parse_mode=“Markdown”,
text=build_msg(
“▶️ SIGNALS BACK LIVE!”,
f”   🔍  Scanner active on all 22 pairs\n”
f”   ⏱  Expiry: *{expiry_mins} minutes*\n”
f”   🔥  Signals firing every 5 minutes”
)
)

async def cmd_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“expiry”, “ “.join(context.args) if context.args else “”)
global expiry_mins
if not context.args:
await update.message.reply_text(
f”⏱ Current expiry is *{expiry_mins} minutes*.\n\nUsage: `/expiry 1` `/expiry 3` `/expiry 5`”,
parse_mode=“Markdown”
)
return
try:
new_expiry = int(context.args[0])
if new_expiry not in [1, 2, 3, 5, 10, 15]:
await update.message.reply_text(“⚠️ Use one of: 1, 2, 3, 5, 10, 15 minutes.”, parse_mode=“Markdown”)
return
expiry_mins = new_expiry
await update.message.reply_text(f”✅ Expiry updated to *{expiry_mins} minutes!*”, parse_mode=“Markdown”)
except ValueError:
await update.message.reply_text(“⚠️ Invalid. Example: `/expiry 5`”, parse_mode=“Markdown”)

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“broadcast”, “ “.join(context.args) if context.args else “”)
if not context.args:
await update.message.reply_text(“Usage: `/broadcast Your message here`”, parse_mode=“Markdown”)
return
message = “ “.join(context.args)
text    = build_msg(“📣 ANNOUNCEMENT”, message)
await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=“Markdown”)
await update.message.reply_text(“✅ Broadcast sent!”)

async def cmd_setpairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“setpairs”, “ “.join(context.args) if context.args else “”)
global active_pairs
if not context.args:
current = “, “.join(active_pairs)
pairs_list = “\n”.join([f”• `{p}`” for p in OTC_PAIRS.keys()])
await update.message.reply_text(
f”📡 *Active pairs:*\n{current}\n\n*All available pairs:*\n{pairs_list}\n\n”
f”Usage: `/setpairs EUR/USD OTC GBP/USD OTC`”,
parse_mode=“Markdown”
)
return
requested = “ “.join(context.args)
new_pairs = [p for p in OTC_PAIRS.keys() if p.replace(” OTC”,””).replace(”/”,””) in requested.replace(”/”,””).replace(” “,””).upper() or p in requested]
if not new_pairs:
await update.message.reply_text(“⚠️ No valid pairs found. Use full names like `EUR/USD OTC`”, parse_mode=“Markdown”)
return
active_pairs = new_pairs
await update.message.reply_text(f”✅ *Active pairs updated!*\n\n” + “\n”.join([f”• {p}” for p in active_pairs]), parse_mode=“Markdown”)

async def cmd_resetpairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“resetpairs”, “”)
global active_pairs
active_pairs = list(OTC_PAIRS.keys())
await update.message.reply_text(“✅ *All 9 pairs restored!*”, parse_mode=“Markdown”)

async def cmd_setscore(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“setscore”, “ “.join(context.args) if context.args else “”)
global min_score
if not context.args:
await update.message.reply_text(f”🎯 Current min score: *{min_score}/8*\n\nUsage: `/setscore 5`”, parse_mode=“Markdown”)
return
try:
val = int(context.args[0])
if val < 1 or val > 8:
await update.message.reply_text(“⚠️ Score must be between 1 and 8.”, parse_mode=“Markdown”)
return
min_score = val
await update.message.reply_text(f”✅ *Min score updated to {min_score}/8!*\n\nSignals now fire when {min_score}+ indicators agree.”, parse_mode=“Markdown”)
except ValueError:
await update.message.reply_text(“⚠️ Invalid number. Example: `/setscore 5`”, parse_mode=“Markdown”)

async def cmd_setcooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“cooldown”, “ “.join(context.args) if context.args else “”)
global pair_cooldown
if not context.args:
await update.message.reply_text(f”⏱ Current cooldown: *{pair_cooldown//60} min*\n\nUsage: `/cooldown 10`”, parse_mode=“Markdown”)
return
try:
mins = int(context.args[0])
if mins < 1 or mins > 60:
await update.message.reply_text(“⚠️ Cooldown must be between 1 and 60 minutes.”, parse_mode=“Markdown”)
return
pair_cooldown = mins * 60
await update.message.reply_text(f”✅ *Pair cooldown updated to {mins} minutes!*”, parse_mode=“Markdown”)
except ValueError:
await update.message.reply_text(“⚠️ Invalid number. Example: `/cooldown 10`”, parse_mode=“Markdown”)

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“today”, “”)
history = load_history()
today   = today_str()
trades  = [t for t in history if t.get(“date”) == today]
if not trades:
await update.message.reply_text(“📅 No trades recorded today yet.”, parse_mode=“Markdown”)
return
wins   = sum(1 for t in trades if t[“result”] == “WIN”)
losses = len(trades) - wins
wr     = winrate(wins, losses)
rows   = “”
for t in trades[-20:]:  # show last 20
emoji = “✅” if t[“result”] == “WIN” else “❌”
rows += f”{emoji} `{t['time']}` — {t[‘pair’]} {t[‘direction’]} ({t[‘pips’]} pips)\n”
await update.message.reply_text(
f”📅 *Today’s Trades*\n\n{rows}\n”
f”✅ {wins}W  ❌ {losses}L  🎯 {wr:.1f}%  ({len(trades)} total)”,
parse_mode=“Markdown”
)

async def cmd_winstreak(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“winstreak”, “”)
await update.message.reply_text(
f”🔥 *Streak Info*\n\n”
f”Current streak : *{streak[‘count’]} {streak[‘type’] or ‘none’}*\n”
f”Best streak    : *{best_streak[‘count’]} {best_streak[‘type’] or ‘none’}*”,
parse_mode=“Markdown”
)

async def cmd_revenue(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“revenue”, “ “.join(context.args) if context.args else “”)
global member_count
if context.args:
try:
member_count = int(context.args[0])
except ValueError:
pass
monthly_rev  = member_count * 25  # estimate $25/mo per member
lifetime_rev = member_count * 150
await update.message.reply_text(
f”💰 *Revenue Tracker*\n\n”
f”Members       : *{member_count}*\n\n”
f”Est. Monthly  : *${monthly_rev}*  (@ $25/mo)\n”
f”Est. Lifetime : *${lifetime_rev}*  (@ $150)\n\n”
f”*Update member count: `/revenue 50`*”,
parse_mode=“Markdown”
)

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“warn”, “ “.join(context.args) if context.args else “”)
msg  = “ “.join(context.args) if context.args else “Market conditions are unfavourable right now. Trade with caution.”
text = build_msg(“⚠️ MARKET WARNING”, msg)
await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=“Markdown”)
await update.message.reply_text(“⚠️ Warning posted!”)

async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“maintenance”, “”)
global bot_paused
bot_paused = True
text = build_msg(
“🔧 MAINTENANCE MODE”,
“Signals are temporarily offline.\nWe’ll be back shortly — thanks for your patience! 🙏”
)
await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=“Markdown”)
await update.message.reply_text(“🔧 Maintenance mode on. Use /resume when ready.”)

async def cmd_manualtip(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“tip”, “ “.join(context.args) if context.args else “”)
if not context.args:
await update.message.reply_text(“Usage: `/tip Your tip here`”, parse_mode=“Markdown”)
return
tip_text = “ “.join(context.args)
text     = build_msg(“📚 TRADING TIP”, f”💡 {tip_text}”)
await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=“Markdown”)
await update.message.reply_text(“📚 Tip posted!”)

async def cmd_drawdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“drawdown”, “”)
stats   = load_stats()
all_wr  = winrate(stats[“wins”], stats[“losses”])
day_wr  = winrate(stats[“daily_wins”], stats[“daily_losses”])
diff    = all_wr - day_wr
d_total = stats[“daily_wins”] + stats[“daily_losses”]
if d_total == 0:
await update.message.reply_text(“📊 No trades today yet.”)
return
status = (
f”⚠️ *Significant drawdown!* Down {diff:.1f}% from average.” if diff > 15 else
f”📉 *Slight drawdown.* Down {diff:.1f}% from average.”      if diff > 5  else
f”✅ *Performing normally.* Within {abs(diff):.1f}% of average.”
)
await update.message.reply_text(
f”📊 *Drawdown Report*\n\n”
f”All time : *{all_wr:.1f}%*\n”
f”Today    : *{day_wr:.1f}%*\n”
f”Diff     : *{diff:+.1f}%*\n\n{status}”,
parse_mode=“Markdown”
)

async def cmd_forceresult(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“forceresult”, “ “.join(context.args) if context.args else “”)
if not context.args:
await update.message.reply_text(“Usage: `/forceresult WIN` or `/forceresult LOSS`”, parse_mode=“Markdown”)
return
outcome = context.args[0].upper()
if outcome not in [“WIN”, “LOSS”]:
await update.message.reply_text(“⚠️ Must be WIN or LOSS.”)
return
is_win = outcome == “WIN”
stats  = load_stats()
stats  = maybe_reset_daily(stats); stats = maybe_reset_weekly(stats)
if is_win:
stats[“wins”] += 1; stats[“daily_wins”] += 1; stats[“weekly_wins”] += 1
else:
stats[“losses”] += 1; stats[“daily_losses”] += 1; stats[“weekly_losses”] += 1
save_stats(stats)
wr    = winrate(stats[“wins”], stats[“losses”])
total = stats[“wins”] + stats[“losses”]
emoji = “✅” if is_win else “❌”
text  = build_msg(
f”{emoji} RESULT UPDATE — {outcome}”,
f”   ✅  Wins      »  *{stats[‘wins’]}*\n”
f”   ❌  Losses   »  *{stats[‘losses’]}*\n”
f”   🎯  Win Rate »  *{wr:.1f}%*  ({total} trades)\n\n”
f”   {win_bar(wr)}”
)
await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=“Markdown”)
await update.message.reply_text(f”{emoji} Manual {outcome} recorded and posted!”)

async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“pin”, “”)
if not last_signal_message_id:
await update.message.reply_text(“⚠️ No signal posted yet to pin.”)
return
try:
await context.bot.pin_chat_message(chat_id=CHANNEL_ID, message_id=last_signal_message_id, disable_notification=False)
await update.message.reply_text(“📌 Last signal pinned!”)
except Exception as e:
await update.message.reply_text(f”❌ Failed to pin: `{e}`\n\nMake sure bot has pin permission.”, parse_mode=“Markdown”)

async def cmd_motivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“motivate”, “”)
import random as *random
quote, emoji = *random.choice(MOTIVATIONAL_QUOTES)
text = build_msg(
f”{emoji} MOTIVATION”,
f”*{quote}*”,
“💪 *Keep grinding. Skii Pro is with you every step.*”
)
await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=“Markdown”)
await update.message.reply_text(“💪 Motivation posted!”)

async def cmd_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != ADMIN_ID: return
log_admin(“members”, “ “.join(context.args) if context.args else “”)
global member_count
if context.args:
try:
member_count = int(context.args[0])
except ValueError:
pass
s    = load_stats()
wr   = winrate(s[“wins”], s[“losses”])
text = build_msg(
f”🔥 {member_count} TRADERS ALREADY IN SKII PRO!”,
f”   💎  Premium signals every 5 minutes\n”
f”   📊  {wr:.1f}% Win Rate\n”
f”   🤖  Fully automated 24/7”,
“⏰ Don’t miss out — join the team!\n👉 *whop.com/skiiprosignals*”
)
await send_to_free_group(context.bot, text)
await update.message.reply_text(f”🔥 Member count ({member_count}) posted!”)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
import traceback
from telegram.error import Conflict, NetworkError, TimedOut
err = context.error
if isinstance(err, Conflict):
print(“⚠️ Conflict: another instance detected. Waiting 5s…”)
await asyncio.sleep(5)
elif isinstance(err, (NetworkError, TimedOut)):
print(f”⚠️ Network error: {err}. Will retry.”)
else:
print(f”❌ Error: {traceback.format_exc()}”)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
app = Application.builder().token(TELEGRAM_TOKEN).build()

```
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
app.run_polling(
    drop_pending_updates=True,
    allowed_updates=["message", "callback_query", "chat_member"],
)
```

if **name** == “**main**”:
main()
