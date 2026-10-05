"""업체별 예상보조금 계산.

값 우선순위 (뒤가 앞을 덮어씀)
  national_params (YAML) < 지역 params (YAML) < 사용자 파라미터 파일 (--params) < 업체별 입력 (company_inputs)

각 항목 결과 상태
  계산     : 필요한 값이 모두 있어 금액 산출
  입력필요 : 해당 항목이 적용되지만 값이 없음 (missing 에 필요한 키 표시)
  해당없음 : 입력상 해당 사항 없음 (예: 신규 차량 구입 0대, 노선별 손실 입력 없음)

계산 결과는 '예상치'다. 실제 지급액은 지자체 예산 범위·정산·평가 결과에 따라 달라진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd
import yaml

from .policy import PolicyRegistry, Resolution
from .regions import normalize_sgg, normalize_sido

COMPUTED, NEED_INPUT, NOT_APPLICABLE = "계산", "입력필요", "해당없음"

ROUTE_TYPE_COMPONENT = {"비수익": "unprofitable_route_loss", "벽지": "remote_route_loss"}
ROUTE_RATE_PARAM = {
    "unprofitable_route_loss": "compensation_rate_unprofitable",
    "remote_route_loss": "compensation_rate_remote",
}


@dataclass
class ComponentResult:
    key: str
    status: str
    amount: float = 0.0
    missing: list[str] = field(default_factory=list)
    detail: str = ""


def _num(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


class _Ctx:
    """한 업체의 계산 컨텍스트 — 값 조회와 누락 키 수집."""

    def __init__(self, company: dict, values: dict):
        self.company = company
        self.values = values

    def get(self, key: str) -> float | None:
        return _num(self.values.get(key))

    def need(self, *keys: str) -> tuple[list[float], list[str]]:
        vals = [self.get(k) for k in keys]
        return vals, [k for k, v in zip(keys, vals) if v is None]

    @property
    def buses(self) -> float:
        v = self.get("operating_buses")
        return v if v is not None else float(_num(self.company.get("BUS_HLD_CNTOM")) or 0)


# ----------------------------------------------------------------------
# 항목별 계산식
# ----------------------------------------------------------------------
def calc_fuel(ctx: _Ctx) -> ComponentResult:
    key = "fuel_tax_subsidy"
    liters = ctx.get("fuel_liters")
    if liters is None and ctx.get("fuel_liters_per_bus_year") is not None:
        liters = ctx.get("fuel_liters_per_bus_year") * ctx.buses
    m3 = ctx.get("cng_m3")
    if m3 is None and ctx.get("cng_m3_per_bus_year") is not None:
        m3 = ctx.get("cng_m3_per_bus_year") * ctx.buses

    if liters is None and m3 is None:
        return ComponentResult(key, NEED_INPUT, missing=["fuel_liters 또는 cng_m3 (연료사용량)"])
    amount, missing, parts = 0.0, [], []
    for qty, rate_key, unit in ((liters, "fuel_subsidy_per_liter", "L"), (m3, "cng_subsidy_per_m3", "㎥")):
        if qty is None:
            continue
        rate = ctx.get(rate_key)
        if rate is None:
            missing.append(rate_key)
            continue
        amount += qty * rate
        parts.append(f"{qty:,.0f}{unit}×{rate:,.2f}")
    if missing:
        return ComponentResult(key, NEED_INPUT, missing=missing)
    return ComponentResult(key, COMPUTED, amount, detail=" + ".join(parts))


def calc_routes(ctx: _Ctx, key: str, routes: list[dict]) -> ComponentResult:
    mine = [r for r in routes if ROUTE_TYPE_COMPONENT.get(str(r.get("노선구분", "")).strip()) == key]
    if not mine:
        return ComponentResult(key, NOT_APPLICABLE, detail="노선별 입력 없음")
    rate = ctx.get(ROUTE_RATE_PARAM[key])
    if rate is None:
        return ComponentResult(key, NEED_INPUT, missing=[ROUTE_RATE_PARAM[key]])
    loss, bad = 0.0, []
    for r in mine:
        cost, rev = _num(r.get("연간운행원가")), _num(r.get("연간운송수입"))
        if cost is None or rev is None:
            bad.append(str(r.get("노선번호", "?")))
            continue
        loss += max(0.0, cost - rev)
    if bad:
        return ComponentResult(key, NEED_INPUT, missing=[f"노선 {','.join(bad)} 원가/수입"])
    return ComponentResult(key, COMPUTED, loss * rate, detail=f"{len(mine)}개 노선 결손 {loss:,.0f}×{rate}")


def calc_per_trip(ctx: _Ctx, key: str, trips_key: str, unit_key: str, rate_key: str) -> ComponentResult:
    vals, missing = ctx.need(trips_key, unit_key, rate_key)
    if missing:
        return ComponentResult(key, NEED_INPUT, missing=missing)
    trips, unit, rate = vals
    return ComponentResult(key, COMPUTED, trips * unit * rate, detail=f"{trips:,.0f}건×{unit:,.0f}원×{rate}")


def calc_vehicle(ctx: _Ctx) -> ComponentResult:
    key = "vehicle_purchase"
    n = ctx.get("new_buses")
    if not n:
        return ComponentResult(key, NOT_APPLICABLE, detail="신규 구입 차량 없음")
    per = ctx.get("vehicle_subsidy_per_bus")
    if per is None:
        return ComponentResult(key, NEED_INPUT, missing=["vehicle_subsidy_per_bus"])
    return ComponentResult(key, COMPUTED, n * per, detail=f"{n:,.0f}대×{per:,.0f}")


def calc_semi_public(ctx: _Ctx, res: Resolution, other_operating: float) -> ComponentResult:
    key = "semi_public_deficit"
    share = 1.0
    partial = res.rules.get("partial_param")
    if partial and res.source_entry and res.source_entry.status == "부분시행":
        share = ctx.get(partial)
        if share is None:
            return ComponentResult(key, NEED_INPUT, missing=[f"{partial} (준공영 대상 비율)"])

    buses = ctx.buses
    if buses <= 0:
        return ComponentResult(key, NEED_INPUT, missing=["operating_buses (운행대수)"])
    (unit_cost, days), missing = ctx.need("standard_cost_per_bus_day", "operating_days")
    revenue = ctx.get("annual_revenue")
    if revenue is None:
        per_day = ctx.get("revenue_per_bus_day")
        if per_day is None:
            missing.append("annual_revenue 또는 revenue_per_bus_day")
        elif days is not None:
            revenue = per_day * buses * days
    if missing:
        return ComponentResult(key, NEED_INPUT, missing=missing)

    cost = unit_cost * buses * days
    profit = (ctx.get("performance_profit_per_bus_year") or 0.0) * buses
    revenue_total = revenue
    if res.rules.get("revenue_includes_other_subsidies"):
        revenue_total += other_operating
    deficit = max(0.0, cost + profit - revenue_total) * share
    detail = (f"원가 {cost:,.0f}(={unit_cost:,.0f}×{buses:,.0f}대×{days:,.0f}일)"
              f" + 성과이윤 {profit:,.0f} − 수입 {revenue_total:,.0f}")
    if share != 1.0:
        detail += f", 대상비율 {share}"
    return ComponentResult(key, COMPUTED, deficit, detail=detail)


# ----------------------------------------------------------------------
# 입력 파일 로딩
# ----------------------------------------------------------------------
def load_user_params(path: str | Path | None) -> dict:
    """사용자 파라미터 YAML: {national: {...}, regions: {'서울특별시': {...}, '경기도/용인시': {...}}}"""
    if not path:
        return {"national": {}, "regions": {}}
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    regions = {}
    for k, v in (raw.get("regions") or {}).items():
        sido, _, sgg = str(k).partition("/")
        regions[(normalize_sido(sido), normalize_sgg(sgg))] = v or {}
    return {"national": raw.get("national") or {}, "regions": regions}


def load_table(path: str | Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    p = Path(path)
    if p.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(p)
    return pd.read_csv(p, encoding="utf-8-sig")


def _company_key(name, sido) -> tuple[str, str]:
    return (str(name or "").strip(), normalize_sido(sido))


# ----------------------------------------------------------------------
# 메인 계산
# ----------------------------------------------------------------------
def estimate_company(
    company: dict,
    registry: PolicyRegistry,
    user_params: dict | None = None,
    company_input: dict | None = None,
    routes: Iterable[dict] = (),
) -> dict:
    user_params = user_params or {"national": {}, "regions": {}}
    res = registry.resolve(company.get("CTPV_NM", ""), company.get("SGG_NM", ""), company.get("OPER_SE_NM", ""))

    values = dict(res.params)
    values.update({k: v for k, v in user_params["national"].items() if v is not None})
    for key in ((res.sido, ""), (res.sido, res.sgg)):
        values.update({k: v for k, v in user_params["regions"].get(key, {}).items() if v is not None})
    values.update({k: v for k, v in (company_input or {}).items() if _num(v) is not None})
    ctx = _Ctx(company, values)
    routes = list(routes)

    results: dict[str, ComponentResult] = {}
    for comp in res.components:
        if comp == "fuel_tax_subsidy":
            results[comp] = calc_fuel(ctx)
        elif comp in ROUTE_RATE_PARAM:
            results[comp] = calc_routes(ctx, comp, routes)
        elif comp == "transfer_discount_loss":
            results[comp] = calc_per_trip(ctx, comp, "transfer_trips", "transfer_loss_per_trip", "transfer_compensation_rate")
        elif comp == "fare_discount_loss":
            results[comp] = calc_per_trip(ctx, comp, "discount_trips", "discount_amount_per_trip", "fare_discount_compensation_rate")
        elif comp == "vehicle_purchase":
            results[comp] = calc_vehicle(ctx)
    if "semi_public_deficit" in res.components:
        # 운행 관련 보조금(유가보조금 등)은 준공영 수입금에 포함될 수 있으므로 먼저 계산 후 반영
        operating = sum(r.amount for k, r in results.items() if k != "vehicle_purchase" and r.status == COMPUTED)
        results["semi_public_deficit"] = calc_semi_public(ctx, res, operating)

    row = {
        "업체명": company.get("BZENTY_NM", ""),
        "시도": res.sido,
        "시군구": res.sgg,
        "운영구분": company.get("OPER_SE_NM", ""),
        "버스보유대수": company.get("BUS_HLD_CNTOM", ""),
        "적용운행대수": ctx.buses,
        "준공영제적용": "Y" if res.semi_public else "N",
        "지역준공영제상태": res.semi_public_status,
        "운영방식": res.semi_public_model,
    }
    total, missing, statuses = 0.0, [], []
    for comp in res.components:
        r = results[comp]
        name = registry.components.get(comp, {}).get("name", comp)
        row[f"{name}"] = round(r.amount) if r.status == COMPUTED else None
        row[f"{name}_상태"] = r.status
        statuses.append(r.status)
        if r.status == COMPUTED:
            total += r.amount
        missing += [f"{name}: {m}" for m in r.missing]
    row["예상보조금합계"] = round(total)
    row["산정완료"] = "Y" if NEED_INPUT not in statuses else "N(부분)"
    row["입력필요항목"] = " | ".join(missing)
    row["산식"] = " | ".join(f"{registry.components.get(k, {}).get('name', k)}: {r.detail}"
                           for k, r in results.items() if r.detail)
    row["비고"] = " | ".join(res.notes)
    row["근거조례"] = " | ".join(res.ordinances)
    return row


def estimate_all(
    corps: pd.DataFrame,
    registry: PolicyRegistry,
    user_params: dict | None = None,
    company_inputs: pd.DataFrame | None = None,
    route_inputs: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """업체 DataFrame(버스업체 표준데이터 컬럼) 전체에 대해 예상보조금 계산."""
    ci: dict[tuple[str, str], dict] = {}
    if company_inputs is not None and not company_inputs.empty:
        for rec in company_inputs.to_dict("records"):
            ci[_company_key(rec.get("업체명"), rec.get("시도"))] = rec
    ri: dict[tuple[str, str], list[dict]] = {}
    if route_inputs is not None and not route_inputs.empty:
        for rec in route_inputs.to_dict("records"):
            ri.setdefault(_company_key(rec.get("업체명"), rec.get("시도")), []).append(rec)

    rows = []
    for c in corps.to_dict("records"):
        key = _company_key(c.get("BZENTY_NM"), c.get("CTPV_NM"))
        rows.append(estimate_company(c, registry, user_params, ci.get(key), ri.get(key, [])))
    return pd.DataFrame(rows)
