#!/bin/bash
# 本机搜索页爬取监督循环
# 每轮: 从服务器拉最新类目树(state.json) -> 跑一批搜索页爬取 -> 检查是否全部完成
# 服务器 BSR 还在扩树, 所以要循环拉取, 直到两边都完事
cd "$(dirname "$0")"
KEY=~/.ssh/crawler_hk_01
SERVER=opsadmin@8.217.170.185

for i in $(seq 1 80); do
  scp -q -i "$KEY" "$SERVER:/opt/crawler/amazon-best-sellers-crawler/output/state.json" output/state.json
  echo "=== 第 $i 轮 $(date '+%m-%d %H:%M:%S') ===" >> search_run.log
  python -u crawler.py --search-only --no-export >> search_run.log 2>&1
  LAST=$(tail -8 search_run.log)
  SERVER_CRAWLING=$(ssh -i "$KEY" "$SERVER" 'pgrep -f "crawl[e]r.py" > /dev/null && echo yes || echo no' 2>/dev/null)
  echo "--- 本轮结束, 服务器BSR进行中: $SERVER_CRAWLING" >> search_run.log
  if echo "$LAST" | grep -q "待抓 0" && [ "$SERVER_CRAWLING" != "yes" ]; then
    echo "=== 全部类目搜索完成, 监督循环退出 ===" >> search_run.log
    break
  fi
  sleep 30
done
