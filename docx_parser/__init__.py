"""DOCX parsing without Word, COM or an office renderer."""
from .models import Document, HeadingNode, ImageBlock, PageLocation, TableBlock, TextBlock
from .parser import DocxParser
from .package import DocxParseError, ParseLimits

__version__ = "0.1.0"
__all__ = ["DocxParser", "DocxParseError", "ParseLimits", "Document", "HeadingNode",
           "ImageBlock", "PageLocation", "TableBlock", "TextBlock"]
