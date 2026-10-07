"""Machine-readable records and human-readable views of the same parsed document."""
from __future__ import annotations

from collections import defaultdict
import hashlib
from html import escape
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .models import atomic_write

if TYPE_CHECKING:
    from .models import Document


class DocumentExporter:
    def __init__(self, document: Document) -> None:
        self.document = document

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    @staticmethod
    def page_label(location: dict) -> str:
        pages = location.get("physical_pages")
        if not pages:
            return "페이지 미확정"
        numbers = ", ".join(map(str, pages))
        coverage = location.get("evidence", {}).get("coverage")
        if coverage == "candidate":
            return f"후보 페이지 {numbers} (실제 표시 페이지 미검증)"
        if location.get("status") == "verified":
            return f"확정 페이지 {numbers}"
        return f"추정 페이지 {numbers} (미검증)"

    @staticmethod
    def markdown(value: str) -> str:
        text = escape(value.replace("\r", " ").replace("\n", " "), quote=False)
        for character in ("\\", "`", "*", "_", "[", "]", "|", "#"):
            text = text.replace(character, "\\" + character)
        return text

    def export(self, directory: str | Path) -> Path:
        root = Path(directory).resolve()
        self.export_images(root)
        data = self.document.to_dict()
        blocks = {block["id"]: block for block in data["blocks"]}
        headings = {heading["id"]: heading for heading in data["headings"]}
        assets = {asset["id"]: asset for asset in data["assets"]}
        grouped = defaultdict(list)
        heading_blocks = defaultdict(list)
        for block in data["blocks"]:
            record = {**block, "heading_path": headings.get(block["heading_id"], {}).get("path_titles", [])}
            grouped[block["kind"]].append(record)
            heading_blocks[block["heading_id"]].append(block["id"])

        for table in grouped["table"]:
            table["cells"] = [
                {**cell, "text": "\n".join(
                    blocks[ident]["text"] for ident in cell["block_ids"]
                    if ident in blocks and blocks[ident]["kind"] == "text"
                )}
                for cell in table["cells"]
            ]
        for image in grouped["image"]:
            image["asset"] = assets.get(image["asset_id"])

        ordered_headings = []
        nodes = self.document.iter_headings() if headings else ()
        for node in nodes:
            heading = headings[node.id]
            source_blocks = heading["evidence"].get("source_block_ids", [heading["block_id"]])
            locations = [blocks[ident]["location"] for ident in source_blocks if ident in blocks]
            ordered_headings.append({
                **heading, "block_ids": heading_blocks[node.id], "locations": locations,
            })

        # Keep the original schema in document.json; separate views add conveniences.
        self.write_json(root / "document.json", data)
        for name, records in (("texts", grouped["text"]), ("tables", grouped["table"]),
                              ("images", grouped["image"]), ("headings", ordered_headings)):
            self.write_json(root / f"{name}.json", records)
        metadata = {key: value for key, value in data.items() if key not in {"blocks", "headings", "assets"}}
        metadata["counts"] = {kind: len(records) for kind, records in grouped.items()}
        metadata["heading_count"] = len(ordered_headings)
        metadata["unassigned_block_ids"] = heading_blocks["root"]
        self.write_json(root / "metadata.json", metadata)
        self.export_readable(root, grouped, ordered_headings)
        return root / "document.json"

    def export_images(self, root: Path) -> None:
        if (root / "images").is_symlink():
            raise ValueError("Refusing images directory symlink")
        extensions = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif",
                      "image/bmp": ".bmp", "image/tiff": ".tiff", "image/svg+xml": ".svg"}
        for asset in self.document.assets:
            data = self.document._asset_bytes.get(asset["id"])
            if data is None or not asset["media_type"].startswith("image/"):
                continue
            digest = hashlib.sha256(data).hexdigest()
            relative = "images/" + digest + extensions.get(asset["media_type"], ".bin")
            atomic_write(root / relative, data)
            asset["output_path"] = relative

    def export_readable(self, root: Path, grouped: dict, headings: list[dict]) -> None:
        text_lines = []
        for block in grouped["text"]:
            text_lines.extend([
                f"[{block['id']}] {self.page_label(block['location'])}",
                "제목 경로: " + " > ".join(block["heading_path"]),
                f"영역: {block['scope']}", block["text"], "",
            ])
        atomic_write(root / "texts.txt", "\n".join(text_lines).encode("utf-8"))

        outline = ["# 제목 계층 (깊이 우선 순서)", "", "자동 분류 제목과 페이지는 검증된 정답이 아닙니다.", ""]
        for heading in headings:
            locations = "; ".join(self.page_label(location) for location in heading["locations"])
            outline.append(
                "  " * max(0, heading["depth"] - 1)
                + f"- {self.markdown(heading['title'])} — {locations or '페이지 미확정'} "
                + f"(수준 {heading['level']}, 직접 소속 객체 {len(heading['block_ids'])}개)"
            )
        if not headings:
            outline.append("인식된 제목이 없습니다. 텍스트는 texts.txt에서 확인하세요.")
        atomic_write(root / "headings.md", ("\n".join(outline) + "\n").encode("utf-8"))

        tables = ["# 추출한 표", "", "원본 병합과 메타데이터는 tables.json에 있습니다. 아래는 셀 주소별 보기입니다.", ""]
        for index, table in enumerate(grouped["table"], start=1):
            tables.extend([
                f"## 표 {index} ({table['rows']}행 × {table['columns']}열)", "",
                f"ID: `{table['id']}`", "", self.page_label(table["location"]), "",
                "제목 경로: " + self.markdown(" > ".join(table["heading_path"])), "",
                "| 행 (1부터) | 열 (1부터) | 행 병합 | 열 병합 | 셀 텍스트 |",
                "| --- | --- | --- | --- | --- |",
            ])
            for cell in table["cells"]:
                tables.append(f"| {cell['row'] + 1} | {cell['column'] + 1} | {cell['row_span']} "
                              f"| {cell['col_span']} | {self.markdown(cell['text'])} |")
            tables.append("")
        atomic_write(root / "tables.md", ("\n".join(tables) + "\n").encode("utf-8"))
