#!/usr/bin/env python3
"""Extract YouTube cookies from a browser.

Two modes:
  - New profile (default): opens a temporary browser profile, you log in,
    close the browser, cookies are extracted and the profile is deleted.
  - Existing profile: extracts cookies from a browser you're already
    logged into (no browser window opened).

Requires: pip install "yt-dlp[default]"
"""

import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "data", "cookies.txt")
DOMAIN_PATTERN = re.compile(r"\.(youtube\.com|google\.com)\t")

# (display_name, yt-dlp name, executable candidates, launch args builder)
BROWSERS = [
    ("Firefox", "firefox", ["firefox"],
     lambda p: ["--new-instance", "--profile", p]),
    ("Chrome", "chrome", ["google-chrome", "google-chrome-stable"],
     lambda p: [f"--user-data-dir={p}", "--no-first-run"]),
    ("Chromium", "chromium", ["chromium", "chromium-browser"],
     lambda p: [f"--user-data-dir={p}", "--no-first-run"]),
    ("Brave", "brave", ["brave", "brave-browser"],
     lambda p: [f"--user-data-dir={p}", "--no-first-run"]),
    ("Edge", "edge", ["microsoft-edge"],
     lambda p: [f"--user-data-dir={p}", "--no-first-run"]),
]

# Where each browser stores profiles
_PROFILE_DIRS = {
    "firefox": os.path.expanduser("~/.mozilla/firefox"),
    "chrome": os.path.expanduser("~/.config/google-chrome"),
    "chromium": os.path.expanduser("~/.config/chromium"),
    "brave": os.path.expanduser("~/.config/BraveSoftware/Brave-Browser"),
    "edge": os.path.expanduser("~/.config/microsoft-edge"),
}


def find_installed_browsers():
    """Detect which browsers are installed."""
    found = []
    for display, yt_name, executables, args_fn in BROWSERS:
        for exe in executables:
            path = shutil.which(exe)
            if path:
                found.append((display, yt_name, path, args_fn))
                break
    return found


# ── Profile listing (for existing-profile mode) ─────────────────────────


def _list_firefox_profiles(base_dir):
    """List Firefox profiles from profiles.ini."""
    profiles = []
    ini_path = os.path.join(base_dir, "profiles.ini")
    if not os.path.isfile(ini_path):
        return profiles
    cfg = configparser.ConfigParser()
    cfg.read(ini_path)
    for section in cfg.sections():
        if not section.startswith("Profile"):
            continue
        name = cfg.get(section, "Name", fallback=None)
        path = cfg.get(section, "Path", fallback=None)
        is_relative = cfg.get(section, "IsRelative", fallback="1")
        if name and path:
            full = os.path.join(base_dir, path) if is_relative == "1" else path
            if os.path.isdir(full):
                profiles.append((name, path))
    return profiles


def _list_chromium_profiles(base_dir):
    """List Chromium-based profiles (Default, Profile 1, etc.)."""
    profiles = []
    if not os.path.isdir(base_dir):
        return profiles
    for entry in sorted(os.listdir(base_dir)):
        prefs = os.path.join(base_dir, entry, "Preferences")
        if not os.path.isfile(prefs):
            continue
        display = entry
        try:
            with open(prefs) as f:
                data = json.load(f)
            name = data.get("profile", {}).get("name")
            if name:
                display = f"{entry} ({name})"
        except Exception:
            pass
        profiles.append((display, entry))
    return profiles


def _pick_profile(yt_name):
    """Show available profiles and let user pick one. Returns profile string or None."""
    base_dir = _PROFILE_DIRS.get(yt_name)
    if not base_dir or not os.path.isdir(base_dir):
        return None

    if yt_name == "firefox":
        all_profiles = _list_firefox_profiles(base_dir)
    else:
        all_profiles = _list_chromium_profiles(base_dir)

    if len(all_profiles) <= 1:
        return None  # 0 or 1 profile — let yt-dlp auto-detect

    print("\nMultiple profiles found:\n")
    for i, (display, _) in enumerate(all_profiles, 1):
        print(f"  {i}) {display}")
    print("  0) Auto (most recent)")
    print()

    choice = input(f"Profile [0-{len(all_profiles)}]: ").strip()
    if choice == "0" or not choice:
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(all_profiles):
            return all_profiles[idx][1]
    except ValueError:
        pass
    print("Invalid choice, using auto-detect.")
    return None


# ── Cookie extraction ────────────────────────────────────────────────────


def _save_cookies(tmp_path):
    """Filter YouTube/Google cookies from tmp file and save to OUTPUT_FILE."""
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    count = 0
    with open(tmp_path) as src, open(OUTPUT_FILE, "w") as dst:
        dst.write("# Netscape HTTP Cookie File\n")
        for line in src:
            if DOMAIN_PATTERN.search(line):
                dst.write(line)
                count += 1
    return count


def extract_from_temp_profile(browser_name, profile_path):
    """Extract cookies from a temporary browser profile directory."""
    import yt_dlp

    yt_dlp_profile = (os.path.join(profile_path, "Default")
                      if browser_name != "firefox" else profile_path)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write("# Netscape HTTP Cookie File\n")
    try:
        with yt_dlp.YoutubeDL({
            "cookiesfrombrowser": (browser_name, yt_dlp_profile),
            "cookiefile": tmp_path,
        }) as ydl:
            pass
        return _save_cookies(tmp_path)
    finally:
        os.unlink(tmp_path)


def extract_from_existing(browser_name, profile=None):
    """Extract cookies from an existing browser profile."""
    import yt_dlp

    browser_tuple = (browser_name,) if profile is None else (browser_name, profile)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write("# Netscape HTTP Cookie File\n")
    try:
        with yt_dlp.YoutubeDL({
            "cookiesfrombrowser": browser_tuple,
            "cookiefile": tmp_path,
        }) as ydl:
            pass
        return _save_cookies(tmp_path)
    finally:
        os.unlink(tmp_path)


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    print("PYTR Cookie Extractor")
    print("=" * 40)

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        print('yt-dlp not found. Install it: pip install "yt-dlp[default]"')
        sys.exit(1)

    browsers = find_installed_browsers()
    if not browsers:
        print("No supported browser found.")
        sys.exit(1)

    print("\nInstalled browsers:\n")
    for i, (display, _, _, _) in enumerate(browsers, 1):
        print(f"  {i}) {display}")
    print()

    choice = input(f"Browser [1-{len(browsers)}]: ").strip()
    try:
        idx = int(choice) - 1
        if not (0 <= idx < len(browsers)):
            raise ValueError
    except ValueError:
        print(f"Invalid choice: {choice}")
        sys.exit(1)

    display, yt_name, exe_path, args_fn = browsers[idx]

    print(f"\n  1) New profile — opens {display}, you log in, close it, done")
    print(f"  2) Existing profile — extract from {display} as-is (must be logged in)")
    print()
    mode = input("Method [1]: ").strip() or "1"

    try:
        if mode == "2":
            profile = _pick_profile(yt_name)
            print(f"\nExtracting cookies from {display}"
                  f"{f' (profile: {profile})' if profile else ''}...")
            count = extract_from_existing(yt_name, profile)
        else:
            tmp_dir = tempfile.mkdtemp(prefix="pytr-cookies-")
            try:
                print(f"\nOpening {display} with a temporary profile...")
                print("  1. Log in to YouTube")
                print("  2. Watch a few videos (so all cookies are set)")
                print("  3. Close the browser when done (do not log out)\n")

                cmd = [exe_path] + args_fn(tmp_dir) + ["https://www.youtube.com/"]
                subprocess.run(cmd)

                print("Extracting cookies...")
                count = extract_from_temp_profile(yt_name, tmp_dir)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as e:
        err = str(e).lower()
        print(f"\nError: {e}")
        if "secretstorage" in err:
            print("\nFor GNOME/XFCE desktops, install: pip install secretstorage")
        elif "kwallet" in err:
            print("\nFor KDE desktops, ensure kwallet is running and unlocked.")
        elif yt_name != "firefox" and ("decrypt" in err or "permission" in err or "keyring" in err):
            print("\nTip: Try Firefox instead — it doesn't encrypt cookies.")
        sys.exit(1)

    if not count:
        print("No YouTube/Google cookies found."
              " Did you log in to YouTube?" if mode != "2" else
              " Are you logged in to YouTube in this browser?")
        sys.exit(1)

    print(f"\nExtracted {count} cookies to {OUTPUT_FILE}")
    print("Cookies are picked up automatically — no restart needed.")


if __name__ == "__main__":
    main()
