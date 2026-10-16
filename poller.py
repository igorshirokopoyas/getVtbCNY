import os
import time
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests
from dotenv import load_dotenv

# Use the operating system's trusted certificate store when available.
# TLS certificate verification remains enabled.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

VTB_URL = (
    "https://www.vtb.ru/api/currencyrates/table/optimized"
    "?category=10&type=1&handbook=0"
)
BOT_URL = os.getenv("BOTHOST_RATES_URL", "").strip().rstrip("/")
SHARED_SECRET = os.getenv("SHARED_SECRET", "").strip()
CHECK_INTERVAL = max(10, int(os.getenv("CHECK_INTERVAL", "30")))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger("vtb-poller")

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.vtb.ru/",
    "Origin": "https://www.vtb.ru",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
})


def validate_config():
    missing = []
    if not BOT_URL:
        missing.append("BOTHOST_RATES_URL")
    if not SHARED_SECRET:
        missing.append("SHARED_SECRET")
    if missing:
        raise RuntimeError(
            "Не заданы обязательные переменные окружения: " + ", ".join(missing)
        )


def get_yuan_rates():
    # requests verifies TLS certificates by default.
    response = session.get(VTB_URL, timeout=(15, 25))
    response.raise_for_status()
    data = response.json()

    for item in data.get("rates", []):
        currency1 = item.get("currency1") or {}
        currency2 = item.get("currency2") or {}
        tooltip = " ".join(str(item.get("tooltip", "")).split())

        if (
            currency1.get("code") == "CNY"
            and currency2.get("code") == "RUB"
            and tooltip == "до 500000.00"
        ):
            try:
                bid = Decimal(str(item["bid"]))
                offer = Decimal(str(item["offer"]))
            except (KeyError, InvalidOperation) as exc:
                raise ValueError(
                    f"В ответе ВТБ отсутствуют корректные bid/offer: {item}"
                ) from exc

            if (
                not bid.is_finite()
                or not offer.is_finite()
                or bid <= 0
                or offer <= 0
            ):
                raise ValueError(f"Некорректный bid/offer: {bid}/{offer}")

            return {"bid": float(bid), "offer": float(offer)}

    raise ValueError("Не найден CNY/RUB с лимитом до 500000.00")


def transmit(rates):
    payload = {
        "bid": rates["bid"],
        "offer": rates["offer"],
        "updated_at": datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S"),
    }

    # Must point to the EUROPE project's /api/rates endpoint.
    response = requests.post(
        BOT_URL,
        json=payload,
        headers={"X-Shared-Secret": SHARED_SECRET},
        timeout=(10, 20),
    )
    response.raise_for_status()

    try:
        return response.json()
    except ValueError:
        return {"http_status": response.status_code, "body": response.text[:300]}


def main():
    validate_config()
    log.info("Опросчик ВТБ запущен; интервал %s сек.", CHECK_INTERVAL)
    log.info("Адрес передачи курса: %s", BOT_URL)

    while True:
        try:
            rates = get_yuan_rates()
            result = transmit(rates)
            log.info(
                "Курс получен и передан: bid=%.4f offer=%.4f; ответ BotHost=%s",
                rates["bid"], rates["offer"], result,
            )
        except requests.exceptions.SSLError:
            log.exception(
                "Ошибка SSL-сертификата ВТБ. Проверьте системные корневые "
                "сертификаты/CA bundle на сервере. Не отключайте TLS-проверку."
            )
        except requests.exceptions.RequestException:
            log.exception("Сетевая ошибка при получении или передаче курса")
        except Exception:
            log.exception("Ошибка получения или передачи курса")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
