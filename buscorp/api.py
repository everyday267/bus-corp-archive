"""공공데이터포털(data.go.kr) 공통 호출 유틸리티.

data.go.kr API는 대부분 `response.header / response.body.items` 구조이지만
서비스마다 `items`가 list, dict, `{"item": [...]}`, `{"item": {...}}`, 빈 문자열 등으로
제각각 내려오므로 여기서 한 번에 정규화한다.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator

import requests

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
SUCCESS_CODES = {"00", "0", "000", "NORMAL_CODE", "INFO-000"}


class ApiError(RuntimeError):
    pass


def normalize_items(items: Any) -> list[dict]:
    """`items` 필드를 항상 list[dict] 로 변환."""
    if items in (None, "", []):
        return []
    if isinstance(items, dict):
        if "item" in items:
            return normalize_items(items["item"])
        return [items]
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict)]
    return []


def parse_response(data: dict) -> tuple[list[dict], int]:
    """data.go.kr JSON 응답에서 (items, totalCount) 추출. 오류코드면 ApiError."""
    resp = data.get("response", data)
    header = resp.get("header", {}) or {}
    code = str(header.get("resultCode", "00"))
    if code not in SUCCESS_CODES:
        raise ApiError(f"API 오류 {code}: {header.get('resultMsg', 'Unknown error')}")
    body = resp.get("body", {}) or {}
    items = normalize_items(body.get("items"))
    total = int(body.get("totalCount") or len(items))
    return items, total


def get_json(
    url: str,
    params: dict,
    *,
    session: requests.Session | None = None,
    retries: int = MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """GET + JSON 파싱. 타임아웃·연결오류·5xx 는 지수 백오프로 최대 `retries` 회 재시도."""
    http = session or requests
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = http.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            if r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            r.raise_for_status()
            try:
                return r.json()
            except ValueError as e:
                # 키 오류 시 data.go.kr 은 XML(OpenAPI_ServiceResponse)로 응답한다
                raise ApiError(f"JSON 이 아닌 응답(서비스키/파라미터 확인 필요): {r.text[:200]}") from e
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last_exc = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and status < 500:
                break
            if attempt < retries:
                sleep(2 ** (attempt + 1))
    raise ApiError(f"요청 실패: {url} ({last_exc})")


def iter_pages(
    url: str,
    params: dict,
    *,
    page_size: int = 1000,
    page_param: str = "pageNo",
    size_param: str = "numOfRows",
    delay: float = 0.3,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[list[dict]]:
    """페이지네이션을 따라가며 페이지별 items 를 yield."""
    page = 1
    fetched = 0
    while True:
        q = {**params, page_param: page, size_param: page_size}
        items, total = parse_response(get_json(url, q, session=session, sleep=sleep))
        if not items:
            return
        fetched += len(items)
        yield items
        if fetched >= total or len(items) < page_size:
            return
        page += 1
        sleep(delay)
