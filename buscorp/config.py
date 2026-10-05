"""환경설정 (API 키·경로)."""

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
POLICY_FILE = DATA_DIR / "subsidy_policies.yaml"

OUTPUT_DIR = Path(os.environ.get("BUSCORP_OUTPUT_DIR", "./bus_corp_archive"))

# 공공데이터포털 서비스키 (일반 인증키, Decoding 값)
PUBLIC_DATA_API_KEY = os.environ.get("PUBLIC_DATA_API_KEY", "")
# 국가법령정보 공동활용 OPEN API 인증값(OC, 신청 시 등록한 이메일 ID)
LAW_API_OC = os.environ.get("LAW_API_OC", "")


def require(value: str, name: str) -> str:
    if not value:
        raise SystemExit(f"[설정 오류] 환경변수 {name} 가 설정되지 않았습니다. README의 'API 키 설정'을 참고하세요.")
    return value
