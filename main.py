import os
import asyncio
import html
import logging
import time

from telegram import Bot
from telegram.constants import ParseMode

from scraper import fetch_all_stores, launch_browser
from cart import Verifier, CartSession, CART_ATTEMPTS
from db import (init_db, is_first_run, mark_seeded, check_item, mark_missing, mark_out, forget)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

MAX_VERIFY = 12          # candidates verified/alerted per run (extras are retried next run)
MAX_CART_ITEMS = 3       # items per run that get the add-to-cart treatment
RUN_BUDGET = 400         # seconds after which no new cart job is started
CART_ITEM_BUDGET = 200   # seconds allowed per item for the wishlist -> cart retries
HARD_STOP = 515          # never run cart work past this many seconds into the run

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def _int_env(name: str) -> int:
    try:
        return int(os.getenv(name) or 0)
    except ValueError:
        return 0


async def send_text(bot: Bot, text: str):
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")


async def send_stock_alert(bot: Bot, item: dict, stock_status: str) -> bool:
    title = html.escape(item.get("title", "Hot Wheels Product"))
    platform = html.escape(item.get("platform", "Store"))
    price = html.escape(item.get("price", "Check App"))
    link = html.escape(item.get("link", ""), quote=True)
    check = {"in": "✅ Verified on product page", "unknown": "⚠️ Listing says in stock (product page not verified)"}
    message = (
        "🔥 <b>HOT WHEELS STOCK / RESTOCK ALERT!</b> 🔥\n\n"
        f"🏎️ <b>Product:</b> {title}\n"
        f"🏬 <b>Store:</b> {platform}\n"
        f"💰 <b>Price:</b> {price}\n"
        f"{check.get(stock_status, '')}\n\n"
        f"🔗 <a href='{link}'><b>Open this car &amp; buy now</b></a>\n"
        "🛒 Adding it to your cart via Wishlist now…"
    )
    if item.get("image"):
        try:
            await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=item["image"], caption=message,
                                 parse_mode=ParseMode.HTML)
            return True
        except Exception as e:
            logging.warning(f"Photo send failed, falling back to text: {e}")
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode=ParseMode.HTML)
        return True
    except Exception as e:
        logging.error(f"Failed to send alert: {e}")
        return False


async def send_cart_result(bot: Bot, item: dict, res: dict):
    title = html.escape(item["title"][:80])
    link = html.escape(item["link"], quote=True)
    st = res["status"]
    if st == "added":
        head = f"🛒 <b>In your cart ✅</b> via Wishlist (attempt {res['tries']}/{CART_ATTEMPTS})"
    elif st == "sold_out":
        head = "⚠️ <b>Sold out before I could add it</b>"
    else:
        head = (f"❌ <b>Could not confirm it in the cart</b> after {res['tries']} wishlist → cart attempts. "
                f"Open the link and add it manually.")
    lines = [head, f"🏎️ {title}", f"🔗 <a href='{link}'>Open product</a>"]
    if st == "added" and res.get("logged_in") is not True:
        lines.append("ℹ️ This used a guest session, so it is <b>not</b> in your own FirstCry account cart. "
                     "Set the FIRSTCRY_STATE_B64 secret (see login_helper.py) to fill your real cart.")
    if st == "failed" and res.get("logged_in") is not True:
        lines.append("ℹ️ Guest/unknown session: Wishlist needs a logged-in session. Set FIRSTCRY_STATE_B64 "
                     "(it may also have expired; re-run login_helper.py).")
    if st == "failed" and res.get("detail"):
        lines.append(f"Reason: {html.escape(res['detail'])}")
    await send_text(bot, "\n".join(lines))


async def monitor_stock():
    t0 = time.monotonic()
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    init_db()
    first_run = is_first_run()
    test_count = _int_env("TEST_COUNT")
    logging.info("Checking all platforms for Hot Wheels stock & new releases...")

    async with launch_browser() as (p, browser):
        items, ok_platforms = await fetch_all_stores(p, browser)
        logging.info(f"Scanned OK: {sorted(ok_platforms) or 'none'} | in-stock items: {len(items)}")

        seen = {pf: set() for pf in ok_platforms}
        candidates = []
        for item in items:
            platform, prod_id, title = item.get("platform"), item.get("id"), item.get("title")
            if not platform or not prod_id or not title:
                continue
            key = f"{platform}_{prod_id}"
            item["key"] = key
            seen.setdefault(platform, set()).add(key)
            if check_item(key, platform, title, item.get("link", "")) and not first_run:
                candidates.append(item)

        for pf in ok_platforms:
            mark_missing(pf, seen.get(pf, set()))

        # Test mode: run the full alert + cart flow on the first N in-stock items without touching the DB.
        test_keys = set()
        if test_count:
            have = {c["key"] for c in candidates}
            extra = [i for i in items if i.get("key") and i["key"] not in have][:test_count]
            test_keys = {i["key"] for i in extra}
            candidates = extra + candidates
            logging.info(f"TEST MODE: {len(extra)} existing item(s) added as candidates")

        # ---------------- Phase 1: verify on product page, then alert immediately
        confirmed, alerts = [], 0
        if candidates:
            verifier = await Verifier.create(browser)
            try:
                for n, item in enumerate(candidates):
                    key, is_test = item["key"], item["key"] in test_keys
                    if n >= MAX_VERIFY:
                        if not is_test:
                            forget(key)          # retry on the next run
                        continue
                    status = await verifier.status(item) if item["platform"] == "FirstCry" else "unknown"
                    logging.info(f"Candidate {item['title'][:50]} -> product page: {status}")
                    if status == "out":
                        if not is_test:
                            mark_out(key)        # listing was wrong; alert on the real restock
                        continue
                    if await send_stock_alert(bot, item, status):
                        alerts += 1
                        confirmed.append(item)
                    elif not is_test:
                        forget(key)              # alert failed: retry next run
                    await asyncio.sleep(1)
            finally:
                await verifier.close()

        # ---------------- Phase 2: add to cart (after all alerts are out)
        fc_items = [i for i in confirmed if i["platform"] == "FirstCry"]
        if fc_items:
            session = await CartSession.create(browser)
            try:
                for item in fc_items[:MAX_CART_ITEMS]:
                    if time.monotonic() - t0 > RUN_BUDGET:
                        await send_text(bot, f"⏱️ Skipped cart attempt (run time budget): "
                                             f"{html.escape(item['title'][:70])}")
                        continue
                    deadline = min(time.monotonic() + CART_ITEM_BUDGET, t0 + HARD_STOP)
                    res = await session.add(item, deadline)
                    logging.info(f"Cart result for {item['id']}: {res}")
                    await send_cart_result(bot, item, res)
            finally:
                await session.close()
        if len(fc_items) > MAX_CART_ITEMS:
            await send_text(bot, f"ℹ️ {len(fc_items) - MAX_CART_ITEMS} more car(s) alerted without a cart attempt "
                                 f"(limit {MAX_CART_ITEMS} per run).")

    if first_run and items:
        mark_seeded()
        await send_text(bot, f"✅ Hot Wheels bot is live. Tracking {len(items)} in-stock items right now. "
                             f"You'll be alerted on new drops and restocks, and I'll try to add them to your cart.")
    logging.info(f"Done in {time.monotonic() - t0:.0f}s. Alerts sent: {alerts}")


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram credentials missing from environment variables!")
        raise SystemExit(1)
    try:
        asyncio.run(asyncio.wait_for(monitor_stock(), timeout=540.0))
    except asyncio.TimeoutError:
        logging.error("❌ Execution timed out after 540 seconds and was aborted.")
