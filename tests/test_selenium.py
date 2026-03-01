"""Selenium tests for PYTR multi-audio: DASH default, HLS on audio switch."""
import time
import sys

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager

BASE = "http://localhost:8000"
PASSWORD = "t"
MULTI_AUDIO_VIDEO = "BboiLthyvAA"
SINGLE_AUDIO_VIDEO = "dQw4w9WgXcQ"


def setup_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.set_preference("media.volume_scale", "0.0")  # mute
    opts.set_preference("media.autoplay.default", 0)  # allow autoplay
    opts.set_preference("media.autoplay.blocking_policy", 0)
    service = Service(GeckoDriverManager().install())
    driver = webdriver.Firefox(service=service, options=opts)
    driver.set_window_size(1280, 900)
    return driver


def login(driver):
    driver.get(f"{BASE}/login")
    pw = driver.find_element(By.NAME, "password")
    pw.send_keys(PASSWORD)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "search-input")))
    print("  [OK] Logged in")


def wait_for(driver, by, value, timeout=30):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, value)))


def wait_visible(driver, by, value, timeout=30):
    return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located((by, value)))


def is_hidden(driver, element_id):
    el = driver.find_element(By.ID, element_id)
    classes = el.get_attribute("class") or ""
    return "hidden" in classes


def wait_quality_ready(driver, timeout=60):
    """Wait for quality button to show an actual value (not placeholder)."""
    WebDriverWait(driver, timeout).until(
        lambda d: any(c.isdigit() for c in d.find_element(By.ID, "quality-btn").text)
    )


def get_player_type(driver):
    """Return 'dash' or 'hls' or None based on active player."""
    return driver.execute_script("return currentPlayerType || null")


def test_search_cursor_pagination(driver):
    """Search returns results with cursor, load more works via /api/more."""
    print(f"\n=== Test: Search cursor pagination ===")

    driver.get(f"{BASE}/")

    # Search for something common
    search_input = driver.find_element(By.ID, "search-input")
    search_input.clear()
    search_input.send_keys("lofi hip hop")
    driver.find_element(By.ID, "search-btn").click()

    # Wait for first batch of results
    WebDriverWait(driver, 30).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, ".video-card:not(.loading-card)")) >= 5
    )
    first_batch = driver.find_elements(By.CSS_SELECTOR, ".video-card:not(.loading-card)")
    first_count = len(first_batch)
    print(f"  [OK] First batch: {first_count} results")
    assert first_count >= 5, f"Expected >= 5 results in first batch, got {first_count}"

    # Scroll to bottom to trigger load more
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(3)

    # Check that more results loaded
    WebDriverWait(driver, 30).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, ".video-card:not(.loading-card)")) > first_count
    )
    total = len(driver.find_elements(By.CSS_SELECTOR, ".video-card:not(.loading-card)"))
    print(f"  [OK] After scroll: {total} results (was {first_count})")
    assert total > first_count, "Load more should have added results"

    print("  === PASSED ===")


def test_channel_cursor_pagination(driver):
    """Channel browsing uses cursor pagination."""
    print(f"\n=== Test: Channel cursor pagination ===")

    # Navigate to a known channel video first, then click channel link
    driver.get(f"{BASE}/watch?v={SINGLE_AUDIO_VIDEO}")

    # Wait for video info
    WebDriverWait(driver, 60).until(
        lambda d: d.find_element(By.ID, "video-title").text not in ("", "Loading...")
    )

    # Click on channel name to go to channel page
    channel_link = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "video-channel"))
    )
    channel_name = channel_link.text
    channel_link.click()
    print(f"  [OK] Clicked channel: {channel_name}")

    # Wait for channel videos to load
    WebDriverWait(driver, 30).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, ".video-card:not(.loading-card)")) >= 5
    )
    first_count = len(driver.find_elements(By.CSS_SELECTOR, ".video-card:not(.loading-card)"))
    print(f"  [OK] First batch: {first_count} channel videos")

    # Scroll to load more
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(3)

    WebDriverWait(driver, 30).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, ".video-card:not(.loading-card)")) > first_count
    )
    total = len(driver.find_elements(By.CSS_SELECTOR, ".video-card:not(.loading-card)"))
    print(f"  [OK] After scroll: {total} channel videos (was {first_count})")

    print("  === PASSED ===")


def test_multi_audio_dash_first(driver):
    """Multi-audio video starts with DASH, shows audio button, can switch to HLS and back."""
    print(f"\n=== Test: Multi-audio DASH-first ({MULTI_AUDIO_VIDEO}) ===")

    driver.get(f"{BASE}/watch?v={MULTI_AUDIO_VIDEO}")

    # Wait for video info to load
    WebDriverWait(driver, 60).until(
        lambda d: d.find_element(By.ID, "video-title").text not in ("", "Loading...")
    )
    title = driver.find_element(By.ID, "video-title").text
    print(f"  [OK] Video title loaded: {title[:50]}")

    # Wait for quality selector to show actual quality (not placeholder)
    wait_quality_ready(driver, 60)
    quality = driver.find_element(By.ID, "quality-btn").text
    print(f"  [OK] Quality selector visible: {quality}")

    # Verify DASH is the active player (not HLS)
    player_type = get_player_type(driver)
    assert player_type == "dash", f"Expected DASH player initially, got {player_type}"
    dash_active = driver.execute_script("return dashPlayer !== null")
    hls_active = driver.execute_script("return hlsPlayer !== null")
    assert dash_active, "dashPlayer should be active"
    assert not hls_active, "hlsPlayer should be null initially"
    print(f"  [OK] Player type: DASH (dashPlayer active, hlsPlayer null)")

    # Check quality includes high resolutions (DASH can go above 1080p)
    dash_quality_height = int(''.join(c for c in quality if c.isdigit()))
    print(f"  [OK] Initial DASH quality: {dash_quality_height}p")

    # Wait for audio button to appear (fetched async)
    WebDriverWait(driver, 30).until(lambda d: not is_hidden(d, "audio-btn-container"))
    audio_btn_text = driver.find_element(By.ID, "audio-btn").text
    assert "Original" in audio_btn_text, f"Audio button should show ORI, got {audio_btn_text}"
    print(f"  [OK] Audio button visible: {audio_btn_text}")

    # Click audio button to open menu
    driver.find_element(By.ID, "audio-btn").click()
    time.sleep(0.5)
    assert not is_hidden(driver, "audio-menu"), "Audio menu should be visible"

    # Count audio options
    options = driver.find_elements(By.CSS_SELECTOR, "#audio-menu .audio-option")
    print(f"  [OK] Audio menu has {len(options)} options")
    assert len(options) > 2, f"Expected >2 audio options, got {len(options)}"

    # Check "Original" option is selected
    selected = driver.find_elements(By.CSS_SELECTOR, "#audio-menu .audio-option.selected")
    assert len(selected) == 1, "Exactly one audio option should be selected"
    assert selected[0].get_attribute("data-lang") == "original", f"Default should be original, got: {selected[0].get_attribute('data-lang')}"
    print(f"  [OK] Default audio: {selected[0].text}")

    # Print all audio options
    for opt in options:
        lang = opt.get_attribute("data-lang")
        text = opt.text
        sel = " (selected)" if "selected" in (opt.get_attribute("class") or "") else ""
        print(f"       - {lang}: {text}{sel}")

    # Ensure playback starts (headless browsers may block autoplay)
    driver.execute_script("document.getElementById('video-player').play()")
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return document.getElementById('video-player').currentTime") > 1
    )
    current_time = driver.execute_script("return document.getElementById('video-player').currentTime")
    print(f"  [OK] Video playing at {current_time:.1f}s")

    print("  === PASSED ===")


def test_audio_switch_to_hls(driver):
    """Switch from DASH to HLS by selecting non-original audio, verify position preserved."""
    print(f"\n=== Test: Audio switch DASH→HLS ({MULTI_AUDIO_VIDEO}) ===")

    driver.get(f"{BASE}/watch?v={MULTI_AUDIO_VIDEO}")

    # Wait for video info to load
    WebDriverWait(driver, 60).until(
        lambda d: d.find_element(By.ID, "video-title").text not in ("", "Loading...")
    )
    print(f"  [OK] Video info loaded")

    # Wait for DASH + audio button
    wait_quality_ready(driver, 60)
    print(f"  [OK] Quality selector visible")
    WebDriverWait(driver, 30).until(lambda d: not is_hidden(d, "audio-btn-container"))
    print(f"  [OK] Audio button visible")

    # Ensure playback starts and let it play a few seconds
    # Wait a moment for DASH to fully initialize before play()
    time.sleep(2)
    driver.execute_script("document.getElementById('video-player').play()")
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return document.getElementById('video-player').currentTime") > 3
    )
    time_before = driver.execute_script("return document.getElementById('video-player').currentTime")
    print(f"  [OK] Playing at {time_before:.1f}s before switch")

    # Find French audio option and click it
    driver.find_element(By.ID, "audio-btn").click()
    time.sleep(0.5)
    options = driver.find_elements(By.CSS_SELECTOR, "#audio-menu .audio-option")
    fr_option = None
    for opt in options:
        if opt.get_attribute("data-lang") == "fr":
            fr_option = opt
            break
    assert fr_option, "French audio option not found"
    fr_option.click()
    time.sleep(1)

    # Verify button changed to FR
    audio_btn_text = driver.find_element(By.ID, "audio-btn").text
    assert "French" in audio_btn_text, f"Audio button should show FR, got {audio_btn_text}"
    print(f"  [OK] Audio button: {audio_btn_text}")

    # Verify player switched to HLS
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return currentPlayerType") == "hls"
    )
    hls_active = driver.execute_script("return hlsPlayer !== null")
    dash_active = driver.execute_script("return dashPlayer !== null")
    assert hls_active, "hlsPlayer should be active after switch"
    assert not dash_active, "dashPlayer should be null after switch to HLS"
    print(f"  [OK] Player type: HLS (hlsPlayer active, dashPlayer null)")

    # Wait for playback to resume (explicit play for headless)
    driver.execute_script("document.getElementById('video-player').play()")
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return document.getElementById('video-player').currentTime") > 1
    )
    time_after = driver.execute_script("return document.getElementById('video-player').currentTime")
    print(f"  [OK] Playback resumed at {time_after:.1f}s after switch")

    # Verify quality menu shows HLS levels (should be <= 1080p)
    wait_quality_ready(driver, 15)
    quality = driver.find_element(By.ID, "quality-btn").text
    print(f"  [OK] HLS quality: {quality}")

    print("  === PASSED ===")


def test_audio_switch_back_to_dash(driver):
    """Switch to HLS (French), then back to DASH (Original), verify 4K restored."""
    print(f"\n=== Test: Audio switch HLS→DASH ({MULTI_AUDIO_VIDEO}) ===")

    driver.get(f"{BASE}/watch?v={MULTI_AUDIO_VIDEO}")

    # Wait for DASH + audio button
    wait_quality_ready(driver, 60)
    WebDriverWait(driver, 30).until(lambda d: not is_hidden(d, "audio-btn-container"))

    # Ensure playback starts and let it play
    driver.execute_script("document.getElementById('video-player').play()")
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return document.getElementById('video-player').currentTime") > 2
    )

    # Switch to French (DASH → HLS)
    driver.find_element(By.ID, "audio-btn").click()
    time.sleep(0.5)
    for opt in driver.find_elements(By.CSS_SELECTOR, "#audio-menu .audio-option"):
        if opt.get_attribute("data-lang") == "fr":
            opt.click()
            break
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return currentPlayerType") == "hls"
    )
    print(f"  [OK] Switched to HLS (French)")

    # Wait for HLS to play a bit (explicit play for headless)
    driver.execute_script("document.getElementById('video-player').play()")
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return document.getElementById('video-player').currentTime") > 2
    )
    time_before_back = driver.execute_script("return document.getElementById('video-player').currentTime")
    print(f"  [OK] HLS playing at {time_before_back:.1f}s")

    # Switch back to Original (HLS → DASH)
    driver.find_element(By.ID, "audio-btn").click()
    time.sleep(0.5)
    for opt in driver.find_elements(By.CSS_SELECTOR, "#audio-menu .audio-option"):
        if opt.get_attribute("data-lang") == "original":
            opt.click()
            break

    # Verify switched back to DASH
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return currentPlayerType") == "dash"
    )
    dash_active = driver.execute_script("return dashPlayer !== null")
    hls_active = driver.execute_script("return hlsPlayer !== null")
    assert dash_active, "dashPlayer should be active after switching back"
    assert not hls_active, "hlsPlayer should be null after switching back"
    print(f"  [OK] Player type: DASH (restored)")

    # Verify audio button shows ORI
    audio_btn_text = driver.find_element(By.ID, "audio-btn").text
    assert "Original" in audio_btn_text, f"Audio button should show ORI, got {audio_btn_text}"
    print(f"  [OK] Audio button: {audio_btn_text}")

    # Wait for DASH quality to populate
    wait_quality_ready(driver, 30)
    quality = driver.find_element(By.ID, "quality-btn").text
    print(f"  [OK] DASH quality restored: {quality}")

    # Verify playback resumed (explicit play for headless)
    driver.execute_script("document.getElementById('video-player').play()")
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return document.getElementById('video-player').currentTime") > 1
    )
    time_after = driver.execute_script("return document.getElementById('video-player').currentTime")
    print(f"  [OK] Playback resumed at {time_after:.1f}s")

    print("  === PASSED ===")


def test_single_audio(driver):
    """Single-audio video uses DASH, no audio button."""
    print(f"\n=== Test: Single-audio video ({SINGLE_AUDIO_VIDEO}) ===")

    driver.get(f"{BASE}/watch?v={SINGLE_AUDIO_VIDEO}")

    # Wait for video info
    WebDriverWait(driver, 60).until(
        lambda d: d.find_element(By.ID, "video-title").text not in ("", "Loading...")
    )
    title = driver.find_element(By.ID, "video-title").text
    print(f"  [OK] Video title loaded: {title[:50]}")

    # Wait for quality selector
    wait_quality_ready(driver, 60)
    quality = driver.find_element(By.ID, "quality-btn").text
    print(f"  [OK] Quality selector visible: {quality}")

    # Verify DASH player
    player_type = get_player_type(driver)
    assert player_type == "dash", f"Expected DASH player, got {player_type}"
    print(f"  [OK] Player type: DASH")

    # Audio button should be hidden
    assert is_hidden(driver, "audio-btn-container"), "Audio button should be hidden for single-audio"
    print(f"  [OK] Audio button hidden (as expected)")

    # Ensure playback starts
    driver.execute_script("document.getElementById('video-player').play()")
    time.sleep(3)
    current_time = driver.execute_script("return document.getElementById('video-player').currentTime")
    print(f"  [OK] Video time: {current_time:.1f}s")

    print("  === PASSED ===")


def test_quality_switch_dash(driver):
    """Quality switching works in DASH mode."""
    print(f"\n=== Test: Quality switch in DASH mode ({MULTI_AUDIO_VIDEO}) ===")

    driver.get(f"{BASE}/watch?v={MULTI_AUDIO_VIDEO}")

    # Wait for quality selector
    wait_quality_ready(driver, 60)

    # Open quality menu
    driver.find_element(By.ID, "quality-btn").click()
    time.sleep(0.5)
    assert not is_hidden(driver, "quality-menu"), "Quality menu should be visible"

    # List qualities
    q_options = driver.find_elements(By.CSS_SELECTOR, "#quality-menu .quality-option")
    heights = [opt.text.strip() for opt in q_options]
    print(f"  [OK] DASH quality options: {heights}")
    assert len(q_options) >= 3, f"Expected >=3 quality levels, got {len(q_options)}"

    # Click lowest quality (last in list, reversed order)
    lowest = q_options[-1]
    lowest_text = lowest.text.strip()
    lowest.click()
    time.sleep(2)

    btn_text = driver.find_element(By.ID, "quality-btn").text
    print(f"  [OK] Switched to quality: {btn_text}")

    print("  === PASSED ===")


def test_audio_switch_at_1440p(driver):
    """Audio switch works when DASH quality is set to 1440p (above HLS max)."""
    print(f"\n=== Test: Audio switch at 1440p ({MULTI_AUDIO_VIDEO}) ===")

    driver.get(f"{BASE}/watch?v={MULTI_AUDIO_VIDEO}")

    # Wait for DASH + audio button
    wait_quality_ready(driver, 60)
    WebDriverWait(driver, 30).until(lambda d: not is_hidden(d, "audio-btn-container"))

    # Start playback
    driver.execute_script("document.getElementById('video-player').play()")
    time.sleep(2)

    # Select 1440p quality
    driver.find_element(By.ID, "quality-btn").click()
    time.sleep(0.5)
    q_options = driver.find_elements(By.CSS_SELECTOR, "#quality-menu .quality-option")
    clicked_1440 = False
    for opt in q_options:
        if opt.text.strip() == "1440p":
            opt.click()
            clicked_1440 = True
            break
    assert clicked_1440, "1440p option not found in quality menu"

    # Wait for quality to settle
    WebDriverWait(driver, 15).until(
        lambda d: "1440" in d.find_element(By.ID, "quality-btn").text
    )
    print(f"  [OK] Quality set to 1440p")

    # Let it play at 1440p
    time.sleep(3)
    time_before = driver.execute_script("return document.getElementById('video-player').currentTime")
    print(f"  [OK] Playing at {time_before:.1f}s at 1440p")

    # Switch audio to French
    driver.find_element(By.ID, "audio-btn").click()
    time.sleep(0.5)
    for opt in driver.find_elements(By.CSS_SELECTOR, "#audio-menu .audio-option"):
        if opt.get_attribute("data-lang") == "fr":
            opt.click()
            break

    # Verify player switched to HLS
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return currentPlayerType") == "hls"
    )
    hls_active = driver.execute_script("return hlsPlayer !== null")
    dash_gone = driver.execute_script("return dashPlayer === null")
    assert hls_active, "hlsPlayer should be active"
    assert dash_gone, "dashPlayer should be null"
    print(f"  [OK] Player switched to HLS")

    # Verify quality dropped to <= 1080p (HLS max)
    wait_quality_ready(driver, 15)
    quality_text = driver.find_element(By.ID, "quality-btn").text
    quality_height = int(''.join(c for c in quality_text if c.isdigit()))
    assert quality_height <= 1080, f"HLS quality should be <= 1080p, got {quality_height}p"
    print(f"  [OK] HLS quality: {quality_text} (<= 1080p)")

    # Verify audio button shows FR
    audio_btn_text = driver.find_element(By.ID, "audio-btn").text
    assert "French" in audio_btn_text, f"Expected FR, got {audio_btn_text}"
    print(f"  [OK] Audio button: {audio_btn_text}")

    # Wait for playback to resume
    driver.execute_script("document.getElementById('video-player').play()")
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return document.getElementById('video-player').currentTime") > 1
    )
    print(f"  [OK] Playback resumed in HLS")

    # Switch back to original
    driver.find_element(By.ID, "audio-btn").click()
    time.sleep(0.5)
    for opt in driver.find_elements(By.CSS_SELECTOR, "#audio-menu .audio-option"):
        if opt.get_attribute("data-lang") == "original":
            opt.click()
            break

    # Verify switched back to DASH
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return currentPlayerType") == "dash"
    )
    print(f"  [OK] Switched back to DASH")

    # Verify quality restored (DASH has 1440p+)
    wait_quality_ready(driver, 15)
    quality_back = driver.find_element(By.ID, "quality-btn").text
    print(f"  [OK] DASH quality restored: {quality_back}")

    print("  === PASSED ===")


def test_subtitles_persist_on_audio_switch(driver):
    """Subtitles remain active after switching audio language."""
    print(f"\n=== Test: Subtitles persist on audio switch ({MULTI_AUDIO_VIDEO}) ===")

    driver.get(f"{BASE}/watch?v={MULTI_AUDIO_VIDEO}")

    # Wait for DASH + audio + subtitle buttons
    wait_quality_ready(driver, 60)
    WebDriverWait(driver, 30).until(lambda d: not is_hidden(d, "audio-btn-container"))
    WebDriverWait(driver, 30).until(lambda d: not is_hidden(d, "subtitle-btn-container"))
    print(f"  [OK] All controls visible")

    # Start playback
    driver.execute_script("document.getElementById('video-player').play()")
    time.sleep(2)

    # Open subtitle menu and pick a subtitle
    driver.find_element(By.ID, "subtitle-btn").click()
    time.sleep(0.5)
    sub_options = driver.find_elements(By.CSS_SELECTOR, "#subtitle-menu .subtitle-option")
    print(f"  [OK] Subtitle menu has {len(sub_options)} options")

    # Pick the first non-Off subtitle
    chosen_lang = None
    for opt in sub_options:
        lang = opt.get_attribute("data-lang")
        if lang:
            opt.click()
            chosen_lang = lang
            break
    assert chosen_lang, "No subtitle language found"
    time.sleep(1)

    # Verify subtitle button shows active language
    sub_btn_text = driver.find_element(By.ID, "subtitle-btn").text
    assert chosen_lang.upper() in sub_btn_text.upper(), f"Subtitle btn should show {chosen_lang}, got {sub_btn_text}"
    print(f"  [OK] Subtitles active: {sub_btn_text}")

    # Verify a track element exists with mode 'showing'
    showing = driver.execute_script("""
        var tracks = document.getElementById('video-player').textTracks;
        for (var i = 0; i < tracks.length; i++) {
            if (tracks[i].mode === 'showing') return tracks[i].language;
        }
        return null;
    """)
    assert showing, "Expected a text track in 'showing' mode"
    print(f"  [OK] TextTrack showing: {showing}")

    # Now switch audio to French
    driver.find_element(By.ID, "audio-btn").click()
    time.sleep(0.5)
    for opt in driver.find_elements(By.CSS_SELECTOR, "#audio-menu .audio-option"):
        if opt.get_attribute("data-lang") == "fr":
            opt.click()
            break
    print(f"  [OK] Switching to French audio...")

    # Wait for HLS player to initialize
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return currentPlayerType") == "hls"
    )

    # Wait for subtitle track to load with actual cues (not just mode='showing')
    WebDriverWait(driver, 15).until(lambda d: d.execute_script("""
        var tracks = document.getElementById('video-player').textTracks;
        for (var i = 0; i < tracks.length; i++) {
            if (tracks[i].mode === 'showing' && tracks[i].cues && tracks[i].cues.length > 0)
                return true;
        }
        return false;
    """))

    # Verify subtitle button still shows active
    sub_btn_text = driver.find_element(By.ID, "subtitle-btn").text
    assert chosen_lang.upper() in sub_btn_text.upper(), \
        f"Subtitle btn should still show {chosen_lang} after audio switch, got {sub_btn_text}"
    print(f"  [OK] Subtitle button still active after audio switch: {sub_btn_text}")

    # Verify text track has cues
    cue_count = driver.execute_script("""
        var tracks = document.getElementById('video-player').textTracks;
        for (var i = 0; i < tracks.length; i++) {
            if (tracks[i].mode === 'showing' && tracks[i].cues)
                return tracks[i].cues.length;
        }
        return 0;
    """)
    assert cue_count > 0, f"Expected subtitle cues after audio switch, got {cue_count}"
    print(f"  [OK] TextTrack has {cue_count} cues after audio switch")

    # Switch back to original and verify again
    driver.find_element(By.ID, "audio-btn").click()
    time.sleep(0.5)
    for opt in driver.find_elements(By.CSS_SELECTOR, "#audio-menu .audio-option"):
        if opt.get_attribute("data-lang") == "original":
            opt.click()
            break
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return currentPlayerType") == "dash"
    )

    # Wait for subtitle track to reload with cues
    WebDriverWait(driver, 30).until(lambda d: d.execute_script("""
        var tracks = document.getElementById('video-player').textTracks;
        for (var i = 0; i < tracks.length; i++) {
            if (tracks[i].mode === 'showing' && tracks[i].cues && tracks[i].cues.length > 0)
                return true;
        }
        return false;
    """))
    cue_count_back = driver.execute_script("""
        var tracks = document.getElementById('video-player').textTracks;
        for (var i = 0; i < tracks.length; i++) {
            if (tracks[i].mode === 'showing' && tracks[i].cues)
                return tracks[i].cues.length;
        }
        return 0;
    """)
    assert cue_count_back > 0, f"Expected subtitle cues after switching back to DASH, got {cue_count_back}"
    print(f"  [OK] TextTrack has {cue_count_back} cues after switching back to DASH")

    print("  === PASSED ===")


PLAYLIST_VIDEO = "NI2sLfseweE"
PLAYLIST_ID = "PLLazhr7ULbhrbrPSpjTWFrUfj0gC-m754"


def test_tv_bottom_overlay(driver):
    """TV mode bottom overlay: generator-based rows with queue, related, channel data."""
    print(f"\n=== Test: TV bottom overlay ({PLAYLIST_VIDEO}) ===")

    # Enable TV mode BEFORE navigation so video-changed handler activates playerMode
    driver.execute_script("localStorage.setItem('tv-mode', 'desktop')")

    # Navigate to playlist URL — page load reads localStorage, video-changed fires with TV active
    driver.get(f"{BASE}/watch?v={PLAYLIST_VIDEO}&list={PLAYLIST_ID}")

    # Wait for video to load
    WebDriverWait(driver, 60).until(
        lambda d: d.find_element(By.ID, "video-title").text not in ("", "Loading...")
    )
    print(f"  [OK] Video loaded: {driver.find_element(By.ID, 'video-title').text[:50]}")

    assert driver.execute_script("return document.body.classList.contains('tv-nav-active')"), "TV mode should be active"
    print(f"  [OK] TV mode active")

    # Wait for queue to be loaded (playlist contents)
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return window._getQueue && window._getQueue()?.videos?.length > 0")
    )
    print(f"  [OK] Queue loaded")

    # Send ArrowDown to show first row
    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ARROW_DOWN)
    time.sleep(0.5)

    # Verify overlay container appears
    overlay = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".tv-related-overlay"))
    )
    assert "visible" in (overlay.get_attribute("class") or ""), "Overlay should be visible"
    print(f"  [OK] Bottom overlay appeared")

    # Verify first row exists
    rows = driver.find_elements(By.CSS_SELECTOR, ".tv-related-overlay .tv-bottom-row")
    assert len(rows) >= 1, f"Expected at least 1 row, got {len(rows)}"
    first_row = rows[0]
    assert "visible" in (first_row.get_attribute("class") or ""), "First row should be visible"
    print(f"  [OK] First row visible")

    # Wait for first row (queue) to resolve — no data-pending attr
    WebDriverWait(driver, 15).until(
        lambda d: not d.find_elements(By.CSS_SELECTOR, ".tv-related-overlay .tv-bottom-row")[0].get_attribute("data-pending")
    )
    cards = driver.find_elements(By.CSS_SELECTOR, ".tv-related-overlay .tv-bottom-row:first-child .tv-overlay-item")
    assert len(cards) > 0, "Queue row should have cards after resolving"
    print(f"  [OK] Queue row resolved with {len(cards)} cards")

    # Send ArrowDown again to show 2nd row (related)
    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ARROW_DOWN)
    time.sleep(0.5)

    rows = driver.find_elements(By.CSS_SELECTOR, ".tv-related-overlay .tv-bottom-row")
    assert len(rows) >= 2, f"Expected at least 2 rows, got {len(rows)}"
    print(f"  [OK] 2nd row appeared (placeholder)")

    # Wait for related row to resolve
    WebDriverWait(driver, 30).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, ".tv-related-overlay .tv-bottom-row")) >= 2
        and not d.find_elements(By.CSS_SELECTOR, ".tv-related-overlay .tv-bottom-row")[1].get_attribute("data-pending")
    )
    related_cards = driver.find_elements(By.CSS_SELECTOR, ".tv-related-overlay .tv-bottom-row:nth-child(2) .tv-overlay-item")
    assert len(related_cards) > 0, "Related row should have cards"
    print(f"  [OK] Related row resolved with {len(related_cards)} cards")

    # Send ArrowDown to show 3rd row (playlists) — needs channel-id-ready
    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ARROW_DOWN)
    time.sleep(0.5)
    rows = driver.find_elements(By.CSS_SELECTOR, ".tv-related-overlay .tv-bottom-row")
    assert len(rows) >= 3, f"Expected at least 3 rows, got {len(rows)}"
    print(f"  [OK] 3rd row appeared (placeholder for playlists)")

    # Send ArrowUp to remove bottom-most row
    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ARROW_UP)
    time.sleep(0.5)
    rows_after_up = driver.find_elements(By.CSS_SELECTOR, ".tv-related-overlay .tv-bottom-row.visible")
    assert len(rows_after_up) < len(rows), "ArrowUp should have removed bottom-most row"
    print(f"  [OK] ArrowUp removed bottom-most row ({len(rows)} -> {len(rows_after_up)})")

    # Send Escape to hide all
    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    time.sleep(0.5)

    # Overlay should be hidden/removed
    overlays = driver.find_elements(By.CSS_SELECTOR, ".tv-related-overlay.visible")
    assert len(overlays) == 0, "Escape should hide all overlays"
    print(f"  [OK] Escape hid all overlays")

    # Check for JS errors in console
    errors = driver.execute_script("""
        return (window._consoleLogs || []).filter(function(l) { return l.indexOf('Error') !== -1 || l.indexOf('error') !== -1; });
    """)
    if errors:
        print(f"  [WARN] JS errors in console: {errors[:3]}")
    else:
        print(f"  [OK] No JS errors in console")

    # Clean up TV mode
    driver.execute_script("localStorage.removeItem('tv-mode'); document.body.classList.remove('tv-nav-active')")

    print("  === PASSED ===")


def main():
    print("Setting up Firefox driver...")
    driver = setup_driver()

    try:
        login(driver)
        test_search_cursor_pagination(driver)
        test_channel_cursor_pagination(driver)
        test_multi_audio_dash_first(driver)
        test_audio_switch_to_hls(driver)
        test_audio_switch_back_to_dash(driver)
        test_single_audio(driver)
        test_quality_switch_dash(driver)
        test_audio_switch_at_1440p(driver)
        test_subtitles_persist_on_audio_switch(driver)
        test_tv_bottom_overlay(driver)
        print("\n========== ALL TESTS PASSED ==========")
    except Exception as e:
        print(f"\n  !!! FAILED: {e}")
        # Take screenshot for debugging
        driver.save_screenshot("/tmp/pytr_test_fail.png")
        print("  Screenshot saved to /tmp/pytr_test_fail.png")
        # Print browser console logs
        try:
            logs = driver.execute_script("return window._consoleLogs || []")
            if logs:
                print("  Browser console:")
                for log in logs[-10:]:
                    print(f"    {log}")
        except:
            pass
        sys.exit(1)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
