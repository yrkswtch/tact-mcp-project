"""
MCP Server for schoolie-net CMS (schoolie-net.jp)
教室HPの閲覧・部分更新をMCPツールとして提供する

【重要】ログイン試行に繰り返し失敗するとアカウントロックされる可能性。
ログイン失敗時はリトライせず即座にエラーを返すこと。
"""
import json
import os
import re
import sys
from typing import Any

import requests
from bs4 import BeautifulSoup

# --- FastMCP ---
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("mcp package not found. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("SchoolieNet")

# --- Configuration ---
BASE_URL = "https://www.schoolie-net.jp/console"
CLASSROOM_ID = "696"
USERNAME = os.environ.get("SCHOOLIE_USERNAME", "")
PASSWORD = os.environ.get("SCHOOLIE_PASSWORD", "")

# --- Session management ---
_session: requests.Session | None = None
_login_failed: bool = False


def _get_session() -> requests.Session:
    """ログイン済みセッションを返す。未ログインならログインする。"""
    global _session, _login_failed
    if _login_failed:
        raise RuntimeError("ログインに失敗済み。アカウントロック防止のためリトライしない。")
    if _session is not None:
        return _session

    if not USERNAME or not PASSWORD:
        _login_failed = True
        raise RuntimeError("SCHOOLIE_USERNAME / SCHOOLIE_PASSWORD が未設定。")

    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    # GET login page for CSRF token
    r = s.get(f"{BASE_URL}/")
    if r.status_code != 200:
        _login_failed = True
        raise RuntimeError(f"ログインページ取得失敗: {r.status_code}")

    soup = BeautifulSoup(r.text, "html.parser")
    csrf_input = soup.find("input", {"name": "_csrfToken"})
    if not csrf_input:
        _login_failed = True
        raise RuntimeError("CSRFトークンが見つからない。")
    csrf = csrf_input["value"]

    # POST login
    r = s.post(f"{BASE_URL}/", data={
        "_csrfToken": csrf,
        "username": USERNAME,
        "password": PASSWORD,
    }, allow_redirects=True)

    if "logout" not in r.text.lower() and "edition" not in r.url.lower():
        _login_failed = True
        raise RuntimeError("ログイン失敗。認証情報を確認してください。")

    _session = s
    return s


def _collect_form_data(soup: BeautifulSoup) -> dict[str, Any]:
    """フォームの全フィールドを収集する。checkbox[]は配列として扱う。"""
    form = soup.find("form")
    if not form:
        return {}
    data: dict[str, Any] = {}

    # Hidden inputs
    for inp in form.find_all("input", {"type": "hidden"}):
        name = inp.get("name", "")
        if name:
            data[name] = inp.get("value", "")

    # Text inputs
    for inp in form.find_all("input", {"type": "text"}):
        name = inp.get("name", "")
        if name:
            data[name] = inp.get("value", "")

    # Radio buttons (checked only)
    for inp in form.find_all("input", {"type": "radio"}):
        if inp.get("checked") is not None:
            name = inp.get("name", "")
            if name:
                data[name] = inp.get("value", "")

    # Textareas
    for ta in form.find_all("textarea"):
        name = ta.get("name", "")
        if name:
            data[name] = ta.get_text()

    # Checkboxes (support array names like name[])
    for inp in form.find_all("input", {"type": "checkbox"}):
        if inp.get("checked") is not None:
            name = inp.get("name", "")
            if name:
                if name.endswith("[]"):
                    if name not in data:
                        data[name] = []
                    data[name].append(inp.get("value", ""))
                else:
                    data[name] = inp.get("value", "")

    # Selects (selected option の value を拾う。display_order 等が0に落ちる事故を防ぐ)
    for sel in form.find_all("select"):
        name = sel.get("name", "")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        if opt is not None:
            data[name] = opt.get("value", "") or ""

    return data


def _get_edit_form(edition_id: str) -> tuple[BeautifulSoup, dict[str, Any]]:
    """指定版の編集フォームを取得し、soup と form_data を返す。"""
    s = _get_session()
    r = s.get(f"{BASE_URL}/editions/{CLASSROOM_ID}/edit/{edition_id}")
    if r.status_code != 200:
        raise RuntimeError(f"編集ページ取得失敗: {r.status_code}")
    soup = BeautifulSoup(r.text, "html.parser")
    form_data = _collect_form_data(soup)
    return soup, form_data


# --- MCP Tools ---

@mcp.tool()
def schoolie_get_versions() -> str:
    """版一覧を取得する。各版のID・ステータス・更新日を返す。"""
    s = _get_session()
    r = s.get(f"{BASE_URL}/editions/{CLASSROOM_ID}/view/1")
    if r.status_code != 200:
        return json.dumps({"error": f"版一覧取得失敗: {r.status_code}"}, ensure_ascii=False)

    soup = BeautifulSoup(r.text, "html.parser")
    versions = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        # Find edition ID from link
        link = tr.find("a", href=re.compile(r"/edit/\d+"))
        if not link:
            continue
        m = re.search(r"/edit/(\d+)", link.get("href", ""))
        if not m:
            continue
        eid = m.group(1)
        cells = [td.get_text(strip=True) for td in tds]
        versions.append({
            "edition_id": eid,
            "cells": cells,
        })

    return json.dumps(versions, ensure_ascii=False, indent=2)


@mcp.tool()
def schoolie_get_fields(edition_id: str, prefix: str = "") -> str:
    """指定版のフィールド値を取得する。

    Args:
        edition_id: 版ID（schoolie_get_versionsで取得）
        prefix: フィールド名のプレフィックスで絞り込み（例: "classroom_staff", "classroom_info"）。空なら全件。
    """
    _, form_data = _get_edit_form(edition_id)

    if prefix:
        filtered = {k: v for k, v in form_data.items() if k.startswith(prefix)}
    else:
        filtered = form_data

    return json.dumps(filtered, ensure_ascii=False, indent=2)


@mcp.tool()
def schoolie_update_fields(edition_id: str, updates: str, save: bool = True) -> str:
    """指定版のフィールドを部分更新する。指定したフィールドだけ上書きし、他は既存値を維持する。

    Args:
        edition_id: 版ID
        updates: 更新するフィールドのJSON文字列（例: {"classroom_staff[greetings]": "新しい挨拶文"}）
        save: Trueなら一時保存（status=1）まで実行。Falseならドライランで更新内容を返すだけ。
    """
    _, form_data = _get_edit_form(edition_id)

    try:
        update_dict = json.loads(updates)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"JSONパースエラー: {e}"}, ensure_ascii=False)

    # Apply updates
    changed = []
    for key, value in update_dict.items():
        old = form_data.get(key, "(未設定)")
        form_data[key] = value
        changed.append({"field": key, "old": str(old)[:100], "new": str(value)[:100]})

    if not save:
        return json.dumps({"dry_run": True, "changes": changed}, ensure_ascii=False, indent=2)

    # Save as draft (status=1)
    form_data["status"] = "1"

    s = _get_session()
    r = s.post(
        f"{BASE_URL}/editions/{CLASSROOM_ID}/edit/{edition_id}",
        data=form_data,
        allow_redirects=True,
    )

    success = "edit" in r.url
    return json.dumps({
        "saved": success,
        "status": "一時保存" if success else "保存失敗",
        "changes": changed,
        "url": r.url,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def schoolie_upload_staff_image(edition_id: str, staff_index: int, photo_path: str, save: bool = True) -> str:
    """スタッフ(講師)の写真をアップロードする。

    schoolie-net CMSの編集ページで「ファイルを選択」+「一時保存」をクリックする操作と等価。
    新規追加・既存写真の差し替え両方に使える(remove_image は触らない)。

    Args:
        edition_id: 版ID(コピーした新版or既存版どちらでも可)
        staff_index: スタッフスロット(0〜9)。CMSの「講師情報 N」は staff_details[N-1] に対応
        photo_path: アップロードする画像ファイルの絶対パス(jpg/png/gif/bmp、2MB以内推奨、横724px推奨)
        save: True=一時保存まで実行。False=ドライランで送信内容を返す。

    Notes:
        - `remove_image=1` を立てると「削除モード」が発動して新画像が無視される。
          よってこのツールは常に remove_image を未指定(=0)で送る。
        - 1POSTで完結(2段POST不要、新版コピー不要、ブラウザのフォーム送信と等価)。
    """
    from pathlib import Path
    p = Path(photo_path)
    if not p.exists():
        return json.dumps({"error": f"photo not found: {photo_path}"}, ensure_ascii=False)
    if not (0 <= staff_index <= 9):
        return json.dumps({"error": f"staff_index must be 0..9, got {staff_index}"}, ensure_ascii=False)

    soup, form_data = _get_edit_form(edition_id)
    field_name = f"classroom_staff[staff_details][{staff_index}][image_data]"

    if not save:
        return json.dumps({
            "dry_run": True,
            "edition_id": edition_id,
            "staff_index": staff_index,
            "photo": str(p),
            "size_bytes": p.stat().st_size,
            "field": field_name,
            "form_fields_count": len(form_data),
        }, ensure_ascii=False, indent=2)

    form_data["status"] = "1"
    s = _get_session()
    with open(p, "rb") as fp:
        files = {field_name: (p.name, fp, "image/jpeg")}
        r = s.post(
            f"{BASE_URL}/editions/{CLASSROOM_ID}/edit/{edition_id}",
            data=form_data,
            files=files,
            allow_redirects=True,
        )

    success = "edit" in r.url
    new_url = None
    if success:
        r2 = s.get(f"{BASE_URL}/editions/{CLASSROOM_ID}/edit/{edition_id}")
        soup2 = BeautifulSoup(r2.text, "html.parser")
        fi = soup2.find("input", {"id": f"staff_details{staff_index}"})
        if fi:
            parent = fi.parent
            for _ in range(6):
                if parent is None:
                    break
                im = parent.find("img") if hasattr(parent, "find") else None
                if im and "logo" not in (im.get("src") or "").lower():
                    new_url = im.get("src")
                    break
                parent = parent.parent if hasattr(parent, "parent") else None

    return json.dumps({
        "uploaded": bool(new_url),
        "edition_id": edition_id,
        "staff_index": staff_index,
        "photo": p.name,
        "size_bytes": p.stat().st_size,
        "image_url": new_url,
        "post_url": r.url,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def schoolie_remove_staff_image(edition_id: str, staff_index: int, save: bool = True) -> str:
    """スタッフ(講師)の写真を削除する(remove_image=1 を立てて保存)。

    Args:
        edition_id: 版ID
        staff_index: スタッフスロット(0〜9)
        save: True=実行。False=ドライラン。
    """
    if not (0 <= staff_index <= 9):
        return json.dumps({"error": f"staff_index must be 0..9, got {staff_index}"}, ensure_ascii=False)

    _, form_data = _get_edit_form(edition_id)
    field_name = f"classroom_staff[staff_details][{staff_index}][remove_image]"
    form_data[field_name] = "1"

    if not save:
        return json.dumps({
            "dry_run": True,
            "edition_id": edition_id,
            "staff_index": staff_index,
            "field": field_name,
        }, ensure_ascii=False, indent=2)

    form_data["status"] = "1"
    s = _get_session()
    r = s.post(
        f"{BASE_URL}/editions/{CLASSROOM_ID}/edit/{edition_id}",
        data=form_data,
        allow_redirects=True,
    )
    return json.dumps({
        "removed": "edit" in r.url,
        "edition_id": edition_id,
        "staff_index": staff_index,
        "post_url": r.url,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def schoolie_copy_version(src_edition_id: str) -> str:
    """指定版をコピーして新版を作成する。GET /copy/{版ID} → リダイレクトで新版IDを返す。

    Args:
        src_edition_id: コピー元版ID
    """
    s = _get_session()
    r = s.get(f"{BASE_URL}/editions/{CLASSROOM_ID}/copy/{src_edition_id}", allow_redirects=False)
    location = r.headers.get("Location", "")
    m = re.search(r"/edit/(\d+)", location)
    if not m:
        return json.dumps({"error": f"copy failed: status={r.status_code} location={location!r}"}, ensure_ascii=False)
    new_id = m.group(1)
    return json.dumps({
        "src_edition_id": src_edition_id,
        "new_edition_id": new_id,
        "edit_url": f"{BASE_URL}/editions/{CLASSROOM_ID}/edit/{new_id}",
        "preview_url": f"{BASE_URL}/editions/{CLASSROOM_ID}/preview/{new_id}",
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def schoolie_request_approval(edition_id: str) -> str:
    """指定版を承認依頼する（status=2）。

    Args:
        edition_id: 版ID
    """
    _, form_data = _get_edit_form(edition_id)
    form_data["status"] = "2"

    s = _get_session()
    r = s.post(
        f"{BASE_URL}/editions/{CLASSROOM_ID}/edit/{edition_id}",
        data=form_data,
        allow_redirects=True,
    )

    success = "edit" not in r.url or "view" in r.url
    return json.dumps({
        "submitted": success,
        "status": "承認依頼済み" if success else "送信失敗",
        "url": r.url,
    }, ensure_ascii=False, indent=2)


# --- Entry point ---
if __name__ == "__main__":
    mcp.run()
