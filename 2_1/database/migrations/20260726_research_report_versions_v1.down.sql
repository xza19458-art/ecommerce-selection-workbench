ALTER TABLE research_projects
  DROP FOREIGN KEY fk_research_project_current_report,
  DROP INDEX idx_research_project_current_report,
  DROP COLUMN current_decision_report_version_id;

-- Intentionally retain research_project_report_versions and its project FK.
-- Historical audit bodies are append-only evidence and are not deleted by a
-- routine application rollback. Reapplying the forward migration restores
-- current terminal references where a unique latest matching version exists.
