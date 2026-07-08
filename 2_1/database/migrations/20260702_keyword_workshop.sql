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
