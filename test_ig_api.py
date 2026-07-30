# -*- coding: utf-8 -*-
"""ig_api(instagrapi 엔진)의 진짜 코드를 가짜 Client 로 검증한다.

인스타그램에 실제로 접속하지 않고, instagrapi 가 던지는 예외 타입만 그대로 흉내내서
  1) 이미 팔로우 중인 사람에게는 팔로우 호출을 안 하는지
  2) 제한/차단 예외가 macro_engine 이 아는 halt 사유로 정확히 옮겨지는지
  3) 네트워크 오류는 '재시도 대상'으로 그대로 올라가는지(그 행을 완료 처리하면 안 되므로)
를 확인한다. 3번이 특히 중요하다 - 여기서 삼켜버리면 일시적 오류로 사람을 통째로 건너뛴다.
"""

import json
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="insta_dm_api_test_")
os.environ["APPDATA"] = _TMP

import config
config.APP_DIR = _TMP

try:
    from instagrapi import exceptions as ig_exc
except ImportError:  # pragma: no cover
    ig_exc = None

import ig_api


class FakeFriendship:
    def __init__(self, following=False, outgoing_request=False):
        self.following = following
        self.outgoing_request = outgoing_request


class FakeClient:
    def __init__(self, friendship=None, raise_on=None, error=None):
        self.friendship = friendship or FakeFriendship()
        self.raise_on = raise_on          # "user_id" | "friendship" | "follow" | "dm"
        self.error = error
        self.follow_calls = []
        self.dm_calls = []

    def _maybe_raise(self, where):
        if self.raise_on == where and self.error is not None:
            raise self.error

    def user_id_from_username(self, username):
        self._maybe_raise("user_id")
        return f"id::{username}"

    def user_friendship_v1(self, user_id):
        self._maybe_raise("friendship")
        return self.friendship

    def user_follow(self, user_id):
        self._maybe_raise("follow")
        self.follow_calls.append(user_id)
        return True

    def direct_send(self, text, user_ids=None, thread_ids=None):
        self._maybe_raise("dm")
        self.dm_calls.append((user_ids, text))
        return object()


def _session(client):
    return ig_api.ApiSession(client, "test_label")


@unittest.skipIf(ig_exc is None, "instagrapi 미설치")
class FollowTests(unittest.TestCase):
    def test_follows_new_user(self):
        cl = FakeClient()
        s = _session(cl)
        r = ig_api.follow_profile(s, "https://www.instagram.com/alice/", log=lambda *_: None)
        self.assertTrue(r.ok)
        self.assertEqual(r.detail, "followed")
        self.assertEqual(cl.follow_calls, ["id::alice"])

    def test_skips_when_already_following(self):
        cl = FakeClient(friendship=FakeFriendship(following=True))
        s = _session(cl)
        r = ig_api.follow_profile(s, "https://www.instagram.com/alice/", log=lambda *_: None)
        self.assertTrue(r.ok)
        self.assertEqual(r.detail, "already_following")
        self.assertEqual(cl.follow_calls, [])  # 쓸데없이 동작 횟수를 쓰지 않아야 한다

    def test_skips_when_request_already_sent_to_private_account(self):
        cl = FakeClient(friendship=FakeFriendship(outgoing_request=True))
        s = _session(cl)
        r = ig_api.follow_profile(s, "https://www.instagram.com/alice/", log=lambda *_: None)
        self.assertEqual(r.detail, "already_following")
        self.assertEqual(cl.follow_calls, [])

    def test_bad_url_is_not_found(self):
        r = ig_api.follow_profile(_session(FakeClient()), "https://example.com/nope",
                                  log=lambda *_: None)
        self.assertFalse(r.ok)
        self.assertEqual(r.detail, "profile_not_found")

    def test_user_not_found_is_reported_not_raised(self):
        cl = FakeClient(raise_on="user_id", error=ig_exc.UserNotFound("no such user"))
        r = ig_api.follow_profile(_session(cl), "https://www.instagram.com/ghost/",
                                  log=lambda *_: None)
        self.assertFalse(r.ok)
        self.assertEqual(r.detail, "profile_not_found")


@unittest.skipIf(ig_exc is None, "instagrapi 미설치")
class DirectMessageTests(unittest.TestCase):
    def test_sends_message_text_verbatim(self):
        cl = FakeClient()
        s = _session(cl)
        r = ig_api.send_dm(s, "alice", "안녕하세요 협업 제안드립니다", log=lambda *_: None)
        self.assertTrue(r.ok)
        self.assertEqual(cl.dm_calls, [(["id::alice"], "안녕하세요 협업 제안드립니다")])

    def test_user_id_is_cached_across_follow_and_dm(self):
        cl = FakeClient()
        s = _session(cl)
        ig_api.follow_profile(s, "https://www.instagram.com/alice/", log=lambda *_: None)
        ig_api.send_dm(s, "alice", "hi", log=lambda *_: None)
        self.assertEqual(list(s._user_ids), ["alice"])


@unittest.skipIf(ig_exc is None, "instagrapi 미설치")
class RestrictionMappingTests(unittest.TestCase):
    """instagrapi 예외 -> halt 사유 매핑. macro_engine 의 안내 문구가 이 값에 걸린다."""

    CASES = [
        ("FeedbackRequired", "action_block:feedback_required"),
        ("PleaseWaitFewMinutes", "action_block:please_wait"),
        ("RateLimitError", "action_block:rate_limited"),
        ("SentryBlock", "action_block:sentry_block"),
        ("ChallengeRequired", "challenge"),
        ("LoginRequired", "logged_out"),
    ]

    def test_each_restriction_maps_and_halts(self):
        import macro_engine
        for exc_name, expected in self.CASES:
            with self.subTest(exc_name):
                cl = FakeClient(raise_on="follow", error=getattr(ig_exc, exc_name)("blocked"))
                s = _session(cl)
                r = ig_api.follow_profile(s, "https://www.instagram.com/alice/",
                                          log=lambda *_: None)
                self.assertFalse(r.ok)
                self.assertEqual(ig_api.detect_restriction(s), expected)
                # 사유가 사용자 안내 문구로 반드시 번역돼야 한다(원문 노출 금지)
                self.assertNotIn("사유:", macro_engine.halt_message(expected))

    def test_dm_restriction_is_also_detected(self):
        cl = FakeClient(raise_on="dm", error=ig_exc.FeedbackRequired("spam"))
        s = _session(cl)
        r = ig_api.send_dm(s, "alice", "hi", log=lambda *_: None)
        self.assertFalse(r.ok)
        self.assertEqual(ig_api.detect_restriction(s), "action_block:feedback_required")

    def test_connection_error_is_raised_for_retry_not_swallowed(self):
        """네트워크 오류를 실패로 처리하면 그 사람은 DM 못 받고 완료 처리된다. 반드시 raise."""
        cl = FakeClient(raise_on="dm", error=ig_exc.ClientConnectionError("network down"))
        s = _session(cl)
        with self.assertRaises(ig_exc.ClientConnectionError):
            ig_api.send_dm(s, "alice", "hi", log=lambda *_: None)
        self.assertIsNone(ig_api.detect_restriction(s))

    def test_unknown_error_is_a_plain_failure(self):
        cl = FakeClient(raise_on="dm", error=ValueError("weird"))
        s = _session(cl)
        r = ig_api.send_dm(s, "alice", "hi", log=lambda *_: None)
        self.assertFalse(r.ok)
        self.assertTrue(r.detail.startswith("dm_failed"))
        self.assertIsNone(ig_api.detect_restriction(s))


@unittest.skipIf(ig_exc is None, "instagrapi 미설치")
class SessionFileTests(unittest.TestCase):
    def test_session_path_is_per_account_label(self):
        a = ig_api.session_file_for("계정A")
        b = ig_api.session_file_for("account_b")
        self.assertNotEqual(a, b)
        self.assertTrue(b.endswith("account_b.json"))

    def test_missing_session_returns_none(self):
        self.assertIsNone(ig_api.load_session("never_logged_in", log=lambda *_: None))


class FakeLoginClient:
    """login() 이 실제로 부르는 최소 표면만 흉내낸다(기기 지문 고정 검증용)."""

    login_error = None
    instances = []

    def __init__(self):
        self.settings = {"uuids": {"uuid": f"device-{len(FakeLoginClient.instances)}"}}
        self.delay_range = None
        self.challenge_code_handler = None
        FakeLoginClient.instances.append(self)

    def set_locale(self, *_a, **_k):
        pass

    def set_country(self, *_a, **_k):
        pass

    def set_country_code(self, *_a, **_k):
        pass

    def set_timezone_offset(self, *_a, **_k):
        pass

    def set_proxy(self, *_a, **_k):
        pass

    def dump_settings(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.settings, f)

    def load_settings(self, path):
        with open(path, encoding="utf-8") as f:
            self.settings = json.load(f)

    def login(self, username, password, verification_code=""):
        raise type(self).login_error


@unittest.skipIf(ig_exc is None, "instagrapi 미설치")
class LoginDeviceFingerprintPinningTests(unittest.TestCase):
    """실패한 로그인 시도도 기기 지문을 저장하는지 - 안 그러면 재시도마다 다른 '기기'가
    되어 instagrapi 의 BadPassword("...even if the password is correct") 를 스스로 유발한다."""

    def setUp(self):
        self._orig_loader = ig_api._load_instagrapi
        FakeLoginClient.instances = []
        FakeLoginClient.login_error = ig_exc.BadPassword("nope")
        ig_api._load_instagrapi = lambda: (FakeLoginClient, ig_exc)
        self.label = "device_pin_test"
        self.path = ig_api.session_file_for(self.label)
        if os.path.exists(self.path):
            os.remove(self.path)

    def tearDown(self):
        ig_api._load_instagrapi = self._orig_loader
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_fingerprint_persists_across_failed_attempts(self):
        with self.assertRaises(RuntimeError):
            ig_api.login(self.label, "user", "wrongpass", log=lambda *_: None)
        self.assertTrue(os.path.exists(self.path),
                        "실패한 첫 시도도 기기 지문을 저장해야 다음 재시도가 같은 기기로 보인다")
        first_uuid = json.load(open(self.path, encoding="utf-8"))["uuids"]["uuid"]

        with self.assertRaises(RuntimeError):
            ig_api.login(self.label, "user", "wrongpass", log=lambda *_: None)
        second_uuid = json.load(open(self.path, encoding="utf-8"))["uuids"]["uuid"]

        self.assertEqual(first_uuid, second_uuid,
                          "재시도마다 다른 기기로 보이면 이 보호 장치가 무의미해진다")


if __name__ == "__main__":
    unittest.main()
