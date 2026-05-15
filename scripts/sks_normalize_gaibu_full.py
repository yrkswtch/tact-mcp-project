# -*- coding: utf-8 -*-
"""SKS純粋外部生(IEB040)の全件半角化。問合せ・内部生版と同パターン。

対象: sks_student_list(kubun="gaibu") が返す検索可能な外部生のみ。
ゴーストコード（内部生になった元・外部生）は IEB040 でブロックされるので対象外。

半角化対象フィールド (sks_student_list の出力キー → IEB040 フォーム名):
  生徒氏名 (seitosm), 保護者氏名 (hogosha), 氏名ﾌﾘｶﾞﾅ (seitokm),
  住所１-３ (ad1/ad2/ad3), 緊急連絡先宛 (emdest), 備考 (biko)

モード:
  dry-run  : 全角を含むレコードを検出。書き込みなし。
  apply    : 先頭 N 件 (--limit, デフォルト1) を sks_gaibusei_register update で書き戻し。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent

# .claude.json から認証情報を env にセット
_CFG_PATH = Path(os.path.expanduser("~/.claude.json"))
if _CFG_PATH.exists() and not os.environ.get("SKS_ACCOUNT"):
    _cfg = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    _env = (
        _cfg.get("projects", {})
        .get("C:/Users/{user}/Documents", {})
        .get("mcpServers", {})
        .get("sks", {})
        .get("env", {})
    )
    for k, v in _env.items():
        os.environ.setdefault(k, v)

sys.path.insert(0, str(ROOT / "servers" / "sks"))
import server  # noqa: E402

# 半角化対象フィールド: 一覧キー → 表示名
TARGETS = {
    "生徒氏名": "seitosm",
    "保護者氏名": "hogosha",
    "氏名ﾌﾘｶﾞﾅ": "seitokm",
    "住所１": "ad1",
    "住所２": "ad2",
    "住所３": "ad3",
    "緊急連絡先宛": "emdest",
    "備考": "biko",
}

# 学年表示名 → sks_gaibusei_register が受ける表示名
GRADE_FULL_MAP = {
    "小1": "小学1年", "小2": "小学2年", "小3": "小学3年", "小4": "小学4年",
    "小5": "小学5年", "小6": "小学6年",
    "中1": "中学1年", "中2": "中学2年", "中3": "中学3年",
    "高1": "高校1年", "高2": "高校2年", "高3": "高校3年",
    "成人": "成人", "他": "その他",
}


def normalize(s: str) -> str:
    if not isinstance(s, str):
        return s
    if "　" not in s and "  " not in s:
        return s
    return " ".join(s.split())


def diff_record(rec: dict) -> dict:
    """半角化で変わるフィールドだけ返す。"""
    changes = {}
    for jp_key in TARGETS:
        v = rec.get(jp_key, "")
        nv = normalize(v)
        if v != nv:
            changes[jp_key] = {"before": v, "after": nv}
    return changes


def dry_run() -> list[dict]:
    res = json.loads(server.sks_student_list(kubun="gaibu"))
    gaibu = res.get("students", [])
    print(f"[*] 純粋外部生: {len(gaibu)} 件")
    targets = []
    for s in gaibu:
        cd = s.get("生徒ｺｰﾄﾞ")
        if not cd:
            continue
        changes = diff_record(s)
        if changes:
            targets.append({
                "seitocd": cd,
                "name": s.get("生徒氏名"),
                "raw": s,
                "changes": changes,
            })
    print(f"[*] 半角化対象: {len(targets)} 件")
    return targets


def apply_changes(targets: list[dict], limit: int) -> list[dict]:
    results = []
    for i, t in enumerate(targets[:limit], 1):
        cd = t["seitocd"]
        s = t["raw"]
        print(f"\n--- [{i}/{min(limit,len(targets))}] {cd} {t['name']} ---")
        for k, ba in t["changes"].items():
            print(f"  {k}: {ba['before']!r} -> {ba['after']!r}")
        # update 引数構築（既存値を normalize したもの）
        ur = json.loads(server.sks_gaibusei_register(
            student_name=normalize(s.get("生徒氏名", "")),
            guardian_name=normalize(s.get("保護者氏名", "")),
            kana=normalize(s.get("氏名ﾌﾘｶﾞﾅ", "")),
            grade=GRADE_FULL_MAP.get(s.get("学年", ""), s.get("学年", "")),
            birth=s.get("生年月日", ""),
            sex=s.get("性別", ""),
            postal_code=s.get("郵便番号", ""),
            address_city=normalize(s.get("住所１", "")),
            address_detail=normalize(s.get("住所２", "")),
            address_building=normalize(s.get("住所３", "")),
            phone=s.get("電話番号", ""),
            emergency_phone=s.get("緊急連絡先電話番号", ""),
            emergency_dest=normalize(s.get("緊急連絡先宛", "")),
            memo=normalize(s.get("備考", "")),
            entry_year=s.get("登録年度", ""),
            kubun=s.get("外部生区分", "講習会生"),
            gaibusei_code=cd,
        ))
        ok = ur.get("result") == "OK"
        print(f"  result: {'OK' if ok else ur.get('result')} {ur.get('error') or ur.get('operation') or ''}")
        results.append({"seitocd": cd, "name": t["name"], "result": ur})
        time.sleep(1.0)
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["dry-run", "apply"])
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--targets-file")
    args = ap.parse_args()

    out_dir = ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.mode == "dry-run":
        targets = dry_run()
        # raw を除いたサマリを保存（容量削減）
        summary = [{"seitocd": t["seitocd"], "name": t["name"], "changes": t["changes"]} for t in targets]
        out_path = out_dir / f"normalize_gaibu_full_targets_{ts}.json"
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        # raw 入りも保存（apply で使う）
        raw_path = out_dir / f"normalize_gaibu_full_targets_raw_{ts}.json"
        raw_path.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[*] 結果（サマリ）: {out_path}")
        print(f"[*] raw 込み: {raw_path}")
        for t in targets[:5]:
            print(f"  {t['seitocd']} {t['name']}")
            for k, ba in t["changes"].items():
                print(f"    {k}: {ba['before']!r} -> {ba['after']!r}")
        return

    if args.targets_file:
        targets = json.loads(Path(args.targets_file).read_text(encoding="utf-8"))
        if not targets or "raw" not in targets[0]:
            print("[!] targets-file は raw 込み（normalize_gaibu_full_targets_raw_*.json）が必要")
            return
    else:
        targets = dry_run()
    if not targets:
        print("[*] 対象0件。終了")
        return
    print(f"\n[*] apply: 先頭 {min(args.limit, len(targets))} 件に書き込み")
    results = apply_changes(targets, args.limit)
    log_path = out_dir / f"normalize_gaibu_full_apply_{ts}.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[*] log: {log_path}")


if __name__ == "__main__":
    main()
