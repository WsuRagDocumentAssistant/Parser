"""XLSX structure parsing without Excel or COM."""
from .models import Cell, Document, HeadingNode, ImageBlock, TableBlock, TextBlock
from .package import ParseLimits, XlsxParseError
from .parser import XlsxParser

__version__ = "0.1.0"
__all__ = ["XlsxParser", "XlsxParseError", "ParseLimits", "Document", "Cell",
           "HeadingNode", "ImageBlock", "TableBlock", "TextBlock"]
