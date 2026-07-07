import time
import requests
import logging
import os
import csv
import math
import json
from collections import deque
from datetime import datetime, timedelta
from plyer import notification
from pybit.unified_trading import HTTP

# ============================================================
#  НАСТРОЙКИ
# ============================================================
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
TIMEFRAMES = [15, 30, 60, 240, 1440, 10080]
TF_NAMES = {15: "15m", 30: "30m", 60: "1h", 240: "4h", 1440: "1d", 10080: "1w"}
LIMIT = 250
ENTRY_TF = 15

# Паттерны
DOJI_THRESHOLD = 0.1
HAMMER_THRESHOLD = 0.3
PINBAR_THRESHOLD = 0.3

# MACD
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Баллы и веса
WEIGHT_MAP = {15: 1.0, 30: 1.5, 60: 2.0, 240: 3.0, 1440: 8.0, 10080: 10.0}
THRESHOLD_SCORE = 5.0

# Торговля
API_KEY = "0eUrmvaqC55nWVQrZg"
API_SECRET = "UXDo2dm362OfjLt5Aq2xGz3Nv87Ht3rgtr4t"
CATEGORY = "linear"
COMMISSION = 0.00055
RISK_PER_TRADE = 2.0
USE_LIMIT_ORDERS = True
LIMIT_OFFSET_PERCENT = 0.15
ORDER_TIMEOUT = 180
RE_ENTRY_AFTER_SL = 3

# Адаптация
ADAPTATION_INTERVAL_HOURS = 6
MIN_TRADES_FOR_ADAPT = 10

# Ограничения
MAX_TP_PERCENT = 3.0
MIN_SL_PERCENT = 0.01
SL_ATR_MULTIPLIER = 2.0
MAX_POSITION_USDT = 5000            # максимальный объём позиции в USDT

# Лоты
LOT_INFO = {
    "BTCUSDT": {"min": 0.001, "max": 100, "step": 0.001},
    "ETHUSDT": {"min": 0.01, "max": 1000, "step": 0.01},
    "SOLUSDT": {"min": 0.1, "max": 10000, "step": 0.1},
    "XRPUSDT": {"min": 1.0, "max": 100000, "step": 1.0},
}
# ============================================================

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("adaptive_trader.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
trade_logger = logging.getLogger("trade")
trade_logger.setLevel(logging.INFO)
trade_handler = logging.FileHandler("adaptive_trades.log", encoding='utf-8')
trade_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
trade_logger.addHandler(trade_handler)

print(f"Логи будут сохранены в: {os.getcwd()}")

# CSV статистики
STATS_CSV = "trades_stats_adaptive.csv"
STATS_HEADERS = [
    "timestamp_open", "symbol", "side", "entry_price", "qty",
    "timestamp_close", "exit_price", "gross_pnl_pct", "result",
    "commission_usdt", "net_pnl_pct",
    "score", "ema20_entry", "ema50_entry", "ema200_entry",
    "macd_entry", "signal_entry", "tf_consensus"
]

def init_stats_csv():
    if os.path.exists(STATS_CSV):
        with open(STATS_CSV, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=';')
            headers = next(reader, [])
        if headers != STATS_HEADERS:
            backup = f"stats_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            os.rename(STATS_CSV, backup)
            logger.info(f"Старый файл переименован в {backup}")
    with open(STATS_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(STATS_HEADERS)
init_stats_csv()

# ---- Глобальные переменные ----
current_params = {
    'threshold_score': THRESHOLD_SCORE,
    'weights': WEIGHT_MAP.copy(),
    'doji_threshold': DOJI_THRESHOLD,
    'hammer_threshold': HAMMER_THRESHOLD,
    'pinbar_threshold': PINBAR_THRESHOLD,
}
last_adaptation_time = datetime.now() - timedelta(hours=ADAPTATION_INTERVAL_HOURS + 1)
open_trades = {}
last_sl_time = {}

# ---- Функция адаптации ----
def analyze_recent_trades(n=20):
    if not os.path.exists(STATS_CSV):
        return None
    with open(STATS_CSV, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=';')
        rows = list(reader)
    if len(rows) < 2:
        return None
    trades = rows[1:]
    closed = [row for row in trades if len(row) >= 12 and row[8] != ""]
    if len(closed) < MIN_TRADES_FOR_ADAPT:
        logger.info(f"Недостаточно сделок для адаптации (нужно {MIN_TRADES_FOR_ADAPT}, есть {len(closed)})")
        return None
    recent = closed[-n:]
    wins = [t for t in recent if t[8] == "Win"]
    winrate = len(wins) / len(recent) * 100 if recent else 0
    logger.info(f"Анализ последних {len(recent)} сделок: винрейт {winrate:.1f}%")
    new_threshold = current_params['threshold_score']
    if winrate > 60:
        new_threshold = min(8.0, current_params['threshold_score'] + 0.5)
        logger.info(f"Винрейт высокий, повышаем порог до {new_threshold}")
    elif winrate < 40:
        new_threshold = max(3.0, current_params['threshold_score'] - 0.5)
        logger.info(f"Винрейт низкий, снижаем порог до {new_threshold}")
    else:
        logger.info("Винрейт в норме, порог не меняем")
    return {
        'threshold_score': new_threshold,
        'winrate': winrate,
        'total_trades': len(recent)
    }

def apply_adaptation(recommendation):
    if recommendation is None:
        return
    old_threshold = current_params['threshold_score']
    new_threshold = recommendation.get('threshold_score', old_threshold)
    current_params['threshold_score'] = new_threshold
    logger.info(f"Параметры обновлены: THRESHOLD_SCORE {old_threshold:.1f} -> {new_threshold:.1f}")
    with open("params_history.log", "a", encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - THRESHOLD_SCORE: {old_threshold:.1f} -> {new_threshold:.1f}, winrate: {recommendation.get('winrate', 0):.1f}%\n")

# ---- Вспомогательные функции ----
def fetch_klines(symbol, interval, limit, retries=3):
    if interval == 1440:
        interval_str = "D"
    elif interval == 10080:
        interval_str = "W"
    else:
        interval_str = str(interval)
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": symbol, "interval": interval_str, "limit": limit}
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if data['retCode'] == 0:
                klines = data['result']['list']
                klines.reverse()
                return klines
            else:
                logger.error(f"Ошибка {symbol} ({interval}): {data['retMsg']}")
                return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Ошибка запроса {symbol} ({interval}), попытка {attempt+1}/{retries}: {e}")
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                logger.error(f"Не удалось выполнить запрос {symbol} ({interval}) после {retries} попыток")
                return None
    return None

def calculate_ema(series, period):
    if len(series) < period:
        return None
    k = 2 / (period + 1)
    ema = series[0]
    for price in series[1:]:
        ema = price * k + ema * (1 - k)
    return ema

def detect_doji(open_price, close_price, high, low, threshold=DOJI_THRESHOLD):
    if high == low:
        return False
    body = abs(open_price - close_price)
    range_ = high - low
    return body <= threshold * range_

def detect_hammer(open_price, close_price, high, low, threshold=HAMMER_THRESHOLD):
    if high == low:
        return False
    body = abs(open_price - close_price)
    range_ = high - low
    lower_shadow = min(open_price, close_price) - low
    upper_shadow = high - max(open_price, close_price)
    return lower_shadow > threshold * body and upper_shadow < 0.3 * body

def detect_pinbar(open_price, close_price, high, low, threshold=PINBAR_THRESHOLD):
    if high == low:
        return False
    body = abs(open_price - close_price)
    range_ = high - low
    lower_shadow = min(open_price, close_price) - low
    upper_shadow = high - max(open_price, close_price)
    return lower_shadow > threshold * body and upper_shadow < 0.3 * body

def analyze_timeframe(symbol, tf, params):
    data = histories[tf][symbol]
    if len(data['close']) < 26:
        return None
    close_list = list(data['close'])
    open_price = data['open'][-1]
    high = data['high'][-1]
    low = data['low'][-1]
    close = data['close'][-1]

    ema20 = calculate_ema(close_list, 20)
    ema50 = calculate_ema(close_list, 50)
    ema200 = calculate_ema(close_list, 200)

    ema_fast = calculate_ema(close_list, MACD_FAST)
    ema_slow = calculate_ema(close_list, MACD_SLOW)
    macd_line = None
    signal_line = None
    hist = None
    if ema_fast is not None and ema_slow is not None:
        macd_line = ema_fast - ema_slow
        macd_histories[tf][sym]['macd_line'].append(macd_line)
        if len(macd_histories[tf][sym]['macd_line']) >= MACD_SIGNAL:
            signal_line = calculate_ema(list(macd_histories[tf][sym]['macd_line']), MACD_SIGNAL)
            if signal_line is not None:
                hist = macd_line - signal_line

    cross = None
    if len(macd_histories[tf][sym]['macd_line']) >= 2:
        prev_line = list(macd_histories[tf][sym]['macd_line'])[-2]
        prev_signal = macd_histories[tf][sym]['signal_line']
        if prev_signal is not None and signal_line is not None:
            if prev_line <= prev_signal and macd_line > signal_line:
                cross = "bullish"
            elif prev_line >= prev_signal and macd_line < signal_line:
                cross = "bearish"

    doji = detect_doji(open_price, close, high, low, params.get('doji_threshold', DOJI_THRESHOLD))
    hammer = detect_hammer(open_price, close, high, low, params.get('hammer_threshold', HAMMER_THRESHOLD))
    pinbar = detect_pinbar(open_price, close, high, low, params.get('pinbar_threshold', PINBAR_THRESHOLD))

    return {
        'ema20': ema20,
        'ema50': ema50,
        'ema200': ema200,
        'macd_line': macd_line,
        'signal_line': signal_line,
        'histogram': hist,
        'cross': cross,
        'doji': doji,
        'hammer': hammer,
        'pinbar': pinbar,
        'open': open_price,
        'high': high,
        'low': low,
        'close': close
    }

def calculate_atr_for_tf(symbol, period=14, tf=15):
    highs = list(histories[tf][symbol]['high'])
    lows = list(histories[tf][symbol]['low'])
    closes = list(histories[tf][symbol]['close'])
    if len(closes) < period + 1:
        return None
    tr_list = []
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i-1])
        lc = abs(lows[i] - closes[i-1])
        tr = max(hl, hc, lc)
        tr_list.append(tr)
    if len(tr_list) >= period:
        return sum(tr_list[-period:]) / period
    return None

def generate_signal(symbol, params):
    tf_data = {}
    for tf in TIMEFRAMES:
        tf_data[tf] = analyze_timeframe(symbol, tf, params)

    score_bull = 0.0
    score_bear = 0.0
    consensus_tf = 0

    for tf, data in tf_data.items():
        if data is None:
            continue
        w = params['weights'].get(tf, 1.0)
        if data['cross'] == 'bullish':
            score_bull += 2 * w
            consensus_tf += 1
        elif data['cross'] == 'bearish':
            score_bear += 2 * w
            consensus_tf -= 1

        if data['ema20'] is not None:
            if data['close'] > data['ema20']:
                score_bull += 1 * w
                consensus_tf += 0.5
            else:
                score_bear += 1 * w
                consensus_tf -= 0.5
        if data['ema50'] is not None:
            if data['close'] > data['ema50']:
                score_bull += 1 * w
            else:
                score_bear += 1 * w
        if data['ema200'] is not None:
            if data['close'] > data['ema200']:
                score_bull += 1 * w
            else:
                score_bear += 1 * w

        if data['hammer']:
            score_bull += 1 * w
            consensus_tf += 1
        if data['pinbar']:
            score_bull += 1 * w
            consensus_tf += 1

    diff = score_bull - score_bear
    threshold = params.get('threshold_score', THRESHOLD_SCORE)
    if diff > threshold:
        daily = tf_data.get(1440)
        if daily is None:
            return "NONE", None, None, diff, consensus_tf, None
        close = daily['close']
        # Фильтр: лонг только если цена ниже EMA50 (откат в бычьем тренде)
        if daily['ema50'] is not None and close > daily['ema50']:
            return "NONE", None, None, diff, consensus_tf, None
        ema20 = daily['ema20']
        ema50 = daily['ema50']
        ema200 = daily['ema200']
        levels = []
        if ema20 is not None and ema20 > close:
            levels.append(('EMA20', ema20))
        if ema50 is not None and ema50 > close:
            levels.append(('EMA50', ema50))
        if ema200 is not None and ema200 > close:
            levels.append(('EMA200', ema200))
        if levels:
            tp = min(levels, key=lambda x: x[1] - close)[1]
        else:
            highs_10 = list(histories[1440][symbol]['high'])[-10:]
            tp = max(highs_10) if highs_10 else close * 1.02
        max_tp = close * (1 + MAX_TP_PERCENT / 100)
        if tp > max_tp:
            tp = max_tp
        if abs(tp - close) / close < 0.005:
            tp = close * 1.02
        atr = calculate_atr_for_tf(symbol, 14, ENTRY_TF)
        if atr is None:
            atr = close * 0.01
        sl = close - max(atr * SL_ATR_MULTIPLIER, close * MIN_SL_PERCENT)
        if (close - sl) / close < MIN_SL_PERCENT:
            sl = close * (1 - MIN_SL_PERCENT)
        return "BUY", tp, sl, diff, consensus_tf, (ema20, ema50, ema200)

    elif diff < -threshold:
        daily = tf_data.get(1440)
        if daily is None:
            return "NONE", None, None, diff, consensus_tf, None
        close = daily['close']
        # Фильтр: шорт только если цена выше EMA50 (откат в медвежьем тренде)
        if daily['ema50'] is not None and close < daily['ema50']:
            return "NONE", None, None, diff, consensus_tf, None
        ema20 = daily['ema20']
        ema50 = daily['ema50']
        ema200 = daily['ema200']
        levels = []
        if ema20 is not None and ema20 < close:
            levels.append(('EMA20', ema20))
        if ema50 is not None and ema50 < close:
            levels.append(('EMA50', ema50))
        if ema200 is not None and ema200 < close:
            levels.append(('EMA200', ema200))
        if levels:
            tp = max(levels, key=lambda x: x[1])[1]
        else:
            lows_10 = list(histories[1440][symbol]['low'])[-10:]
            tp = min(lows_10) if lows_10 else close * 0.98
        min_tp = close * (1 - MAX_TP_PERCENT / 100)
        if tp < min_tp:
            tp = min_tp
        if abs(tp - close) / close < 0.005:
            tp = close * 0.98
        atr = calculate_atr_for_tf(symbol, 14, ENTRY_TF)
        if atr is None:
            atr = close * 0.01
        sl = close + max(atr * SL_ATR_MULTIPLIER, close * MIN_SL_PERCENT)
        if (sl - close) / close < MIN_SL_PERCENT:
            sl = close * (1 + MIN_SL_PERCENT)
        return "SELL", tp, sl, diff, consensus_tf, (ema20, ema50, ema200)

    return "NONE", None, None, diff, consensus_tf, None

# ---- Торговые функции ----
def get_session():
    return HTTP(
        testnet=False,
        demo=True,
        api_key=API_KEY,
        api_secret=API_SECRET,
        recv_window=15000
    )

def get_balance_usdt():
    session = get_session()
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        if resp['retCode'] == 0:
            return float(resp['result']['list'][0]['totalEquity'])
        else:
            logger.error(f"Ошибка баланса: {resp['retMsg']}")
            return None
    except Exception as e:
        logger.error(f"Исключение при получении баланса: {e}")
        return None

def adjust_qty_to_lot(symbol, qty):
    info = LOT_INFO.get(symbol)
    if info is None:
        info = {"min": 0.001, "max": 1000, "step": 0.001}
    min_lot = info["min"]
    max_lot = info["max"]
    step = info["step"]
    if qty < min_lot:
        qty = min_lot
    elif qty > max_lot:
        qty = max_lot
    else:
        precision = int(round(-math.log10(step))) if step < 1 else 0
        qty = round(qty / step) * step
        qty = round(qty, precision)
    return qty

def calculate_position_size(symbol, entry_price, stop_loss_price):
    balance = get_balance_usdt()
    if balance is None:
        return 0.001
    risk_amount = balance * (RISK_PER_TRADE / 100)
    risk_per_contract = abs(entry_price - stop_loss_price)
    if risk_per_contract == 0:
        return 0.001
    qty = risk_amount / risk_per_contract
    # Ограничение по максимальной сумме в USDT
    max_qty_by_usdt = MAX_POSITION_USDT / entry_price
    qty = min(qty, max_qty_by_usdt)
    qty = adjust_qty_to_lot(symbol, qty)
    info = LOT_INFO.get(symbol, {"min": 0.001})
    if qty < info["min"]:
        qty = info["min"]
    return qty

def place_limit_order(symbol, side, qty, price, timeout=ORDER_TIMEOUT):
    session = get_session()
    params = {"category": CATEGORY, "symbol": symbol, "side": side,
              "orderType": "Limit", "qty": str(qty), "price": str(price),
              "timeInForce": "GTC"}
    logger.info(f"Отправка лимитного ордера {side} {symbol} qty={qty} price={price}, timeout={timeout}с")
    try:
        resp = session.place_order(**params)
        if resp['retCode'] != 0:
            logger.error(f"Лимит ошибка: {resp['retMsg']}")
            return None
        order_id = resp['result']['orderId']
        start = time.time()
        while time.time() - start < timeout:
            status = session.get_open_orders(category=CATEGORY, symbol=symbol, orderId=order_id)
            if status['retCode'] == 0:
                orders = status['result']['list']
                if not orders:
                    break
                for ord in orders:
                    if ord['orderId'] == order_id and ord['orderStatus'] == 'Filled':
                        logger.info(f"Лимитный ордер {side} {symbol} исполнен по {price}")
                        trade_logger.info(f"ОТКРЫТИЕ: {side} {symbol} qty={qty} цена={price} (лимит)")
                        return resp['result']
            time.sleep(2)
        session.cancel_order(category=CATEGORY, symbol=symbol, orderId=order_id)
        logger.warning(f"Лимитный ордер {side} {symbol} отменён по таймауту")
        return None
    except Exception as e:
        logger.error(f"Исключение в лимитном ордере: {e}")
        return None

def place_market_order(symbol, side, qty):
    session = get_session()
    params = {"category": CATEGORY, "symbol": symbol, "side": side,
              "orderType": "Market", "qty": str(qty), "timeInForce": "GTC"}
    try:
        resp = session.place_order(**params)
        if resp['retCode'] == 0:
            logger.info(f"Рыночный ордер {side} {symbol} qty={qty} размещён")
            trade_logger.info(f"ОТКРЫТИЕ: {side} {symbol} qty={qty} цена=MARKET")
            return resp['result']
        else:
            logger.error(f"Рыночный ошибка: {resp['retMsg']}")
            return None
    except Exception as e:
        logger.error(f"Исключение в рыночном ордере: {e}")
        return None

def set_stop_loss_take_profit(symbol, position_side, sl_price, tp_price):
    session = get_session()
    try:
        params = {"category": CATEGORY, "symbol": symbol, "side": position_side,
                  "stopLoss": str(sl_price), "stopLossTrigger": "LastPrice",
                  "takeProfit": str(tp_price), "takeProfitTrigger": "LastPrice"}
        resp = session.set_trading_stop(**params)
        if resp['retCode'] == 0:
            logger.info(f"SL/TP установлены: SL={sl_price}, TP={tp_price}")
            trade_logger.info(f"SL/TP: SL={sl_price} TP={tp_price}")
            return True
        else:
            logger.error(f"Ошибка SL/TP: {resp['retMsg']}")
            return False
    except Exception as e:
        logger.error(f"Исключение SL/TP: {e}")
        return False

def get_open_positions():
    session = get_session()
    try:
        resp = session.get_positions(category=CATEGORY, settleCoin="USDT")
        if resp['retCode'] == 0:
            return resp['result']['list']
        else:
            logger.error(f"Ошибка получения позиций: {resp['retMsg']}")
            return None
    except UnicodeError:
        return None
    except Exception as e:
        logger.error(f"Исключение получения позиций: {e}")
        return None

def has_open_position(symbol):
    positions = get_open_positions()
    if positions is None:
        return None
    for pos in positions:
        if pos['symbol'] == symbol and float(pos['size']) > 0:
            return True
    return False

def get_last_price(symbol, tf=15):
    data = fetch_klines(symbol, tf, 1)
    if data and len(data) > 0:
        return float(data[0][4])
    return None

def add_trade_open(symbol, side, entry_price, qty, score, consensus_tf, ema_values):
    global open_trades
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    open_trades[symbol] = {
        'timestamp_open': timestamp,
        'symbol': symbol,
        'side': side,
        'entry_price': entry_price,
        'qty': qty,
        'score': score,
        'consensus_tf': consensus_tf,
        'ema20': ema_values[0] if ema_values else None,
        'ema50': ema_values[1] if ema_values else None,
        'ema200': ema_values[2] if ema_values else None,
    }
    with open(STATS_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow([
            timestamp, symbol, side, entry_price, qty,
            "", "", "", "",
            "", "",
            round(score, 1) if score else "",
            ema_values[0] if ema_values else "",
            ema_values[1] if ema_values else "",
            ema_values[2] if ema_values else "",
            "", "", consensus_tf
        ])
    logger.info(f"Сделка открыта: {side} {symbol} по {entry_price}, qty={qty}, score={score:.1f}")

def close_trade(symbol, exit_price, score=None):
    global open_trades
    if symbol not in open_trades:
        return
    trade = open_trades[symbol]
    entry = trade['entry_price']
    side = trade['side']
    qty = trade['qty']
    if side == "Buy":
        gross_pnl = (exit_price - entry) * qty
    else:
        gross_pnl = (entry - exit_price) * qty
    commission_usdt = (entry * qty + exit_price * qty) * COMMISSION
    net_pnl = gross_pnl - commission_usdt
    gross_pnl_pct = (gross_pnl / (entry * qty)) * 100
    net_pnl_pct = (net_pnl / (entry * qty)) * 100
    result = "Win" if net_pnl > 0 else "Loss" if net_pnl < 0 else "Breakeven"

    rows = []
    if os.path.exists(STATS_CSV):
        with open(STATS_CSV, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=';')
            rows = list(reader)
        for i in range(len(rows)-1, -1, -1):
            if len(rows[i]) >= 4 and rows[i][1] == symbol and rows[i][5] == "":
                rows[i][5] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                rows[i][6] = str(exit_price)
                rows[i][7] = str(round(gross_pnl_pct, 2))
                rows[i][8] = result
                rows[i][9] = str(round(commission_usdt, 2))
                rows[i][10] = str(round(net_pnl_pct, 2))
                if score is not None:
                    rows[i][11] = str(round(score, 1))
                break
        with open(STATS_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerows(rows)
    del open_trades[symbol]
    logger.info(f"Сделка закрыта: {side} {symbol} результат {result} (валовая {gross_pnl_pct:.2f}%, чистая {net_pnl_pct:.2f}%, комиссия {commission_usdt:.2f} USDT)")
    update_stats()

def update_stats():
    if not os.path.exists(STATS_CSV):
        return
    with open(STATS_CSV, 'r', newline='', encoding='utf-8') as f:
        rows = list(csv.reader(f, delimiter=';'))
    if len(rows) < 2:
        logger.info("Статистика: пока нет завершённых сделок")
        return
    wins = losses = total = 0
    for row in rows[1:]:
        if len(row) >= 11 and row[8] != "":
            total += 1
            if row[8] == "Win":
                wins += 1
            elif row[8] == "Loss":
                losses += 1
    if total == 0:
        logger.info("Статистика: пока нет завершённых сделок")
        return
    winrate = wins / total * 100
    logger.info(f"📊 Статистика сделок: Всего {total}, Win {wins} ({winrate:.1f}%), Loss {losses}")

# ---- Загрузка истории ----
logger.info("Загрузка истории для всех таймфреймов...")
histories = {tf: {sym: {
    'open': deque(maxlen=LIMIT),
    'high': deque(maxlen=LIMIT),
    'low': deque(maxlen=LIMIT),
    'close': deque(maxlen=LIMIT),
    'volume': deque(maxlen=LIMIT)
} for sym in SYMBOLS} for tf in TIMEFRAMES}

macd_histories = {tf: {sym: {
    'macd_line': deque(maxlen=200),
    'signal_line': None,
    'histogram': None
} for sym in SYMBOLS} for tf in TIMEFRAMES}

for tf in TIMEFRAMES:
    tf_name = TF_NAMES[tf]
    for sym in SYMBOLS:
        data = fetch_klines(sym, tf, LIMIT)
        if data:
            for k in data:
                histories[tf][sym]['open'].append(float(k[1]))
                histories[tf][sym]['high'].append(float(k[2]))
                histories[tf][sym]['low'].append(float(k[3]))
                histories[tf][sym]['close'].append(float(k[4]))
                histories[tf][sym]['volume'].append(float(k[5]))
            logger.info(f"  {sym} ({tf_name}): загружено {len(data)} свечей")
        else:
            logger.warning(f"  {sym} ({tf_name}): не удалось загрузить")

# ---- Основной цикл с оптимизацией API ----
def main_loop():
    global open_trades, last_sl_time, last_adaptation_time
    logger.info(f"Адаптивный трейдер запущен (пересчёт параметров каждые {ADAPTATION_INTERVAL_HOURS} часов).")

    # Время последнего обновления для каждого ТФ (для экономии запросов)
    last_update = {tf: 0 for tf in TIMEFRAMES}
    # Интервалы обновления в секундах
    update_intervals = {
        15: 60,       # 15m – раз в минуту
        30: 120,      # 30m – раз в 2 минуты
        60: 300,      # 1h – раз в 5 минут
        240: 600,     # 4h – раз в 10 минут
        1440: 7200,   # 1d – раз в 2 часа
        10080: 21600  # 1w – раз в 6 часов
    }

    while True:
        now = time.time()
        # Обновляем историю с разной периодичностью
        for tf in TIMEFRAMES:
            interval_sec = update_intervals.get(tf, 60)
            if now - last_update[tf] < interval_sec:
                continue
            for sym in SYMBOLS:
                data = fetch_klines(sym, tf, 1)
                if data and len(data) > 0:
                    k = data[0]
                    histories[tf][sym]['open'].append(float(k[1]))
                    histories[tf][sym]['high'].append(float(k[2]))
                    histories[tf][sym]['low'].append(float(k[3]))
                    histories[tf][sym]['close'].append(float(k[4]))
                    histories[tf][sym]['volume'].append(float(k[5]))
            last_update[tf] = now

        # Адаптация параметров
        if (datetime.now() - last_adaptation_time).total_seconds() > ADAPTATION_INTERVAL_HOURS * 3600:
            logger.info("Запуск адаптации параметров...")
            recommendation = analyze_recent_trades(n=20)
            if recommendation:
                apply_adaptation(recommendation)
            else:
                logger.info("Недостаточно данных для адаптации, пропускаем")
            last_adaptation_time = datetime.now()

        for sym in SYMBOLS:
            has_pos = has_open_position(sym)
            if has_pos is None:
                continue
            if sym in open_trades and not has_pos:
                last_price = get_last_price(sym, ENTRY_TF)
                if last_price:
                    score = open_trades[sym].get('score', None)
                    close_trade(sym, last_price, score)
                else:
                    logger.warning(f"Не удалось получить цену для закрытия {sym}")

            if has_pos:
                continue

            signal, tp, sl, diff, consensus_tf, ema_vals = generate_signal(sym, current_params)
            if signal == "NONE":
                continue

            if sym in last_sl_time:
                if (datetime.now() - last_sl_time[sym]).total_seconds() < RE_ENTRY_AFTER_SL * ENTRY_TF * 60:
                    logger.info(f"{sym} пропущен: повторный вход запрещён {RE_ENTRY_AFTER_SL} свечей после SL")
                    continue

            entry_price = histories[ENTRY_TF][sym]['close'][-1]
            if signal == "BUY":
                expected_pct = (tp - entry_price) / entry_price * 100
            else:
                expected_pct = (entry_price - tp) / entry_price * 100
            if expected_pct <= COMMISSION * 2 * 100:
                logger.info(f"{sym} пропущен: ожидаемая прибыль {expected_pct:.2f}% <= комиссии")
                continue

            qty = calculate_position_size(sym, entry_price, sl)
            if qty == 0:
                logger.warning(f"{sym} пропущен: qty=0")
                continue
            info = LOT_INFO.get(sym, {"min": 0.001, "max": 1000})
            if qty < info["min"] or qty > info["max"]:
                logger.warning(f"{sym} пропущен: qty={qty} вне диапазона [{info['min']}, {info['max']}]")
                continue

            side = "Buy" if signal == "BUY" else "Sell"
            if USE_LIMIT_ORDERS:
                offset = entry_price * (LIMIT_OFFSET_PERCENT / 100)
                limit_price = round(entry_price - offset, 2) if side == "Buy" else round(entry_price + offset, 2)
                logger.info(f"Попытка открыть {side} {sym} лимит по {limit_price} (текущая {entry_price}, qty={qty})")
                order_result = place_limit_order(sym, side, qty, limit_price)
            else:
                order_result = place_market_order(sym, side, qty)

            if order_result:
                pos_side = "Buy" if signal == "BUY" else "Sell"
                set_stop_loss_take_profit(sym, pos_side, sl, tp)
                add_trade_open(sym, side, entry_price, qty, diff, consensus_tf, ema_vals)
                if sym in last_sl_time:
                    del last_sl_time[sym]
            else:
                logger.error(f"Не удалось открыть позицию {sym}")

        time.sleep(300)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        logger.info("Остановка по запросу пользователя.")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)