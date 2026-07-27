# -*- coding: utf-8 -*-
"""Selenium 드라이버 생성 - 프로그램 전용 크롬 + 일반 크롬 위장 + 계정별 프로필.

- undetected-chromedriver 를 쓰지 않는다(버전 꼬임/크래시 원인). chrome_provisioner 로 받은
  전용 크롬 바이너리를 직접 지정해 구동한다.
- user-agent 는 '평범한 최신 크롬'으로 강제(HeadlessChrome/Testing 흔적 제거),
  navigator.webdriver 등 자동화 흔적을 CDP 로 제거한다(인스타 봇 탐지 회피).
- 계정 라벨별 profile_dir 로 로그인 세션(쿠키)을 분리 유지 -> 계정 전환 = 프로필 전환.
- headless 로 절대 돌리지 않는다: 인스타는 실제 브라우저 UI 를 눈으로 조작하는 게 목적이고
  headless 흔적은 탐지 신호를 더 늘린다. 창은 항상 실제로 뜬다.
"""

import os
import platform
import time

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

from chrome_provisioner import ensure_chrome


def _clear_profile_locks(profile_dir, log=print):
    """이전 실행에서 크래시한 크롬이 남긴 프로필 잠금 파일을 제거한다."""
    if not profile_dir:
        return
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = os.path.join(profile_dir, name)
        try:
            if os.path.lexists(p):
                os.remove(p)
                log(f"이전 크롬 프로필 잠금 제거: {name}")
        except Exception:
            pass


def _normal_user_agent(major):
    system = platform.system()
    if system == "Windows":
        os_token = "Windows NT 10.0; Win64; x64"
    elif system == "Darwin":
        os_token = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        os_token = "X11; Linux x86_64"
    return (
        f"Mozilla/5.0 ({os_token}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


def build_driver(profile_dir, log=print):
    """전용 크롬으로 selenium 드라이버 생성. profile_dir = 계정별 프로필 폴더(세션 유지)."""
    chrome_path, driver_path, version = ensure_chrome(log=log)
    major = version.split(".")[0]
    user_agent = _normal_user_agent(major)

    options = webdriver.ChromeOptions()
    options.binary_location = chrome_path
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--start-maximized")
    options.add_argument("--lang=ko-KR")
    options.add_argument(f"--user-agent={user_agent}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-infobars")
    options.add_argument("--password-store=basic")
    if platform.system() != "Windows":
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_experimental_option("prefs", {
        "intl.accept_languages": "ko-KR,ko",
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,  # 알림 팝업 차단
    })
    options.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
    options.add_experimental_option(
        "perfLoggingPrefs", {"enableNetwork": True, "enablePage": True})

    service = Service(executable_path=driver_path)

    driver = None
    last_err = None
    for attempt in range(3):
        _clear_profile_locks(profile_dir, log=log)
        try:
            driver = webdriver.Chrome(service=service, options=options)
            break
        except Exception as e:
            last_err = e
            log(f"크롬 기동 실패({attempt + 1}/3): {e}")
            time.sleep(2.0 + attempt * 1.5)
    if driver is None:
        raise last_err if last_err else RuntimeError("크롬 드라이버 생성 실패")
    driver.set_page_load_timeout(60)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": (
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "Object.defineProperty(navigator,'languages',{get:()=>['ko-KR','ko']});"
                "window.chrome={runtime:{}};"
            )},
        )
        driver.execute_cdp_cmd(
            "Network.setUserAgentOverride",
            {"userAgent": user_agent, "acceptLanguage": "ko-KR,ko",
             "platform": "Win32" if platform.system() == "Windows" else "MacIntel"},
        )
    except Exception:
        pass

    try:
        driver.maximize_window()
    except Exception:
        pass
    return driver
