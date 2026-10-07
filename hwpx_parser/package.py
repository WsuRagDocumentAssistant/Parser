"""Bounded, namespace-aware access to an untrusted HWPX ZIP package."""
from dataclasses import dataclass
from io import BytesIO
import posixpath
import time
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree as XML
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError


class HwpxParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParseLimits:
    max_file_bytes: int = 100 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_xml_bytes: int = 16 * 1024 * 1024
    max_entries: int = 10_000
    max_nodes: int = 300_000
    max_depth: int = 96
    max_blocks: int = 100_000
    max_compression_ratio: int = 1000
    timeout_seconds: float = 60

    def __post_init__(self):
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError("All parse limits must be positive")
        if self.max_depth > 128:
            raise ValueError("max_depth must be <= 128 for bounded XML traversal")


NAMESPACES = {
    f"http://www.hancom.co.kr/hwpml/{year}/{part}"
    for year in (2011, 2016)
    for part in ("head", "paragraph", "core", "section", "app", "master-page", "history")
} | {
    f"http://www.owpml.org/owpml/{year}/{part}"
    for year in (2021, 2023, 2024)
    for part in ("head", "paragraph", "core", "section", "app", "master-page", "history")
} | {
    "http://www.idpf.org/2007/opf/", "http://www.idpf.org/2007/opf",
    "http://purl.org/dc/elements/1.1/", "http://purl.org/dc/terms/",
    "urn:oasis:names:tc:opendocument:xmlns:container",
    "http://www.hancom.co.kr/schema/2011/hpf",
    "http://www.hancom.co.kr/hwpml/2011/version",
}


def tag(element) -> str:
    """Prefixes are irrelevant; unknown namespaces are never accepted by local name."""
    uri, separator, name = element.tag[1:].partition("}") if element.tag.startswith("{") else ("", "", element.tag)
    if separator and uri in NAMESPACES:
        return name
    return element.tag if separator else "{}" + element.tag


def child(element, name):
    return next((item for item in element if tag(item) == name), None)


def attrs(element) -> dict:
    return dict(element.attrib) if element is not None else {}


def record(element) -> dict:
    return {"tag": tag(element), "attributes": attrs(element),
            "text": element.text, "children": [record(item) for item in element]}


class Package:
    def __init__(self, data: bytes, limits: ParseLimits):
        self.limits = limits
        self.deadline = time.monotonic() + limits.timeout_seconds
        self.bytes_read = 0
        self.nodes = 0
        self.cache: dict[str, bytes] = {}
        self.xml_cache: dict = {}
        try:
            self.zip = ZipFile(BytesIO(data))
            self.entries = {}
            entries = self.zip.infolist()
            if len(entries) > limits.max_entries:
                raise HwpxParseError("ZIP entry count exceeds limit")
            total = 0
            for entry in entries:
                name = entry.filename
                if (name.startswith("/") or "\\" in name or ":" in name
                        or ".." in name.split("/") or "\x00" in name):
                    raise HwpxParseError(f"Unsafe ZIP path: {name!r}")
                if entry.is_dir():
                    continue
                if name in self.entries:
                    raise HwpxParseError(f"Duplicate ZIP member: {name}")
                if entry.flag_bits & 1:
                    raise HwpxParseError(f"Encrypted ZIP member: {name}")
                total += entry.file_size
                if entry.file_size / max(entry.compress_size, 1) > limits.max_compression_ratio:
                    raise HwpxParseError(f"Excessive compression ratio: {name}")
                self.entries[name] = entry
            if total > limits.max_total_bytes:
                raise HwpxParseError("Uncompressed package exceeds limit")
            if self.read("mimetype").strip() != b"application/hwp+zip":
                raise HwpxParseError("Not an HWPX package (mimetype must be application/hwp+zip)")
            if "META-INF/manifest.xml" in self.entries:
                encryption = self.xml("META-INF/manifest.xml")
                if any(e.tag.rsplit("}", 1)[-1] in {"EncryptedData", "encryption-data"} for e in encryption.iter()):
                    raise HwpxParseError("Encrypted HWPX documents are unsupported")
        except (BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
            if hasattr(self, "zip"):
                self.zip.close()
            raise HwpxParseError(f"Invalid ZIP package: {exc}") from exc
        except HwpxParseError:
            self.zip.close()
            raise

    def check(self):
        if time.monotonic() > self.deadline:
            raise HwpxParseError("Parse time limit exceeded")

    def read(self, name: str) -> bytes:
        self.check()
        if name in self.cache:
            return self.cache[name]
        if name not in self.entries:
            raise HwpxParseError(f"Missing package member: {name}")
        entry = self.entries[name]
        maximum = min(entry.file_size, self.limits.max_total_bytes - self.bytes_read)
        try:
            with self.zip.open(entry) as stream:
                data = stream.read(maximum + 1)
            if len(data) != entry.file_size or len(data) > maximum:
                raise HwpxParseError(f"Expanded data exceeds limit: {name}")
        except (BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
            raise HwpxParseError(f"Cannot read {name}: {exc}") from exc
        self.bytes_read += len(data)
        self.cache[name] = data
        self.check()
        return data

    def xml(self, name: str):
        if name in self.xml_cache:
            return self.xml_cache[name]
        if name not in self.entries:
            raise HwpxParseError(f"Missing XML member: {name}")
        if self.entries[name].file_size > self.limits.max_xml_bytes:
            raise HwpxParseError(f"XML size exceeds limit: {name}")
        try:
            root = XML.fromstring(self.read(name), forbid_dtd=True, forbid_entities=True, forbid_external=True)
        except (DefusedXmlException, ParseError, ValueError) as exc:
            raise HwpxParseError(f"Invalid or unsafe XML in {name}: {exc}") from exc
        stack = [(root, 1)]
        while stack:
            item, depth = stack.pop()
            self.nodes += 1
            if depth > self.limits.max_depth or self.nodes > self.limits.max_nodes:
                raise HwpxParseError(f"XML depth/node limit exceeded: {name}")
            stack.extend((c, depth + 1) for c in item)
        self.xml_cache[name] = root
        self.check()
        return root

    def resolve(self, href: str, origin: str, must_exist: bool = True) -> str:
        """Hancom root-relative paths and ordinary OPF relative paths are both supported."""
        parts = urlsplit(href)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:
            raise HwpxParseError(f"Non-package reference: {href}")
        decoded = unquote(parts.path)
        if not decoded or decoded.startswith("/") or "\\" in decoded or ":" in decoded or "\x00" in decoded:
            raise HwpxParseError(f"Unsafe package reference: {href}")
        candidates = list(dict.fromkeys([
            posixpath.normpath(decoded),
            posixpath.normpath(posixpath.join(posixpath.dirname(origin), decoded)),
        ]))
        safe = [p for p in candidates if p != ".." and not p.startswith("../")]
        existing = [p for p in safe if p in self.entries]
        if len(existing) > 1:
            raise HwpxParseError(f"Ambiguous package reference: {href}")
        if existing:
            return existing[0]
        if not safe:
            raise HwpxParseError(f"Package path escapes root: {href}")
        if must_exist:
            raise HwpxParseError(f"Unresolved package reference: {href} from {origin}")
        return safe[-1]
