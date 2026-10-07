import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from . import XlsxParser, XlsxParseError


def main(argv=None):
    cli = argparse.ArgumentParser(description="XLSX cells, formulas and structure without Excel.")
    cli.add_argument("input", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args(argv)
    try:
        document = XlsxParser().parse(args.input)
        output = args.output or Path("output") / document.metadata["sha256"][:16]
        result = document.export(output)
        print(json.dumps({"output": str(result.resolve()), "status": document.status,
                          "structure_status": document.structure_status,
                          "counts": dict(Counter(b.kind for b in document.blocks)),
                          "sheets": len(document.sections), "assets": len(document.assets),
                          "page_status": document.page_status}, ensure_ascii=False, indent=2))
        return 0
    except (XlsxParseError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
