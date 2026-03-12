import os
import time
import json
import requests
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────────
JSONBIN_KEY = '$2a$10$GVJAWrFcqVEjhXlZfI9.8OtzhH/KuN06w6NpkWLyID9Y9rDqgQgMi'
JSONBIN_BIN = '69b11b67cadcd23453e5c1ce'
JSONBIN_URL = f'https://api.jsonbin.io/v3/b/{JSONBIN_BIN}'

STARTING_CASH  = 100.0
TRADE_SIZE     = 100.0
TAKE_PROFIT    = 1.0
STOP_LOSS      = 0.50
SCAN_INTERVAL  = 60
PRICE_INTERVAL = 5

COINS = [
    {'id': 'bitcoin',     'sym': 'BTC', 'binance': 'BTCUSDT'},
    {'id': 'ethereum',    'sym': 'ETH', 'binance': 'ETHUSDT'},
    {'id': 'solana',      'sym': 'SOL', 'binance': 'SOLUSDT'},
    {'id': 'binancecoin', 'sym': 'BNB', 'binance': 'BNBUSDT'},
    {'id': 'ripple',      'sym': 'XRP', 'binance': 'XRPUSDT'},
]

# ── STATE ────────────────────────────────────────────────────
state = {
    'cash': STARTING_CASH,
    'active': None,
    'closed': [],
    'savedAt': datetime.utcnow().isoformat()
}
price_history = {c['sym']: [] for c in COINS}

# ── MATH (bez numpy) ─────────────────────────────────────────
def mean(arr):
    return sum(arr) / len(arr) if arr else 0

def std(arr):
    if len(arr) < 2: return 0
    m = mean(arr)
    return (sum((x - m) ** 2 for x in arr) / len(arr)) ** 0.5

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    deltas = [prices[i] - prices[i-1] for i in range(len(prices)-period, len(prices))]
    gains  = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = mean(gains) if gains else 0
    avg_loss = mean(losses) if losses else 0.001
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_ema(prices, period):
    if len(prices) < period: return prices[-1] if prices else 0
    k = 2 / (period + 1)
    ema = prices[-period]
    for p in prices[-period+1:]:
        ema = p * k + ema * (1 - k)
    return ema

def calc_macd(prices):
    if len(prices) < 26: return 0
    m = calc_ema(prices, 12) - calc_ema(prices, 26)
    return m - (m * 0.85)

def calc_bb_pct(prices, period=20):
    if len(prices) < period: return 50
    sl = prices[-period:]
    mid = mean(sl)
    s = std(sl)
    upper = mid + 2 * s
    lower = mid - 2 * s
    cur = prices[-1]
    return ((cur - lower) / (upper - lower)) * 100 if (upper - lower) else 50

# ── JSONBIN ──────────────────────────────────────────────────
def load_state():
    global state
    try:
        res = requests.get(f'{JSONBIN_URL}/latest',
            headers={'X-Master-Key': JSONBIN_KEY}, timeout=10)
        if res.ok:
            data = res.json().get('record', {})
            if data.get('savedAt'):
                state = data
                state['cash'] = float(state.get('cash', STARTING_CASH))
                print(f"[LOAD] Cash: ${state['cash']:.2f} | Trades: {len(state.get('closed', []))}")
                return True
    except Exception as e:
        print(f"[LOAD ERROR] {e}")
    return False

def save_state():
    try:
        payload = {**state, 'savedAt': datetime.utcnow().isoformat()}
        res = requests.put(JSONBIN_URL, json=payload,
            headers={'Content-Type': 'application/json',
                     'X-Master-Key': JSONBIN_KEY,
                     'X-Bin-Versioning': 'false'}, timeout=10)
        if res.ok:
            print(f"[SAVE] OK — Cash: ${state['cash']:.2f}")
        else:
            print(f"[SAVE ERROR] {res.status_code}")
    except Exception as e:
        print(f"[SAVE ERROR] {e}")

# ── PRICES ───────────────────────────────────────────────────
def fetch_prices():
    # Binance
    try:
        symbols = json.dumps([c['binance'] for c in COINS])
        res = requests.get('https://api.binance.com/api/v3/ticker/24hr',
            params={'symbols': symbols}, timeout=10)
        if res.ok:
            prices = {}
            for t in res.json():
                coin = next((c for c in COINS if c['binance'] == t['symbol']), None)
                if coin:
                    p = float(t['lastPrice'])
                    prices[coin['sym']] = p
                    price_history[coin['sym']].append(p)
                    if len(price_history[coin['sym']]) > 50:
                        price_history[coin['sym']].pop(0)
            print("[PRICES] Binance: " + " | ".join([f"{s}:${p:.4f}" for s,p in prices.items()]))
            return prices
    except Exception as e:
        print(f"[PRICES] Binance failed: {e}")

    # CoinGecko fallback
    try:
        ids = ','.join([c['id'] for c in COINS])
        res = requests.get('https://api.coingecko.com/api/v3/simple/price',
            params={'ids': ids, 'vs_currencies': 'usd'}, timeout=10)
        if res.ok:
            data = res.json()
            prices = {}
            for c in COINS:
                if c['id'] in data:
                    p = float(data[c['id']]['usd'])
                    prices[c['sym']] = p
                    price_history[c['sym']].append(p)
                    if len(price_history[c['sym']]) > 50:
                        price_history[c['sym']].pop(0)
            print("[PRICES] CoinGecko: " + " | ".join([f"{s}:${p:.4f}" for s,p in prices.items()]))
            return prices
    except Exception as e:
        print(f"[PRICES] CoinGecko failed: {e}")

    return {}

# ── INDICATORS ───────────────────────────────────────────────
def analyze_coin(sym):
    ph = price_history[sym]
    if len(ph) < 5: return 0, []
    rsi  = calc_rsi(ph)
    macd = calc_macd(ph)
    bb   = calc_bb_pct(ph)
    ema_cross = calc_ema(ph, 9) - calc_ema(ph, 21) if len(ph) >= 21 else 0
    score, sigs = 0, []
    if rsi < 45:      score += 1; sigs.append('RSI↓')
    if macd > 0:      score += 1; sigs.append('MACD+')
    if bb < 45:       score += 1; sigs.append('BB↓')
    if ema_cross > 0: score += 1; sigs.append('EMA✓')
    return score, sigs

def find_best_coin(prices):
    best_sym, best_score, best_sigs = None, 0, []
    for c in COINS:
        if c['sym'] not in prices: continue
        score, sigs = analyze_coin(c['sym'])
        if score > best_score:
            best_score, best_sym, best_sigs = score, c['sym'], sigs
    return best_sym, best_score, best_sigs

# ── TRADING ──────────────────────────────────────────────────
def open_trade(sym, price):
    if state['cash'] < TRADE_SIZE:
        print(f"[TRADE] Nema kapitala! Cash: ${state['cash']:.2f}")
        return
    state['cash'] -= TRADE_SIZE
    amt = TRADE_SIZE / price
    tp_price = price * (1 + TAKE_PROFIT / TRADE_SIZE)
    sl_price = price * (1 - STOP_LOSS  / TRADE_SIZE)
    state['active'] = {
        'coin': sym, 'entry': price, 'amt': amt,
        'size': TRADE_SIZE, 'tp': tp_price, 'sl': sl_price,
        'opened': datetime.utcnow().isoformat()
    }
    print(f"[OPEN] {sym} @ ${price:.4f} | TP: ${tp_price:.4f} | SL: ${sl_price:.4f}")
    save_state()

def close_trade(price, result):
    t = state['active']
    if not t: return
    val = t['amt'] * price
    pnl = val - t['size']
    state['cash'] += val
    closed = {
        'coin': t['coin'], 'result': result,
        'entry': t['entry'], 'exit': price,
        'pnl': round(pnl, 4), 'pct': round((pnl / t['size']) * 100, 2),
        'size': t['size'], 'dur': 0,
        'at': datetime.utcnow().isoformat()
    }
    state['closed'].insert(0, closed)
    if len(state['closed']) > 200:
        state['closed'] = state['closed'][:200]
    state['active'] = None
    emoji = '🎉' if result == 'WIN' else '🛑'
    print(f"[CLOSE] {emoji} {closed['coin']} {result} | P&L: ${pnl:+.4f} | Cash: ${state['cash']:.2f}")
    save_state()

def check_trade(prices):
    t = state.get('active')
    if not t: return
    price = prices.get(t['coin'])
    if not price: return
    val = t['amt'] * price
    pnl = val - t['size']
    if pnl >= TAKE_PROFIT:
        close_trade(price, 'WIN')
    elif pnl <= -STOP_LOSS:
        close_trade(price, 'LOSS')
    else:
        print(f"[MONITOR] {t['coin']} @ ${price:.4f} | P&L: ${pnl:+.4f}")

# ── MAIN ─────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("🤖 ScalpBot Pro — Python 24/7")
    print("=" * 50)
    load_state()
    last_scan = 0
    while True:
        now = time.time()
        prices = fetch_prices()
        if prices:
            if state.get('active'):
                check_trade(prices)
            elif now - last_scan >= SCAN_INTERVAL:
                last_scan = now
                print("\n[SCAN] Analiziram tržište...")
                for c in COINS:
                    if len(price_history[c['sym']]) < 5 and c['sym'] in prices:
                        for _ in range(20):
                            price_history[c['sym']].append(prices[c['sym']])
                best_sym, best_score, best_sigs = find_best_coin(prices)
                if best_sym and best_score >= 3:
                    print(f"[SIGNAL] {best_sym} Score:{best_score}/4 [{' '.join(best_sigs)}] → ULAZIM!")
                    open_trade(best_sym, prices[best_sym])
                else:
                    print(f"[SCAN] Nema signala — čekam...")
        time.sleep(PRICE_INTERVAL)

if __name__ == '__main__':
    main()
