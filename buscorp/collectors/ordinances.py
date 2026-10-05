"""준공영제·버스 재정지원 자치법규(조례) 자동 점검 — 국가법령정보 공동활용 OPEN API.

신청: https://open.law.go.kr → OPEN API 활용신청 → 발급된 OC(이메일 ID)를 LAW_API_OC 로 설정.

조례의 '존재'는 준공영제 근거가 마련되었다는 뜻일 뿐 실제 시행과는 다를 수 있다
(예: 조례 제정 후 시행 준비 중, 마을버스만 대상). 따라서 결과는 레지스트리의
`semi_public` 항목과 대조해 '신규 후보' / '레지스트리에 있으나 조례 미검색' 을 표시하고,
최종 판단은 사람이 지자체 고시·보도자료로 확인한 뒤 YAML 을 수정한다.
"""

from __future__ import annotations

import re

import pandas as pd

from ..api import get_json

URL = "https://www.law.go.kr/DRF/lawSearch.do"

# 준공영제는 지역에 따라 '공공관리제'(경기), '공영제' 등 다른 명칭을 쓴다
SEMI_PUBLIC_QUERIES = ["준공영제", "공공관리제", "버스 공영제"]
SUBSIDY_QUERIES = ["재정지원 여객자동차", "보조금 여객자동차", "마을버스 재정지원", "벽지노선"]

SEMI_PUBLIC_PAT = re.compile(r"준공영제|공공관리제|공영제")


def search(oc: str, query: str, *, display: int = 100, **kw) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        data = get_json(
            URL,
            {"OC": oc, "target": "ordin", "type": "JSON", "query": query, "display": display, "page": page},
            **kw,
        )
        body = data.get("OrdinSearch", {}) or {}
        items = body.get("law", []) or []
        if isinstance(items, dict):
            items = [items]
        rows.extend(items)
        total = int(body.get("totalCnt") or 0)
        if not items or len(rows) >= total:
            return rows
        page += 1


def _norm(item: dict) -> dict:
    def g(*keys):
        for k in keys:
            if item.get(k):
                return str(item[k]).strip()
        return ""

    name = g("자치법규명")
    return {
        "자치법규명": name,
        "지자체": g("지자체기관명"),
        "자치법규종류": g("자치법규종류"),
        "공포일자": g("공포일자"),
        "시행일자": g("시행일자"),
        "자치법규일련번호": g("자치법규일련번호", "MST"),
        "자치법규ID": g("자치법규ID"),
        "준공영제관련": bool(SEMI_PUBLIC_PAT.search(name)),
        "상세링크": g("자치법규상세링크"),
    }


def collect(oc: str, queries: list[str] | None = None, **kw) -> pd.DataFrame:
    queries = queries or SEMI_PUBLIC_QUERIES + SUBSIDY_QUERIES
    seen: dict[str, dict] = {}
    for q in queries:
        for item in search(oc, q, **kw):
            row = _norm(item)
            key = row["자치법규일련번호"] or row["자치법규명"]
            seen.setdefault(key, {**row, "검색어": q})
        print(f"  조례 검색 '{q}': 누적 {len(seen)}건")
    df = pd.DataFrame(list(seen.values()))
    if df.empty:
        return df
    return df.sort_values(["준공영제관련", "지자체"], ascending=[False, True]).reset_index(drop=True)


def diff_with_registry(ordin_df: pd.DataFrame, registry) -> pd.DataFrame:
    """조례 검색 결과 ↔ 레지스트리 대조표.

    상태:
      - 일치            : 레지스트리에 준공영제로 등록 + 조례 검색됨
      - 신규후보        : 준공영제 조례가 있으나 레지스트리에 미등록 → 확인 후 YAML 추가
      - 조례미검색      : 레지스트리엔 준공영제인데 조례 검색 안 됨 → 명칭 변경/폐지 여부 확인
    """
    found = (
        ordin_df[ordin_df["준공영제관련"]][["지자체", "자치법규명", "시행일자"]].to_dict("records")
        if not ordin_df.empty
        else []
    )
    registered = {(e.sido, e.sgg or ""): e for e in registry.semi_public_entries()}
    rows, matched = [], set()
    for f in found:
        key = registry.key_from_jurisdiction(f["지자체"])
        status = "일치" if key in registered else "신규후보"
        if key in registered:
            matched.add(key)
        rows.append({"시도": key[0], "시군구": key[1], "상태": status, **f})
    for key, e in registered.items():
        if key not in matched:
            rows.append({"시도": key[0], "시군구": key[1], "상태": "조례미검색", "자치법규명": e.semi_public.get("ordinance", "")})
    return pd.DataFrame(rows)
