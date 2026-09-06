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
  CONSTRAINT chk_scoring_profile_status
    CHECK (status IN ('draft', 'active', 'archived'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='用户领域评分模型身份';

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
  CONSTRAINT chk_scoring_profile_scope_type
    CHECK (scope_type IN ('marketplace', 'category', 'niche', 'hybrid')),
  CONSTRAINT chk_scoring_profile_source_type
    CHECK (source_type IN ('manual', 'trained')),
  CONSTRAINT fk_scoring_profile_version_profile
    FOREIGN KEY (profile_id) REFERENCES scoring_profiles(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_scoring_profile_version_niche
    FOREIGN KEY (niche_id) REFERENCES market_niches(id)
    ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='领域评分模型不可变参数版本';

ALTER TABLE scoring_profiles
  ADD CONSTRAINT fk_scoring_profile_current_version
    FOREIGN KEY (current_version_id) REFERENCES scoring_profile_versions(id)
    ON DELETE RESTRICT;
