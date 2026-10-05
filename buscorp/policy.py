"""지자체별 준공영제·보조금 정책 레지스트리 (data/subsidy_policies.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import POLICY_FILE
from .regions import normalize_sgg, normalize_sido, policy_sido

SEMI_PUBLIC_ACTIVE = {"시행", "부분시행"}
VALID_STATUS = {"시행", "부분시행", "조례제정", "미시행", "확인필요"}


def normalize_scope(oper_se: str | None) -> str:
    """버스업체 운영구분명 → 레지스트리 scope 값."""
    s = str(oper_se or "")
    for key, scope in (("마을", "마을버스"), ("농어촌", "농어촌버스"), ("광역", "광역버스"),
                       ("시외", "시외버스"), ("고속", "고속버스"), ("시내", "시내버스")):
        if key in s:
            return scope
    return ""


def scope_matches(entry_scopes: list[str], company_scope: str) -> bool:
    if not company_scope:
        return True  # 운영구분 미상이면 지역 제도를 그대로 적용하되 결과에 표시
    if company_scope in entry_scopes:
        return True
    # '노선버스'는 시내·농어촌·마을 노선을 포괄 (시외·고속은 시·도 준공영제 대상 아님)
    return "노선버스" in entry_scopes and company_scope in {"시내버스", "농어촌버스", "마을버스"}


@dataclass
class RegionPolicy:
    sido: str
    sgg: str = ""
    semi_public: dict = field(default_factory=dict)
    subsidy_rules: dict = field(default_factory=dict)
    subsidy_ordinances: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    notes: str = ""

    @property
    def status(self) -> str:
        return self.semi_public.get("status", "확인필요")

    @property
    def scopes(self) -> list[str]:
        return list(self.semi_public.get("scope", []))

    def is_semi_public_for(self, company_scope: str) -> bool:
        return self.status in SEMI_PUBLIC_ACTIVE and scope_matches(self.scopes, company_scope)


@dataclass
class Resolution:
    """특정 업체(시도·시군구·운영구분)에 적용되는 정책 해석 결과."""

    sido: str
    sgg: str
    company_scope: str
    semi_public: bool
    semi_public_status: str
    semi_public_model: str
    source_entry: RegionPolicy | None
    components: list[str]
    params: dict
    rules: dict
    ordinances: list[str]
    notes: list[str]


class PolicyRegistry:
    def __init__(self, raw: dict):
        self.raw = raw
        self.components: dict = raw.get("components", {})
        self.national_params: dict = raw.get("national_params", {}) or {}
        self.default_components: dict = raw.get("default_components", {})
        self.entries: list[RegionPolicy] = [
            RegionPolicy(
                sido=normalize_sido(r["sido"]),
                sgg=normalize_sgg(r.get("sgg")),
                semi_public=r.get("semi_public", {}) or {},
                subsidy_rules=r.get("subsidy_rules", {}) or {},
                subsidy_ordinances=r.get("subsidy_ordinances", []) or [],
                params=r.get("params", {}) or {},
                notes=r.get("notes", "") or "",
            )
            for r in raw.get("regions", [])
        ]

    @classmethod
    def load(cls, path: str | Path = POLICY_FILE) -> "PolicyRegistry":
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f))

    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        errors = []
        seen = set()
        for e in self.entries:
            key = (e.sido, e.sgg)
            if key in seen:
                errors.append(f"중복 항목: {key}")
            seen.add(key)
            if e.status not in VALID_STATUS:
                errors.append(f"{key}: 잘못된 status '{e.status}'")
            if e.status in SEMI_PUBLIC_ACTIVE and not e.semi_public.get("ordinance"):
                errors.append(f"{key}: 준공영제 시행 지역인데 근거 조례(ordinance) 없음")
        for comps in self.default_components.values():
            for c in comps:
                if c not in self.components:
                    errors.append(f"정의되지 않은 보조금 항목: {c}")
        return errors

    def find(self, sido: str, sgg: str = "") -> tuple[RegionPolicy | None, RegionPolicy | None]:
        """(시도 항목, 시군구 항목) 반환."""
        sido_key = policy_sido(sido, sgg)
        sgg_key = normalize_sgg(sgg)
        sido_e = next((e for e in self.entries if e.sido == sido_key and not e.sgg), None)
        sgg_e = next((e for e in self.entries if e.sido == sido_key and e.sgg and e.sgg == sgg_key), None)
        return sido_e, sgg_e

    def resolve(self, sido: str, sgg: str = "", oper_se: str = "") -> Resolution:
        sido_e, sgg_e = self.find(sido, sgg)
        scope = normalize_scope(oper_se)
        notes: list[str] = []

        # 시군구 항목이 업체 운영구분에 해당하면 시군구 제도가 우선, 아니면 시도 제도
        chosen = None
        for e in (sgg_e, sido_e):
            if e and e.is_semi_public_for(scope):
                chosen = e
                break
        semi = chosen is not None
        status_src = chosen or sgg_e or sido_e

        if sido_e is None and sgg_e is None:
            notes.append(f"레지스트리에 '{policy_sido(sido, sgg)}' 항목 없음 — data/subsidy_policies.yaml 보완 필요")
        if not scope:
            notes.append("운영구분 미상 — 지역 제도를 그대로 적용")
        for e in (sgg_e, sido_e):
            if e and e.status in {"조례제정", "확인필요"} and scope_matches(e.scopes, scope):
                notes.append(f"{e.sido} {e.sgg}".strip() + f": 준공영제 '{e.status}' — 시행 여부 확인 후 레지스트리 갱신")

        params = dict(self.national_params)
        rules: dict = {}
        ordinances: list[str] = []
        for e in (sido_e, sgg_e):  # 시군구 값이 시도 값을 덮어씀
            if e:
                params.update({k: v for k, v in e.params.items() if v is not None or k not in params})
                rules.update(e.subsidy_rules)
                ordinances += e.subsidy_ordinances
                if e.semi_public.get("ordinance"):
                    ordinances.append(e.semi_public["ordinance"])

        components = list(self.default_components.get("semi_public" if semi else "standard", []))
        return Resolution(
            sido=policy_sido(sido, sgg),
            sgg=normalize_sgg(sgg),
            company_scope=scope,
            semi_public=semi,
            semi_public_status=status_src.status if status_src else "확인필요",
            semi_public_model=(chosen.semi_public.get("model", "") if chosen else ""),
            source_entry=chosen,
            components=components,
            params=params,
            rules=rules,
            ordinances=list(dict.fromkeys(ordinances)),
            notes=notes,
        )

    # ------------------------------------------------------------------
    def semi_public_entries(self) -> list[RegionPolicy]:
        return [e for e in self.entries if e.semi_public.get("ordinance")]

    def key_from_jurisdiction(self, jurisdiction: str) -> tuple[str, str]:
        """법령DB 지자체명('경기도 광명시', '(구)광주광역시') → (시도, 시군구) 레지스트리 키."""
        parts = re.sub(r"\(구\)", "", jurisdiction or "").split()
        if not parts:
            return ("", "")
        sgg = normalize_sgg(" ".join(parts[1:])) if len(parts) > 1 else ""
        return (policy_sido(parts[0], sgg), sgg)

    def status_table(self) -> list[dict]:
        rows = []
        for e in self.entries:
            sp = e.semi_public
            rows.append({
                "시도": e.sido,
                "시군구": e.sgg,
                "준공영제상태": e.status,
                "대상": ",".join(e.scopes),
                "운영방식": sp.get("model", ""),
                "도입(참고)": sp.get("since", ""),
                "근거조례": sp.get("ordinance", ""),
                "조례시행일": sp.get("ordinance_effective", ""),
                "확인여부": "확인" if sp.get("verified") else "미확인",
                "근거·비고": sp.get("evidence", "") or e.notes,
                "재정지원조례수": len(e.subsidy_ordinances),
            })
        return rows
