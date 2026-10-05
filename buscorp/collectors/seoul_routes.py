"""서울특별시 버스노선정보 수집 — 서울특별시_노선정보조회 서비스 (data.go.kr 15000193).

활용신청: data.go.kr 에서 '서울특별시_노선정보조회 서비스' 신청 (서비스키는 공용 PUBLIC_DATA_API_KEY)
엔드포인트: http://ws.bus.go.kr/api/rest/busRouteInfo/getBusRouteList?strSrch=<노선번호 검색어>

getBusRouteList 는 '노선번호 부분일치' 검색만 지원하므로 숫자 0~9 로 검색해 합친 뒤
노선ID로 중복을 제거한다. 응답에 운수사명(corpNm)·배차간격(term)·노선길이(length)가 포함돼
노선별 상세 호출 없이 업체-노선 매칭이 가능하다.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from ..api import ApiError, get_json, normalize_items
from .route_common import finalize, hhmm, to_minutes

BASE = "http://ws.bus.go.kr/api/rest/busRouteInfo"
DEFAULT_TERMS = list("0123456789")

ROUTE_TYPES = {
    "1": "공항", "2": "마을", "3": "간선", "4": "지선", "5": "순환",
    "6": "광역", "7": "인천", "8": "경기", "9": "폐지", "0": "공용",
}
# 서울 경유 타 시도 면허 노선 — 경기·TAGO 수집기에서 수집하므로 기본 제외
OTHER_REGION_TYPES = {"7": "인천광역시", "8": "경기도"}


def parse(data: dict) -> list[dict]:
    """ws.bus.go.kr JSON: {msgHeader:{headerCd, headerMsg}, msgBody:{itemList:[...]}}. headerCd 4 = 결과없음."""
    header = data.get("msgHeader") or {}
    code = str(header.get("headerCd", "0"))
    if code == "4":
        return []
    if code != "0":
        raise ApiError(f"서울 버스 API 오류 {code}: {header.get('headerMsg', '')}")
    return normalize_items((data.get("msgBody") or {}).get("itemList"))


def search_routes(api_key: str, term: str, **kw) -> list[dict]:
    return parse(get_json(f"{BASE}/getBusRouteList",
                          {"serviceKey": api_key, "strSrch": term, "resultType": "json"}, **kw))


def standardize(items: list[dict]) -> pd.DataFrame:
    rows = []
    for it in items:
        rtype = str(it.get("routeType", "")).strip()
        rows.append({
            "출처": "서울",
            "시도": OTHER_REGION_TYPES.get(rtype, "서울특별시"),
            "지역": "서울",
            "노선ID": str(it.get("busRouteId", "")),
            "노선번호": it.get("busRouteNm"),
            "노선유형": ROUTE_TYPES.get(rtype, rtype),
            "운수업체": it.get("corpNm"),
            "면허관청": "서울특별시" if rtype not in OTHER_REGION_TYPES else OTHER_REGION_TYPES[rtype],
            "기점": it.get("stStationNm"),
            "종점": it.get("edStationNm"),
            "첫차": hhmm(it.get("firstBusTm")),
            "막차": hhmm(it.get("lastBusTm")),
            "배차간격(분)": to_minutes(it.get("term")),
            "노선길이(km)": to_minutes(it.get("length")),
            **{f"raw_{k}": v for k, v in it.items()},
        })
    return finalize(pd.DataFrame(rows))


def collect(
    api_key: str,
    terms: Iterable[str] | None = None,
    *,
    include_other_regions: bool = False,
    include_abolished: bool = False,
    **kw,
) -> pd.DataFrame:
    seen: dict[str, dict] = {}
    for t in terms or DEFAULT_TERMS:
        for it in search_routes(api_key, t, **kw):
            rid = str(it.get("busRouteId", ""))
            if rid:
                seen.setdefault(rid, it)
        print(f"  [서울] 검색어 '{t}': 누적 {len(seen)}개 노선")
    items = [
        it for it in seen.values()
        if (include_other_regions or str(it.get("routeType")) not in OTHER_REGION_TYPES)
        and (include_abolished or str(it.get("routeType")) != "9")
    ]
    return standardize(items)
