# -*- coding: utf-8 -*-
"""사용자 설정 저장(settings.json) - 하루 처리 상한 + 크롬 자동 계정 전환 스위치.

읽기/쓰기 어느 쪽이 실패해도 앱은 그대로 돌아가야 한다(기본값으로 폴백).
"""

import json
import os

import config


def load():
    try:
        with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save(data):
    try:
        os.makedirs(os.path.dirname(config.SETTINGS_FILE), exist_ok=True)
        with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def get_daily_cap():
    value = load().get("daily_cap", config.DEFAULT_DAILY_CAP)
    try:
        value = int(value)
    except Exception:
        return config.DEFAULT_DAILY_CAP
    return max(config.DAILY_CAP_MIN, min(config.DAILY_CAP_MAX, value))


def set_daily_cap(value):
    try:
        value = int(value)
    except Exception:
        return config.DEFAULT_DAILY_CAP
    value = max(config.DAILY_CAP_MIN, min(config.DAILY_CAP_MAX, value))
    data = load()
    data["daily_cap"] = value
    save(data)
    return value


def get_auto_switch():
    """프로그램이 인스타 자체 '계정 전환'을 대신 눌러도 되는가. **기본값은 끔.**

    v1.5.0 은 이걸 항상 켠 것과 같았고, 그 결과 고객이 직접 로그인해 둔 서브계정에서 부모
    계정으로 브라우저가 끌려 나갔다(2026-08-04 실측 `login_switch_attempt want=mightysun_09`).
    지금까지 이 기능이 증명한 효과는 '고객이 원하는 계정에서 벗어나게 만든 것' 하나뿐이므로
    기본은 끄고, 원하는 사람만 화면에서 켠다.
    """
    return bool(load().get("auto_switch", False))


def set_auto_switch(value):
    data = load()
    data["auto_switch"] = bool(value)
    save(data)
    return bool(value)
