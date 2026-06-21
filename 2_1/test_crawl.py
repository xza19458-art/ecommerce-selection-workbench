"""测试爬取功能"""

from pathlib import Path
from core.controller import AppController

# 创建控制器实例
controller = AppController()

# 测试参数
keyword = "toys"
pages = 10
save_path = Path("html") / f"{keyword}_{pages}.html"

# 确保保存文件夹存在
save_path.parent.mkdir(exist_ok=True)

print(f"开始测试爬取功能...")
print(f"关键词: {keyword}")
print(f"页数: {pages}")
print(f"保存路径: {save_path}")

try:
    # 爬取Amazon网页
    controller.crawl_amazon(f"https://www.amazon.com/s?k={keyword}", save_path, pages)
    print("✅ 爬取成功！")
    print(f"HTML文件已保存到: {save_path}")

    # 检查文件是否存在
    if save_path.exists():
        print(f"文件大小: {save_path.stat().st_size} 字节")
    else:
        print("❌ 文件未生成")

except Exception as e:
    print(f"❌ 爬取失败: {str(e)}")
    import traceback
    traceback.print_exc()

finally:
    # 停止浏览器
    controller.stop_browser()
    print("浏览器已停止")
