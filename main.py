import os
import io
import asyncio
import html
import logging
import threading
import time

from telegram import Bot
from telegram.constants import ParseMode

from scraper import fetch_all_stores, launch_browser
from cart import Verifier, CartSession, CART_ATTEMPTS
from db import (init_db, is_first_run, mark_seeded, check_item, mark_missing, mark_out, forget)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

MAX_VERIFY = 12          # candidates verified/alerted per run (extras are retried next run)
GATE_MAX = 12            # candidates per run that get the My Shortlist deliverability check before alerting
GATE_BUDGET = 60         # seconds allowed per deliverability check
MAX_CART_ITEMS = 12      # items per run that get the add-to-cart treatment
RUN_BUDGET = 330         # seconds after which no new gate/cart job is started
CART_ITEM_BUDGET = 100   # seconds allowed per item for the wishlist -> cart retries
HARD_STOP = 500          # never run browser work past this many seconds into the run
WATCHDOG = 585           # the process is force-exited after this many seconds, whatever happens

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


async def send_stock_alert(bot: Bot, item: dict, stock_status: str, gate: dict = None) -> bool:
    title = html.escape(item.get("title", "Hot Wheels Product"))
    platform = html.escape(item.get("platform", "Store"))
    price = html.escape(item.get("price", "Check App"))
    link = html.escape(item.get("link", ""), quote=True)
    check = "✅ Verified on product page" if stock_status == "in" else "⚠️ Listing says in stock (product page not verified)"
    if gate and gate.get("state") == "ok":
        pin = f" {html.escape(gate['pincode'])}" if gate.get("pincode") else ""
        check += f"\n📍 Deliverable to your pincode{pin} ✅"
    message = (
        "🔥 <b>HOT WHEELS STOCK / RESTOCK ALERT!</b> 🔥\n\n"
        f"🏎️ <b>Product:</b> {title}\n"
        f"🏬 <b>Store:</b> {platform}\n"
        f"💰 <b>Price:</b> {price}\n"
        f"{check}\n\n"
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
    pin = f" (pincode {html.escape(res['pincode'])})" if res.get("pincode") else ""
    acct = f"\n👤 Bot is logged in as: {html.escape(res['account'])}" if res.get("account") else ""
    if st == "added":
        proof = " Confirmed from a separate login session." if res.get("persisted") else ""
        head = f"🛒 <b>In your cart ✅</b> via Wishlist (attempt {res['tries']}/{CART_ATTEMPTS}).{proof}{acct}"
    elif st == "session_only":
        back = " I put the car back on your Shortlist." if res.get("restored") else ""
        head = ("⚠️ <b>Added in the bot's browser, but NOT in your own cart.</b> A fresh session with the same saved "
                f"login sees an empty cart.{back}{acct}")
    elif st == "shortlist_only":
        head = ("📌 <b>On your FirstCry Shortlist</b>: open the app → Shortlist → Add to cart. "
                f"(The bot's cart is not your cart, so I don't add it there.){acct}")
    elif st == "sold_out":
        head = "⚠️ <b>Sold out before I could add it</b>"
    elif st == "undeliverable":
        head = f"🚫 <b>Not deliverable to your pincode{pin}</b>: FirstCry won't let you add this car."
    else:
        head = (f"❌ <b>Could not confirm it in the cart</b> after {res['tries']} wishlist → cart attempts. "
                f"Open the link and add it manually.")
    lines = [head, f"🏎️ {title}", f"🔗 <a href='{link}'>Open product</a>"]
    if st == "added" and res.get("logged_in") is not True:
        lines.append("ℹ️ This used a guest session, so it is <b>not</b> in your own FirstCry account cart. "
                     "Set the FIRSTCRY_STATE_B64 secret (see login_helper.py) to fill your real cart.")
    if st == "session_only":
        lines.append("What to check: (1) in your own browser, is the top right of firstcry.com showing the SAME account "
                     "as above (not 'Login / Register')? (2) If not, log in there with the same method you used in "
                     "login_helper.py. (3) If it is the same account, re-run login_helper.py and update FIRSTCRY_STATE_B64.")
    if st == "failed" and res.get("logged_in") is False and "log in" not in (res.get("detail") or ""):
        lines.append("ℹ️ This ran as a guest, so Wishlist and cart can't be tied to your account. "
                     "Set FIRSTCRY_STATE_B64 (see login_helper.py).")
    if st == "failed" and res.get("detail"):
        lines.append(f"Reason: {html.escape(res['detail'])}")
    await send_text(bot, "\n".join(lines))


async def send_evidence(bot: Bot, item: dict, res: dict):
    """On a cart failure, send what FirstCry's pages really looked like so the code can be fixed from facts."""
    ev = res.get("evidence") or {}
    pid = item["id"]
    files = [
        ("probe", f"probe_{pid}.json", "🔧 Debug: what is inside this car's card"),
        ("card_html", f"card_{pid}.html", None),
        ("shortlist_probe", f"shortlist_probe_{pid}.json", "🔧 Debug: state after clicking Shortlist + My Shortlist page"),
    ]
    try:
        for key, name, caption in files:
            if ev.get(key):
                await bot.send_document(chat_id=TELEGRAM_CHAT_ID, document=io.BytesIO(ev[key].encode("utf-8")),
                                        filename=name, caption=caption)
        for key, name in (("listing_png", f"listing_{pid}.png"), ("shortlist_png", f"my_shortlist_{pid}.png")):
            if ev.get(key):
                await bot.send_document(chat_id=TELEGRAM_CHAT_ID, document=io.BytesIO(ev[key]), filename=name)
    except Exception as e:
        logging.warning(f"Could not send debug files: {e}")


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

        confirmed, alerts = [], 0
        verifier = session = None
        evidence_sent = cart_png_sent = False
        try:
            if candidates:
                verifier = await Verifier.create(browser)
                if any(c["platform"] == "FirstCry" for c in candidates):
                    session = await CartSession.create(browser)

            # ---------------- Phase 1: verify, check deliverability, then alert
            gates_done = 0
            for n, item in enumerate(candidates):
                key, is_test = item["key"], item["key"] in test_keys
                if n >= MAX_VERIFY:
                    if not is_test:
                        forget(key)          # retry on the next run
                    continue
                is_fc = item["platform"] == "FirstCry"
                status = await verifier.status(item) if is_fc else "unknown"
                logging.info(f"Candidate {item['title'][:50]} -> product page: {status}")
                if status in ("out", "undeliverable"):
                    if not is_test:
                        mark_out(key)        # listing was wrong / not deliverable; alert on a real restock
                    else:
                        await send_text(bot, f"🧪 Test: skipped ({status}) {html.escape(item['title'][:70])}")
                    continue

                gate = None
                if (session and is_fc and gates_done < GATE_MAX and time.monotonic() - t0 < RUN_BUDGET):
                    gates_done += 1
                    gate = await session.gate(item, min(time.monotonic() + GATE_BUDGET, t0 + HARD_STOP))
                    logging.info(f"Gate {item['id']}: {gate.get('state')} ({gate.get('detail')})")
                    if gate.get("evidence") and not evidence_sent:
                        await send_evidence(bot, item, {"evidence": gate["evidence"]})
                        evidence_sent = True
                    if gate["state"] in ("undeliverable", "oos"):
                        if not is_test:
                            mark_out(key)
                        else:
                            await send_text(bot, f"🧪 Test: skipped, FirstCry shows it as {gate['state']} "
                                                 f"(pincode {gate.get('pincode') or '?'}): {html.escape(item['title'][:70])}")
                        continue

                if await send_stock_alert(bot, item, status, gate):
                    alerts += 1
                    confirmed.append(item)
                elif not is_test:
                    forget(key)              # alert failed: retry next run
                await asyncio.sleep(1)

            # ---------------- Phase 2: add to cart (after all alerts are out)
            fc_items = [i for i in confirmed if i["platform"] == "FirstCry"]
            if session:
                for item in fc_items[:MAX_CART_ITEMS]:
                    if time.monotonic() - t0 > RUN_BUDGET:
                        await send_text(bot, f"⏱️ Ran out of run time before reaching this car (add it manually): "
                                             f"{html.escape(item['title'][:70])}")
                        continue
                    deadline = min(time.monotonic() + CART_ITEM_BUDGET, t0 + HARD_STOP)
                    res = await session.add(item, deadline)
                    logging.info(f"Cart result for {item['id']}: {res}")
                    await send_cart_result(bot, item, res)
                    if res.get("cart_png") and not cart_png_sent:
                        try:
                            await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=io.BytesIO(res["cart_png"]),
                                                 caption="📸 The cart page as the bot sees it")
                        except Exception as e:
                            logging.warning(f"Could not send cart screenshot: {e}")
                        cart_png_sent = True
                    if res["status"] == "failed" and res.get("evidence") and not evidence_sent:
                        await send_evidence(bot, item, res)
                        evidence_sent = True
            if len(fc_items) > MAX_CART_ITEMS:
                await send_text(bot, f"ℹ️ {len(fc_items) - MAX_CART_ITEMS} more car(s) alerted without a cart attempt "
                                     f"(limit {MAX_CART_ITEMS} per run).")
        finally:
            for closer in (verifier, session):
                if closer:
                    try:
                        await asyncio.wait_for(closer.close(), timeout=10)
                    except Exception:
                        pass

    if first_run and items:
        mark_seeded()
        await send_text(bot, f"✅ Hot Wheels bot is live. Tracking {len(items)} in-stock items right now. "
                             f"You'll be alerted on new drops and restocks, and I'll try to add them to your cart.")
    logging.info(f"Done in {time.monotonic() - t0:.0f}s. Alerts sent: {alerts}")


def _watchdog():
    logging.error(f"❌ Watchdog: run exceeded {WATCHDOG}s, forcing exit so the database can still be saved.")
    os._exit(0)


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram credentials missing from environment variables!")
        raise SystemExit(1)
    timer = threading.Timer(WATCHDOG, _watchdog)
    timer.daemon = True
    timer.start()
    try:
        asyncio.run(asyncio.wait_for(monitor_stock(), timeout=540.0))
    except asyncio.TimeoutError:
        logging.error("❌ Execution timed out after 540 seconds and was aborted.")
    finally:
        timer.cancel()
