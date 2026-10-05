import json

import pandas as pd
import pytest

from buscorp import __main__ as cli
from buscorp import api, config
from buscorp.collectors import gyeonggi_routes as gg
from buscorp.collectors import route_common as rc
from buscorp.collectors import seoul_routes as seoul

# ---------------------------------------------------------------- fixtures: 가짜 API 응답
SEOUL_ITEMS = {
    "1": [
        {"busRouteId": "100100001", "busRouteNm": "101", "routeType": "3", "corpNm": "(주)서울운수",
         "stStationNm": "우이동", "edStationNm": "서소문", "firstBusTm": "20261005043000",
         "lastBusTm": "20261005223000", "term": "10", "length": "25.3"},
        {"busRouteId": "100100002", "busRouteNm": "M5107", "routeType": "8", "corpNm": "경기고속"},
        {"busRouteId": "100100003", "busRouteNm": "1", "routeType": "9", "corpNm": "폐지운수"},
    ],
    "2": [  # 중복 노선 + 마을버스
        {"busRouteId": "100100001", "busRouteNm": "101", "routeType": "3", "corpNm": "(주)서울운수"},
        {"busRouteId": "100100004", "busRouteNm": "종로02", "routeType": "2", "corpNm": "종로마을버스",
         "firstBusTm": "0600", "lastBusTm": "2300", "term": "12"},
    ],
}


def seoul_response(term):
    items = SEOUL_ITEMS.get(term)
    if not items:
        return {"msgHeader": {"headerCd": "4", "headerMsg": "결과가 없습니다."}, "msgBody": {"itemList": None}}
    return {"msgHeader": {"headerCd": "0"}, "msgBody": {"itemList": items}}


GG_LIST = {
    "3": [{"routeId": "233000031", "routeName": "3000", "routeTypeCd": "11", "regionName": "수원"},
          {"routeId": "233000099", "routeName": "30", "routeTypeCd": "13", "regionName": "용인"}],
    "5": [{"routeId": "100000001", "routeName": "5", "routeTypeCd": "13", "regionName": "서울"}],
}
GG_DETAIL = {
    "233000031": {"routeId": "233000031", "routeName": "3000", "companyName": "경기여객(주)",
                  "adminName": "수원시", "upFirstTime": "05:00", "upLastTime": "23:00",
                  "peekAlloc": "10", "nPeekAlloc": "20", "routeTypeName": "직행좌석형시내버스"},
    "233000099": {"routeId": "233000099", "routeName": "30", "companyName": "용인교통", "adminName": "용인시"},
    "100000001": {"routeId": "100000001", "companyName": "서울버스", "adminName": "서울특별시"},
}


def gg_response(op, params):
    if op == "getBusRouteListv2":
        items = GG_LIST.get(params["keyword"])
        if not items:
            return {"response": {"msgHeader": {"resultCode": 4}, "msgBody": None}}
        return {"response": {"msgHeader": {"resultCode": 0}, "msgBody": {"busRouteList": items}}}
    return {"response": {"msgHeader": {"resultCode": 0},
                         "msgBody": {"busRouteInfoItem": GG_DETAIL[params["routeId"]]}}}


@pytest.fixture
def fake_api(monkeypatch):
    calls = []

    def fake_get_json(url, params, **kw):
        calls.append((url, params))
        if "ws.bus.go.kr" in url:
            return seoul_response(params["strSrch"])
        if "6410000" in url:
            return gg_response(url.rsplit("/", 1)[1], params)
        raise AssertionError(url)

    monkeypatch.setattr(seoul, "get_json", fake_get_json)
    monkeypatch.setattr(gg, "get_json", fake_get_json)
    return calls


# ---------------------------------------------------------------- common
@pytest.mark.parametrize("raw,expected", [
    ("20261005043000", "0430"), ("0430", "0430"), ("04:30", "0430"), ("530", "0530"),
    ("", ""), (None, ""), ("9999", ""),
])
def test_hhmm(raw, expected):
    assert rc.hhmm(raw) == expected


def test_daily_trips_wraps_midnight():
    assert rc.daily_trips("2300", "0100", 30) == 5
    assert rc.daily_trips("0500", "2300", None) is None


@pytest.mark.parametrize("a,b", [("(주)서울운수", "서울운수"), ("서울운수 주식회사", "㈜서울운수"), ("경기여객(주)", "경기여객")])
def test_normalize_company(a, b):
    assert rc.normalize_company(a) == rc.normalize_company(b)


# ---------------------------------------------------------------- seoul
def test_seoul_parse_errors():
    with pytest.raises(api.ApiError):
        seoul.parse({"msgHeader": {"headerCd": "7", "headerMsg": "인증 실패"}})
    assert seoul.parse({"msgHeader": {"headerCd": "4"}}) == []


def test_seoul_collect(fake_api):
    df = seoul.collect("KEY", ["1", "2", "3"])
    assert sorted(df["노선ID"]) == ["100100001", "100100004"]  # 경기면허(8)·폐지(9) 제외, 중복 제거
    r = df.set_index("노선ID").loc["100100001"]
    assert (r["시도"], r["노선유형"], r["운수업체"], r["첫차"], r["막차"]) == ("서울특별시", "간선", "(주)서울운수", "0430", "2230")
    assert r["노선길이(km)"] == 25.3 and r["1일운행횟수(추정)"] == 18 * 6 + 1
    assert df.set_index("노선ID").loc["100100004", "노선유형"] == "마을"
    assert all(p["resultType"] == "json" for _, p in fake_api)

    df_all = seoul.collect("KEY", ["1"], include_other_regions=True)
    assert df_all.set_index("노선ID").loc["100100002", "시도"] == "경기도"


# ---------------------------------------------------------------- gyeonggi
def test_gyeonggi_collect_with_cache_and_limit(fake_api, tmp_path):
    cache = tmp_path / "gg.json"
    df = gg.collect("KEY", ["3", "5"], max_details=1, cache_path=cache)
    assert json.loads(cache.read_text(encoding="utf-8")).keys() == {"233000031"}
    assert list(df["상세수집"]) == ["Y", "N", "N"]  # 상세 전엔 면허관청을 몰라 서울 노선도 경기로 남음
    r = df.set_index("노선ID").loc["233000031"]
    assert (r["운수업체"], r["면허관청"], r["첫차"], r["막차"]) == ("경기여객(주)", "수원시", "0500", "2300")
    assert (r["첨두배차(분)"], r["비첨두배차(분)"], r["배차간격(분)"]) == (10, 20, 15)
    assert r["노선유형"] == "직행좌석형시내버스"

    n_detail = sum(1 for u, _ in fake_api if "InfoItem" in u)
    df = gg.collect("KEY", ["3", "5"], cache_path=cache)  # 캐시 이어받기
    assert sum(1 for u, _ in fake_api if "InfoItem" in u) - n_detail == 2
    assert set(df["노선ID"]) == {"233000031", "233000099"}  # 서울 면허(adminName) 노선 제외
    assert (df["상세수집"] == "Y").all()


def test_gyeonggi_detail_failure_keeps_partial_cache(monkeypatch, tmp_path):
    def flaky(url, params, **kw):
        if "InfoItem" in url and params["routeId"] == "233000099":
            raise api.ApiError("LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR")
        return gg_response(url.rsplit("/", 1)[1], params)

    monkeypatch.setattr(gg, "get_json", flaky)
    cache = tmp_path / "gg.json"
    df = gg.collect("KEY", ["3"], cache_path=cache)
    assert list(json.loads(cache.read_text(encoding="utf-8"))) == ["233000031"]
    assert len(df) == 2


# ---------------------------------------------------------------- matching
def test_match_and_summarize():
    routes = pd.DataFrame([
        {"출처": "서울", "시도": "서울특별시", "노선ID": "1", "운수업체": "(주)서울운수", "1일운행횟수(추정)": 100, "배차간격(분)": 10, "노선길이(km)": 20},
        {"출처": "서울", "시도": "서울특별시", "노선ID": "2", "운수업체": "서울운수", "1일운행횟수(추정)": 50, "배차간격(분)": 20, "노선길이(km)": 10},
        {"출처": "경기", "시도": "경기도", "노선ID": "3", "운수업체": "경기여객(주)", "1일운행횟수(추정)": 80, "배차간격(분)": 15, "노선길이(km)": 30},
        {"출처": "경기", "시도": "경기도", "노선ID": "4", "운수업체": "없는운수", "1일운행횟수(추정)": 10, "배차간격(분)": 60, "노선길이(km)": 5},
    ])
    corps = pd.DataFrame([
        {"BZENTY_NM": "서울운수(주)", "CTPV_NM": "서울특별시"},
        {"BZENTY_NM": "경기여객 주식회사", "CTPV_NM": "충청남도"},  # 시도 불일치 → 전국 유일명으로 매칭
    ])
    m = rc.match_companies(routes, corps)
    assert list(m["매칭방식"]) == ["시도+업체명", "시도+업체명", "업체명(전국유일)", "미매칭"]
    s = rc.summarize_by_company(m).set_index("업체")
    assert s.loc["서울운수(주)", "수집노선수"] == 2 and s.loc["서울운수(주)", "일운행횟수합계"] == 150
    assert s.loc["서울운수(주)", "평균배차간격"] == 15
    assert s.loc["없는운수", "표준데이터매칭"] == "N"


# ---------------------------------------------------------------- CLI
def test_cli_routes_end_to_end(fake_api, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "PUBLIC_DATA_API_KEY", "KEY")
    pd.DataFrame([{"BZENTY_NM": "서울운수", "CTPV_NM": "서울특별시"}]).to_csv(
        tmp_path / "bus_corps_latest.csv", index=False, encoding="utf-8-sig")
    cli.main(["routes", "--source", "seoul", "gyeonggi", "--terms", "1", "2", "3"])
    allr = pd.read_csv(tmp_path / "routes_all_latest.csv", dtype=str)
    assert set(allr["출처"]) == {"서울", "경기"}
    assert (tmp_path / "routes_by_company_latest.csv").exists()
    assert (tmp_path / "cache" / "gyeonggi_route_detail.json").exists()
