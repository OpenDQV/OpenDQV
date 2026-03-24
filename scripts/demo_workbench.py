"""
Workbench demo GIF — captures key Streamlit UI states via Playwright,
then stitches them into docs/demo_workbench.gif using Pillow.

Run with:
    python3 scripts/demo_workbench.py
"""

import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

URL = "http://localhost:8501"
OUT = Path("docs/demo_workbench.gif")
FRAMES_DIR = Path("/tmp/wb_frames")
FRAMES_DIR.mkdir(exist_ok=True)


CROP_TOP = 590   # y-pixel where useful content starts (below dev-mode banner)


def _shot(page, name: str, wait_ms: int = 0) -> Path:
    if wait_ms:
        page.wait_for_timeout(wait_ms)
    path = FRAMES_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    # Crop out the dev-mode warning banner that spans the top of every page
    img = Image.open(path)
    w, h = img.size
    cropped = img.crop((0, CROP_TOP, w, h))
    cropped.save(path)
    print(f"  captured {name}")
    return path


def capture(page) -> list[tuple[Path, int]]:
    """Returns list of (path, hold_frames) tuples."""
    shots: list[tuple[Path, int]] = []

    # ── 1. Contracts table — scroll past dev warning ───────────────────────────
    page.goto(URL)
    page.wait_for_selector("h1", timeout=30000)
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(2000)
    # Scroll to Data Contracts section, past the warning banner
    page.evaluate("document.querySelector('h2') && document.querySelector('h2').scrollIntoView()")
    page.wait_for_timeout(800)
    shots.append((_shot(page, "01_contracts"), 40))

    # ── 2. Type in the search box ──────────────────────────────────────────────
    search = page.locator("input[placeholder*='Filter']").first
    if not search.is_visible():
        search = page.locator("input[type='text']").first
    if search.is_visible():
        search.fill("banking")
        shots.append((_shot(page, "02_search_banking", wait_ms=1200), 35))
        search.fill("")
        page.wait_for_timeout(500)

    # ── 3. Industry filter — open dropdown and pick Healthcare ────────────────
    multiselect = page.locator("[data-testid='stMultiSelect']").first
    if multiselect.is_visible():
        multiselect.click()
        page.wait_for_timeout(600)
        # Click Healthcare option
        option = page.locator("li", has_text="Healthcare").first
        if option.is_visible():
            option.click()
            page.wait_for_timeout(800)
        shots.append((_shot(page, "03_healthcare_filter"), 35))
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

    # ── 4. Navigate to Validate ────────────────────────────────────────────────
    page.locator("[data-testid='stSidebar']").locator("button", has_text="Validate").click()
    page.wait_for_load_state("networkidle", timeout=10000)
    page.wait_for_timeout(2000)
    shots.append((_shot(page, "04_validate"), 40))

    # ── 5. Navigate to Monitoring ─────────────────────────────────────────────
    page.locator("text=Monitoring").first.click()
    page.wait_for_load_state("networkidle", timeout=10000)
    page.wait_for_timeout(2000)
    shots.append((_shot(page, "05_monitoring"), 40))

    return shots


def build_gif(shots: list[tuple[Path, int]]) -> None:
    frames: list[Image.Image] = []
    for path, hold in shots:
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        target_w = 1200
        target_h = int(h * target_w / w)
        img = img.resize((target_w, target_h), Image.LANCZOS)
        frames.extend([img] * hold)

    if not frames:
        print("No frames captured.", file=sys.stderr)
        sys.exit(1)

    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        optimize=False,
        duration=80,   # 80ms per frame ≈ 12fps
        loop=0,
    )
    print(f"\nSaved {OUT} — {len(frames)} frames, {OUT.stat().st_size // 1024}KB")


if __name__ == "__main__":
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 1100})
        page = ctx.new_page()
        print(f"Navigating to {URL} ...")
        shots = capture(page)
        browser.close()

    print(f"Building GIF from {len(shots)} screenshots ...")
    build_gif(shots)
