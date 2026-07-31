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

from selenium.common.exceptions import StaleElementReferenceException
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
# 채팅 위젯/다이얼로그 닫기 버튼 svg aria-label.
CLOSE_LABELS = ("닫기", "Close")
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
    except Exception:
        return False
    # 2026-07-31 고객 리포트(계정 전환 안 됨): 비밀번호 입력칸이 안 보인다고 곧바로 로그인된
    # 것으로 판정하면 안 된다. 완전히 새(쿠키 없는) 크롬 프로필로 처음 instagram.com/ 에 가도
    # 마케팅 랜딩 화면이 "로그인"/"가입하기" 버튼만 보여주고 비밀번호 칸은 그 버튼을 눌러야
    # 나타난다 - 그 상태에서 결과가 True 로 새면, 별명을 "Jimin"->"Jimin2" 로 바꿔 새 프로필로
    # 크롬을 새로 띄워도 "이미 로그인되어 있습니다"로 오판해 실제로는 로그인 안 된 빈 세션을
    # 그대로 쓰게 된다(다른 계정으로 전환이 안 되는 것처럼 보이는 근본 원인). 그래서 실제
    # 로그인 성공 시에만 심어지는 sessionid 쿠키가 있을 때만 True 를 반환한다
    # (`_login_appears_complete` 가 이미 쓰는 것과 같은 신호).
    try:
        cookies = driver.get_cookies()
    except Exception:
        return False
    return any(c.get("name") == "sessionid" and c.get("value") for c in cookies)


def goto_login_screen(driver):
    driver.get(config.INSTAGRAM_LOGIN_URL)


def current_username(driver):
    """지금 로그인된 인스타 아이디를 읽는다(못 읽으면 None).

    별명만 보고는 어떤 계정이 붙어 있는지 알 수 없어서 '계정 전환이 안 된다'는 상황을 진단할 수
    없다. 페이지 이동 없이 현재 화면에서만 읽는다.
    """
    try:
        name = driver.execute_script(
            "var m=document.documentElement.innerHTML.match("
            "/\"username\"\\s*:\\s*\"([A-Za-z0-9._]{1,30})\"/); return m ? m[1] : null;")
        if name:
            return name
    except Exception:
        pass
    try:
        for a in driver.find_elements(By.CSS_SELECTOR, "a[href^='/'][role='link']"):
            href = (a.get_attribute("href") or "").rstrip("/")
            slug = href.rsplit("/", 1)[-1]
            img = a.find_elements(By.CSS_SELECTOR, "img[alt*='프로필'], img[alt*='profile']")
            if img and slug:
                return slug
    except Exception:
        pass
    return None


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


def _type_into(driver, finder, text, attempts=3):
    """타이핑 도중 요소가 stale 이 돼도 다시 찾아서 이어친다. 성공하면 실제로 입력한 요소를 반환.

    2026-07-31 실측: 새 메시지 다이얼로그는 첫 글자 입력 직후 리스트를 다시 그리면서 입력 요소를
    교체한다. 한 번 잡은 element 로 끝까지 send_keys 하면 StaleElementReferenceException 이
    나고, 그게 그대로 위로 튀어 그 행 전체가 예외로 죽었다(고객 리포트: 4~5번째 행 연속 중단).

    반환값이 '입력에 성공한 그 요소'인 게 중요하다. Enter 는 반드시 같은 요소에 눌러야 한다 -
    다시 찾아서 누르면 그 사이 리렌더된 빈 입력창에 Enter 를 눌러 빈 메시지가 나갈 수 있다.
    """
    for attempt in range(attempts):
        el = finder()
        if el is None:
            return None
        try:
            el.clear()
        except Exception:
            pass
        try:
            _type_like_human(el, text)
            return el
        except StaleElementReferenceException:
            if attempt == attempts - 1:
                return None
            time.sleep(0.6)
        except Exception:
            return None
    return None


def _dialog_scope(driver, require_input=False):
    """열려 있는 모달(새 메시지 창)만 골라낸다. 없으면 None.

    받은편지함 화면에는 왼쪽 사이드바에도 '검색' 입력창이 있어서, 다이얼로그로 범위를 좁히지
    않으면 사이드바 입력창을 잡아 엉뚱한 곳에 타이핑하게 된다(실제로 그래서 실패했다).

    화면에는 알림 허용 안내 같은 다른 모달이 같이 떠 있을 수 있다. 그냥 '첫 번째 모달'을
    잡으면 입력창이 없는 안내 모달을 잡는다(실측). require_input 이면 입력창을 가진 모달만
    고른다.
    """
    best = None
    for d in driver.find_elements(By.CSS_SELECTOR, "div[role='dialog']"):
        try:
            if not d.is_displayed():
                continue
            if require_input:
                if any(i.is_displayed() for i in d.find_elements(By.CSS_SELECTOR, "input")):
                    return d
                continue
            if best is None:
                best = d
        except Exception:
            continue
    return best


def _safe_click(driver, el):
    """일반 클릭이 다른 요소에 가로막히면(overlay) JS 클릭으로 한 번 더 시도한다."""
    try:
        el.click()
        return True
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].click();", el)
        return True
    except Exception:
        return False


def close_open_chat_widget(driver):
    """프로필 위에 떠 있는 채팅 위젯을 닫는다.

    [메시지] 버튼으로 연 대화창은 프로필을 벗어나도 남아 있을 수 있다. 그대로 두면 다음 사람
    프로필에서 '이전 사람의 입력창'을 잡아 엉뚱한 사람에게 메시지를 보낼 수 있다(오발송).
    """
    for label in CLOSE_LABELS:
        try:
            svgs = driver.find_elements(By.CSS_SELECTOR, f"svg[aria-label='{label}']")
        except Exception:
            continue
        for svg in svgs:
            try:
                target = svg.find_element(
                    By.XPATH, "ancestor::*[self::button or @role='button'][1]")
            except Exception:
                target = svg
            try:
                if not target.is_displayed():
                    continue
            except Exception:
                continue
            if _safe_click(driver, target):
                time.sleep(0.4)
                return True
    return False


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
            if _safe_click(driver, el):
                return True
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

    # [메시지] 버튼은 프로필 헤더가 늦게 그려지면 한 박자 뒤에 나타난다. 한 번만 훑고 포기하면
    # 멀쩡한 프로필에서도 실패하므로 잠깐 폴링한다.
    clicked = False
    deadline = time.time() + config.WAIT_TIMEOUT
    while time.time() < deadline and not clicked:
        clicked = _click_text_button(driver, MESSAGE_BUTTON_TEXTS)
        if not clicked:
            time.sleep(0.6)
    if not clicked:
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
            if _safe_click(driver, target):
                opened = True
                break
        if opened:
            break
    if not opened:
        return None, "new_message_button_not_found"

    time.sleep(random.uniform(config.PRE_TYPE_PAUSE_MIN, config.PRE_TYPE_PAUSE_MAX))

    def _find_search_box():
        """다이얼로그 안의 검색 입력만 고른다(사이드바 검색창을 잡으면 안 된다)."""
        scope = _dialog_scope(driver, require_input=True)
        if scope is None:
            return None
        for el in scope.find_elements(By.CSS_SELECTOR, "input"):
            try:
                if el.is_displayed():
                    return el
            except Exception:
                continue
        return None

    search = None
    deadline = time.time() + config.WAIT_TIMEOUT
    while time.time() < deadline and search is None:
        search = _find_search_box()
        if search is None:
            time.sleep(0.5)
    if search is None:
        return None, "dm_search_box_not_found"

    # 첫 글자 입력 직후 다이얼로그가 리스트를 다시 그리며 input 을 교체한다 -> stale 재조회 필요.
    if not _type_into(driver, _find_search_box, username):
        return None, "dm_search_box_not_found"
    time.sleep(random.uniform(1.2, 2.2))  # 검색 결과가 뜰 시간

    # 결과 목록에서 정확히 이 아이디인 행을 고른다(부분 일치 계정 오발송 방지).
    # 반드시 다이얼로그 안에서만 찾는다 - 뒤에 깔린 대화 목록에도 같은 이름이 있을 수 있다.
    picked = False
    deadline = time.time() + config.WAIT_TIMEOUT
    while time.time() < deadline and not picked:
        scope = _dialog_scope(driver, require_input=True)
        rows = []
        if scope is not None:
            try:
                # 실측(2026-07-31): 검색 결과 한 줄은 div[role='option'] 이다.
                # role='button'/label 만 보면 하나도 안 걸려 'dm_user_not_found_in_search' 가 난다.
                rows = scope.find_elements(
                    By.XPATH,
                    ".//div[@role='option'] | .//div[@role='button'] | .//label | .//li")
            except Exception:
                rows = []
        for el in rows:
            try:
                if not el.is_displayed():
                    continue
                lines = [ln.strip().lower() for ln in (el.text or "").split("\n") if ln.strip()]
            except Exception:
                continue
            if username.lower() in lines:
                if _safe_click(driver, el):
                    picked = True
                    break
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


def _send_dm_once(driver, username, message, log=print):
    # 이전 사람의 채팅 위젯이 떠 있으면 그 입력창을 잡아 '엉뚱한 사람'에게 보낼 수 있다.
    close_open_chat_widget(driver)

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
        typed = _type_into(driver, lambda: _find_message_box(driver), message)
        if typed is None:
            return ActionResult(False, "dm_typing_failed")
        time.sleep(random.uniform(config.POST_TYPE_PAUSE_MIN, config.POST_TYPE_PAUSE_MAX))
        # Enter 는 방금 입력한 그 요소에 눌러야 한다(빈 메시지 발송 방지).
        typed.send_keys(Keys.RETURN)
        time.sleep(random.uniform(config.POST_SEND_PAUSE_MIN, config.POST_SEND_PAUSE_MAX))
        log(f"DM 발송 완료: @{username}")
        return ActionResult(True, "sent")
    except Exception as e:
        return ActionResult(False, f"dm_send_failed: {e}")
    finally:
        # 보냈든 실패했든 위젯을 닫아 다음 사람에게 상태가 새지 않게 한다.
        close_open_chat_widget(driver)


def send_dm(driver, username, message, log=print):
    """해당 유저와의 DM 스레드를 열고 메시지를 전송한다.

    경로 1) 프로필의 [메시지] 버튼 (기본)
    경로 2) 받은편지함 [새로운 메시지] -> 검색 -> [채팅] (폴백)
    두 경로 모두 인스타 실제 UI 클릭이라 딥링크처럼 조용히 죽지 않는다.

    인스타 화면이 다시 그려지는 순간에 걸리면(stale) 한 번 더 시도한다. 여기서 예외를 그대로
    올리면 그 행이 통째로 죽고 연속 실패로 배치가 멈춘다(2026-07-31 고객 리포트: 4,5번째 행).
    """
    last = None
    for attempt in range(2):
        try:
            last = _send_dm_once(driver, username, message, log=log)
        except StaleElementReferenceException as e:
            last = ActionResult(False, f"dm_stale_retry: {e}")
        if last.ok or last.detail == "profile_not_found":
            return last
        if attempt == 0:
            log(f"DM 재시도: @{username} ({last.detail})")
            time.sleep(random.uniform(1.5, 3.0))
    return last
