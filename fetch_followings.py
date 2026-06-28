#!/usr/bin/env python3
import json
import time
import random
import argparse
import requests
import os
import signal
from pathlib import Path
from urllib.parse import urlparse
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
# twitter-api-safe-relay configuration
# ==========================================
RELAY_URL = os.environ.get("RELAY_URL", "http://localhost:3000")

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

def fix_pbs_url(url):
    if not url:
        return url
    return cleanup_url(url)

# ==========================================
# Thread pool for image downloads
# ==========================================
IMAGE_POOL = ThreadPoolExecutor(max_workers=20)

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
def save_versioned_image(acc, kind, pbs_url):
    filename = pbs_filename(pbs_url)
    if not filename:
        return

    ts = datetime.now(JST).strftime("%Y%m%d-%H%M%S")
    dirpath = Path(f"images/{acc}/{kind}")
    dirpath.mkdir(parents=True, exist_ok=True)

    save_path = dirpath / f"{ts}_{filename}"

    try:
        tqdm.write(Fore.BLUE + f"[IMG FETCH] {pbs_url}" + Style.RESET_ALL)
        r = requests.get(pbs_url, timeout=10)
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


def ensure_image_new(acc, kind, pbs_url, success_map):
    if not pbs_url:
        return
    old = success_map.get(acc, {}).get(f"{kind}_pic")
    if old == pbs_url:
        return
    IMAGE_POOL.submit(save_versioned_image, acc, kind, pbs_url)

# ==========================================
# Fetch from API Relay
# ==========================================
def fetch_from_relay(acc, is_screen_name=False, cache=None):
    if cache and str(acc) in cache:
        tqdm.write(Fore.GREEN + f"[CACHE] Using GraphQL profile for: {acc}" + Style.RESET_ALL)
        return cache[str(acc)]

    if is_screen_name:
        query_id = "2qvSHpkWTMS9i0zJAwDNiA" # UserByScreenName
        operation = "UserByScreenName"
        variables = {"screen_name": acc, "withSafetyModeUserFields": True}
    else:
        query_id = "DaeC_2LfMgwCujE03HSZtw" # UserByRestId
        operation = "UserByRestId"
        variables = {"userId": acc, "withSafetyModeUserFields": True}

    url = f"{RELAY_URL}/i/api/graphql/{query_id}/{operation}"
    params = {
        "variables": json.dumps(variables),
        "features": json.dumps({})
    }
    
    try:
        tqdm.write(Fore.BLUE + f"[FETCH] {url} (acc={acc})" + Style.RESET_ALL)
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        if "errors" in data:
            tqdm.write(Fore.RED + f"[ERR] API Relay returned errors: {data['errors']}" + Style.RESET_ALL)
            return None
            
        user_res = data.get("data", {}).get("user", {}).get("result", {})
        if not user_res or "legacy" not in user_res:
            tqdm.write(Fore.RED + f"[ERR] API Relay returned unexpected format or user not found" + Style.RESET_ALL)
            return None
            
        legacy = user_res.get("legacy", {})
        core = user_res.get("core", {})
        
        avatar_url = user_res.get("avatar", {}).get("image_url") or legacy.get("profile_image_url_https", "")
        if avatar_url:
            avatar_url = avatar_url.replace("_normal", "") # Original size

        banner_url = legacy.get("profile_banner_url", "")

        return {
            "account_id": user_res.get("rest_id", str(acc)),
            "screen_name": core.get("screen_name") or legacy.get("screen_name"),
            "name": core.get("name") or legacy.get("name"),
            "bio": legacy.get("description"),
            "location": legacy.get("location"),
            "joined": core.get("created_at") or legacy.get("created_at"),
            "profile_pic": avatar_url,
            "profile_banner": banner_url,
            "fetched_from": "GraphQL",
            "fetched_at": iso_now(),
            "avatar_unset": not bool(avatar_url) or "default_profile_images" in avatar_url,
            "banner_unset": not bool(banner_url),
        }
    except Exception as e:
        tqdm.write(Fore.RED + f"[ERR] Relay: {e}" + Style.RESET_ALL)
        return None

# ==========================================
# Loaders
# ==========================================
def load_followings(path):
    txt = Path(path).read_text(encoding="utf-8-sig")
    start = txt.find("[")
    return [x["following"]["accountId"] for x in json.loads(txt[start:])]

def load_names_list(path):
    return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

def load_success_map():
    p = Path("logs/success.jsonl")
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
            out[data["account_id"]] = data
            if data.get("screen_name"):
                out[data["screen_name"].lower()] = data
        except:
            pass
    return out

def fetch_my_followings_from_relay():
    ids = []
    fetched_profiles = {}
    cursor = None
    cache_path = Path("data/following_cache.json")
    
    # Check if we have a recent cache (less than 1 hour old)
    if cache_path.exists():
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
        if datetime.now() - mtime < timedelta(hours=1):
            tqdm.write(Fore.GREEN + "[API] Using local cache for following list (less than 1 hour old)." + Style.RESET_ALL)
            try:
                cached_data = json.loads(cache_path.read_text(encoding="utf-8"))
                return cached_data.get("ids", []), cached_data.get("profiles", {})
            except Exception as e:
                tqdm.write(Fore.YELLOW + f"[WARN] Failed to read cache: {e}. Fetching from API..." + Style.RESET_ALL)
    
    tqdm.write(Fore.CYAN + "[API] Authenticating to find your internal Account ID..." + Style.RESET_ALL)
    try:
        r_auth = requests.get(f"{RELAY_URL}/1.1/account/verify_credentials.json", timeout=10)
        r_auth.raise_for_status()
        me = r_auth.json()
        my_id = me.get("id_str")
        my_screen_name = me.get("screen_name")
        if not my_id:
            tqdm.write(Fore.RED + "[ERR] Could not determine your account ID from verify_credentials." + Style.RESET_ALL)
            return [], {}
        tqdm.write(Fore.GREEN + f"[API] Authenticated as @{my_screen_name} (ID: {my_id}). Fetching following list..." + Style.RESET_ALL)
    except Exception as e:
        tqdm.write(Fore.RED + f"[ERR] API Relay auth failed: {e}" + Style.RESET_ALL)
        return [], {}

    query_id = "eNoXdfXv5rU75RBzlmfuPA" # Current Following queryId
    
    while True:
        url = f"{RELAY_URL}/i/api/graphql/{query_id}/Following"
        variables = {
            "userId": my_id,
            "count": 100,
            "includePromotedContent": False
        }
        if cursor:
            variables["cursor"] = cursor
            
        params = {
            "variables": json.dumps(variables),
            "features": json.dumps({})
        }
        
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            
            if "errors" in data:
                tqdm.write(Fore.RED + f"[ERR] GraphQL returned errors: {data['errors']}" + Style.RESET_ALL)
                break
                
            instructions = data.get("data", {}).get("user", {}).get("result", {}).get("timeline", {}).get("timeline", {}).get("instructions", [])
            
            entries = []
            for inst in instructions:
                if inst.get("type") == "TimelineAddEntries":
                    entries = inst.get("entries", [])
                    break
            
            batch_ids = []
            next_cursor = None
            
            for entry in entries:
                if entry["entryId"].startswith("user-"):
                    user_res = entry.get("content", {}).get("itemContent", {}).get("user_results", {}).get("result", {})
                    if "rest_id" in user_res:
                        uid = str(user_res["rest_id"])
                        batch_ids.append(uid)
                        
                        legacy = user_res.get("legacy", {})
                        core = user_res.get("core", {})
                        
                        avatar_url = user_res.get("avatar", {}).get("image_url") or legacy.get("profile_image_url_https", "")
                        if avatar_url:
                            avatar_url = avatar_url.replace("_normal", "")
                        banner_url = legacy.get("profile_banner_url", "")
                        
                        fetched_profiles[uid] = {
                            "account_id": uid,
                            "screen_name": core.get("screen_name") or legacy.get("screen_name"),
                            "name": core.get("name") or legacy.get("name"),
                            "bio": legacy.get("description"),
                            "location": legacy.get("location"),
                            "joined": core.get("created_at") or legacy.get("created_at"),
                            "profile_pic": avatar_url,
                            "profile_banner": banner_url,
                            "fetched_from": "GraphQL Following",
                            "fetched_at": iso_now(),
                            "avatar_unset": not bool(avatar_url) or "default_profile_images" in avatar_url,
                            "banner_unset": not bool(banner_url),
                        }
                        
                elif entry["entryId"].startswith("cursor-bottom-"):
                    next_cursor = entry.get("content", {}).get("value")
                    
            ids.extend(batch_ids)
            tqdm.write(Fore.BLUE + f"[API] Fetched {len(batch_ids)} profiles (total: {len(ids)})..." + Style.RESET_ALL)
            
            if not next_cursor or next_cursor == cursor or not batch_ids:
                break
                
            cursor = next_cursor
            time.sleep(1)
            
        except Exception as e:
            tqdm.write(Fore.RED + f"[ERR] API Relay GraphQL failed: {e}" + Style.RESET_ALL)
            break
            
    # Save cache
    try:
        cache_path.parent.mkdir(exist_ok=True)
        cache_path.write_text(json.dumps({
            "ids": ids,
            "profiles": fetched_profiles
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        tqdm.write(Fore.YELLOW + f"[WARN] Failed to save following cache: {e}" + Style.RESET_ALL)
            
    return ids, fetched_profiles

# ==========================================
# Cosense export
# ==========================================
def export_cosense_single(results):
    Path("output").mkdir(exist_ok=True)
    pages = []
    
    # Avoid duplicate pages if success_map has both account_id and screen_name mapping to the same dict
    seen_ids = set()

    for key, u in results.items():
        acc_id = u.get("account_id")
        if acc_id in seen_ids:
            continue
        seen_ids.add(acc_id)

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

        pages.append({"title": f"@{sn}", "lines": lines})

    Path("output/cosense_followings.json").write_text(
        json.dumps({"pages": pages}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

# ==========================================
# 画像欠落ユーザー検出
# ==========================================
def find_accounts_needing_profile_images(success_map):
    missing = []
    seen = set()
    for key, info in success_map.items():
        acc_id = info.get("account_id")
        if acc_id in seen:
            continue
        seen.add(acc_id)

        avatar_unset = info.get("avatar_unset", False)
        if avatar_unset:
            continue

        pic = info.get("profile_pic")
        if pic is None or pic == "":
            missing.append(acc_id)
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
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--validate-images", action="store_true")
    parser.add_argument("--fetch-missing-images", action="store_true")
    parser.add_argument("--names-file", type=str, help="Path to a text file with screen names (one per line)")
    parser.add_argument("--archive", action="store_true", help="Load target IDs from data/following.js instead of API")
    args = parser.parse_args()

    is_screen_name_mode = False
    fetched_profiles_cache = {}
    
    if args.names_file:
        ids = load_names_list(args.names_file)
        is_screen_name_mode = True
    elif args.archive:
        ids = load_followings("data/following.js")
    else:
        ids, fetched_profiles_cache = fetch_my_followings_from_relay()
        if not ids:
            print("Failed to fetch followings from API or you follow 0 people. Make sure Relay is running and logged in.")
            return
        
    success_map = load_success_map()

    # EXPORT ONLY
    if args.export_only:
        tqdm.write(Fore.CYAN + "[EXPORT] Cosense output only" + Style.RESET_ALL)
        export_cosense_single(success_map)
        return

    # --validate
    if args.validate:
        print(f"[VALIDATE] input targets: {len(ids)} users")
        unique_success = len(set(u["account_id"] for u in success_map.values()))
        print(f"[VALIDATE] success.jsonl: {unique_success} unique users")
        
        # for screen name mode, we check lowercased
        if is_screen_name_mode:
            missing_ids = [i for i in ids if i.lower() not in success_map]
        else:
            missing_ids = [i for i in ids if str(i) not in success_map]
            
        print(f"[VALIDATE] Missing {len(missing_ids)} targets in success.jsonl:")
        for m in missing_ids:
            print(" ", m)
        return

    # --validate-images
    if args.validate_images:
        unique_success = len(set(u["account_id"] for u in success_map.values()))
        print(f"[VALIDATE-IMAGES] success.jsonl: {unique_success} users")
        missing = find_accounts_needing_profile_images(success_map)
        print(f"[VALIDATE-IMAGES] accounts with missing images: {len(missing)}")
        for acc in missing:
            print(" ", acc)
        return

    # --fetch-missing-images
    if args.fetch_missing_images:
        unique_success = len(set(u["account_id"] for u in success_map.values()))
        print(f"Loaded {unique_success} unique previous entries")
        missing = find_accounts_needing_profile_images(success_map)
        print(f"[VALIDATE-IMAGES] accounts with missing images: {len(missing)}")

        if not missing:
            print("[FETCH-MISSING-IMAGES] No accounts need re-fetch.")
            return

        pbar = tqdm(missing, desc="[FETCH-MISSING-IMAGES]", unit="user")
        for acc in pbar:
            if INTERRUPTED:
                tqdm.write(Fore.YELLOW + "[STOP] Interrupted safely." + Style.RESET_ALL)
                break

            info = fetch_from_relay(acc, is_screen_name=False, cache=fetched_profiles_cache)
            if info:
                ensure_image_new(acc, "profile", info["profile_pic"], success_map)
                ensure_image_new(acc, "banner", info["profile_banner"], success_map)
                success_map[acc] = info
                if info.get("screen_name"):
                    success_map[info["screen_name"].lower()] = info
                append_success(info)
                tqdm.write(Fore.GREEN + f"[FETCH-MISSING-IMAGES] ✔ {acc} @{info.get('screen_name')}" + Style.RESET_ALL)
            else:
                tqdm.write(Fore.RED + f"[FETCH-MISSING-IMAGES] ✘ {acc} (Relay failed)" + Style.RESET_ALL)
            # Only sleep if we actually made a network request for this specific user
            is_cached = fetched_profiles_cache and (str(acc) in fetched_profiles_cache)
            if not is_cached:
                time.sleep(random.uniform(0.7, 1.5))

        IMAGE_POOL.shutdown(wait=True)
        return

    unique_success = len(set(u.get("account_id") for u in success_map.values()))
    tqdm.write(f"Loaded {unique_success} unique previous entries")

    # Determine target
    if args.force:
        target_ids = ids
        initial_done = 0
        tqdm.write(Fore.CYAN + "Force mode: Fetch ALL accounts again" + Style.RESET_ALL)
    else:
        if is_screen_name_mode:
            remaining = [i for i in ids if i.lower() not in success_map]
        else:
            remaining = [i for i in ids if str(i) not in success_map]
        target_ids = remaining
        initial_done = len(ids) - len(remaining)
        if args.resume:
            tqdm.write(f"Resume: {len(remaining)} remaining")
        else:
            tqdm.write("Full mode (only new accounts)")

    if args.single:
        acc = target_ids[0] if target_ids else ids[0]
        info = fetch_from_relay(acc, is_screen_name=is_screen_name_mode, cache=fetched_profiles_cache)
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

        info = fetch_from_relay(acc, is_screen_name=is_screen_name_mode, cache=fetched_profiles_cache)

        if info:
            acc_id = info["account_id"]
            ensure_image_new(acc_id, "profile", info["profile_pic"], success_map)
            ensure_image_new(acc_id, "banner", info["profile_banner"], success_map)

            success_map[acc_id] = info
            if info.get("screen_name"):
                success_map[info["screen_name"].lower()] = info
                
            append_success(info)
            tqdm.write(Fore.GREEN + f"✔ {acc} @{info.get('screen_name')}" + Style.RESET_ALL)
        else:
            tqdm.write(Fore.RED + f"✘ {acc} (Relay failed)" + Style.RESET_ALL)

        # Only sleep if we actually made a network request for this specific user
        is_cached = fetched_profiles_cache and (str(acc) in fetched_profiles_cache)
        if not is_cached:
            time.sleep(random.uniform(0.7, 1.5))

        pbar.update(1)

    IMAGE_POOL.shutdown(wait=True)
    export_cosense_single(success_map)

    if INTERRUPTED:
        tqdm.write(Fore.YELLOW + "[INFO] You can resume safely." + Style.RESET_ALL)
    else:
        print("Done!")

if __name__ == "__main__":
    main()
