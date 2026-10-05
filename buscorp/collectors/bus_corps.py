"""전국버스업체표준데이터 수집 (tn_pubr_public_bus_corp_api)."""

from __future__ import annotations

import pandas as pd

from ..api import iter_pages

URL = "https://api.data.go.kr/openapi/tn_pubr_public_bus_corp_api"

FIELDS = {
    "BZENTY_NM": "업체명",
    "CTPV_NM": "시도명",
    "SGG_NM": "시군구명",
    "LCTN_ROAD_NM_ADDR": "소재지도로명주소",
    "LCTN_LOTNO_ADDR": "소재지지번주소",
    "OPER_SE_NM": "운영구분명",
    "RTE_CNT": "노선수",
    "BUS_HLD_CNTOM": "버스보유대수",
    "TELNO": "전화번호",
    "RPRSV_NM": "대표자명",
    "TNOEMP": "총직원수",
    "DATA_CRTR_YMD": "데이터기준일자",
    "instt_code": "제공기관코드",
    "instt_nm": "제공기관명",
}
NUMERIC = ["RTE_CNT", "BUS_HLD_CNTOM", "TNOEMP"]


def fetch(api_key: str, **kw) -> list[dict]:
    items: list[dict] = []
    for page in iter_pages(URL, {"serviceKey": api_key, "type": "json"}, **kw):
        items.extend(page)
        print(f"  버스업체 {len(items)}건 수집")
    return items


def to_frame(items: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(items)
    for col in FIELDS:
        if col not in df.columns:
            df[col] = None
    df = df[list(FIELDS)]
    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """시도별 업체수·노선수·버스대수·직원수."""
    return (
        df.groupby("CTPV_NM")
        .agg(
            업체수=("BZENTY_NM", "count"),
            총노선수=("RTE_CNT", "sum"),
            총버스보유대수=("BUS_HLD_CNTOM", "sum"),
            총직원수=("TNOEMP", "sum"),
        )
        .sort_values("총버스보유대수", ascending=False)
        .reset_index()
        .rename(columns={"CTPV_NM": "시도명"})
    )
