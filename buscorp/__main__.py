"""CLI: python -m buscorp <command>

  corps        전국 버스업체 표준데이터 수집
  routes       TAGO 노선별 운행정보 수집 (--city 도시코드, 생략 시 전체)
  ordinances   준공영제·재정지원 조례 검색 → 레지스트리 대조표
  policy       레지스트리 검증 + 지자체별 준공영제 현황표 출력
  estimate     업체별 예상보조금 계산
  all          corps → policy → estimate 순차 실행
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config
from .collectors import bus_corps, ordinances, routes
from .policy import PolicyRegistry
from .subsidy import estimate_all, load_table, load_user_params


def _out(name: str) -> Path:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return config.OUTPUT_DIR / name


def _save(df: pd.DataFrame, stem: str, *, latest: bool = True) -> Path:
    today = datetime.now().strftime("%Y%m%d")
    path = _out(f"{stem}_{today}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    if latest:
        df.to_csv(_out(f"{stem}_latest.csv"), index=False, encoding="utf-8-sig")
    print(f"  저장: {path} ({len(df)}건)")
    return path


def _log(task: str, count: int, path: Path) -> None:
    log = _out("archive_log.csv")
    new = not log.exists()
    with open(log, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["실행일시", "작업", "건수", "파일"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task, count, str(path)])


# ----------------------------------------------------------------------
def cmd_corps(args) -> pd.DataFrame:
    key = config.require(config.PUBLIC_DATA_API_KEY, "PUBLIC_DATA_API_KEY")
    items = bus_corps.fetch(key)
    if not items:
        raise SystemExit("[실패] 수집된 버스업체 데이터가 없습니다.")
    df = bus_corps.to_frame(items)
    path = _save(df, "bus_corps")
    with open(_out("bus_corps_latest.json"), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    summary = bus_corps.summarize(df)
    _save(summary, "summary", latest=False)
    _log("corps", len(df), path)
    print(summary.to_string(index=False))
    return df


def cmd_routes(args) -> None:
    key = config.require(config.PUBLIC_DATA_API_KEY, "PUBLIC_DATA_API_KEY")
    df = routes.collect(key, args.city or None, with_detail=not args.no_detail, with_stops=args.stops)
    df["1일운행횟수(추정)"] = df.apply(routes.daily_trips_estimate, axis=1)
    path = _save(df, "routes")
    _log("routes", len(df), path)


def cmd_ordinances(args) -> None:
    oc = config.require(config.LAW_API_OC, "LAW_API_OC")
    reg = PolicyRegistry.load(args.policy)
    df = ordinances.collect(oc)
    path = _save(df, "ordinances")
    diff = ordinances.diff_with_registry(df, reg)
    _save(diff, "semi_public_check")
    _log("ordinances", len(df), path)
    todo = diff[diff["상태"] != "일치"]
    if not todo.empty:
        print("\n[확인 필요 — data/subsidy_policies.yaml 갱신 검토]")
        print(todo.to_string(index=False))


def cmd_policy(args) -> None:
    reg = PolicyRegistry.load(args.policy)
    errors = reg.validate()
    for e in errors:
        print(f"  [레지스트리 오류] {e}")
    df = pd.DataFrame(reg.status_table())
    _save(df, "semi_public_status")
    print(df[["시도", "시군구", "준공영제상태", "대상", "운영방식", "확인여부"]].to_string(index=False))
    if errors:
        raise SystemExit(1)


def cmd_estimate(args, corps: pd.DataFrame | None = None) -> None:
    reg = PolicyRegistry.load(args.policy)
    if corps is None:
        src = Path(args.corps) if args.corps else _out("bus_corps_latest.csv")
        if not src.exists():
            raise SystemExit(f"[오류] 업체 파일이 없습니다: {src} — 먼저 `python -m buscorp corps` 실행")
        corps = load_table(src)
    df = estimate_all(
        corps,
        reg,
        load_user_params(args.params),
        load_table(args.company_inputs),
        load_table(args.route_inputs),
    )
    path = _save(df, "subsidy_estimate")
    _log("estimate", len(df), path)

    by_region = (
        df.groupby(["시도", "준공영제적용"])
        .agg(업체수=("업체명", "count"), 예상보조금합계=("예상보조금합계", "sum"),
             산정완료업체=("산정완료", lambda s: (s == "Y").sum()))
        .reset_index()
    )
    _save(by_region, "subsidy_estimate_by_region")
    print(by_region.to_string(index=False))
    incomplete = (df["산정완료"] != "Y").sum()
    if incomplete:
        print(f"\n※ {incomplete}개 업체는 입력값이 부족해 일부 항목만 계산됨 — '입력필요항목' 컬럼 참고")


def cmd_all(args) -> None:
    corps = cmd_corps(args)
    cmd_policy(args)
    cmd_estimate(args, corps)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="buscorp", description="전국 노선버스 업체·노선·보조금 아카이빙")
    p.add_argument("--policy", default=str(config.POLICY_FILE), help="정책 레지스트리 YAML")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("corps", help="버스업체 표준데이터 수집")

    r = sub.add_parser("routes", help="TAGO 노선 운행정보 수집")
    r.add_argument("--city", nargs="*", help="TAGO 도시코드 (예: 25 대전, 22 대구)")
    r.add_argument("--no-detail", action="store_true", help="노선 상세(배차간격) 조회 생략")
    r.add_argument("--stops", action="store_true", help="경유 정류소 수까지 조회 (호출량 많음)")

    sub.add_parser("ordinances", help="준공영제 조례 점검")
    sub.add_parser("policy", help="레지스트리 검증·현황표")

    for name in ("estimate", "all"):
        e = sub.add_parser(name, help="예상보조금 계산" if name == "estimate" else "전체 실행")
        if name == "estimate":
            e.add_argument("--corps", help="업체 CSV (기본: bus_corps_latest.csv)")
        e.add_argument("--params", help="사용자 단가 파라미터 YAML (examples/params_example.yaml 참고)")
        e.add_argument("--company-inputs", help="업체별 입력 CSV/XLSX (examples/company_inputs_example.csv)")
        e.add_argument("--route-inputs", help="노선별 원가·수입 CSV/XLSX (examples/route_inputs_example.csv)")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    handler = {
        "corps": cmd_corps, "routes": cmd_routes, "ordinances": cmd_ordinances,
        "policy": cmd_policy, "estimate": cmd_estimate, "all": cmd_all,
    }[args.cmd]
    handler(args)


if __name__ == "__main__":
    sys.exit(main())
