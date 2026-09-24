# Bonuska v0.17.31 a03 — headerless continuation-page safeguard.
#
# This block is appended to backend/bonuska/parsers/pdf_invoice.py by the
# transition bootstrap. It deliberately keeps the existing structured parser
# as the primary parser. The fallback is accepted only when:
#   * it finds a strict 1..N sequence of item numbers,
#   * it is a strict superset of the primary result,
#   * every missing item has the normal "N р.д." service tail and two money
#     values (price + row amount),
#   * the reconstructed item sum equals "Итого, руб" to one kopeck.
#
# This prevents the historical "94 дБ / 0,75 Вт" false-row regression.

# === BONUSKA_V01731_A03_HEADERLESS_CONTINUATION_BEGIN ===

_BONUSKA_V01731_A03_ORIGINAL_PARSE_INVOICE = parse_invoice


def _bonuska_v01731_a03_money(value: str):
    from decimal import Decimal

    return Decimal(value.replace("\u00a0", " ").replace(" ", "").replace(",", "."))


def _bonuska_v01731_a03_strict_sequence(pdf_path):
    import re
    from decimal import Decimal

    import pdfplumber

    money_re = re.compile(
        r"(?<!\d)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+),\d{2}(?!\d)"
    )
    item_start_re = re.compile(r"^(\d{1,4})\s+(.+)$")
    service_tail_re = re.compile(r"\s+(?:Х\s+)?\d+\s+р\.д\.\s+", re.IGNORECASE)
    total_re = re.compile(
        r"^Итого,?\s*руб:?\s*((?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+),\d{2})",
        re.IGNORECASE,
    )

    lines = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.splitlines():
                clean = " ".join(raw.replace("\u00a0", " ").split()).strip()
                if clean:
                    lines.append(clean)

    expected = 1
    current = None
    records = []
    invoice_total = None

    def finish_current():
        nonlocal current
        if current is None:
            return True

        text = " ".join(current["parts"])
        service_match = service_tail_re.search(text)
        values = money_re.findall(text)

        # A genuine invoice row in this format has a service term followed by
        # at least price and row total. Reject the whole fallback if one row is
        # structurally ambiguous instead of guessing.
        if service_match is None or len(values) < 2:
            return False

        name = text[: service_match.start()].strip()
        if not name:
            return False

        records.append(
            {
                "row_no": current["row_no"],
                "name": name,
                "amount": _bonuska_v01731_a03_money(values[-1]),
            }
        )
        current = None
        return True

    for line in lines:
        total_match = total_re.match(line)
        if total_match:
            if not finish_current():
                return None
            invoice_total = _bonuska_v01731_a03_money(total_match.group(1))
            break

        start = item_start_re.match(line)
        if start and int(start.group(1)) == expected:
            if not finish_current():
                return None
            current = {
                "row_no": expected,
                "parts": [start.group(2)],
            }
            expected += 1
            continue

        if current is not None:
            current["parts"].append(line)

    if current is not None and not finish_current():
        return None

    if not records or invoice_total is None:
        return None

    row_numbers = [row["row_no"] for row in records]
    if row_numbers != list(range(1, len(records) + 1)):
        return None

    reconstructed_total = sum(
        (row["amount"] for row in records),
        Decimal("0.00"),
    )
    if abs(reconstructed_total - invoice_total) > Decimal("0.01"):
        return None

    return {
        "records": records,
        "invoice_total": invoice_total,
        "reconstructed_total": reconstructed_total,
    }


def _bonuska_v01731_a03_make_missing_item(item_cls, row, brand):
    # Keep this deliberately narrow. If the current InvoiceItem contract ever
    # changes incompatibly, abort the fallback instead of manufacturing data.
    try:
        return item_cls(
            row_no=row["row_no"],
            name=row["name"],
            brand=brand,
            amount=row["amount"],
        )
    except TypeError:
        try:
            return item_cls(
                row["row_no"],
                row["name"],
                brand,
                row["amount"],
            )
        except TypeError:
            return None


def parse_invoice(pdf_path):
    primary = _BONUSKA_V01731_A03_ORIGINAL_PARSE_INVOICE(pdf_path)

    try:
        fallback = _bonuska_v01731_a03_strict_sequence(pdf_path)
        if not fallback:
            return primary

        primary_items = list(getattr(primary, "items", None) or [])
        if not primary_items:
            return primary

        primary_by_row = {}
        for item in primary_items:
            try:
                primary_by_row[int(item.row_no)] = item
            except (AttributeError, TypeError, ValueError):
                return primary

        fallback_records = fallback["records"]
        fallback_rows = {row["row_no"] for row in fallback_records}
        primary_rows = set(primary_by_row)

        # The continuation fallback may only ADD strictly missing rows.
        if len(fallback_records) <= len(primary_items):
            return primary
        if not primary_rows.issubset(fallback_rows):
            return primary

        detector = globals().get("detect_brand")
        if not callable(detector):
            return primary

        item_cls = type(primary_items[0])
        merged_items = []

        for row in fallback_records:
            row_no = row["row_no"]
            existing = primary_by_row.get(row_no)
            if existing is not None:
                merged_items.append(existing)
                continue

            brand = detector(row["name"])
            new_item = _bonuska_v01731_a03_make_missing_item(item_cls, row, brand)
            if new_item is None:
                return primary
            merged_items.append(new_item)

        # A final independent money guard: do not replace the primary result
        # unless the actual merged item objects still add up to the invoice
        # total after preserving the structured parser's existing rows.
        from decimal import Decimal

        merged_total = sum(
            (getattr(item, "amount", Decimal("0.00")) for item in merged_items),
            Decimal("0.00"),
        )
        if abs(merged_total - fallback["invoice_total"]) > Decimal("0.01"):
            return primary

        totals = {}
        ignored_brand = globals().get("IGNORED_BRAND")
        for item in merged_items:
            brand = getattr(item, "brand", None)
            if not brand or brand == ignored_brand:
                continue
            totals[brand] = totals.get(brand, Decimal("0.00")) + getattr(
                item,
                "amount",
                Decimal("0.00"),
            )

        primary.items = merged_items
        primary.totals_by_brand = totals
        return primary
    except Exception:
        # The safeguard must never make a previously parseable invoice fail.
        # Existing parser behaviour remains the fail-safe path.
        return primary


# === BONUSKA_V01731_A03_HEADERLESS_CONTINUATION_END ===
