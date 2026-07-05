import time
import requests
import logging
import os
import csv
from collections import deque
from datetime import datetime
from plyer import notification
from pybit.unified_trading import HTTP

# ===== НАСТРОЙКИ АНАЛИЗА =====
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
INTERVAL = 240                    # 4 часа
LIMIT = 200
VOLUME_INTERVAL = 15
VOLUME_THRESHOLD = 2.0
HVN_THRESHOLD = 1.5
HVN_TOUCH_PERCENT = 0.003
SLEEP_TIME = 300
SIGNAL_DIRECTION = "both"
DOJI_ENABLED = True
DOJI_THRESHOLD = 0.1
MACD_TIMEFRAMES = [60, 240]
LONG_CANDLE_THRESHOLD = 0.7
RECOMMENDATION_ENABLED = True
RECOMMENDATION_THRESHOLD = 60
ATR_PERIOD = 14
SL_MULTIPLIER = 1.5
TP1_MULTIPLIER = 2.0
TP2_MULTIPLIER = 3.0

# ===== НАСТРОЙКИ ТОРГОВЛИ (ДЕМО) =====
API_KEY = "0000"            # ЗАМЕНИ НА СВОЙ
API_SECRET = "00000"      # ЗАМЕНИ НА СВОЙ
CATEGORY = "linear"                       # "linear" для фьючерсов
LEVERAGE = 3                              # плечо (не используется)
COMMISSION = 0.00055                      # 0.055% комиссия за сделку (вход+выход)
ORDER_SIZE = {                            # размер позиции в контрактах
    "BTCUSDT": 0.01,
    "ETHUSDT": 0.1,
    "SOLUSDT": 1.0,
    "XRPUSDT": 100.0
}
# ======================================

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("demo_trader.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

trade_logger = logging.getLogger("trade")
trade_logger.setLevel(logging.INFO)
trade_handler = logging.FileHandler("trades.log", encoding='utf-8')
trade_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
trade_logger.addHandler(trade_handler)

print(f"Логи будут сохранены в: {os.getcwd()}")

# ---- Инициализация CSV для статистики сделок ----
STATS_CSV = "trades_stats.csv"
STATS_HEADERS = ["timestamp_open", "symbol", "side", "entry_price", "qty", "timestamp_close", "exit_price", "gross_pnl_pct", "result", "commission_usdt", "net_pnl_pct"]

def init_stats_csv():
    if os.path.exists(STATS_CSV):
        with open(STATS_CSV, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=';')
            headers = next(reader, [])
        if headers != STATS_HEADERS:
            backup_name = f"trades_stats_old_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            os.rename(STATS_CSV, backup_name)
            logger.info(f"Старый файл статистики переименован в {backup_name}")
            with open(STATS_CSV, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerow(STATS_HEADERS)
            logger.info("Создан новый файл статистики с обновлёнными колонками")
    else:
        with open(STATS_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(STATS_HEADERS)
        logger.info("Создан новый файл статистики")

init_stats_csv()

# ---- Хранилище для открытых сделок ----
open_trades = {}  # key: symbol, value: dict с данными сделки (включая qty)

def add_trade_open(symbol, side, entry_price, qty):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    open_trades[symbol] = {
        'timestamp_open': timestamp,
        'symbol': symbol,
        'side': side,
        'entry_price': entry_price,
        'qty': qty
    }
    with open(STATS_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow([timestamp, symbol, side, entry_price, qty, "", "", "", "", "", ""])
    logger.info(f"Сделка открыта: {side} {symbol} по {entry_price}, qty={qty}")

def close_trade(symbol, exit_price):
    if symbol not in open_trades:
        return
    trade = open_trades[symbol]
    entry = trade['entry_price']
    side = trade['side']
    qty = trade['qty']

    # Расчёт валовой прибыли (в USDT)
    if side == "Buy":
        gross_pnl = (exit_price - entry) * qty
    else:  # Sell
        gross_pnl = (entry - exit_price) * qty

    # Расчёт комиссии (в USDT) – взимается как при входе, так и при выходе
    commission_usdt = (entry * qty + exit_price * qty) * COMMISSION
    net_pnl = gross_pnl - commission_usdt
    gross_pnl_pct = (gross_pnl / (entry * qty)) * 100
    net_pnl_pct = (net_pnl / (entry * qty)) * 100
    result = "Win" if net_pnl > 0 else "Loss" if net_pnl < 0 else "Breakeven"

    # Обновляем CSV
    rows = []
    updated = False
    if os.path.exists(STATS_CSV):
        with open(STATS_CSV, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=';')
            rows = list(reader)
        for i in range(len(rows)-1, -1, -1):
            if len(rows[i]) >= 4 and rows[i][1] == symbol and rows[i][5] == "":
                rows[i][5] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')  # timestamp_close
                rows[i][6] = str(exit_price)
                rows[i][7] = str(round(gross_pnl_pct, 2))
                rows[i][8] = result
                rows[i][9] = str(round(commission_usdt, 2))
                rows[i][10] = str(round(net_pnl_pct, 2))
                updated = True
                break
        if updated:
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
        reader = csv.reader(f, delimiter=';')
        rows = list(reader)
    if len(rows) < 2:
        logger.info("Статистика: пока нет завершённых сделок")
        return
    trades = rows[1:]
    wins = 0
    losses = 0
    total = 0
    for row in trades:
        if len(row) >= 11 and row[8] != "":
            total += 1
            if row[8] == "Win":
                wins += 1
            elif row[8] == "Loss":
                losses += 1
    if total == 0:
        logger.info("Статистика: пока нет завершённых сделок")
        return
    winrate = wins / total * 100 if total > 0 else 0
    logger.info(f"📊 Статистика сделок: Всего {total}, Win {wins} ({winrate:.1f}%), Loss {losses}")

# ---- ВСЕ ФУНКЦИИ ИНДИКАТОРОВ И ЗАГРУЗКИ ----
def format_interval(minutes):
    if minutes % 60 == 0:
        hours = minutes // 60
        if hours < 24:
            return f"{hours}H"
        else:
            return f"{hours//24}D"
    return f"{minutes}м"

TF_STR = format_interval(INTERVAL)
VOLUME_TF_STR = format_interval(VOLUME_INTERVAL)

histories = {sym: {
    'open': deque(maxlen=LIMIT),
    'high': deque(maxlen=LIMIT),
    'low': deque(maxlen=LIMIT),
    'close': deque(maxlen=LIMIT),
    'volume': deque(maxlen=LIMIT)
} for sym in SYMBOLS}

macd_histories = {tf: {sym: {
    'close': deque(maxlen=LIMIT),
    'macd_line': deque(maxlen=200),
    'signal_line': None
} for sym in SYMBOLS} for tf in MACD_TIMEFRAMES}

volume_histories = {sym: deque(maxlen=50) for sym in SYMBOLS}
prev_macd = {tf: {sym: {'macd_line': None, 'signal_line': None} for sym in SYMBOLS} for tf in MACD_TIMEFRAMES}
hvn_cache = {sym: [] for sym in SYMBOLS}
last_hvn_update = {sym: 0 for sym in SYMBOLS}

def fetch_klines(symbol, interval, limit):
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "spot", "symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data['retCode'] == 0:
            klines = data['result']['list']
            klines.reverse()
            return klines
        else:
            logger.error(f"Ошибка {symbol}: {data['retMsg']}")
            return None
    except Exception as e:
        logger.error(f"Ошибка запроса {symbol}: {e}")
        return None

def calculate_ema(series, period):
    if len(series) < period:
        return None
    k = 2 / (period + 1)
    ema = series[0]
    for price in series[1:]:
        ema = price * k + ema * (1 - k)
    return ema

def get_indicators(symbol):
    close_list = list(histories[symbol]['close'])
    if len(close_list) < 26:
        return None
    ema20 = calculate_ema(close_list, 20)
    ema50 = calculate_ema(close_list, 50)
    ema200 = calculate_ema(close_list, 200)
    if None in (ema20, ema50, ema200):
        return None
    ema_fast = calculate_ema(close_list, 12)
    ema_slow = calculate_ema(close_list, 26)
    if ema_fast is None or ema_slow is None:
        return None
    macd_line = ema_fast - ema_slow
    macd_histories[INTERVAL][sym]['macd_line'].append(macd_line)
    if len(macd_histories[INTERVAL][sym]['macd_line']) >= 9:
        signal_line = calculate_ema(list(macd_histories[INTERVAL][sym]['macd_line']), 9)
        hist = macd_line - signal_line if signal_line is not None else None
    else:
        signal_line = None
        hist = None
    return {
        'open': list(histories[sym]['open'])[-1],
        'high': list(histories[sym]['high'])[-1],
        'low': list(histories[sym]['low'])[-1],
        'close': close_list[-1],
        'ema20': ema20,
        'ema50': ema50,
        'ema200': ema200,
        'macd_line': macd_line,
        'signal_line': signal_line,
        'hist': hist
    }

def get_macd_for_timeframe(symbol, tf):
    close_list = list(macd_histories[tf][sym]['close'])
    if len(close_list) < 26:
        return None
    ema_fast = calculate_ema(close_list, 12)
    ema_slow = calculate_ema(close_list, 26)
    if ema_fast is None or ema_slow is None:
        return None
    macd_line = ema_fast - ema_slow
    macd_histories[tf][sym]['macd_line'].append(macd_line)
    if len(macd_histories[tf][sym]['macd_line']) >= 9:
        signal_line = calculate_ema(list(macd_histories[tf][sym]['macd_line']), 9)
        hist = macd_line - signal_line if signal_line is not None else None
    else:
        signal_line = None
        hist = None
    return macd_line, signal_line, hist

def build_volume_profile(symbol):
    highs = list(histories[symbol]['high'])
    lows = list(histories[symbol]['low'])
    volumes = list(histories[symbol]['volume'])
    if len(highs) < 50:
        return []
    min_price = min(lows)
    max_price = max(highs)
    if max_price == min_price:
        return []
    num_bins = 50
    bin_size = (max_price - min_price) / num_bins
    bins = [0] * num_bins
    for i in range(len(highs)):
        avg_price = (highs[i] + lows[i]) / 2
        bin_idx = int((avg_price - min_price) / bin_size)
        if 0 <= bin_idx < num_bins:
            bins[bin_idx] += volumes[i]
    avg_vol = sum(bins) / num_bins
    hvn_levels = []
    for i, vol in enumerate(bins):
        if vol > avg_vol * HVN_THRESHOLD:
            price_level = min_price + (i + 0.5) * bin_size
            hvn_levels.append(round(price_level, 2))
    return hvn_levels

def send_alert(title, message, direction="НЕЙТРАЛЬНО"):
    full_msg = f"[{direction}] {message}"
    logger.info(f"{title}: {full_msg}")
    try:
        notification.notify(title=title, message=full_msg, timeout=5)
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление: {e}")

def is_doji(open_price, close_price, high, low, threshold):
    if high == low:
        return False
    body = abs(open_price - close_price)
    range_ = high - low
    return body <= threshold * range_

def get_doji_signal(symbol, close_price, ema200):
    close_list = list(histories[symbol]['close'])
    open_list = list(histories[symbol]['open'])
    if len(close_list) < 2 or len(open_list) < 2:
        return "НЕЙТРАЛЬНО"
    prev_close = close_list[-2]
    prev_open = open_list[-2]
    prev_bullish = prev_close > prev_open
    prev_bearish = prev_close < prev_open
    trend_bullish = close_price > ema200
    trend_bearish = close_price < ema200
    if trend_bullish and prev_bullish:
        return "БЫЧИЙ"
    elif trend_bearish and prev_bearish:
        return "МЕДВЕЖИЙ"
    else:
        return "НЕЙТРАЛЬНО"

def is_long_candle(open_price, close_price, high, low, threshold):
    if high == low:
        return False
    body = abs(open_price - close_price)
    range_ = high - low
    return body > threshold * range_

def check_macd_cross(symbol, tf, macd_line, signal_line):
    prev = prev_macd[tf][symbol]
    cross = None
    if prev['macd_line'] is not None and prev['signal_line'] is not None:
        if prev['macd_line'] <= prev['signal_line'] and macd_line > signal_line:
            cross = "bullish"
        elif prev['macd_line'] >= prev['signal_line'] and macd_line < signal_line:
            cross = "bearish"
    prev_macd[tf][symbol]['macd_line'] = macd_line
    prev_macd[tf][symbol]['signal_line'] = signal_line
    return cross

def calculate_atr(symbol, period=ATR_PERIOD):
    highs = list(histories[symbol]['high'])
    lows = list(histories[symbol]['low'])
    closes = list(histories[symbol]['close'])
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
    else:
        return None

def calculate_sl_tp(symbol, price, direction, atr):
    hvn_levels = hvn_cache.get(symbol, [])
    hvn_levels = sorted(hvn_levels)
    below = [l for l in hvn_levels if l < price]
    above = [l for l in hvn_levels if l > price]
    nearest_below = below[-1] if below else price * 0.95
    nearest_above = above[0] if above else price * 1.05

    if direction == "ЛОНГ":
        sl_candidate1 = price - SL_MULTIPLIER * atr
        sl = min(nearest_below, sl_candidate1)
        tp1 = nearest_above
        if len(above) > 1:
            tp2 = above[1]
        else:
            tp2 = price + TP2_MULTIPLIER * atr
        return round(sl, 2), round(tp1, 2), round(tp2, 2)
    else:
        sl_candidate1 = price + SL_MULTIPLIER * atr
        sl = max(nearest_above, sl_candidate1)
        tp1 = nearest_below
        if len(below) > 1:
            tp2 = below[-2]
        else:
            tp2 = price - TP2_MULTIPLIER * atr
        return round(sl, 2), round(tp1, 2), round(tp2, 2)

def get_recommendation(sym, open_price, high, low, close,
                       ema20, ema50, ema200,
                       macd_line_4h, signal_line_4h, hist_4h,
                       macd_data, volume_ratio, volume_direction,
                       hvn_touch, doji_dir, long_candle_dir):
    score_long = 0
    score_short = 0
    WEIGHT_EMA20 = 15
    WEIGHT_EMA50 = 10
    WEIGHT_MACD_CROSS_4H = 25
    WEIGHT_MACD_REVERSAL_1H = 20
    WEIGHT_VOLUME = 15
    WEIGHT_HVN = 5
    WEIGHT_DOJI = 5
    WEIGHT_LONG_CANDLE = 10

    if low <= ema20 <= high:
        if close > ema20:
            score_long += WEIGHT_EMA20
        else:
            score_short += WEIGHT_EMA20

    if low <= ema50 <= high:
        if close > ema50:
            score_long += WEIGHT_EMA50
        else:
            score_short += WEIGHT_EMA50

    if signal_line_4h is not None and hist_4h is not None:
        cross = check_macd_cross(sym, INTERVAL, macd_line_4h, signal_line_4h)
        if cross == "bullish":
            score_long += WEIGHT_MACD_CROSS_4H
        elif cross == "bearish":
            score_short += WEIGHT_MACD_CROSS_4H

    if 60 in macd_data and macd_data[60]['signal'] is not None:
        macd_1h_line = macd_data[60]['line']
        macd_1h_hist = macd_data[60]['hist']
        if macd_line_4h < 0 and macd_1h_line > 0 and macd_1h_hist > 0:
            score_long += WEIGHT_MACD_REVERSAL_1H
        elif macd_line_4h > 0 and macd_1h_line < 0 and macd_1h_hist < 0:
            score_short += WEIGHT_MACD_REVERSAL_1H

    if volume_ratio >= VOLUME_THRESHOLD:
        if volume_direction == "ПОКУПКА":
            score_long += WEIGHT_VOLUME
        else:
            score_short += WEIGHT_VOLUME

    if hvn_touch:
        if close > ema200:
            score_long += WEIGHT_HVN
        else:
            score_short += WEIGHT_HVN

    if doji_dir == "БЫЧИЙ":
        score_long += WEIGHT_DOJI
    elif doji_dir == "МЕДВЕЖИЙ":
        score_short += WEIGHT_DOJI

    if long_candle_dir == "ЛОНГ":
        score_long += WEIGHT_LONG_CANDLE
    elif long_candle_dir == "ШОРТ":
        score_short += WEIGHT_LONG_CANDLE

    total = score_long + score_short
    if total == 0:
        return "НЕЙТРАЛЬНО", 0
    confidence = (score_long - score_short) / total * 100
    if confidence >= RECOMMENDATION_THRESHOLD:
        return "КУПИТЬ", confidence
    elif confidence <= -RECOMMENDATION_THRESHOLD:
        return "ПРОДАТЬ", confidence
    else:
        return "ЖДАТЬ", confidence

# ---- ЗАГРУЗКА ИСТОРИИ ----
logger.info("Загрузка истории...")
for sym in SYMBOLS:
    data = fetch_klines(sym, INTERVAL, LIMIT)
    if data:
        for k in data:
            histories[sym]['open'].append(float(k[1]))
            histories[sym]['high'].append(float(k[2]))
            histories[sym]['low'].append(float(k[3]))
            histories[sym]['close'].append(float(k[4]))
            histories[sym]['volume'].append(float(k[5]))
        logger.info(f"  {sym}: загружено {len(data)} свечей ({TF_STR})")
    else:
        logger.warning(f"  {sym}: не удалось загрузить")

for tf in MACD_TIMEFRAMES:
    tf_str = format_interval(tf)
    for sym in SYMBOLS:
        data = fetch_klines(sym, tf, LIMIT)
        if data:
            for k in data:
                macd_histories[tf][sym]['close'].append(float(k[4]))
            logger.info(f"  {sym} ({tf_str}): загружено {len(data)} свечей")
        else:
            logger.warning(f"  {sym} ({tf_str}): не удалось загрузить")

for sym in SYMBOLS:
    data = fetch_klines(sym, VOLUME_INTERVAL, 50)
    if data:
        for k in data:
            volume_histories[sym].append(float(k[5]))
        logger.info(f"  {sym}: загружено {len(data)} свечей ({VOLUME_TF_STR})")
    else:
        logger.warning(f"  {sym}: не удалось загрузить объёмы")

# ---- ФУНКЦИИ ДЛЯ РАБОТЫ С ПОЗИЦИЯМИ (демо-торговля) ----
def get_session():
    return HTTP(
        testnet=False,
        demo=True,
        api_key=API_KEY,
        api_secret=API_SECRET
    )

def place_order(symbol, side, order_type, qty, price=None, reduce_only=False):
    session = get_session()
    params = {
        "category": CATEGORY,
        "symbol": symbol,
        "side": side,
        "orderType": order_type,
        "qty": str(qty),
        "timeInForce": "GTC"
    }
    if price:
        params["price"] = str(price)
    if reduce_only:
        params["reduceOnly"] = True
    try:
        resp = session.place_order(**params)
        if resp['retCode'] == 0:
            logger.info(f"Ордер {side} {order_type} на {symbol} qty={qty} размещён")
            trade_logger.info(f"ОТКРЫТИЕ: {side} {symbol} qty={qty} цена={price or 'MARKET'}")
            return resp['result']
        else:
            logger.error(f"Ошибка размещения ордера: {resp['retMsg']}")
            return None
    except Exception as e:
        logger.error(f"Исключение при размещении ордера: {e}")
        return None

def set_stop_loss_take_profit(symbol, position_side, sl_price, tp1_price):
    session = get_session()
    try:
        sl_params = {
            "category": CATEGORY,
            "symbol": symbol,
            "side": position_side,
            "stopLoss": str(sl_price),
            "stopLossTrigger": "LastPrice"
        }
        if tp1_price:
            sl_params["takeProfit"] = str(tp1_price)
            sl_params["takeProfitTrigger"] = "LastPrice"
        resp = session.set_trading_stop(**sl_params)
        if resp['retCode'] == 0:
            logger.info(f"SL/TP установлены для {symbol}: SL={sl_price}, TP={tp1_price}")
            trade_logger.info(f"SL/TP: SL={sl_price} TP={tp1_price}")
            return True
        else:
            logger.error(f"Ошибка установки SL/TP: {resp['retMsg']}")
            return False
    except Exception as e:
        logger.error(f"Исключение при установке SL/TP: {e}")
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
        logger.error(f"Исключение при получении позиций: {e}")
        return None

def has_open_position(symbol):
    positions = get_open_positions()
    if positions is None:
        return None
    for pos in positions:
        if pos['symbol'] == symbol and float(pos['size']) > 0:
            return True
    return False

def get_last_price(symbol):
    data = fetch_klines(symbol, 1, 1)
    if data and len(data) > 0:
        return float(data[0][4])
    return None

# ---- ОСНОВНОЙ ЦИКЛ С ПРОВЕРКОЙ КОМИССИИ ----
def main_loop():
    logger.info("Демо-трейдер запущен (с учётом комиссии). Ожидание сигналов...")
    while True:
        now = datetime.now()
        current_time = now.timestamp()
        for sym in SYMBOLS:
            # Получение свечи
            data = fetch_klines(sym, INTERVAL, 1)
            if data is None or len(data) == 0:
                continue
            k = data[0]
            open_price = float(k[1])
            high = float(k[2])
            low = float(k[3])
            close = float(k[4])
            volume = float(k[5])
            open_time = int(k[0])

            histories[sym]['open'].append(open_price)
            histories[sym]['high'].append(high)
            histories[sym]['low'].append(low)
            histories[sym]['close'].append(close)
            histories[sym]['volume'].append(volume)

            ind = get_indicators(sym)
            if ind is None:
                continue
            ema20 = ind['ema20']
            ema50 = ind['ema50']
            ema200 = ind['ema200']
            macd_line_4h = ind['macd_line']
            signal_line_4h = ind['signal_line']
            hist_4h = ind['hist']

            # MACD доп. ТФ
            macd_data = {}
            for tf in MACD_TIMEFRAMES:
                if tf == INTERVAL:
                    continue
                tf_data = fetch_klines(sym, tf, 1)
                if tf_data:
                    last_close = float(tf_data[0][4])
                    macd_histories[tf][sym]['close'].append(last_close)
                macd_tf = get_macd_for_timeframe(sym, tf)
                if macd_tf is not None:
                    macd_data[tf] = {
                        'line': macd_tf[0],
                        'signal': macd_tf[1],
                        'hist': macd_tf[2]
                    }

            dt_str = datetime.fromtimestamp(open_time/1000).strftime('%Y-%m-%d %H:%M')
            print(f"\n[{sym}] {dt_str} | Open: {open_price:.2f}, Close: {close:.2f}")
            print(f"  EMA20: {ema20:.2f} | EMA50: {ema50:.2f} | EMA200: {ema200:.2f}")
            if signal_line_4h is not None and hist_4h is not None:
                print(f"  MACD (4H): {macd_line_4h:.4f} | Signal: {signal_line_4h:.4f} | Hist: {hist_4h:.4f}")
            for tf, d in macd_data.items():
                if d['signal'] is not None:
                    tf_str = format_interval(tf)
                    print(f"  MACD ({tf_str}): {d['line']:.4f} | Signal: {d['signal']:.4f} | Hist: {d['hist']:.4f}")

            # ----- СБОР СИГНАЛОВ -----
            vol_ratio = 0
            vol_direction = "НЕЙТРАЛЬНО"
            vol_data = fetch_klines(sym, VOLUME_INTERVAL, 1)
            if vol_data and len(vol_data) > 0:
                vol = float(vol_data[0][5])
                volume_histories[sym].append(vol)
                if len(volume_histories[sym]) >= 10:
                    avg_vol = sum(list(volume_histories[sym])[:-1]) / (len(volume_histories[sym]) - 1)
                    vol_ratio = vol / avg_vol if avg_vol > 0 else 0
                    vol_direction = "ПОКУПКА" if close > open_price else "ПРОДАЖА"
                    print(f"  Объём ({VOLUME_TF_STR}): {vol:.0f}, средний: {avg_vol:.0f}, отношение: {vol_ratio:.2f}x ({vol_direction})")
                    if vol_ratio >= VOLUME_THRESHOLD:
                        msg = f"Объём: {vol:.0f}, средний: {avg_vol:.0f}, отношение: {vol_ratio:.2f}x ({vol_direction})"
                        send_alert(f"Аномальный объём {sym} ({VOLUME_TF_STR})", msg, vol_direction)

            hvn_touch = False
            if current_time - last_hvn_update[sym] > 300:
                hvn_cache[sym] = build_volume_profile(sym)
                last_hvn_update[sym] = current_time
                if hvn_cache[sym]:
                    print(f"  HVN для {sym}: {hvn_cache[sym]}")
            if hvn_cache[sym]:
                for level in hvn_cache[sym]:
                    if (abs(close - level) <= level * HVN_TOUCH_PERCENT or
                        abs(high - level) <= level * HVN_TOUCH_PERCENT or
                        abs(low - level) <= level * HVN_TOUCH_PERCENT):
                        hvn_touch = True
                        msg = f"Цена {close:.2f} коснулась HVN {level:.2f} (High: {high:.2f}, Low: {low:.2f})"
                        send_alert(f"HVN касание {sym}", msg, "НЕЙТРАЛЬНО")
                        break

            doji_dir = "НЕЙТРАЛЬНО"
            if DOJI_ENABLED and is_doji(open_price, close, high, low, DOJI_THRESHOLD):
                doji_dir = get_doji_signal(sym, close, ema200)
                if doji_dir == "НЕЙТРАЛЬНО":
                    if close > open_price:
                        doji_dir = "БЫЧИЙ_СВЕЧА"
                    elif close < open_price:
                        doji_dir = "МЕДВЕЖИЙ_СВЕЧА"
                msg = f"Доджи на {TF_STR} ТФ: Open={open_price:.2f}, Close={close:.2f}, High={high:.2f}, Low={low:.2f}"
                send_alert(f"Доджи {sym}", msg, doji_dir)

            long_candle_dir = "НЕЙТРАЛЬНО"
            if is_long_candle(open_price, close, high, low, LONG_CANDLE_THRESHOLD):
                long_candle_dir = "ЛОНГ" if close > open_price else "ШОРТ"
                msg = f"Длинная свеча на {TF_STR}: тело={abs(close-open_price):.2f}, диапазон={high-low:.2f} ({abs(close-open_price)/(high-low)*100:.1f}%)"
                send_alert(f"Длинная свеча {sym}", msg, long_candle_dir)

            # ----- ГЕНЕРАЦИЯ РЕКОМЕНДАЦИИ И ТОРГОВЛЯ -----
            if RECOMMENDATION_ENABLED:
                recommendation, confidence = get_recommendation(
                    sym,
                    open_price,
                    high,
                    low,
                    close,
                    ema20,
                    ema50,
                    ema200,
                    macd_line_4h,
                    signal_line_4h,
                    hist_4h,
                    macd_data,
                    vol_ratio,
                    vol_direction,
                    hvn_touch,
                    doji_dir,
                    long_candle_dir
                )
                if recommendation != "НЕЙТРАЛЬНО" and abs(confidence) >= RECOMMENDATION_THRESHOLD:
                    atr = calculate_atr(sym)
                    if atr is not None:
                        direction = "ЛОНГ" if recommendation == "КУПИТЬ" else "ШОРТ"
                        sl, tp1, tp2 = calculate_sl_tp(sym, close, direction, atr)

                        # ----- ПРОВЕРКА КОМИССИИ -----
                        # Рассчитываем ожидаемую валовую прибыль по TP1 (в процентах от входа)
                        if recommendation == "КУПИТЬ":
                            expected_gross_pct = (tp1 - close) / close * 100
                        else:
                            expected_gross_pct = (close - tp1) / close * 100
                        # Комиссия в процентах от входа (примерно 0.11% для двух сторон)
                        commission_pct = COMMISSION * 2 * 100  # 0.11%
                        if expected_gross_pct <= commission_pct:
                            logger.info(f"Сделка по {sym} пропущена: ожидаемая прибыль {expected_gross_pct:.2f}% не покрывает комиссию {commission_pct:.2f}%")
                            continue

                        msg = (f"Рекомендация: {recommendation} с уверенностью {abs(confidence):.1f}%\n"
                               f"SL: {sl} | TP1: {tp1} | TP2: {tp2}")
                        send_alert(f"СИГНАЛ {sym}", msg, recommendation)

                        # ----- ТОРГОВАЯ ЛОГИКА -----
                        has_pos = has_open_position(sym)
                        if has_pos is None:
                            logger.warning(f"Не удалось проверить позицию по {sym}, пропускаем итерацию")
                            continue
                        if has_pos:
                            logger.info(f"По {sym} уже есть открытая позиция, пропускаем")
                            continue

                        # Проверка закрытых позиций (если в open_trades есть, а позиции нет)
                        if sym in open_trades and not has_pos:
                            last_price = get_last_price(sym)
                            if last_price:
                                close_trade(sym, last_price)
                            else:
                                logger.warning(f"Не удалось получить цену для закрытия {sym}")

                        qty = ORDER_SIZE.get(sym, 0.001)
                        if qty == 0:
                            logger.warning(f"Размер позиции для {sym} не задан")
                            continue

                        # Корректировка qty для минимальной суммы 5 USDT
                        min_order_value = 5.0
                        if qty * close < min_order_value:
                            qty = min_order_value / close
                            qty = round(qty, 6)

                        side = "Buy" if recommendation == "КУПИТЬ" else "Sell"
                        order_result = place_order(sym, side, "Market", qty)
                        if order_result:
                            pos_side = "Buy" if recommendation == "КУПИТЬ" else "Sell"
                            set_stop_loss_take_profit(sym, pos_side, sl, tp1)
                            add_trade_open(sym, side, close, qty)
                        else:
                            logger.error(f"Не удалось открыть позицию {sym}")

        time.sleep(SLEEP_TIME)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        logger.info("Остановка по запросу пользователя.")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
