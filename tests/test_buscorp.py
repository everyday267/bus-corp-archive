import pandas as pd
import pytest
import requests

from buscorp import api
from buscorp.collectors import ordinances, routes
from buscorp.policy import PolicyRegistry, normalize_scope
from buscorp.regions import normalize_sgg, normalize_sido, policy_sido
from buscorp.subsidy import COMPUTED, NEED_INPUT, estimate_all, estimate_company


@pytest.fixture(scope="module")
def reg():
    return PolicyRegistry.load()


def corp(name, sido, sgg, oper, buses):
    return {"BZENTY_NM": name, "CTPV_NM": sido, "SGG_NM": sgg, "OPER_SE_NM": oper, "BUS_HLD_CNTOM": buses}


# ---------------------------------------------------------------- api
@pytest.mark.parametrize("items,expected", [
    (None, []), ("", []), ({"a": 1}, [{"a": 1}]), ({"item": {"a": 1}}, [{"a": 1}]),
    ({"item": [{"a": 1}, {"a": 2}]}, [{"a": 1}, {"a": 2}]), ([{"a": 1}], [{"a": 1}]),
])
def test_normalize_items(items, expected):
    assert api.normalize_items(items) == expected


def test_parse_response_error():
    with pytest.raises(api.ApiError):
        api.parse_response({"response": {"header": {"resultCode": "30", "resultMsg": "KEY ERR"}}})


class FakeResp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, ""

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeSession:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return self.pages.pop(0)


def page(items, total):
    return FakeResp({"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": items}, "totalCount": total}}})


def test_iter_pages_follows_pagination():
    s = FakeSession([page([{"i": 1}, {"i": 2}], 3), page([{"i": 3}], 3)])
    got = [i for p in api.iter_pages("u", {}, page_size=2, session=s, sleep=lambda _: None) for i in p]
    assert [g["i"] for g in got] == [1, 2, 3]
    assert [c["pageNo"] for c in s.calls] == [1, 2]


def test_get_json_retries_bounded():
    class Boom:
        n = 0

        def get(self, *a, **k):
            Boom.n += 1
            raise requests.Timeout()

    with pytest.raises(api.ApiError):
        api.get_json("u", {}, session=Boom(), retries=2, sleep=lambda _: None)
    assert Boom.n == 3


# ---------------------------------------------------------------- regions
def test_region_normalization():
    assert normalize_sido("서울") == "서울특별시"
    assert normalize_sido("강원도") == "강원특별자치도"
    assert normalize_sgg("창원시 의창구") == "창원시"
    assert policy_sido("전남광주통합특별시", "광산구") == "광주광역시"
    assert policy_sido("전남광주통합특별시", "순천시") == "전라남도"


# ---------------------------------------------------------------- policy
def test_registry_valid(reg):
    assert reg.validate() == []


def test_all_sido_covered(reg):
    sidos = {e.sido for e in reg.entries}
    for s in ["서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시",
              "세종특별자치시", "경기도", "강원특별자치도", "충청북도", "충청남도", "전북특별자치도",
              "전라남도", "경상북도", "경상남도", "제주특별자치도"]:
        assert s in sidos, s


def test_scope_and_semi_public_resolution(reg):
    assert normalize_scope("시내버스") == "시내버스"
    assert reg.resolve("서울특별시", "종로구", "시내버스").semi_public
    assert not reg.resolve("서울특별시", "종로구", "마을버스").semi_public  # 서울 준공영제는 시내버스만
    assert reg.resolve("경상남도", "창원시 성산구", "시내버스").semi_public  # 시군구 우선
    assert not reg.resolve("경상남도", "김해시", "시내버스").semi_public
    r = reg.resolve("경기도", "광명시", "마을버스")
    assert not r.semi_public and any("조례제정" in n for n in r.notes)
    assert reg.resolve("전남광주통합특별시", "북구", "시내버스").semi_public


def test_key_from_jurisdiction(reg):
    assert reg.key_from_jurisdiction("경기도 광명시") == ("경기도", "광명시")
    assert reg.key_from_jurisdiction("(구)광주광역시") == ("광주광역시", "")
    assert reg.key_from_jurisdiction("전남광주통합특별시 고흥군") == ("전라남도", "고흥군")


# ---------------------------------------------------------------- subsidy
PARAMS = {
    "national": {"fuel_subsidy_per_liter": 300, "compensation_rate_unprofitable": 0.8,
                 "compensation_rate_remote": 1.0, "transfer_loss_per_trip": 500,
                 "transfer_compensation_rate": 0.5, "fare_discount_compensation_rate": 1.0,
                 "vehicle_subsidy_per_bus": 1000},
    "regions": {("서울특별시", ""): {"standard_cost_per_bus_day": 1000, "revenue_per_bus_day": 800},
                ("경기도", ""): {"standard_cost_per_bus_day": 1000, "revenue_per_bus_day": 800}},
}


def test_semi_public_deficit(reg):
    row = estimate_company(corp("A", "서울특별시", "중구", "시내버스", 10), reg, PARAMS)
    # (1000-800) × 10대 × 365일
    assert row["준공영제적용"] == "Y"
    assert row["준공영제 재정지원(운송수입 부족분)"] == 200 * 10 * 365


def test_semi_public_deficit_offsets_fuel_subsidy(reg):
    row = estimate_company(corp("A", "서울특별시", "중구", "시내버스", 10), reg, PARAMS, {"fuel_liters": 1000})
    assert row["유가보조금 (경유 유류세 인상분 / CNG 세금 상당액)"] == 300_000
    assert row["준공영제 재정지원(운송수입 부족분)"] == 200 * 10 * 365 - 300_000
    assert row["예상보조금합계"] == 200 * 10 * 365


def test_partial_semi_public_requires_share(reg):
    row = estimate_company(corp("B", "경기도", "수원시", "시내버스", 100), reg, PARAMS)
    assert row["준공영제 재정지원(운송수입 부족분)_상태"] == NEED_INPUT
    assert "semi_public_bus_share" in row["입력필요항목"]
    row = estimate_company(corp("B", "경기도", "수원시", "시내버스", 100), reg, PARAMS, {"semi_public_bus_share": 0.5})
    assert row["준공영제 재정지원(운송수입 부족분)"] == 200 * 100 * 365 * 0.5


def test_standard_region_components(reg):
    routes_in = [
        {"노선구분": "비수익", "노선번호": "1", "연간운행원가": 300, "연간운송수입": 100},
        {"노선구분": "벽지", "노선번호": "2", "연간운행원가": 100, "연간운송수입": 150},  # 흑자 → 0
    ]
    ci = {"transfer_trips": 10, "discount_trips": 5, "discount_amount_per_trip": 100, "new_buses": 2}
    row = estimate_company(corp("C", "충청남도", "천안시", "시내버스", 5), reg, PARAMS, ci, routes_in)
    assert row["준공영제적용"] == "N"
    assert row["비수익노선 손실보상"] == 160
    assert row["벽지노선 손실보상"] == 0
    assert row["환승할인 손실보전"] == 2500
    assert row["요금할인·무료 손실보전 (어르신·청소년 등)"] == 500
    assert row["차량 구입 보조 (저상·전기·수소버스)"] == 2000
    assert row["유가보조금 (경유 유류세 인상분 / CNG 세금 상당액)_상태"] == NEED_INPUT
    assert row["산정완료"] == "N(부분)"


def test_missing_params_never_invent_values(reg):
    row = estimate_company(corp("D", "부산광역시", "해운대구", "시내버스", 10), reg)
    assert row["준공영제 재정지원(운송수입 부족분)_상태"] == NEED_INPUT
    assert row["예상보조금합계"] == 0


def test_estimate_all_matches_inputs(reg):
    corps = pd.DataFrame([corp("A", "서울특별시", "중구", "시내버스", 10), corp("A", "부산광역시", "중구", "시내버스", 10)])
    ci = pd.DataFrame([{"업체명": "A", "시도": "서울", "operating_buses": 5}])
    df = estimate_all(corps, reg, PARAMS, ci)
    assert list(df["적용운행대수"]) == [5, 10]


# ---------------------------------------------------------------- collectors
def test_daily_trips_estimate():
    row = pd.Series({"intervaltime": 15, "startvehicletime": "0530", "endvehicletime": "2330"})
    assert routes.daily_trips_estimate(row) == 18 * 4 + 1


def test_ordinance_diff(reg):
    df = pd.DataFrame([
        {"지자체": "서울특별시", "자치법규명": "서울특별시 시내버스 준공영제 운영에 관한 조례", "시행일자": "20250103", "준공영제관련": True},
        {"지자체": "경상북도 포항시", "자치법규명": "포항시 시내버스 준공영제 운영 조례", "시행일자": "20270101", "준공영제관련": True},
    ])
    diff = ordinances.diff_with_registry(df, reg)
    status = {(r["시도"], r["시군구"]): r["상태"] for r in diff.to_dict("records")}
    assert status[("서울특별시", "")] == "일치"
    assert status[("경상북도", "포항시")] == "신규후보"
    assert status[("부산광역시", "")] == "조례미검색"
