"""노선 수집기 공통 — 표준 컬럼, 시각·배차 파싱, 업체명 매칭.

출처별(TAGO·서울·경기) 응답 필드가 달라 아래 STANDARD_COLUMNS 로 맞춘 뒤 합친다.
원본 필드는 표준 컬럼 뒤에 그대로 남겨 둔다.
"""

from __future__ import annotations

import re

import pandas as pd

from ..regions import normalize_sido

STANDARD_COLUMNS = [
    "출처", "시도", "지역", "노선ID", "노선번호", "노선유형", "운수업체", "면허관청",
    "기점", "종점", "첫차", "막차", "배차간격(분)", "첨두배차(분)", "비첨두배차(분)",
    "노선길이(km)", "정류소수", "1일운행횟수(추정)",
]


def hhmm(value) -> str:
    """'20231020043000' / '0430' / '04:30' / 430 → '0430'. 해석 불가면 ''."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) >= 12:          # yyyyMMddHHmm[ss]
        digits = digits[8:12]
    elif len(digits) == 3:         # 530 → 0530
        digits = digits.zfill(4)
    if len(digits) != 4 or int(digits[:2]) > 29 or int(digits[2:]) > 59:
        return ""
    return digits


def to_minutes(value) -> float | None:
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def average_interval(*values) -> float | None:
    vals = [v for v in (to_minutes(x) for x in values) if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def daily_trips(first: str, last: str, interval_min, service_hours: float | None = None) -> float | None:
    """첫·막차(HHMM)와 배차간격(분)으로 1일 운행횟수(기점 출발 기준)를 추정."""
    interval = to_minutes(interval_min)
    if interval is None:
        return None
    if service_hours is None:
        first, last = hhmm(first), hhmm(last)
        if not first or not last:
            return None
        start = int(first[:2]) * 60 + int(first[2:])
        end = int(last[:2]) * 60 + int(last[2:])
        if end <= start:
            end += 24 * 60
        service_hours = (end - start) / 60
    return round(service_hours * 60 / interval) + 1


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    """표준 컬럼 보강·정렬 + 1일 운행횟수 추정."""
    for col in STANDARD_COLUMNS:
        if col not in df.columns:
            df[col] = None
    if not df.empty:
        df["1일운행횟수(추정)"] = [
            daily_trips(f, l, i) for f, l, i in zip(df["첫차"], df["막차"], df["배차간격(분)"])
        ]
    extra = [c for c in df.columns if c not in STANDARD_COLUMNS]
    return df[STANDARD_COLUMNS + extra]


# ----------------------------------------------------------------------
# 업체명 매칭
# ----------------------------------------------------------------------
_CORP_FORMS = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(합\)|합자회사|\(사\)|사단법인|\(재\)|재단법인")


def normalize_company(name) -> str:
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    return re.sub(r"[\s·.,\-()]", "", _CORP_FORMS.sub("", str(name)))


def match_companies(routes: pd.DataFrame, corps: pd.DataFrame) -> pd.DataFrame:
    """노선의 운수업체명을 버스업체 표준데이터 업체명(BZENTY_NM)에 매칭해 '매칭업체명' 컬럼 추가.

    1순위: (시도, 정규화명) 일치 / 2순위: 정규화명이 전국에서 유일하게 일치
    """
    by_sido: dict[tuple[str, str], str] = {}
    by_name: dict[str, set[str]] = {}
    for rec in corps.to_dict("records"):
        n = normalize_company(rec.get("BZENTY_NM"))
        if not n:
            continue
        by_sido.setdefault((normalize_sido(rec.get("CTPV_NM")), n), rec["BZENTY_NM"])
        by_name.setdefault(n, set()).add(rec["BZENTY_NM"])

    out = routes.copy()
    matched, how = [], []
    for sido, comp in zip(out["시도"], out["운수업체"]):
        n = normalize_company(comp)
        hit = by_sido.get((normalize_sido(sido), n)) if n else None
        if hit:
            matched.append(hit); how.append("시도+업체명")
        elif n and len(by_name.get(n, ())) == 1:
            matched.append(next(iter(by_name[n]))); how.append("업체명(전국유일)")
        else:
            matched.append(None); how.append("미매칭" if n else "업체명없음")
    out["매칭업체명"] = matched
    out["매칭방식"] = how
    return out


def summarize_by_company(routes: pd.DataFrame) -> pd.DataFrame:
    """업체별 노선수·운행횟수·평균배차·총노선길이 (match_companies 결과 입력)."""
    df = routes.copy()
    df["업체"] = df["매칭업체명"].fillna(df["운수업체"])
    df = df[df["업체"].notna() & (df["업체"].astype(str) != "")]
    for col in ("1일운행횟수(추정)", "배차간격(분)", "노선길이(km)"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return (
        df.groupby(["시도", "업체"], dropna=False)
        .agg(
            수집노선수=("노선ID", "nunique"),
            일운행횟수합계=("1일운행횟수(추정)", "sum"),
            평균배차간격=("배차간격(분)", "mean"),
            총노선길이km=("노선길이(km)", "sum"),
            표준데이터매칭=("매칭업체명", lambda s: "Y" if s.notna().any() else "N"),
            출처=("출처", lambda s: ",".join(sorted(set(map(str, s))))),
        )
        .round(1)
        .reset_index()
        .sort_values(["시도", "수집노선수"], ascending=[True, False])
    )
