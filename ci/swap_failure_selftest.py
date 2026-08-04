# -*- coding: utf-8 -*-
"""스왑이 **실패하는** 길을 진짜 윈도우에서 끝까지 돌려 보는 CI 자체검증 (kmong 1898680).

`swap_selftest.py` 는 성공 경로만 본다. 그런데 이 고객을 두 번 옛 버전에 묶어 둔 것은
성공 경로가 아니라 실패 경로였고, 그때 우리가 받은 정보는 `사유 미기록` 한 줄이었다.
그래서 여기서는 실패를 **일부러 만들어** 두 가지를 확인한다:

  A) 대상 exe 가 다른 프로세스에 잠겨 있어도(AV 실시간 검사/늦게 죽는 자식 프로세스)
     스왑 스크립트가 재시도해서 결국 교체에 성공한다.
  B) 회복 불가능한 실패(staged 파일이 사라짐)에서도 결과 파일에 **비어 있지 않은 사유**가
     남고, 대상 exe 는 그대로 살아남고, 백오프 마커는 지워지지 않는다.
     -> 다음 실행의 앱이 그 사유를 그대로 서버로 올린다.

하나라도 어긋나면 exit 1 로 빌드를 깬다.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import updater  # noqa: E402

WINDIR = os.getenv("SystemRoot", r"C:\Windows")
OLD_SRC = os.path.join(WINDIR, "System32", "where.exe")
NEW_SRC = os.path.join(WINDIR, "System32", "hostname.exe")

# 잠금 유지 시간(초). 스왑 스크립트의 재시도 예산(SWAP_RETRIES * SWAP_RETRY_SLEEP)보다
# 확실히 짧아야 '재시도해서 이긴다' 를 증명할 수 있다.
LOCK_SECONDS = 8


def _make_case(name):
    root = tempfile.mkdtemp(prefix="swapfail_")
    work = os.path.join(root, f"한글 폴더 {name}")
    os.makedirs(work)
    target = os.path.join(work, "인스타DM매크로.exe")
    shutil.copy2(OLD_SRC, target)
    staged = target + ".update-9.9.9.tmp"
    shutil.copy2(NEW_SRC, staged)
    result_path = os.path.join(work, "update_result.json")
    state_path = os.path.join(work, "update_state.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"target": "9.9.9", "phase": "swap_launched"}, f)
    return root, work, target, staged, result_path, state_path


def _run_swap(target, staged, result_path, state_path, expected_size):
    script, mode = updater.write_swap_script(
        current_exe=target, staged=staged, latest="9.9.9", pid=0xFFFFFF,
        result_path=result_path, state_path=state_path, expected_size=expected_size)
    proc = subprocess.Popen(updater.swap_command(script, mode))
    proc.wait(timeout=300)
    deadline = time.time() + 90
    while not os.path.exists(result_path) and time.time() < deadline:
        time.sleep(1)
    if not os.path.exists(result_path):
        return None
    with open(result_path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def case_locked_target_is_retried_until_it_wins():
    """A) 대상 exe 를 다른 프로세스가 붙잡고 있어도 재시도해서 교체에 성공한다."""
    root, _work, target, staged, result_path, state_path = _make_case("잠금")
    try:
        new_bytes = open(staged, "rb").read()
        # FileShare=None 으로 열면 rename/move 가 '다른 프로세스가 사용 중' 으로 실패한다.
        holder = subprocess.Popen([
            updater.POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
            f"$fs = [System.IO.File]::Open('{target}', 'Open', 'Read', 'None'); "
            f"Start-Sleep -Seconds {LOCK_SECONDS}; $fs.Close()"])
        time.sleep(1.5)  # 잠금이 확실히 걸린 뒤에 스왑을 시작한다.
        result = _run_swap(target, staged, result_path, state_path, len(new_bytes))
        holder.wait(timeout=60)

        if result is None:
            print("FAIL[A]: 결과 파일이 없습니다")
            return False
        print("result[A]:", json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            print(f"FAIL[A]: 잠금이 풀렸는데도 스왑이 실패했습니다 (err={result.get('error')})")
            return False
        if int(result.get("tries") or 0) < 2:
            print(f"FAIL[A]: 재시도가 일어나지 않았습니다(tries={result.get('tries')}) "
                  f"- 잠금 재현이 안 된 것이므로 이 검증은 무의미합니다")
            return False
        if open(target, "rb").read() != new_bytes:
            print("FAIL[A]: 대상 파일이 새 내용으로 바뀌지 않았습니다")
            return False
        print(f"OK[A]: 잠긴 exe 를 {result.get('tries')}번째 시도에 교체했습니다")
        return True
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_unrecoverable_failure_still_records_a_reason():
    """B) 회복 불가 실패에서도 사유가 비어 있으면 안 된다(이 사고의 핵심)."""
    root, _work, target, staged, result_path, state_path = _make_case("실패")
    try:
        old_bytes = open(target, "rb").read()
        expected = os.path.getsize(staged)
        os.unlink(staged)  # 새 파일이 사라진 상황(AV 격리와 같은 결과)
        result = _run_swap(target, staged, result_path, state_path, expected)

        if result is None:
            print("FAIL[B]: 실패했는데 결과 파일이 없습니다 - 다음 실행이 눈이 멉니다")
            return False
        print("result[B]:", json.dumps(result, ensure_ascii=False))
        ok = True
        if result.get("ok"):
            print("FAIL[B]: 실패인데 ok=true 로 보고했습니다")
            ok = False
        if not str(result.get("error") or "").strip():
            print("FAIL[B]: 사유가 비어 있습니다 - 이게 바로 '사유 미기록' 입니다")
            ok = False
        else:
            print(f"OK[B]: 사유가 기록됐습니다: {str(result.get('error'))[:160]}")
        if not os.path.exists(target):
            print("FAIL[B]: 실패하면서 고객의 exe 를 없앴습니다")
            ok = False
        elif open(target, "rb").read() != old_bytes:
            print("FAIL[B]: 실패했는데 대상 파일이 변형됐습니다")
            ok = False
        else:
            print("OK[B]: 실패해도 고객의 exe 는 그대로 살아 있습니다")
        if not os.path.exists(state_path):
            print("FAIL[B]: 실패인데 백오프 마커를 지웠습니다(성공으로 오인)")
            ok = False
        else:
            print("OK[B]: 실패 시 마커가 남아 앱이 사유를 보고할 수 있습니다")

        # 앱이 그 결과를 실제로 읽어 진단으로 올리는지까지 확인한다.
        keep_result, keep_state = updater._RESULT_PATH, updater._STATE_PATH
        updater._RESULT_PATH, updater._STATE_PATH = result_path, state_path
        sent = []
        keep_log = updater.remote_log
        updater.remote_log = lambda e, d="", **k: sent.append((e, d))
        try:
            updater._report_previous_swap()
        finally:
            updater.remote_log = keep_log
            updater._RESULT_PATH, updater._STATE_PATH = keep_result, keep_state
        if not sent or sent[0][0] != "update_swap_failed":
            print(f"FAIL[B]: 앱이 실패를 보고하지 않았습니다: {sent}")
            ok = False
        elif "사유 미기록" in sent[0][1] or not sent[0][1].strip():
            print(f"FAIL[B]: 보고 내용에 사유가 없습니다: {sent[0][1]}")
            ok = False
        else:
            print(f"OK[B]: 앱이 서버로 올릴 문구: {sent[0][1][:200]}")
        return ok
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    results = [
        case_locked_target_is_retried_until_it_wins(),
        case_unrecoverable_failure_still_records_a_reason(),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
