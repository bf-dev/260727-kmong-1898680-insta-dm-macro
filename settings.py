# -*- coding: utf-8 -*-
"""사용자 설정 저장(settings.json) - 지금은 하루 처리 상한 하나뿐.

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
