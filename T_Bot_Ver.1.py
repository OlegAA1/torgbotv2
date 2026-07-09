import asyncio
from datetime import datetime, timedelta

from tinkoff.invest import (
    Client,
    SandboxClient,
    CandleInterval,
    Quotation,
    RequestError,
)
from tinkoff.invest.utils import quotation_to_decimal


# Токен песочницы
TOKEN = ""

# FIGI для Сбербанка (пример)
SBER_FIGI = "BBG004730N88"  # можно заменить после получения через instruments


async def main():
    # 1. Создаём клиент для песочницы
    async with SandboxClient(TOKEN, target="sandbox-invest-public-api.tbank.ru:443") as sandbox:
        # 2. Открываем счёт
        try:
            account = await sandbox.sandbox.open_sandbox_account()
            account_id = account.account_id
            print(f"✅ Аккаунт открыт, ID: {account_id}")
        except RequestError as e:
            print(f"❌ Ошибка открытия счёта: {e}")
            return

        # 3. Пополняем баланс (100 000 RUB)
        try:
            await sandbox.sandbox.sandbox_pay_in(
                account_id=account_id,
                amount=Quotation(units=100000, nano=0),
            )
            print("💰 Баланс пополнен на 100 000 RUB")
        except RequestError as e:
            print(f"❌ Ошибка пополнения: {e}")
            return

        # 4. Получаем свечи для Сбербанка (последние 10 дневных свечей)
        try:
            candles = await sandbox.market_data.get_candles(
                figi=SBER_FIGI,
                from_=datetime.now() - timedelta(days=10),
                to=datetime.now(),
                interval=CandleInterval.CANDLE_INTERVAL_1_DAY,
            )
            print(f"📊 Получено {len(candles.candles)} свечей")
            for c in candles.candles[-5:]:  # покажем последние 5
                print(
                    f"{c.time.strftime('%Y-%m-%d')} → "
                    f"O={quotation_to_decimal(c.open):.2f}, "
                    f"H={quotation_to_decimal(c.high):.2f}, "
                    f"L={quotation_to_decimal(c.low):.2f}, "
                    f"C={quotation_to_decimal(c.close):.2f}"
                )
        except RequestError as e:
            print(f"❌ Ошибка получения свечей: {e}")
            return

        # 5. Можно также получить список инструментов (FIGI для Сбера)
        try:
            instruments = await sandbox.instruments.find_instrument(query="Сбер")
            print(f"🔍 Найдено инструментов: {len(instruments.instruments)}")
            for inst in instruments.instruments[:3]:
                print(f"  {inst.name} → FIGI: {inst.figi}")
        except RequestError as e:
            print(f"❌ Ошибка поиска инструментов: {e}")


if __name__ == "__main__":
    asyncio.run(main())
