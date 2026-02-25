#!/usr/bin/env python3
"""Extract YouTube/Google cookies from any browser using yt-dlp's Python API.

Uses yt-dlp as a library to extract cookies without processing any video URL,
so it completes instantly regardless of network conditions.

Requires: pip install yt-dlp
"""

import os
import re
import sys
import tempfile

BROWSERS = {
    "1": ("Firefox", "firefox"),
    "2": ("Chrome", "chrome"),
    "3": ("Chromium", "chromium"),
    "4": ("Brave", "brave"),
    "5": ("Edge", "edge"),
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "data", "cookies.txt")
DOMAIN_PATTERN = re.compile(r"\.(youtube\.com|google\.com)\t")


def extract_cookies(browser_name):
    """Use yt-dlp Python API to extract cookies from browser."""
    import yt_dlp

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write("# Netscape HTTP Cookie File\n")

    try:
        # yt-dlp loads cookies in __init__, saves to file in __exit__
        with yt_dlp.YoutubeDL({
            "cookiesfrombrowser": (browser_name,),
            "cookiefile": tmp_path,
        }) as ydl:
            pass

        # Filter only YouTube/Google cookies
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        count = 0
        with open(tmp_path, "r") as src, open(OUTPUT_FILE, "w") as dst:
            dst.write("# Netscape HTTP Cookie File\n")
            for line in src:
                if DOMAIN_PATTERN.search(line):
                    dst.write(line)
                    count += 1

        return count
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main():
    print("PYTR Cookie Extractor")
    print("=" * 40)

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        print("yt-dlp not found. Install it: pip install yt-dlp")
        sys.exit(1)

    print("\nSelect your browser:\n")
    for key, (name, _) in BROWSERS.items():
        print(f"  {key}) {name}")
    print()

    choice = input("Browser [1-5]: ").strip()
    if choice not in BROWSERS:
        print(f"Invalid choice: {choice}")
        sys.exit(1)

    display_name, browser_name = BROWSERS[choice]
    print(f"\nExtracting {display_name} cookies...")

    try:
        count = extract_cookies(browser_name)
    except Exception as e:
        err = str(e).lower()
        print(f"\nError: {e}")
        if "secretstorage" in err:
            print("\nFor GNOME/XFCE desktops, install: pip install secretstorage")
        elif "kwallet" in err:
            print("\nFor KDE desktops, ensure kwallet is running and unlocked.")
        elif browser_name != "firefox" and ("decrypt" in err or "permission" in err or "keyring" in err):
            print("\nTip: Try Firefox instead — it doesn't encrypt cookies.")
        sys.exit(1)

    if not count:
        print("No YouTube/Google cookies found. Are you logged in to YouTube?")
        sys.exit(1)

    print(f"\nExtracted {count} cookies to {OUTPUT_FILE}")
    print("Restart the container to apply: docker compose restart")


if __name__ == "__main__":
    main()
