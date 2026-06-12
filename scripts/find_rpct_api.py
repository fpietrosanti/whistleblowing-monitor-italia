"""Intercept ANAC dati.anticorruzione.it API calls to find RPCT data endpoint."""
import asyncio
import re
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        all_responses = []

        def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            size = response.headers.get("content-length", "?")
            if not any(x in url for x in [".js", ".css", ".png", ".jpg", ".svg", ".woff", ".ico", "fonts."]):
                all_responses.append({
                    "url": url, "status": response.status,
                    "ct": ct[:60], "size": size,
                })

        page.on("response", on_response)

        print("1. Navigating to dati.anticorruzione.it/#/rpct...")
        await page.goto("https://dati.anticorruzione.it/#/rpct", wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(5000)

        print(f"   Page title: {await page.title()}")
        print(f"   Responses captured: {len(all_responses)}")
        for c in all_responses:
            print(f"   {c['status']} size={c['size']:>10s} {c['ct'][:40]:40s} {c['url'][:160]}")

        # Look for "Esporta" button
        print("\n2. Looking for Esporta button...")
        buttons = await page.query_selector_all("button, a, span")
        for btn in buttons:
            text = await btn.text_content()
            if text and "sport" in text.lower():
                print(f"   Found: '{text.strip()}'")

        # Try clicking export if available
        try:
            export_btn = await page.query_selector("text=Esporta")
            if export_btn:
                print("\n3. Clicking Esporta...")
                all_responses.clear()
                await export_btn.click()
                await page.wait_for_timeout(3000)
                print(f"   New responses: {len(all_responses)}")
                for c in all_responses:
                    print(f"   {c['status']} size={c['size']:>10s} {c['ct'][:40]:40s} {c['url'][:160]}")

                # Look for JSON export option
                json_btn = await page.query_selector("text=JSON")
                if not json_btn:
                    json_btn = await page.query_selector("text=Esporta JSON")
                if json_btn:
                    print("\n4. Clicking JSON export...")
                    all_responses.clear()
                    await json_btn.click()
                    await page.wait_for_timeout(5000)
                    print(f"   New responses: {len(all_responses)}")
                    for c in all_responses:
                        print(f"   {c['status']} size={c['size']:>10s} {c['ct'][:40]:40s} {c['url'][:160]}")
        except Exception as e:
            print(f"   Error: {e}")

        # Check page content for API patterns
        content = await page.content()
        api_patterns = re.findall(r'"(https?://[^"]+(?:api|rpct|data)[^"]*)"', content)
        if api_patterns:
            print(f"\n5. API URLs in page source:")
            for u in set(api_patterns):
                print(f"   {u[:180]}")

        # Check JS bundle for API base URL
        scripts = re.findall(r'src="([^"]+\.js[^"]*)"', content)
        print(f"\n6. JS bundles: {len(scripts)}")
        for s in scripts[:5]:
            print(f"   {s[:120]}")

        await browser.close()


asyncio.run(main())
