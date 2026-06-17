#!/usr/bin/env python3
"""
전국 노선버스업체 공공데이터 아카이빙 스크립트
공공데이터포털 API: tn_pubr_public_bus_corp_api
갱신 주기: 월 1회 권장
"""

import requests
import json
import csv
import os
import time
from datetime import datetime
import pandas as pd

# ============================================================
# 설정
# ============================================================
API_KEY = os.environ.get("PUBLIC_DATA_API_KEY", "YOUR_API_KEY_HERE")  # 환경변수 우선
BASE_URL = "https://api.data.go.kr/openapi/tn_pubr_public_bus_corp_api"
OUTPUT_DIR = "./bus_corp_archive"
MAX_ROWS = 1000  # API 최대값


def fetch_all_bus_corps(api_key: str) -> list[dict]:
    """전국 버스업체 데이터 전체 수집 (페이지네이션)"""
    all_items = []
    page_no = 1

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 데이터 수집 시작...")

    while True:
        params = {
            "serviceKey": api_key,
            "pageNo": page_no,
            "numOfRows": MAX_ROWS,
            "type": "json",
        }

        try:
            response = requests.get(BASE_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            # 응답 구조 파싱
            response_body = data.get("response", {})
            header = response_body.get("header", {})
            result_code = header.get("resultCode", "")

            if result_code != "00":
                error_msg = header.get("resultMsg", "Unknown error")
                print(f"  [오류] API 에러코드 {result_code}: {error_msg}")
                break

            body = response_body.get("body", {})
            total_count = int(body.get("totalCount", 0))
            items = body.get("items", [])

            # items가 dict인 경우 (단일 item)
            if isinstance(items, dict):
                items = [items]
            elif not isinstance(items, list):
                items = []

            all_items.extend(items)

            fetched = len(all_items)
            print(f"  페이지 {page_no}: {len(items)}건 수집 (누적: {fetched}/{total_count}건)")

            # 종료 조건
            if fetched >= total_count or len(items) == 0:
                print(f"  수집 완료: 총 {fetched}건")
                break

            page_no += 1
            time.sleep(0.3)  # API 호출 간 딜레이

        except requests.exceptions.Timeout:
            print(f"  [오류] 페이지 {page_no} 요청 타임아웃 - 재시도...")
            time.sleep(5)
            continue
        except requests.exceptions.RequestException as e:
            print(f"  [오류] 요청 실패: {e}")
            break
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  [오류] 응답 파싱 실패: {e}")
            break

    return all_items


def save_to_csv(items: list[dict], filepath: str) -> None:
    """데이터를 CSV로 저장"""
    if not items:
        print("  저장할 데이터가 없습니다.")
        return

    fieldnames = [
        "BZENTY_NM",          # 업체명
        "CTPV_NM",            # 시도명
        "SGG_NM",             # 시군구명
        "LCTN_ROAD_NM_ADDR",  # 소재지도로명주소
        "LCTN_LOTNO_ADDR",    # 소재지지번주소
        "OPER_SE_NM",         # 운영구분명
        "RTE_CNT",            # 노선수
        "BUS_HLD_CNTOM",      # 버스보유대수
        "TELNO",              # 전화번호
        "RPRSV_NM",           # 대표자명
        "TNOEMP",             # 총직원수
        "DATA_CRTR_YMD",      # 데이터기준일자
        "instt_code",         # 제공기관코드
        "instt_nm",           # 제공기관명
    ]

    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)

    print(f"  CSV 저장 완료: {filepath}")


def save_to_json(items: list[dict], filepath: str) -> None:
    """데이터를 JSON으로 저장"""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"  JSON 저장 완료: {filepath}")


def generate_summary(items: list[dict]) -> pd.DataFrame:
    """시도별 업체수/버스대수 요약 통계"""
    df = pd.DataFrame(items)

    for col in ["RTE_CNT", "BUS_HLD_CNTOM", "TNOEMP"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).astype(int)

    summary = (
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
    return summary


def archive(api_key: str = API_KEY) -> None:
    """메인 아카이빙 실행 함수"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")

    # 1. 데이터 수집
    items = fetch_all_bus_corps(api_key)

    if not items:
        print("[실패] 수집된 데이터가 없습니다.")
        return

    # 2. 날짜별 스냅샷 저장
    csv_path = os.path.join(OUTPUT_DIR, f"bus_corps_{today}.csv")
    json_path = os.path.join(OUTPUT_DIR, f"bus_corps_{today}.json")
    save_to_csv(items, csv_path)
    save_to_json(items, json_path)

    # 3. 최신본 별도 저장
    latest_csv = os.path.join(OUTPUT_DIR, "bus_corps_latest.csv")
    latest_json = os.path.join(OUTPUT_DIR, "bus_corps_latest.json")
    save_to_csv(items, latest_csv)
    save_to_json(items, latest_json)

    # 4. 요약 통계 저장
    summary = generate_summary(items)
    summary_path = os.path.join(OUTPUT_DIR, f"summary_{today}.csv")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print(f"  요약 통계 저장: {summary_path}")

    # 5. 로그 기록
    log_path = os.path.join(OUTPUT_DIR, "archive_log.csv")
    log_exists = os.path.exists(log_path)
    with open(log_path, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if not log_exists:
            writer.writerow(["실행일시", "수집건수", "CSV파일", "JSON파일"])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            len(items),
            csv_path,
            json_path,
        ])

    print(f"\n[완료] {len(items)}개 업체 아카이빙 완료")
    print(f"  저장 위치: {os.path.abspath(OUTPUT_DIR)}")
    print("\n[시도별 요약]")
    print(summary.to_string(index=False))


def run_scheduler(api_key: str = API_KEY) -> None:
    """
    매월 1일 오전 9시 자동 실행 스케줄러
    실행: python bus_corp_archive.py --schedule
    의존성: pip install schedule
    """
    import schedule

    def job():
        if datetime.now().day == 1:
            print(f"[스케줄러] 월간 아카이빙 시작: {datetime.now():%Y-%m-%d %H:%M}")
            archive(api_key)

    schedule.every().day.at("09:00").do(job)
    print("[스케줄러] 시작 - 매월 1일 09:00 자동 실행")
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--schedule":
        run_scheduler()
    else:
        api_key = os.environ.get("PUBLIC_DATA_API_KEY", API_KEY)
        archive(api_key)
