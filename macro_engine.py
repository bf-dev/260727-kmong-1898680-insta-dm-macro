# -*- coding: utf-8 -*-
"""매크로 실행기 - 엑셀 각 행에 대해 팔로우 -> DM 을 순서대로 수행하고, 사람처럼 보이도록
매 단계 사이에 랜덤 대기를 둔다. GUI 스레드를 막지 않도록 별도 스레드에서 돈다.

v1.1 부터는 '계속 돌리면 안 되는 상황'에서 스스로 멈춘다:
  - 하루 처리 상한(daily cap)에 도달
  - 인스타 차단/본인확인/로그아웃 화면 감지
  - 연속 실패 누적(계정 제한 또는 인스타 DOM 변경 의심)
멈출 때 그 행은 완료 처리하지 않는다(다음 실행에 그 사람부터 다시 시도).
"""

import random
import threading
import time

import bridge
import config
import diag_collector
import instagram_actions
import progress_store

# halt 사유별 사용자 안내 문구(GUI 로그 + 팝업에 그대로 쓴다).
HALT_MESSAGES = {
    "daily_cap": "오늘 설정한 최대 인원까지 처리해서 매크로를 멈췄습니다. "
                 "내일 다시 시작하시면 남은 사람부터 이어서 진행됩니다.",
    "challenge": "인스타그램이 본인 확인(챌린지) 화면을 띄웠습니다. 매크로를 멈췄습니다. "
                 "크롬 창에서 직접 본인 확인을 마친 뒤 다시 시작해 주세요.",
    "account_suspended": "이 계정이 인스타그램에서 정지된 것으로 보입니다. 매크로를 멈췄습니다. "
                         "다른 계정으로 전환하거나 계정 상태를 먼저 확인해 주세요.",
    "account_disabled": "이 계정이 비활성화된 것으로 보입니다. 매크로를 멈췄습니다.",
    "logged_out": "로그인이 풀렸습니다. 매크로를 멈췄습니다. "
                  "[로그인 / 계정 전환] 으로 다시 로그인한 뒤 시작해 주세요.",
    "action_block": "인스타그램이 이 계정의 활동을 제한(작업 차단)했습니다. 매크로를 멈췄습니다. "
                    "오늘은 이 계정으로 더 돌리지 마시고, 하루 이상 쉬거나 다른 계정으로 "
                    "전환한 뒤 진행해 주세요.",
    "consecutive_failures": "연속으로 실패해서 매크로를 멈췄습니다. 계정이 제한됐거나 "
                            "인스타그램 화면이 바뀐 경우입니다. 크롬 창 상태를 확인해 주세요.",
    "selector_drift": "팔로우 버튼 / DM 입력창을 연속으로 찾지 못했습니다. 인스타그램 화면 구조가 "
                      "바뀐 것으로 보여 매크로를 멈췄습니다. 제작자에게 알려주시면 바로 "
                      "업데이트해 드립니다(진단 정보는 자동 전송됐습니다).",
}


def halt_message(reason):
    """'action_block:잠시 후 다시 시도' 처럼 근거 문구가 붙어도 앞부분으로 안내를 찾는다."""
    base = (reason or "").split(":", 1)[0]
    return HALT_MESSAGES.get(base, f"매크로를 멈췄습니다. (사유: {reason})")


class MacroEngine(threading.Thread):
    """`session` 은 엔진에 따라 다르다: api 엔진이면 ig_api.ApiSession, browser 엔진이면
    selenium 드라이버. `actions` 모듈이 그 차이를 전부 흡수하므로 아래 흐름은 동일하다.

    v1.8.0 - **여기가 '[시작] 이 죽는' 버그의 진짜 원인이었다.**
    v1.1.0 부터 이 클래스는 중지 플래그를 `self._stop = threading.Event()` 로 들고 있었다.
    그런데 `threading.Thread` 는 `_stop` 이라는 **내부 메서드**를 이미 쓴다. 스레드가 끝나면
    CPython 의 `Thread._wait_for_tstate_lock()` 이 `self._stop()` 을 호출하는데, 그 자리에
    Event 객체가 덮여 있으니 `TypeError: 'Event' object is not callable` 이 난다.
    `is_alive()` 가 `_wait_for_tstate_lock()` 을 부르므로 결과는:

        **한 번이라도 끝난 엔진에 `engine.is_alive()` 를 부르면 예외가 터진다.**

    `on_start_click()` 의 첫 줄이 바로 그 호출이라, 매크로가 한 번 끝난 뒤에는 [시작] 이
    예외로 즉사했다. tkinter 는 버튼 콜백 예외를 stderr 로만 흘리고, `--noconsole` exe 에서는
    stderr 가 None 이라 **화면에도 로그에도 아무것도 안 남았다.** 고객이 본 그대로다:
    "재시작을 하고 싶어서 다시 '시작'버튼을 누르면 아무 반응이 없어요".

    고객 실측 로그(1.1.0 ~ 1.6.0, 5일치)가 이것을 그대로 증명한다: `macro_start` 앞에는
    **예외 없이 항상** `process_start`(앱 재시작)가 있다. 한 프로세스 안에서 두 번 실행된
    적이 단 한 번도 없다.

    그래서 이름을 `_stop_event` 로 바꾼다. 아래 `_ASSERT` 가 Thread 내부 이름을 다시
    덮는 순간 임포트 시점에 터지므로, 같은 실수가 두 번 나올 수 없다.
    """

    # Thread 가 자기 것으로 쓰는 이름들. 여기에 뭘 대입하면 스레드가 끝나는 순간 깨진다.
    _THREAD_RESERVED = ("_stop", "_wait_for_tstate_lock", "_bootstrap", "_bootstrap_inner",
                        "_set_ident", "_set_tstate_lock", "_reset_internal_locks",
                        "_started", "_target", "_args", "_kwargs", "_daemonic", "_name")

    def __init__(self, session, rows, excel_path, account_label, log_cb, done_cb=None,
                 daily_cap=None, halt_cb=None, actions=None):
        super().__init__(daemon=True)
        self.session = session
        self.actions = actions or instagram_actions
        self.rows = rows
        self.excel_path = excel_path
        self.account_label = account_label
        self.log_cb = log_cb or (lambda *_: None)
        self.done_cb = done_cb or (lambda *_: None)
        self.halt_cb = halt_cb or (lambda *_: None)
        self.daily_cap = int(daily_cap) if daily_cap else config.DEFAULT_DAILY_CAP
        self._stop_event = threading.Event()
        self.stats = {"followed": 0, "dm_sent": 0, "failed": 0, "skipped": 0}
        self.halt_reason = None
        self.finished = False       # run() 이 끝까지 갔는가(스레드 상태와 별개로 우리가 안다)
        self._fail_streak = 0
        self._selector_miss_streak = 0

    def stop(self):
        self._stop_event.set()

    def stop_requested(self):
        return self._stop_event.is_set()

    def is_running(self):
        """`is_alive()` 를 직접 부르지 않는다. 끝난 스레드에 물어보다 예외가 나면 그것만으로
        [시작] 이 죽기 때문이다. 판단이 불가능하면 '안 돌고 있다' 로 본다(= 막지 않는다)."""
        if self.finished:
            return False
        try:
            return bool(self.is_alive())
        except Exception:
            return False

    def _log(self, msg):
        try:
            self.log_cb(msg)
        except Exception:
            pass

    def _halt(self, reason, row=None):
        """중단 확정. 사유를 남기고 사용자에게 안내한다. 호출한 쪽은 곧바로 루프를 빠져나올 것."""
        self.halt_reason = reason
        message = halt_message(reason)
        self._log(f"[중단] {message}")
        bridge.remote_log("macro_halt", f"account={self.account_label} reason={reason}", force=True)
        if row is not None and not reason.startswith("daily_cap"):
            self._diag(row, f"halt_{reason.split(':', 1)[0]}", reason)
        try:
            self.halt_cb(reason, message)
        except Exception:
            pass

    def _restricted(self, row, stage):
        """인스타 차단/본인확인/로그아웃 화면이면 중단하고 True. 아니면 False."""
        try:
            reason = self.actions.detect_restriction(self.session)
        except Exception:
            return False
        if not reason:
            return False
        self._log(f"[{row.row_no}행] {stage} 직후 인스타그램 제한 화면 감지: {reason}")
        self._halt(reason, row=row)
        return True

    def run(self):
        """스레드 본체는 **무슨 일이 있어도** `finished` 를 세우고 끝난다. 그래야 다음 [시작]
        이 '아직 도는 중' 으로 오판하지 않는다(예전엔 예외로 죽은 스레드가 그대로 남았다)."""
        try:
            self._run_body()
        except Exception as e:
            self.halt_reason = f"engine_crash:{e}"
            self._log(f"[오류] 매크로 실행 중 예기치 못한 오류로 멈췄습니다: {e}")
            try:
                bridge.remote_log("macro_crash",
                                  f"account={self.account_label} error={e}", force=True)
            except Exception:
                pass
            try:
                self.halt_cb("engine_crash", f"매크로가 오류로 멈췄습니다: {e}\n"
                                             f"[시작] 을 다시 누르면 남은 사람부터 이어서 진행됩니다.")
            except Exception:
                pass
        finally:
            self.finished = True

    def _run_body(self):
        done_rows = progress_store.load_done_rows(self.excel_path, self.account_label)
        total = len(self.rows)
        pending = [r for r in self.rows if r.row_no not in done_rows]
        used_today = progress_store.get_daily_count(self.account_label)
        remaining_today = max(0, self.daily_cap - used_today)
        self._log(f"총 {total}행 중 이미 완료 {total - len(pending)}행, 남은 {len(pending)}행 진행합니다.")
        self._log(f"오늘 이 계정으로 {used_today}명 처리했습니다 "
                  f"(하루 최대 {self.daily_cap}명 / 오늘 남은 여유 {remaining_today}명).")
        bridge.remote_log(
            "macro_start",
            f"account={self.account_label} total={total} pending={len(pending)} "
            f"daily_cap={self.daily_cap} used_today={used_today}",
            force=True,
        )

        if remaining_today <= 0:
            self._halt("daily_cap")
            self._finish()
            return

        for idx, row in enumerate(pending):
            if self._stop_event.is_set():
                self._log("중지 요청으로 매크로를 멈췄습니다.")
                break

            if progress_store.get_daily_count(self.account_label) >= self.daily_cap:
                self._halt("daily_cap")
                break

            self._log(f"[{row.row_no}행] @{row.username} 처리 시작 ({idx + 1}/{len(pending)})")
            try:
                follow_result = self.actions.follow_profile(self.session, row.url, log=self._log)
            except Exception as e:
                self._log(f"[{row.row_no}행] 팔로우 중 오류(다음 실행에 재시도): {e}")
                self._diag(row, "follow_exception", str(e))
                bridge.remote_log("row_error", f"row={row.row_no} follow_exception={e}")
                continue  # 완료 표시 안 함 -> 다음 실행에 재시도

            if follow_result.ok:
                # already_following 은 '이미 팔로우 중' 이라 새로 센 팔로우가 아니다.
                # followed_unverified 는 클릭은 들어갔지만 버튼 상태 확인을 못 한 경우 - 센다.
                if follow_result.detail in ("followed", "followed_unverified"):
                    self.stats["followed"] += 1
                self._log(f"[{row.row_no}행] 팔로우 결과: {follow_result.detail}")
            else:
                self._log(f"[{row.row_no}행] 팔로우 실패: {follow_result.detail} (DM은 계속 시도)")
                self._note_failure(row, follow_result.detail, stage="follow")

            # 팔로우는 인스타가 가장 먼저 막는 동작이다. 여기서 차단 화면이 떴으면 DM 은 시도조차
            # 하지 않고 즉시 멈춘다(완료 처리도 안 하므로 다음 실행에 이 사람부터 다시 시도).
            if self._restricted(row, "팔로우"):
                break

            if self._stop_event.is_set():
                break

            time.sleep(random.uniform(config.DELAY_AFTER_FOLLOW_MIN, config.DELAY_AFTER_FOLLOW_MAX))

            try:
                dm_result = self.actions.send_dm(self.session, row.username, row.message, log=self._log)
            except Exception as e:
                self._log(f"[{row.row_no}행] DM 발송 중 오류(다음 실행에 재시도): {e}")
                self._diag(row, "dm_exception", str(e))
                bridge.remote_log("row_error", f"row={row.row_no} dm_exception={e}")
                continue  # 완료 표시 안 함

            if dm_result.ok:
                self.stats["dm_sent"] += 1
                self._log(f"[{row.row_no}행] DM 발송 완료")
            else:
                self.stats["failed"] += 1
                self._log(f"[{row.row_no}행] DM 실패: {dm_result.detail}")
                self._note_failure(row, dm_result.detail, stage="dm")

            if self._restricted(row, "DM"):
                break

            # 여기까지 왔으면(성공이든 명시적 실패든) 같은 사람에게 다시 DM 이 나가지 않도록
            # 반드시 완료 처리한다. 재시도가 필요한 건 위의 '예외'/'중단' 경로뿐이다.
            progress_store.mark_done(self.excel_path, self.account_label, row.row_no)
            used_today = progress_store.bump_daily_count(self.account_label)
            # 화면 갱신 실패(창이 닫혔다, Tk 호출이 스레드에서 거절됐다 등)가 **실행 자체를**
            # 끝내면 안 된다. 예전엔 여기서 난 예외가 그대로 스레드를 죽였고, 고객 눈에는
            # "1명만 보내고 멈췄다" 로 보였다.
            try:
                self.done_cb(row.row_no, follow_result.ok, dm_result.ok)
            except Exception as e:
                bridge.remote_log("row_done_cb_error", f"row={row.row_no} error={e}")

            bridge.remote_log(
                "row_done",
                f"row={row.row_no} user={row.username} follow={follow_result.detail} "
                f"dm={dm_result.detail} used_today={used_today}/{self.daily_cap}",
            )

            if follow_result.ok and dm_result.ok:
                self._fail_streak = 0
                self._selector_miss_streak = 0
            elif self._selector_miss_streak >= config.SELECTOR_MISS_HALT_STREAK:
                self._halt("selector_drift", row=row)
                break
            elif self._fail_streak >= config.CONSECUTIVE_FAILURE_HALT:
                self._halt("consecutive_failures", row=row)
                break

            if used_today >= self.daily_cap:
                self._halt("daily_cap")
                break

            if idx < len(pending) - 1 and not self._stop_event.is_set():
                wait_s = random.uniform(config.DELAY_BETWEEN_PEOPLE_MIN, config.DELAY_BETWEEN_PEOPLE_MAX)
                self._log(f"다음 사람까지 {wait_s:.0f}초 대기합니다 (계정 보호를 위한 랜덤 간격)...")
                for _ in range(int(wait_s * 2)):
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.5)

        self._finish()

    def _finish(self):
        used_today = progress_store.get_daily_count(self.account_label)
        summary = (f"account={self.account_label} followed={self.stats['followed']} "
                   f"dm_sent={self.stats['dm_sent']} failed={self.stats['failed']} "
                   f"used_today={used_today}/{self.daily_cap} halt={self.halt_reason or 'none'}")
        self._log(f"매크로 종료. {summary}")
        bridge.upload_run(summary, kind="run-summary")

    def _note_failure(self, row, detail, stage):
        """실패 1건 기록. 셀렉터를 못 찾은 경우는 'DOM 이 바뀐 것'이라 따로 표시해 올린다."""
        self._fail_streak += 1
        if detail in self.actions.SELECTOR_MISS_DETAILS:
            self._selector_miss_streak += 1
            self._log(f"[{row.row_no}행] 인스타그램 화면에서 요소를 찾지 못했습니다({detail}). "
                      f"진단 정보를 전송합니다.")
            bridge.remote_log(
                "selector_miss",
                f"stage={stage} detail={detail} row={row.row_no} user={row.username} "
                f"streak={self._selector_miss_streak}",
                force=True,
            )
            self._diag(row, f"selector_miss_{detail}", f"stage={stage}", kind="selector-miss")
        else:
            self._selector_miss_streak = 0
            self._diag(row, f"{stage}_failed_{detail}")

    def _diag(self, row, label, detail="", kind="diag"):
        try:
            zpath = diag_collector.capture_zip(
                self.session, f"{label}_row{row.row_no}", extra_text=detail)
            bridge.upload_run(
                f"row={row.row_no} user={row.username} {label}: {detail}",
                zip_path=zpath, kind=kind,
            )
        except Exception:
            pass


def _assert_no_thread_name_collision():
    """임포트 시점 안전장치. `MacroEngine` 인스턴스가 `Thread` 내부 이름을 덮으면 그 순간 터진다.

    v1.1.0~v1.7.0 은 `self._stop = Event()` 로 `Thread._stop` 을 덮었고, 그 결과 매크로가 한 번
    끝난 뒤 `engine.is_alive()` 가 TypeError 를 던져 [시작] 버튼이 조용히 죽었다. 고객은 앱을
    껐다 켜야만 다시 돌릴 수 있었다(5일치 로그가 그대로 증명). 다시는 조용히 못 들어오게 막는다.
    """
    class _Probe(MacroEngine):
        def __init__(self):
            MacroEngine.__init__(self, None, [], "", "check", log_cb=None)

    probe = _Probe()
    bad = [n for n in MacroEngine._THREAD_RESERVED if n in probe.__dict__
           and callable(getattr(threading.Thread, n, None))]
    if bad:
        raise RuntimeError(
            f"MacroEngine 이 threading.Thread 의 내부 이름을 덮었습니다: {bad}. "
            f"끝난 스레드에 is_alive() 를 부르는 순간 깨지고 [시작] 이 조용히 죽습니다.")
    # 끝난 스레드에 물어봐도 안전한가를 실제로 확인한다(가장 확실한 증거).
    probe.finished = True
    probe.is_running()


_assert_no_thread_name_collision()
