# -*- coding: utf-8 -*-
"""诊断2: 搜索封锁是 cookie 绑定还是 IP 绑定"""
import time
from playwright.sync_api import sync_playwright

PROXY = {
    "server": "http://l135.kdlfps.com:18866",
    "username": "f2558497901-region-US-period-5",
    "password": "j62i609a",
}
BASE = "https://www.amazon.com"
U1 = BASE + "/s?rh=n:1040660&fs=true&page=1"
U2 = BASE + "/s?rh=n:1040660&fs=true&page=2"


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, proxy=PROXY,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="en-US", timezone_id="America/New_York",
                            viewport={"width": 1366, "height": 900})
        pg = ctx.new_page()

        def warm():
            pg.goto(BASE + "/", timeout=60000, wait_until="domcontentloaded")
            pg.wait_for_timeout(5000)

        def probe(label, url):
            pg.goto(url, timeout=60000, wait_until="domcontentloaded")
            pg.wait_for_timeout(4000)
            html = pg.content()
            tiles = html.count('"s-search-result"')
            blocked = ("Sorry! Something went wrong" in pg.title()
                       or "unauthorized AI agent" in html)
            print(f"{label} | tiles: {tiles} | blocked: {blocked}", flush=True)

        warm()
        probe("p1 新会话首次搜索   ", U1)
        ctx.clear_cookies()
        warm()
        probe("p2 清cookie+重预热  ", U2)
        ctx.clear_cookies()
        warm()
        probe("p3 再来一次        ", U1)
        b.close()


if __name__ == "__main__":
    main()
