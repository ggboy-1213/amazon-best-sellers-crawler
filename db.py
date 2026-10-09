# -*- coding: utf-8 -*-
"""
MySQL 存储层 (连接配置见 .env 的 MYSQL_* 项)

库: amazon_bestsellers   表: category_top_products (建表脚本见 schema.sql)
写入策略: 按 (site, node_id, source) 先删后插, 事务保证幂等 —— 重复爬取不产生脏数据
"""
import pymysql

import config

MYSQL_CONF = dict(
    host=config.get("MYSQL_HOST", "127.0.0.1"),
    port=config.get_int("MYSQL_PORT", 3306),
    user=config.get("MYSQL_USER", "root"),
    password=config.get("MYSQL_PASSWORD", ""),
    database=config.get("MYSQL_DATABASE", "amazon_bestsellers"),
    charset=config.get("MYSQL_CHARSET", "utf8mb4"),
)

SITE = config.get("SITE", "US")


def connect() -> pymysql.connections.Connection:
    return pymysql.connect(**MYSQL_CONF, autocommit=False)


def save_products(conn, node_id: str, source: str, rows: list):
    """rows: [(rank, asin), ...] 按 rank 升序; 先删该类目该来源的旧行再插入"""
    if not rows:
        return
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM category_top_products WHERE site=%s AND node_id=%s AND source=%s",
            (SITE, node_id, source))
        cur.executemany(
            """INSERT INTO category_top_products
               (site, node_id, category_name, category_path, is_leaf, source, top_rank, asin, product_url, crawl_time)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())""",
            [(SITE, node_id, name, path, leaf, source, rank, asin,
              f"https://www.amazon.com/dp/{asin}") for rank, asin, name, path, leaf in rows])
    conn.commit()


def update_category_meta(conn, node_id: str, category_name: str, category_path: str, is_leaf: int):
    """补齐/刷新某类目所有行的类目元信息 (名称/路径/是否叶子)"""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE category_top_products
               SET category_name=%s, category_path=%s, is_leaf=%s
               WHERE site=%s AND node_id=%s""",
            (category_name, category_path, is_leaf, SITE, node_id))
    conn.commit()


def stats(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""SELECT source, COUNT(*), COUNT(DISTINCT node_id)
                       FROM category_top_products GROUP BY source""")
        by_source = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        cur.execute("SELECT COUNT(DISTINCT asin) FROM category_top_products")
        unique_asins = cur.fetchone()[0]
    return {"by_source": by_source, "unique_asins": unique_asins}


def fetch_all(conn):
    """导出用: 全表按类目路径+来源+排名排序"""
    with conn.cursor() as cur:
        cur.execute("""SELECT node_id, category_name, category_path, is_leaf, source,
                              top_rank, asin, product_url, crawl_time
                       FROM category_top_products
                       ORDER BY category_path, source, top_rank""")
        return cur.fetchall()
