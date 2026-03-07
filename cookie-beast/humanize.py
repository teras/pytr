"""Human-like browser interactions via Selenium ActionChains + Bézier curves."""
import random
import time

from selenium.webdriver.common.action_chains import ActionChains


def _bezier_points(start: tuple, end: tuple, steps: int = 20) -> list[tuple]:
    """Generate points along a cubic Bézier curve between start and end."""
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    # Control points: slight curve, clamped to avoid going off-screen
    jitter_x = max(abs(dx) * 0.15, 20)
    jitter_y = max(abs(dy) * 0.15, 20)
    cp1x = sx + dx * random.uniform(0.2, 0.4) + random.uniform(-jitter_x, jitter_x)
    cp1y = sy + dy * random.uniform(0.2, 0.4) + random.uniform(-jitter_y, jitter_y)
    cp2x = sx + dx * random.uniform(0.6, 0.8) + random.uniform(-jitter_x, jitter_x)
    cp2y = sy + dy * random.uniform(0.6, 0.8) + random.uniform(-jitter_y, jitter_y)
    points = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt**3 * sx + 3 * mt**2 * t * cp1x + 3 * mt * t**2 * cp2x + t**3 * ex
        y = mt**3 * sy + 3 * mt**2 * t * cp1y + 3 * mt * t**2 * cp2y + t**3 * ey
        points.append((int(x), int(y)))
    return points


def _clamp_points(points: list[tuple], vw: int, vh: int) -> list[tuple]:
    """Clamp points to viewport bounds with margin."""
    margin = 5
    return [(max(margin, min(x, vw - margin)), max(margin, min(y, vh - margin))) for x, y in points]


def move_to_element(driver, element):
    """Move mouse to element with human-like Bézier curve."""
    vw = driver.execute_script("return window.innerWidth")
    vh = driver.execute_script("return window.innerHeight")

    # Scroll element into view first
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'})", element)
    time.sleep(random.uniform(0.2, 0.5))

    # Get element position relative to viewport
    rect = driver.execute_script(
        "var r = arguments[0].getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}", element)
    target_x = int(rect["x"] + rect["w"] / 2)
    target_y = int(rect["y"] + rect["h"] / 2)

    # Start from roughly center of viewport
    start = (vw // 2 + random.randint(-100, 100), vh // 2 + random.randint(-100, 100))
    end = (target_x, target_y)
    points = _bezier_points(start, end, steps=random.randint(12, 25))
    points = _clamp_points(points, vw, vh)

    # Reset mouse position to start
    actions = ActionChains(driver)
    actions.move_by_offset(0, 0)  # ensure pointer is initialized
    actions.perform()

    # Move through points using move_to_location via JS
    for px, py in points:
        ActionChains(driver).move_by_offset(0, 0).perform()
        driver.execute_script(
            "document.elementFromPoint(arguments[0], arguments[1])", px, py)
        time.sleep(random.uniform(0.01, 0.04))

    # Final precise move to element
    ActionChains(driver).move_to_element(element).perform()
    time.sleep(random.uniform(0.05, 0.15))


def click_element(driver, element):
    """Scroll into view, move to element, then click."""
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'})", element)
    time.sleep(random.uniform(0.2, 0.5))
    # Small pause then click
    actions = ActionChains(driver)
    actions.move_to_element(element)
    actions.pause(random.uniform(0.05, 0.2))
    actions.click()
    actions.perform()


def smooth_scroll(driver, amount: int):
    """Scroll smoothly in small increments."""
    direction = 1 if amount > 0 else -1
    remaining = abs(amount)
    while remaining > 0:
        step = min(remaining, random.randint(30, 80))
        driver.execute_script(f"window.scrollBy(0, {step * direction})")
        remaining -= step
        time.sleep(random.uniform(0.02, 0.08))
