#!/usr/bin/env python3
import os
from pathlib import Path

BASE = Path("images")

def fix_banner_extensions():
    if not BASE.exists():
        print("images/ が存在しません")
        return

    count = 0

    for user_dir in BASE.iterdir():
        banner_dir = user_dir / "banner"
        if not banner_dir.exists():
            continue

        for file in banner_dir.iterdir():
            if not file.is_file():
                continue

            name = file.name
            # timestamp_filename: 20250101-120000_1500x500
            # 後半のファイル名部分を分離
            try:
                ts, fname = name.split("_", 1)
            except ValueError:
                continue

            # 拡張子がない → .jpg を付与
            if "." not in fname:
                new_name = f"{ts}_{fname}.jpg"
                new_path = file.with_name(new_name)
                print(f"[RENAME] {file} → {new_path}")
                file.rename(new_path)
                count += 1

    print(f"Done. Fixed {count} files.")

if __name__ == "__main__":
    fix_banner_extensions()
