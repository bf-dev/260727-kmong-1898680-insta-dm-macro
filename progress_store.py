# -*- coding: utf-8 -*-
"""진행상황 저장 - 같은 엑셀 파일을 다시 열었을 때 처리한 행(번호)부터 이어서 하도록.

파일 경로+수정시각+크기를 키로 잡아, 파일이 바뀌면(고객이 새 엑셀로 교체) 별도
진행상황으로 취급한다. 각 계정 라벨별로도 분리 저장(계정 바꿔서 이어하기 대응).
"""

import hashlib
import json
import os
import time

import config


def _key_for(path, account_label):
    try:
        st = os.stat(path)
        sig = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    except Exception:
        sig = os.path.abspath(path)
    sig = f"{account_label or 'default'}|{sig}"
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()


def _path_for(key):
    os.makedirs(config.PROGRESS_DIR, exist_ok=True)
    return os.path.join(config.PROGRESS_DIR, f"{key}.json")


def load_done_rows(excel_path, account_label):
    """이미 처리 완료된 행 번호 set 반환(없으면 빈 set)."""
    key = _key_for(excel_path, account_label)
    p = _path_for(key)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(int(x) for x in data.get("done_rows", []))
    except Exception:
        return set()


def mark_done(excel_path, account_label, row_no):
    key = _key_for(excel_path, account_label)
    p = _path_for(key)
    try:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"done_rows": []}
        done = set(int(x) for x in data.get("done_rows", []))
        done.add(int(row_no))
        data["done_rows"] = sorted(done)
        data["excel_path"] = os.path.abspath(excel_path)
        data["account_label"] = account_label
        data["updated_at"] = time.time()
        os.makedirs(config.PROGRESS_DIR, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass  # 진행상황 저장 실패가 매크로 자체를 막으면 안 된다.


def reset(excel_path, account_label):
    key = _key_for(excel_path, account_label)
    p = _path_for(key)
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def migrate(excel_path, old_label, new_label):
    """v1.5.0: 진행상황 키를 '별명' -> '실제 계정 id' 로 옮길 때 쓰는 1회성 승계.

    승계를 안 하면 이미 DM 을 보낸 사람에게 **한 번 더 보내는 사고**가 난다. 대상 키에 이미
    기록이 있으면 건드리지 않고(=덮어쓰지 않고), 없을 때만 예전 별명 기록을 복사한다.
    반환: 실제로 복사했으면 True.
    """
    if not old_label or not new_label or old_label == new_label:
        return False
    src = _path_for(_key_for(excel_path, old_label))
    dst = _path_for(_key_for(excel_path, new_label))
    if os.path.exists(dst) or not os.path.exists(src):
        return False
    try:
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["account_label"] = new_label
        data["migrated_from"] = old_label
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return True
    except Exception:
        return False


# ---------------- 하루 처리량 카운터 (v1.1) ----------------
# 엑셀 파일이 아니라 '인스타 계정' 기준으로 센다. 같은 계정으로 엑셀을 바꿔 돌려도 하루
# 총량은 합산돼야 계정 보호가 된다. 날짜는 사용자 로컬 날짜(고객이 체감하는 '오늘').

_KEEP_DAYS = 14


def _daily_path(account_label):
    os.makedirs(config.PROGRESS_DIR, exist_ok=True)
    key = hashlib.sha1((account_label or "default").encode("utf-8")).hexdigest()[:16]
    return os.path.join(config.PROGRESS_DIR, f"daily_{key}.json")


def today_str():
    return time.strftime("%Y-%m-%d")


def _load_daily(account_label):
    try:
        with open(_daily_path(account_label), "r", encoding="utf-8") as f:
            data = json.load(f)
        counts = data.get("counts", {})
        return {str(k): int(v) for k, v in counts.items()}
    except Exception:
        return {}


def get_daily_count(account_label, day=None):
    """해당 계정이 그 날짜에 처리한 인원 수."""
    return _load_daily(account_label).get(day or today_str(), 0)


def bump_daily_count(account_label, n=1):
    """오늘 카운트를 n 만큼 올리고 올린 뒤 값을 반환. 저장 실패해도 앱을 막지 않는다."""
    day = today_str()
    counts = _load_daily(account_label)
    new_value = counts.get(day, 0) + n
    counts[day] = new_value
    # 오래된 날짜는 정리(파일이 무한정 커지지 않게)
    if len(counts) > _KEEP_DAYS:
        for old in sorted(counts)[:-_KEEP_DAYS]:
            counts.pop(old, None)
    try:
        with open(_daily_path(account_label), "w", encoding="utf-8") as f:
            json.dump({"account_label": account_label, "counts": counts,
                       "updated_at": time.time()}, f, ensure_ascii=False)
    except Exception:
        pass
    return new_value


def migrate_daily(old_label, new_label):
    """하루 처리량 카운터도 같은 이유로 승계한다(상한이 초기화돼 계정이 위험해지지 않게)."""
    if not old_label or not new_label or old_label == new_label:
        return False
    src, dst = _daily_path(old_label), _daily_path(new_label)
    if os.path.exists(dst) or not os.path.exists(src):
        return False
    try:
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["account_label"] = new_label
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def reset_daily_count(account_label):
    try:
        p = _daily_path(account_label)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass
