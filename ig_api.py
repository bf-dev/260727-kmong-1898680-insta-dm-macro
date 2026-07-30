# -*- coding: utf-8 -*-
"""instagrapi(비공개 모바일 API) 엔진 - v1.1 부터의 기본 동작 방식.

`instagram_actions.py`(크롬 웹 UI 조작)와 **완전히 같은 함수 모양**을 노출한다:
    follow_profile(session, profile_url, log) -> ActionResult
    send_dm(session, username, message, log)  -> ActionResult
    detect_restriction(session)               -> 사유 문자열 또는 None
그래서 `macro_engine` 은 어느 엔진을 쓰든 코드가 같다(엔진만 갈아끼운다).

웹 UI 방식 대비 장점: 셀렉터가 없어서 인스타 화면이 바뀌어도 안 깨지고, 브라우저를 안 띄우니
훨씬 가볍고 빠르다. 대신 아이디/비밀번호가 필요하다 - 비밀번호는 저장하지 않고, 최초 로그인
후 받은 세션만 파일로 보관한다(다음 실행부터는 비밀번호 없이 그 세션으로 붙는다).

instagrapi 문서의 밴 회피 지침을 그대로 따른다(docs/usage-guide/best-practices.md):
계정당 고정 IP, 요청 사이 랜덤 지연(delay_range), 그리고 제한 응답(429/feedback_required)은
재시도하지 않고 즉시 중단.
"""

import os
import time

import config

# instagrapi 는 무거워서 실제로 쓸 때만 import 한다(GUI 기동 속도 + 웹 UI 엔진만 쓸 때 불필요).
_client_cls = None
_exceptions = None


def _load_instagrapi():
    global _client_cls, _exceptions
    if _client_cls is None:
        from instagrapi import Client
        from instagrapi import exceptions
        _client_cls = Client
        _exceptions = exceptions
    return _client_cls, _exceptions


class ActionResult:
    def __init__(self, ok, detail=""):
        self.ok = ok
        self.detail = detail


# 웹 UI 엔진에만 있는 개념(셀렉터 못 찾음). API 엔진에는 셀렉터가 없으므로 비어 있다.
SELECTOR_MISS_DETAILS = frozenset()


class LoginNeedsCode(Exception):
    """2단계 인증 코드 또는 이메일/SMS 챌린지 코드가 필요하다는 신호(GUI 가 받아서 입력창을 띄운다)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind  # "2fa" | "challenge"


class ApiSession:
    """instagrapi Client + 이 실행에서 감지된 제한 상태를 함께 들고 다니는 핸들."""

    def __init__(self, client, account_label):
        self.client = client
        self.account_label = account_label
        self.restriction = None      # 감지되면 사유 문자열이 들어간다(macro_engine 이 읽고 중단)
        self._user_ids = {}          # username -> user_id 캐시(같은 사람에 대한 조회 반복 방지)

    def user_id(self, username):
        if username not in self._user_ids:
            self._user_ids[username] = self.client.user_id_from_username(username)
        return self._user_ids[username]


def session_file_for(account_label):
    safe = "".join(c for c in (account_label or "default") if c.isalnum() or c in "._-")
    return os.path.join(config.APP_DIR, "sessions", f"{safe or 'default'}.json")


def _new_client(proxy=None):
    Client, _ = _load_instagrapi()
    cl = Client()
    # 요청 사이 랜덤 지연(instagrapi 권장). 행 사이의 긴 대기는 macro_engine 이 따로 준다.
    cl.delay_range = [config.API_DELAY_MIN, config.API_DELAY_MAX]
    cl.set_locale("ko_KR")
    cl.set_country("KR")
    cl.set_country_code(82)
    cl.set_timezone_offset(9 * 3600)
    if proxy:
        cl.set_proxy(proxy)
    return cl


def _save(cl, account_label):
    path = session_file_for(account_label)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cl.dump_settings(path)
    except Exception:
        pass


def load_session(account_label, proxy=None, log=print):
    """저장된 세션으로 접속을 시도한다. 성공하면 ApiSession, 없거나 만료면 None."""
    path = session_file_for(account_label)
    if not os.path.exists(path):
        return None
    cl = _new_client(proxy)
    try:
        cl.load_settings(path)
        cl.get_timeline_feed()  # 세션이 살아있는지 확인하는 가장 가벼운 호출
    except Exception as e:
        log(f"저장된 세션이 만료됐습니다({type(e).__name__}). 다시 로그인해 주세요.")
        return None
    log(f"'{account_label}' 저장된 세션으로 접속했습니다(비밀번호 입력 불필요).")
    return ApiSession(cl, account_label)


def login(account_label, username, password, verification_code=None, proxy=None, log=print):
    """아이디/비밀번호로 로그인하고 세션을 저장한다. 비밀번호는 어디에도 저장하지 않는다.

    2단계 인증/챌린지가 필요하면 LoginNeedsCode 를 올려 GUI 가 코드를 받아 다시 부르게 한다.
    """
    _, exc = _load_instagrapi()
    cl = _new_client(proxy)

    # 같은 계정으로 재로그인할 때는 이전 기기 정보를 재사용한다(매번 새 기기로 붙으면
    # 인스타가 의심 로그인으로 보고 챌린지를 띄운다 - instagrapi best-practices).
    path = session_file_for(account_label)
    if os.path.exists(path):
        try:
            cl.load_settings(path)
            cl.set_uuids(cl.get_settings().get("uuids", {}))
        except Exception:
            pass

    def _challenge_code_handler(_username, choice):
        raise LoginNeedsCode("challenge", f"인스타그램이 {choice} 로 보낸 확인 코드가 필요합니다.")

    cl.challenge_code_handler = _challenge_code_handler

    try:
        cl.login(username, password, verification_code=verification_code or "")
    except exc.TwoFactorRequired:
        raise LoginNeedsCode("2fa", "2단계 인증 코드가 필요합니다. 인증 앱/문자의 6자리 코드를 입력해 주세요.")
    except exc.BadPassword as e:
        # instagrapi 문서 주의: 비밀번호가 맞아도 인스타가 IP/기기를 불신하면 이 응답이 온다.
        raise RuntimeError(
            "인스타그램이 로그인을 거부했습니다. 아이디/비밀번호를 다시 확인해 주세요. "
            f"(같은 계정으로 다른 기기에서 로그인한 직후에도 잠시 이 응답이 옵니다) 원문: {e}")
    except exc.ChallengeRequired:
        raise LoginNeedsCode("challenge", "인스타그램 본인 확인이 필요합니다. 이메일/문자로 온 코드를 입력해 주세요.")

    _save(cl, account_label)
    log(f"'{account_label}' 로그인 성공(세션 저장 완료 - 다음부터는 비밀번호 없이 접속합니다).")
    return ApiSession(cl, account_label)


def login_by_sessionid(account_label, sessionid, proxy=None, log=print):
    """브라우저에서 직접 로그인한 뒤 sessionid 만 가져와 붙는 경로(비밀번호를 안 쓰고 싶을 때)."""
    cl = _new_client(proxy)
    cl.login_by_sessionid(sessionid.strip())
    _save(cl, account_label)
    log(f"'{account_label}' 세션 ID 로 접속했습니다.")
    return ApiSession(cl, account_label)


def logout(session, log=print):
    try:
        session.client.logout()
    except Exception:
        pass
    try:
        os.remove(session_file_for(session.account_label))
    except Exception:
        pass
    log("로그아웃했습니다(저장된 세션 삭제).")
    return True


# ---------------- 제한/차단 매핑 ----------------
# instagrapi 예외를 macro_engine 이 아는 halt 사유로 옮긴다. 여기서 '중단'으로 분류한 것은
# 재시도하면 계정만 더 위험해지는 응답들이다(instagrapi best-practices 의 429/피드백 블록 지침).

def _restriction_for(exc_module, error):
    if isinstance(error, exc_module.ChallengeRequired):
        return "challenge"
    if isinstance(error, exc_module.LoginRequired):
        return "logged_out"
    if isinstance(error, exc_module.FeedbackRequired):
        return "action_block:feedback_required"
    if isinstance(error, exc_module.PleaseWaitFewMinutes):
        return "action_block:please_wait"
    if isinstance(error, (exc_module.RateLimitError, exc_module.ClientThrottledError)):
        return "action_block:rate_limited"
    if isinstance(error, exc_module.SentryBlock):
        return "action_block:sentry_block"
    if isinstance(error, exc_module.ProxyAddressIsBlocked):
        return "action_block:proxy_blocked"
    return None


def _run(session, what, fn):
    """API 호출 1회 실행. (성공여부, 값 또는 detail) 반환.

    - 제한/차단 계열 예외: session.restriction 에 사유를 남기고 실패로 반환(엔진이 중단한다).
    - 네트워크 오류: 그대로 raise 한다 -> 엔진의 '예외' 경로로 가서 그 행은 완료 처리되지 않고
      다음 실행에 재시도된다(일시적 문제로 사람을 건너뛰면 안 되므로).
    """
    _, exc = _load_instagrapi()
    try:
        return True, fn()
    except exc.UserNotFound:
        return False, "profile_not_found"
    except Exception as error:
        reason = _restriction_for(exc, error)
        if reason:
            session.restriction = reason
            return False, f"{what}_blocked:{reason}"
        if isinstance(error, (exc.ClientConnectionError, exc.ClientThrottledError)):
            raise
        return False, f"{what}_failed: {type(error).__name__} {error}"


def detect_restriction(session):
    """이번 실행에서 인스타가 제한/차단/본인확인을 걸었으면 그 사유. 아니면 None."""
    return getattr(session, "restriction", None)


def follow_profile(session, profile_url, log=print):
    """엑셀 C열 URL 의 사용자를 팔로우. 이미 팔로우 중이면 API 호출 없이 스킵한다."""
    import excel_reader
    username = excel_reader.extract_username(profile_url)
    if not username:
        return ActionResult(False, "profile_not_found")

    ok, value = _run(session, "follow", lambda: session.user_id(username))
    if not ok:
        return ActionResult(False, value)
    user_id = value

    # 이미 팔로우 중인 사람에게 팔로우를 또 날리면 그만큼 '동작 횟수'만 쓰고 얻는 게 없다.
    ok, friendship = _run(session, "follow", lambda: session.client.user_friendship_v1(user_id))
    if ok and getattr(friendship, "following", False):
        log(f"이미 팔로우 중 (스킵): @{username}")
        return ActionResult(True, "already_following")
    if ok and getattr(friendship, "outgoing_request", False):
        log(f"이미 팔로우 요청 보냄 (스킵): @{username}")
        return ActionResult(True, "already_following")

    ok, value = _run(session, "follow", lambda: session.client.user_follow(user_id))
    if not ok:
        return ActionResult(False, value)
    if not value:
        return ActionResult(False, "follow_rejected")
    log(f"팔로우 완료: @{username}")
    return ActionResult(True, "followed")


def send_dm(session, username, message, log=print):
    """엑셀 F열 문구를 그대로 DM 발송(문구를 다듬거나 템플릿화하지 않는다)."""
    ok, value = _run(session, "dm", lambda: session.user_id(username))
    if not ok:
        return ActionResult(False, value)
    user_id = value

    ok, value = _run(session, "dm",
                     lambda: session.client.direct_send(message, user_ids=[user_id]))
    if not ok:
        return ActionResult(False, value)
    if not value:
        return ActionResult(False, "dm_rejected")
    log(f"DM 발송 완료: @{username}")
    return ActionResult(True, "sent")


def account_username(session):
    try:
        return session.client.account_info().username
    except Exception:
        return ""


def wait_between_requests():
    time.sleep(config.API_DELAY_MIN)
