# -*- coding: utf-8 -*-
"""인스타그램 웹 UI 조작 - 로그인 감지 / 팔로우 / DM 발송.

원칙:
  - 절대 비공개(undocumented) API 를 직접 호출하지 않는다. 전부 실제 웹 UI 클릭/입력으로
    수행한다(요청에 명시된 탐지 회피 원칙 - API 호출은 훨씬 더 잘 걸린다).
  - 아이디/비번을 자동 입력하지 않는다. 로그인은 항상 사람이 실제로 뜬 크롬 창에서 직접
    한다(멀티 계정 요구사항: "로그인만 바꿔서 이어서 사용"). 프로그램은 로그인 완료 여부만
    감지한다.
  - 절대 하드코딩된 절대경로 XPath(예: /html/body/div[2]/div/div/div[2]/...) 를 쓰지 않는다.
    인스타 DOM 은 자주 바뀌므로 버튼 텍스트/aria-label 기반의 상대 탐색만 사용한다(한국어/
    영어 UI 문구를 둘 다 받아들인다 - 브라우저 lang=ko-KR 이지만 계정 언어 설정에 따라
    영어로 뜨는 경우도 있어 안전하게 이중으로 매칭한다).
"""

import random
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config

FOLLOW_TEXTS = {"follow", "팔로우"}
FOLLOWING_TEXTS = {"following", "팔로잉", "requested", "요청됨"}
UNFOLLOW_HINT_TEXTS = {"message", "메시지", "메시지 보내기"}  # 이미 팔로우 중일 때 옆에 뜨는 버튼


class ActionResult:
    def __init__(self, ok, detail=""):
        self.ok = ok
        self.detail = detail


def _human_pause(min_s, max_s):
    time.sleep(random.uniform(min_s, max_s))


def is_logged_in(driver):
    """현재 인스타그램 웹에 로그인된 상태인지. 로그인 폼이 보이면 미로그인."""
    try:
        driver.get(config.INSTAGRAM_BASE + "/")
        WebDriverWait(driver, 8).until(
            lambda d: d.execute_script("return document.readyState") == "complete")
    except Exception:
        pass
    time.sleep(1.5)
    try:
        login_fields = driver.find_elements(By.NAME, "username")
        password_fields = driver.find_elements(By.NAME, "password")
        if login_fields and password_fields:
            for f in login_fields:
                if f.is_displayed():
                    return False
        return True
    except Exception:
        return False


def goto_login_screen(driver):
    driver.get(config.INSTAGRAM_LOGIN_URL)


def wait_for_manual_login(driver, timeout_s, poll_cb=None):
    """사람이 직접 로그인 폼을 채우고 로그인할 때까지 폴링(타임아웃까지). 감지되면 True."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if is_logged_in(driver):
            return True
        if poll_cb:
            try:
                poll_cb()
            except Exception:
                pass
        time.sleep(2)
    return False


def logout(driver, log=print):
    """실제 인스타그램 로그아웃(서버 세션 무효화). 프로필 메뉴 -> 로그아웃 클릭.
    UI 를 못 찾으면 쿠키만 지워서라도 이 프로필에서는 로그아웃 상태로 만든다."""
    try:
        driver.get(config.INSTAGRAM_BASE + "/accounts/edit/")
        time.sleep(2)
        # "더보기"(more)/프로필 메뉴 아이콘을 눌러야 로그아웃이 나오는 레이아웃이 흔함.
        candidates = driver.find_elements(By.XPATH, "//*[self::span or self::div]")
        clicked_menu = False
        for el in candidates:
            try:
                t = (el.text or "").strip().lower()
            except Exception:
                continue
            if t in ("더 보기", "more", "설정", "settings"):
                try:
                    el.click()
                    clicked_menu = True
                    time.sleep(1)
                    break
                except Exception:
                    continue
        logout_texts = {"로그아웃", "log out", "logout"}
        els = driver.find_elements(By.XPATH, "//*[self::span or self::div or self::button]")
        for el in els:
            try:
                t = (el.text or "").strip().lower()
            except Exception:
                continue
            if t in logout_texts:
                try:
                    el.click()
                    time.sleep(2)
                    log("인스타그램 로그아웃 완료")
                    return True
                except Exception:
                    continue
        log("로그아웃 메뉴를 UI 에서 못 찾아 세션 쿠키를 직접 정리합니다")
    except Exception as e:
        log(f"로그아웃 UI 처리 중 오류(쿠키로 대체): {e}")
    try:
        driver.delete_all_cookies()
        driver.get(config.INSTAGRAM_BASE + "/accounts/login/")
    except Exception:
        pass
    return True


def _find_buttonish(driver):
    """버튼처럼 동작하는 요소(button, role=button div/span) 전부 수집."""
    xp = "//button | //div[@role='button'] | //span[@role='button']"
    try:
        return driver.find_elements(By.XPATH, xp)
    except Exception:
        return []


def follow_profile(driver, profile_url, log=print):
    """프로필 URL 로 이동해 팔로우 버튼을 찾아 클릭. 이미 팔로우 중이면 스킵으로 처리.

    사람처럼: 페이지 로드 후 곧바로 누르지 않고 잠깐(둘러보는) 대기 후 클릭한다.
    """
    driver.get(profile_url)
    try:
        WebDriverWait(driver, config.WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.TAG_NAME, "header")))
    except Exception:
        pass

    # 비공개 계정 등 프로필이 아예 안 뜨는 경우(존재하지 않음/차단) 조기 감지
    page_text = ""
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        pass
    if "sorry, this page" in page_text or "페이지를 사용할 수 없" in page_text:
        return ActionResult(False, "profile_not_found")

    _human_pause(config.PROFILE_VIEW_PAUSE_MIN, config.PROFILE_VIEW_PAUSE_MAX)  # 훑어보는 대기

    buttons = _find_buttonish(driver)
    already_following = False
    for el in buttons:
        try:
            t = (el.text or "").strip().lower()
        except Exception:
            continue
        if t in FOLLOWING_TEXTS:
            already_following = True
        if t in FOLLOW_TEXTS:
            try:
                el.click()
                log(f"팔로우 클릭: {profile_url}")
                time.sleep(random.uniform(config.POST_FOLLOW_CLICK_PAUSE_MIN,
                                          config.POST_FOLLOW_CLICK_PAUSE_MAX))
                return ActionResult(True, "followed")
            except Exception as e:
                return ActionResult(False, f"follow_click_failed: {e}")

    if already_following:
        log(f"이미 팔로우 중 (스킵): {profile_url}")
        return ActionResult(True, "already_following")

    return ActionResult(False, "follow_button_not_found")


def _type_like_human(element, text):
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(config.TYPE_JITTER_MIN, config.TYPE_JITTER_MAX))


def _find_message_box(driver):
    candidates = driver.find_elements(
        By.XPATH,
        "//textarea | //div[@contenteditable='true' and @role='textbox']")
    for el in candidates:
        try:
            aria = (el.get_attribute("aria-label") or "").lower()
            placeholder = (el.get_attribute("placeholder") or "").lower()
        except Exception:
            continue
        hint = aria + placeholder
        if "message" in hint or "메시지" in hint or "메세지" in hint:
            return el
    # 힌트가 안 잡히면 contenteditable/textarea 중 화면에 보이는 마지막 요소로 폴백
    for el in reversed(candidates):
        try:
            if el.is_displayed() and el.is_enabled():
                return el
        except Exception:
            continue
    return None


def send_dm(driver, username, message, log=print):
    """direct/t/<username> 로 바로 이동해 메시지 입력창을 찾아 전송.

    이 URL 은 팔로우 여부와 무관하게 해당 유저와의 DM 스레드(신규면 새 대화)를 바로 연다
    (엔터프라이즈 새 메시지 검색 UI 를 안 타므로 더 안정적/덜 취약).
    """
    driver.get(f"{config.INSTAGRAM_BASE}/direct/t/{username}/")
    try:
        WebDriverWait(driver, config.WAIT_TIMEOUT).until(
            lambda d: d.execute_script("return document.readyState") == "complete")
    except Exception:
        pass
    time.sleep(random.uniform(config.DM_PAGE_LOAD_PAUSE_MIN, config.DM_PAGE_LOAD_PAUSE_MAX))

    page_text = ""
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        pass
    if "isn't available" in page_text or "사용할 수 없" in page_text:
        return ActionResult(False, "dm_thread_not_available")

    # 일부 계정은 "message request" 안내 화면에서 실제 입력창이 늦게 마운트된다. 폴링.
    box = None
    deadline = time.time() + config.WAIT_TIMEOUT
    while time.time() < deadline and box is None:
        box = _find_message_box(driver)
        if box is None:
            time.sleep(0.7)
    if box is None:
        return ActionResult(False, "message_box_not_found")

    try:
        box.click()
        time.sleep(random.uniform(config.PRE_TYPE_PAUSE_MIN, config.PRE_TYPE_PAUSE_MAX))
        _type_like_human(box, message)
        time.sleep(random.uniform(config.POST_TYPE_PAUSE_MIN, config.POST_TYPE_PAUSE_MAX))
        box.send_keys(Keys.RETURN)
        time.sleep(random.uniform(config.POST_SEND_PAUSE_MIN, config.POST_SEND_PAUSE_MAX))
        log(f"DM 발송 완료: @{username}")
        return ActionResult(True, "sent")
    except Exception as e:
        return ActionResult(False, f"dm_send_failed: {e}")
