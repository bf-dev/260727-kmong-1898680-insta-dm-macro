# -*- coding: utf-8 -*-
"""Artifacts API 리포터 - 절대 프로그램을 막거나 죽이면 안 된다(전부 백그라운드+catch-all).

- remote_log(event, detail): 짧은 진단 한 줄, 이벤트별 디바운스.
- upload_run(summary, zip_path): 실행 1회당 요약 텍스트 + (선택) 무거운 덤프 ZIP.
엔드포인트는 customerId/source/text/file(s) 만 읽는다. 다른 키는 버려지므로 event/ts 등은
전부 text 문자열에 접어 넣는다.
"""

import threading
import time

import requests

import config

WORKS_API = config.WORKS_API
_SOURCE_BASE = f"{config.APP_NAME}-v{config.APP_VERSION}"

_last = {}
_lock = threading.Lock()
_DEBOUNCE = 10  # seconds


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def remote_log(event, detail="", force=False):
    """짧은 진단 한 줄을 백그라운드로 전송. 이벤트별 디바운스(같은 이벤트 10초 내 재전송 안 함)."""
    with _lock:
        if not force and (time.time() - _last.get(event, 0)) < _DEBOUNCE:
            return
        _last[event] = time.time()

    text = f"[{_now()}] customer={config.CUSTOMER_ID} [{event}] {detail}".strip()[:6000]

    def _send():
        try:
            requests.post(
                WORKS_API,
                json={"customerId": config.CUSTOMER_ID,
                      "source": f"{_SOURCE_BASE}-diag", "text": text},
                timeout=8,
            )
        except Exception:
            pass  # 리포팅 실패가 앱에 영향을 주면 안 된다.

    threading.Thread(target=_send, daemon=True).start()


def upload_run(summary, zip_path=None, kind="run"):
    """실행 1회 리포트: 짧은 요약 + (선택) ZIP(로그/스크린샷/페이지 덤프). 백그라운드, best-effort."""
    source = f"{config.APP_NAME}-{kind}"
    text = f"[{_now()}] customer={config.CUSTOMER_ID} {summary}".strip()[:6000]

    def _send():
        try:
            data = {"customerId": config.CUSTOMER_ID, "source": source, "text": text}
            if zip_path:
                with open(zip_path, "rb") as fh:
                    files = {"file": (zip_path.split("/")[-1].split("\\")[-1],
                                      fh.read(), "application/zip")}
                requests.post(WORKS_API, data=data, files=files, timeout=30)
            else:
                requests.post(WORKS_API, json=data, timeout=15)
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()
