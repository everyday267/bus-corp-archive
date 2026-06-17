# 전국 노선버스업체 공공데이터 아카이빙

공공데이터포털의 **전국버스업체표준데이터** API를 활용하여 전국 노선버스업체 정보를 월 1회 자동 수집·저장하는 스크립트입니다.

- **데이터 출처**: [공공데이터포털 - 전국버스업체표준데이터](https://www.data.go.kr/data/15139231/standard.do)
- **API**: `tn_pubr_public_bus_corp_api`
- **수집 항목**: 업체명, 시도명, 시군구명, 소재지, 운영구분, 노선수, 버스보유대수, 전화번호, 대표자명, 총직원수

---

## 설치

```bash
pip install -r requirements.txt
```

## API 키 설정

1. [data.go.kr](https://www.data.go.kr) 회원가입 및 로그인
2. `tn_pubr_public_bus_corp_api` 검색 → **활용신청** (자동승인)
3. 마이페이지에서 서비스키 확인
4. 환경변수 설정:

```bash
export PUBLIC_DATA_API_KEY="발급받은_서비스키"
```

> `.env` 파일 사용 시 `python-dotenv` 설치 후 활용 가능

---

## 사용법

### 즉시 1회 실행

```bash
python bus_corp_archive.py
```

### 매월 1일 자동 실행 (스케줄러)

```bash
python bus_corp_archive.py --schedule
```

---

## 저장 구조

```
./bus_corp_archive/
├── bus_corps_20260617.csv   # 날짜별 스냅샷
├── bus_corps_latest.csv     # 항상 최신본
├── bus_corps_latest.json
├── summary_20260617.csv     # 시도별 요약 통계
└── archive_log.csv          # 실행 이력 로그
```

| 파일 | 설명 |
|---|---|
| `bus_corps_YYYYMMDD.csv` | 날짜별 전체 데이터 스냅샷 |
| `bus_corps_latest.csv` | 가장 최근 수집본 (항상 덮어씀) |
| `summary_YYYYMMDD.csv` | 시도별 업체수·버스대수 요약 |
| `archive_log.csv` | 수집 일시·건수 누적 로그 |

---

## 관련 법령

- 여객자동차 운수사업법 제4조
- 지방자치단체 버스 운영 조례
