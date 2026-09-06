USE amazon_selection;

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
  status_changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近状态变更时间',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_research_project_market_name (marketplace, normalized_name),
  KEY idx_research_project_status_updated (status, updated_at),
  KEY idx_research_project_market_updated (marketplace, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='选品研究项目';

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
