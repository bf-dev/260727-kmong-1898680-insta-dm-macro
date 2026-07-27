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
