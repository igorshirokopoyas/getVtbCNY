
import os
import time
import logging
from pathlib import Path
from datetime import datetime
from decimal import Decimal, InvalidOperation

# Важно: подключаем системное хранилище сертификатов ДО requests.
import truststore
truststore.inject_into_ssl()

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# =========================================================
# CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

VTB_API_URL = (
    "https://www.vtb.ru/api/currencyrates/table/optimized"
    "?category=10&type=1&handbook=0"
)

EUROPE_API_URL = os.getenv(
    "EUROPE_API_URL",
    "https://bot-1792135903-6908-franterpage.bothost.tech/api/rates",
).strip()

SHARED_SECRET = os.getenv("SHARED_SECRET", "").strip()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

# Тариф ВТБ: до 500 000 рублей.
TARGET_TOOLTIP = "до 500000.00"


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

log = logging.getLogger(__name__)


# =========================================================
# HTTP SESSION
# =========================================================

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

# Повторяем запросы при временных сетевых ошибках.
retry_strategy = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "POST"]),
    respect_retry_after_header=True,
)

adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)


# =========================================================
# FETCH VTB RATE
# =========================================================

def normalize_tooltip(value):
    """Нормализует пробелы в названии тарифа."""
    return " ".join(str(value or "").split()).casefold()


def get_yuan_rates():
    response = session.get(
        VTB_API_URL,
        timeout=(15, 25),
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError("ВТБ вернул ответ неожиданного формата")

    items = data.get("rates")

    if not isinstance(items, list):
        raise ValueError(
            "В ответе ВТБ отсутствует список rates"
        )

    for item in items:
        if not isinstance(item, dict):
            continue

        currency1 = item.get("currency1") or {}
        currency2 = item.get("currency2") or {}

        if not isinstance(currency1, dict):
            continue

        if not isinstance(currency2, dict):
            continue

        tooltip = normalize_tooltip(item.get("tooltip"))

        if (
            currency1.get("code") != "CNY"
            or currency2.get("code") != "RUB"
            or tooltip != TARGET_TOOLTIP
        ):
            continue

        try:
            bid = Decimal(str(item["bid"]))
            offer = Decimal(str(item["offer"]))
        except (KeyError, InvalidOperation, TypeError) as exc:
            raise ValueError(
                f"Некорректные значения bid/offer от ВТБ: {item}"
            ) from exc

        if (
            not bid.is_finite()
            or not offer.is_finite()
            or bid <= 0
            or offer <= 0
        ):
            raise ValueError(
                f"ВТБ вернул некорректный курс: bid={bid}, offer={offer}"
            )

        log.info(
            "Курс ВТБ получен: CNY/RUB, тариф до 500000 ₽, "
            "bid=%s, offer=%s",
            bid,
            offer,
        )

        return {
            "bid": bid,
            "offer": offer,
        }

    raise ValueError(
        "Не найден курс CNY/RUB для тарифа «до 500000.00». "
        "Возможно, ВТБ изменил формат ответа или название тарифа."
    )


# =========================================================
# SEND RATE TO EUROPE TELEGRAM BOT
# =========================================================

def send_rate_to_europe(rates):
    if not EUROPE_API_URL:
        raise RuntimeError("Не задан EUROPE_API_URL")

    if not SHARED_SECRET:
        raise RuntimeError(
            "Не задан SHARED_SECRET в переменных окружения проекта ВТБ РУ"
        )

    # Время фиксации полученного курса на стороне poller,
    # а не официальное время обновления курса в ВТБ.
    updated_at = datetime.now().astimezone().strftime(
        "%d.%m.%Y %H:%M:%S"
    )

    payload = {
        "bid": float(rates["bid"]),
        "offer": float(rates["offer"]),
        "updated_at": updated_at,
    }

    response = session.post(
        EUROPE_API_URL,
        json=payload,
        headers={
            "X-Shared-Secret": SHARED_SECRET,
        },
        timeout=(15, 25),
    )

    response.raise_for_status()

    log.info(
        "Курс отправлен в Telegram-проект: bid=%s, offer=%s, "
        "HTTP %s",
        rates["bid"],
        rates["offer"],
        response.status_code,
    )


# =========================================================
# MAIN LOOP
# =========================================================

def main():
    if CHECK_INTERVAL < 5:
        raise RuntimeError(
            "CHECK_INTERVAL не должен быть меньше 5 секунд"
        )

    if not SHARED_SECRET:
        raise RuntimeError(
            "Отсутствует SHARED_SECRET. "
            "Укажи тот же секрет, что и в проекте «ТГ для отправки ЕВРОПА»."
        )

    log.info("VTB poller запускается")
    log.info("Интервал проверки: %s секунд", CHECK_INTERVAL)
    log.info("Адрес приёмника: %s", EUROPE_API_URL)
    log.info("Проверка SSL включена; truststore активирован")

    while True:
        try:
            rates = get_yuan_rates()
            send_rate_to_europe(rates)

        except requests.exceptions.SSLError:
            log.exception(
                "Ошибка SSL при обращении к ВТБ или приёмнику. "
                "Проверь сертификаты в окружении BotHost."
            )

        except requests.exceptions.Timeout:
            log.exception("Истекло время ожидания HTTP-запроса")

        except requests.exceptions.RequestException:
            log.exception("Ошибка HTTP-запроса")

        except Exception:
            log.exception("Ошибка получения или передачи курса")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
