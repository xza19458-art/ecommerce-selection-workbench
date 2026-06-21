# -*- coding: utf-8 -*-
"""
Amazon 蓝海产品自动分析系统
"""

# 抑制libpng警告
import warnings
warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')

from bs4 import BeautifulSoup
import pandas as pd
import re
import matplotlib.pyplot as plt
import numpy as np


# ==============================
# 工具函数
# ==============================

def parse_count(text):
    if not text:
        return None
    text = text.replace(",", "").strip()
    if "K" in text:
        return int(float(text.replace("K", "")) * 1000)
    try:
        return int(text)
    except:
        return None


def clean_price(text):
    if not text:
        return None
    # 移除所有非数字和小数点的字符
    price_str = re.sub(r'[^\d.]', '', text)
    try:
        return float(price_str)
    except:
        return None


def extract_monthly_bought(item):
    """更准确地提取月销量"""
    # 尝试多种方式提取月销量
    patterns = [
        r'(\d+[\d\.,]*K?)\s*(bought|sold)\s*(in|last|this|past)\s*(30|thirty|month)?',
        r'(\d+[\d\.,]*K?)\s*(bought|sold)\s*(in|last)\s*(30|thirty|month)',
        r'(\d+[\d\.,]*K?)\s*(bought|sold)\s*(this|past)\s*month',
        r'(\d+[\d\.,]*K?)\s*(units|items)\s*(sold|bought)\s*(monthly|per\s*month)'
    ]

    # 搜索所有文本
    for string in item.stripped_strings:
        for pattern in patterns:
            match = re.search(pattern, string, re.IGNORECASE)
            if match:
                return parse_count(match.group(1))

    # 尝试查找特定的月销量标签
    bought_tags = item.select(".a-size-small.a-color-secondary")
    for tag in bought_tags:
        for pattern in patterns:
            match = re.search(pattern, tag.text, re.IGNORECASE)
            if match:
                return parse_count(match.group(1))

    # 直接查找包含bought的标签
    for tag in item.select(".a-size-base.a-color-secondary"):
        text = tag.text.strip()
        if "bought" in text:
            # 提取数字部分
            match = re.search(r'(\d+[\d\.,]*K?)', text)
            if match:
                return parse_count(match.group(1))

    return None


def calculate_blue_score(row):
    """优化的蓝海评分算法"""
    if pd.isna(row['monthly_bought']) or pd.isna(row['rating']) or pd.isna(row['review_count']):
        return None

    # 基础分数：销量 × 评分
    base_score = row['monthly_bought'] * row['rating']

    # 竞争因素：评论数越少，分数越高
    competition_factor = 1 / (np.log10(row['review_count'] + 10) + 1)

    # 价格因素：适中的价格更好
    if pd.notna(row['price']):
        # 价格在10-50之间为最佳，偏离这个范围会降低分数
        price_factor = 1.0
        if row['price'] < 10:
            price_factor = row['price'] / 10
        elif row['price'] > 50:
            price_factor = 50 / row['price']
    else:
        price_factor = 0.8  # 没有价格信息，降低分数

    # 排名因素：排名越靠前，分数越高
    if pd.notna(row['rank']):
        rank_factor = 1 / (np.log10(row['rank'] + 10) + 1)
    else:
        rank_factor = 0.8  # 没有排名信息，降低分数

    # 促销因素：有促销的产品加分
    deal_factor = 1.2 if row['is_deal'] == 1 else 1.0

    # 综合评分
    blue_score = base_score * competition_factor * price_factor * rank_factor * deal_factor
    return blue_score


def parse_amazon_page(html_path):
    """解析Amazon产品页面"""
    print(f"开始分析: {html_path}")

    # 从HTML路径中提取文件名（不含扩展名）
    import os
    import datetime
    base_name = os.path.splitext(os.path.basename(html_path))[0]

    # 获取运行开始的时间，精确到小时
    start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:00:00")

    # 读取HTML
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # 清理HTML，删除无用部分
    soup = BeautifulSoup(html_content, "lxml")

    # 移除脚本和样式标签
    for script in soup(['script', 'style']):
        script.decompose()

    # 移除广告和推荐部分
    for ad in soup.select('.s-sponsored-search-results'):
        ad.decompose()

    for rec in soup.select('.s-result-item.s-asin.AdHolder'):
        rec.decompose()

    # 更精确地选择商品项
    # 主搜索结果
    items = soup.select('div[data-component-type="s-search-result"]:not(.AdHolder)')

    # 额外的推荐商品（轮播中的商品）
    carousel_items = soup.select('.a-carousel-card div[data-asin]')
    items.extend(carousel_items)

    # 去重（根据data-asin）
    seen_asins = set()
    unique_items = []
    for item in items:
        asin = item.get('data-asin')
        if asin and asin not in seen_asins:
            seen_asins.add(asin)
            unique_items.append(item)

    items = unique_items

    data = []

    for item in items:
        try:
            # 提取ASIN（商品唯一ID）
            asin = item.get("data-asin")
            if not asin:
                continue

            # 提取标题
            title = item.select_one("h2 span")
            title = title.text.strip() if title else None

            # 提取商品链接
            link = item.select_one("h2 a")
            link = "https://www.amazon.com" + link["href"] if link else None

            # 提取评分
            rating_tag = item.select_one(".a-icon-alt")
            rating = float(rating_tag.text.split(" ")[0]) if rating_tag else None

            # 提取评论数
            review_tag = item.select_one("span.a-size-mini")
            review_count = parse_count(review_tag.text.strip("()")) if review_tag else None

            # 提取价格
            price_tag = item.select_one(".a-price .a-offscreen")
            price = clean_price(price_tag.text) if price_tag else None

            # 提取月销量（使用更准确的方法）
            monthly_bought = extract_monthly_bought(item)

            # 提取是否促销
            is_deal = 1 if item.select_one(".a-badge-text") else 0

            # 提取图片链接
            image_tag = item.select_one(".s-image")
            image_url = image_tag.get("src") if image_tag else None

            # 提取搜索排名
            rank = int(item.get("data-index")) if item.get("data-index") else None

            # 计算蓝海评分
            row_data = {
                "asin": asin,
                "title": title,
                "link": link,
                "rating": rating,
                "review_count": review_count,
                "price": price,
                "monthly_bought": monthly_bought,
                "is_deal": is_deal,
                "image_url": image_url,
                "rank": rank,
                "time": start_time
            }

            # 计算蓝海评分
            blue_score = calculate_blue_score(pd.Series(row_data))
            row_data["blue_score"] = blue_score

            data.append(list(row_data.values()))

        except Exception as e:
            # 更详细的错误处理
            print(f"处理商品时出错: {e}")
            continue

    # ==============================
    # 转 DataFrame
    # ==============================

    columns = [
        "asin", "title", "link", "rating", "review_count",
        "price", "monthly_bought", "is_deal", "image_url", "rank", "time", "blue_score"
    ]

    df = pd.DataFrame(data, columns=columns)

    # 数据清洗：移除重复项
    df = df.drop_duplicates(subset=["asin"])

    # 保存原始数据
    raw_csv = f"{base_name}_raw.csv"
    try:
        df.to_csv(raw_csv, index=False, encoding="utf-8-sig")
    except PermissionError:
        # 如果文件被占用，使用临时文件名
        import time
        timestamp = int(time.time())
        temp_filename = f"{base_name}_raw_{timestamp}.csv"
        df.to_csv(temp_filename, index=False, encoding="utf-8-sig")
        print(f"警告：文件被占用，已保存为 {temp_filename}")

    # ==============================
    # 蓝海筛选
    # ==============================

    blue_df = df[
        (df["monthly_bought"] > 1000) &
        (df["review_count"] < 500) &
        (df["rating"] >= 4.0) &
        (df["price"].between(10, 50))
    ]

    blue_df = blue_df.sort_values(by="blue_score", ascending=False)

    # 保存蓝海产品
    blue_csv = f"{base_name}_blue_products.csv"
    try:
        blue_df.to_csv(blue_csv, index=False, encoding="utf-8-sig")
    except PermissionError:
        # 如果文件被占用，使用临时文件名
        import time
        timestamp = int(time.time())
        temp_filename = f"{base_name}_blue_products_{timestamp}.csv"
        blue_df.to_csv(temp_filename, index=False, encoding="utf-8-sig")
        print(f"警告：文件被占用，已保存为 {temp_filename}")

    # ==============================
    # Excel输出
    # ==============================

    excel_file = f"{base_name}_analysis.xlsx"
    try:
        with pd.ExcelWriter(excel_file) as writer:
            df.to_excel(writer, sheet_name="全部数据", index=False)
            blue_df.to_excel(writer, sheet_name="蓝海产品", index=False)
    except PermissionError:
        # 如果文件被占用，使用临时文件名
        import time
        timestamp = int(time.time())
        temp_filename = f"{base_name}_analysis_{timestamp}.xlsx"
        with pd.ExcelWriter(temp_filename) as writer:
            df.to_excel(writer, sheet_name="全部数据", index=False)
            blue_df.to_excel(writer, sheet_name="蓝海产品", index=False)
        print(f"警告：文件被占用，已保存为 {temp_filename}")

    # ==============================
    # 可视化（必须用 matplotlib）
    # ==============================

    plt.figure(figsize=(12, 8))

    # 散点图：竞争 vs 需求
    plt.subplot(2, 2, 1)
    plt.scatter(df["review_count"], df["monthly_bought"], alpha=0.5)
    plt.xlabel("Review Count (Competition)")
    plt.ylabel("Monthly Bought (Demand)")
    plt.title("Competition vs Demand")

    # 散点图：价格 vs 月销量
    plt.subplot(2, 2, 2)
    plt.scatter(df["price"], df["monthly_bought"], alpha=0.5)
    plt.xlabel("Price")
    plt.ylabel("Monthly Bought")
    plt.title("Price vs Monthly Bought")

    # 散点图：排名 vs 月销量
    plt.subplot(2, 2, 3)
    plt.scatter(df["rank"], df["monthly_bought"], alpha=0.5)
    plt.xlabel("Rank")
    plt.ylabel("Monthly Bought")
    plt.title("Rank vs Monthly Bought")

    # 散点图：评分 vs 月销量
    plt.subplot(2, 2, 4)
    plt.scatter(df["rating"], df["monthly_bought"], alpha=0.5)
    plt.xlabel("Rating")
    plt.ylabel("Monthly Bought")
    plt.title("Rating vs Monthly Bought")

    plt.tight_layout()
    plot_file = f"{base_name}_analysis_plots.png"
    try:
        plt.savefig(plot_file)
    except PermissionError:
        # 如果文件被占用，使用临时文件名
        import time
        timestamp = int(time.time())
        temp_filename = f"{base_name}_analysis_plots_{timestamp}.png"
        plt.savefig(temp_filename)
        print(f"警告：文件被占用，已保存为 {temp_filename}")
    plt.close()

    print("✅ 完成！")
    print(f"总商品数: {len(df)}")
    print(f"蓝海产品数: {len(blue_df)}")
    print(f"数据字段: {list(df.columns)}")


if __name__ == "__main__":
    # 运行分析
    parse_amazon_page("amazon.html")