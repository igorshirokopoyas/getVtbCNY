import os
import time
import logging
from datetime import datetime
import requests
from dotenv import load_dotenv

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

VTB_URL = "https://www.vtb.ru/api/currencyrates/table/optimized?category=10&type=1&handbook=0"
BOT_URL = os.environ["BOTHOST_RATES_URL"].rstrip("/")
SHARED_SECRET = os.environ["SHARED_SECRET"]
CHECK_INTERVAL = max(10, int(os.getenv("CHECK_INTERVAL", "30")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("vtb-poller")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.vtb.ru/",
    "Origin": "https://www.vtb.ru",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
})

def get_yuan_rates():
    response = session.get(VTB_URL, timeout=(15, 25))
    response.raise_for_status()
    data = response.json()
    for item in data.get("rates", []):
        currency1 = item.get("currency1") or {}
        currency2 = item.get("currency2") or {}
        tooltip = " ".join(str(item.get("tooltip", "")).split())
        if currency1.get("code") == "CNY" and currency2.get("code") == "RUB" and tooltip == "до 500000.00":
            bid, offer = float(item["bid"]), float(item["offer"])
            if bid <= 0 or offer <= 0:
                raise ValueError(f"Invalid bid/offer: {bid}/{offer}")
            return {"bid": bid, "offer": offer}
    raise ValueError("Не найден CNY/RUB с лимитом до 500000.00")

def transmit(rates):
    payload = {
        "bid": rates["bid"],
        "offer": rates["offer"],
        "updated_at": datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S"),
    }
    response = requests.post(
        BOT_URL,
        json=payload,
        headers={"X-Shared-Secret": SHARED_SECRET},
        timeout=(10, 20),
    )
    response.raise_for_status()
    return response.json()

def main():
    log.info("Опросчик ВТБ запущен; интервал %s сек.", CHECK_INTERVAL)
    while True:
        try:
            rates = get_yuan_rates()
            result = transmit(rates)
            log.info("bid=%s offer=%s; BotHost response=%s", rates["bid"], rates["offer"], result)
        except Exception:
            log.exception("Ошибка получения или передачи курса")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
