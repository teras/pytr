"""Cookie Beast — keeps YouTube cookies alive via stealth headless Firefox."""
import logging
import os
import random
import time
from pathlib import Path

# Fix os.getlogin() in Docker (no tty)
os.getlogin = lambda: os.environ.get("USER", "root")

from undetected_geckodriver import Firefox
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("cookie-beast")

COOKIES_INPUT = Path(os.environ.get("COOKIES_INPUT", "/data/cookies.txt"))
COOKIES_OUTPUT = Path(os.environ.get("COOKIES_OUTPUT", "/data/cookies.txt"))
MIN_INTERVAL = int(os.environ.get("MIN_INTERVAL", "900"))    # 15 min
MAX_INTERVAL = int(os.environ.get("MAX_INTERVAL", "3600"))   # 60 min

CHROME_TO_UNIX = 11644473600

PAGES = [
    ("home", "https://www.youtube.com/"),
    ("trending", "https://www.youtube.com/feed/trending"),
    ("subscriptions", "https://www.youtube.com/feed/subscriptions"),
    ("history", "https://www.youtube.com/feed/history"),
    ("liked", "https://www.youtube.com/playlist?list=LL"),
]

SEARCH_TERMS = [
    "music", "news", "cooking", "travel", "science",
    "documentary", "guitar", "jazz", "nature", "space",
    "technology", "history", "art", "comedy", "diy",
]


def load_cookies_txt(path: Path) -> list[dict]:
    """Parse Netscape cookies.txt, handling Chrome-epoch timestamps."""
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


def export_cookies_txt(driver, path: Path):
    """Export browser cookies to Netscape cookies.txt format."""
    cookies = driver.get_cookies()
    lines = ["# Netscape HTTP Cookie File", ""]
    for c in cookies:
        domain = c.get("domain", "")
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        p = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = str(c.get("expiry", 0))
        lines.append(f"{domain}\t{flag}\t{p}\t{secure}\t{expiry}\t{c['name']}\t{c['value']}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.rename(path)
    log.info("Exported %d cookies", len(cookies))


def create_driver():
    """Create a stealth headless Firefox via undetected-geckodriver."""
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


def action_navigate(driver):
    """Visit a random YouTube page."""
    name, url = random.choice(PAGES)
    log.info("Navigate: %s", name)
    driver.get(url)
    time.sleep(random.uniform(2, 5))


def action_search(driver):
    """Search for a random term."""
    term = random.choice(SEARCH_TERMS)
    log.info("Search: %s", term)
    driver.get(f"https://www.youtube.com/results?search_query={term}")
    time.sleep(random.uniform(3, 6))


def action_search_and_watch(driver):
    """Search for a random term and click a video."""
    term = random.choice(SEARCH_TERMS)
    log.info("Search+watch: %s", term)
    driver.get(f"https://www.youtube.com/results?search_query={term}")
    time.sleep(random.uniform(2, 4))
    thumbs = driver.find_elements(By.CSS_SELECTOR, "a[href*='/watch']")
    if not thumbs:
        log.info("No videos found, skipping")
        return
    random.choice(thumbs[:12]).click()
    stay = random.uniform(5, 15)
    time.sleep(stay)
    log.info("Watched %.0fs: %s", stay, driver.title[:60])
    driver.back()
    time.sleep(random.uniform(1, 3))


# Weighted: 50% navigate, 30% search+watch, 20% search
WEIGHTED_ACTIONS = [
    (action_navigate, 5),
    (action_search_and_watch, 3),
    (action_search, 2),
]


def pick_action():
    total = sum(w for _, w in WEIGHTED_ACTIONS)
    r = random.randint(1, total)
    cumulative = 0
    for action, weight in WEIGHTED_ACTIONS:
        cumulative += weight
        if r <= cumulative:
            return action
    return WEIGHTED_ACTIONS[0][0]


def main():
    if not COOKIES_INPUT.exists():
        log.error("No cookies file at %s — run extract-cookies first", COOKIES_INPUT)
        raise SystemExit(1)

    log.info("Starting Cookie Beast")
    driver = create_driver()

    try:
        # Load initial cookies
        driver.get("https://www.youtube.com/")
        time.sleep(2)
        cookies = load_cookies_txt(COOKIES_INPUT)
        loaded = 0
        for c in cookies:
            try:
                driver.add_cookie(c)
                loaded += 1
            except Exception:
                pass
        log.info("Loaded %d/%d cookies", loaded, len(cookies))

        driver.get("https://www.youtube.com/")
        time.sleep(3)

        # Handle consent dialog
        try:
            consent_buttons = driver.find_elements(By.CSS_SELECTOR,
                "button[aria-label*='Accept'], button[aria-label*='accept'], "
                "form[action*='consent'] button")
            if consent_buttons:
                log.info("Accepting consent dialog")
                consent_buttons[0].click()
                time.sleep(3)
        except Exception:
            pass

        # Verify login
        avatars = driver.find_elements(By.CSS_SELECTOR, "button#avatar-btn, img.yt-spec-avatar-shape__avatar")
        if avatars:
            log.info("Logged in successfully")
        else:
            log.warning("Login status uncertain — continuing anyway")

        # Initial action to verify everything works
        action = pick_action()
        try:
            action(driver)
        except Exception as e:
            log.warning("Initial action failed: %s", e)

        export_cookies_txt(driver, COOKIES_OUTPUT)

        # Main loop: sleep, then act
        while True:
            interval = random.randint(MIN_INTERVAL, MAX_INTERVAL)
            log.info("Sleeping %d minutes", interval // 60)
            time.sleep(interval)

            action = pick_action()
            try:
                action(driver)
            except Exception as e:
                log.warning("Action failed: %s", e)

            export_cookies_txt(driver, COOKIES_OUTPUT)

    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
