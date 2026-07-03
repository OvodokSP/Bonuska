import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pdfplumber

from bonuska.core.brand_rules import detect_brand, IGNORED_BRAND

MONEY_RE = r"\d{1,3}(?: \d{3})*,\d{2}"
INVOICE_RE = re.compile(r"Сч[её]т-договор\s*№\s*([А-ЯA-Z0-9]+)\s*от\s*(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)
ITEM_START_RE = re.compile(rf"^(\d+)\s+(.+?)\s+({MONEY_RE})\s*$")


@dataclass
class InvoiceItem:
    row_no: int
    name: str
    brand: str
    amount: Decimal


@dataclass
class ParsedInvoice:
    invoice_number: str | None
    invoice_date: str | None
    items: list[InvoiceItem]
    totals_by_brand: dict[str, Decimal]


def parse_money(value: str) -> Decimal:
    return Decimal(value.replace(" ", "").replace(",", "."))


def extract_text(pdf_path: str | Path) -> str:
    chunks: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            chunks.append(text)
    return "\n".join(chunks)


def parse_invoice(pdf_path: str | Path) -> ParsedInvoice:
    text = extract_text(pdf_path)
    header = INVOICE_RE.search(text)
    invoice_number = header.group(1) if header else None
    invoice_date = header.group(2) if header else None

    items: list[InvoiceItem] = []
    current: dict[str, Any] | None = None
    last_row_no = 0

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if line.startswith("Итого") or line.startswith("в т.ч.") or line.startswith("*") or line.startswith("2. "):
            break

        m = ITEM_START_RE.match(line)
        if m:
            row_no = int(m.group(1))
            # Защита от строки итога вида "1 852 808,02", которая может
            # быть извлечена PDF-парсером перед подписью "Итого".
            if row_no <= last_row_no:
                break
            if current:
                items.append(_finalize_item(current))
            amount = parse_money(m.group(3))
            name_part = _strip_service_columns(m.group(2))
            current = {"row_no": row_no, "name_parts": [name_part], "amount": amount}
            last_row_no = row_no
            continue

        # Продолжение наименования текущей позиции.
        if current and not re.match(r"^\d+\.", line):
            current["name_parts"].append(line)

    if current:
        items.append(_finalize_item(current))

    totals: dict[str, Decimal] = defaultdict(Decimal)
    for item in items:
        if item.brand != IGNORED_BRAND:
            totals[item.brand] += item.amount

    return ParsedInvoice(invoice_number, invoice_date, items, dict(totals))


def _strip_service_columns(name_part: str) -> str:
    # Убираем хвостовые служебные колонки: срок, цена, количество.
    # Сумма строки уже извлечена как последний денежный показатель.
    pattern = rf"\s+(?:Х\s+)?\d+\s+р\.д\.\s+{MONEY_RE}\s+\d+(?:[,.]\d+)?\s+шт\.\s*$"
    return re.sub(pattern, "", name_part).strip()


def _finalize_item(raw: dict[str, Any]) -> InvoiceItem:
    name = " ".join(raw["name_parts"])
    name = re.sub(r"\s+", " ", name).strip()
    return InvoiceItem(
        row_no=raw["row_no"],
        name=name,
        brand=detect_brand(name),
        amount=raw["amount"],
    )


def to_jsonable(parsed: ParsedInvoice) -> dict[str, Any]:
    return {
        "invoice_number": parsed.invoice_number,
        "invoice_date": parsed.invoice_date,
        "items": [
            {**asdict(item), "amount": str(item.amount)}
            for item in parsed.items
        ],
        "totals_by_brand": {brand: str(amount) for brand, amount in parsed.totals_by_brand.items()},
    }
