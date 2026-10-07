import re
from .models import HeadingNode, TableBlock, TextBlock


class HeadingBuilder:
    def __init__(self, style_levels=None, heading_scopes=("body",), auto_headings=None):
        if style_levels is not None and not isinstance(style_levels, dict):
            raise ValueError("style_levels must be an object mapping style IDs/names to levels")
        self.levels = dict(style_levels or {})
        if any(not isinstance(k, str) or type(v) is not int or not 1 <= v <= 100
               for k, v in self.levels.items()):
            raise ValueError("Style levels must be integers between 1 and 100")
        if isinstance(heading_scopes, str) or not heading_scopes or not set(heading_scopes) <= {"body", "cell", "textbox"}:
            raise ValueError("heading_scopes must contain body, cell and/or textbox")
        if auto_headings is not None and type(auto_headings) is not bool:
            raise ValueError("auto_headings must be boolean")
        self.scopes = set(heading_scopes)
        self.auto = style_levels is None if auto_headings is None else auto_headings

    def build(self, document):
        root = HeadingNode("root", "", 0, 0, None)
        document.headings = [root]
        body_stack, local_stacks, owners = [root], {}, {}
        for block in document.blocks:
            if isinstance(block, TableBlock):
                owners.update({cell.id: block for cell in block.cells})
            parent = owners.get(block.parent_id)
            if block.scope == "body":
                stack = body_stack
            elif block.scope not in {"cell", "textbox"}:
                stack = [root]
            else:
                # A cell/textbox heading cannot change the following body paragraph's owner.
                stack = local_stacks.setdefault(block.parent_id, [body_stack[-1]])
            block.heading_id = parent.heading_id if parent else stack[-1].id
            level, source, raw = None, "style_mapping", None
            evidence = {}
            if isinstance(block, TextBlock) and block.scope in self.scopes and block.text.strip():
                metadata = block.metadata
                key = next((k for k in (metadata.get("style_id"), metadata.get("style", {}).get("name"))
                            if k in self.levels), None)
                outline = metadata.get("outline_level")
                if key is not None:
                    level = self.levels[key]
                    evidence = {"style_key": key}
                elif outline is not None and outline < 9:
                    raw, level, source = outline, outline + 1, "outline"
                    evidence = {"outline_level": raw, "origin": metadata.get("outline_origin")}
                elif outline is None and self.auto:
                    name = metadata.get("style", {}).get("name") or metadata.get("style_id") or ""
                    match = re.fullmatch(r"(?:heading|제목|개요)\s*([1-9])", name, re.IGNORECASE)
                    if match:
                        level, source = int(match[1]), "auto_style"
                        evidence = {"classification": "heuristic", "style_name": name}
            if level is not None:
                while len(stack) > 1 and stack[-1].level >= level:
                    stack.pop()
                parent_heading = stack[-1]
                if parent_heading.level >= level:
                    parent_heading = root
                ident = "heading-" + block.id
                node = HeadingNode(ident, block.text, level, parent_heading.depth + 1,
                                   parent_heading.id, block.id, raw, source, evidence,
                                   path_ids=parent_heading.path_ids + [ident],
                                   path_titles=parent_heading.path_titles + [block.text])
                parent_heading.children.append(ident)
                document.headings.append(node)
                stack.append(node)
                block.heading_id = ident
            owners[block.id] = block
        document.metadata["heading_configuration"] = {
            "style_levels": self.levels, "heading_scopes": sorted(self.scopes), "auto_headings": self.auto,
        }
