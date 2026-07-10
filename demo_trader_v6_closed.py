import time
import requests
import logging
import os
import csv
import math
from collections import deque
from datetime import datetime, timedelta
from plyer import notification
from pybit.unified_trading import HTTP

# ============================================================
# НАСТРОЙКИ
# ============================================================
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "ADAUSDT", "DOGEUSDT", "DOTUSDT", "LINKUSDT",
    "AVAXUSDT", "ATOMUSDT", "UNIUSDT", "LTCUSDT", "BCHUSDT",
    "NEARUSDT", "ALGOUSDT", "VETUSDT", "ICPUSDT",
    "ETCUSDT", "XLMUSDT", "AAVEUSDT", "CRVUSDT"
]
TIMEFRAMES = [5, 15, 30, 60, 240, 1440, 10080]   # добавлен 5 минут
ENTRY_TF = 15

DOJI_THRESHOLD = 0.1
HAMMER_THRESHOLD = 0.3
PINBAR_THRESHOLD = 0.3

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

WEIGHT_MAP = {5: 1.0, 15: 1.0, 30: 1.5, 60: 2.0, 240: 3.0, 1440: 8.0, 10080: 10.0}
THRESHOLD_SCORE = 5.0

API_KEY = ""
API_SECRET = ""
CATEGORY = "linear"
COMMISSION = 0.00055
RISK_PER_TRADE = 2.0
USE_LIMIT_ORDERS = True
LIMIT_OFFSET_PERCENT = 0.15
ORDER_TIMEOUT = 180
RE_ENTRY_AFTER_SL = 3

MIN_SL_PERCENT = 0.01
SL_ATR_MULTIPLIER = 2.0
MAX_TP_PERCENT = 3.0
MAX_POSITION_USDT = 5000
EMA_CLOSE_PROXIMITY = 0.015  # 1.5%
PENALTY_FOR_MACD_CONFLICT = 5

# === НАСТРОЙКИ ДЛЯ ПЕРЕДВИЖЕНИЯ СТОП-ЛОССА (5-минутный ТФ) ===
TRAILING_STOP_ENABLED = True
TRAILING_MIN_PROFIT_PCT = 1.5          # % прибыли для активации
TRAILING_STEP_PCT = 0.5                # минимальное изменение для нового передвижения
TRAILING_USE_EMA50 = True              # если True – EMA50, иначе EMA20
TRAILING_OFFSET_PCT = 0.3              # отступ от EMA в процентах
TRAILING_TF = 5                        # таймфрейм для трейлинга (5 минут)

LOT_INFO = {
    "BTCUSDT": {"min": 0.001, "max": 100, "step": 0.001},
    "ETHUSDT": {"min": 0.01, "max": 1000, "step": 0.01},
    "SOLUSDT": {"min": 0.1, "max": 10000, "step": 0.1},
    "XRPUSDT": {"min": 1.0, "max": 100000, "step": 1.0},
    "BNBUSDT": {"min": 0.01, "max": 1000, "step": 0.01},
    "ADAUSDT": {"min": 1.0, "max": 100000, "step": 1.0},
    "DOGEUSDT": {"min": 1.0, "max": 100000, "step": 1.0},
    "DOTUSDT": {"min": 0.1, "max": 10000, "step": 0.1},
    "LINKUSDT": {"min": 0.1, "max": 10000, "step": 0.1},
    "AVAXUSDT": {"min": 0.1, "max": 10000, "step": 0.1},
    "ATOMUSDT": {"min": 0.1, "max": 10000, "step": 0.1},
    "UNIUSDT": {"min": 0.1, "max": 10000, "step": 0.1},
    "LTCUSDT": {"min": 0.01, "max": 1000, "step": 0.01},
    "BCHUSDT": {"min": 0.01, "max": 1000, "step": 0.01},
    "NEARUSDT": {"min": 0.1, "max": 10000, "step": 0.1},
    "ALGOUSDT": {"min": 1.0, "max": 100000, "step": 1.0},
    "VETUSDT": {"min": 1.0, "max": 100000, "step": 1.0},
    "ICPUSDT": {"min": 0.1, "max": 10000, "step": 0.1},
    "ETCUSDT": {"min": 0.01, "max": 1000, "step": 0.01},
    "XLMUSDT": {"min": 1.0, "max": 100000, "step": 1.0},
    "AAVEUSDT": {"min": 0.01, "max": 1000, "step": 0.01},
    "CRVUSDT": {"min": 0.1, "max": 10000, "step": 0.1},
}
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("demo_trader_v6.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
trade_logger = logging.getLogger("trade")
trade_logger.setLevel(logging.INFO)
trade_handler = logging.FileHandler("trades_v6.log", encoding='utf-8')
trade_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
trade_logger.addHandler(trade_handler)

print(f"Логи будут сохранены в: {os.getcwd()}")

STATS_CSV = "trades_stats_v6.csv"
STATS_HEADERS = [
    "timestamp_open", "symbol", "side", "entry_price", "qty",
    "timestamp_close", "exit_price", "gross_pnl_pct", "result",
    "commission_usdt", "net_pnl_pct",
    "score", "ema20_entry", "ema50_entry", "ema200_entry",
    "macd_entry", "signal_entry", "tf_consensus",
    "sl", "tp", "expected_gain_pct", "expected_loss_pct",
    "duration_minutes"
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

open_trades = {}
last_sl_time = {}

current_params = {
    'threshold_score': THRESHOLD_SCORE,
    'weights': WEIGHT_MAP.copy(),
}

histories = {tf: {sym: {
    'open': deque(maxlen=250),
    'high': deque(maxlen=250),
    'low': deque(maxlen=250),
    'close': deque(maxlen=250),
    'volume': deque(maxlen=250)
} for sym in SYMBOLS} for tf in TIMEFRAMES}

prev_macd = {tf: {sym: {'macd': None, 'signal': None} for sym in SYMBOLS} for tf in TIMEFRAMES}
last_completed_price = {}

def calculate_ema(series, period):
    if len(series) < period:
        return None
    k = 2 / (period + 1)
    ema = series[0]
    for price in series[1:]:
        ema = price * k + ema * (1 - k)
    return ema

def fetch_klines_completed(symbol, interval, limit, retries=3):
    if interval == 1440:
        interval_str = "D"
    elif interval == 10080:
        interval_str = "W"
    else:
        interval_str = str(interval)
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": symbol, "interval": interval_str, "limit": limit + 1}
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                time.sleep(2 * (attempt + 1))
                continue
            data = resp.json()
            if data['retCode'] == 0:
                klines = data['result']['list']
                klines.reverse()
                if not klines:
                    logger.warning(f"Нет свечей для {symbol} ({interval})")
                    return []
                interval_ms = interval * 60 * 1000 if interval < 1440 else (24 * 60 * 60 * 1000 if interval == 1440 else 7 * 24 * 60 * 60 * 1000)
                now_ms = int(time.time() * 1000)
                last_candle_time = int(klines[-1][0])
                if now_ms >= last_candle_time + interval_ms:
                    if len(klines) > limit:
                        klines = klines[-limit:]
                    return klines
                else:
                    if len(klines) > limit:
                        klines = klines[-limit-1:-1]
                    else:
                        klines = klines[:-1]
                    return klines
            else:
                logger.error(f"Ошибка {symbol} ({interval}): {data['retMsg']}")
                return []
        except Exception as e:
            logger.warning(f"Ошибка {symbol} ({interval}), попытка {attempt+1}/{retries}: {e}")
            time.sleep(2 * (attempt + 1))
    logger.error(f"Не удалось загрузить {symbol} ({interval}) после {retries} попыток")
    return []

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

def calculate_macd(close_list, fast=12, slow=26, signal=9):
    if len(close_list) < slow:
        return None, None, None
    macd_values = []
    for i in range(slow, len(close_list)):
        f = calculate_ema(close_list[:i+1], fast)
        s = calculate_ema(close_list[:i+1], slow)
        if f is not None and s is not None:
            macd_values.append(f - s)
    if len(macd_values) < signal:
        return None, None, None
    signal_line = calculate_ema(macd_values, signal)
    if signal_line is None:
        return None, None, None
    macd_line = macd_values[-1]
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def analyze_timeframe(symbol, tf, params):
    data = histories[tf][symbol]
    if len(data['close']) < 2:
        return None
    open_price = list(data['open'])[-2]
    high = list(data['high'])[-2]
    low = list(data['low'])[-2]
    close = list(data['close'])[-2]

    close_list = list(data['close'])[:-1]
    if len(close_list) < 26:
        return None

    ema20 = calculate_ema(close_list, 20)
    ema50 = calculate_ema(close_list, 50)
    ema200 = calculate_ema(close_list, 200)

    macd_line, signal_line, hist = calculate_macd(close_list, MACD_FAST, MACD_SLOW, MACD_SIGNAL)

    cross = None
    if macd_line is not None and signal_line is not None:
        prev = prev_macd[tf][symbol]
        if prev['macd'] is not None and prev['signal'] is not None:
            if prev['macd'] <= prev['signal'] and macd_line > signal_line:
                cross = "bullish"
            elif prev['macd'] >= prev['signal'] and macd_line < signal_line:
                cross = "bearish"
        prev_macd[tf][symbol]['macd'] = macd_line
        prev_macd[tf][symbol]['signal'] = signal_line

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
    highs = list(histories[tf][symbol]['high'])[:-1]
    lows = list(histories[tf][symbol]['low'])[:-1]
    closes = list(histories[tf][symbol]['close'])[:-1]
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

    daily = tf_data.get(1440)
    weekly = tf_data.get(10080)

    daily_macd_bullish = daily and daily['macd_line'] is not None and daily['signal_line'] is not None and daily['macd_line'] > daily['signal_line']
    daily_macd_bearish = daily and daily['macd_line'] is not None and daily['signal_line'] is not None and daily['macd_line'] < daily['signal_line']
    daily_above_ema50 = daily and daily['ema50'] is not None and daily['close'] > daily['ema50']
    daily_below_ema50 = daily and daily['ema50'] is not None and daily['close'] < daily['ema50']

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

    if daily_macd_bullish:
        score_bear -= PENALTY_FOR_MACD_CONFLICT * 4
    elif daily_macd_bearish:
        score_bull -= PENALTY_FOR_MACD_CONFLICT * 4

    diff = score_bull - score_bear
    threshold = params.get('threshold_score', THRESHOLD_SCORE)

    direction = None
    if diff > threshold:
        direction = "BUY"
    elif diff < -threshold:
        direction = "SELL"
    else:
        return "NONE", None, None, diff, consensus_tf, None

    # Фильтр близости к EMA на всех ТФ
    for tf, data in tf_data.items():
        if data is None:
            continue
        price = data['close']
        for ema_name, ema_value in [('ema20', data['ema20']), ('ema50', data['ema50']), ('ema200', data['ema200'])]:
            if ema_value is None:
                continue
            distance_pct = abs(price - ema_value) / ema_value
            if distance_pct < EMA_CLOSE_PROXIMITY:
                if direction == "BUY" and price < ema_value:
                    logger.info(f"Запрет BUY {symbol}: цена {price} близка к {ema_name} {ema_value} на ТФ {tf} (расстояние {distance_pct*100:.2f}%)")
                    return "NONE", None, None, diff, consensus_tf, None
                elif direction == "SELL" and price > ema_value:
                    logger.info(f"Запрет SELL {symbol}: цена {price} близка к {ema_name} {ema_value} на ТФ {tf} (расстояние {distance_pct*100:.2f}%)")
                    return "NONE", None, None, diff, consensus_tf, None

    # Фильтр дневного тренда
    if direction == "BUY" and daily_above_ema50 and diff <= threshold + 3:
        return "NONE", None, None, diff, consensus_tf, None
    if direction == "SELL" and daily_below_ema50 and diff >= -threshold - 3:
        return "NONE", None, None, diff, consensus_tf, None

    # Расчёт TP
    all_levels = []
    for tf, data in tf_data.items():
        if data is None:
            continue
        for ema_name, ema_value in [('ema20', data['ema20']), ('ema50', data['ema50']), ('ema200', data['ema200'])]:
            if ema_value is not None:
                all_levels.append((tf, ema_name, ema_value))

    entry_price = last_completed_price.get(symbol)
    if entry_price is None:
        entry_price = tf_data[ENTRY_TF]['close'] if tf_data.get(ENTRY_TF) else None
        if entry_price is None:
            return "NONE", None, None, diff, consensus_tf, None

    if direction == "BUY":
        levels_above = [(tf, name, val) for tf, name, val in all_levels if val > entry_price]
        if levels_above:
            levels_above.sort(key=lambda x: x[2] - entry_price)
            tp = levels_above[0][2]
            logger.info(f"TP для {symbol} (BUY): ближайший уровень {levels_above[0][1]} на ТФ {levels_above[0][0]} = {tp}")
        else:
            tp = entry_price * 1.02
    else:
        levels_below = [(tf, name, val) for tf, name, val in all_levels if val < entry_price]
        if levels_below:
            levels_below.sort(key=lambda x: entry_price - x[2])
            tp = levels_below[0][2]
            logger.info(f"TP для {symbol} (SELL): ближайший уровень {levels_below[0][1]} на ТФ {levels_below[0][0]} = {tp}")
        else:
            tp = entry_price * 0.98

    if direction == "BUY":
        max_tp = entry_price * (1 + MAX_TP_PERCENT / 100)
        if tp > max_tp:
            tp = max_tp
        if (tp - entry_price) / entry_price < 0.005:
            tp = entry_price * 1.005
    else:
        min_tp = entry_price * (1 - MAX_TP_PERCENT / 100)
        if tp < min_tp:
            tp = min_tp
        if (entry_price - tp) / entry_price < 0.005:
            tp = entry_price * 0.995

    atr = calculate_atr_for_tf(symbol, 14, ENTRY_TF)
    if atr is None:
        atr = entry_price * 0.01
    if direction == "BUY":
        sl = entry_price - max(atr * SL_ATR_MULTIPLIER, entry_price * MIN_SL_PERCENT)
        if (entry_price - sl) / entry_price < MIN_SL_PERCENT:
            sl = entry_price * (1 - MIN_SL_PERCENT)
    else:
        sl = entry_price + max(atr * SL_ATR_MULTIPLIER, entry_price * MIN_SL_PERCENT)
        if (sl - entry_price) / entry_price < MIN_SL_PERCENT:
            sl = entry_price * (1 + MIN_SL_PERCENT)

    ema_vals = (daily['ema20'] if daily else None, daily['ema50'] if daily else None, daily['ema200'] if daily else None)

    return direction, tp, sl, diff, consensus_tf, ema_vals

# ---- Торговые функции ----
def get_session():
    return HTTP(
        testnet=False,
        demo=True,
        api_key=API_KEY,
        api_secret=API_SECRET,
        recv_window=30000
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

def get_last_completed_price(symbol, tf=15):
    data = fetch_klines_completed(symbol, tf, 1)
    if data and len(data) > 0:
        return float(data[0][4])
    return None

def add_trade_open(symbol, side, entry_price, qty, score, consensus_tf, ema_values, sl, tp):
    global open_trades
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if side == "Buy":
        expected_gain_pct = (tp - entry_price) / entry_price * 100
        expected_loss_pct = (entry_price - sl) / entry_price * 100
    else:
        expected_gain_pct = (entry_price - tp) / entry_price * 100
        expected_loss_pct = (sl - entry_price) / entry_price * 100
    open_trades[symbol] = {
        'timestamp_open': timestamp,
        'symbol': symbol,
        'side': side,
        'entry_price': entry_price,
        'qty': qty,
        'sl': sl,
        'tp': tp,
        'expected_gain_pct': expected_gain_pct,
        'expected_loss_pct': expected_loss_pct,
        'score': score,
        'consensus_tf': consensus_tf,
        'ema20': ema_values[0] if ema_values else None,
        'ema50': ema_values[1] if ema_values else None,
        'ema200': ema_values[2] if ema_values else None,
    }
    logger.info(f"СДЕЛКА ОТКРЫТА: {side} {symbol} | Вход {entry_price} | SL {sl} | TP {tp} | Прибыль {expected_gain_pct:.2f}% | Риск {expected_loss_pct:.2f}%")
    trade_logger.info(f"ОТКРЫТИЕ: {side} {symbol} qty={qty} цена={entry_price} SL={sl} TP={tp} gain={expected_gain_pct:.1f}% loss={expected_loss_pct:.1f}%")

def close_trade(symbol, exit_price, score=None):
    global open_trades
    if symbol not in open_trades:
        return
    trade = open_trades[symbol]
    entry = trade['entry_price']
    side = trade['side']
    qty = trade['qty']
    sl = trade['sl']
    tp = trade['tp']
    expected_gain = trade['expected_gain_pct']
    expected_loss = trade['expected_loss_pct']

    open_time = datetime.strptime(trade['timestamp_open'], '%Y-%m-%d %H:%M:%S')
    close_time = datetime.now()
    duration_minutes = (close_time - open_time).total_seconds() / 60

    if side == "Buy":
        gross_pnl = (exit_price - entry) * qty
    else:
        gross_pnl = (entry - exit_price) * qty
    commission_usdt = (entry * qty + exit_price * qty) * COMMISSION
    net_pnl = gross_pnl - commission_usdt
    gross_pnl_pct = (gross_pnl / (entry * qty)) * 100
    net_pnl_pct = (net_pnl / (entry * qty)) * 100
    result = "Win" if net_pnl > 0 else "Loss" if net_pnl < 0 else "Breakeven"

    with open(STATS_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow([
            trade['timestamp_open'],
            symbol,
            side,
            entry,
            qty,
            close_time.strftime('%Y-%m-%d %H:%M:%S'),
            exit_price,
            round(gross_pnl_pct, 2),
            result,
            round(commission_usdt, 2),
            round(net_pnl_pct, 2),
            round(score, 1) if score else "",
            trade.get('ema20', ""),
            trade.get('ema50', ""),
            trade.get('ema200', ""),
            "", "",
            trade.get('consensus_tf', ""),
            round(sl, 2),
            round(tp, 2),
            round(expected_gain, 2),
            round(expected_loss, 2),
            round(duration_minutes, 1)
        ])
    del open_trades[symbol]
    logger.info(f"Сделка закрыта: {side} {symbol} результат {result} (валовая {gross_pnl_pct:.2f}%, чистая {net_pnl_pct:.2f}%, комиссия {commission_usdt:.2f} USDT, длительность {int(duration_minutes)}м)")
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
logger.info("Загрузка истории для всех ТФ (только завершённые свечи)...")
for tf in TIMEFRAMES:
    for sym in SYMBOLS:
        data = fetch_klines_completed(sym, tf, 250)
        if data:
            for k in data:
                histories[tf][sym]['open'].append(float(k[1]))
                histories[tf][sym]['high'].append(float(k[2]))
                histories[tf][sym]['low'].append(float(k[3]))
                histories[tf][sym]['close'].append(float(k[4]))
                histories[tf][sym]['volume'].append(float(k[5]))
            logger.info(f"  {sym} ({tf}м): загружено {len(data)} завершённых свечей")
        else:
            logger.warning(f"  {sym} ({tf}м): не удалось загрузить")

# ---- Основной цикл ----
def main_loop():
    global open_trades, last_sl_time, last_completed_price
    logger.info(f"V6 Closed запущен (только закрытые свечи, трейлинг стопа на {TRAILING_TF}m EMA50).")
    last_update = {tf: 0 for tf in TIMEFRAMES}
    intervals = {
        5: 30,          # 5m – каждые 30 секунд
        15: 60,
        30: 120,
        60: 300,
        240: 600,
        1440: 3600,
        10080: 21600
    }
    last_candle_time = {tf: {sym: None for sym in SYMBOLS} for tf in TIMEFRAMES}

    while True:
        now = time.time()
        for tf in TIMEFRAMES:
            if now - last_update[tf] < intervals.get(tf, 60):
                continue
            for sym in SYMBOLS:
                data = fetch_klines_completed(sym, tf, 1)
                if data:
                    k = data[0]
                    candle_time = int(k[0])
                    if last_candle_time[tf][sym] != candle_time:
                        histories[tf][sym]['open'].append(float(k[1]))
                        histories[tf][sym]['high'].append(float(k[2]))
                        histories[tf][sym]['low'].append(float(k[3]))
                        histories[tf][sym]['close'].append(float(k[4]))
                        histories[tf][sym]['volume'].append(float(k[5]))
                        last_candle_time[tf][sym] = candle_time
                        if tf == ENTRY_TF:
                            last_completed_price[sym] = float(k[4])
            last_update[tf] = now

        for sym in SYMBOLS:
            has_pos = has_open_position(sym)
            if has_pos is None:
                continue

            # === Проверка закрытия позиции (если в open_trades есть, а в API нет) ===
            if sym in open_trades and not has_pos:
                last_price = get_last_completed_price(sym, ENTRY_TF)
                if last_price:
                    score = open_trades[sym].get('score', None)
                    close_trade(sym, last_price, score)
                else:
                    logger.warning(f"Не удалось получить цену для закрытия {sym}")
                continue

            # === ОБРАБОТКА ОТКРЫТОЙ ПОЗИЦИИ (передвижение стоп-лосса по 5m) ===
            if sym in open_trades and has_pos:
                trade = open_trades[sym]
                entry = trade['entry_price']
                current_price = histories[ENTRY_TF][sym]['close'][-1]
                side = trade['side']
                current_sl = trade['sl']
                current_tp = trade['tp']

                if TRAILING_STOP_ENABLED:
                    if side == "Buy":
                        profit_pct = (current_price - entry) / entry * 100
                    else:
                        profit_pct = (entry - current_price) / entry * 100

                    if profit_pct >= TRAILING_MIN_PROFIT_PCT:
                        tf_data = analyze_timeframe(sym, TRAILING_TF, current_params)
                        if tf_data is not None:
                            ema_value = tf_data['ema50'] if TRAILING_USE_EMA50 else tf_data['ema20']
                            # Проверяем MACD на разворот (опционально)
                            macd_hist = tf_data['histogram'] if tf_data is not None else None
                            if ema_value is not None:
                                # Базовое передвижение по EMA
                                if side == "Buy":
                                    new_sl = ema_value * (1 - TRAILING_OFFSET_PCT / 100)
                                    # Дополнительное условие: если MACD отрицательный (разворот вниз) – передвигаем
                                    if macd_hist is not None and macd_hist < 0:
                                        if new_sl > current_sl and new_sl < current_price:
                                            if (new_sl - current_sl) / entry * 100 >= TRAILING_STEP_PCT:
                                                set_stop_loss_take_profit(sym, "Buy", new_sl, current_tp)
                                                open_trades[sym]['sl'] = new_sl
                                                logger.info(f"Стоп-лосс {sym} передвинут на {new_sl:.4f} (EMA{50 if TRAILING_USE_EMA50 else 20} на {TRAILING_TF}m, MACD разворот, прибыль {profit_pct:.1f}%)")
                                else:  # Sell
                                    new_sl = ema_value * (1 + TRAILING_OFFSET_PCT / 100)
                                    if macd_hist is not None and macd_hist > 0:
                                        if new_sl < current_sl and new_sl > current_price:
                                            if (current_sl - new_sl) / entry * 100 >= TRAILING_STEP_PCT:
                                                set_stop_loss_take_profit(sym, "Sell", new_sl, current_tp)
                                                open_trades[sym]['sl'] = new_sl
                                                logger.info(f"Стоп-лосс {sym} передвинут на {new_sl:.4f} (EMA{50 if TRAILING_USE_EMA50 else 20} на {TRAILING_TF}m, MACD разворот, прибыль {profit_pct:.1f}%)")
                continue

            # === ГЕНЕРАЦИЯ НОВОГО СИГНАЛА ===
            signal, tp, sl, diff, consensus_tf, ema_vals = generate_signal(sym, current_params)
            if signal == "NONE":
                continue

            if sym in last_sl_time:
                if (datetime.now() - last_sl_time[sym]).total_seconds() < RE_ENTRY_AFTER_SL * ENTRY_TF * 60:
                    logger.info(f"{sym} пропущен: повторный вход запрещён {RE_ENTRY_AFTER_SL} свечей после SL")
                    continue

            entry_price = last_completed_price.get(sym)
            if entry_price is None:
                entry_price = histories[ENTRY_TF][sym]['close'][-2] if len(histories[ENTRY_TF][sym]['close']) >= 2 else None
                if entry_price is None:
                    logger.warning(f"Нет цены входа для {sym}, пропускаем")
                    continue

            current_price = histories[ENTRY_TF][sym]['close'][-1] if len(histories[ENTRY_TF][sym]['close']) >= 1 else entry_price
            if abs(current_price - entry_price) / entry_price > 0.005:
                logger.info(f"{sym} пропущен: цена ушла от уровня входа на {abs(current_price - entry_price)/entry_price*100:.2f}%")
                continue

            if signal == "BUY":
                expected_pct = (tp - entry_price) / entry_price * 100
            else:
                expected_pct = (entry_price - tp) / entry_price * 100
            if expected_pct <= COMMISSION * 2 * 100:
                logger.info(f"{sym} пропущен: ожидаемая прибыль {expected_pct:.2f}% <= комиссии {COMMISSION*2*100:.2f}%")
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
                risk_pct = abs(sl - entry_price) / entry_price * 100
                logger.info(f"РАСЧЁТ: {side} {sym} | Вход {entry_price} | SL {sl} | TP {tp} | Прибыль {expected_pct:.2f}% | Риск {risk_pct:.2f}%")
                logger.info(f"Попытка открыть {side} {sym} лимит по {limit_price} (текущая {current_price}, qty={qty})")
                order_result = place_limit_order(sym, side, qty, limit_price)
            else:
                risk_pct = abs(sl - entry_price) / entry_price * 100
                logger.info(f"РАСЧЁТ: {side} {sym} | Вход {entry_price} | SL {sl} | TP {tp} | Прибыль {expected_pct:.2f}% | Риск {risk_pct:.2f}%")
                logger.info(f"Попытка открыть {side} {sym} рыночный qty={qty}")
                order_result = place_market_order(sym, side, qty)

            if order_result:
                pos_side = "Buy" if signal == "BUY" else "Sell"
                set_stop_loss_take_profit(sym, pos_side, sl, tp)
                add_trade_open(sym, side, entry_price, qty, diff, consensus_tf, ema_vals, sl, tp)
                if sym in last_sl_time:
                    del last_sl_time[sym]
            else:
                logger.error(f"Не удалось открыть позицию {sym}")

        time.sleep(60)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        logger.info("Остановка по запросу пользователя.")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
