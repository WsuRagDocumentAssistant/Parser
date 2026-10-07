from __future__ import annotations

from importlib import import_module
from pathlib import Path

from .adapter import ModelAdapter
from .models import DocumentModel


class DocumentParser:
    """Dispatch by extension; options belong to the selected format parser."""

    formats = {
        ".hwpx": ("hwpx_parser", "HwpxParser"),
        ".docx": ("docx_parser", "DocxParser"),
        ".xlsx": ("xlsx_parser", "XlsxParser"),
        ".pdf": ("pdf_parser", "PdfParser"),
    }

    def __init__(self, **format_options):
        unknown = set(format_options) - {suffix[1:] for suffix in self.formats}
        if unknown:
            raise ValueError(f"Unsupported parser configurations: {sorted(unknown)}")
        self.format_options = format_options

    def parse(self, path: str | Path, *, image_dir: str | Path | None = None,
              password: str | None = None) -> DocumentModel:
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix not in self.formats:
            raise ValueError(f"Unsupported document extension: {suffix or '(none)'}")
        if password is not None and suffix != ".pdf":
            raise ValueError("password is only supported for PDF")
        module, name = self.formats[suffix]
        # PdfParser defaults to ocr=None; importing/using PaddleOCR is explicit opt-in.
        backend = getattr(import_module(module), name)(**self.format_options.get(suffix[1:], {}))
        document = backend.parse(source, password=password) if suffix == ".pdf" else backend.parse(source)
        document.metadata["format"] = suffix[1:]
        return ModelAdapter(document).build(image_dir=image_dir)


def parse(file_path: str | Path, *, image_dir: str | Path | None = None,
          password: str | None = None, **format_options) -> DocumentModel:
    return DocumentParser(**format_options).parse(file_path, image_dir=image_dir, password=password)
