CREATE DATABASE IF NOT EXISTS amazon_selection
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE amazon_selection;

CREATE TABLE IF NOT EXISTS schema_migrations (
  migration_id VARCHAR(190) NOT NULL COMMENT '迁移文件标识',
  filename VARCHAR(255) NOT NULL COMMENT '正向迁移文件名',
  checksum CHAR(64) NOT NULL COMMENT '正向SQL文件SHA256',
  status VARCHAR(16) NOT NULL COMMENT 'running/applied/failed/rolled_back',
  execution_mode VARCHAR(16) NOT NULL COMMENT 'baseline/migration/rollback',
  rollback_checksum CHAR(64) NULL COMMENT '配套回滚SQL文件SHA256',
  execution_ms INT UNSIGNED NULL COMMENT '最近执行耗时毫秒',
  error_message TEXT NULL COMMENT '最近迁移失败原因',
  applied_at DATETIME NULL COMMENT '最近成功应用时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (migration_id),
  KEY idx_schema_migrations_status (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='应用数据库迁移台账';

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
  product_size VARCHAR(255) NULL COMMENT '尺寸/规格（搜索结果可见规格或标题尺寸，最佳努力采集）',
  date_first_available DATE NULL COMMENT 'Amazon Date First Available（最佳努力采集）',
  detail_collected_at DATETIME NULL COMMENT '最近一次有效详情页采集时间',
  detail_source_file VARCHAR(1024) NULL COMMENT '最近一次详情页HTML来源文件',
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

CREATE TABLE IF NOT EXISTS product_bsr_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'BSR快照ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  snapshot_at DATETIME NOT NULL COMMENT '采集时间',
  rank_value INT UNSIGNED NOT NULL COMMENT '热销榜排名',
  category_name VARCHAR(512) NOT NULL COMMENT '热销榜类目',
  category_url TEXT NULL COMMENT 'Amazon热销榜类目链接',
  is_primary TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否主类目排名',
  raw_text TEXT NULL COMMENT '原始热销榜文案',
  source_file VARCHAR(1024) NULL COMMENT '详情页HTML来源文件',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_bsr_snapshot_category (product_id, snapshot_at, category_name),
  KEY idx_product_bsr_time (product_id, snapshot_at),
  KEY idx_bsr_category_rank (category_name, rank_value),
  CONSTRAINT fk_product_bsr_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Amazon热销榜排名时间序列';

CREATE TABLE IF NOT EXISTS product_physical_specs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '商品规格ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  parent_asin VARCHAR(20) NULL COMMENT '父ASIN（页面明确提供时）',
  item_length_in DECIMAL(10,3) NULL COMMENT '商品长度（英寸）',
  item_width_in DECIMAL(10,3) NULL COMMENT '商品宽度（英寸）',
  item_height_in DECIMAL(10,3) NULL COMMENT '商品高度（英寸）',
  package_length_in DECIMAL(10,3) NULL COMMENT '包装长度（英寸）',
  package_width_in DECIMAL(10,3) NULL COMMENT '包装宽度（英寸）',
  package_height_in DECIMAL(10,3) NULL COMMENT '包装高度（英寸）',
  item_weight_oz DECIMAL(12,3) NULL COMMENT '商品重量（盎司）',
  package_weight_oz DECIMAL(12,3) NULL COMMENT '包装重量（盎司）',
  unit_count DECIMAL(12,3) NULL COMMENT '单位数量',
  model_number VARCHAR(255) NULL COMMENT '型号',
  raw_dimensions_json JSON NULL COMMENT '尺寸原始标签与文案',
  raw_weight_json JSON NULL COMMENT '重量原始标签与文案',
  source_file VARCHAR(1024) NULL COMMENT '详情页HTML来源文件',
  collected_at DATETIME NOT NULL COMMENT '采集时间',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_physical_specs_product (product_id),
  KEY idx_product_physical_specs_parent (parent_asin),
  CONSTRAINT fk_product_physical_specs_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品结构化物理规格';

CREATE TABLE IF NOT EXISTS product_offer_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '报价快照ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  snapshot_at DATETIME NOT NULL COMMENT '采集时间',
  current_price DECIMAL(12,2) NULL COMMENT '当前详情页价格',
  list_price DECIMAL(12,2) NULL COMMENT '划线价/标价',
  currency VARCHAR(8) NULL COMMENT '币种',
  discount_percent DECIMAL(7,2) NULL COMMENT '折扣百分比',
  coupon_text VARCHAR(512) NULL COMMENT '优惠券原始文案',
  availability_status VARCHAR(64) NULL COMMENT '库存状态',
  featured_offer_seller VARCHAR(255) NULL COMMENT 'Featured Offer卖家',
  ships_from VARCHAR(255) NULL COMMENT '发货方',
  fulfillment_channel VARCHAR(32) NULL COMMENT 'Amazon/FBA/FBM/unknown',
  is_prime TINYINT(1) NULL COMMENT '是否显示Prime',
  offer_count INT UNSIGNED NULL COMMENT '其他报价数量',
  badges_json JSON NULL COMMENT '页面徽标',
  image_count INT UNSIGNED NULL COMMENT '商品图片数量',
  video_count INT UNSIGNED NULL COMMENT '商品视频数量',
  bullet_count INT UNSIGNED NULL COMMENT '卖点条目数量',
  has_a_plus TINYINT(1) NULL COMMENT '是否检测到A+内容',
  rating_histogram_json JSON NULL COMMENT '星级占比',
  postal_code VARCHAR(32) NULL COMMENT '页面展示地址邮编',
  source_file VARCHAR(1024) NULL COMMENT '详情页HTML来源文件',
  raw_json JSON NULL COMMENT '报价与页面信号原始字段',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_offer_snapshot_time (product_id, snapshot_at),
  KEY idx_product_offer_time (product_id, snapshot_at),
  CONSTRAINT fk_product_offer_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品报价与履约时间序列';

CREATE TABLE IF NOT EXISTS product_variants (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '商品变体关系ID',
  source_product_id BIGINT UNSIGNED NULL COMMENT '发现该关系的商品ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT '站点',
  parent_asin VARCHAR(20) NOT NULL COMMENT '父ASIN或关系根ASIN',
  child_asin VARCHAR(20) NOT NULL COMMENT '子ASIN',
  attributes_json JSON NULL COMMENT '变体属性',
  product_url TEXT NULL COMMENT '子ASIN链接',
  is_selected TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否当前页面选中变体',
  first_seen_at DATETIME NOT NULL COMMENT '首次发现时间',
  last_seen_at DATETIME NOT NULL COMMENT '最近发现时间',
  source_file VARCHAR(1024) NULL COMMENT '详情页HTML来源文件',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_variant_relation (marketplace, parent_asin, child_asin),
  KEY idx_product_variant_child (marketplace, child_asin),
  CONSTRAINT fk_product_variants_source FOREIGN KEY (source_product_id) REFERENCES products(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Amazon父子ASIN与变体属性';

CREATE TABLE IF NOT EXISTS product_metric_inputs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '商品指标输入ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  period_start DATE NOT NULL COMMENT '统计周期开始',
  period_end DATE NOT NULL COMMENT '统计周期结束',
  source_type VARCHAR(32) NOT NULL DEFAULT 'manual' COMMENT 'manual/csv/sp_api/ads_api/third_party',
  source_label VARCHAR(255) NULL COMMENT '来源说明',
  sessions BIGINT UNSIGNED NULL COMMENT '会话数',
  page_views BIGINT UNSIGNED NULL COMMENT '页面浏览量',
  units_ordered BIGINT UNSIGNED NULL COMMENT '订购件数',
  orders BIGINT UNSIGNED NULL COMMENT '订单数',
  ordered_sales DECIMAL(18,2) NULL COMMENT '订购销售额',
  featured_offer_percentage DECIMAL(7,4) NULL COMMENT 'Featured Offer比例0-1',
  impressions BIGINT UNSIGNED NULL COMMENT '曝光量',
  clicks BIGINT UNSIGNED NULL COMMENT '点击量',
  cart_adds BIGINT UNSIGNED NULL COMMENT '加购量',
  purchases BIGINT UNSIGNED NULL COMMENT '购买量',
  ad_spend DECIMAL(18,2) NULL COMMENT '广告花费',
  ad_clicks BIGINT UNSIGNED NULL COMMENT '广告点击量',
  ad_orders BIGINT UNSIGNED NULL COMMENT '广告订单量',
  ad_sales DECIMAL(18,2) NULL COMMENT '广告销售额',
  total_sales DECIMAL(18,2) NULL COMMENT '总销售额',
  unit_purchase_cost DECIMAL(12,4) NULL COMMENT '单件采购成本',
  unit_shipping_cost DECIMAL(12,4) NULL COMMENT '单件头程/运输成本',
  unit_fba_fee DECIMAL(12,4) NULL COMMENT '单件FBA费用',
  unit_referral_fee DECIMAL(12,4) NULL COMMENT '单件佣金',
  unit_other_cost DECIMAL(12,4) NULL COMMENT '单件其他成本',
  assumed_cvr_low DECIMAL(8,6) NULL COMMENT '低情景转化率0-1',
  assumed_cvr_base DECIMAL(8,6) NULL COMMENT '中情景转化率0-1',
  assumed_cvr_high DECIMAL(8,6) NULL COMMENT '高情景转化率0-1',
  notes TEXT NULL COMMENT '备注',
  raw_json JSON NULL COMMENT '原始导入行',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_metric_period_source (product_id, period_start, period_end, source_type),
  KEY idx_product_metric_period (product_id, period_end),
  CONSTRAINT fk_product_metric_input_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='卖家人工或官方商品指标输入';

CREATE TABLE IF NOT EXISTS product_estimates (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '商品估算ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  as_of_date DATE NOT NULL COMMENT '估算基准日期',
  metric_key VARCHAR(64) NOT NULL COMMENT '估算指标键',
  value_low DECIMAL(20,6) NULL COMMENT '低值',
  value_base DECIMAL(20,6) NULL COMMENT '基准值',
  value_high DECIMAL(20,6) NULL COMMENT '高值',
  unit VARCHAR(32) NOT NULL COMMENT '单位',
  model_version VARCHAR(64) NOT NULL COMMENT '模型版本',
  confidence_score DECIMAL(7,2) NOT NULL DEFAULT 0 COMMENT '置信度0-100',
  confidence_level VARCHAR(16) NOT NULL COMMENT '低/中/高',
  method VARCHAR(128) NOT NULL COMMENT '计算方法',
  evidence_json JSON NULL COMMENT '使用证据与限制',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_estimate_version (product_id, as_of_date, metric_key, model_version),
  KEY idx_product_estimate_date (product_id, as_of_date),
  CONSTRAINT fk_product_estimate_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='版本化商品经验估算';

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

CREATE TABLE IF NOT EXISTS keyword_serp_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '关键词SERP聚合快照ID',
  keyword_id BIGINT UNSIGNED NOT NULL COMMENT '关键词ID',
  snapshot_at DATETIME NOT NULL COMMENT '采集时间',
  page_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '本批次页数',
  total_card_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '商品卡片总数',
  organic_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '非广告卡片数',
  sponsored_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '广告卡片数',
  unique_asin_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '唯一ASIN数',
  ad_density DECIMAL(7,4) NULL COMMENT '广告卡片占比0-1',
  price_p25 DECIMAL(12,2) NULL COMMENT '自然结果价格P25',
  price_median DECIMAL(12,2) NULL COMMENT '自然结果价格中位数',
  price_p75 DECIMAL(12,2) NULL COMMENT '自然结果价格P75',
  review_p25 DECIMAL(14,2) NULL COMMENT '自然结果评论数P25',
  review_median DECIMAL(14,2) NULL COMMENT '自然结果评论数中位数',
  review_p75 DECIMAL(14,2) NULL COMMENT '自然结果评论数P75',
  rating_median DECIMAL(5,2) NULL COMMENT '自然结果评分中位数',
  monthly_bought_median DECIMAL(14,2) NULL COMMENT '自然结果近月购买量中位数',
  demand_cr3 DECIMAL(7,4) NULL COMMENT '近月购买量前三集中度0-1',
  demand_cr10 DECIMAL(7,4) NULL COMMENT '近月购买量前十集中度0-1',
  data_coverage DECIMAL(7,4) NULL COMMENT '核心字段完整覆盖率0-1',
  raw_json JSON NULL COMMENT '聚合口径、页码和缺失统计',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_keyword_serp_snapshot_time (keyword_id, snapshot_at),
  KEY idx_keyword_serp_time (keyword_id, snapshot_at),
  CONSTRAINT fk_keyword_serp_keyword FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='关键词搜索结果市场聚合快照';

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

CREATE TABLE IF NOT EXISTS keyword_idea_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '关键词创意运行ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT '站点',
  seed_keywords_json JSON NOT NULL COMMENT '本轮种子词JSON',
  sources_json JSON NOT NULL COMMENT '本轮启用来源JSON',
  expansion_mode VARCHAR(32) NOT NULL DEFAULT 'suggest_alpha_num' COMMENT '扩展方式',
  status VARCHAR(32) NOT NULL DEFAULT 'running' COMMENT 'running/completed/error',
  total_found INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '原始候选数',
  total_saved INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '保存或合并候选数',
  warning_message TEXT NULL COMMENT '非阻塞警告',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  finished_at DATETIME NULL COMMENT '完成时间',
  PRIMARY KEY (id),
  KEY idx_keyword_idea_runs_created (created_at),
  KEY idx_keyword_idea_runs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='关键词创意工坊运行记录';

CREATE TABLE IF NOT EXISTS keyword_ideas (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '关键词创意ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT '站点',
  keyword VARCHAR(255) NOT NULL COMMENT '展示关键词',
  normalized_keyword VARCHAR(255) NOT NULL COMMENT '归一化关键词',
  status VARCHAR(32) NOT NULL DEFAULT 'candidate' COMMENT 'candidate/promoted/tracking/ignored',
  source_types VARCHAR(255) NOT NULL COMMENT '来源类型，逗号分隔',
  seed_keywords_json JSON NOT NULL COMMENT '发现该词的种子词JSON',
  evidence_json JSON NULL COMMENT '来源证据和评分信号JSON',
  idea_score DECIMAL(6,2) NOT NULL DEFAULT 0 COMMENT '早期创意分',
  confidence_score DECIMAL(6,2) NOT NULL DEFAULT 0 COMMENT '证据置信度',
  recommendation_level VARCHAR(32) NOT NULL DEFAULT '仅作灵感' COMMENT '中文推荐等级',
  reason TEXT NOT NULL COMMENT '中文评分原因',
  occurrence_count INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '合并发现次数',
  last_run_id BIGINT UNSIGNED NULL COMMENT '最近运行ID',
  promoted_keyword_id BIGINT UNSIGNED NULL COMMENT '已推广关键词ID',
  tracking_task_id BIGINT UNSIGNED NULL COMMENT '已创建追踪任务ID',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_keyword_idea_market_norm (marketplace, normalized_keyword),
  KEY idx_keyword_idea_status_score (status, idea_score),
  KEY idx_keyword_idea_updated (updated_at),
  KEY idx_keyword_idea_source (source_types),
  CONSTRAINT fk_keyword_ideas_last_run
    FOREIGN KEY (last_run_id) REFERENCES keyword_idea_runs(id)
    ON DELETE SET NULL,
  CONSTRAINT fk_keyword_ideas_keyword
    FOREIGN KEY (promoted_keyword_id) REFERENCES keywords(id)
    ON DELETE SET NULL,
  CONSTRAINT fk_keyword_ideas_tracking
    FOREIGN KEY (tracking_task_id) REFERENCES keyword_tracking_tasks(id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='关键词创意工坊候选池';

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

CREATE TABLE IF NOT EXISTS research_projects (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '研究项目ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT '站点',
  name VARCHAR(255) NOT NULL COMMENT '项目名称',
  normalized_name VARCHAR(255) NOT NULL COMMENT '归一化名称，用于去重',
  status VARCHAR(32) NOT NULL DEFAULT 'idea' COMMENT 'idea/collecting/validating/candidate/manual_review/approved/rejected',
  objective TEXT NULL COMMENT '研究目标',
  strategy VARCHAR(64) NULL COMMENT '研究策略或方向标签',
  decision_summary TEXT NULL COMMENT '人工最终结论或阶段结论',
  decided_at DATETIME NULL COMMENT '批准或拒绝时间',
  current_decision_report_version_id BIGINT UNSIGNED NULL COMMENT '当前终态所依据的不可变报告版本',
  status_changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近状态变更时间',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_research_project_market_name (marketplace, normalized_name),
  KEY idx_research_project_status_updated (status, updated_at),
  KEY idx_research_project_market_updated (marketplace, updated_at),
  KEY idx_research_project_current_report (current_decision_report_version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='选品研究项目';

CREATE TABLE IF NOT EXISTS research_project_report_versions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '不可变报告版本ID',
  project_id BIGINT UNSIGNED NOT NULL COMMENT '研究项目ID',
  version_no INT UNSIGNED NOT NULL COMMENT '项目内递增版本号',
  freeze_kind VARCHAR(16) NOT NULL COMMENT 'manual/decision',
  source_project_status VARCHAR(32) NOT NULL COMMENT '冻结前项目阶段',
  decision_status VARCHAR(32) NULL COMMENT '决策版本目标状态 approved/rejected',
  decision_summary TEXT NULL COMMENT '决策版本人工结论',
  evaluated_on DATE NOT NULL COMMENT '报告评估日期',
  schema_version VARCHAR(32) NOT NULL COMMENT '报告结构版本',
  method_version VARCHAR(64) NOT NULL COMMENT '报告方法版本',
  evidence_as_of DATETIME NULL COMMENT '报告最新证据时间',
  evidence_fingerprint CHAR(64) NOT NULL COMMENT '来源证据SHA256',
  report_fingerprint CHAR(64) NOT NULL COMMENT '报告SHA256（不含生成时间）',
  report_json JSON NOT NULL COMMENT '完整决策报告正文',
  report_json_sha256 CHAR(64) NOT NULL COMMENT '规范化报告JSON SHA256',
  report_markdown MEDIUMTEXT NOT NULL COMMENT '冻结时的版本化Markdown',
  report_markdown_sha256 CHAR(64) NOT NULL COMMENT 'Markdown UTF-8 SHA256',
  version_note VARCHAR(2000) NULL COMMENT '人工版本说明',
  idempotency_key VARCHAR(64) NOT NULL COMMENT '单次显式动作幂等键',
  frozen_at DATETIME(6) NOT NULL COMMENT '服务端冻结时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_research_report_project_version (project_id, version_no),
  UNIQUE KEY uk_research_report_project_idempotency (project_id, idempotency_key),
  KEY idx_research_report_project_frozen (project_id, frozen_at),
  KEY idx_research_report_project_kind (project_id, freeze_kind, frozen_at),
  KEY idx_research_report_fingerprint (project_id, report_fingerprint),
  CONSTRAINT chk_research_report_freeze_kind
    CHECK (freeze_kind IN ('manual', 'decision')),
  CONSTRAINT chk_research_report_decision_payload
    CHECK (
      (
        freeze_kind = 'manual'
        AND decision_status IS NULL
        AND decision_summary IS NULL
      )
      OR
      (
        freeze_kind = 'decision'
        AND decision_status IN ('approved', 'rejected')
        AND CHAR_LENGTH(TRIM(decision_summary)) >= 10
      )
    ),
  CONSTRAINT fk_research_report_version_project
    FOREIGN KEY (project_id) REFERENCES research_projects(id)
    ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='研究项目不可变决策报告版本';

ALTER TABLE research_projects
  ADD CONSTRAINT fk_research_project_current_report
    FOREIGN KEY (current_decision_report_version_id)
    REFERENCES research_project_report_versions(id)
    ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS research_project_observation_plans (
  project_id BIGINT UNSIGNED NOT NULL COMMENT '研究项目ID，一项目一份当前观察计划',
  status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active/paused',
  cadence_days SMALLINT UNSIGNED NOT NULL DEFAULT 14 COMMENT '人工复核周期天数',
  next_review_on DATE NOT NULL COMMENT '下一次人工复核日期，不是后台调度时间',
  plan_note VARCHAR(2000) NULL COMMENT '观察目标与人工边界说明',
  last_reviewed_at DATETIME(6) NULL COMMENT '最近一次人工确认完成复核的时间',
  last_review_evaluated_on DATE NULL COMMENT '最近人工复核所用报告评估日',
  last_review_evidence_fingerprint CHAR(64) NULL COMMENT '最近人工复核所见证据指纹',
  last_review_report_fingerprint CHAR(64) NULL COMMENT '最近人工复核所见报告指纹',
  last_review_note VARCHAR(2000) NULL COMMENT '最近一次人工复核摘要，非不可变审计档案',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
  PRIMARY KEY (project_id),
  KEY idx_research_observation_status_due (status, next_review_on),
  CONSTRAINT chk_research_observation_status
    CHECK (status IN ('active', 'paused')),
  CONSTRAINT chk_research_observation_cadence
    CHECK (cadence_days BETWEEN 3 AND 180),
  CONSTRAINT fk_research_observation_project
    FOREIGN KEY (project_id) REFERENCES research_projects(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='研究项目人工观察计划；不执行后台采集或自动决策';

CREATE TABLE IF NOT EXISTS research_project_products (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '项目商品关系ID',
  project_id BIGINT UNSIGNED NOT NULL COMMENT '研究项目ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品ID',
  role VARCHAR(32) NOT NULL DEFAULT 'candidate' COMMENT 'candidate/benchmark/competitor/reference',
  notes TEXT NULL COMMENT '商品在本项目中的备注',
  added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '加入时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_research_project_product (project_id, product_id),
  KEY idx_research_project_product_lookup (product_id, project_id),
  CONSTRAINT fk_research_project_products_project
    FOREIGN KEY (project_id) REFERENCES research_projects(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_research_project_products_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='研究项目关联商品';

CREATE TABLE IF NOT EXISTS research_project_keywords (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '项目关键词关系ID',
  project_id BIGINT UNSIGNED NOT NULL COMMENT '研究项目ID',
  keyword_id BIGINT UNSIGNED NOT NULL COMMENT '关键词ID',
  role VARCHAR(32) NOT NULL DEFAULT 'candidate' COMMENT 'seed/candidate/core/long_tail/reference',
  notes TEXT NULL COMMENT '关键词在本项目中的备注',
  added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '加入时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_research_project_keyword (project_id, keyword_id),
  KEY idx_research_project_keyword_lookup (keyword_id, project_id),
  CONSTRAINT fk_research_project_keywords_project
    FOREIGN KEY (project_id) REFERENCES research_projects(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_research_project_keywords_keyword
    FOREIGN KEY (keyword_id) REFERENCES keywords(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='研究项目关联关键词';

CREATE TABLE IF NOT EXISTS research_project_notes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '项目笔记ID',
  project_id BIGINT UNSIGNED NOT NULL COMMENT '研究项目ID',
  note_type VARCHAR(32) NOT NULL DEFAULT 'observation' COMMENT 'observation/opportunity/risk/decision',
  content TEXT NOT NULL COMMENT '人工记录内容',
  content_hash CHAR(64) NOT NULL COMMENT '归一化内容SHA256，用于防重复提交',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_research_project_note_hash (project_id, note_type, content_hash),
  KEY idx_research_project_note_created (project_id, created_at),
  CONSTRAINT fk_research_project_notes_project
    FOREIGN KEY (project_id) REFERENCES research_projects(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='研究项目人工笔记';

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

CREATE TABLE IF NOT EXISTS scoring_profiles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '领域评分模型ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT '适用站点',
  name VARCHAR(128) NOT NULL COMMENT '模型名称',
  normalized_name VARCHAR(128) NOT NULL COMMENT '归一化名称，用于同站点去重',
  description TEXT NULL COMMENT '领域目标与人工边界说明',
  status VARCHAR(16) NOT NULL DEFAULT 'draft' COMMENT 'draft/active/archived',
  current_version_id BIGINT UNSIGNED NULL COMMENT '当前使用的不可变参数版本',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_scoring_profile_market_name (marketplace, normalized_name),
  KEY idx_scoring_profile_status_updated (status, updated_at),
  KEY idx_scoring_profile_current_version (current_version_id),
  CONSTRAINT chk_scoring_profile_status CHECK (status IN ('draft', 'active', 'archived'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户领域评分模型身份';

CREATE TABLE IF NOT EXISTS scoring_profile_versions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '领域评分模型不可变版本ID',
  profile_id BIGINT UNSIGNED NOT NULL COMMENT '领域评分模型ID',
  version_no INT UNSIGNED NOT NULL COMMENT '模型内递增版本号',
  parent_model_version VARCHAR(64) NOT NULL COMMENT '继承的通用评分模型版本',
  config_schema_version VARCHAR(64) NOT NULL COMMENT '参数结构版本',
  scope_type VARCHAR(16) NOT NULL COMMENT 'marketplace/category/niche/hybrid',
  category_scope VARCHAR(512) NULL COMMENT 'Amazon类目范围；不通过标题自动推断',
  niche_id BIGINT UNSIGNED NULL COMMENT '可选市场利基ID',
  source_type VARCHAR(16) NOT NULL DEFAULT 'manual' COMMENT 'manual/trained',
  config_json JSON NOT NULL COMMENT '白名单权重、轴组合、阈值与证据政策',
  config_sha256 CHAR(64) NOT NULL COMMENT '规范化版本内容SHA256',
  change_note VARCHAR(1000) NULL COMMENT '人工版本说明',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '版本生成时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_scoring_profile_version_no (profile_id, version_no),
  UNIQUE KEY uk_scoring_profile_config_hash (profile_id, config_sha256),
  KEY idx_scoring_profile_version_created (profile_id, created_at),
  KEY idx_scoring_profile_version_niche (niche_id),
  CONSTRAINT chk_scoring_profile_scope_type CHECK (scope_type IN ('marketplace', 'category', 'niche', 'hybrid')),
  CONSTRAINT chk_scoring_profile_source_type CHECK (source_type IN ('manual', 'trained')),
  CONSTRAINT fk_scoring_profile_version_profile FOREIGN KEY (profile_id) REFERENCES scoring_profiles(id) ON DELETE CASCADE,
  CONSTRAINT fk_scoring_profile_version_niche FOREIGN KEY (niche_id) REFERENCES market_niches(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='领域评分模型不可变参数版本';

ALTER TABLE scoring_profiles
  ADD CONSTRAINT fk_scoring_profile_current_version
    FOREIGN KEY (current_version_id) REFERENCES scoring_profile_versions(id)
    ON DELETE RESTRICT;
