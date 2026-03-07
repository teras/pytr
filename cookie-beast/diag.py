"""Diagnostic script — test cookie loading, stealth, and YouTube session."""
import os
import time
from pathlib import Path

# Fix os.getlogin() in Docker (no tty)
os.getlogin = lambda: os.environ.get("USER", "root")

from undetected_geckodriver import Firefox
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options

import humanize as human

COOKIES_FILE = Path("/data/cookies.txt")
CHROME_TO_UNIX = 11644473600


def load_cookies_txt(path: Path) -> list[dict]:
    cookies = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, cookie_path, secure, expiry_str, name, value = parts[:7]
        expiry = int(expiry_str)
        if expiry > 100000000000:
            expiry = int(expiry / 1000000) - CHROME_TO_UNIX
        cookie = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": cookie_path,
            "secure": secure == "TRUE",
        }
        if expiry > 0:
            cookie["expiry"] = expiry
        cookies.append(cookie)
    return cookies


def create_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--width=1920")
    opts.add_argument("--height=1080")
    opts.set_preference("general.useragent.override",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0")
    opts.set_preference("privacy.resistFingerprinting", False)
    opts.set_preference("general.platform.override", "Win64")
    opts.set_preference("general.oscpu.override", "Windows NT 10.0; Win64; x64")
    return Firefox(options=opts)


def main():
    print("=== Cookie Daemon Diagnostic (stealth mode) ===\n")

    if not COOKIES_FILE.exists():
        print(f"ERROR: {COOKIES_FILE} not found")
        return
    cookies = load_cookies_txt(COOKIES_FILE)
    yt_cookies = [c for c in cookies if "youtube" in c["domain"] or "google" in c["domain"]]
    print(f"1. Loaded {len(cookies)} cookies ({len(yt_cookies)} YouTube/Google)")

    print("\n2. Starting stealth headless Firefox (undetected-geckodriver)...")
    driver = create_driver()

    try:
        # 3. Check stealth
        print("\n3. Stealth checks...")
        driver.get("https://www.youtube.com/")
        time.sleep(2)
        webdriver_val = driver.execute_script("return navigator.webdriver")
        print(f"   navigator.webdriver = {webdriver_val}")
        ua = driver.execute_script("return navigator.userAgent")
        print(f"   User-Agent: {ua}")
        platform = driver.execute_script("return navigator.platform")
        print(f"   Platform: {platform}")
        plugins = driver.execute_script("return navigator.plugins.length")
        print(f"   Plugins count: {plugins}")

        # 4. Inject cookies
        print("\n4. Injecting cookies...")
        loaded = failed = 0
        for c in cookies:
            try:
                driver.add_cookie(c)
                loaded += 1
            except Exception:
                failed += 1
        print(f"   Loaded: {loaded}, Skipped: {failed}")

        # 5. Reload
        print("\n5. Reloading with cookies...")
        driver.get("https://www.youtube.com/")
        time.sleep(4)

        # Handle consent
        try:
            consent_buttons = driver.find_elements(By.CSS_SELECTOR,
                "button[aria-label*='Accept'], button[aria-label*='accept'], "
                "form[action*='consent'] button")
            if consent_buttons:
                print("   Consent dialog found, accepting...")
                human.click_element(driver, consent_buttons[0])
                time.sleep(3)
        except Exception:
            pass

        print(f"   Title: {driver.title}")
        print(f"   URL: {driver.current_url}")

        # 6. Login check
        print("\n6. Login status...")
        avatars = driver.find_elements(By.CSS_SELECTOR, "button#avatar-btn, img.yt-spec-avatar-shape__avatar")
        signin = driver.find_elements(By.CSS_SELECTOR, "a[href*='accounts.google.com'], ytd-button-renderer a[aria-label*='Sign in']")
        if avatars:
            print("   >>> LOGGED IN <<<")
        elif signin:
            print("   >>> NOT LOGGED IN <<<")
        else:
            print("   >>> UNCERTAIN <<<")

        # 7. Page content
        print("\n7. Page content...")
        body = driver.find_element(By.TAG_NAME, "body").text[:300]
        print(f"   {body[:200]}")

        # 8. Test subscriptions
        print("\n8. Testing subscriptions (requires login)...")
        driver.get("https://www.youtube.com/feed/subscriptions")
        time.sleep(3)
        print(f"   Title: {driver.title}")
        print(f"   URL: {driver.current_url}")
        print(f"   Redirected to login: {'accounts.google.com' in driver.current_url}")

        # 9. Test human-like actions
        print("\n9. Testing human-like actions...")
        driver.get("https://www.youtube.com/")
        time.sleep(3)
        human.smooth_scroll(driver, 400)
        print("   Smooth scrolled 400px")
        time.sleep(1)
        thumbnails = driver.find_elements(By.CSS_SELECTOR, "a#thumbnail[href]")
        if thumbnails:
            human.move_to_element(driver, thumbnails[0])
            print("   Moved mouse to first thumbnail (Bézier curve)")
            time.sleep(1)

        # 10. Browser cookies count
        print("\n10. Browser cookies...")
        browser_cookies = driver.get_cookies()
        yt_browser = [c for c in browser_cookies if "youtube" in c.get("domain", "") or "google" in c.get("domain", "")]
        print(f"    Total: {len(browser_cookies)}, YouTube/Google: {len(yt_browser)}")

        # 11. Screenshot
        driver.save_screenshot("/data/diag_screenshot.png")
        print("\n11. Screenshot saved to /data/diag_screenshot.png")

    finally:
        driver.quit()
        print("\nDone.")


if __name__ == "__main__":
    main()
