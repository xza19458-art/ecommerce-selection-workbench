CREATE DATABASE IF NOT EXISTS amazon_selection
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE amazon_selection;

CREATE TABLE IF NOT EXISTS products (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '商品ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT '站点',
  asin VARCHAR(20) NOT NULL COMMENT 'ASIN',
  title TEXT NOT NULL COMMENT '商品标题',
  title_zh TEXT NULL COMMENT 'Chinese product title translation',
  title_lang VARCHAR(16) NULL COMMENT 'Detected product title source language',
  title_translation_status VARCHAR(32) NULL COMMENT 'Product title translation status',
  title_translation_engine VARCHAR(64) NULL COMMENT 'Product title translation engine',
  title_translated_at DATETIME NULL COMMENT 'Product title translation time',
  brand VARCHAR(255) NULL COMMENT '品牌',
  category_path VARCHAR(1024) NULL COMMENT '类目路径',
  product_url TEXT NOT NULL COMMENT '商品链接',
  image_url TEXT NOT NULL COMMENT '主图链接',
  first_seen_at DATETIME NOT NULL COMMENT '首次采集时间',
  last_seen_at DATETIME NOT NULL COMMENT '最近采集时间',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_marketplace_asin (marketplace, asin)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品基础信息';

CREATE TABLE IF NOT EXISTS product_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '快照ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  snapshot_at DATETIME NOT NULL COMMENT '采集时间',
  price DECIMAL(10,2) NOT NULL COMMENT '价格',
  rating DECIMAL(3,2) NOT NULL COMMENT '评分',
  review_count INT UNSIGNED NOT NULL COMMENT '评论数',
  monthly_bought INT UNSIGNED NULL COMMENT '近月购买量（缺失=无徽标，NULL=未知；见 2026-06-24 裁定）',
  is_deal TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否促销',
  is_sponsored TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否广告',
  page_no INT UNSIGNED NULL COMMENT '搜索页码',
  organic_rank INT UNSIGNED NULL COMMENT '自然序位估算（非Amazon内部真实排名）',
  raw_json JSON NULL COMMENT '原始解析字段',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_snapshot_time (product_id, snapshot_at),
  KEY idx_snapshot_at (snapshot_at),
  CONSTRAINT fk_product_snapshots_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品时间序列快照';

CREATE TABLE IF NOT EXISTS product_reviews (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '评论ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  review_id VARCHAR(128) NULL COMMENT 'Amazon评论ID',
  content_hash CHAR(64) NOT NULL COMMENT '评论内容去重哈希',
  rating DECIMAL(3,2) NULL COMMENT '评论评分',
  title TEXT NULL COMMENT '评论标题',
  title_zh TEXT NULL COMMENT 'Chinese review title translation',
  body TEXT NULL COMMENT '评论正文',
  body_zh TEXT NULL COMMENT 'Chinese review body translation',
  review_lang VARCHAR(16) NULL COMMENT 'Detected review source language',
  review_translation_status VARCHAR(32) NULL COMMENT 'Review translation status',
  review_translation_engine VARCHAR(64) NULL COMMENT 'Review translation engine',
  review_translated_at DATETIME NULL COMMENT 'Review translation time',
  review_at DATETIME NULL COMMENT '评论时间',
  reviewer_name VARCHAR(255) NULL COMMENT '评论者名称',
  verified_purchase TINYINT(1) NULL COMMENT '是否验证购买',
  helpful_votes INT UNSIGNED NULL COMMENT '有用票数',
  variant_info VARCHAR(1024) NULL COMMENT '变体信息',
  source_url TEXT NULL COMMENT '评论来源链接',
  raw_json JSON NULL COMMENT '原始解析字段',
  collected_at DATETIME NOT NULL COMMENT '采集时间',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_review_hash (product_id, content_hash),
  KEY idx_product_review_rating (product_id, rating),
  KEY idx_review_collected_at (collected_at),
  CONSTRAINT fk_product_reviews_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品评论明细';

CREATE TABLE IF NOT EXISTS product_review_insights (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '评论洞察ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  insight_date DATE NOT NULL COMMENT '洞察日期',
  review_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '参与分析评论数',
  negative_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '低分评论数',
  avg_rating DECIMAL(3,2) NULL COMMENT '评论样本平均评分',
  pain_points_json JSON NULL COMMENT '痛点主题JSON',
  positive_points_json JSON NULL COMMENT '好评主题JSON',
  opportunity_summary TEXT NULL COMMENT '改良机会摘要',
  risk_summary TEXT NULL COMMENT '评论风险摘要',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_review_insight_date (product_id, insight_date),
  KEY idx_review_insight_date (insight_date),
  CONSTRAINT fk_product_review_insights_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品评论痛点洞察';

CREATE TABLE IF NOT EXISTS translation_cache (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Translation cache ID',
  source_hash CHAR(64) NOT NULL COMMENT 'SHA256 hash of source text',
  source_lang VARCHAR(16) NOT NULL COMMENT 'Source language',
  target_lang VARCHAR(16) NOT NULL COMMENT 'Target language',
  engine VARCHAR(64) NOT NULL COMMENT 'Translation engine',
  source_text MEDIUMTEXT NOT NULL COMMENT 'Original source text',
  translated_text MEDIUMTEXT NULL COMMENT 'Translated text',
  status VARCHAR(32) NOT NULL COMMENT 'Translation status',
  error_message TEXT NULL COMMENT 'Translation error message',
  translated_at DATETIME NULL COMMENT 'Translation time',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated time',
  PRIMARY KEY (id),
  UNIQUE KEY uk_translation_cache (source_hash, source_lang, target_lang, engine),
  KEY idx_translation_status (status),
  KEY idx_translation_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Translation result cache';

CREATE TABLE IF NOT EXISTS keywords (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '关键词ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT '站点',
  keyword VARCHAR(255) NOT NULL COMMENT '关键词',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_marketplace_keyword (marketplace, keyword)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='关键词';

CREATE TABLE IF NOT EXISTS keyword_rank_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '关键词排名快照ID',
  keyword_id BIGINT UNSIGNED NOT NULL COMMENT '关键词ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  snapshot_at DATETIME NOT NULL COMMENT '采集时间',
  page_no INT UNSIGNED NULL COMMENT '搜索页码',
  organic_rank INT UNSIGNED NULL COMMENT '自然序位估算（非Amazon内部真实排名）',
  is_sponsored TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否广告',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_keyword_product_snapshot (keyword_id, product_id, snapshot_at),
  KEY idx_keyword_snapshot (keyword_id, snapshot_at),
  CONSTRAINT fk_keyword_rank_snapshots_keyword
    FOREIGN KEY (keyword_id) REFERENCES keywords(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_keyword_rank_snapshots_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='关键词排名时间序列';

CREATE TABLE IF NOT EXISTS product_scores (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '评分ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  keyword_id BIGINT UNSIGNED NULL COMMENT '关键词ID',
  score_date DATE NOT NULL COMMENT '评分日期',
  total_score DECIMAL(6,2) NOT NULL COMMENT '综合得分',
  demand_score DECIMAL(6,2) NOT NULL COMMENT '需求得分',
  growth_score DECIMAL(6,2) NOT NULL COMMENT '增长得分',
  competition_score DECIMAL(6,2) NOT NULL COMMENT '竞争得分',
  rating_score DECIMAL(6,2) NOT NULL COMMENT '评分稳定得分',
  price_score DECIMAL(6,2) NOT NULL COMMENT '价格带得分',
  rank_score DECIMAL(6,2) NOT NULL COMMENT '自然序位得分',
  reason TEXT NOT NULL COMMENT '中文推荐理由',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_keyword_score_date (product_id, keyword_id, score_date),
  KEY idx_total_score (total_score),
  CONSTRAINT fk_product_scores_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_product_scores_keyword
    FOREIGN KEY (keyword_id) REFERENCES keywords(id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='选品评分结果';

CREATE TABLE IF NOT EXISTS keyword_tracking_tasks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '关键词追踪任务ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT '站点',
  keyword VARCHAR(255) NOT NULL COMMENT '追踪关键词',
  target_snapshots INT UNSIGNED NOT NULL DEFAULT 3 COMMENT '目标快照时间点数',
  status VARCHAR(32) NOT NULL DEFAULT 'active' COMMENT 'active/completed/paused/error',
  pages_per_keyword INT UNSIGNED NOT NULL DEFAULT 2 COMMENT '每轮采集页数',
  last_collected_at DATETIME NULL COMMENT '最近成功采集/入库时间',
  last_checked_at DATETIME NULL COMMENT '最近检查时间',
  achieved_snapshots INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '当前已达到快照时间点数',
  error_message TEXT NULL COMMENT '异常信息',
  active_keyword VARCHAR(255)
    GENERATED ALWAYS AS (CASE WHEN status = 'active' THEN keyword ELSE NULL END) STORED
    COMMENT 'active任务唯一约束生成列',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_keyword_tracking_active (marketplace, active_keyword),
  KEY idx_keyword_tracking_keyword (marketplace, keyword),
  KEY idx_keyword_tracking_status (status, updated_at),
  KEY idx_keyword_tracking_due (status, last_collected_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='关键词长期追踪任务';

CREATE TABLE IF NOT EXISTS crawl_jobs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '任务ID',
  keyword VARCHAR(255) NULL COMMENT '关键词',
  url TEXT NULL COMMENT '采集链接',
  pages INT UNSIGNED NULL COMMENT '采集页数',
  status VARCHAR(32) NOT NULL COMMENT '任务状态',
  started_at DATETIME NOT NULL COMMENT '开始时间',
  finished_at DATETIME NULL COMMENT '结束时间',
  total_found INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '解析商品数',
  total_valid INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '有效商品数',
  total_inserted INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '入库商品数',
  error_message TEXT NULL COMMENT '错误信息',
  PRIMARY KEY (id),
  KEY idx_started_at (started_at),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='采集任务日志';
