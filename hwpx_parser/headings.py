from collections.abc import Sequence
import re

from .models import Document, HeadingNode, TableBlock, TextBlock
from .style_clusters import _build_style_clusters, _cluster_key_of


ROMAN_STYLE = re.compile(r"(?:[IVXLCDM]+|[Ⅰ-Ⅻ]+)[.)]?")
NUMBERED_STYLE = re.compile(r"(\d+(?:[.-]\d+)*)(?:[.)])?(?:\s+제목(?:\s+\d+)?)?")
NAMED_STYLE = re.compile(r"(heading|제목|outline|개요)\s*(\d*)", re.IGNORECASE)
NUMBER_ONLY = re.compile(r"\s*\d+(?:[-.]\d+)*[.)]?\s*")


def compact_title(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def is_title_text(text: str) -> bool:
    """Guard inferred titles, without truncating the original body text."""
    text = compact_title(text)
    return bool(text and len(text) <= 120 and not re.search(r'[.!?。！？]$', text))


class HeadingBuilder:
    def __init__(
        self, style_levels: dict[str, int], outline_level_base: int,
        heading_scopes: Sequence[str] = ("body",),
        style_scopes: dict[str, list[str]] | None = None,
        join_numbered_cells: bool = False, *, auto_detect: bool = False,
    ) -> None:
        if outline_level_base not in (0, 1):
            raise ValueError("outline_level_base must be 0 or 1")
        if any(not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 100 for v in style_levels.values()):
            raise ValueError("Style levels must be integers between 1 and 100")
        self.style_levels = style_levels
        self.base = outline_level_base
        if not heading_scopes or not set(heading_scopes) <= {"body", "cell", "textbox"}:
            raise ValueError("heading_scopes must contain body, cell and/or textbox")
        self.scopes = set(heading_scopes)
        self.style_scopes = style_scopes or {}
        for scopes in self.style_scopes.values():
            if not isinstance(scopes, list) or not set(scopes) <= {"body", "cell", "textbox"}:
                raise ValueError("style_scopes values must be lists of body, cell and/or textbox")
        self.join_numbered_cells = join_numbered_cells
        self.auto_detect = auto_detect

    @staticmethod
    def _automatic_styles(document: Document) -> dict[str, tuple[str, str, int]]:
        """Recognize conventional style names, never arbitrary numbered body text."""
        styles = {
            block.metadata.get("style_id"): block.metadata.get("style", {})
            for block in document.blocks
            if isinstance(block, TextBlock) and block.text.strip()
            and block.scope in {"body", "cell", "textbox"}
        }
        has_roman = any(
            ROMAN_STYLE.fullmatch(style.get("name", "").strip())
            for style in styles.values()
        )
        inferred = {}
        for ident, style in styles.items():
            for name in (style.get("name", ""), style.get("engName", "")):
                name = name.strip()
                if ROMAN_STYLE.fullmatch(name):
                    inferred[ident] = (name, "numbered_style", 1)
                elif match := NUMBERED_STYLE.fullmatch(name):
                    level = len(re.split(r"[.-]", match[1])) + int(has_roman)
                    inferred[ident] = (name, "numbered_style", level)
                elif match := NAMED_STYLE.fullmatch(name):
                    level = int(match[2] or 1)
                    rule = "outline_style" if match[1].lower() in {"outline", "개요"} else "named_style"
                    inferred[ident] = (name, rule, level)
                else:
                    continue
                if not 1 <= inferred[ident][2] <= 100:
                    del inferred[ident]
                break
        return inferred

    @staticmethod
    def _style_headings(document: Document) -> dict[str, dict]:
        """Use the organization's body/style baseline, not style names alone."""
        def find(record, name):
            if record.get('tag') == name:
                return record.get('attributes', {})
            for child in record.get('children', []):
                if result := find(child, name):
                    return result
            return {}

        paragraphs = document.metadata.get('paragraph_properties', {})
        characters = document.metadata.get('character_properties', {})
        records = []
        for block in document.blocks:
            if block.scope != 'body':
                continue
            if not isinstance(block, TextBlock):
                records.append({'block_type': block.kind})
                continue
            para = paragraphs.get(block.metadata.get('paragraph_property_id'), {})
            total = bold = 0
            sizes = []
            for run in block.runs:
                length = max(0, run['end'] - run['start'])
                char = characters.get(run.get('char_property_id'), {})
                total += length
                if any(c.get('tag') == 'bold' for c in char.get('children', [])):
                    bold += length
                height = char.get('attributes', {}).get('height')
                try:
                    if length and height is not None:
                        sizes.append(int(height) / 100)
                except (TypeError, ValueError):
                    pass
            sf = dict(style_id_ref=block.metadata.get('style_id'),
                      para_pr_id_ref=block.metadata.get('paragraph_property_id'),
                      font_size=max(sizes, default=None), bold_ratio=bold / total if total else 0,
                      alignment=find(para, 'align').get('horizontal'))
            for key, tag in [('indent', 'intent'), ('margin_left', 'left')]:
                try:
                    sf[key] = int(find(para, tag).get('value'))
                except (TypeError, ValueError):
                    sf[key] = None
            records.append(dict(block_type='paragraph', text_content=block.text,
                                style_features=sf, block_id=block.id))
        stats = _build_style_clusters(records)
        document.metadata['heading_style_clusters'] = stats['report']
        candidates = {}
        for record in records:
            if record['block_type'] != 'paragraph':
                continue
            sf = record['style_features']
            info = stats['cluster_info_of'].get(_cluster_key_of(sf), {})
            if info.get('cluster_role_candidate') == 'section_heading' and is_title_text(record['text_content']):
                candidates[record['block_id']] = dict(info, font_size=sf['font_size'], bold_ratio=sf['bold_ratio'])
        return candidates

    def build(self, document: Document) -> None:
        root = HeadingNode("root", "", 0, 0, None)
        document.headings = [root]
        stack = [root]
        pending = None
        automatic = self._automatic_styles(document) if self.auto_detect else {}
        style_headings = self._style_headings(document) if self.auto_detect else {}
        numbered_level = 0
        cells = {
            cell.id: (block.id, cell.row, cell.column, cell.col_span)
            for block in document.blocks if isinstance(block, TableBlock)
            for cell in block.cells
        }
        cell_texts = {block.parent_id for block in document.blocks
                      if isinstance(block, TextBlock) and block.text.strip()}
        empty_cells = cells.keys() - cell_texts
        join_cells = self.join_numbered_cells or self.auto_detect
        for block in document.blocks:
            level = None
            raw_level = None
            source = "style_mapping"
            evidence = {}
            if isinstance(block, TextBlock) and (
                block.scope in self.scopes
                or self.auto_detect and block.scope in {"body", "cell", "textbox"}
            ):
                style = block.metadata.get("style", {})
                candidates = [block.metadata.get("style_id"), style.get("name"), style.get("engName")]
                key = next((c for c in candidates if c in self.style_levels), None)
                mapped = self.style_levels.get(key) if block.scope in self.scopes else None
                if key in self.style_scopes and block.scope not in self.style_scopes[key]:
                    mapped = None
                heading = block.metadata.get("heading", {})
                if heading.get("type") == "OUTLINE" and block.scope in self.scopes:
                    try:
                        raw_level = int(heading["level"])
                        level = raw_level - self.base + 1
                        if not 1 <= level <= 100:
                            raise ValueError
                    except (KeyError, TypeError, ValueError):
                        level = None
                        document.diagnostics.append({"severity": "warning", "code": "invalid_heading_level",
                                                     "block_id": block.id, "details": heading})
                    source = "outline"
                    evidence = dict(heading, raw_level_base=self.base)
                    if mapped is not None and level is not None and mapped != level:
                        document.diagnostics.append({"severity": "warning", "code": "heading_style_conflict",
                                                     "block_id": block.id, "outline_level": level, "mapped_level": mapped})
                if level is None and mapped is not None:
                    level = mapped
                    source = "style_mapping"
                    evidence = {"style_id": block.metadata.get("style_id"), "style": style}
                if (level is None and key is None and self.auto_detect
                        and heading.get("type") not in {"NUMBER", "BULLET", "OUTLINE"}):
                    inferred = automatic.get(block.metadata.get("style_id"))
                    if inferred is not None and is_title_text(block.text):
                        name, rule, inferred_level = inferred
                        # ponytail: style conventions are heuristics; a semantic classifier
                        # is needed for unstyled titles and ambiguous list/heading styles.
                        looks_like_prose = (block.text.lstrip().startswith(("-", "·", "•"))
                                            or len(block.text.strip()) > 160)
                        if rule != "outline_style" or block.scope == "body" and not looks_like_prose:
                            level = inferred_level + (numbered_level if rule == "outline_style" else 0)
                            if level > 100:
                                level = None
                            source = "auto_style"
                            evidence = {"classification": "heuristic", "rule": rule,
                                        "style_name": name, "style_id": block.metadata.get("style_id")}
                if (level is None and key is None and self.auto_detect and block.scope == 'body'
                        and heading.get('type') not in {'NUMBER', 'BULLET', 'OUTLINE'}):
                    candidate = style_headings.get(block.id)
                    if candidate:
                        level = candidate.get('depth_rank_candidate') or 1
                        source = 'auto_style'
                        evidence = dict(candidate, classification='heuristic', rule='style_cluster')
                cell = cells.get(block.parent_id)
                if (pending is not None and cell is not None and block.parent_id in empty_cells
                        and pending[1][:2] == cell[:2]
                        and pending[1][2] + pending[1][3] == cell[2]):
                    pending = (pending[0], cell)
                adjacent = (pending is not None and cell is not None
                            and pending[1][:2] == cell[:2]
                            and pending[1][2] + pending[1][3] == cell[2])
                numeric = NUMBER_ONLY.fullmatch(block.text) is not None
                if (self.auto_detect and adjacent and level is None and key is None
                        and pending[0].source == "auto_style"
                        and heading.get("type") not in {"NUMBER", "BULLET", "OUTLINE"}
                        and block.text.strip() and not numeric):
                    level = pending[0].level
                if level is not None and not block.text.strip():
                    level = None
                if level is not None:
                    # Only adjacent number/title candidates in the same table row can join.
                    if (join_cells and adjacent and pending[0].level == level and not numeric):
                        node = pending[0]
                        node.display_number = node.title.strip()
                        node.title = node.title.strip() + " " + block.text.strip()
                        node.path_titles[-1] = node.title
                        node.evidence["source_block_ids"] = [node.block_id, block.id]
                        node.evidence["joined_numbered_cells"] = True
                        block.heading_id = node.id
                        pending = None
                        continue
                    pending = None
                    while stack[-1].level >= level:
                        stack.pop()
                    parent = stack[-1]
                    node = HeadingNode(
                        id="heading-" + block.id, title=block.text, level=level,
                        depth=parent.depth + 1, parent_id=parent.id, block_id=block.id,
                        raw_level=raw_level, source=source, evidence=evidence,
                        path_ids=parent.path_ids + ["heading-" + block.id],
                        path_titles=parent.path_titles + [block.text],
                    )
                    parent.children.append(node.id)
                    if level > parent.level + 1:
                        document.diagnostics.append({"severity": "info", "code": "heading_level_gap", "block_id": block.id})
                    # Numbering is retained as source metadata; do not invent visible numbers.
                    if source == "outline":
                        node.evidence["display_number_status"] = "unresolved"
                    stack.append(node)
                    document.headings.append(node)
                    if source == "auto_style" and evidence["rule"] == "numbered_style":
                        numbered_level = level
                    if join_cells and cell is not None and numeric:
                        pending = (node, cell)
                elif block.text.strip():
                    pending = None
            elif isinstance(block, TextBlock):
                pending = None
            block.heading_id = stack[-1].id
        if self.auto_detect:
            document.metadata["heading_configuration"] = "Explicit OUTLINE, style names and document style clustering"
            document.diagnostics.append({
                "severity": "info", "code": "automatic_headings",
                "count": sum(node.source == "auto_style" for node in document.headings),
                "message": "Style-based titles and levels are heuristic; unstyled titles may be missed.",
            })
        # Heading display text is normalized; extracted TextBlock.text stays lossless.
        for node in document.headings:
            node.title = compact_title(node.title)
            node.path_titles = [compact_title(text) for text in node.path_titles]
