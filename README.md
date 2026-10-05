# 전국 노선버스 업체·노선·보조금 아카이브

전국 노선버스 업체 정보와 노선별 운행정보를 수집하고, **지자체별 준공영제 시행여부와 보조금 지급 구조**를 정리한 뒤 **업체별 예상보조금**을 계산합니다.

| 단계 | 명령 | 데이터 출처 |
|---|---|---|
| ① 업체 정보 수집 | `python -m buscorp corps` | 공공데이터포털 [전국버스업체표준데이터](https://www.data.go.kr/data/15139231/standard.do) (`tn_pubr_public_bus_corp_api`) |
| ② 노선별 운행정보 | `python -m buscorp routes --city 25` | 국토교통부 (TAGO) 버스노선정보 `BusRouteInfoInqireService` |
| ③ 준공영제 시행여부 점검 | `python -m buscorp ordinances` | 국가법령정보 OPEN API (자치법규) |
| ④ 보조금 구조·준공영제 현황표 | `python -m buscorp policy` | `data/subsidy_policies.yaml` (레지스트리) |
| ⑤ 예상보조금 계산 | `python -m buscorp estimate --params ...` | ①+④+사용자 입력값 |
| 전체 | `python -m buscorp all --params ...` | ① → ④ → ⑤ |

---

## 설치 / API 키

```bash
pip install -r requirements.txt
export PUBLIC_DATA_API_KEY="data.go.kr 일반 인증키(Decoding)"   # ①② 공용
export LAW_API_OC="open.law.go.kr 에 등록한 OC(이메일 ID)"       # ③
export BUSCORP_OUTPUT_DIR=./bus_corp_archive                     # 선택
```

data.go.kr 에서 **전국버스업체표준데이터**, **국토교통부_(TAGO)_버스노선정보** 두 API를 각각 활용신청해야 합니다.

---

## ② 노선별 운행정보

```bash
python -m buscorp routes --city 25 22        # 도시코드 지정 (생략 시 TAGO 전 도시)
python -m buscorp routes --city 25 --stops   # 경유 정류소 수까지 (호출량 많음)
```

수집 항목: 노선ID·번호·유형, 기·종점, 첫·막차, 배차간격(평일/토/일), 경유정류소 수, **1일 운행횟수 추정**(배차간격·운행시간 기반).

**한계**
- TAGO 는 서울·경기 노선을 온전히 제공하지 않음 → 서울시 버스노선정보 API, 경기도 GBIS API 수집기 추가 필요
- TAGO 응답에 **운수업체명이 없음** → 업체-노선 연결은 지자체 노선현황 자료로 `route_inputs` 를 작성해 보완

---

## ③④ 준공영제 시행여부 · 보조금 지급 구조

`data/subsidy_policies.yaml` 이 지자체별 정책의 단일 기준입니다. 2026-10-05 법령정보센터 자치법규 검색 결과로 초기 구축했습니다.

| 시도 | 준공영제 | 비고 |
|---|---|---|
| 서울·부산·대구·인천·광주·대전 | 시행 (시내버스, 수입금공동관리형) | 각 시 준공영제 운영 조례 |
| 제주 | 시행 (노선버스) | 버스 준공영제 운영 조례 |
| 경기 | 부분시행 (공공관리제: 공공지원형·노선입찰형) | 협약노선만 → 업체별 `semi_public_bus_share` 입력 |
| 충북 청주시 · 경남 창원시 | 시행 (시내버스) | 시군 조례 |
| 경기 용인·양주, 강원 춘천 (노선버스) / 경기 광명·파주·하남 (마을버스) | 조례제정 | **시행 여부 확인 필요** |
| 울산 · 세종 | 확인필요 | 준공영제 조례 미검색, 세종은 공영(도시교통공사) 병행 |
| 그 외 도 | 미시행 | 비수익·벽지노선 손실보상 등 개별 재정지원 |

- 상태값: `시행` / `부분시행` / `조례제정` / `미시행` / `확인필요`
- 모든 항목이 `verified: false` 상태입니다. **조례가 있다고 시행 중인 것은 아니므로**, 지자체 고시·보도자료로 확인한 뒤 `verified: true` 로 바꾸세요. `since`(도입연월)도 참고값입니다.
- 2026년 광주·전남 통합(전남광주통합특별시)은 별도 확인 전까지 구 광주 자치구 → 광주 정책, 그 외 → 전남 정책으로 매핑합니다 (`buscorp/regions.py`).
- `ordinances` 명령은 조례 검색 결과를 레지스트리와 대조해 `신규후보`(조례는 있는데 미등록) / `조례미검색`(등록됐는데 조례 없음)을 `semi_public_check_*.csv` 로 출력합니다. 월 1회 돌려 레지스트리를 갱신하세요.

### 보조금 항목 (계산식)

| 항목 | 적용 | 산식 |
|---|---|---|
| 준공영제 재정지원 | 준공영 지역 | max(0, 표준운송원가(원/대·일)×운행대수×운행일수 + 성과이윤 − 운송수입) × 대상비율 |
| 비수익노선 손실보상 | 비준공영 | Σ노선 max(0, 운행원가 − 운송수입) × 보전율 |
| 벽지노선 손실보상 | 비준공영 | 위와 동일 (벽지 보전율) |
| 환승할인 손실보전 | 비준공영 | 환승건수 × 건당 손실액 × 보전율 |
| 요금할인·무료 손실보전 | 비준공영 | 할인건수 × 건당 할인액 × 보전율 |
| 유가보조금 | 공통 | 경유 L × 단가 + CNG ㎥ × 단가 (여객자동차법 제50조⑤⑥) |
| 차량 구입 보조 | 공통 | 구입대수 × 대당 보조액 |

준공영 지역은 수입금에 보조금이 포함되는 구조(예: 경기도 조례 제2조제5호)를 반영해, 유가보조금 등 운행 보조금만큼 적자보전액을 차감합니다(`revenue_includes_other_subsidies`).

---

## ⑤ 예상보조금 계산

```bash
python -m buscorp estimate \
  --params examples/params_example.yaml \
  --company-inputs examples/company_inputs_example.csv \
  --route-inputs examples/route_inputs_example.csv
```

값 우선순위: `national_params` < 지역 `params`(YAML) < `--params` < `--company-inputs`

- **단가는 추정해서 채우지 않습니다.** 값이 없으면 해당 항목은 `입력필요` 로 표시되고 합계에서 빠지며, `입력필요항목` 컬럼에 필요한 키가 나옵니다.
- `examples/params_example.yaml` 의 숫자는 **흐름 확인용 임의값**입니다. 실제로는 지자체 표준운송원가 고시·예산서, 국토부 유가보조금 단가 고시 등으로 바꿔 넣으세요.
- 업체 매칭 키: `업체명` + `시도`

출력: `subsidy_estimate_*.csv` (업체별 항목·합계·산식·근거조례), `subsidy_estimate_by_region_*.csv` (시도×준공영 여부 합계)

---

## 저장 구조

```
./bus_corp_archive/
├── bus_corps_YYYYMMDD.csv / bus_corps_latest.csv / bus_corps_latest.json
├── summary_YYYYMMDD.csv              # 시도별 업체·노선·차량 요약
├── routes_YYYYMMDD.csv               # 노선 운행정보
├── ordinances_YYYYMMDD.csv           # 조례 검색 결과
├── semi_public_check_YYYYMMDD.csv    # 조례 ↔ 레지스트리 대조
├── semi_public_status_YYYYMMDD.csv   # 지자체별 준공영제 현황표
├── subsidy_estimate_YYYYMMDD.csv     # 업체별 예상보조금
└── archive_log.csv                   # 실행 이력
```

## 자동 실행 / 테스트

```bash
python bus_corp_archive.py --schedule   # 매월 1일 09:00 `all` 실행 (구 진입점, `corps` 단독 실행도 지원)
python -m pytest -q
```

## 관련 법령

- 여객자동차 운수사업법 제50조 (재정 지원: 수익성 없는 노선, 유류세 인상분, CNG 등)
- 대중교통의 육성 및 이용촉진에 관한 법률
- 각 지자체 시내버스 준공영제 운영 조례 / 여객자동차 운수사업 재정지원 조례 (`data/subsidy_policies.yaml` 참고)
