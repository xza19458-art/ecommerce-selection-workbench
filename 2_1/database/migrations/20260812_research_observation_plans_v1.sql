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
