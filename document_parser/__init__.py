"""Unified parsers returning the organization's DocumentModel contract."""
from .models import DocumentModel
from .parser import DocumentParser, parse

__all__ = ["DocumentParser", "DocumentModel", "parse"]
