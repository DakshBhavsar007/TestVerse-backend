import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://digital-foodordering-system.onrender.com/admin/login")
        await page.wait_for_timeout(3000)
        html = await page.content()
        with open("auth.html", "w", encoding="utf-8") as f:
            f.write(html)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
