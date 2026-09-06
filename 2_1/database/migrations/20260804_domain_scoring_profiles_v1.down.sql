ALTER TABLE scoring_profiles
  DROP FOREIGN KEY fk_scoring_profile_current_version;

DROP TABLE IF EXISTS scoring_profile_versions;

DROP TABLE IF EXISTS scoring_profiles;
