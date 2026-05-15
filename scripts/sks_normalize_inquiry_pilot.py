# -*- coding: utf-8 -*-
"""SKS問合せ管理(tryers)の半角化パイロット。

内部生・外部生の正規化と同パターンで、問合せレコードの全角スペース等を半角化する。

モード:
  dry-run  : 全件 read のみ。全角を含むレコードを検出して一覧表示。書き込みなし。
  apply    : dry-run 結果から先頭 N 件だけ書き込み（--limit, デフォルト1件）。

セッション分離:
  このスクリプトは独自の requests.Session を持ち、MCP/CDP とは別 session。
  read→update を 1 プロセス内で完結させて screen-state 汚染を避ける。
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

# .claude.json から SKS 認証情報を読み込んで env にセット（推測禁止ルール厳守）
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

# システムフィールド（normalize 対象外: cmd/code/番号系/タイムスタンプ）
SYSTEM_FIELDS = {"cmd", "code", "number", "kyoshitsucd", "csrf_token"}


def normalize(s: str) -> str:
    """全角スペース→半角、連続空白を1個に圧縮。"""
    if not isinstance(s, str):
        return s
    if "　" not in s and "  " not in s:
        return s
    return " ".join(s.split())


def diff_fields(current: dict) -> dict:
    """文字列フィールド全体を走査し、半角化で変わるフィールドだけを返す。"""
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


def list_inquiries() -> list[dict]:
    """全問合せレコードのインデックスを返す。"""
    res = json.loads(server.sks_lookup(ns="toiawase"))
    return res.get("matches", [])


def dry_run() -> list[dict]:
    """全件 read して半角化対象を検出。書き込みなし。"""
    inquiries = list_inquiries()
    print(f"[*] 問合せ全件: {len(inquiries)} 件")
    print(f"[*] 全件 read で半角化対象を検出中...")

    targets = []
    for i, inq in enumerate(inquiries, 1):
        no = inq.get("seitocd")
        if not no:
            continue
        try:
            current = server._inquiry_load_edit(no)
        except Exception as e:
            print(f"  [!] {no}: load 失敗 {e!r}")
            continue
        changes = diff_fields(current)
        if changes:
            targets.append({
                "inquiry_no": no,
                "name": inq.get("name"),
                "inquiry_date": inq.get("inquiry_date"),
                "changes": changes,
            })
            mark = "*"
        else:
            mark = "."
        if i % 20 == 0:
            print(f"  [{i}/{len(inquiries)}] hits={len(targets)}")
        sys.stdout.write(mark)
        sys.stdout.flush()
        time.sleep(0.1)  # 軽スロットリング
    print()
    print(f"[*] 半角化対象: {len(targets)} 件")
    return targets


def apply_changes(targets: list[dict], limit: int) -> list[dict]:
    """先頭 N 件に対して update を実行。"""
    results = []
    for inq in targets[:limit]:
        no = inq["inquiry_no"]
        new_fields = {k: ba["after"] for k, ba in inq["changes"].items()}
        print(f"\n--- {no} {inq['name']} ---")
        for k, ba in inq["changes"].items():
            print(f"  {k}: {ba['before']!r} -> {ba['after']!r}")
        ur = json.loads(server.sks_inquiry_update(no, new_fields))
        ok = ur.get("ok")
        print(f"  result: {'OK' if ok else 'NG'}")
        results.append({"inquiry_no": no, "result": ur})
        time.sleep(1.0)
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["dry-run", "apply"], help="dry-run: 検出のみ / apply: 書き込み")
    ap.add_argument("--limit", type=int, default=1, help="apply 時の処理件数（デフォルト1）")
    ap.add_argument("--targets-file", help="apply 時に dry-run の結果 JSON を再利用")
    args = ap.parse_args()

    out_dir = ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.mode == "dry-run":
        targets = dry_run()
        out_path = out_dir / f"normalize_inquiry_targets_{ts}.json"
        out_path.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[*] 結果: {out_path}")
        # 上位サンプル
        for t in targets[:5]:
            print(f"  {t['inquiry_no']} {t['name']} ({t['inquiry_date']})")
            for k, ba in t["changes"].items():
                print(f"    {k}: {ba['before']!r} -> {ba['after']!r}")
        return

    # apply モード
    if args.targets_file:
        targets = json.loads(Path(args.targets_file).read_text(encoding="utf-8"))
    else:
        targets = dry_run()
    if not targets:
        print("[*] 対象0件。終了")
        return
    print(f"\n[*] apply: 先頭 {min(args.limit, len(targets))} 件に書き込み")
    results = apply_changes(targets, args.limit)
    log_path = out_dir / f"normalize_inquiry_apply_{ts}.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[*] log: {log_path}")


if __name__ == "__main__":
    main()
