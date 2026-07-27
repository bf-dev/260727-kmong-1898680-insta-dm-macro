# -*- coding: utf-8 -*-
"""실패/성공 진단 덤프 - 페이지 HTML + 쿠키(값 마스킹) + 스크린샷을 ZIP 으로 묶는다.

Artifacts API 요구사항(웹 스크레이핑/브라우저 자동화 도구): 페이지 콘텐츠(HTML, 쿠키,
localStorage), 요청 시점의 셀렉터 매칭 결과를 남긴다. 자격증명(비밀번호/토큰 값)은 절대
포함하지 않는다 - 쿠키는 이름만 남기고 값은 길이만 표시한다.
"""

import io
import json
import os
import tempfile
import time
import zipfile


def _redacted_cookies(driver):
    try:
        cookies = driver.get_cookies()
    except Exception:
        return []
    out = []
    for c in cookies:
        out.append({
            "name": c.get("name"),
            "domain": c.get("domain"),
            "value_len": len(str(c.get("value", ""))),
        })
    return out


def capture_zip(driver, label, extra_text=""):
    """현재 페이지 상태를 zip 파일로 만들어 임시경로에 저장하고 그 경로를 반환."""
    try:
        html = driver.page_source
    except Exception:
        html = ""
    cookies = _redacted_cookies(driver)
    try:
        url = driver.current_url
    except Exception:
        url = ""
    try:
        local_storage = driver.execute_script(
            "try{return JSON.stringify(window.localStorage);}catch(e){return '{}';}")
    except Exception:
        local_storage = "{}"

    meta = {
        "label": label,
        "url": url,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cookies": cookies,
        "note": extra_text[:2000],
    }

    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
            z.writestr("page.html", html[:2_000_000])
            z.writestr("local_storage.json", local_storage[:200_000])
            try:
                png = driver.get_screenshot_as_png()
                z.writestr("screenshot.png", png)
            except Exception:
                pass
        return path
    except Exception:
        try:
            os.unlink(path)
        except Exception:
            pass
        return None
