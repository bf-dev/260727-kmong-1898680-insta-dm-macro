# -*- coding: utf-8 -*-
"""wait_for_manual_login() 회귀 테스트 (kmong 1898680, 2026-07-31 고객 리포트).

리포트: 로그인 대기 중 크롬 창이 ~3초마다 새로고침돼 아이디/비번 입력값이 지워져
로그인을 끝낼 수 없었다. 원인: 예전 wait_for_manual_login() 이 매 폴링(2초)마다
is_logged_in() 을 그대로 호출했고, is_logged_in() 은 driver.get(INSTAGRAM_BASE) 로
페이지를 통째로 새로고침했다. 이 테스트는 (1) 폴링 루프가 절대 driver.get()/refresh()
를 부르지 않는지, (2) 로그인 미완료/완료 판정이 올바른지를 고정한다.
"""

import unittest

import instagram_actions as ig


class FakeElement:
    def __init__(self, displayed=True):
        self._displayed = displayed

    def is_displayed(self):
        return self._displayed


class FakeDriver:
    """selenium 없이 폴링 로직만 검증하기 위한 가짜 드라이버.
    get()/refresh() 호출 여부를 카운트해서, 폴링 중 절대 안 불려야 함을 증명한다."""

    def __init__(self, url, password_visible, has_session_cookie):
        self.current_url = url
        self._password_visible = password_visible
        self._has_session_cookie = has_session_cookie
        self.get_calls = 0
        self.refresh_calls = 0

    def get(self, url):
        self.get_calls += 1

    def refresh(self):
        self.refresh_calls += 1

    def find_elements(self, by, selector):
        if "password" in selector:
            return [FakeElement(displayed=self._password_visible)] if self._password_visible else []
        return []

    def get_cookies(self):
        if self._has_session_cookie:
            return [{"name": "sessionid", "value": "abc123"}]
        return [{"name": "csrftoken", "value": "xyz"}]

    def execute_script(self, script):
        return "complete"


class LoginAppearsCompleteTests(unittest.TestCase):
    def test_still_on_login_page_is_incomplete(self):
        d = FakeDriver("https://www.instagram.com/accounts/login/", password_visible=True,
                       has_session_cookie=False)
        self.assertFalse(ig._login_appears_complete(d))

    def test_password_field_still_visible_is_incomplete_even_off_login_url(self):
        # 2FA/체크포인트 중간 화면 등 URL은 바뀌었지만 여전히 비번 입력을 요구하는 경우
        d = FakeDriver("https://www.instagram.com/challenge/", password_visible=True,
                       has_session_cookie=False)
        self.assertFalse(ig._login_appears_complete(d))

    def test_off_login_no_password_but_no_session_cookie_is_incomplete(self):
        d = FakeDriver("https://www.instagram.com/", password_visible=False,
                       has_session_cookie=False)
        self.assertFalse(ig._login_appears_complete(d))

    def test_real_login_success_is_complete(self):
        d = FakeDriver("https://www.instagram.com/", password_visible=False,
                       has_session_cookie=True)
        self.assertTrue(ig._login_appears_complete(d))

    def test_never_navigates(self):
        d = FakeDriver("https://www.instagram.com/accounts/login/", password_visible=True,
                       has_session_cookie=False)
        ig._login_appears_complete(d)
        self.assertEqual(d.get_calls, 0)
        self.assertEqual(d.refresh_calls, 0)


class IsLoggedInTests(unittest.TestCase):
    """is_logged_in(): 2026-07-31 계정 전환 리포트("Jimin2 로 바꿔도 Jimin 세션에 머무는 것
    처럼 보임") 회귀 테스트. 새 프로필의 마케팅 랜딩 화면(비밀번호 칸 없음, 세션 쿠키도 없음)을
    로그인된 것으로 오판하면 안 된다 - 그러면 앱이 새 로그인을 요청하지 않고 넘어가 버린다."""

    def test_login_url_is_not_logged_in(self):
        d = FakeDriver("https://www.instagram.com/accounts/login/", password_visible=True,
                       has_session_cookie=False)
        self.assertFalse(ig.is_logged_in(d))

    def test_password_field_visible_is_not_logged_in(self):
        d = FakeDriver("https://www.instagram.com/", password_visible=True,
                       has_session_cookie=False)
        self.assertFalse(ig.is_logged_in(d))

    def test_fresh_profile_landing_page_without_session_cookie_is_not_logged_in(self):
        # 완전히 새 프로필: 비밀번호 칸도 안 보이고(랜딩 화면) 세션 쿠키도 없다 - 로그인 아님.
        d = FakeDriver("https://www.instagram.com/", password_visible=False,
                       has_session_cookie=False)
        self.assertFalse(ig.is_logged_in(d))

    def test_real_session_cookie_present_is_logged_in(self):
        d = FakeDriver("https://www.instagram.com/", password_visible=False,
                       has_session_cookie=True)
        self.assertTrue(ig.is_logged_in(d))


class WaitForManualLoginNoReloadTests(unittest.TestCase):
    def test_polling_loop_never_calls_get_or_refresh(self):
        """핵심 회귀 테스트: 로그인 대기 루프가 완료될 때까지 driver.get()/refresh() 를
        단 한 번도 호출하지 않아야 한다(사람이 타이핑 중인 폼을 지우면 안 되므로)."""
        d = FakeDriver("https://www.instagram.com/accounts/login/", password_visible=True,
                       has_session_cookie=False)
        poll_count = [0]

        def poll_cb():
            poll_count[0] += 1
            if poll_count[0] >= 3:
                # 3번째 폴링 시점에 "사람이 로그인을 완료"했다고 시뮬레이션
                d.current_url = "https://www.instagram.com/"
                d._password_visible = False
                d._has_session_cookie = True

        original_sleep = ig.time.sleep
        ig.time.sleep = lambda s: None
        try:
            ok = ig.wait_for_manual_login(d, timeout_s=30, poll_cb=poll_cb)
        finally:
            ig.time.sleep = original_sleep

        self.assertTrue(ok)
        self.assertEqual(d.get_calls, 0, "폴링 중 driver.get() 호출 금지 (새로고침 버그 재발)")
        self.assertEqual(d.refresh_calls, 0, "폴링 중 driver.refresh() 호출 금지")

    def test_times_out_without_ever_reloading(self):
        d = FakeDriver("https://www.instagram.com/accounts/login/", password_visible=True,
                       has_session_cookie=False)
        original_sleep = ig.time.sleep
        calls = [0]

        def fake_sleep(s):
            calls[0] += 1
            if calls[0] > 3:
                raise SystemExit("loop did not terminate")

        ig.time.sleep = fake_sleep
        try:
            # deadline already passed -> loop body should not even execute once
            ok = ig.wait_for_manual_login(d, timeout_s=-1, poll_cb=None)
        finally:
            ig.time.sleep = original_sleep

        self.assertFalse(ok)
        self.assertEqual(d.get_calls, 0)
        self.assertEqual(d.refresh_calls, 0)


class StaleTypingTests(unittest.TestCase):
    """_type_into: 타이핑 도중 요소가 stale 이 돼도 다시 찾아 이어친다.
    (2026-07-31 고객 리포트: 새 메시지 검색창이 리렌더되며 4,5번째 행이 연속 예외로 죽음)"""

    def test_retries_after_stale_element(self):
        from selenium.common.exceptions import StaleElementReferenceException

        class Flaky:
            def __init__(self, fail_after):
                self.fail_after = fail_after
                self.typed = []

            def clear(self):
                pass

            def send_keys(self, ch):
                if self.fail_after is not None and len(self.typed) >= self.fail_after:
                    raise StaleElementReferenceException("gone")
                self.typed.append(ch)

        boxes = [Flaky(fail_after=2), Flaky(fail_after=None)]
        state = {"i": 0}

        def finder():
            el = boxes[min(state["i"], len(boxes) - 1)]
            state["i"] += 1
            return el

        original_sleep = ig.time.sleep
        ig.time.sleep = lambda s: None
        try:
            ok = ig._type_into(None, finder, "abcdef")
        finally:
            ig.time.sleep = original_sleep

        self.assertTrue(ok, "stale 이 나면 새 요소를 찾아 다시 쳐야 한다")
        self.assertEqual("".join(boxes[1].typed), "abcdef")

    def test_gives_up_when_element_never_appears(self):
        self.assertFalse(ig._type_into(None, lambda: None, "abc"))


class ProfileDirIsolationTests(unittest.TestCase):
    """별명이 다르면 크롬 프로필도 반드시 달라야 한다. 같아지면 계정 전환이 안 된다."""

    def test_labels_differing_only_by_stripped_chars_do_not_collide(self):
        import config
        a = config.profile_dir_for("Jimin")
        b = config.profile_dir_for("Jimin!")
        c = config.profile_dir_for("Ji min")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(b, c)

    def test_same_label_is_stable(self):
        import config
        self.assertEqual(config.profile_dir_for("Jimin2"), config.profile_dir_for("Jimin2"))


if __name__ == "__main__":
    unittest.main()
