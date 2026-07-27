# -*- coding: utf-8 -*-
"""인스타 DM 매크로 - 진입점.

- --noconsole GUI exe 에서 sys.stdout 이 None 일 수 있어 방어 후 utf-8 재설정.
- 이미 실행 중이면 두 번째 인스턴스는 안내 후 종료(계정 크롬 프로필 충돌 방지).
- 예기치 못한 예외도 원격 진단(Artifacts API)으로 보내고 최대한 창은 살려 둔다.
"""

import os
import sys


def _enforce_single_instance():
    if (os.getenv("DIAG_AUTO") == "1" or os.getenv("DIAG_SCREENSHOT")
            or "--guidemo" in sys.argv):
        return
    try:
        import single_instance
        if single_instance.ensure_single_instance():
            single_instance.notify_already_running()
            try:
                import bridge
                bridge.remote_log("second_instance_blocked",
                                  "이미 실행 중이라 두 번째 인스턴스를 종료함", force=True)
            except Exception:
                pass
            sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass


def _reconfigure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main():
    _reconfigure_stdio()
    _enforce_single_instance()
    try:
        import bridge
        bridge.remote_log("process_start", "main entry", force=True)
    except Exception:
        pass
    try:
        import app
        return app.main()
    except Exception as e:
        try:
            import bridge
            bridge.upload_run(f"fatal at startup: {e}", kind="error")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    sys.exit(main())
