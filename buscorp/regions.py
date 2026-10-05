"""시도·시군구 명칭 정규화.

원천 데이터마다 '서울', '서울시', '서울특별시', '강원도'/'강원특별자치도' 처럼 표기가 달라
보조금 정책 레지스트리와 매칭하려면 표준 명칭으로 맞춰야 한다.

※ 2026년 광주광역시·전라남도 통합(전남광주통합특별시)처럼 행정구역이 바뀌면
  SIDO_ALIASES 와 data/subsidy_policies.yaml 을 함께 갱신할 것.
"""

from __future__ import annotations

import re

SIDO_ALIASES: dict[str, str] = {
    "서울": "서울특별시", "서울시": "서울특별시",
    "부산": "부산광역시", "부산시": "부산광역시",
    "대구": "대구광역시", "대구시": "대구광역시",
    "인천": "인천광역시", "인천시": "인천광역시",
    "광주": "광주광역시", "광주시": "광주광역시", "(구)광주광역시": "광주광역시",
    "대전": "대전광역시", "대전시": "대전광역시",
    "울산": "울산광역시", "울산시": "울산광역시",
    "세종": "세종특별자치시", "세종시": "세종특별자치시",
    "경기": "경기도",
    "강원": "강원특별자치도", "강원도": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전북특별자치도", "전라북도": "전북특별자치도",
    "전남": "전라남도",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주": "제주특별자치도", "제주도": "제주특별자치도",
}
SIDO_NAMES = set(SIDO_ALIASES.values()) | {"전남광주통합특별시"}


def normalize_sido(name: str | None) -> str:
    if not name:
        return ""
    n = re.sub(r"\s+", "", str(name))
    return SIDO_ALIASES.get(n, n)


GWANGJU_FORMER_GU = {"동구", "서구", "남구", "북구", "광산구"}


def policy_sido(sido: str, sgg: str = "") -> str:
    """정책 레지스트리 조회용 시도명.

    통합 후 버스 재정지원 체계가 별도로 확인되기 전까지는 종전 체계로 간주한다:
    '전남광주통합특별시 + 구(舊)광주 자치구' → 광주광역시 정책, 나머지 시군 → 전라남도 정책.
    """
    sido = normalize_sido(sido)
    if sido == "전남광주통합특별시":
        return "광주광역시" if normalize_sgg(sgg) in GWANGJU_FORMER_GU else "전라남도"
    return sido


def normalize_sgg(name: str | None) -> str:
    """시군구명 정규화 — 공백 제거, '창원시 의창구' → '창원시' 처럼 일반구는 상위 시로 올린다."""
    if not name:
        return ""
    parts = str(name).split()
    if len(parts) >= 2 and parts[0].endswith("시") and parts[1].endswith("구"):
        return parts[0]
    return "".join(parts)
