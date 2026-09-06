USE amazon_selection;

CREATE TABLE IF NOT EXISTS market_niches (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '市场利基ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT '站点',
  name VARCHAR(255) NOT NULL COMMENT '利基名称',
  normalized_name VARCHAR(255) NOT NULL COMMENT '归一化名称，用于去重',
  status VARCHAR(32) NOT NULL DEFAULT 'draft' COMMENT 'draft/active/archived',
  definition TEXT NULL COMMENT '人工定义的包含/排除边界',
  category_scope VARCHAR(512) NULL COMMENT '人工类目范围提示',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_market_niche_market_name (marketplace, normalized_name),
  KEY idx_market_niche_status_updated (status, updated_at),
  KEY idx_market_niche_market_updated (marketplace, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='人工定义的市场与利基对象';

CREATE TABLE IF NOT EXISTS niche_keywords (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '利基关键词关系ID',
  niche_id BIGINT UNSIGNED NOT NULL COMMENT '市场利基ID',
  keyword_id BIGINT UNSIGNED NOT NULL COMMENT '关键词ID',
  role VARCHAR(32) NOT NULL DEFAULT 'core' COMMENT 'seed/core/long_tail/reference',
  notes TEXT NULL COMMENT '关键词在利基中的边界说明',
  added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '加入时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_niche_keyword (niche_id, keyword_id),
  KEY idx_niche_keyword_lookup (keyword_id, niche_id),
  CONSTRAINT fk_niche_keywords_niche FOREIGN KEY (niche_id) REFERENCES market_niches(id) ON DELETE CASCADE,
  CONSTRAINT fk_niche_keywords_keyword FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='利基成员关键词';

CREATE TABLE IF NOT EXISTS niche_products (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '利基人工商品关系ID',
  niche_id BIGINT UNSIGNED NOT NULL COMMENT '市场利基ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  role VARCHAR(32) NOT NULL DEFAULT 'benchmark' COMMENT 'candidate/benchmark/competitor/reference',
  notes TEXT NULL COMMENT '商品作为人工锚点的说明',
  added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '加入时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_niche_product (niche_id, product_id),
  KEY idx_niche_product_lookup (product_id, niche_id),
  CONSTRAINT fk_niche_products_niche FOREIGN KEY (niche_id) REFERENCES market_niches(id) ON DELETE CASCADE,
  CONSTRAINT fk_niche_products_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='利基人工候选与对标商品';

CREATE TABLE IF NOT EXISTS research_project_niches (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '研究项目利基关系ID',
  project_id BIGINT UNSIGNED NOT NULL COMMENT '研究项目ID',
  niche_id BIGINT UNSIGNED NOT NULL COMMENT '市场利基ID',
  role VARCHAR(32) NOT NULL DEFAULT 'candidate' COMMENT 'candidate/primary/reference',
  added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '加入时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_research_project_niche (project_id, niche_id),
  KEY idx_research_project_niche_lookup (niche_id, project_id),
  CONSTRAINT fk_research_project_niches_project FOREIGN KEY (project_id) REFERENCES research_projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_research_project_niches_niche FOREIGN KEY (niche_id) REFERENCES market_niches(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='研究项目关联市场利基';

CREATE TABLE IF NOT EXISTS niche_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '利基证据快照ID',
  niche_id BIGINT UNSIGNED NOT NULL COMMENT '市场利基ID',
  snapshot_at DATETIME NOT NULL COMMENT '快照生成时间',
  source_latest_at DATETIME NULL COMMENT '本次聚合使用的最新来源时间',
  evidence_hash CHAR(64) NOT NULL COMMENT '成员与来源快照哈希，用于幂等',
  keyword_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '成员关键词数',
  keyword_with_rank_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '有排名证据的成员关键词数',
  keyword_with_serp_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '有SERP聚合的成员关键词数',
  rank_coverage DECIMAL(7,4) NULL COMMENT '排名证据关键词覆盖率0-1',
  serp_coverage DECIMAL(7,4) NULL COMMENT 'SERP聚合关键词覆盖率0-1',
  serp_data_coverage DECIMAL(7,4) NULL COMMENT 'SERP核心字段加权覆盖率0-1',
  page_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '最新成员SERP页数合计',
  observed_product_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '跨关键词去重观察ASIN数',
  product_with_snapshot_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '有商品快照的观察ASIN数',
  product_snapshot_coverage DECIMAL(7,4) NULL COMMENT '观察ASIN商品快照覆盖率0-1',
  repeated_product_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '至少出现在两个成员关键词的ASIN数',
  cross_keyword_overlap DECIMAL(7,4) NULL COMMENT '跨关键词重复ASIN占比0-1',
  manual_product_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '人工关联锚点商品数，不进入市场样本',
  price_p25 DECIMAL(12,2) NULL COMMENT '去重观察ASIN最新价格P25',
  price_median DECIMAL(12,2) NULL COMMENT '去重观察ASIN最新价格中位数',
  price_p75 DECIMAL(12,2) NULL COMMENT '去重观察ASIN最新价格P75',
  review_p25 DECIMAL(14,2) NULL COMMENT '去重观察ASIN最新评论数P25',
  review_median DECIMAL(14,2) NULL COMMENT '去重观察ASIN最新评论数中位数',
  review_p75 DECIMAL(14,2) NULL COMMENT '去重观察ASIN最新评论数P75',
  rating_median DECIMAL(5,2) NULL COMMENT '去重观察ASIN最新评分中位数',
  monthly_bought_median DECIMAL(14,2) NULL COMMENT '有值ASIN近月购买量中位数',
  monthly_bought_total DECIMAL(18,2) NULL COMMENT '近月购买量下界代理合计，非真实销量',
  monthly_bought_coverage DECIMAL(7,4) NULL COMMENT '观察ASIN近月购买量覆盖率0-1',
  demand_cr3 DECIMAL(7,4) NULL COMMENT '有值ASIN近月购买量前三集中度0-1',
  demand_cr10 DECIMAL(7,4) NULL COMMENT '有值ASIN近月购买量前十集中度0-1',
  ad_density DECIMAL(7,4) NULL COMMENT '成员关键词最新SERP广告卡片加权占比0-1',
  brand_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '已知品牌数',
  brand_coverage DECIMAL(7,4) NULL COMMENT '观察ASIN品牌覆盖率0-1',
  brand_product_cr3 DECIMAL(7,4) NULL COMMENT '已知品牌样本中前三品牌商品占比0-1，非销售份额',
  raw_json JSON NULL COMMENT '聚合口径、来源摘要和证据警告',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_niche_snapshot_evidence (niche_id, evidence_hash),
  KEY idx_niche_snapshot_time (niche_id, snapshot_at),
  CONSTRAINT fk_niche_snapshots_niche FOREIGN KEY (niche_id) REFERENCES market_niches(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='市场利基按需证据快照';
