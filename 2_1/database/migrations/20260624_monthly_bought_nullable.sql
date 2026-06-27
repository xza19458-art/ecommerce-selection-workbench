-- 2026-06-24 monthly_bought 必填口径裁定：近月购买量改为可空。
-- 很多正常 Amazon listing 本就没有 "X bought in past month" 徽标，缺失=未知（NULL），
-- 不再 100% 必填。与 STORAGE_REQUIRED_FIELDS 去掉 monthly_bought 对齐；评分侧
-- _score_demand 已对 None/0 返回需求分 0，安全。详见
-- decisions/2026-06-24-monthly_bought-必填口径裁定.md。
ALTER TABLE product_snapshots
  MODIFY monthly_bought INT UNSIGNED NULL COMMENT '近月购买量（缺失=无徽标，NULL=未知）';
