# -*- coding: utf-8 -*-
"""SKS内部生(IEB010)の全件半角化。問合せ版と同パターン。

対象: 全文字列フィールド（cmd/code/番号系/タイムスタンプ等のシステムフィールドは除外）。
家族氏名 (r1name〜r4name) や住所、備考、メモ等の全角スペースを半角化。

モード:
  dry-run  : 全件 read のみ。半角化対象を一覧表示。
  apply    : dry-run 結果から先頭 N 件 (--limit, デフォルト1) を update。

セッション分離: 独自 requests.Session で 1 プロセス完結。MCP/CDP とは混ぜない。
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

# .claude.json から SKS 認証情報を読み込んで env にセット
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

# 半角化対象外のシステムフィールド
SYSTEM_FIELDS = {
    "cmd", "code", "TORIKOMIFLG",
    "kyoshitsucd", "seitocd", "gaibuseicd",
    # 日付・郵便番号は normalize 対象外（フォーマット決まり、空白は元から含まない）
    # ただし im* は人間入力フィールドなのでそのまま走査対象に残す
    "datebirth", "nyujukudt", "postalcd",
    # システム返しのボタンキャプション（全件 "　授業登録　"）
    "bnjugyot",
}


def normalize(s: str) -> str:
    """全角スペース→半角、連続空白を1個に圧縮。"""
    if not isinstance(s, str):
        return s
    if "　" not in s and "  " not in s:
        return s
    return " ".join(s.split())


def diff_fields(current: dict) -> dict:
    """全文字列フィールドを走査して半角化で変わるものだけ返す。"""
    changes = {}
    for k, v in current.items():
        if k in SYSTEM_FIELDS:
            continue
        if not isinstance(v, str):
            continue
        nv = normalize(v)
        if v != nv:
            changes[k] = {"before": v, "after": nv}
    return changes


def list_students() -> list[dict]:
    """内部生(在籍+退塾済み含む)の一覧を返す。"""
    res = json.loads(server.sks_student_list(include_taijuku=True))
    return res.get("students", [])


def dry_run() -> list[dict]:
    """全件 read で半角化対象を検出。書き込みなし。"""
    students = list_students()
    print(f"[*] 内部生全件: {len(students)} 件")
    print(f"[*] 全件 read で半角化対象を検出中...")

    targets = []
    for i, s in enumerate(students, 1):
        cd = s.get("生徒ｺｰﾄﾞ") or s.get("seitocd")
        if not cd:
            continue
        try:
            fr = json.loads(server.sks_internal_get_fields(cd))
        except Exception as e:
            print(f"\n  [!] {cd}: load 失敗 {e!r}")
            continue
        if fr.get("result") != "OK":
            print(f"\n  [!] {cd}: {fr.get('error')}")
            continue
        current = fr.get("fields", {})
        changes = diff_fields(current)
        if changes:
            targets.append({
                "seitocd": cd,
                "name": s.get("生徒氏名"),
                "changes": changes,
            })
            mark = "*"
        else:
            mark = "."
        if i % 20 == 0:
            print(f" [{i}/{len(students)}] hits={len(targets)}")
        else:
            sys.stdout.write(mark)
            sys.stdout.flush()
        time.sleep(0.1)
    print()
    print(f"[*] 半角化対象: {len(targets)} 件")
    return targets


def apply_changes(targets: list[dict], limit: int) -> list[dict]:
    results = []
    for i, t in enumerate(targets[:limit], 1):
        cd = t["seitocd"]
        new_fields = {k: ba["after"] for k, ba in t["changes"].items()}
        print(f"\n--- [{i}/{min(limit,len(targets))}] {cd} {t['name']} ---")
        for k, ba in t["changes"].items():
            print(f"  {k}: {ba['before']!r} -> {ba['after']!r}")
        ur = json.loads(server.sks_internal_update_fields(cd, new_fields))
        ok = ur.get("result") == "OK"
        print(f"  result: {'OK' if ok else 'NG'} {ur.get('error') or ''}")
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
        out_path = out_dir / f"normalize_naibu_full_targets_{ts}.json"
        out_path.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[*] 結果: {out_path}")
        for t in targets[:5]:
            print(f"  {t['seitocd']} {t['name']}")
            for k, ba in t["changes"].items():
                print(f"    {k}: {ba['before']!r} -> {ba['after']!r}")
        return

    if args.targets_file:
        targets = json.loads(Path(args.targets_file).read_text(encoding="utf-8"))
    else:
        targets = dry_run()
    if not targets:
        print("[*] 対象0件。終了")
        return
    print(f"\n[*] apply: 先頭 {min(args.limit, len(targets))} 件に書き込み")
    results = apply_changes(targets, args.limit)
    log_path = out_dir / f"normalize_naibu_full_apply_{ts}.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[*] log: {log_path}")


if __name__ == "__main__":
    main()
