USE amazon_selection;

ALTER TABLE products
  ADD COLUMN title_zh TEXT NULL COMMENT 'Chinese product title translation' AFTER title,
  ADD COLUMN title_lang VARCHAR(16) NULL COMMENT 'Detected product title source language' AFTER title_zh,
  ADD COLUMN title_translation_status VARCHAR(32) NULL COMMENT 'Product title translation status' AFTER title_lang,
  ADD COLUMN title_translation_engine VARCHAR(64) NULL COMMENT 'Product title translation engine' AFTER title_translation_status,
  ADD COLUMN title_translated_at DATETIME NULL COMMENT 'Product title translation time' AFTER title_translation_engine;

ALTER TABLE product_reviews
  ADD COLUMN title_zh TEXT NULL COMMENT 'Chinese review title translation' AFTER title,
  ADD COLUMN body_zh TEXT NULL COMMENT 'Chinese review body translation' AFTER body,
  ADD COLUMN review_lang VARCHAR(16) NULL COMMENT 'Detected review source language' AFTER body_zh,
  ADD COLUMN review_translation_status VARCHAR(32) NULL COMMENT 'Review translation status' AFTER review_lang,
  ADD COLUMN review_translation_engine VARCHAR(64) NULL COMMENT 'Review translation engine' AFTER review_translation_status,
  ADD COLUMN review_translated_at DATETIME NULL COMMENT 'Review translation time' AFTER review_translation_engine;

CREATE TABLE IF NOT EXISTS translation_cache (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Translation cache ID',
  source_hash CHAR(64) NOT NULL COMMENT 'SHA256 hash of source text',
  source_lang VARCHAR(16) NOT NULL COMMENT 'Source language',
  target_lang VARCHAR(16) NOT NULL COMMENT 'Target language',
  engine VARCHAR(64) NOT NULL COMMENT 'Translation engine',
  source_text MEDIUMTEXT NOT NULL COMMENT 'Original source text',
  translated_text MEDIUMTEXT NULL COMMENT 'Translated text',
  status VARCHAR(32) NOT NULL COMMENT 'Translation status',
  error_message TEXT NULL COMMENT 'Translation error message',
  translated_at DATETIME NULL COMMENT 'Translation time',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated time',
  PRIMARY KEY (id),
  UNIQUE KEY uk_translation_cache (source_hash, source_lang, target_lang, engine),
  KEY idx_translation_status (status),
  KEY idx_translation_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Translation result cache';
