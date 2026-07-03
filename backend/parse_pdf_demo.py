import json
import sys
from pathlib import Path

from bonuska.parsers.pdf_invoice import parse_invoice, to_jsonable


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python parse_pdf_demo.py <invoice.pdf>")

    pdf_path = Path(sys.argv[1])
    result = parse_invoice(pdf_path)
    print(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
