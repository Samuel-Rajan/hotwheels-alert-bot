import logging
import httpx
from bs4 import BeautifulSoup
import re

async def fetch_all_stores():
    """Fetches real-time Hot Wheels inventory using HTTP requests and BeautifulSoup HTML parsing."""
    all_in_stock = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        
        # --- 1. FIRSTCRY (HTML PARSING) ---
        try:
            logging.info("[FirstCry] Querying search page...")
            fc_url = "https://www.firstcry.com/search?q=hot%20wheels"
            res = await client.get(fc_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                # Find product cards / links
                cards = soup.select("div[class*='product'], div.l-box, li[class*='product'], a[href*='/p/']")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
                        a_tag = card if card.name == 'a' else card.find('a', href=True)
                        if a_tag and a_tag.get('href'):
                            href = a_tag['href']
                            link = href if href.startswith("http") else f"https://www.firstcry.com{href}"
                            
                            # Extract product ID from URL
                            prod_id_match = re.search(r'/p/([a-zA-Z0-9\-_]+)', link)
                            prod_id = prod_id_match.group(1) if prod_id_match else link
                            
                            # Extract price if visible
                            price = "Check App"
                            for word in text.split():
                                if "₹" in word or "INR" in word or any(char.isdigit() for char in word):
                                    if any(c.isdigit() for c in word) and len(word) < 8:
                                        price = word
                                        break

                            all_in_stock.append({
                                "platform": "FirstCry",
                                "id": prod_id,
                                "title": text[:80], # Take first 80 chars as clean title snippet
                                "price": price,
                                "link": link
                            })
        except Exception as e:
            logging.error(f"[FirstCry] Fetch error: {e}")

        # --- 2. BIGBASKET (HTML PARSING) ---
        try:
            logging.info("[BigBasket] Querying catalog search...")
            bb_url = "https://www.bigbasket.com/search/?slug=hot%20wheels"
            res = await client.get(bb_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                products = soup.select("div[qa='productCard'], div[class*='ProductCard']")
                for prod in products:
                    text = prod.get_text(" ", strip=True)
                    if "hot wheels" in text.lower():
                        a_tag = prod.find('a', href=True)
                        link = f"https://www.bigbasket.com{a_tag['href']}" if a_tag else bb_url
                        prod_id = a_tag['href'].split('/')[-2] if a_tag else "bb_hotwheels"
                        
                        all_in_stock.append({
                            "platform": "BigBasket",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[BigBasket] Fetch error: {e}")

        # Note: Blinkit, Instamart, and Zepto enforce strict WAF blocks on cloud IPs. 
        # FirstCry and BigBasket are your primary high-success inventory channels here!

    return all_in_stock
