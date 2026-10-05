#!/usr/bin/env python3
"""
전국 노선버스업체 공공데이터 아카이빙 (하위 호환용 진입점)

  python bus_corp_archive.py              # 버스업체 1회 수집 (= python -m buscorp corps)
  python bus_corp_archive.py --schedule   # 매월 1일 09:00 전체 파이프라인 실행

세부 기능은 `python -m buscorp --help` 참고.
"""

import sys
import time
from datetime import datetime

from buscorp.__main__ import main


def run_scheduler() -> None:
    """매월 1일 09:00 corps → policy → estimate 실행. 의존성: pip install schedule"""
    import schedule

    def job():
        if datetime.now().day == 1:
            print(f"[스케줄러] 월간 아카이빙 시작: {datetime.now():%Y-%m-%d %H:%M}")
            try:
                main(["all"])
            except SystemExit as e:
                print(f"[스케줄러] 실행 실패: {e}")

    schedule.every().day.at("09:00").do(job)
    print("[스케줄러] 시작 - 매월 1일 09:00 자동 실행")
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--schedule":
        run_scheduler()
    else:
        main(["corps"])
