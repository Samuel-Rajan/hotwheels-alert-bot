import os
import asyncio
import logging
from telegram import Bot
from telegram.constants import ParseMode
from scraper import fetch_all_stores
from db import init_db, process_item_and_check_restock

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def send_stock_alert(bot: Bot, item: dict):
    """Formats and sends a Telegram message with a direct buy link."""
    title = item.get('title', 'Hot Wheels Product')
    platform = item.get('platform', 'Store')
    price = item.get('price', 'Check App')
    link = item.get('link', 'https://google.com')
    
    message = (
        f"🔥 <b>HOT WHEELS STOCK / RESTOCK ALERT!</b> 🔥\n\n"
        f"🏎️ <b>Product:</b> {title}\n"
        f"🏬 <b>Store:</b> {platform}\n"
        f"💰 <b>Price:</b> {price}\n\n"
        f"🔗 <a href='{link}'><b>Click here to Buy Now</b></a>"
    )
    
    try:
        if item.get("image"):
            await bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=item["image"],
                caption=message,
                parse_mode=ParseMode.HTML
            )
        else:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logging.error(f"Failed to send Telegram message: {e}")

async def monitor_stock():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    init_db()
    logging.info("Checking all platforms for Hot Wheels stock & new releases...")

    try:
        available_items = await fetch_all_stores()
        
        for item in available_items:
            platform = item.get('platform', 'Store')
            prod_id = item.get('id', '')
            title = item.get('title', '')
            
            if not prod_id or not title:
                continue
                
            unique_key = f"{platform}_{prod_id}"
            
            # Checks SQLite database with 30-min TTL logic to prevent duplicate alerts
            if process_item_and_check_restock(unique_key, platform, title):
                logging.info(f"Stock/Restock detected: {title} on {platform}")
                await send_stock_alert(bot, item)
                
    except Exception as e:
        logging.error(f"Error during execution cycle: {e}")

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram credentials missing from environment variables!")
    else:
        try:
            # Force script to timeout safely after 30 seconds if any store connection hangs
            asyncio.run(asyncio.wait_for(monitor_stock(), timeout=30.0))
        except asyncio.TimeoutError:
            logging.error("❌ Execution timed out after 30 seconds and was safely aborted.")
