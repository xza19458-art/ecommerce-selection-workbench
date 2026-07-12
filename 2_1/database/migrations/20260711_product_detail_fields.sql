USE amazon_selection;

ALTER TABLE products
  ADD COLUMN date_first_available DATE NULL
    COMMENT 'Amazon Date First Available（最佳努力采集）' AFTER product_size,
  ADD COLUMN detail_collected_at DATETIME NULL
    COMMENT '最近一次有效详情页采集时间' AFTER date_first_available,
  ADD COLUMN detail_source_file VARCHAR(1024) NULL
    COMMENT '最近一次详情页HTML来源文件' AFTER detail_collected_at;

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
