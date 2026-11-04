"""
Run this ON YOUR OWN COMPUTER (not on GitHub) to let the bot use your FirstCry account.

    pip install playwright
    playwright install chromium
    python login_helper.py

A browser opens. Log in to FirstCry (OTP) yourself, then press Enter in the terminal.
The script prints the file 'firstcry_state.b64.txt'. Paste its ENTIRE contents into a GitHub secret
named FIRSTCRY_STATE_B64 (Settings -> Secrets and variables -> Actions -> New repository secret).

Treat that text like a password: anyone with it can act as you on FirstCry.
Never commit these files (they are listed in .gitignore). Delete them after saving the secret.
The session expires after a while; just run this again and update the secret.
"""
import asyncio
import base64
import gzip
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-IN")
        page = await ctx.new_page()
        await page.goto("https://www.firstcry.com/")
        print("\n1) In the browser window, click Login / Register and sign in with your OTP.")
        print("2) Check that you can see 'My Account'.")
        input("3) Press Enter here when you are logged in... ")
        state = await ctx.storage_state()
        await browser.close()

    raw = json.dumps(state).encode()
    Path("firstcry_state.json").write_bytes(raw)
    b64 = base64.b64encode(gzip.compress(raw)).decode()
    Path("firstcry_state.b64.txt").write_text(b64)
    print(f"\nSaved firstcry_state.b64.txt ({len(b64)} characters).")
    if len(b64) > 48000:
        print("WARNING: larger than GitHub's 48 KB secret limit. Log in again with fewer tabs/extensions.")
    print("Copy its contents into the GitHub secret FIRSTCRY_STATE_B64, then delete both files.")


if __name__ == "__main__":
    asyncio.run(main())
