import logging
import httpx

async def fetch_all_stores():
    """Fetches real-time Hot Wheels inventory across all target Indian quick-commerce platforms using direct API requests."""
    all_in_stock = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        
        # --- 1. FIRSTCRY ---
        try:
            logging.info("[FirstCry] Querying search API...")
            fc_url = "https://www.firstcry.com/api/v1/search/products?q=hot%20wheels&sort=newness"
            res = await client.get(fc_url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                for prod in data.get("products", []):
                    if prod.get("inStock", True):  # Default true if status field is missing
                        title = prod.get("productName", "Hot Wheels Product")
                        if "hot wheels" in title.lower():
                            all_in_stock.append({
                                "platform": "FirstCry",
                                "id": str(prod.get("productId", prod.get("productUrl"))),
                                "title": title,
                                "price": str(prod.get("price", "Check App")),
                                "link": f"https://www.firstcry.com/p/{prod.get('productUrl')}" if not str(prod.get('productUrl')).startswith("http") else prod.get('productUrl')
                            })
        except Exception as e:
            logging.error(f"[FirstCry] API fetch error: {e}")

        # --- 2. BLINKIT ---
        try:
            logging.info("[Blinkit] Querying search endpoint...")
            # Blinkit public consumer catalog search route
            blinkit_url = "https://blinkit.com/v4/search/products?q=hot%20wheels"
            res = await client.get(blinkit_url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                # Traverse typical Blinkit nested response blocks
                products = data.get("data", {}).get("products", [])
                for prod in products:
                    title = prod.get("name", "")
                    if "hot wheels" in title.lower():
                        all_in_stock.append({
                            "platform": "Blinkit",
                            "id": str(prod.get("product_id")),
                            "title": title,
                            "price": f"₹{prod.get('price', 'Check App')}",
                            "link": f"https://blinkit.com/prid/{prod.get('product_id')}"
                        })
        except Exception as e:
            logging.error(f"[Blinkit] API fetch error: {e}")

        # --- 3. BIGBASKET (BBNow) ---
        try:
            logging.info("[BigBasket] Querying catalog search...")
            bb_url = "https://www.bigbasket.com/listing-product-search/?qt=hot%20wheels"
            res = await client.get(bb_url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                items = data.get("tab_info", [{}])[0].get("product_info", {}.get("products", []))
                for prod in items:
                    title = prod.get("desc", "")
                    if "hot wheels" in title.lower():
                        price = prod.get("pricing", {}).get("price", {}), "Check App"
                        all_in_stock.append({
                            "platform": "BigBasket",
                            "id": str(prod.get("id")),
                            "title": title,
                            "price": f"₹{price}",
                            "link": f"https://www.bigbasket.com/pd/{prod.get('id')}"
                        })
        except Exception as e:
            logging.error(f"[BigBasket] API fetch error: {e}")

        # --- 4. SWIGGY INSTAMART ---
        try:
            logging.info("[Instamart] Querying store inventory API...")
            # Instamart search routing endpoint
            instamart_url = "https://www.swiggy.com/api/instamart/search?query=hot%20wheels"
            res = await client.get(instamart_url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                widgets = data.get("data", {}.get("widgets", []))
                for widget in widgets:
                    for prod in widget.get("products", []):
                        title = prod.get("display_name", "")
                        if "hot wheels" in title.lower():
                            price = prod.get("price", {}).get("store_price", "Check App")
                            all_in_stock.append({
                                "platform": "Instamart",
                                "id": str(prod.get("id")),
                                "title": title,
                                "price": f"₹{price}",
                                "link": f"https://www.swiggy.com/instamart/item/{prod.get('seo_slug', prod.get('id'))}"
                            })
        except Exception as e:
            logging.error(f"[Instamart] API fetch error: {e}")

        # --- 5. ZEPTO ---
        try:
            logging.info("[Zepto] Querying inventory catalog...")
            zepto_url = "https://www.zeptonow.com/api/v1/search?query=hot%20wheels"
            res = await client.get(zepto_url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                results = data.get("results", []) or data.get("products", [])
                for prod in results:
                    title = prod.get("name", "")
                    if "hot wheels" in title.lower():
                        price = prod.get("mrp", "Check App")
                        all_in_stock.append({
                            "platform": "Zepto",
                            "id": str(prod.get("id", prod.get("product_id"))),
                            "title": title,
                            "price": f"₹{price}",
                            "link": f"https://www.zeptonow.com/pn/{prod.get('slug', prod.get('id'))}"
                        })
        except Exception as e:
            logging.error(f"[Zepto] API fetch error: {e}")

    return all_in_stock
