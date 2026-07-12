USE amazon_selection;

-- 2026-07-09 商品尺寸/规格字段：搜索结果页可见规格、变体文案或标题尺寸最佳努力采集。
-- 该字段可空，不参与入库完整性拦截；后续如接入详情页包装尺寸/FBA 尺寸，应另行建更严格字段。
ALTER TABLE products
  ADD COLUMN product_size VARCHAR(255) NULL COMMENT '尺寸/规格（搜索结果可见规格或标题尺寸，最佳努力采集）' AFTER category_path;
