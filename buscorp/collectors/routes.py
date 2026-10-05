"""노선별 운행정보 수집 — 국토교통부 TAGO 버스노선정보 (BusRouteInfoInqireService).

활용신청: data.go.kr '국토교통부_(TAGO)_버스노선정보' (서비스키는 버스업체 API와 공용)

수집 흐름
  1) getCtyCodeList          : TAGO 지원 도시코드 목록
  2) getRouteNoList          : 도시별 전체 노선 목록 (routeid, routeno, routetp, 기·종점, 첫·막차)
  3) getRouteInfoIem         : 노선별 상세 (배차간격 평일/토/일)
  4) getRouteAcctoThrghSttnList (옵션) : 노선별 경유 정류소 → 정류소 수·노선 길이 추정에 사용

※ TAGO 는 서울특별시·경기도 노선 일부를 제공하지 않는다. 서울(서울시 버스노선정보 API),
  경기(경기도 GBIS 버스노선 API)는 별도 수집기가 필요하다 — README '한계' 참고.
※ TAGO 응답에는 운수업체명이 없다. 업체-노선 매칭은 지자체 노선현황 자료나
  route_inputs.csv(수기 입력)로 보완한다.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from ..api import get_json, iter_pages, parse_response

BASE = "https://apis.data.go.kr/1613000/BusRouteInfoInqireService"

ROUTE_COLUMNS = [
    "citycode", "cityname", "routeid", "routeno", "routetp",
    "startnodenm", "endnodenm", "startvehicletime", "endvehicletime",
    "intervaltime", "intervalsattime", "intervalsuntime", "stop_count",
]


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


def collect(
    api_key: str,
    city_codes: Iterable[int | str] | None = None,
    *,
    with_detail: bool = True,
    with_stops: bool = False,
    **kw,
) -> pd.DataFrame:
    """도시(들)의 노선 운행정보를 DataFrame 으로 수집.

    city_codes 를 생략하면 TAGO 지원 전 도시를 순회한다(호출량이 많으니 일일 트래픽 한도 확인).
    """
    cities = {str(c["citycode"]): c.get("cityname", "") for c in fetch_cities(api_key, **kw)}
    targets = [str(c) for c in city_codes] if city_codes else list(cities)

    rows: list[dict] = []
    for code in targets:
        routes = fetch_route_list(api_key, code, **kw)
        print(f"  [{code} {cities.get(code, '')}] 노선 {len(routes)}개")
        for r in routes:
            row = {"citycode": code, "cityname": cities.get(code, ""), **r}
            rid = str(r.get("routeid", ""))
            if with_detail and rid:
                row.update({k: v for k, v in fetch_route_detail(api_key, code, rid, **kw).items() if v not in (None, "")})
            if with_stops and rid:
                row["stop_count"] = len(fetch_route_stops(api_key, code, rid, **kw))
            rows.append(row)

    df = pd.DataFrame(rows)
    for col in ROUTE_COLUMNS:
        if col not in df.columns:
            df[col] = None
    extra = [c for c in df.columns if c not in ROUTE_COLUMNS]
    return df[ROUTE_COLUMNS + extra]


def daily_trips_estimate(row: pd.Series, service_hours: float | None = None) -> float | None:
    """배차간격(분)과 첫·막차 시각(HHMM)으로 1일 운행횟수(편도)를 추정."""
    try:
        interval = float(row.get("intervaltime"))
    except (TypeError, ValueError):
        return None
    if interval <= 0:
        return None
    if service_hours is None:
        try:
            s, e = str(int(row["startvehicletime"])).zfill(4), str(int(row["endvehicletime"])).zfill(4)
        except (KeyError, TypeError, ValueError):
            return None
        start = int(s[:2]) * 60 + int(s[2:])
        end = int(e[:2]) * 60 + int(e[2:])
        if end <= start:
            end += 24 * 60
        service_hours = (end - start) / 60
    return round(service_hours * 60 / interval) + 1
