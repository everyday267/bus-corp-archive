"""노선별 운행정보 수집 — 국토교통부 TAGO 버스노선정보 (BusRouteInfoInqireService).

활용신청: data.go.kr '국토교통부_(TAGO)_버스노선정보' (서비스키는 버스업체 API와 공용)

수집 흐름
  1) getCtyCodeList          : TAGO 지원 도시코드 목록
  2) getRouteNoList          : 도시별 전체 노선 목록 (routeid, routeno, routetp, 기·종점, 첫·막차)
  3) getRouteInfoIem         : 노선별 상세 (배차간격 평일/토/일)
  4) getRouteAcctoThrghSttnList (옵션) : 노선별 경유 정류소 → 정류소 수·노선 길이 추정에 사용

※ 서울·경기 노선은 seoul_routes / gyeonggi_routes 수집기를 사용한다.
※ TAGO 응답에는 운수업체명이 없어 업체 매칭이 안 된다. 업체-노선 연결은 지자체 노선현황 자료나
  route_inputs.csv(수기 입력)로 보완한다.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from ..api import get_json, iter_pages, parse_response
from ..regions import SIDO_NAMES, normalize_sido
from .route_common import finalize, hhmm, to_minutes

BASE = "https://apis.data.go.kr/1613000/BusRouteInfoInqireService"


def _params(api_key: str, **extra) -> dict:
    return {"serviceKey": api_key, "_type": "json", **extra}


def fetch_cities(api_key: str, **kw) -> list[dict]:
    items, _ = parse_response(get_json(f"{BASE}/getCtyCodeList", _params(api_key), **kw))
    return items


def fetch_route_list(api_key: str, city_code: int | str, **kw) -> list[dict]:
    out: list[dict] = []
    for page in iter_pages(f"{BASE}/getRouteNoList", _params(api_key, cityCode=city_code), page_size=500, **kw):
        out.extend(page)
    return out


def fetch_route_detail(api_key: str, city_code, route_id: str, **kw) -> dict:
    items, _ = parse_response(
        get_json(f"{BASE}/getRouteInfoIem", _params(api_key, cityCode=city_code, routeId=route_id), **kw)
    )
    return items[0] if items else {}


def fetch_route_stops(api_key: str, city_code, route_id: str, **kw) -> list[dict]:
    out: list[dict] = []
    for page in iter_pages(
        f"{BASE}/getRouteAcctoThrghSttnList",
        _params(api_key, cityCode=city_code, routeId=route_id),
        page_size=500,
        **kw,
    ):
        out.extend(page)
    return out


def standardize(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        city = r.get("cityname", "")
        sido = normalize_sido(city)
        out.append({
            "출처": "TAGO",
            "시도": sido if sido in SIDO_NAMES else "",
            "지역": city,
            "노선ID": str(r.get("routeid", "")),
            "노선번호": r.get("routeno"),
            "노선유형": r.get("routetp"),
            "기점": r.get("startnodenm"),
            "종점": r.get("endnodenm"),
            "첫차": hhmm(r.get("startvehicletime")),
            "막차": hhmm(r.get("endvehicletime")),
            "배차간격(분)": to_minutes(r.get("intervaltime")),
            "정류소수": r.get("stop_count"),
            **{f"raw_{k}": v for k, v in r.items()},
        })
    return finalize(pd.DataFrame(out))


def collect(
    api_key: str,
    city_codes: Iterable[int | str] | None = None,
    *,
    with_detail: bool = True,
    with_stops: bool = False,
    **kw,
) -> pd.DataFrame:
    """도시(들)의 노선 운행정보를 표준 컬럼 DataFrame 으로 수집.

    city_codes 를 생략하면 TAGO 지원 전 도시를 순회한다(호출량이 많으니 일일 트래픽 한도 확인).
    TAGO 도시명이 시 단위(예: 청주시)면 '시도' 는 비워 두고 '지역' 에 도시명을 남긴다.
    """
    cities = {str(c["citycode"]): c.get("cityname", "") for c in fetch_cities(api_key, **kw)}
    targets = [str(c) for c in city_codes] if city_codes else list(cities)

    rows: list[dict] = []
    for code in targets:
        routes = fetch_route_list(api_key, code, **kw)
        print(f"  [TAGO {code} {cities.get(code, '')}] 노선 {len(routes)}개")
        for r in routes:
            row = {"citycode": code, "cityname": cities.get(code, ""), **r}
            rid = str(r.get("routeid", ""))
            if with_detail and rid:
                row.update({k: v for k, v in fetch_route_detail(api_key, code, rid, **kw).items() if v not in (None, "")})
            if with_stops and rid:
                row["stop_count"] = len(fetch_route_stops(api_key, code, rid, **kw))
            rows.append(row)
    return standardize(rows)
