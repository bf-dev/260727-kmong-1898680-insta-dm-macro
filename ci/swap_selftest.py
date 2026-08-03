# -*- coding: utf-8 -*-
"""실제 윈도우에서 exe 스왑 스크립트를 끝까지 돌려 보는 CI 자체검증 (kmong 1898680).

유닛테스트는 '스크립트를 올바른 인코딩으로 썼는가'까지만 본다. 정작 v1.3.x 를 죽인 것은
cmd.exe 가 그 파일을 **읽는 순간** 한글 경로가 깨진 것이었으므로, 진짜 검증은 진짜 윈도우에서
스크립트를 실행해 파일이 실제로 바뀌는지 보는 것뿐이다. 그래서 CI 에서:

  1) 한글 폴더 + 한글 파일명(`인스타DM매크로.exe`)으로 '옛 exe' 를 만든다.
  2) 다른 내용의 '새 exe' 를 옆에 staged 로 놓는다.
  3) 실제 스왑 스크립트를 생성해 실행한다.
  4) 대상 파일이 새 내용으로 바뀌고 `update_result.json` 에 ok=true 가 찍히는지 확인한다.

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


def main():
    root = tempfile.mkdtemp(prefix="swaptest_")
    work = os.path.join(root, "한글 폴더 테스트")
    os.makedirs(work)
    target = os.path.join(work, "인스타DM매크로.exe")
    shutil.copy2(OLD_SRC, target)
    staged = target + ".update-9.9.9.tmp"
    shutil.copy2(NEW_SRC, staged)

    new_bytes = open(staged, "rb").read()
    old_bytes = open(target, "rb").read()
    if new_bytes == old_bytes:
        print("FAIL: 테스트용 옛/새 파일 내용이 같아 검증이 무의미합니다")
        return 1

    result_path = os.path.join(work, "update_result.json")
    state_path = os.path.join(work, "update_state.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"target": "9.9.9", "fail_count": 1}, f)

    script, mode = updater.write_swap_script(
        current_exe=target, staged=staged, latest="9.9.9", pid=0xFFFFFF,
        result_path=result_path, state_path=state_path, expected_size=len(new_bytes))
    print(f"mode={mode} script={script}")
    print(f"target={target}")

    proc = subprocess.Popen(updater.swap_command(script, mode))
    proc.wait(timeout=180)

    deadline = time.time() + 60
    while not os.path.exists(result_path) and time.time() < deadline:
        time.sleep(1)

    ok = True
    if not os.path.exists(result_path):
        print("FAIL: 스왑 스크립트가 결과 파일을 남기지 않았습니다")
        return 1
    with open(result_path, "r", encoding="utf-8-sig") as f:
        result = json.load(f)
    print("result:", json.dumps(result, ensure_ascii=False))
    if not result.get("ok"):
        print("FAIL: 스왑이 실패로 보고됐습니다")
        ok = False

    placed = open(target, "rb").read()
    if placed != new_bytes:
        print(f"FAIL: 대상 파일이 새 내용으로 바뀌지 않았습니다 "
              f"(size={len(placed)} expected={len(new_bytes)})")
        ok = False
    else:
        print(f"OK: 한글 경로의 exe 가 실제로 교체됐습니다 ({len(placed)} bytes)")

    if os.path.exists(state_path):
        print("FAIL: 성공했는데 백오프 마커가 남아 있습니다")
        ok = False
    else:
        print("OK: 성공 시 백오프 마커가 지워졌습니다")

    if os.path.exists(staged):
        print("WARN: staged 임시 파일이 남았습니다")

    shutil.rmtree(root, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
