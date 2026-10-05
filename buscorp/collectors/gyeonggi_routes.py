"""경기도 버스노선정보 수집 — 경기도_버스노선 조회 v2 (GBIS, data.go.kr 15080662).

활용신청: data.go.kr '경기도_버스노선 조회' (서비스키는 공용 PUBLIC_DATA_API_KEY)

  getBusRouteListv2     (keyword)  : 노선번호 부분일치 검색 → 노선ID·번호·유형·지역
  getBusRouteInfoItemv2 (routeId)  : 노선 상세 → 운수업체(companyName), 첫·막차, 평일 첨두/비첨두 배차, 면허관청

경기도 노선은 2천 개가 넘어 상세 호출이 많다(개발계정 일일 트래픽 한도 주의).
상세 응답은 출력 폴더의 캐시(JSON)에 저장해 다음 실행 때 이어서 수집하고,
--max-details 로 1회 호출 상한을 둘 수 있다. 상세 미수집 노선은 '상세수집=N' 으로 남는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from ..api import ApiError, get_json, normalize_items
from .route_common import average_interval, finalize, hhmm, to_minutes

BASE = "https://apis.data.go.kr/6410000/busrouteservice/v2"
DEFAULT_TERMS = list("0123456789")

ROUTE_TYPES = {
    "11": "직행좌석형시내버스", "12": "좌석형시내버스", "13": "일반형시내버스", "14": "광역급행형시내버스",
    "15": "따복형시내버스", "16": "경기순환버스", "21": "직행좌석형농어촌버스", "22": "좌석형농어촌버스",
    "23": "일반형농어촌버스", "30": "마을버스", "41": "고속형시외버스", "42": "좌석형시외버스",
    "43": "일반형시외버스", "51": "리무진공항버스", "52": "좌석형공항버스", "53": "일반형공항버스",
}


def parse(data: dict) -> list[dict]:
    """GBIS JSON: {response:{msgHeader:{resultCode, resultMessage}, msgBody:{busRouteList|busRouteInfoItem}}}.

    resultCode 0 = 정상, 4 = 결과없음. msgBody 의 키 이름은 오퍼레이션마다 달라 첫 값(list/dict)을 사용한다.
    """
    resp = data.get("response", data)
    header = resp.get("msgHeader") or {}
    code = str(header.get("resultCode", "0"))
    if code == "4":
        return []
    if code != "0":
        raise ApiError(f"경기 버스 API 오류 {code}: {header.get('resultMessage', '')}")
    body = resp.get("msgBody") or {}
    for v in body.values():
        if isinstance(v, (list, dict)):
            return normalize_items(v)
    return []


def search_routes(api_key: str, keyword: str, **kw) -> list[dict]:
    return parse(get_json(f"{BASE}/getBusRouteListv2",
                          {"serviceKey": api_key, "keyword": keyword, "format": "json"}, **kw))


def route_detail(api_key: str, route_id: str, **kw) -> dict:
    items = parse(get_json(f"{BASE}/getBusRouteInfoItemv2",
                           {"serviceKey": api_key, "routeId": route_id, "format": "json"}, **kw))
    return items[0] if items else {}


def sido_from_admin(admin: str | None) -> str:
    a = str(admin or "")
    if "서울" in a:
        return "서울특별시"
    if "인천" in a:
        return "인천광역시"
    return "경기도"


def standardize(routes: list[dict], details: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for r in routes:
        rid = str(r.get("routeId", ""))
        d = details.get(rid) or {}
        merged = {**r, **d}
        rtype = str(merged.get("routeTypeCd", "")).strip()
        admin = merged.get("adminName")
        rows.append({
            "출처": "경기",
            "시도": sido_from_admin(admin),
            "지역": merged.get("regionName"),
            "노선ID": rid,
            "노선번호": merged.get("routeName"),
            "노선유형": merged.get("routeTypeName") or ROUTE_TYPES.get(rtype, rtype),
            "운수업체": merged.get("companyName"),
            "면허관청": admin,
            "기점": merged.get("startStationName"),
            "종점": merged.get("endStationName"),
            "첫차": hhmm(merged.get("upFirstTime")),
            "막차": hhmm(merged.get("upLastTime")),
            "첨두배차(분)": to_minutes(merged.get("peekAlloc")),
            "비첨두배차(분)": to_minutes(merged.get("nPeekAlloc")),
            "배차간격(분)": average_interval(merged.get("peekAlloc"), merged.get("nPeekAlloc")),
            "상세수집": "Y" if rid in details else "N",
            **{f"raw_{k}": v for k, v in merged.items()},
        })
    return finalize(pd.DataFrame(rows))


def _load_cache(path: Path | None) -> dict[str, dict]:
    if path and path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(path: Path | None, cache: dict) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)


def collect(
    api_key: str,
    terms: Iterable[str] | None = None,
    *,
    with_detail: bool = True,
    max_details: int | None = None,
    cache_path: Path | None = None,
    only_gyeonggi: bool = True,
    **kw,
) -> pd.DataFrame:
    seen: dict[str, dict] = {}
    for t in terms or DEFAULT_TERMS:
        for r in search_routes(api_key, t, **kw):
            rid = str(r.get("routeId", ""))
            if rid:
                seen.setdefault(rid, r)
        print(f"  [경기] 검색어 '{t}': 누적 {len(seen)}개 노선")

    cache = _load_cache(cache_path)
    if with_detail:
        todo = [rid for rid in seen if rid not in cache]
        if max_details is not None:
            todo = todo[:max_details]
        try:
            for i, rid in enumerate(todo, 1):
                cache[rid] = route_detail(api_key, rid, **kw)
                if i % 100 == 0:
                    print(f"  [경기] 노선 상세 {i}/{len(todo)}")
                    _save_cache(cache_path, cache)
        except ApiError as e:  # 트래픽 초과 등 — 받은 만큼 저장하고 다음 실행에서 이어감
            print(f"  [경기] 상세 수집 중단: {e}")
        finally:
            _save_cache(cache_path, cache)
        left = sum(1 for rid in seen if rid not in cache)
        if left:
            print(f"  [경기] 상세 미수집 {left}개 — 다시 실행하면 캐시에 이어서 수집")

    df = standardize(list(seen.values()), cache)
    if only_gyeonggi and not df.empty:
        df = df[df["시도"] == "경기도"].reset_index(drop=True)
    return df
