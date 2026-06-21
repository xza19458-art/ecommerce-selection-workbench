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
