# -*- coding: utf-8 -*-
"""단일 인스턴스 보장.

프로그램을 두 개 켜면 같은 크롬 프로필(user-data-dir)을 동시에 잡아 한쪽 크롬이
죽고 세션이 꼬일 수 있다(naver-comment-macro 에서 실측된 문제와 동일 패턴).
두 번째 인스턴스는 켜지자마자 스스로 종료시킨다. 윈도우에서는 네임드 뮤텍스로
(가장 확실), 그 외/실패 시 파일 락으로 판정한다.
"""

import os
import sys

import config

_MUTEX_NAME = "Global\\InstaDmMacro_SingleInstance_1898680"
_mutex_handle = None
_lock_fh = None


def _acquire_windows_mutex():
    """윈도우 네임드 뮤텍스. 이미 존재하면 두 번째 인스턴스로 판정."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        last_error = kernel32.GetLastError()
        ERROR_ALREADY_EXISTS = 183
        if not handle:
            return None, False
        if last_error == ERROR_ALREADY_EXISTS:
            # 뮤텍스는 이미 다른 프로세스가 쥐고 있다 -> 우리는 두 번째.
            return handle, False
        return handle, True
    except Exception:
        return None, None  # 판정 불가(윈도우 아님/실패) -> 파일 락으로 폴백.


def _acquire_file_lock():
    """비윈도우/폴백 파일 락.

    반환:
      (fh, True)  우리가 락을 잡음 = 첫 인스턴스.
      (None, False) 이미 다른 프로세스가 락을 쥠 = 두 번째 인스턴스(막아야 함).
      (None, None)  판정 자체 실패 = 막지 않음.
    """
    try:
        os.makedirs(config.APP_DIR, exist_ok=True)
        path = os.path.join(config.APP_DIR, "instance.lock")
    except Exception:
        return None, None

    try:
        fh = open(path, "a+")
    except Exception:
        return None, None

    # 윈도우면 msvcrt, 아니면 fcntl 로 비차단 배타 락을 시도한다.
    try:
        import msvcrt
        _locker = lambda: msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        import fcntl
        _locker = lambda: fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        fh.close()
        return None, None

    try:
        _locker()
        return fh, True           # 락 획득 = 첫 인스턴스
    except (OSError, IOError, BlockingIOError):
        # 이미 누군가 락을 쥐고 있다 = 우리는 두 번째.
        fh.close()
        return None, False
    except Exception:
        fh.close()
        return None, None


def ensure_single_instance():
    """이미 실행 중이면 True(=우리가 두 번째, 종료해야 함)를 돌려준다.
    우리가 유일/첫 인스턴스면 False."""
    global _mutex_handle, _lock_fh

    handle, is_first = _acquire_windows_mutex()
    if is_first is not None:
        _mutex_handle = handle
        return not is_first  # is_first=False -> 두 번째 -> True

    # 윈도우 뮤텍스로 판정 못하면 파일 락.
    fh, ok = _acquire_file_lock()
    if ok:
        _lock_fh = fh
        return False
    if ok is False:
        return True  # 락 획득 실패 = 이미 누가 쥠 = 두 번째.
    return False      # 판정 자체 실패 -> 막지 않는다(앱을 못 켜게 하는 것보다 낫다).


def notify_already_running():
    """두 번째 인스턴스에 안내 메시지(가능하면 메시지박스). 조용히 실패해도 됨."""
    msg = ("인스타 DM 매크로가 이미 실행 중입니다.\n\n"
           "프로그램을 두 개 켜면 계정 브라우저가 서로 충돌해 오류가 반복됩니다.\n"
           "기존 창을 사용하세요. (이 창은 자동으로 닫힙니다.)")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "이미 실행 중", 0x40)  # MB_ICONINFORMATION
        return
    except Exception:
        pass
    try:
        if sys.stdout is not None:
            print(msg)
    except Exception:
        pass
