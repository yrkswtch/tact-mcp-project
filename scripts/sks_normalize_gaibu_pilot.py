# -*- coding: utf-8 -*-
"""外部生1件のパイロット書き戻し: 氏名・保護者氏名・住所の全角→半角統一。
sks_gaibusei_register に gaibusei_code 指定で update モード呼び出し。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "servers" / "sks"))
import server  # noqa: E402


def normalize(s: str) -> str:
    return " ".join(s.split()) if isinstance(s, str) else s


def main() -> None:
    # 外部生から全角を含むものを5件
    res = json.loads(server.sks_student_list(kubun="gaibu"))
    gaibu = res.get("students", [])
    targets = [s for s in gaibu if "　" in s.get("生徒氏名", "")][:5]
    print(f"[*] パイロット対象 5件 (外部生):")
    for s in targets:
        print(f"  {s['生徒ｺｰﾄﾞ']} | {s['生徒氏名']:18} | 学年:{s['学年']} | 生年月日:{s['生年月日']}")

    out_dir = ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    log_path = out_dir / f"normalize_gaibu_pilot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    log = {"started_at": datetime.now().isoformat(), "items": []}

    # 学年名 → 内部 key 変換
    grade_full_map = {
        "小1": "小学1年", "小2": "小学2年", "小3": "小学3年", "小4": "小学4年",
        "小5": "小学5年", "小6": "小学6年",
        "中1": "中学1年", "中2": "中学2年", "中3": "中学3年",
        "高1": "高校1年", "高2": "高校2年", "高3": "高校3年",
        "成人": "成人", "他": "その他",
    }

    for s in targets[:1]:  # まず1件だけ実機テスト
        cd = s["生徒ｺｰﾄﾞ"]
        original = {
            "student_name": s.get("生徒氏名", ""),
            "guardian_name": s.get("保護者氏名", ""),
            "ad1": s.get("住所１", ""),
            "ad2": s.get("住所２", ""),
            "ad3": s.get("住所３", ""),
        }
        new_payload = {
            "student_name": normalize(original["student_name"]),
            "guardian_name": normalize(original["guardian_name"]),
            "address_city": normalize(original["ad1"]),
            "address_detail": normalize(original["ad2"]),
            "address_building": normalize(original["ad3"]),
        }
        # 必須引数
        kana = s.get("氏名ﾌﾘｶﾞﾅ", "")
        grade = grade_full_map.get(s.get("学年", ""), s.get("学年", ""))
        birth = s.get("生年月日", "")
        sex = s.get("性別", "")
        phone = s.get("電話番号", "")
        emergency_phone = s.get("緊急連絡先電話番号", "")
        emergency_dest = s.get("緊急連絡先宛", "")
        memo = s.get("備考", "")
        postal = s.get("郵便番号", "")
        kubun = s.get("外部生区分", "講習会生")
        entry_year = s.get("登録年度", "")

        print(f"\n--- {cd} {original['student_name']} ---")
        print(f"  diff:")
        for k in original:
            v_old = original[k]
            v_new = new_payload.get(k.replace("ad", "address_").replace("address_1", "address_city").replace("address_2", "address_detail").replace("address_3", "address_building"), "")
            # 簡易表示
            if v_old != normalize(v_old):
                print(f"    {k}: {v_old!r} → {normalize(v_old)!r}")

        # 更新呼び出し
        ur = json.loads(server.sks_gaibusei_register(
            student_name=new_payload["student_name"],
            guardian_name=new_payload["guardian_name"],
            kana=kana,
            grade=grade,
            birth=birth,
            sex=sex,
            postal_code=postal,
            address_city=new_payload["address_city"],
            address_detail=new_payload["address_detail"],
            address_building=new_payload["address_building"],
            phone=phone,
            emergency_phone=emergency_phone,
            emergency_dest=emergency_dest,
            memo=memo,
            entry_year=entry_year,
            kubun=kubun,
            gaibusei_code=cd,  # ← update モード
        ))
        print(f"  result: {ur.get('result')} {ur.get('operation', '')}")
        log["items"].append({"seitocd": cd, "before": original, "request": new_payload, "result": ur})

    log["finished_at"] = datetime.now().isoformat()
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[*] log: {log_path}")


if __name__ == "__main__":
    main()
