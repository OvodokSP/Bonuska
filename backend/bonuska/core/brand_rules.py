import re
from decimal import Decimal

IGNORED_BRAND = "Не учитывается"

SERVER_HINTS = [
    "арм",
    "рабочая станция",
    "видеосервер",
    "сервер",
]

BRANDS = [
    "LTV", "LPA", "LPA-IP", "ЛПТ", "ЛКД", "Аргус исп.Л", "LUIS+", "LTC", "DKC",
    "ITK", "Световые технологии", "Schneider Electric", "ЦМО", "OSTEC", "Ардатов", "EKF",
    "LEDeffect", "IEK",
]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def detect_brand(name: str) -> str:
    n = normalize(name)

    # Специальное правило: Аргус исп.Л
    if re.search(r"исп\.\s*л", n, flags=re.IGNORECASE):
        return "Аргус исп.Л"

    # Специальное правило: LTV-сервер
    if "ltv" in n and any(hint in n for hint in SERVER_HINTS):
        return "LTV-сервер"

    # Обычные правила
    for brand in BRANDS:
        if brand == "Аргус исп.Л":
            continue
        if normalize(brand) in n:
            return brand

    return IGNORED_BRAND
