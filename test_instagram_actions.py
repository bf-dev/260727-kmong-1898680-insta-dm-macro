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


if __name__ == "__main__":
    unittest.main()
