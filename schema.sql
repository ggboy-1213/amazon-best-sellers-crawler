-- =====================================================================
-- Amazon Best Sellers 类目 Top 商品 ASIN 抓取 - MySQL 建表脚本
-- 目标库: amazon_bestsellers
-- 用法: mysql -uroot -p < schema.sql
-- =====================================================================

CREATE DATABASE IF NOT EXISTS `amazon_bestsellers`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `amazon_bestsellers`;

DROP TABLE IF EXISTS `category_top_products`;

CREATE TABLE `category_top_products` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `site`          VARCHAR(8)      NOT NULL DEFAULT 'US'   COMMENT '亚马逊站点: US=美国站(com), 其他站点为对应国家后缀',
  `node_id`       VARCHAR(20)     NOT NULL                COMMENT '类目Node ID, 即BSR榜单URL末尾/搜索页 rh=n: 后面的数字',
  `category_name` VARCHAR(255)    NOT NULL                COMMENT '类目名称, 如 Clothing / Dresses',
  `category_path` VARCHAR(1024)   DEFAULT NULL            COMMENT '类目完整路径, 如 Women''s Fashion > Clothing > Dresses',
  `is_leaf`       TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否叶子类目: 1=是(无下级子类目), 0=否(还有下级子类目)',
  `source`        VARCHAR(10)     NOT NULL                COMMENT '数据来源: BSR=Best Sellers榜单页(官方排名, 只有前100); SEARCH=类目搜索页 s?rh=n:{node_id}&fs=true (最多400, 结果顺序, 含少量赞助商品)',
  `top_rank`      INT UNSIGNED    NOT NULL                COMMENT '排名: source=BSR 时为榜单名次1-100; source=SEARCH 时为搜索结果位次1-400',
  `asin`          VARCHAR(10)     NOT NULL                COMMENT '商品ASIN (Amazon标准识别码, 10位)',
  `product_url`   VARCHAR(255)    DEFAULT NULL            COMMENT '商品链接: https://www.amazon.com/dp/{asin}',
  `crawl_time`    DATETIME        NOT NULL                COMMENT '抓取时间 (UTC)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_site_node_src_rank` (`site`, `node_id`, `source`, `top_rank`),
  KEY `idx_asin` (`asin`),
  KEY `idx_node` (`node_id`),
  KEY `idx_category_name` (`category_name`),
  KEY `idx_source` (`source`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = 'Amazon Best Sellers 类目Top商品表: 每个子类目存BSR榜单前100(source=BSR) + 搜索页前400(source=SEARCH)的ASIN';
