# -*- coding: utf-8 -*-
"""诊断3: 本机 IP 限流是会话级还是 IP 级"""
import sys
import time
from playwright.sync_api import sync_playwright

BASE = "https://www.amazon.com"
U = BASE + "/s?rh=n:14130291011&fs=true&page=1"  # Smartwatches, 之前被拦的类目


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=False,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="en-US", timezone_id="America/New_York",
                            viewport={"width": 1366, "height": 900})
        pg = ctx.new_page()

        def probe(label):
            pg.goto(U, timeout=60000, wait_until="domcontentloaded")
            pg.wait_for_timeout(4000)
            html = pg.content()
            tiles = html.count('"s-search-result"')
            nores = "No results for your search" in html
            print(f"{label} | tiles: {tiles} | no-results页: {nores}", flush=True)

        pg.goto(BASE + "/", timeout=60000, wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)
        probe("① 当前会话直接搜: ")
        ctx.clear_cookies()
        pg.goto(BASE + "/", timeout=60000, wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)
        probe("② 清cookie+重预热: ")
        b.close()


if __name__ == "__main__":
    sys.exit(main())
