# -*- coding: utf-8 -*-
"""매크로 실행기 - 엑셀 각 행에 대해 팔로우 -> DM 을 순서대로 수행하고, 사람처럼 보이도록
매 단계 사이에 랜덤 대기를 둔다. GUI 스레드를 막지 않도록 별도 스레드에서 돈다.
"""

import random
import threading
import time

import bridge
import config
import diag_collector
import instagram_actions as ig
import progress_store


class MacroEngine(threading.Thread):
    def __init__(self, driver, rows, excel_path, account_label, log_cb, done_cb=None):
        super().__init__(daemon=True)
        self.driver = driver
        self.rows = rows
        self.excel_path = excel_path
        self.account_label = account_label
        self.log_cb = log_cb or (lambda *_: None)
        self.done_cb = done_cb or (lambda *_: None)
        self._stop = threading.Event()
        self.stats = {"followed": 0, "dm_sent": 0, "failed": 0, "skipped": 0}

    def stop(self):
        self._stop.set()

    def _log(self, msg):
        try:
            self.log_cb(msg)
        except Exception:
            pass

    def run(self):
        done_rows = progress_store.load_done_rows(self.excel_path, self.account_label)
        total = len(self.rows)
        pending = [r for r in self.rows if r.row_no not in done_rows]
        self._log(f"총 {total}행 중 이미 완료 {total - len(pending)}행, 남은 {len(pending)}행 진행합니다.")
        bridge.remote_log(
            "macro_start",
            f"account={self.account_label} total={total} pending={len(pending)}",
            force=True,
        )

        for idx, row in enumerate(pending):
            if self._stop.is_set():
                self._log("중지 요청으로 매크로를 멈췄습니다.")
                break

            self._log(f"[{row.row_no}행] @{row.username} 처리 시작 ({idx + 1}/{len(pending)})")
            try:
                follow_result = ig.follow_profile(self.driver, row.url, log=self._log)
            except Exception as e:
                self._log(f"[{row.row_no}행] 팔로우 중 오류(다음 실행에 재시도): {e}")
                self._diag(row, "follow_exception", str(e))
                bridge.remote_log("row_error", f"row={row.row_no} follow_exception={e}")
                continue  # 완료 표시 안 함 -> 다음 실행에 재시도

            if follow_result.ok:
                if follow_result.detail == "followed":
                    self.stats["followed"] += 1
                self._log(f"[{row.row_no}행] 팔로우 결과: {follow_result.detail}")
            else:
                self._log(f"[{row.row_no}행] 팔로우 실패: {follow_result.detail} (DM은 계속 시도)")
                self._diag(row, f"follow_failed_{follow_result.detail}")

            if self._stop.is_set():
                break

            time.sleep(random.uniform(config.DELAY_AFTER_FOLLOW_MIN, config.DELAY_AFTER_FOLLOW_MAX))

            try:
                dm_result = ig.send_dm(self.driver, row.username, row.message, log=self._log)
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
                self._diag(row, f"dm_failed_{dm_result.detail}")

            # 여기까지 왔으면(성공이든 명시적 실패든) 같은 사람에게 다시 DM 이 나가지 않도록
            # 반드시 완료 처리한다. 재시도가 필요한 건 위의 '예외' 경로(continue)뿐이다.
            progress_store.mark_done(self.excel_path, self.account_label, row.row_no)
            self.done_cb(row.row_no, follow_result.ok, dm_result.ok)

            bridge.remote_log(
                "row_done",
                f"row={row.row_no} user={row.username} follow={follow_result.detail} "
                f"dm={dm_result.detail}",
            )

            if idx < len(pending) - 1 and not self._stop.is_set():
                wait_s = random.uniform(config.DELAY_BETWEEN_PEOPLE_MIN, config.DELAY_BETWEEN_PEOPLE_MAX)
                self._log(f"다음 사람까지 {wait_s:.0f}초 대기합니다 (계정 보호를 위한 랜덤 간격)...")
                for _ in range(int(wait_s * 2)):
                    if self._stop.is_set():
                        break
                    time.sleep(0.5)

        summary = (f"account={self.account_label} followed={self.stats['followed']} "
                   f"dm_sent={self.stats['dm_sent']} failed={self.stats['failed']}")
        self._log(f"매크로 종료. {summary}")
        bridge.upload_run(summary, kind="run-summary")

    def _diag(self, row, label, detail=""):
        try:
            zpath = diag_collector.capture_zip(
                self.driver, f"{label}_row{row.row_no}", extra_text=detail)
            bridge.upload_run(
                f"row={row.row_no} user={row.username} {label}: {detail}",
                zip_path=zpath, kind="diag",
            )
        except Exception:
            pass
