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

FOLLOW_TEXTS = {
    "follow", "팔로우",
    # 2026-07-31 고객 진단 ZIP 실측: 상대가 나를 이미 팔로우 중이면 인스타는 버튼 문구를
    # "팔로우"가 아니라 **"맞팔로우"**(=Follow back)로 바꿔 그린다. v1.3.x 는 정확히 일치하는
    # 문자열만 봤기 때문에 이 버튼을 못 찾고 follow_button_not_found 로 실패했다.
    # (`<button type="button">맞팔로우</button>` - 6개 진단 ZIP 전부 동일)
    "맞팔로우", "맞팔로우하기", "follow back", "follow back?",
    "팔로우하기", "다시 팔로우", "follow again",
}
FOLLOWING_TEXTS = {
    "following", "팔로잉", "requested", "요청됨", "요청 됨", "요청 취소",
    "request sent", "cancel request", "팔로우 취소", "unfollow",
}
UNFOLLOW_HINT_TEXTS = {"message", "메시지", "메시지 보내기"}  # 이미 팔로우 중일 때 옆에 뜨는 버튼

# 텍스트가 위 목록에 정확히 없을 때 쓰는 보수적 판정용. 인스타는 문구를 자주 바꾸므로
# "팔로우/follow 가 들어가되 팔로잉/팔로워/취소가 아닌 짧은 버튼"까지는 팔로우 버튼으로 본다.
_FOLLOW_NEGATIVE = ("팔로잉", "팔로워", "취소", "following", "followers", "unfollow", "remove")
# 팔로우와 무관한데 '팔로우' 글자가 들어간 링크/문구를 걸러내기 위한 길이 상한(글자 수).
_FOLLOW_TEXT_MAXLEN = 12
# 팔로우 버튼이 늦게 그려지는 경우를 기다리는 시간(초)과, 클릭 후 상태 변화를 확인하는 시간(초).
FOLLOW_LOOKUP_TIMEOUT = 6
FOLLOW_CONFIRM_TIMEOUT = 6


def _norm_text(raw):
    """버튼 텍스트 정규화: 좌우 공백 제거 + 소문자 + 내부 연속 공백 1칸."""
    return " ".join((raw or "").split()).lower()


def classify_follow_text(raw):
    """버튼 문구 하나를 'following' / 'follow' / None 으로 분류한다.

    반환값:
      "following" - 이미 팔로우 중이거나 요청을 보낸 상태(= 우리 입장에서는 성공/스킵)
      "follow"    - 지금 눌러야 하는 팔로우 버튼("팔로우", "맞팔로우", "Follow back" ...)
      None        - 팔로우와 무관한 버튼

    이미-팔로우 판정을 먼저 한다: "팔로우 취소"처럼 두 단어가 다 들어간 문구를 팔로우 버튼으로
    오인해 누르면 **언팔로우**가 돼버리기 때문이다.
    """
    t = _norm_text(raw)
    if not t:
        return None
    if t in FOLLOWING_TEXTS:
        return "following"
    if t in FOLLOW_TEXTS:
        return "follow"
    if len(t) > _FOLLOW_TEXT_MAXLEN:
        return None
    if any(neg in t for neg in _FOLLOW_NEGATIVE):
        # "팔로잉"/"팔로우 취소" 계열은 이미 팔로우 중이라는 신호로만 쓴다(누르지 않는다).
        if "팔로잉" in t or "following" in t:
            return "following"
        return None
    if "팔로우" in t or "follow" in t:
        return "follow"
    return None

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


def session_is_live(driver):
    """**페이지 이동 없이** 이 크롬 창이 지금 로그인 상태인지만 본다(v1.6.0).

    `is_logged_in()` 은 instagram.com 으로 `driver.get()` 을 때리므로 [시작] 버튼 경로에서
    부르면 (a) 고객이 보고 있는 화면을 마음대로 옮기고 (b) 로그인 스레드가 같은 드라이버를
    쓰는 중이면 명령이 엉킨다. [시작] 이 '진짜 로그인돼 있나'를 확인할 때는 이 함수를 쓴다.

    판정 근거는 로그인 성공 시에만 심어지는 `sessionid` 쿠키 하나. 로그인 화면에 머물러 있으면
    False. 크롬 창이 죽었으면 예외가 나므로 False.
    """
    try:
        if "/accounts/login" in (driver.current_url or "").lower():
            return False
    except Exception:
        return False
    try:
        return any(c.get("name") == "sessionid" and c.get("value")
                   for c in (driver.get_cookies() or []))
    except Exception:
        return False


def goto_login_screen(driver):
    driver.get(config.INSTAGRAM_LOGIN_URL)


# ---------------------------------------------------------------------------
# 계정 동일성(누구로 실행 중인가) - v1.5.0 부터 **읽는 곳이 어디든 같은 함수 하나**만 쓴다.
#
# v1.4.0 사고: 로그인 직후에는 쿠키를 읽고([시작]) 직전에도 쿠키를 읽었는데, 그 사이에 고객이
# 크롬 창에서 인스타 자체 '계정 전환'으로 계정을 바꾸면 값이 달라진다. v1.4.0 은 그걸
# '변조'로 보고 [시작] 을 막아버렸다(고객 실측: start_blocked_uid_drift 3회 반복, 사용 불가).
# 실제로는 고객이 의도한 정상 동작이다. 그래서 v1.5.0 의 원칙은 딱 하나:
#
#   **팔로우/DM 이 실제로 실행되는 그 계정**이 유일한 정답이고, 프로그램은 그 계정을 따라간다.
#
# 그 '실행 계정'을 가장 정확하게 알려주는 건 페이지 컨텍스트에서 쿠키를 그대로 실어 보내는
# 인증 API 응답이다(팔로우/DM XHR 과 동일한 자격증명으로 나가므로 정의상 같은 계정).
#   GET /api/v1/accounts/current_user/  (헤더 X-IG-App-ID)  -> {"user":{"pk":..,"username":..}}
# 미로그인 상태에서 이 엔드포인트는 302 -> /accounts/login 으로 튄다(2026-08-03 실측).
IG_WEB_APP_ID = "936619743392459"

_CURRENT_USER_JS = """
var done = arguments[arguments.length - 1];
try {
  fetch('/api/v1/accounts/current_user/?edit=true', {
    method: 'GET', credentials: 'include', redirect: 'follow',
    headers: {'X-IG-App-ID': '%s', 'X-Requested-With': 'XMLHttpRequest'}
  }).then(function (r) {
    if (r.redirected || !r.ok) { done({error: 'http_' + r.status + (r.redirected ? '_redirected' : '')}); return null; }
    return r.json().then(function (j) {
      var u = (j && j.user) || {};
      done({id: (u.pk !== undefined && u.pk !== null) ? String(u.pk) : (u.pk_id ? String(u.pk_id) : null),
            username: u.username || null});
    });
  }).catch(function (e) { done({error: String(e)}); });
} catch (e) { done({error: String(e)}); }
""" % IG_WEB_APP_ID

# 인스타 웹이 문서에 심어 두는 '지금 로그인한 사용자' 블록. 실측(진단 ZIP):
#   "PolarisViewer",[],{"data":{... "id":"45010010845", ... "username":"xxtwinklebeamxx" ...}}
_VIEWER_IDENTITY_JS = (
    "var h=document.documentElement.innerHTML;"
    "var i=h.indexOf('PolarisViewer');"
    "if(i<0){return null;}"
    "var seg=h.slice(i,i+8000);"
    "var u=seg.match(/\"username\":\"([A-Za-z0-9._]{1,30})\"/);"
    "var d=seg.match(/\"id\":\"([0-9]{5,25})\"/);"
    "if(!u&&!d){return null;}"
    "return {id: d?d[1]:null, username: u?u[1]:null};"
)

_VIEWER_USERNAME_JS = _VIEWER_IDENTITY_JS  # 이름만 유지(예전 호출부/테스트 호환)


def _on_instagram_page(driver):
    try:
        return "instagram.com" in (driver.current_url or "")
    except Exception:
        return False


def _api_probe(driver):
    """(ident|None, 이유문자열). 이유를 남기는 이유는 아래 주석 참고."""
    if not _on_instagram_page(driver):
        try:
            where = (driver.current_url or "")[:60]
        except Exception:
            where = "?"
        return None, f"off_instagram({where})"
    try:
        driver.set_script_timeout(12)
    except Exception:
        pass
    res = driver.execute_async_script(_CURRENT_USER_JS)
    if not isinstance(res, dict):
        return None, "no_result"
    if res.get("error"):
        return None, str(res["error"])[:60]
    if not res.get("id"):
        return None, "no_id"
    return ({"user_id": str(res["id"]), "username": res.get("username") or None,
             "source": "api"}, "ok")


def _identity_from_api(driver):
    """실행 계정의 정답 소스. 페이지 컨텍스트에서 인증 API 를 그대로 호출한다.

    고객 실환경(2026-08-03 08:55)에서 이 소스는 계속 `api=none` 이 나오고 전부 viewer 폴백으로
    돌아간다. 왜 none 인지(302 리다이렉트인지, 4xx 인지, 인스타 페이지가 아니라서인지)는
    지금까지 로그만으로 구별할 수 없었다. v1.6.0 부터 `identity_report` 가 그 **이유**까지
    한 줄에 찍는다(`api=none(http_302_redirected)` 처럼). 추측 대신 다음 실행 로그로 판정한다.
    """
    ident, _reason = _api_probe(driver)
    return ident


def _identity_from_viewer(driver):
    """서버가 문서에 그려 넣은 viewer 블록. API 가 막혔을 때의 2순위."""
    res = driver.execute_script(_VIEWER_IDENTITY_JS)
    if not isinstance(res, dict) or not res.get("id"):
        return None
    return {"user_id": str(res["id"]), "username": res.get("username") or None, "source": "viewer"}


def _username_from_dom(driver):
    """좌측 내비의 내 프로필 링크(alt='<아이디>님의 프로필 사진')에서 아이디만 읽는다."""
    try:
        for a in driver.find_elements(By.CSS_SELECTOR, "a[href^='/'][role='link']"):
            href = (a.get_attribute("href") or "").rstrip("/")
            slug = href.rsplit("/", 1)[-1]
            if not slug:
                continue
            for img in a.find_elements(By.CSS_SELECTOR, "img[alt]"):
                alt = img.get_attribute("alt") or ""
                if slug in alt and ("프로필" in alt or "profile" in alt.lower()):
                    return slug
    except Exception:
        pass
    return None


def _identity_from_cookie(driver):
    """`ds_user_id` 쿠키. 3순위 - 멀티 계정 세션에서는 활성 계정과 어긋날 수 있다."""
    uid = None
    for c in driver.get_cookies() or []:
        if c.get("name") == "ds_user_id" and c.get("value"):
            uid = str(c["value"])
            break
    if not uid:
        return None
    return {"user_id": uid, "username": _username_from_dom(driver), "source": "cookie"}


def resolve_identity(driver):
    """**지금 이 크롬 세션이 어느 계정으로 동작 중인가**. 로그인/[시작]/진단 전부 이 함수만 쓴다.

    반환: {"user_id": str|None, "username": str|None, "source": "api"|"viewer"|"cookie"|"none"}
    페이지 이동은 하지 않는다(실행 중 호출해도 안전).
    """
    for fn in (_identity_from_api, _identity_from_viewer, _identity_from_cookie):
        try:
            ident = fn(driver)
        except Exception:
            ident = None
        if ident and ident.get("user_id"):
            return ident
    # id 는 못 읽었지만 아이디만이라도 나오면 그거라도 돌려준다(진단 가치).
    try:
        name = _username_from_dom(driver)
    except Exception:
        name = None
    return {"user_id": None, "username": name, "source": "none"}


def identity_report(driver):
    """세 소스(api / viewer / cookie)를 **전부** 읽어 한 줄로 만든다. 진단 전용.

    v1.4.0 사고를 두고 '읽는 위치마다 다른 소스를 봐서 값이 갈렸다'는 가설이 있었다. 이걸
    추측으로 남기지 않기 위해, [시작] 때 세 소스를 동시에 찍는다. 다음 실행 로그 한 줄이면
    세 값이 실제로 일치하는지(=고객이 크롬에서 계정을 바꾼 것) 아닌지가 바로 판정된다.

    v1.6.0: api 가 none 일 때 **왜** none 인지까지 같이 남긴다. 고객 환경에서는 계속 api=none
    이었는데(=1순위 소스가 통째로 죽어 있다) 그 원인이 로그에 없어 추측만 가능했다.
    """
    out = []
    try:
        got, reason = _api_probe(driver)
        out.append(f"api={got['user_id']}/{got.get('username') or '?'}" if got
                   else f"api=none({reason})")
    except Exception as e:
        out.append(f"api=err({str(e)[:60]})")
    for name, fn in (("viewer", _identity_from_viewer), ("cookie", _identity_from_cookie)):
        try:
            got = fn(driver)
        except Exception as e:
            out.append(f"{name}=err({str(e)[:60]})")
            continue
        if not got:
            out.append(f"{name}=none")
        else:
            out.append(f"{name}={got.get('user_id')}/{got.get('username') or '?'}")
    return " ".join(out)


def identity_str(ident):
    """로그 한 줄용 표기: `@아이디(숫자id, src=api)`."""
    ident = ident or {}
    return (f"@{ident.get('username') or '?'}"
            f"({ident.get('user_id') or '?'}, src={ident.get('source') or 'none'})")


def current_user_id(driver):
    """실행 계정의 숫자 id. `resolve_identity` 의 얇은 래퍼(소스가 갈라지지 않게)."""
    return resolve_identity(driver).get("user_id")


def current_username(driver):
    """실행 계정의 아이디. `resolve_identity` 의 얇은 래퍼."""
    return resolve_identity(driver).get("username")


def current_identity(driver):
    """(숫자 id, 아이디) 튜플. 한 번만 읽어서 두 값이 서로 다른 시점의 값이 되지 않게 한다."""
    ident = resolve_identity(driver)
    return ident.get("user_id"), ident.get("username")


# ---------------------------------------------------------------------------
# 인스타 자체 '계정 전환'(프로필 메뉴 -> 계정 전환) 조작 - 연결된 서브계정 대응
#
# 이 고객은 부모 계정 하나에 서브계정이 여러 개 붙어 있다("A아이디 한가지로 3가지 계정을
# 만들 수 있는데"). 크롬 프로필을 나눠도 부모로 로그인하면 그 세션에 서브계정이 전부 딸려
# 오므로, 원하는 계정으로 '활성'을 바꾸는 건 인스타 자체 전환 UI 를 쓰는 수밖에 없다.
#
# 주의(정직하게): 아래 셀렉터는 텍스트/aria-label 기반으로 최대한 느슨하게 짰지만
# **라이브 인스타 계정으로 실측 검증하지 못했다**(이 인프라에서 인스타 로그인이 계속 거부됨).
# 그래서 실패해도 앱을 막지 않고, 실패 시 화면 DOM 을 진단으로 올려 다음 판에 셀렉터를
# 확정할 수 있게 한다. 전환 성공 판정은 오직 `resolve_identity` 로만 한다.
_SWITCH_MENU_TEXTS = ("계정 전환", "계정 전환하기", "Switch accounts", "Switch account")
_MORE_MENU_LABELS = ("더 보기", "More", "설정", "Settings", "옵션", "Options")
_CLICKABLE_XPATH_ROLES = "self::button or @role='button' or @role='menuitem' or @role='link'"


def _clickable_by_text(driver, texts, scope=None):
    """텍스트가 들어간 가장 '작은'(=가장 안쪽) 클릭 요소를 고른다."""
    best = None
    best_len = None
    for t in texts:
        xp = f".//*[{_CLICKABLE_XPATH_ROLES}][contains(normalize-space(.), '{t}')]"
        try:
            els = (scope or driver).find_elements(By.XPATH, xp)
        except Exception:
            els = []
        for el in els:
            try:
                if not el.is_displayed():
                    continue
                length = len((el.text or "").strip())
            except Exception:
                continue
            if best is None or length < best_len:
                best, best_len = el, length
    return best


def _click(driver, el):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    except Exception:
        pass
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


def _open_more_menu(driver):
    for label in _MORE_MENU_LABELS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, f"svg[aria-label='{label}']")
        except Exception:
            els = []
        for svg in els:
            try:
                holder = svg.find_element(By.XPATH,
                                          "./ancestor::*[self::button or @role='button' or @role='link'][1]")
            except Exception:
                continue
            if _click(driver, holder):
                time.sleep(1.0)
                return True
    return False


def list_switchable_accounts(driver):
    """계정 전환 대화상자에 보이는 아이디 목록(열려 있지 않으면 빈 목록)."""
    names = []
    try:
        rows = driver.find_elements(
            By.XPATH, "//div[@role='dialog']//*[string-length(normalize-space(text()))>0]")
    except Exception:
        rows = []
    for el in rows:
        try:
            t = (el.text or "").strip()
        except Exception:
            continue
        if t and len(t) <= 30 and all(ch.isalnum() or ch in "._" for ch in t) and t not in names:
            names.append(t)
    return names


def switch_to_account(driver, target_username, log=print):
    """인스타 자체 계정 전환으로 `target_username` 을 활성 계정으로 만든다.

    반환: (성공여부, 설명). **성공 판정은 전환 후 `resolve_identity` 결과로만 한다**
    (메뉴를 눌렀다는 사실만으로 성공이라고 하지 않는다).
    """
    if not target_username:
        return False, "대상 아이디가 없습니다"
    steps = []
    try:
        cur = resolve_identity(driver)
        if (cur.get("username") or "").lower() == target_username.lower():
            return True, f"이미 @{target_username} 로 동작 중"
        try:
            driver.get(config.INSTAGRAM_BASE + "/")
            time.sleep(2.0)
        except Exception:
            pass

        el = _clickable_by_text(driver, _SWITCH_MENU_TEXTS)
        steps.append(f"direct_menu={'found' if el else 'none'}")
        if el is None:
            steps.append(f"more_menu={'opened' if _open_more_menu(driver) else 'failed'}")
            el = _clickable_by_text(driver, _SWITCH_MENU_TEXTS)
            steps.append(f"menu_after_more={'found' if el else 'none'}")
        if el is None:
            return False, "계정 전환 메뉴를 찾지 못했습니다 (" + ", ".join(steps) + ")"
        _click(driver, el)
        time.sleep(1.5)

        seen = list_switchable_accounts(driver)
        steps.append("dialog_accounts=" + (",".join(seen) if seen else "none"))
        target = None
        try:
            target = driver.find_element(
                By.XPATH,
                f"//div[@role='dialog']//*[normalize-space(text())='{target_username}']")
        except Exception:
            target = None
        if target is None:
            target = _clickable_by_text(driver, (target_username,))
        if target is None:
            return False, f"목록에서 @{target_username} 를 찾지 못했습니다 (" + ", ".join(steps) + ")"
        try:
            holder = target.find_element(
                By.XPATH, f"./ancestor-or-self::*[{_CLICKABLE_XPATH_ROLES}][1]")
        except Exception:
            holder = target
        _click(driver, holder)

        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(2.0)
            now = resolve_identity(driver)
            if (now.get("username") or "").lower() == target_username.lower():
                return True, f"전환 완료 {identity_str(now)}"
        return False, ("전환 후에도 계정이 바뀌지 않았습니다 "
                       f"{identity_str(resolve_identity(driver))} (" + ", ".join(steps) + ")")
    except Exception as e:
        return False, f"계정 전환 중 오류: {e} (" + ", ".join(steps) + ")"


def clear_session(driver):
    """이 크롬 프로필의 로그인 세션만 끊고 로그인 화면으로 보낸다(프로필 폴더는 유지).

    '별명 != 실제 계정' 이 감지됐을 때 자동으로 부르는 경로. 크롬이 떠 있는 동안 프로필 폴더를
    통째로 지우는 건 불가능/위험하므로, 쿠키만 지워서 확실히 새 로그인을 받게 한다.
    """
    try:
        driver.get(config.INSTAGRAM_BASE + "/")
    except Exception:
        pass
    try:
        driver.delete_all_cookies()
    except Exception:
        pass
    try:
        driver.execute_script("try{localStorage.clear();sessionStorage.clear();}catch(e){}")
    except Exception:
        pass
    try:
        driver.get(config.INSTAGRAM_LOGIN_URL)
    except Exception:
        pass
    return True


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

    follow_el, state = _find_follow_control(driver)
    if state == "following":
        log(f"이미 팔로우 중 (스킵): {profile_url}")
        return ActionResult(True, "already_following")

    if follow_el is None:
        # 여기까지 왔는데 팔로우 계열 컨트롤이 하나도 없다. 프로필 자체가 안 떴을 수도 있으니
        # 'DOM 이 바뀐 것'과 '페이지가 안 뜬 것'을 구분해서 보고한다(전자만 selector-miss).
        if not _profile_header_loaded(driver):
            return ActionResult(False, "profile_page_not_loaded")
        return ActionResult(False, "follow_button_not_found")

    if not _safe_click(driver, follow_el):
        return ActionResult(False, "follow_click_failed")
    log(f"팔로우 클릭: {profile_url}")
    _human_pause(config.POST_FOLLOW_CLICK_PAUSE_MIN, config.POST_FOLLOW_CLICK_PAUSE_MAX)

    # 클릭이 실제로 먹었는지 확인한다. 버튼이 '팔로잉'/'요청됨' 으로 바뀌어야 성공이다.
    # (예전 버전은 클릭 예외만 없으면 무조건 followed 로 보고해서, 실제로는 안 눌린 경우가
    #  성공으로 집계됐다.)
    confirmed = _wait_follow_state(driver, timeout_s=FOLLOW_CONFIRM_TIMEOUT)
    if confirmed == "following":
        return ActionResult(True, "followed")
    if confirmed == "follow":
        return ActionResult(False, "follow_click_no_change")
    # 버튼이 사라졌거나(레이아웃 변경) 상태를 못 읽는 경우: 클릭 자체는 들어갔으므로 성공으로
    # 보되 사유를 남긴다. 여기서 실패로 몰면 이미 팔로우된 사람을 계속 재시도하게 된다.
    return ActionResult(True, "followed_unverified")


def _profile_header_loaded(driver):
    """프로필 페이지가 실제로 그려졌는지(헤더 + 사용자 이름 영역)."""
    try:
        headers = driver.find_elements(By.TAG_NAME, "header")
    except Exception:
        return False
    for h in headers:
        try:
            if h.is_displayed():
                return True
        except Exception:
            continue
    return False


def _profile_header(driver):
    """프로필 상단 헤더(<header>). 팔로우/메시지 버튼이 여기 안에 있다.

    실측(고객 진단 ZIP 6건 전부): `header > section > ... > button[type=button]` 이 팔로우
    버튼이고, 페이지 아래쪽 '비슷한 계정' 추천 목록의 팔로우/팔로잉 버튼은 header 바깥이다.
    헤더로 범위를 좁히지 않으면 추천 계정의 '팔로잉' 을 보고 '이미 팔로우 중' 이라고 오판하거나,
    최악의 경우 추천 계정을 대신 팔로우한다.
    """
    try:
        for h in driver.find_elements(By.TAG_NAME, "header"):
            try:
                if h.is_displayed():
                    return h
            except Exception:
                continue
    except Exception:
        pass
    return None


def _buttonish_in(scope):
    xp = ".//button | .//div[@role='button'] | .//span[@role='button']"
    try:
        return scope.find_elements(By.XPATH, xp)
    except Exception:
        return []


def _follow_state_from_buttons(driver):
    """프로필 헤더의 버튼들을 훑어 (팔로우 버튼 요소, 상태) 를 돌려준다.

    상태는 'following'(이미 팔로우/요청됨) / 'follow'(눌러야 함) / None(관련 버튼 없음).
    '이미 팔로우 중' 이 하나라도 보이면 팔로우 버튼보다 우선한다(오클릭으로 언팔로우되는 것을
    막는다). 헤더를 못 찾은 경우에만 문서 전체로 폴백한다.
    """
    header = _profile_header(driver)
    elements = _buttonish_in(header) if header is not None else _find_buttonish(driver)
    follow_el = None
    for el in elements:
        try:
            kind = classify_follow_text(el.text)
        except StaleElementReferenceException:
            continue
        except Exception:
            continue
        if kind == "following":
            return None, "following"
        if kind == "follow" and follow_el is None:
            follow_el = el
    return follow_el, ("follow" if follow_el is not None else None)


def _find_follow_control(driver):
    """팔로우 버튼(또는 이미-팔로우 상태)을 찾는다. DOM 이 늦게 그려지는 경우까지 기다린다."""
    deadline = time.time() + FOLLOW_LOOKUP_TIMEOUT
    follow_el, state = _follow_state_from_buttons(driver)
    while state is None and time.time() < deadline:
        time.sleep(0.5)
        follow_el, state = _follow_state_from_buttons(driver)
    return follow_el, state


def _wait_follow_state(driver, timeout_s=FOLLOW_CONFIRM_TIMEOUT):
    """클릭 후 버튼 상태가 '팔로잉/요청됨' 으로 바뀔 때까지 짧게 기다린다."""
    deadline = time.time() + timeout_s
    _, last = _follow_state_from_buttons(driver)   # 최소 한 번은 반드시 확인한다
    while last != "following" and time.time() < deadline:
        time.sleep(0.5)
        _, last = _follow_state_from_buttons(driver)
    return last


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
