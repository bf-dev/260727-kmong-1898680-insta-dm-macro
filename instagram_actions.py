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

# 프로필 페이지의 [메시지] 버튼(= DM 스레드를 여는 정식 경로). 한국어/영어 UI 모두 대응.
MESSAGE_BUTTON_TEXTS = {"message", "메시지", "메시지 보내기", "send message"}
# 받은편지함의 [새로운 메시지](연필) 아이콘 svg aria-label.
NEW_MESSAGE_LABELS = ("새로운 메시지", "New message")
# 새 메시지 다이얼로그에서 상대를 고른 뒤 누르는 확인 버튼.
CHAT_CONFIRM_TEXTS = {"채팅", "chat", "다음", "next"}

# 셀렉터를 못 찾았다는 뜻의 detail 값들(= 인스타 DOM 변경 의심 신호). macro_engine 이 이걸 보고
# 일반 실패와 구분해 'selector-miss' 진단을 따로 올린다.
SELECTOR_MISS_DETAILS = {
    "follow_button_not_found", "message_box_not_found",
    "message_button_not_found", "new_message_button_not_found",
    "dm_search_box_not_found", "dm_chat_button_not_found",
}

# 계정 제한/차단 화면의 문구(소문자 비교). 인스타는 이런 화면을 띄운 뒤에도 클릭 자체는
# 계속 받아주기 때문에, 감지하지 않으면 매크로가 차단된 계정으로 계속 두드리게 된다.
BLOCK_TEXT_PATTERNS = (
    "action blocked", "작업이 차단",
    "temporarily blocked", "일시적으로 차단", "일시적으로 제한",
    "try again later", "나중에 다시 시도", "잠시 후 다시 시도",
    "we restrict certain activity", "특정 활동을 제한", "활동이 제한",
    "we limit how often", "너무 자주",
    "your account has been suspended", "계정이 정지",
    "confirm it's you", "confirm its you", "본인 확인",
    "suspicious login", "의심스러운 로그인",
)
# URL 만 봐도 확실한 것들(챌린지/정지/로그인 튕김).
BLOCK_URL_MARKERS = (
    ("/challenge", "challenge"),
    ("/accounts/suspended", "account_suspended"),
    ("/accounts/disabled", "account_disabled"),
    ("/accounts/login", "logged_out"),
)


class ActionResult:
    def __init__(self, ok, detail=""):
        self.ok = ok
        self.detail = detail


def detect_restriction(driver):
    """지금 화면이 인스타의 제한/차단/본인확인/로그아웃 화면인지 검사해 사유를 반환(아니면 None).

    페이지 이동을 하지 않는다(현재 상태만 본다) - 매 행마다 불리므로 부작용이 없어야 한다.
    """
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        url = ""
    for marker, reason in BLOCK_URL_MARKERS:
        if marker in url:
            return reason

    try:
        body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        return None
    # 페이지 전체 텍스트에서 찾으면 오탐이 늘어난다. 차단 안내는 항상 짧은 모달/전면 화면이라
    # 본문이 길면(= 평범한 프로필/DM 화면) 차단 화면이 아니라고 본다.
    if len(body_text) > 4000:
        return None
    for pattern in BLOCK_TEXT_PATTERNS:
        if pattern in body_text:
            return f"action_block:{pattern}"
    return None


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
    # 2026-07-30 실측: 인스타 로그인 폼의 input 은 더 이상 name="username"/"password" 가 아니다
    # (현재는 name="email"/"pass", 변형에 따라 name 자체가 없기도 하다). 이름에 의존하면
    # '로그인 폼이 떠 있는데 로그인된 걸로 오판'해서 매크로가 전 행을 실패시킨다.
    # 그래서 이름 대신 (1) 로그인 URL 로 튕겼는지 (2) 화면에 비밀번호 입력칸이 보이는지로 본다.
    try:
        if "/accounts/login" in (driver.current_url or "").lower():
            return False
    except Exception:
        pass
    try:
        for f in driver.find_elements(By.CSS_SELECTOR, "input[type='password']"):
            if f.is_displayed():
                return False
        return True
    except Exception:
        return False


def goto_login_screen(driver):
    driver.get(config.INSTAGRAM_LOGIN_URL)


def _login_appears_complete(driver):
    """수동 로그인 대기 중 폴링 전용 체크. `is_logged_in()`과 달리 절대 driver.get()/refresh()를
    호출하지 않는다 - 사람이 아이디/비번을 타이핑 중인 그 순간 페이지를 새로고침하면 입력값이
    통째로 날아간다(2026-07-31 고객 리포트: 3초마다 크롬창이 새로고침돼 로그인 자체가
    불가능했음 - 원인은 이 함수가 예전엔 is_logged_in()을 그대로 불러 매 폴링마다
    driver.get(INSTAGRAM_BASE)를 실행했던 것). 여기서는 이미 브라우저에 떠 있는 상태만
    읽는다: 현재 URL, 화면에 보이는 비밀번호 입력칸, 로그인 성공 시 인스타가 심어주는
    sessionid 쿠키. 셋 다 페이지를 건드리지 않고도 읽을 수 있다."""
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        return False
    if "instagram.com" not in url:
        return False
    if "/accounts/login" in url:
        return False
    try:
        for f in driver.find_elements(By.CSS_SELECTOR, "input[type='password']"):
            if f.is_displayed():
                return False
    except Exception:
        # DOM 조회 실패(예: 네비게이션 중간)는 아직 완료 아님으로 취급 - 페이지를 건드리지 않고 재시도
        return False
    try:
        cookies = driver.get_cookies()
    except Exception:
        return False
    return any(c.get("name") == "sessionid" and c.get("value") for c in cookies)


def wait_for_manual_login(driver, timeout_s, poll_cb=None):
    """사람이 직접 로그인 폼을 채우고 로그인할 때까지 폴링(타임아웃까지). 감지되면 True.
    폴링 중에는 절대 페이지를 새로고침/이동하지 않는다(`_login_appears_complete` 참고) -
    사람이 타이핑하는 도중 창을 리로드하면 입력값이 날아가 로그인 자체를 못 한다."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _login_appears_complete(driver):
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


def _wait_for_message_box(driver, timeout_s=None):
    """메시지 입력창이 마운트될 때까지 폴링. 못 찾으면 None."""
    deadline = time.time() + (timeout_s if timeout_s is not None else config.WAIT_TIMEOUT)
    while time.time() < deadline:
        box = _find_message_box(driver)
        if box is not None:
            return box
        time.sleep(0.7)
    return None


def _click_text_button(driver, texts):
    """버튼처럼 동작하는 요소 중 표시 텍스트가 texts 에 정확히 맞는 것을 클릭."""
    for el in _find_buttonish(driver):
        try:
            t = (el.text or "").strip().lower()
        except Exception:
            continue
        if t in texts:
            try:
                el.click()
                return True
            except Exception:
                continue
    return False


def _open_thread_via_profile(driver, username, log=print):
    """프로필 페이지의 [메시지] 버튼을 눌러 DM 스레드를 연다.

    2026-07-31 실측: 예전에 쓰던 `/direct/t/<username>/` 딥링크는 더 이상 스레드를 열지 않는다
    (DM 받은편지함만 뜨고 오른쪽 대화창이 비어 있음 - 고객 진단 ZIP 의 page.html 에
    textarea/contenteditable 가 0개, GraphQL 1675012 오류 동반). 프로필의 실제 [메시지]
    버튼은 인스타가 내부적으로 스레드를 생성/이동시켜 주므로 이 경로가 정상 동작한다.
    """
    driver.get(f"{config.INSTAGRAM_BASE}/{username}/")
    try:
        WebDriverWait(driver, config.WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.TAG_NAME, "header")))
    except Exception:
        pass
    time.sleep(random.uniform(config.DM_PAGE_LOAD_PAUSE_MIN, config.DM_PAGE_LOAD_PAUSE_MAX))

    page_text = ""
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        pass
    if "sorry, this page" in page_text or "페이지를 사용할 수 없" in page_text:
        return None, "profile_not_found"

    if not _click_text_button(driver, MESSAGE_BUTTON_TEXTS):
        return None, "message_button_not_found"
    log(f"프로필에서 [메시지] 클릭: @{username}")
    box = _wait_for_message_box(driver)
    return box, ("ok" if box is not None else "message_box_not_found")


def _open_thread_via_new_message(driver, username, log=print):
    """받은편지함의 [새로운 메시지] 아이콘 -> 검색 -> 상대 선택 -> [채팅] 으로 스레드를 연다.

    프로필에 [메시지] 버튼이 안 보이는 경우(레이아웃 변형, 버튼이 '더 보기' 안으로 접힘 등)의
    폴백 경로. 인스타 자체 UI 를 그대로 따라가므로 딥링크보다 깨질 확률이 낮다.
    """
    driver.get(f"{config.INSTAGRAM_BASE}/direct/inbox/")
    try:
        WebDriverWait(driver, config.WAIT_TIMEOUT).until(
            lambda d: d.execute_script("return document.readyState") == "complete")
    except Exception:
        pass
    time.sleep(random.uniform(config.DM_PAGE_LOAD_PAUSE_MIN, config.DM_PAGE_LOAD_PAUSE_MAX))

    # [새로운 메시지](연필) 아이콘: svg 의 aria-label 로 찾고 클릭 가능한 조상을 누른다.
    opened = False
    for label in NEW_MESSAGE_LABELS:
        try:
            svgs = driver.find_elements(By.CSS_SELECTOR, f"svg[aria-label='{label}']")
        except Exception:
            svgs = []
        for svg in svgs:
            try:
                target = svg.find_element(By.XPATH, "ancestor::*[self::button or @role='button'][1]")
            except Exception:
                target = svg
            try:
                target.click()
                opened = True
                break
            except Exception:
                continue
        if opened:
            break
    if not opened:
        return None, "new_message_button_not_found"

    time.sleep(random.uniform(config.PRE_TYPE_PAUSE_MIN, config.PRE_TYPE_PAUSE_MAX))

    # 검색창에 아이디 입력
    search = None
    deadline = time.time() + config.WAIT_TIMEOUT
    while time.time() < deadline and search is None:
        for el in driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input[name='queryBox']"):
            try:
                if el.is_displayed():
                    search = el
                    break
            except Exception:
                continue
        if search is None:
            time.sleep(0.5)
    if search is None:
        return None, "dm_search_box_not_found"

    _type_like_human(search, username)
    time.sleep(random.uniform(1.2, 2.2))  # 검색 결과가 뜰 시간

    # 결과 목록에서 정확히 이 아이디인 행을 고른다(부분 일치 계정 오발송 방지).
    picked = False
    deadline = time.time() + config.WAIT_TIMEOUT
    while time.time() < deadline and not picked:
        for el in driver.find_elements(By.XPATH, "//div[@role='button'] | //label | //li"):
            try:
                if not el.is_displayed():
                    continue
                lines = [ln.strip().lower() for ln in (el.text or "").split("\n") if ln.strip()]
            except Exception:
                continue
            if username.lower() in lines:
                try:
                    el.click()
                    picked = True
                    break
                except Exception:
                    continue
        if not picked:
            time.sleep(0.6)
    if not picked:
        return None, "dm_user_not_found_in_search"

    time.sleep(random.uniform(config.PRE_TYPE_PAUSE_MIN, config.PRE_TYPE_PAUSE_MAX))
    if not _click_text_button(driver, CHAT_CONFIRM_TEXTS):
        return None, "dm_chat_button_not_found"
    log(f"새 메시지 검색으로 스레드 열기: @{username}")
    box = _wait_for_message_box(driver)
    return box, ("ok" if box is not None else "message_box_not_found")


def send_dm(driver, username, message, log=print):
    """해당 유저와의 DM 스레드를 열고 메시지를 전송한다.

    경로 1) 프로필의 [메시지] 버튼 (기본)
    경로 2) 받은편지함 [새로운 메시지] -> 검색 -> [채팅] (폴백)
    두 경로 모두 인스타 실제 UI 클릭이라 딥링크처럼 조용히 죽지 않는다.
    """
    box, detail = _open_thread_via_profile(driver, username, log=log)
    if box is None:
        if detail == "profile_not_found":
            return ActionResult(False, "profile_not_found")
        log(f"프로필 경로 실패({detail}) - 새 메시지 검색으로 재시도: @{username}")
        box, detail = _open_thread_via_new_message(driver, username, log=log)
    if box is None:
        return ActionResult(False, detail or "message_box_not_found")

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
