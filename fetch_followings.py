#!/usr/bin/env python3
import json
import time
import random
import argparse
import requests
import os
import signal
from pathlib import Path
from urllib.parse import unquote, urlparse
from bs4 import BeautifulSoup
from tqdm import tqdm
from colorama import Fore, Style
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

# ==========================================
# Global interrupt flag
# ==========================================
INTERRUPTED = False

def handle_sigint(signum, frame):
    global INTERRUPTED
    INTERRUPTED = True
    tqdm.write(Fore.RED + "\n[CTRL-C] Interrupt requested. Stopping safely..." + Style.RESET_ALL)

signal.signal(signal.SIGINT, handle_sigint)

# ==========================================
# JST timestamp
# ==========================================
JST = timezone(timedelta(hours=9))

def iso_now():
    return datetime.now(JST).isoformat(timespec="seconds")

# ==========================================
# Nitter servers
# see: https://github.com/zedeus/nitter/wiki/Instances
# ==========================================
NITTERS = [
    "https://nitter.tiekoetter.com",
    "https://xcancel.com",
    "https://lightbrd.com",
    "https://nitter.space",
    "https://nuku.trabun.org",
]

# ==========================================
# Helpers
# ==========================================
def cleanup_url(url):
    return url.split("?")[0] if url else url

def pbs_filename(pbs_url):
    if not pbs_url:
        return None
    name = os.path.basename(urlparse(pbs_url).path)
    if "." not in name:
        name += ".jpg"
    return name

def nitter_to_pbs(url):
    """
    Nitter の /pic/ URL を元の Twitter 画像 URL に戻す。
    - /pic/ の後ろが空: アバター未設定・凍結 → None
    - abs.twimg.com/... の場合: そのまま abs ドメインで返す（初期アバター対応）
    - それ以外: pbs.twimg.com/... として返す
    """
    if not url or "/pic/" not in url:
        return None

    path = url.split("/pic/", 1)[1]
    if not path:
        # "/pic/" のみ → 凍結などで画像なし
        return None

    decoded = unquote(path)
    decoded = cleanup_url(decoded)

    # すでに pbs なら追加しない
    if decoded.startswith("pbs.twimg.com/"):
        return "https://" + decoded

    # 初期アバター (abs.twimg.com) は abs のまま扱う
    if decoded.startswith("abs.twimg.com/"):
        return "https://" + decoded

    return "https://pbs.twimg.com/" + decoded

def fix_pbs_url(url):
    if not url:
        return url
    url = cleanup_url(url)

    # ダブル pbs 対策:
    #   https://pbs.twimg.com/https://pbs.twimg.com/XXXX
    if "https://pbs.twimg.com/https://pbs.twimg.com/" in url:
        url = url.replace("https://pbs.twimg.com/https://pbs.twimg.com/", "https://pbs.twimg.com/")

    # もう1段階:
    #   https://pbs.twimg.com/pbs.twimg.com/XXXX
    if "pbs.twimg.com/pbs.twimg.com/" in url:
        url = url.replace("pbs.twimg.com/pbs.twimg.com/", "pbs.twimg.com/")

    # pbs + abs 対策:
    #   https://pbs.twimg.com/abs.twimg.com/sticky/default_profile_images/...
    if "https://pbs.twimg.com/abs.twimg.com/" in url:
        url = url.replace("https://pbs.twimg.com/abs.twimg.com/", "https://abs.twimg.com/")

    # decode された結果が pbs.* で始まる時
    if url.startswith("pbs.twimg.com/"):
        return "https://" + url

    return url

# ==========================================
# Thread pool for image downloads
# ==========================================
IMAGE_POOL = ThreadPoolExecutor(max_workers=6)

# ==========================================
# Append success.jsonl safely
# ==========================================
def append_success(info):
    Path("logs").mkdir(exist_ok=True)
    with open("logs/success.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(info, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())

# ==========================================
# Actual image download
# ==========================================
def save_versioned_image(acc, kind, pbs_url, nitter_url):
    filename = pbs_filename(pbs_url)
    if not filename:
        return

    ts = datetime.now(JST).strftime("%Y%m%d-%H%M%S")
    dirpath = Path(f"images/{acc}/{kind}")
    dirpath.mkdir(parents=True, exist_ok=True)

    save_path = dirpath / f"{ts}_{filename}"

    try:
        tqdm.write(Fore.BLUE + f"[IMG FETCH] {nitter_url}" + Style.RESET_ALL)
        r = requests.get(nitter_url, timeout=10)
        if r.status_code != 200:
            tqdm.write(Fore.RED + f"[IMG FAIL] HTTP {r.status_code}" + Style.RESET_ALL)
            return
        save_path.write_bytes(r.content)
        tqdm.write(Fore.GREEN + f"[IMG OK] {save_path}" + Style.RESET_ALL)
    except Exception as e:
        tqdm.write(Fore.RED + f"[IMG ERROR] {e}" + Style.RESET_ALL)
        return

    # Update latest symlink (or copy on Windows)
    latest = Path(f"images/{acc}/{kind}.jpg")
    if latest.exists():
        latest.unlink()
    try:
        latest.symlink_to(save_path.relative_to(latest.parent))
    except Exception:
        latest.write_bytes(save_path.read_bytes())


def ensure_image_new(acc, kind, pbs_url, nitter_url, success_map):
    if not pbs_url:
        return
    old = success_map.get(acc, {}).get(f"{kind}_pic")
    if old == pbs_url:
        return
    IMAGE_POOL.submit(save_versioned_image, acc, kind, pbs_url, nitter_url)

# ==========================================
# HTML parse
# ==========================================
def parse_profile(html, base_url, acc):
    soup = BeautifulSoup(html, "html.parser")

    username = soup.select_one("a.profile-card-username") or soup.select_one("a.username")
    displayname = soup.select_one("a.profile-card-fullname")
    bio = soup.select_one("div.profile-bio")
    location = soup.select_one("div.profile-location span:last-child")
    joined = soup.select_one("div.profile-joindate")

    avatar = (
        soup.select_one("a.profile-card-avatar img")
        or soup.select_one("img.profile-avatar")
        or soup.select_one("img.avatar")
        or soup.select_one("img.rounded")
    )

    avatar_n = None
    avatar_unset = False
    if avatar and avatar.get("src"):
        src = avatar["src"]

        # /pic/ 単体 → 画像未設定（凍結など）
        if src == "/pic/" or src.endswith("/pic/"):
            avatar_unset = True

        avatar_n = src if src.startswith("http") else base_url + src

    # Banner: デフォルト色のみ (背景色 #1DA1F2) は「未設定」とみなす
    banner_unset = False
    banner_n = None

    banner_div = soup.select_one("div.profile-banner")
    banner_img = None
    if banner_div:
        banner_img = banner_div.select_one("img")
        if banner_img and banner_img.get("src"):
            src = banner_img["src"]
            banner_n = src if src.startswith("http") else base_url + src
        else:
            # <div class="profile-banner"><a style="background-color: #1DA1F2;"></a></div>
            a = banner_div.select_one("a")
            if a:
                style = a.get("style", "")
                if "#1DA1F2" in style:
                    banner_unset = True

    return {
        "account_id": acc,
        "screen_name": username.text.lstrip("@") if username else None,
        "name": displayname.text.strip() if displayname else None,
        "bio": bio.text.strip() if bio else None,
        "location": location.text.strip() if location else None,
        "joined": joined.text.strip() if joined else None,
        "profile_pic_nitter": avatar_n,
        "profile_banner_nitter": banner_n,
        "profile_pic": nitter_to_pbs(avatar_n),
        "profile_banner": nitter_to_pbs(banner_n),
        "fetched_from": base_url,
        "fetched_at": iso_now(),
        # 未設定判定フラグ
        "avatar_unset": avatar_unset,
        "banner_unset": banner_unset,
    }

# ==========================================
# Fetch from Nitter
# ==========================================
def fetch_from_nitter(acc):
    for base in NITTERS:
        url = f"{base}/i/user/{acc}"
        try:
            tqdm.write(Fore.BLUE + f"[FETCH] {url}" + Style.RESET_ALL)
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            info = parse_profile(r.text, base, acc)
            return info
        except Exception as e:
            tqdm.write(Fore.RED + f"[ERR] {base}: {e}" + Style.RESET_ALL)
    return None

# ==========================================
# Load following.js
# ==========================================
def load_followings(path):
    txt = Path(path).read_text(encoding="utf-8-sig")
    start = txt.find("[")
    return [x["following"]["accountId"] for x in json.loads(txt[start:])]

# ==========================================
# success.jsonl loader
# ==========================================
def load_success_map():
    p = Path("logs/success.jsonl")
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
            out[data["account_id"]] = data
        except:
            pass
    return out

# ==========================================
# Cosense export
# ==========================================
def export_cosense_single(results):
    Path("output").mkdir(exist_ok=True)
    pages = []

    for acc, u in results.items():
        sn = u.get("screen_name")
        if not sn:
            continue
        lines = [
            f"@{sn}",
            "",
            f"[/icons/x.icon] [@{sn} https://x.com/{sn}]",
            "",
            f"Name: {u.get('name')}",
            f"Bio: {u.get('bio')}",
            f"Location: {u.get('location')}",
            f"Joined: {u.get('joined')}",
            "",
        ]
        pic = fix_pbs_url(u.get("profile_pic")) or ""
        banner = fix_pbs_url(u.get("profile_banner")) or ""

        lines.append(f"Profile Image: [{pic}]")
        lines.append(f"Profile Banner: [{banner}]")
        lines.append("")
        lines.append(f"Last Updated: {u.get('fetched_at', '<unknown>')}")
        lines.append(f"Fetched From: {u.get('fetched_from', '<unknown>')}")
        lines.append("")
        # lines.append("#twitter #followings")

        pages.append({"title": f"@{sn}", "lines": lines})

    Path("output/cosense_followings.json").write_text(
        json.dumps({"pages": pages}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

# ==========================================
# 画像欠落ユーザー検出（プロフィール画像のみ）
# ==========================================
def find_accounts_needing_profile_images(success_map):
    """
    success_map から、プロフィール画像の再取得が必要なアカウント ID のリストを返す。
    - profile_pic が空 / None
    - URL が明らかにおかしい (pbs + https/pbs + abs など)
    - avatar_unset=True のものは「未設定」としてスキップ
    """
    missing = []
    for acc, info in success_map.items():
        avatar_unset = info.get("avatar_unset", False)
        if avatar_unset:
            # 凍結などでアバター未設定 → ここでは OK 扱い
            continue

        pic = info.get("profile_pic")

        # 完全に無い
        if pic is None or pic == "":
            missing.append(acc)
            continue

        # おかしな URL パターン
        if (
            "pbs.twimg.com/https://pbs.twimg.com/" in pic
            or "pbs.twimg.com/pbs.twimg.com/" in pic
            or "pbs.twimg.com/abs.twimg.com/" in pic
        ):
            missing.append(acc)
            continue

    return missing

# ==========================================
# Main
# ==========================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--single", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--validate", action="store_true")           # followings.js vs success.jsonl
    parser.add_argument("--validate-images", action="store_true")    # 画像欠落チェック（読み取り専用）
    parser.add_argument("--fetch-missing-images", action="store_true")  # 画像欠落ユーザーのみ再取得
    args = parser.parse_args()

    ids = load_followings("data/following.js")
    success_map = load_success_map()

    # EXPORT ONLY
    if args.export_only:
        tqdm.write(Fore.CYAN + "[EXPORT] Cosense output only" + Style.RESET_ALL)
        export_cosense_single(success_map)
        return

    # ==========================================
    # --validate : followings.js と success.jsonl の差分
    # ==========================================
    if args.validate:
        print("[VALIDATE] followings.js:", len(ids), "users")
        print("[VALIDATE] success.jsonl:", len(success_map), "users")

        missing_ids = [i for i in ids if i not in success_map]
        print("[VALIDATE] Missing", len(missing_ids), "users in success.jsonl:")

        for m in missing_ids:
            print(" ", m)

        return

    # ==========================================
    # --validate-images : 画像欠落ユーザーの一覧を表示（success.jsonl は変更しない）
    # ==========================================
    if args.validate_images:
        print(f"[VALIDATE-IMAGES] success.jsonl: {len(success_map)} users")
        missing = find_accounts_needing_profile_images(success_map)
        print(f"[VALIDATE-IMAGES] accounts with missing images: {len(missing)}")
        for acc in missing:
            print(" ", acc)
        return

    # ==========================================
    # --fetch-missing-images : 画像欠落ユーザーのみを再取得して success.jsonl を更新
    # ==========================================
    if args.fetch_missing_images:
        print(f"Loaded {len(success_map)} previous entries")
        missing = find_accounts_needing_profile_images(success_map)
        print(f"[VALIDATE-IMAGES] success.jsonl: {len(success_map)} users")
        print(f"[VALIDATE-IMAGES] accounts with missing images: {len(missing)}")

        if not missing:
            print("[FETCH-MISSING-IMAGES] No accounts need re-fetch.")
            return

        pbar = tqdm(missing, desc="[FETCH-MISSING-IMAGES]", unit="user")
        for acc in pbar:
            if INTERRUPTED:
                tqdm.write(Fore.YELLOW + "[STOP] Interrupted safely." + Style.RESET_ALL)
                break

            info = fetch_from_nitter(acc)
            if info:
                # 新しい情報で success_map を更新
                ensure_image_new(acc, "profile", info["profile_pic"], info["profile_pic_nitter"], success_map)
                ensure_image_new(acc, "banner", info["profile_banner"], info["profile_banner_nitter"], success_map)

                success_map[acc] = info
                append_success(info)

                tqdm.write(
                    Fore.GREEN +
                    f"[FETCH-MISSING-IMAGES] ✔ {acc} @{info.get('screen_name')} (from {info['fetched_from']})" +
                    Style.RESET_ALL
                )
            else:
                tqdm.write(
                    Fore.RED +
                    f"[FETCH-MISSING-IMAGES] ✘ {acc} (all Nitter failed)" +
                    Style.RESET_ALL
                )

            # 少しだけスリープ（Nitter への負荷を抑える）
            time.sleep(random.uniform(0.7, 1.5))

        IMAGE_POOL.shutdown(wait=True)
        return

    tqdm.write(f"Loaded {len(success_map)} previous entries")

    # Determine target
    if args.force:
        target_ids = ids
        initial_done = 0
        tqdm.write(Fore.CYAN + "Force mode: Fetch ALL accounts again" + Style.RESET_ALL)
    else:
        remaining = [i for i in ids if i not in success_map]
        target_ids = remaining
        initial_done = len(success_map)
        if args.resume:
            tqdm.write(f"Resume: {len(remaining)} remaining")
        else:
            tqdm.write("Full mode (only new accounts)")

    # Single mode
    if args.single:
        acc = target_ids[0] if target_ids else ids[0]
        info = fetch_from_nitter(acc)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    desc = "Fetch"
    if args.force:
        desc = Fore.CYAN + "Fetch (FORCE)" + Style.RESET_ALL
    elif args.resume:
        desc = Fore.CYAN + "Fetch (RESUME)" + Style.RESET_ALL

    pbar = tqdm(
        total=len(ids),
        initial=initial_done,
        desc=desc,
        unit="user",
        leave=True,
    )

    for acc in target_ids:
        if INTERRUPTED:
            tqdm.write(Fore.YELLOW + "[STOP] Interrupted safely." + Style.RESET_ALL)
            break

        info = fetch_from_nitter(acc)

        if info:
            ensure_image_new(acc, "profile", info["profile_pic"], info["profile_pic_nitter"], success_map)
            ensure_image_new(acc, "banner", info["profile_banner"], info["profile_banner_nitter"], success_map)

            success_map[acc] = info
            append_success(info)

            tqdm.write(
                Fore.GREEN +
                f"✔ {acc} @{info.get('screen_name')} (from {info['fetched_from']})" +
                Style.RESET_ALL
            )
        else:
            tqdm.write(Fore.RED + f"✘ {acc} (all Nitter failed)" + Style.RESET_ALL)

        pbar.update(1)
        time.sleep(random.uniform(0.7, 1.5))

    IMAGE_POOL.shutdown(wait=True)
    export_cosense_single(success_map)

    if INTERRUPTED:
        tqdm.write(Fore.YELLOW + "[INFO] You can resume safely." + Style.RESET_ALL)
    else:
        print("Done!")

if __name__ == "__main__":
    main()
