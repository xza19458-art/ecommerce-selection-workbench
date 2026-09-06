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
  ADD COLUMN current_decision_report_version_id BIGINT UNSIGNED NULL
    COMMENT '当前终态所依据的不可变报告版本' AFTER decided_at,
  ADD KEY idx_research_project_current_report (current_decision_report_version_id),
  ADD CONSTRAINT fk_research_project_current_report
    FOREIGN KEY (current_decision_report_version_id)
    REFERENCES research_project_report_versions(id)
    ON DELETE RESTRICT;

UPDATE research_projects rp
JOIN (
  SELECT project_id, decision_status, id
  FROM (
    SELECT
      project_id,
      decision_status,
      id,
      ROW_NUMBER() OVER (
        PARTITION BY project_id, decision_status
        ORDER BY version_no DESC, id DESC
      ) AS row_no
    FROM research_project_report_versions
    WHERE freeze_kind = 'decision'
  ) ranked_versions
  WHERE row_no = 1
) latest_version
  ON latest_version.project_id = rp.id
 AND latest_version.decision_status = rp.status
SET rp.current_decision_report_version_id = latest_version.id
WHERE rp.status IN ('approved', 'rejected')
  AND rp.current_decision_report_version_id IS NULL;
