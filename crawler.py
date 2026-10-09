# -*- coding: utf-8 -*-
"""
Amazon Best Sellers (Women's Fashion) 子类目商品爬虫 v2

两阶段抓取, 数据全部写入本地 MySQL (amazon_bestsellers.category_top_products):

  Phase 1  BSR Top100  (requests, 快)
    从根类目 7147440011 BFS 递归发现全部子类目, 每个类目抓 zgbs 榜单 pg=1/pg=2,
    解析 data-client-recs-list 得到官方 Best Sellers 排名 1-100。source='BSR'

  Phase 2  搜索页 Top400 (Playwright 有头 Chrome, 慢)
    每个类目抓类目搜索页 https://www.amazon.com/s?rh=n:{node_id}&fs=true&page=N,
    最多 400 个商品 (Amazon 搜索页上限)。source='SEARCH', top_rank=结果位次。
    Akamai 防护强, 必须真实浏览器 + 先访问首页预热建立会话。

断点续爬: output/state.json 记录 BSR 完成节点 + SEARCH 完成节点, 重跑自动跳过。
所有配置项 (站点/根类目/MySQL/限速等) 在 .env 文件中修改。

用法:
  python crawler.py                 # 全流程 (BSR 续爬 -> SEARCH 续爬 -> 导出CSV)
  python crawler.py --bsr-only      # 只跑 Phase 1
  python crawler.py --search-only   # 只跑 Phase 2
  python crawler.py --max-nodes 3   # 限制处理类目数 (试跑)
"""

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from urllib.parse import urlparse

import requests

import config

try:
    # Windows 下 Python 自带证书包可能缺根证书, 优先用系统证书库
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import db

BASE = config.get("AMAZON_BASE", "https://www.amazon.com")
ROOT_NODE = config.get("ROOT_NODE", "7147440011")
ROOT_NAME = config.get("ROOT_NAME", "Women's Fashion")
DEPT = config.get("DEPT", "fashion")
MAX_SEARCH_ITEMS = config.get_int("MAX_SEARCH_ITEMS", 400)
MAX_SEARCH_PAGES = config.get_int("MAX_SEARCH_PAGES", 12)  # 搜索页单页 ~48 个, 留余量

OUT_DIR = config.get("OUT_DIR", "output")
STATE_FILE = os.path.join(OUT_DIR, "state.json")
SEARCH_DONE_FILE = os.path.join(OUT_DIR, "search_done.json")  # 独立文件: BSR 和 SEARCH 可在不同机器跑
PAGES_FILE = os.path.join(OUT_DIR, "pages.jsonl")
CHROME_PROFILE = os.path.join(OUT_DIR, "chrome_profile")

HEADERS = {
    "User-Agent": config.get(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

CHILD_LINK_RE = re.compile(
    r'href="[^"]*/zgbs/' + DEPT + r'/(\d+)/[^"]*ref=zg_bs_nav_' + DEPT + r'_(\d+)_(\d+)[^"]*"'
    r'[^>]*>(.*?)</a>', re.S)
RECS_LIST_RE = re.compile(r'data-client-recs-list="([^"]+)"')
CHALLENGE_HINTS = ("bm-verify", "api-services-support@amazon.com",
                   "To discuss automated access", "Enter the characters you see below")

MAX_RETRY = config.get_int("MAX_RETRY", 4)
BSR_DELAY = config.get_delay_range("BSR_DELAY_MIN", "BSR_DELAY_MAX", (0.9, 1.7))
SEARCH_DELAY = config.get_delay_range("SEARCH_DELAY_MIN", "SEARCH_DELAY_MAX", (1.5, 3.0))

# 代理: 浏览器(搜索页)始终走代理; BSR(requests)默认直连, 设 BSR_PROXY=1 才走代理
PROXY_URL = config.get("PROXY_URL", "").strip()
BSR_USE_PROXY = config.get("BSR_PROXY", "0").strip().lower() in ("1", "true", "yes", "y")
SEARCH_FAIL_BREAKER = config.get_int("SEARCH_FAIL_BREAKER", 5)  # 连续失败N个类目则中止SEARCH阶段
# 本机是否跑搜索页阶段 (服务器上设 0, 搜索页由有干净 IP 的机器负责)
SEARCH_ENABLED = config.get("ENABLE_SEARCH", "1").strip().lower() in ("1", "true", "yes", "y")


def proxy_for_playwright():
    if not PROXY_URL:
        return None
    u = urlparse(PROXY_URL)
    conf = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        conf["username"] = u.username
    if u.password:
        conf["password"] = u.password
    return conf


def node_url(node_id: str, pg: int) -> str:
    return f"{BASE}/Best-Sellers/zgbs/{DEPT}/{node_id}/ref=zg_bs_pg_{pg}_{DEPT}?_encoding=UTF8&pg={pg}"


def search_url(node_id: str, pg: int) -> str:
    return f"{BASE}/s?rh=n:{node_id}&fs=true&page={pg}"


class Browser:
    """有头 Chrome (Playwright), 负责搜索页抓取; 持久化 profile 复用会话 cookie"""

    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        os.makedirs(CHROME_PROFILE, exist_ok=True)
        # CHROME_CHANNEL: chrome=系统版Chrome(Windows本地); 留空=Playwright自带Chromium(Linux服务器)
        channel = config.get("CHROME_CHANNEL", "").strip() or None
        launch_kwargs = dict(
            user_data_dir=os.path.abspath(CHROME_PROFILE),
            channel=channel,
            headless=False,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--window-position=320,180"],
        )
        pw_proxy = proxy_for_playwright()
        if pw_proxy:
            launch_kwargs["proxy"] = pw_proxy
            print(f"[browser] 搜索页走代理: {pw_proxy['server']}, 出口地区见 .env 用户名 region- 参数")
        self.ctx = self._pw.chromium.launch_persistent_context(**launch_kwargs)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self._warmed = False

    def _has_session(self) -> bool:
        names = {c["name"] for c in self.ctx.cookies(BASE)}
        return "session-token" in names and "ubid-main" in names

    def warm_up(self, force=False):
        """访问首页建立会话 cookie, 没有这一步搜索页会被 Akamai 拦截"""
        if self._warmed and not force:
            return
        if not force and self._has_session():
            self._warmed = True
            return
        print("    [browser] 预热: 打开首页建立会话...")
        for i in range(1, 4):
            try:
                self.page.goto(BASE + "/", timeout=60000, wait_until="domcontentloaded")
                self.page.wait_for_timeout(5000 + random.randint(0, 3000))
                self._warmed = True
                return
            except Exception as e:
                print(f"    [browser] 预热第{i}次失败: {str(e).splitlines()[0][:70]}")
                time.sleep(4 * i)
        raise RuntimeError("homepage warmup failed after 3 retries")

    def fetch_search(self, url: str, want_tiles=True) -> list:
        """打开搜索页, 返回按 DOM 顺序排列的 ASIN 列表; 被拦截时自动重试/再预热"""
        for attempt in range(1, 4):
            self.warm_up(force=(attempt > 1))
            try:
                self.page.goto(url, timeout=60000, wait_until="domcontentloaded")
                self.page.wait_for_timeout(1500 + random.randint(0, 800))
                # 快速预检: 已知拦截页直接判失败, 不等选择器超时
                early = self.page.content()
                if "unauthorized AI agent" in early:
                    raise RuntimeError("AI-AGENT-BLOCK")
                if "Sorry! Something went wrong" in self.page.title():
                    raise RuntimeError("503-BLOCK")
                if want_tiles:
                    self.page.wait_for_selector(
                        "div[data-component-type=s-search-result]", timeout=25000)
                self.page.wait_for_timeout(800 + random.randint(0, 900))
                title = self.page.title()
                if "Sorry" in title or "Robot" in title or "CAPTCHA" in title.upper():
                    raise RuntimeError(f"blocked (title={title[:40]})")
                if not want_tiles:
                    return []
                return self.page.evaluate(
                    """() => Array.from(
                           document.querySelectorAll('div[data-component-type="s-search-result"]'))
                           .map(d => d.getAttribute('data-asin')).filter(Boolean)""")
            except Exception as e:
                # 诊断页面类型: ai-agent拦截页 / bm-verify质询页 / 503页, 便于判断封锁原因
                try:
                    t = self.page.title()
                    h = self.page.content()
                    if "unauthorized AI agent" in h:
                        kind = "AI-AGENT-BLOCK"
                    elif "bm-verify" in h:
                        kind = "JS-CHALLENGE"
                    elif "Sorry! Something went wrong" in t:
                        kind = "503-BLOCK"
                    else:
                        kind = "unknown"
                    print(f"    [browser] 第{attempt}次失败: {kind} title={t[:35]!r}")
                except Exception:
                    print(f"    [browser] 第{attempt}次失败: {str(e).splitlines()[0][:70]}")
                time.sleep(5 * attempt + random.uniform(0, 3))
        raise RuntimeError(f"browser fetch failed after 3 attempts: {url[:80]}")

    def close(self):
        try:
            self.ctx.close()
        finally:
            self._pw.stop()


class Crawler:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        if BSR_USE_PROXY and PROXY_URL:
            self.session.proxies.update({"http": PROXY_URL, "https": PROXY_URL})
            print(f"[bsr] requests 走代理: {urlparse(PROXY_URL).hostname}")
        self.nodes = {}          # node_id -> BSR 记录 (含 children/name/parent)
        self.search_done = set()  # 已完成搜索页抓取的 node_id
        self.pending = []        # BFS 队列 [node_id, name, parent, depth]
        self.failed_bsr = []
        self.failed_search = []
        self.db = db.connect()

    # ---------- 状态 ----------
    def load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
            for line in state.get("done", []):
                rec = json.loads(line)
                self.nodes[rec["node_id"]] = rec
            self.pending = state.get("pending", [])
            # 兼容旧格式: state.json 里内嵌的 search_done 迁移到独立文件
            legacy = set(state.get("search_done", []))
            if legacy:
                self.search_done |= legacy
                self._save_search_done()
        if os.path.exists(SEARCH_DONE_FILE):
            with open(SEARCH_DONE_FILE, encoding="utf-8") as f:
                self.search_done |= set(json.load(f))
        print(f"[resume] BSR 已完成 {len(self.nodes)}, SEARCH 已完成 {len(self.search_done)}, "
              f"队列 {len(self.pending)}")

    def _save_search_done(self):
        tmp = SEARCH_DONE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(self.search_done), f)
        os.replace(tmp, SEARCH_DONE_FILE)

    def save_state(self):
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"done": [json.dumps(r, ensure_ascii=False) for r in self.nodes.values()],
                       "pending": self.pending}, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)

    # ---------- Phase 1: BSR ----------
    def fetch_bsr(self, url: str) -> str:
        for attempt in range(1, MAX_RETRY + 1):
            try:
                r = self.session.get(url, timeout=30)
            except requests.RequestException as e:
                print(f"    ! network error ({e.__class__.__name__}), retry {attempt}/{MAX_RETRY}")
                time.sleep(3 * attempt)
                continue
            if r.status_code == 200:
                if any(h in r.text for h in CHALLENGE_HINTS):
                    raise RuntimeError("challenge/captcha on zgbs, stop")
                return r.text
            print(f"    ! HTTP {r.status_code}, retry {attempt}/{MAX_RETRY}")
            time.sleep(2.5 * attempt)
        raise RuntimeError(f"failed after {MAX_RETRY} retries: {url[:80]}")

    @staticmethod
    def unescape(text: str) -> str:
        return (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'"))

    def parse_bsr(self, html: str):
        items = []
        m = RECS_LIST_RE.search(html)
        if m:
            for entry in json.loads(m.group(1).replace("&quot;", '"')):
                asin = entry.get("id", "")
                rank = entry.get("metadataMap", {}).get("render.zg.rank", "")
                if re.fullmatch(r"[A-Z0-9]{10}", asin):
                    items.append((int(rank) if str(rank).isdigit() else 0, asin))
        children = {}
        for cid, _depth, _parent, anchor in CHILD_LINK_RE.findall(html):
            name = re.sub(r"\s+", " ", self.unescape(re.sub(r"<[^>]+>", "", anchor))).strip()
            if name and cid not in children:
                children[cid] = name
        return items, children

    def crawl_bsr_node(self, node_id: str):
        html = self.fetch_bsr(node_url(node_id, 1))
        time.sleep(random.uniform(*BSR_DELAY))
        items1, children = self.parse_bsr(html)
        items2 = []
        if items1:
            html2 = self.fetch_bsr(node_url(node_id, 2))
            items2, _ = self.parse_bsr(html2)
            time.sleep(random.uniform(*BSR_DELAY))
        seen, asins = set(), []
        for rank, asin in sorted(items1 + items2):
            if asin not in seen:
                seen.add(asin)
                asins.append({"rank": rank, "asin": asin})
        return asins, children

    def run_bsr(self, max_nodes=None):
        if self.pending:
            queue = [tuple(t) for t in self.pending]
        else:
            queue = [(ROOT_NODE, ROOT_NAME, "", 1)]
        crawled = 0
        while queue:
            node_id, name, parent, depth = queue.pop(0)
            self.pending = [list(t) for t in queue]
            if node_id in self.nodes:
                rec = self.nodes[node_id]
                for child in rec["children"]:
                    cid = child["id"] if isinstance(child, dict) else child
                    if cid not in self.nodes:
                        queue.append((cid, "", node_id, depth + 1))
                continue
            print(f"[BSR {crawled + 1}] depth={depth} node={node_id} name={name!r}")
            try:
                asins, children = self.crawl_bsr_node(node_id)
            except RuntimeError as e:
                print(f"    ✗ 跳过: {e}")
                self.failed_bsr.append(node_id)
                self.save_state()
                continue
            rec = {"node_id": node_id, "name": name, "parent": parent, "depth": depth,
                   "children": [{"id": c, "name": n} for c, n in sorted(children.items())],
                   "asins": asins}
            self.nodes[node_id] = rec
            with open(PAGES_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._save_bsr_to_db(rec)
            for cid, cname in children.items():
                queue.append((cid, cname, node_id, depth + 1))
            self.save_state()
            print(f"    ✓ {len(asins)} ASINs, {len(children)} 子类目")
            crawled += 1
            if max_nodes and crawled >= max_nodes:
                print(f"--max-nodes {max_nodes} 达到, 提前结束 BSR 阶段")
                break
        self.pending = [list(t) for t in queue]
        self.save_state()
        print(f"BSR 阶段完成: 本次 {crawled}, 累计 {len(self.nodes)} 类目, 失败 {len(self.failed_bsr)}")

    def _save_bsr_to_db(self, rec):
        """BSR 记录写 MySQL; 同时补齐该类目已存在的 SEARCH 行的元信息"""
        rows = [(i["rank"], i["asin"], rec.get("name") or rec["node_id"],
                 self.path_of(rec["node_id"]), 0 if rec["children"] else 1)
                for i in rec["asins"]]
        db.save_products(self.db, rec["node_id"], "BSR", rows)
        db.update_category_meta(self.db, rec["node_id"], rec.get("name") or rec["node_id"],
                                self.path_of(rec["node_id"]), 0 if rec["children"] else 1)

    # ---------- Phase 2: 搜索页 Top400 ----------
    def run_search(self, browser: Browser, max_nodes=None):
        # 叶子类目优先 (数据价值最高), 其次按层级
        todo = [nid for nid in self.nodes if nid not in self.search_done]
        todo.sort(key=lambda nid: (0 if not self.nodes[nid]["children"] else 1,
                                   self.nodes[nid]["depth"], nid))
        print(f"SEARCH 阶段: 待抓 {len(todo)} 个类目 (共 {len(self.nodes)})")
        done = 0
        consecutive_fail = 0
        for idx, node_id in enumerate(todo, 1):
            rec = self.nodes[node_id]
            leaf = 0 if rec["children"] else 1
            print(f"[SEARCH {idx}/{len(todo)}] node={node_id} {rec.get('name')!r} "
                  f"({'leaf' if leaf else 'branch'})")
            try:
                rows, n_pages = self.crawl_search_node(browser, node_id, rec)
            except RuntimeError as e:
                print(f"    ✗ 失败: {e}")
                self.failed_search.append(node_id)
                self.save_state()
                consecutive_fail += 1
                if consecutive_fail >= SEARCH_FAIL_BREAKER:
                    print(f"!! 连续 {consecutive_fail} 个类目失败, 疑似代理/IP 被封, "
                          f"中止 SEARCH 阶段 (已完成的 {len(self.search_done)} 个不受影响, "
                          f"重跑可续)")
                    break
                continue
            consecutive_fail = 0
            db.save_products(self.db, node_id, "SEARCH",
                             [(r, a, rec.get("name") or node_id, self.path_of(node_id), leaf)
                              for r, a in rows])
            self.search_done.add(node_id)
            self._save_search_done()
            self.save_state()
            print(f"    ✓ {len(rows)} ASINs / {n_pages} 页")
            done += 1
            if max_nodes and done >= max_nodes:
                print(f"--max-nodes {max_nodes} 达到, 提前结束 SEARCH 阶段")
                break
            time.sleep(random.uniform(*SEARCH_DELAY))
        print(f"SEARCH 阶段完成: 本次 {done}, 累计 {len(self.search_done)}, 失败 {len(self.failed_search)}")

    def crawl_search_node(self, browser: Browser, node_id: str, rec: dict):
        seen, rows = set(), []
        pages_used = 0
        for pg in range(1, MAX_SEARCH_PAGES + 1):
            asins = browser.fetch_search(search_url(node_id, pg))
            pages_used = pg
            new = [a for a in asins if a not in seen]
            if not new:
                break
            seen.update(new)
            for a in new:
                if len(rows) < MAX_SEARCH_ITEMS:
                    rows.append((len(rows) + 1, a))
            if len(rows) >= MAX_SEARCH_ITEMS or len(asins) < 10:
                break
            time.sleep(random.uniform(*SEARCH_DELAY))
        return rows, pages_used

    # ---------- 工具 ----------
    def path_of(self, nid):
        chain = []
        cur = nid
        while cur:
            rec = self.nodes.get(cur, {})
            chain.append(rec.get("name") or cur)
            cur = rec.get("parent", "")
        return " > ".join(reversed(chain))

    def export_csv(self):
        rows = db.fetch_all(self.db)
        path = os.path.join(OUT_DIR, "top_products_export.csv")
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["node_id", "category_name", "category_path", "is_leaf",
                        "source", "top_rank", "asin", "product_url", "crawl_time"])
            w.writerows(rows)
        print(f"已导出 CSV: {path} ({len(rows)} 行)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bsr-only", action="store_true", help="只跑 BSR Top100 阶段")
    ap.add_argument("--search-only", action="store_true", help="只跑搜索页 Top400 阶段")
    ap.add_argument("--max-nodes", type=int, default=None, help="限制本次处理的类目数量")
    ap.add_argument("--no-export", action="store_true", help="结束时不导出 CSV")
    args = ap.parse_args()

    c = Crawler()
    c.load_state()

    # 断点恢复时把已有的 BSR 记录同步进 MySQL (幂等)
    if not args.search_only and c.nodes:
        print(f"同步 {len(c.nodes)} 条已有 BSR 记录到 MySQL...")
        for rec in c.nodes.values():
            c._save_bsr_to_db(rec)

    if not args.search_only:
        c.run_bsr(max_nodes=args.max_nodes)

    if not args.bsr_only:
        if not SEARCH_ENABLED:
            print("ENABLE_SEARCH=0, 跳过搜索页阶段 (本机只负责 BSR)")
        else:
            browser = Browser()
            try:
                browser.warm_up()
                c.run_search(browser, max_nodes=args.max_nodes)
            finally:
                browser.close()

    st = db.stats(c.db)
    print(f"MySQL 统计: {st}")
    if not args.no_export:
        c.export_csv()


if __name__ == "__main__":
    main()
