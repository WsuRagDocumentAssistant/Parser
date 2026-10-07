import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from . import HwpxParser, HwpxParseError


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Pure Python HWPX parser (no Hangul/COM). Pages are estimates, never verified.")
    cli.add_argument("input", type=Path)
    cli.add_argument("--output", type=Path, help="Output directory for document.json and images/")
    cli.add_argument("--profile", type=Path, help="JSON heading configuration; applied only when explicitly selected")
    cli.add_argument("--no-auto-headings", action="store_true", help="Disable heading style heuristics")
    cli.add_argument("--page-mode", choices=("auto", "layout", "structural"), default="auto")
    cli.add_argument("--require-pages", action="store_true", help="Exit 3 if any block has unknown pages (still writes partial output)")
    cli.add_argument("--require-verified-pages", action="store_true", help="Exit 3 unless all pages are verified; XML-only mode cannot satisfy this")
    args = cli.parse_args(argv)
    try:
        config = {}
        if args.profile:
            config = json.loads(args.profile.read_text(encoding="utf-8-sig"))
            allowed = {"style_levels", "outline_level_base", "heading_scopes", "style_scopes", "join_numbered_heading_cells", "auto_headings"}
            if not isinstance(config, dict) or set(config) - allowed:
                raise ValueError("Unknown/invalid profile fields")
        if args.no_auto_headings:
            config["auto_headings"] = False
        document = HwpxParser(page_mode=args.page_mode, **config).parse(args.input)
        output = args.output or Path("output") / document.metadata["sha256"][:16]
        result = document.export(output)
        summary = {"output": str(result.resolve()), "status": document.status,
                   "structure_status": document.structure_status,
                   "counts": dict(Counter(b.kind for b in document.blocks)),
                   "headings": len(document.headings) - 1, "assets": len(document.assets),
                   "pages": dict(Counter(b.location.status for b in document.blocks)),
                   "page_status": document.page_status,
                   "estimated_page_count": document.metadata.get("estimated_page_count"),
                   "page_coverage": dict(Counter(b.location.evidence.get("coverage", "unspecified")
                                                 for b in document.blocks)),
                   "heading_sources": dict(Counter(h.source for h in document.iter_headings()))}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.require_verified_pages and document.page_status != "verified":
            print("Verified physical pages require independent pagination evidence; XML-only output is not verified.", file=sys.stderr)
            return 3
        if args.require_pages and document.page_status == "incomplete":
            print("Some physical pages are unknown; partial output was written.", file=sys.stderr)
            return 3
        return 0
    except (HwpxParseError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
