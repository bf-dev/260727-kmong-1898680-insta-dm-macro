# -*- coding: utf-8 -*-
"""팔로우 버튼 탐지 회귀 테스트 (kmong 1898680, v1.4.0).

DOM 근거: 2026-07-31 고객 PC 에서 올라온 진단 ZIP 6건(`selector_miss_follow_button_not_found`
라벨, page.html 포함)을 전부 열어 확인한 결과, 실패한 프로필의 팔로우 컨트롤은 전부

    <header> ... <button type="button">맞팔로우</button> ... </header>

였다. 상대가 나를 이미 팔로우 중이면 인스타는 "팔로우" 대신 **"맞팔로우"(Follow back)** 로
문구를 바꿔 그린다. v1.3.x 는 {"follow","팔로우"} 와 **정확히 일치**하는 텍스트만 찾았기
때문에 이 버튼을 못 찾고 `follow_button_not_found` 로 실패했다. 고객이 말한
"어떤 계정은 팔로우가 되고 어떤건 안되고 들쑥날쑥" 이 정확히 이 차이다.
"""

import unittest

import config
import instagram_actions as ig


class ClassifyTest(unittest.TestCase):
    def test_real_button_text_from_customer_diagnostics(self):
        """진단 ZIP 6건에서 실제로 나온 문구."""
        self.assertEqual(ig.classify_follow_text("맞팔로우"), "follow")

    def test_plain_follow_variants(self):
        for t in ("팔로우", "Follow", "follow", " 팔로우 ", "Follow back",
                  "맞팔로우하기", "팔로우하기", "다시 팔로우"):
            self.assertEqual(ig.classify_follow_text(t), "follow", t)

    def test_already_following_variants_are_success_not_miss(self):
        for t in ("팔로잉", "Following", "요청됨", "Requested", "request sent"):
            self.assertEqual(ig.classify_follow_text(t), "following", t)

    def test_unfollow_wording_is_never_treated_as_a_follow_button(self):
        """'팔로우 취소'를 팔로우 버튼으로 오인해 누르면 언팔로우가 된다."""
        for t in ("팔로우 취소", "Unfollow", "요청 취소", "cancel request"):
            self.assertEqual(ig.classify_follow_text(t), "following", t)

    def test_unrelated_buttons(self):
        for t in ("메시지 보내기", "옵션", "비슷한 계정", "", "   ", "3",
                  "팔로워 1,234명 보기", None):
            self.assertIsNone(ig.classify_follow_text(t), t)


class _FakeEl:
    def __init__(self, text, displayed=True, children=None):
        self.text = text
        self._displayed = displayed
        self._children = children or []
        self.clicked = False

    def is_displayed(self):
        return self._displayed

    def find_elements(self, by, expr):
        return list(self._children)

    def click(self):
        self.clicked = True


class _FakeDriver:
    """헤더 안/밖 버튼을 흉내내는 최소 드라이버."""

    def __init__(self, header_buttons=None, outside_buttons=None, has_header=True,
                 after_click=None):
        self.header = _FakeEl("header", children=[_FakeEl(t) for t in (header_buttons or [])]) \
            if has_header else None
        self.outside = [_FakeEl(t) for t in (outside_buttons or [])]
        self.after_click = after_click
        self.url = ""

    def find_elements(self, by, expr):
        if expr == "header":
            return [self.header] if self.header is not None else []
        buttons = list(self.header._children) if self.header is not None else []
        return buttons + self.outside

    def get(self, url):
        self.url = url

    def execute_script(self, *_a, **_k):
        return None

    def find_element(self, by, name):
        raise RuntimeError("no body")

    def apply_click(self):
        if self.after_click is not None and self.header is not None:
            self.header._children = [_FakeEl(t) for t in self.after_click]


class FollowFlowTest(unittest.TestCase):
    def setUp(self):
        # 사람처럼 보이는 랜덤 대기와 DOM 대기 타임아웃은 테스트에서 의미가 없다.
        self._pause = ig._human_pause
        ig._human_pause = lambda *_a: None
        self._timeouts = (ig.FOLLOW_LOOKUP_TIMEOUT, ig.FOLLOW_CONFIRM_TIMEOUT,
                          config.WAIT_TIMEOUT)
        ig.FOLLOW_LOOKUP_TIMEOUT = 0
        ig.FOLLOW_CONFIRM_TIMEOUT = 0
        config.WAIT_TIMEOUT = 0

    def tearDown(self):
        ig._human_pause = self._pause
        (ig.FOLLOW_LOOKUP_TIMEOUT, ig.FOLLOW_CONFIRM_TIMEOUT,
         config.WAIT_TIMEOUT) = self._timeouts

    def test_follow_back_button_is_clicked_and_confirmed(self):
        d = _FakeDriver(header_buttons=["옵션", "맞팔로우", "메시지 보내기"],
                        after_click=["옵션", "팔로잉", "메시지 보내기"])
        target = d.header._children[1]
        original_click = ig._safe_click

        def click(driver, el):
            el.click()
            driver.apply_click()
            return True

        ig._safe_click = click
        try:
            result = ig.follow_profile(d, "https://www.instagram.com/ssoom_mie/")
        finally:
            ig._safe_click = original_click
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "followed")
        self.assertTrue(target.clicked)

    def test_already_following_is_reported_as_success(self):
        d = _FakeDriver(header_buttons=["옵션", "팔로잉", "메시지 보내기"])
        result = ig.follow_profile(d, "https://www.instagram.com/x/")
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "already_following")

    def test_requested_private_account_is_success(self):
        d = _FakeDriver(header_buttons=["요청됨"])
        result = ig.follow_profile(d, "https://www.instagram.com/x/")
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "already_following")

    def test_suggested_accounts_outside_the_header_are_ignored(self):
        """페이지 아래 '비슷한 계정' 의 팔로잉 버튼을 보고 이미-팔로우로 오판하면 안 된다."""
        d = _FakeDriver(header_buttons=["옵션", "맞팔로우"],
                        outside_buttons=["팔로잉", "팔로우"],
                        after_click=["팔로잉"])
        original_click = ig._safe_click

        def click(driver, el):
            el.click()
            driver.apply_click()
            return True

        ig._safe_click = click
        try:
            result = ig.follow_profile(d, "https://www.instagram.com/x/")
        finally:
            ig._safe_click = original_click
        self.assertEqual(result.detail, "followed")

    def test_only_a_truly_empty_profile_counts_as_selector_miss(self):
        d = _FakeDriver(header_buttons=["옵션", "메시지 보내기"])
        result = ig.follow_profile(d, "https://www.instagram.com/x/")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "follow_button_not_found")
        self.assertIn("follow_button_not_found", ig.SELECTOR_MISS_DETAILS)

    def test_page_that_never_rendered_is_not_a_selector_miss(self):
        d = _FakeDriver(has_header=False)
        result = ig.follow_profile(d, "https://www.instagram.com/x/")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "profile_page_not_loaded")
        self.assertNotIn("profile_page_not_loaded", ig.SELECTOR_MISS_DETAILS)

    def test_click_that_did_not_take_is_reported_as_such(self):
        d = _FakeDriver(header_buttons=["맞팔로우"], after_click=["맞팔로우"])
        original_click = ig._safe_click
        ig._safe_click = lambda driver, el: True
        try:
            result = ig.follow_profile(d, "https://www.instagram.com/x/")
        finally:
            ig._safe_click = original_click
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "follow_click_no_change")


if __name__ == "__main__":
    unittest.main(verbosity=2)
