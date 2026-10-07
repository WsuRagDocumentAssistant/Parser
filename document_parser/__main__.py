import argparse
import json
from pathlib import Path
import sys

from . import parse


def main(argv=None):
    cli = argparse.ArgumentParser(description="Parse HWPX/DOCX/XLSX/PDF into WSU DocumentModel (UTF-8)")
    cli.add_argument("input", type=Path)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--password-file", type=Path)
    cli.add_argument("--ocr", choices=("none", "paddle"), default="none")
    cli.add_argument("--ocr-model", choices=("server", "korean"), default="server")
    cli.add_argument("--ocr-dpi", type=int, default=200)
    cli.add_argument("--ocr-min-confidence", type=float, default=0.5)
    cli.add_argument("--ocr-det-limit-side-len", type=int, default=960)
    args = cli.parse_args(argv)
    try:
        options = {}
        if args.ocr == "paddle":
            if args.input.suffix.lower() != ".pdf":
                raise ValueError("OCR is only supported for PDF")
            from pdf_parser import PaddleOcr
            options["pdf"] = {"ocr": PaddleOcr(model=args.ocr_model, dpi=args.ocr_dpi,
                                               min_confidence=args.ocr_min_confidence,
                                               det_limit_side_len=args.ocr_det_limit_side_len)}
        password = args.password_file.read_text(encoding="utf-8-sig").removesuffix("\n") if args.password_file else None
        model = parse(args.input, password=password, **options)
        root = args.output or Path("output") / model.structure.metadata["sha256"]
        result = model.export(root)
        print(json.dumps({"output": str(result.resolve()), "blocks": len(model.blocks),
                          "status": model.structure.status, "page_status": model.structure.page_status},
                         ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, ImportError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
