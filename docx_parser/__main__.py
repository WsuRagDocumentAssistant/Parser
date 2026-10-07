import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from . import DocxParser, DocxParseError


def main(argv=None):
    cli = argparse.ArgumentParser(description="DOCX structure parser without Word. Saved pages are estimates.")
    cli.add_argument("input", type=Path)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--profile", type=Path, help="JSON: style_levels, heading_scopes, auto_headings")
    cli.add_argument("--no-auto-headings", action="store_true")
    cli.add_argument("--page-mode", choices=("auto", "layout", "structural"), default="auto")
    cli.add_argument("--require-pages", action="store_true")
    cli.add_argument("--require-verified-pages", action="store_true")
    args = cli.parse_args(argv)
    try:
        config = {}
        if args.profile:
            config = json.loads(args.profile.read_text(encoding="utf-8-sig"))
            if not isinstance(config, dict) or set(config) - {"style_levels", "heading_scopes", "auto_headings"}:
                raise ValueError("Unknown/invalid profile fields")
        if args.no_auto_headings:
            config["auto_headings"] = False
        document = DocxParser(page_mode=args.page_mode, **config).parse(args.input)
        output = args.output or Path("output") / document.metadata["sha256"][:16]
        result = document.export(output)
        print(json.dumps({"output": str(result.resolve()), "status": document.status,
                          "structure_status": document.structure_status,
                          "counts": dict(Counter(b.kind for b in document.blocks)),
                          "headings": len(document.headings) - 1, "assets": len(document.assets),
                          "page_status": document.page_status,
                          "pages": dict(Counter(b.location.status for b in document.blocks)),
                          "estimated_page_count": document.metadata.get("estimated_page_count")},
                         ensure_ascii=False, indent=2))
        if args.require_verified_pages and document.page_status != "verified":
            print("Physical pages require independent rendering evidence; output was written.", file=sys.stderr)
            return 3
        if args.require_pages and document.page_status == "incomplete":
            print("Some physical pages are unknown; output was written.", file=sys.stderr)
            return 3
        return 0
    except (DocxParseError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
