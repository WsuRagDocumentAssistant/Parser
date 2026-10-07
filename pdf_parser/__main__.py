import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from . import PdfParser, PdfParseError, PaddleOcr


def main(argv=None, *, ocr=None):
    cli = argparse.ArgumentParser(description="PDF structure parser with optional PP-OCRv5 server CPU fallback.")
    cli.add_argument("input", type=Path)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--password-file", type=Path, help="UTF-8 password file; strips one trailing newline")
    cli.add_argument("--no-auto-headings", action="store_true")
    cli.add_argument("--table-strategy", choices=("lines", "text", "none"), default="lines")
    cli.add_argument("--ocr", choices=("none", "paddle"), default="none")
    cli.add_argument("--ocr-model", choices=("server", "korean"), default="server",
                     help="server det+rec, or server det+Korean mobile rec")
    cli.add_argument("--ocr-dpi", type=int, default=200)
    cli.add_argument("--ocr-min-confidence", type=float, default=0.5)
    cli.add_argument("--ocr-det-limit-side-len", type=int, default=960)
    cli.add_argument("--require-pages", action="store_true")
    cli.add_argument("--require-verified-pages", action="store_true")
    args = cli.parse_args(argv)
    try:
        password = None
        if args.password_file:
            password = args.password_file.read_text(encoding="utf-8-sig").removesuffix("\n")
        backend = None
        if args.ocr == "paddle":
            backend = ocr if ocr is not None else PaddleOcr(dpi=args.ocr_dpi, min_confidence=args.ocr_min_confidence,
                                                          model=args.ocr_model, det_limit_side_len=args.ocr_det_limit_side_len)
        document = PdfParser(auto_headings=not args.no_auto_headings,
                             table_strategy=args.table_strategy,
                             ocr=backend).parse(args.input, password=password)
        output = args.output or Path("output") / document.metadata["sha256"][:16]
        result = document.export(output)
        print(json.dumps({"output": str(result.resolve()), "status": document.status,
                          "structure_status": document.structure_status,
                          "counts": dict(Counter(b.kind for b in document.blocks)),
                          "headings": len(document.headings) - 1, "assets": len(document.assets),
                          "page_status": document.page_status,
                          "page_count": document.metadata["page_count"]}, ensure_ascii=False, indent=2))
        if (args.require_pages or args.require_verified_pages) and document.page_status != "verified":
            print("Some physical pages are unknown; output was written.", file=sys.stderr)
            return 3
        return 0
    except (PdfParseError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
