import os
import asyncio
import logging
from telegram import Bot
from telegram.constants import ParseMode
from scraper import fetch_all_stores
from db import init_db, is_new_item, save_item

# --- CONFIGURATION (Loaded from Cloud Environment Variables) ---
TELEGRAM_BOT_TOKEN = os.getenv("9252825402:AAF4hD79TE1zvZEsEEHE0vj22zAp9amy0bQ")
TELEGRAM_CHAT_ID = os.getenv("1341566131")
CHECK_INTERVAL_SECONDS = 10  # Checks all platforms every 10 seconds

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def send_stock_alert(bot: Bot, item: dict):
    """Formats and sends a Telegram message with an image and buy link."""
    message = (
        f"🔥 <b>HOT WHEELS STOCK ALERT!</b> 🔥\n\n"
        f"🏎️ <b>Product:</b> {item['title']}\n"
        f"🏬 <b>Store:</b> {item['platform']}\n"
        f"💰 <b>Price:</b> ₹{item['price']} (MRP: ₹{item['mrp']})\n\n"
        f"🔗 <a href='{item['link']}'><b>Click here to Buy Now</b></a>"
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
    logging.info("Hot Wheels Inventory Monitor Started for Blinkit, Zepto, Swiggy Instamart, BigBasket, and FirstCry...")

    while True:
        try:
            logging.info("Checking all stores for Hot Wheels...")
            available_items = await fetch_all_stores()
            
            for item in available_items:
                unique_key = f"{item['platform']}_{item['id']}"
                
                # Check if this is a newly listed / restocked item
                if is_new_item(unique_key):
                    logging.info(f"New stock detected: {item['title']} on {item['platform']}")
                    await send_stock_alert(bot, item)
                    save_item(unique_key, item['platform'], item['title'])
                    
        except Exception as e:
            logging.error(f"Error during execution cycle: {e}")
            
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("⚠️ Please update your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in main.py before running!")
    else:
        asyncio.run(monitor_stock())