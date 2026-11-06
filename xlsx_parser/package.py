"""Bounded OPC ZIP/XML access. Relationships are relative to their source part."""
from dataclasses import dataclass
from io import BytesIO
import posixpath
import time
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree as XML
from defusedxml.common import DefusedXmlException


class XlsxParseError(ValueError):
    pass


# Total namespace-scope entries an XML part may create (declarations x nesting copies).
MAX_NAMESPACE_ENTRIES = 200_000


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
            raise ValueError("max_depth must be <= 128")


NS = {
    "s": ("http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "http://purl.oclc.org/ooxml/spreadsheetml/main"),
    "r": ("http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "http://purl.oclc.org/ooxml/officeDocument/relationships"),
    "a": ("http://schemas.openxmlformats.org/drawingml/2006/main",
          "http://purl.oclc.org/ooxml/drawingml/main"),
    "xdr": ("http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
            "http://purl.oclc.org/ooxml/drawingml/spreadsheetDrawing"),
    "rel": ("http://schemas.openxmlformats.org/package/2006/relationships",),
    "ct": ("http://schemas.openxmlformats.org/package/2006/content-types",),
    "mc": ("http://schemas.openxmlformats.org/markup-compatibility/2006",),
}
URI_PREFIX = {uri: prefix for prefix, uris in NS.items() for uri in uris}
REL_BASES = NS["r"]


def tag(element) -> str:
    uri, sep, name = element.tag[1:].partition("}") if element.tag.startswith("{") else ("", "", element.tag)
    return URI_PREFIX[uri] + ":" + name if sep and uri in URI_PREFIX else element.tag


def child(element, name):
    return next((item for item in element if tag(item) == name), None) if element is not None else None


def attr(element, name="id", prefix="r", default=None):
    if element is not None:
        for uri in NS[prefix]:
            value = element.get("{" + uri + "}" + name)
            if value is not None:
                return value
    return default


def record(element):
    return {"tag": tag(element), "attributes": dict(element.attrib), "text": element.text,
            "children": [record(item) for item in element]} if element is not None else None


class Package:
    def __init__(self, data: bytes, limits: ParseLimits):
        self.limits = limits
        self.deadline = time.monotonic() + limits.timeout_seconds
        self.bytes_read = self.nodes = 0
        self.cache, self.xml_cache, self.rel_cache = {}, {}, {}
        self.namespace_maps = {}
        try:
            self.zip = ZipFile(BytesIO(data))
            self.entries = {}
            entries = self.zip.infolist()
            if len(entries) > limits.max_entries:
                raise XlsxParseError("ZIP entry count exceeds limit")
            total = 0
            for entry in entries:
                name = entry.filename
                if (name.startswith("/") or "\\" in name or ":" in name
                        or ".." in name.split("/") or "\x00" in name):
                    raise XlsxParseError(f"Unsafe ZIP path: {name!r}")
                if entry.is_dir():
                    continue
                if name in self.entries:
                    raise XlsxParseError(f"Duplicate ZIP member: {name}")
                if entry.flag_bits & 1:
                    raise XlsxParseError(f"Encrypted ZIP member: {name}")
                total += entry.file_size
                if entry.file_size / max(entry.compress_size, 1) > limits.max_compression_ratio:
                    raise XlsxParseError(f"Excessive compression ratio: {name}")
                self.entries[name] = entry
            if total > limits.max_total_bytes:
                raise XlsxParseError("Uncompressed package exceeds limit")
            types = self.xml("[Content_Types].xml")
            if tag(types) != "ct:Types":
                raise XlsxParseError("Unsupported content types namespace/root")
            self.defaults, self.overrides = {}, {}
            for entry in types:
                if tag(entry) == "ct:Default":
                    key = entry.get("Extension", "").lower()
                    target = self.defaults
                elif tag(entry) == "ct:Override":
                    key = self.resolve(entry.get("PartName", ""), "")
                    target = self.overrides
                else:
                    continue
                if not key or key in target or not entry.get("ContentType"):
                    raise XlsxParseError("Missing/duplicate content type declaration")
                target[key] = entry.get("ContentType")
        except (BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
            if hasattr(self, "zip"):
                self.zip.close()
            raise XlsxParseError(f"Invalid ZIP package (encrypted/binary XLS unsupported): {exc}") from exc
        except XlsxParseError:
            self.zip.close()
            raise

    def check(self):
        if time.monotonic() > self.deadline:
            raise XlsxParseError("Parse time limit exceeded")

    def read(self, name):
        self.check()
        if name in self.cache:
            return self.cache[name]
        if name not in self.entries:
            raise XlsxParseError(f"Missing package member: {name}")
        entry = self.entries[name]
        maximum = min(entry.file_size, self.limits.max_total_bytes - self.bytes_read)
        try:
            with self.zip.open(entry) as stream:
                data = stream.read(maximum + 1)
            if len(data) != entry.file_size or len(data) > maximum:
                raise XlsxParseError(f"Expanded data exceeds limit: {name}")
        except (BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
            raise XlsxParseError(f"Cannot read {name}: {exc}") from exc
        self.bytes_read += len(data)
        self.cache[name] = data
        self.check()
        return data

    def xml(self, name):
        if name in self.xml_cache:
            return self.xml_cache[name]
        if name not in self.entries or self.entries[name].file_size > self.limits.max_xml_bytes:
            raise XlsxParseError(f"XML missing or exceeds size limit: {name}")
        try:
            depth = 0
            # Enforce depth/node limits during parsing, before building recursive records.
            namespaces, pending, stack, copied = {}, [], [], 0
            parser = XML.iterparse(BytesIO(self.read(name)), events=("start", "end", "start-ns"),
                                   forbid_dtd=True, forbid_entities=True, forbid_external=True)
            for event, item in parser:
                if event == "start-ns":
                    pending.append(item)
                elif event == "start":
                    depth += 1
                    self.nodes += 1
                    if depth > self.limits.max_depth or self.nodes > self.limits.max_nodes:
                        raise XlsxParseError(f"XML depth/node limit exceeded: {name}")
                    # Share the parent scope unless this element declares namespaces. Copying the whole
                    # scope at every element (and keeping each mc:Choice copy) multiplied memory by the
                    # number of declarations; copied entries are now counted against a budget.
                    scope = stack[-1] if stack else {}
                    if pending:
                        scope = {**scope, **dict(pending)}
                        copied += len(scope)
                        if copied > MAX_NAMESPACE_ENTRIES:
                            raise XlsxParseError(f"XML namespace declaration limit exceeded: {name}")
                        pending.clear()
                    stack.append(scope)
                    if tag(item) == "mc:Choice":
                        namespaces[id(item)] = scope
                else:
                    depth -= 1
                    stack.pop()
                if self.nodes % 1024 == 0:
                    self.check()
            root = parser.root
        except (DefusedXmlException, ParseError, ValueError) as exc:
            raise XlsxParseError(f"Invalid or unsafe XML in {name}: {exc}") from exc
        self.xml_cache[name] = root
        self.namespace_maps.update(namespaces)
        self.check()
        return root

    def resolve(self, href, origin):
        parts = urlsplit(href)
        decoded = unquote(parts.path)
        if (parts.scheme or parts.netloc or parts.query or parts.fragment or not decoded
                or "\\" in decoded or ":" in decoded or "\x00" in decoded):
            raise XlsxParseError(f"Unsafe/non-package reference: {href!r}")
        path = posixpath.normpath(decoded.lstrip("/") if decoded.startswith("/")
                                 else posixpath.join(posixpath.dirname(origin), decoded))
        if path in {".", ".."} or path.startswith("../"):
            raise XlsxParseError(f"Package reference escapes root: {href}")
        return path

    def relationships(self, origin):
        if origin in self.rel_cache:
            return self.rel_cache[origin]
        path = posixpath.join(posixpath.dirname(origin), "_rels", posixpath.basename(origin) + ".rels") if origin else "_rels/.rels"
        result = {}
        if path in self.entries:
            root = self.xml(path)
            if tag(root) != "rel:Relationships":
                raise XlsxParseError(f"Unsupported relationships root: {path}")
            for entry in root:
                if tag(entry) != "rel:Relationship":
                    continue
                ident, target, kind = entry.get("Id"), entry.get("Target"), entry.get("Type")
                if not ident or not target or not kind or ident in result:
                    raise XlsxParseError(f"Missing/duplicate relationship: {path}")
                mode = entry.get("TargetMode", "Internal")
                if mode not in {"Internal", "External"}:
                    raise XlsxParseError(f"Invalid relationship TargetMode: {mode}")
                external = mode == "External"
                result[ident] = {"id": ident, "type": kind, "target": target, "external": external,
                                 "path": None if external else self.resolve(target, origin)}
        self.rel_cache[origin] = result
        return result

    def content_type(self, name):
        return self.overrides.get(name, self.defaults.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream"))

    @staticmethod
    def is_relation(relation, name):
        return relation is not None and relation["type"] in {base + "/" + name for base in REL_BASES}

    def alternate(self, element):
        for choice in element:
            if tag(choice) == "mc:Choice":
                scope = self.namespace_maps.get(id(choice), {})
                requires = choice.get("Requires", "").split()
                if requires and all(scope.get(prefix) in URI_PREFIX for prefix in requires):
                    return choice
        return child(element, "mc:Fallback")
