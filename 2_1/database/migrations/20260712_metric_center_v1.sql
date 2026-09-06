USE amazon_selection;

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
  CONSTRAINT fk_product_physical_specs_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
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
  CONSTRAINT fk_product_offer_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
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
  CONSTRAINT fk_product_variants_source
    FOREIGN KEY (source_product_id) REFERENCES products(id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Amazon父子ASIN与变体属性';

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
  CONSTRAINT fk_keyword_serp_keyword
    FOREIGN KEY (keyword_id) REFERENCES keywords(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='关键词搜索结果市场聚合快照';

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
  CONSTRAINT fk_product_metric_input_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
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
  CONSTRAINT fk_product_estimate_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='版本化商品经验估算';
