import logging
import httpx
from bs4 import BeautifulSoup
import re
import json

async def fetch_all_stores():
    """
    Bulletproof scraper for FirstCry, BigBasket, Blinkit, Swiggy Instamart, and Zepto.
    Handles HTML DOM cards, embedded Next.js state data, direct JSON APIs, and mobile app headers.
    """
    all_in_stock = []
    
    # Authenticity headers with Geolocation for Chennai, India quick-commerce servicing
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,application/json,*/*;q=0.8",
        "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8",
        "Cache-Control": "no-cache",
        "x-location-context": "13.0827,80.2707", # Chennai Lat/Lng context
        "x-city-id": "1",
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        
        # ==========================================
        # 1. FIRSTCRY (Sort by Newness + Broad DOM Selector)
        # ==========================================
        try:
            logging.info("[FirstCry] Querying search page (sorted by newness)...")
            fc_url = "https://www.firstcry.com/search?q=hot%20wheels&sort=newness"
            res = await client.get(fc_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                
                # Check for embedded state JSON payload first
                script_tag = soup.find("script", id="__NEXT_DATA__")
                if script_tag and script_tag.string:
                    try:
                        js_data = json.loads(script_tag.string)
                        prods = js_data.get("props", {}).get("pageProps", {}).get("products", [])
                        for prod in prods:
                            title = prod.get("prodName", "")
                            if "hot wheels" in title.lower() and prod.get("isOutOfStock") is not True:
                                prod_url = prod.get("prodUrl", "")
                                link = prod_url if prod_url.startswith("http") else f"https://www.firstcry.com{prod_url}"
                                all_in_stock.append({
                                    "platform": "FirstCry",
                                    "id": str(prod.get("prodId", prod_url)),
                                    "title": title[:80],
                                    "price": f"₹{prod.get('price', 'Check App')}",
                                    "link": link
                                })
                    except Exception:
                        pass
                
                # HTML DOM Fallback parsing (catches McLaren W1, Audi RS6, Lotus Cortina, etc.)
                cards = soup.select(".prod-item, .list-block, div[class*='product'], div[class*='item'], li[class*='product'], a[href]")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    text_lower = text.lower()
                    
                    if "hot wheels" in text_lower and "out of stock" not in text_lower and "sold out" not in text_lower:
                        a_tag = card if card.name == 'a' else card.find('a', href=True)
                        if a_tag and a_tag.get('href'):
                            href = a_tag['href']
                            if any(x in href for x in ["/search?", "/s?", "#", "javascript:"]):
                                continue

                            link = href if href.startswith("http") else f"https://www.firstcry.com{href}"
                            
                            # Clean Unique ID extraction
                            prod_id_match = re.search(r'/p/([a-zA-Z0-9\-_]+)', link) or re.search(r'/([0-9]{5,10})', link)
                            prod_id = prod_id_match.group(1) if prod_id_match else link.split('?')[0].split('/')[-1]

                            if not prod_id or len(prod_id) < 3:
                                continue

                            price = "Check App"
                            for word in text.split():
                                if "₹" in word or "INR" in word:
                                    price = word
                                    break

                            all_in_stock.append({
                                "platform": "FirstCry",
                                "id": prod_id,
                                "title": text[:80],
                                "price": price,
                                "link": link
                            })
        except Exception as e:
            logging.error(f"[FirstCry] Error: {e}")

        # ==========================================
        # 2. BIGBASKET
        # ==========================================
        try:
            logging.info("[BigBasket] Querying catalog search...")
            bb_url = "https://www.bigbasket.com/search/?slug=hot%20wheels"
            res = await client.get(bb_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                products = soup.select("div[qa='productCard'], div[class*='ProductCard'], a[href*='/pd/']")
                for prod in products:
                    text = prod.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
                        a_tag = prod if prod.name == 'a' else prod.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"https://www.bigbasket.com{href}" if href.startswith('/') else href or bb_url
                        
                        prod_id_match = re.search(r'/pd/([0-9]+)', href)
                        prod_id = prod_id_match.group(1) if prod_id_match else "bb_" + href.split('/')[-2]

                        all_in_stock.append({
                            "platform": "BigBasket",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[BigBasket] Error: {e}")

        # ==========================================
        # 3. BLINKIT
        # ==========================================
        try:
            logging.info("[Blinkit] Querying search endpoint...")
            blinkit_url = "https://blinkit.com/s/?q=hot%20wheels"
            res = await client.get(blinkit_url, headers=headers)
            if res.status_code in [200, 202]:
                soup = BeautifulSoup(res.text, 'html.parser')
                items = soup.select("div[class*='Product'], a[href*='/prid/'], div[class*='item']")
                for item in items:
                    text = item.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
                        a_tag = item if item.name == 'a' else item.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"https://blinkit.com{href}" if href.startswith('/') else href or blinkit_url
                        
                        prod_id_match = re.search(r'/prid/([0-9]+)', href)
                        prod_id = prod_id_match.group(1) if prod_id_match else href.split('/')[-1]

                        all_in_stock.append({
                            "platform": "Blinkit",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[Blinkit] Error: {e}")

        # ==========================================
        # 4. SWIGGY INSTAMART
        # ==========================================
        try:
            logging.info("[Instamart] Querying store inventory...")
            instamart_url = "https://www.swiggy.com/instamart/search?query=hot%20wheels"
            res = await client.get(instamart_url, headers=headers)
            if res.status_code in [200, 202]:
                soup = BeautifulSoup(res.text, 'html.parser')
                cards = soup.select("div[class*='ItemCard'], a[href*='/item/'], div[class*='ProductCard']")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
                        a_tag = card if card.name == 'a' else card.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"https://www.swiggy.com{href}" if href.startswith('/') else href or instamart_url
                        
                        prod_id = href.split('/')[-1] if href else "instamart_hw"

                        all_in_stock.append({
                            "platform": "Instamart",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[Instamart] Error: {e}")

        # ==========================================
        # 5. ZEPTO
        # ==========================================
        try:
            logging.info("[Zepto] Querying catalog...")
            zepto_url = "https://www.zeptonow.com/search?query=hot%20wheels"
            res = await client.get(zepto_url, headers=headers)
            if res.status_code in [200, 202]:
                soup = BeautifulSoup(res.text, 'html.parser')
                cards = soup.select("a[href*='/pn/'], div[class*='productCard'], div[class*='ProductCard']")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
                        a_tag = card if card.name == 'a' else card.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"https://www.zeptonow.com{href}" if href.startswith('/') else href or zepto_url
                        
                        prod_id = href.split('/')[-1] if href else "zepto_hw"

                        all_in_stock.append({
                            "platform": "Zepto",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[Zepto] Error: {e}")

    # Deduplicate items in the same run to prevent multiple alerts for the same item
    unique_results = {}
    for item in all_in_stock:
        key = f"{item['platform']}_{item['id']}"
        if key not in unique_results:
            unique_results[key] = item

    return list(unique_results.values())
