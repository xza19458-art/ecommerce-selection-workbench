"""选品建议文案：商品详情页的推荐结论 / 风险提示 / 进入策略。

从 GUI（`ui/main_window.py`）下沉而来，供 Tkinter GUI 与 Web API 共享（DRY），
判定逻辑与文案与原 GUI **保持一致**。纯函数：只读 product 字典与最新快照，
不写库、不联网、不改评分口径。
"""

from __future__ import annotations

from typing import Any


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    for ch in ("$", "%"):
        text = text.replace(ch, "")
    try:
        return float(text)
    except ValueError:
        return None


def selection_conclusion(product: dict, snapshots: list[dict]) -> str:
    """推荐结论：综合需求 / 竞争 / 评分给出中文判断。"""
    latest = snapshots[-1] if snapshots else {}
    review_count = _to_number(latest.get("review_count"))
    monthly_bought = _to_number(latest.get("monthly_bought"))
    rank = _to_number(latest.get("organic_rank"))
    rating = _to_number(latest.get("rating"))

    high_demand = (monthly_bought is not None and monthly_bought >= 10000) or (rank is not None and rank <= 10)
    high_competition = review_count is not None and review_count >= 5000
    good_rating = rating is not None and rating >= 4.3

    if high_demand and high_competition:
        return (
            "该商品需求强、排名靠前"
            f"{'、评分较好' if good_rating else ''}，但评论数很高，头部竞争压力较大。"
            "适合作为对标款和差异化分析对象，不建议直接同质化进入。"
        )
    if high_demand:
        return "该商品需求信号较强，建议继续观察趋势、评论痛点和利润空间，再决定是否进入。"
    if high_competition:
        return "该商品竞争壁垒较高，除非存在明显差异化机会，否则不建议优先进入。"
    return "该商品暂未显示出明显头部需求或强竞争信号，建议结合更多历史快照和评论痛点继续观察。"


def risk_text(product: dict, snapshots: list[dict]) -> str:
    """风险提示：评论壁垒 / 评分 / 价格带 / 综合得分等硬信号。"""
    latest = snapshots[-1] if snapshots else {}
    review_count = _to_number(latest.get("review_count"))
    monthly_bought = _to_number(latest.get("monthly_bought"))
    rank = _to_number(latest.get("organic_rank"))
    rating = _to_number(latest.get("rating"))
    price = _to_number(latest.get("price"))
    total_score = _to_number(product.get("total_score"))

    risks: list[str] = []
    if review_count is not None and review_count >= 10000:
        risks.append("评论壁垒很高，新品难以快速建立信任")
    elif review_count is not None and review_count >= 3000:
        risks.append("评论数偏高，需要明确差异化卖点")
    if monthly_bought is not None and monthly_bought >= 10000 and review_count is not None and review_count >= 5000:
        risks.append("需求强但头部竞争强，不适合直接复制")
    if rank is not None and rank <= 10:
        risks.append("自然排名靠前，说明该款已处于强曝光位置")
    if rating is not None and rating < 4.2:
        risks.append("评分偏低，可能存在质量或预期管理问题")
    if price is not None and price < 12:
        risks.append("价格偏低，利润和广告容错空间可能不足")
    if price is not None and price > 80:
        risks.append("价格偏高，需要验证转化率、退货和履约成本")
    if total_score is not None and total_score < 55:
        risks.append("综合得分偏低，现阶段不应作为优先进入对象")

    return "；".join(risks) if risks else "暂无明显硬风险，但仍需补充评论痛点、利润和供应链验证。"


def entry_strategy(product: dict, snapshots: list[dict]) -> str:
    """进入策略：根据需求 / 竞争 / 评分 / 价格 / 得分给出行动建议。"""
    latest = snapshots[-1] if snapshots else {}
    review_count = _to_number(latest.get("review_count"))
    monthly_bought = _to_number(latest.get("monthly_bought"))
    rank = _to_number(latest.get("organic_rank"))
    rating = _to_number(latest.get("rating"))
    price = _to_number(latest.get("price"))
    total_score = _to_number(product.get("total_score"))

    high_demand = (monthly_bought is not None and monthly_bought >= 10000) or (rank is not None and rank <= 10)
    high_competition = review_count is not None and review_count >= 5000

    if high_demand and high_competition:
        return (
            "对标分析优先：拆解差评、规格组合、材质/功能差异、包装和价格带，"
            "找到可验证差异后再小批量测试。"
        )
    if high_demand and not high_competition:
        return "可进入验证池：继续采集 3-5 个周期，确认需求稳定后核算利润和广告预算。"
    if rating is not None and rating < 4.2:
        return "先做痛点挖掘：重点分析差评原因，判断是否存在质量改良或预期管理机会。"
    if price is not None and price < 12:
        return "先做利润校验：低价品需确认采购、FBA、退货和广告后仍有毛利空间。"
    if total_score is not None and total_score >= 70:
        return "建议放入候选池：补充评论痛点和竞品对比后再判断是否立项。"
    return "建议继续观察：当前信号不足，先积累更多历史快照和关键词维度数据。"
