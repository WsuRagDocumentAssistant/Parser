#================================================
# table_hierarchy/keyword_config.py
# 표 판별 휴리스틱 키워드 설정
#
# 기본값에는 특정 문서/도메인에 종속되지 않는 한국어 표 일반 어휘만 둔다.
# 특정 문서군(예: 대학 평가보고서)에 맞춘 도메인 어휘나 문서별 수동 예외가
# 필요하면 set_keyword_config()로 주입한다.
#
# 판별 로직 자체는 구조 기반 fallback(라벨 길이/숫자 포함 여부/값 비율 등)이
# 있어 키워드 없이도 동작한다. 키워드는 판정 확신도를 높이는 보조 신호다.
#================================================

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableKeywordConfig:
    """
    역할: 표 헤더/레코드 판별에 쓰는 키워드 사전과 문서별 예외를 담는다.
    사용: get_keyword_config()로 조회, set_keyword_config()로 교체.
          부분 변경은 dataclasses.replace() 사용.
          예) set_keyword_config(replace(
                  get_keyword_config(),
                  extra_label_keywords=frozenset({"사업명", "추진내용"}),
              ))
    """

    # header_row_detector: 컬럼 헤더 라벨로 인정할 일반 표 어휘
    label_keywords: frozenset = frozenset(
        {
            "구분", "항목", "내용", "세부내용", "기준", "지표", "목표", "실적", "결과", "비고",
            "연도", "월", "일자", "번호", "순번", "분야", "유형", "대상", "방법", "담당", "부서",
            "성과", "계획", "현황",
        }
    )

    # header_col_detector: 행 헤더(라벨 열) 값으로 인정할 일반 표 어휘
    label_col_keywords: frozenset = frozenset(
        {
            "합계", "평균", "목표", "실적", "달성률",
            "구분", "항목", "분류", "지표", "내용", "세부내용", "영역", "분야", "유형", "기준",
        }
    )

    # header_col_detector: 헤더 이름 자체로 쓰이는 어휘 ("구분" 열 등)
    header_name_keywords: frozenset = frozenset(
        {"구분", "항목", "분류", "지표", "내용", "세부내용", "영역", "분야", "유형", "기준"}
    )

    # 문서 도메인 어휘 주입 슬롯 (기본 비움).
    # label_keywords / label_col_keywords에 합산되어 쓰인다.
    extra_label_keywords: frozenset = frozenset()
    extra_label_col_keywords: frozenset = frozenset()

    # record_stability_filter: 첫 structured record가 하위 헤더 행으로
    # 의심되는 도메인 어휘 (기본 비움 — 구조 판정만으로 동작)
    header_like_keywords: frozenset = frozenset()

    # record_stability_filter: 강제로 raw_only 처리할 표 ID.
    # 일반 판정이 놓치는 것으로 확인된 표에 대한 문서별 수동 예외 (기본 비움)
    forced_raw_only_table_ids: frozenset = frozenset()

    @property
    def effective_label_keywords(self) -> frozenset:
        return self.label_keywords | self.extra_label_keywords

    @property
    def effective_label_col_keywords(self) -> frozenset:
        return self.label_col_keywords | self.extra_label_col_keywords


_current_config = TableKeywordConfig()


def get_keyword_config() -> TableKeywordConfig:
    return _current_config


def set_keyword_config(config: TableKeywordConfig) -> None:
    global _current_config
    _current_config = config
