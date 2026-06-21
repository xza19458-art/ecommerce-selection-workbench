import pandas as pd

# 读取CSV文件
df = pd.read_csv('amazon_raw.csv')

# 打印所有rank值（排除NaN）
ranks = df['rank'].dropna().astype(int).sort_values()
print("所有rank值:")
print(ranks.tolist())
print(f"\nrank值数量: {len(ranks)}")

# 检查特定范围
print("\n检查范围:")
print(f"3-6: {list(range(3,7))} -> {[r for r in ranks if 3 <= r <= 6]}")
print(f"10-57: {[r for r in ranks if 10 <= r <= 57]}")
print(f"59: {[r for r in ranks if r == 59]}")

# 检查轮播商品（rank为0或NaN）
carousel_products = df[df['rank'].isna() | (df['rank'] == 0)]
print(f"\n轮播商品数量: {len(carousel_products)}")
print("轮播商品ASIN:")
print(carousel_products['asin'].tolist())