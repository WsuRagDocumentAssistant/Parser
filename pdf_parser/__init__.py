"""PDF structure parsing without desktop applications or external services."""
from .models import Cell, Document, HeadingNode, ImageBlock, PageLocation, TableBlock, TextBlock
from .parser import PdfParser, PdfParseError, ParseLimits
from .ocr import PaddleOcr

__version__ = "0.1.0"
__all__ = ["PdfParser", "PdfParseError", "ParseLimits", "Document", "Cell", "PageLocation",
           "HeadingNode", "ImageBlock", "TableBlock", "TextBlock", "PaddleOcr"]
