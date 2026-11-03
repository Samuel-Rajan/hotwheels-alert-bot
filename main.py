import os
import asyncio
import html
import logging

from telegram import Bot
from telegram.constants import ParseMode

from scraper import fetch_all_stores
from db import init_db, is_empty, check_item, mark_missing

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def send_stock_alert(bot: Bot, item: dict):
    title = html.escape(item.get("title", "Hot Wheels Product"))
    platform = html.escape(item.get("platform", "Store"))
    price = html.escape(item.get("price", "Check App"))
    link = html.escape(item.get("link", "https://google.com"), quote=True)

    message = (
        "🔥 <b>HOT WHEELS STOCK / RESTOCK ALERT!</b> 🔥\n\n"
        f"🏎️ <b>Product:</b> {title}\n"
        f"🏬 <b>Store:</b> {platform}\n"
        f"💰 <b>Price:</b> {price}\n\n"
        f"🔗 <a href='{link}'><b>Click here to Buy Now</b></a>"
    )

    if item.get("image"):
        try:
            await bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID, photo=item["image"],
                caption=message, parse_mode=ParseMode.HTML,
            )
            return
        except Exception as e:
            logging.warning(f"Photo send failed, falling back to text: {e}")

    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"Failed to send Telegram message: {e}")


async def monitor_stock():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    init_db()
    first_run = is_empty()
    logging.info("Checking all platforms for Hot Wheels stock & new releases...")

    items, ok_platforms = await fetch_all_stores()
    logging.info(f"Scanned OK: {sorted(ok_platforms) or 'none'} | in-stock items: {len(items)}")

    seen = {p: set() for p in ok_platforms}
    alerts = 0

    for item in items:
        platform, prod_id, title = item.get("platform"), item.get("id"), item.get("title")
        if not platform or not prod_id or not title:
            continue
        key = f"{platform}_{prod_id}"
        seen.setdefault(platform, set()).add(key)

        should_alert = check_item(key, platform, title, item.get("link", ""))
        if should_alert and not first_run:
            logging.info(f"New/restock: {title} on {platform}")
            await send_stock_alert(bot, item)
            alerts += 1
            await asyncio.sleep(1)  # stay under Telegram rate limits

    # Only platforms that scanned successfully may mark items as out of stock
    for platform in ok_platforms:
        mark_missing(platform, seen.get(platform, set()))

    if first_run:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"✅ Hot Wheels bot is live. Tracking {len(items)} in-stock items right now. "
                 f"You'll be alerted on new drops and restocks.",
        )
    logging.info(f"Done. Alerts sent: {alerts}")


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram credentials missing from environment variables!")
        raise SystemExit(1)
    try:
        asyncio.run(asyncio.wait_for(monitor_stock(), timeout=240.0))
    except asyncio.TimeoutError:
        logging.error("❌ Execution timed out after 240 seconds and was aborted.")
