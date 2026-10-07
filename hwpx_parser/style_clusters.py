"""Style clustering reused from HwpxParser_sub 8056258068dbef1afd46b657f824df6eabda5a7a.
Source: hwpx/analysis/add_document_blocks_to_json.py.
"""
from typing import Any

def _cluster_key_of(sf: dict[str, Any]) -> tuple:
    """paragraph 스타일 피처 -> 클러스터 시그니처 키."""
    size = sf.get("font_size")
    return (
        sf.get("style_id_ref"),
        sf.get("para_pr_id_ref"),
        round(size * 2) / 2 if size is not None else None,
        sf.get("bold_ratio", 0.0) >= 0.5,
    )


# depth_rank 병합 시 같은 계층으로 볼 글자 크기 허용 오차 (pt)
_DEPTH_RANK_SIZE_TOLERANCE = 0.9

# heading-like 판정 점수 임계
_HEADING_SCORE_THRESHOLD = 3.0


def _build_style_clusters(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """
    텍스트가 있는 paragraph 블록을 스타일 시그니처로 묶은 뒤,

    1) 본문 기준선(body baseline) 확정: 총 글자수 최대 클러스터
    2) heading-like 점수제: 글자 크기/굵기/짧은 텍스트/희소성/
       후행 블록 다양성/들여쓰기 차이를 가산, 고빈도·장문을 감산
    3) style_cluster_rank(세분 랭크)와 depth_rank_candidate(병합 랭크) 분리

    키워드·정규식 없이 문서 스스로의 스타일 일관성만 사용한다.
    """
    clusters: dict[tuple, dict[str, Any]] = {}
    member_indices: dict[tuple, list[int]] = {}

    # 후행 블록 신호용: 의미 있는 블록의 순서 목록
    meaningful: list[int] = []
    for idx, block in enumerate(blocks):
        bt = block["block_type"]
        if bt == "paragraph":
            if (block.get("text_content") or "").strip():
                meaningful.append(idx)
        elif bt in ("table", "image", "shape", "shape_group"):
            meaningful.append(idx)

    next_meaningful: dict[int, int | None] = {}
    for pos, idx in enumerate(meaningful):
        next_meaningful[idx] = meaningful[pos + 1] if pos + 1 < len(meaningful) else None

    for idx, block in enumerate(blocks):
        if block["block_type"] != "paragraph":
            continue
        text = (block.get("text_content") or "").strip()
        if not text:
            continue

        sf = block["style_features"]
        key = _cluster_key_of(sf)

        cluster = clusters.setdefault(key, {
            "count": 0,
            "total_chars": 0,
            "font_size": key[2],
            "bold": key[3],
            "alignment": sf.get("alignment"),
            "indent_values": [],
            "sample_texts": [],
        })
        cluster["count"] += 1
        cluster["total_chars"] += len(text)
        if sf.get("indent") is not None:
            cluster["indent_values"].append(sf["indent"])
        if len(cluster["sample_texts"]) < 5:
            cluster["sample_texts"].append(text[:60])
        member_indices.setdefault(key, []).append(idx)

    if not clusters:
        return {
            "cluster_records": {},
            "body_font_size": None,
            "body_cluster_id": None,
            "cluster_info_of": {},
            "cluster_key_of": _cluster_key_of,
            "report": [],
        }

    # ── 1) 본문 기준선 확정 ──
    body_key = max(clusters, key=lambda k: clusters[k]["total_chars"])
    body_cluster = clusters[body_key]
    body_size = body_cluster["font_size"]
    body_alignment = body_cluster["alignment"]
    body_indent_avg = (
        sum(body_cluster["indent_values"]) / len(body_cluster["indent_values"])
        if body_cluster["indent_values"] else 0
    )

    total_paragraphs = sum(c["count"] for c in clusters.values())

    # ── 2) heading-like 점수제 ──
    scores: dict[tuple, tuple[float, list[str]]] = {}
    for key, cluster in clusters.items():
        if key == body_key:
            continue

        size = cluster["font_size"]
        avg_len = cluster["total_chars"] / cluster["count"]
        freq_ratio = cluster["count"] / total_paragraphs

        score = 0.0
        signals: list[str] = []

        if size is not None and body_size is not None and size > body_size + 0.1:
            gain = min(2.0, 1.0 + (size - body_size) / 4.0)
            score += gain
            signals.append(f"font_size {size} > body {body_size} (+{gain:.1f})")

        if cluster["bold"]:
            score += 1.0
            signals.append("bold (+1.0)")

        if avg_len <= 40:
            score += 1.0
            signals.append(f"short avg_len {avg_len:.0f} (+1.0)")
        elif avg_len <= 80:
            score += 0.5
            signals.append(f"medium avg_len {avg_len:.0f} (+0.5)")

        if 2 <= cluster["count"] and freq_ratio <= 0.3:
            score += 0.5
            signals.append(f"repeated but rare count={cluster['count']} (+0.5)")

        # 후행 블록 다양성: heading은 같은 스타일이 연달아 오지 않는다
        members = member_indices.get(key, [])
        followed_by_other = 0
        followed_total = 0
        for m in members:
            nxt = next_meaningful.get(m)
            if nxt is None:
                continue
            followed_total += 1
            nxt_block = blocks[nxt]
            if nxt_block["block_type"] != "paragraph":
                followed_by_other += 1
            else:
                nxt_key = _cluster_key_of(nxt_block["style_features"])
                if nxt_key != key:
                    followed_by_other += 1
        if followed_total and followed_by_other / followed_total >= 0.7:
            score += 1.0
            signals.append(
                f"followed_by_other {followed_by_other}/{followed_total} (+1.0)"
            )

        indent_avg = (
            sum(cluster["indent_values"]) / len(cluster["indent_values"])
            if cluster["indent_values"] else 0
        )
        if cluster["alignment"] != body_alignment or abs(indent_avg - body_indent_avg) > 500:
            score += 0.5
            signals.append("alignment/indent differs from body (+0.5)")

        if freq_ratio > 0.3:
            score -= 2.0
            signals.append(f"too frequent {freq_ratio:.0%} (-2.0)")
        if avg_len > 100:
            score -= 2.0
            signals.append(f"too long avg_len {avg_len:.0f} (-2.0)")

        scores[key] = (score, signals)

    heading_keys = [
        key for key, (score, _) in scores.items()
        if score >= _HEADING_SCORE_THRESHOLD
    ]

    # ── 3) 랭크 산정: style_cluster_rank(세분) / depth_rank(병합) 분리 ──
    heading_sorted = sorted(
        heading_keys,
        key=lambda k: (
            -(clusters[k]["font_size"] or 0),
            not clusters[k]["bold"],
            clusters[k]["total_chars"] / clusters[k]["count"],
        ),
    )
    style_rank_of = {key: rank + 1 for rank, key in enumerate(heading_sorted)}

    # depth_rank: (글자 크기 밴드, bold 여부)로 병합.
    # 같은 크기라도 bold 제목(□)과 비굵음 소제목(○)은 다른 계층이다.
    depth_rank_of: dict[tuple, int] = {}
    current_rank = 0
    band_size: float | None = None
    band_bold: bool | None = None
    for key in heading_sorted:
        size = clusters[key]["font_size"] or 0
        bold = clusters[key]["bold"]
        if (
            band_size is None
            or band_size - size > _DEPTH_RANK_SIZE_TOLERANCE
            or bold != band_bold
        ):
            current_rank += 1
            band_size = size
            band_bold = bold
        depth_rank_of[key] = current_rank

    # ── 클러스터 ID 부여 (글자수 많은 순 C01..) 및 리포트 생성 ──
    ordered_keys = sorted(
        clusters,
        key=lambda k: (-clusters[k]["total_chars"], -clusters[k]["count"]),
    )
    cluster_id_of = {
        key: f"C{index + 1:02d}" for index, key in enumerate(ordered_keys)
    }

    cluster_info_of: dict[tuple, dict[str, Any]] = {}
    report: list[dict[str, Any]] = []
    for key in ordered_keys:
        cluster = clusters[key]
        score, signals = scores.get(key, (None, []))
        is_body = key == body_key
        is_heading = key in style_rank_of

        if is_body:
            role_candidate = "body_baseline"
            confidence = 0.9
        elif is_heading:
            role_candidate = "section_heading"
            confidence = min(0.9, 0.45 + 0.08 * (score or 0))
        else:
            role_candidate = "body"
            confidence = 0.7

        info = {
            "style_cluster_id": cluster_id_of[key],
            "cluster_role_candidate": role_candidate,
            "cluster_confidence": round(confidence, 2),
            "style_cluster_rank": style_rank_of.get(key),
            "depth_rank_candidate": depth_rank_of.get(key),
        }
        cluster_info_of[key] = info

        indent_avg = (
            sum(cluster["indent_values"]) / len(cluster["indent_values"])
            if cluster["indent_values"] else 0
        )
        report.append({
            "cluster_id": cluster_id_of[key],
            "role_candidate": role_candidate,
            "style_cluster_rank": style_rank_of.get(key),
            "depth_rank_candidate": depth_rank_of.get(key),
            "heading_score": round(score, 2) if score is not None else None,
            "heading_signals": signals,
            "block_count": cluster["count"],
            "total_chars": cluster["total_chars"],
            "avg_text_length": round(cluster["total_chars"] / cluster["count"], 1),
            "font_size": cluster["font_size"],
            "bold": cluster["bold"],
            "alignment": cluster["alignment"],
            "avg_indent_left": round(indent_avg, 1),
            "signature": {
                "style_id_ref": key[0],
                "para_pr_id_ref": key[1],
            },
            "sample_texts": cluster["sample_texts"],
        })

    return {
        "cluster_records": clusters,
        "body_font_size": body_size,
        "body_cluster_id": cluster_id_of[body_key],
        "cluster_info_of": cluster_info_of,
        "cluster_key_of": _cluster_key_of,
        "heading_cluster_count": len(heading_keys),
        "depth_rank_count": current_rank,
        "report": report,
    }
