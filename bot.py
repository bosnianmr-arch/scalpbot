import os
import time
import json
import requests
import numpy as np
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────────
JSONBIN_KEY = '$2a$10$GVJAWrFcqVEjhXlZfI9.8OtzhH/KuN06w6NpkWLyID9Y9rDqgQgMi'
JSONBIN_BIN = '69b11b67cadcd23453e5c1ce'
JSONBIN_URL = f'https://api.jsonbin.io/v3/b/{JSONBIN_BIN}'

STARTING_CASH = 100.0
TRADE_SIZE    = 100.0   # sve na jedan trade
TAKE_PROFIT   = 1.0     # +$1
STOP_LOSS     = 0.50    # -$0.50
SCAN_INTERVAL = 60      # sekundi između skeniranja
PRICE_INTERVAL = 5      # sekundi između provjere cijene

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
    'active': None,   # samo jedan trade
    'closed': [],
    'savedAt': datetime.utcnow().isoformat()
}

price_history = {c['sym']: [] for c in COINS}

# ── JSONBIN ──────────────────────────────────────────────────
def load_state():
    global state
    try:
        res = requests.get(f'{JSONBIN_URL}/latest', headers={'X-Master-Key': JSONBIN_KEY}, timeout=10)
        if res.ok:
            data = res.json().get('record', {})
            if data.get('savedAt'):
                state = data
                state['cash'] = float(state.get('cash', STARTING_CASH))
                print(f"[LOAD] Cash: ${state['cash']:.2f} | Closed trades: {len(state.get('closed', []))}")
                return True
    except Exception as e:
        print(f"[LOAD ERROR] {e}")
    return False

def save_state():
    try:
        payload = {**state, 'savedAt': datetime.utcnow().isoformat()}
        res = requests.put(
            JSONBIN_URL,
            json=payload,
            headers={
                'Content-Type': 'application/json',
                'X-Master-Key': JSONBIN_KEY,
                'X-Bin-Versioning': 'false'
            },
            timeout=10
        )
        if res.ok:
            print(f"[SAVE] OK — Cash: ${state['cash']:.2f}")
        else:
            print(f"[SAVE ERROR] {res.status_code}: {res.text}")
    except Exception as e:
        print(f"[SAVE ERROR] {e}")

# ── PRICES ───────────────────────────────────────────────────
def fetch_prices():
    # Pokušaj Binance
    try:
        symbols = json.dumps([c['binance'] for c in COINS])
        res = requests.get(
            f'https://api.binance.com/api/v3/ticker/24hr',
            params={'symbols': symbols},
            timeout=10
        )
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
            print(f"[PRICES] Binance: " + " | ".join([f"{s}:${p:.4f}" for s,p in prices.items()]))
            return prices
    except Exception as e:
        print(f"[PRICES] Binance failed: {e}")

    # Fallback: CoinGecko
    try:
        ids = ','.join([c['id'] for c in COINS])
        res = requests.get(
            f'https://api.coingecko.com/api/v3/simple/price',
            params={'ids': ids, 'vs_currencies': 'usd'},
            timeout=10
        )
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
            print(f"[PRICES] CoinGecko: " + " | ".join([f"{s}:${p:.4f}" for s,p in prices.items()]))
            return prices
    except Exception as e:
        print(f"[PRICES] CoinGecko failed: {e}")

    return {}

# ── INDICATORS ───────────────────────────────────────────────
def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    deltas = np.diff(prices[-period-1:])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses) or 0.001
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_ema(prices, period):
    if len(prices) < period:
        return prices[-1] if prices else 0
    k = 2 / (period + 1)
    ema = prices[-period]
    for p in prices[-period+1:]:
        ema = p * k + ema * (1 - k)
    return ema

def calc_macd(prices):
    if len(prices) < 26:
        return 0
    ema12 = calc_ema(prices, 12)
    ema26 = calc_ema(prices, 26)
    macd = ema12 - ema26
    return macd - (macd * 0.85)

def calc_bb_pct(prices, period=20):
    if len(prices) < period:
        return 50
    sl = prices[-period:]
    mid = np.mean(sl)
    std = np.std(sl)
    upper = mid + 2 * std
    lower = mid - 2 * std
    cur = prices[-1]
    return ((cur - lower) / (upper - lower)) * 100 if (upper - lower) else 50

def analyze_coin(sym):
    ph = price_history[sym]
    if len(ph) < 5:
        return 0, []

    rsi  = calc_rsi(ph)
    macd = calc_macd(ph)
    bb   = calc_bb_pct(ph)
    ema_cross = calc_ema(ph, 9) - calc_ema(ph, 21) if len(ph) >= 21 else 0

    score = 0
    sigs = []
    if rsi < 45:       score += 1; sigs.append('RSI↓')
    if macd > 0:       score += 1; sigs.append('MACD+')
    if bb < 45:        score += 1; sigs.append('BB↓')
    if ema_cross > 0:  score += 1; sigs.append('EMA✓')

    return score, sigs

# ── TRADING ──────────────────────────────────────────────────
def find_best_coin(prices):
    """Nađi coin sa najboljim score-om"""
    best_sym, best_score, best_sigs = None, 0, []
    for c in COINS:
        sym = c['sym']
        if sym not in prices:
            continue
        score, sigs = analyze_coin(sym)
        if score > best_score:
            best_score = score
            best_sym = sym
            best_sigs = sigs
    return best_sym, best_score, best_sigs

def open_trade(sym, price):
    global state
    if state['cash'] < TRADE_SIZE:
        print(f"[TRADE] Nema dovoljno kapitala! Cash: ${state['cash']:.2f}")
        return

    state['cash'] -= TRADE_SIZE
    tp_price = price + (TAKE_PROFIT / (TRADE_SIZE / price))
    sl_price = price - (STOP_LOSS  / (TRADE_SIZE / price))
    amt = TRADE_SIZE / price

    state['active'] = {
        'coin': sym,
        'entry': price,
        'amt': amt,
        'size': TRADE_SIZE,
        'tp': tp_price,
        'sl': sl_price,
        'tp_usd': TAKE_PROFIT,
        'sl_usd': STOP_LOSS,
        'opened': datetime.utcnow().isoformat()
    }
    print(f"[OPEN] {sym} @ ${price:.4f} | TP: ${tp_price:.4f} (+$1) | SL: ${sl_price:.4f} (-$0.50)")
    save_state()

def close_trade(price, result):
    global state
    t = state['active']
    if not t:
        return

    val = t['amt'] * price
    pnl = val - t['size']
    state['cash'] += val

    closed = {
        'coin': t['coin'],
        'result': result,
        'entry': t['entry'],
        'exit': price,
        'pnl': round(pnl, 4),
        'pct': round((pnl / t['size']) * 100, 2),
        'size': t['size'],
        'dur': 0,
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
    """Provjeri TP/SL na aktivnom tradeu"""
    t = state.get('active')
    if not t:
        return
    sym = t['coin']
    price = prices.get(sym)
    if not price:
        return

    val = t['amt'] * price
    pnl = val - t['size']

    if pnl >= TAKE_PROFIT:
        close_trade(price, 'WIN')
    elif pnl <= -STOP_LOSS:
        close_trade(price, 'LOSS')
    else:
        print(f"[MONITOR] {sym} @ ${price:.4f} | P&L: ${pnl:+.4f} | TP: +${TAKE_PROFIT} | SL: -${STOP_LOSS}")

# ── MAIN LOOP ────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("🤖 ScalpBot Pro — Python 24/7")
    print("=" * 50)

    load_state()

    last_scan = 0

    while True:
        now = time.time()

        # Uzmi live cijene
        prices = fetch_prices()

        if prices:
            # Provjeri otvoreni trade (TP/SL) — svakih 5s
            if state.get('active'):
                check_trade(prices)

            # Skeniraj novi signal — svake minute
            elif now - last_scan >= SCAN_INTERVAL:
                last_scan = now
                print(f"\n[SCAN] Analiziram tržište...")

                # Popuni historiju ako je prazna
                for c in COINS:
                    if len(price_history[c['sym']]) < 5 and c['sym'] in prices:
                        for _ in range(20):
                            price_history[c['sym']].append(prices[c['sym']])

                best_sym, best_score, best_sigs = find_best_coin(prices)

                if best_sym and best_score >= 3:
                    print(f"[SIGNAL] {best_sym} Score:{best_score}/4 [{' '.join(best_sigs)}] → ULAZIM!")
                    open_trade(best_sym, prices[best_sym])
                else:
                    print(f"[SCAN] Nema dovoljno jakog signala — čekam...")

        time.sleep(PRICE_INTERVAL)

if __name__ == '__main__':
    main()
