"""Tkinter 主界面：文件管理器风格，左侧文件夹选项，右侧文件内容。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from tkinter import messagebox
from pathlib import Path
from typing import Dict

from core.controller import AppController
from services.trend_analysis import assess_product_trend


class MainWindow(tk.Tk):
    """主窗口：文件管理器风格，左侧文件夹选项，右侧文件内容。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("Amazon 爬虫工具")
        self.minsize(800, 600)

        # 窗口居中
        self._center_window()

        self._controller = AppController()

        # 设置字体
        try:
            self.option_add("*Font", "{Microsoft YaHei UI} 10")
        except tk.TclError:
            pass
        try:
            self._font_title = tkfont.Font(
                self, family="Microsoft YaHei UI", size=12, weight="bold"
            )
        except tk.TclError:
            self._font_title = tkfont.Font(self, size=12, weight="bold")

        # 设置样式
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        # 顶部工具栏
        top = ttk.Frame(self, padding=(12, 10))
        top.pack(side=tk.TOP, fill=tk.X)

        # 开始按钮
        self._btn_start = ttk.Button(top, text="开始", width=10, command=self._start_process)
        self._btn_start.pack(side=tk.LEFT)

        # 返回上级按钮
        self._btn_back = ttk.Button(top, text="返回上级", width=10, command=self._go_back)
        self._btn_back.pack(side=tk.LEFT, padx=5)

        # 当前路径显示
        self._path_var = tk.StringVar()
        self._path_var.set("html")
        self._path_label = ttk.Label(top, textvariable=self._path_var, relief=tk.SUNKEN, padding=(5, 2))
        self._path_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)

        # 状态标签
        self._status = ttk.Label(self, text="就绪", anchor=tk.W, padding=(10, 6))
        self._status.pack(side=tk.BOTTOM, fill=tk.X)

        # 主体内容
        self._body = ttk.Frame(self)
        self._body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 左侧文件夹列表
        left_frame = ttk.Frame(self._body, width=200)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 5), pady=10)

        # 文件夹标题
        ttk.Label(left_frame, text="文件夹", font=self._font_title).pack(pady=(0, 10))

        # 文件夹列表
        self._folder_list = ttk.Treeview(left_frame, columns=["name"], show="tree")
        self._folder_list.pack(fill=tk.BOTH, expand=True)

        # 添加文件夹节点
        self._folder_list.insert("", tk.END, "html", text="html 文件夹", open=True)
        self._folder_list.insert("", tk.END, "data", text="数据结果文件夹", open=True)
        self._folder_list.insert("", tk.END, "logs", text="报错日志", open=True)

        # 绑定文件夹点击事件
        self._folder_list.bind("<<TreeviewSelect>>", self._on_folder_select)

        # 右侧内容区域
        right_frame = ttk.Frame(self._body)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 10), pady=10)

        # 右侧上部分：文件内容
        file_frame = ttk.Frame(right_frame)
        file_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 文件列表标题
        ttk.Label(file_frame, text="文件内容", font=self._font_title).pack(pady=(0, 10))

        # 文件列表
        columns = ("select", "name", "date", "type", "size")
        self._file_tree = ttk.Treeview(file_frame, columns=columns, show="headings")

        # 设置列
        self._file_tree.heading("select", text="选择")
        self._file_tree.heading("name", text="名称")
        self._file_tree.heading("date", text="修改日期")
        self._file_tree.heading("type", text="类型")
        self._file_tree.heading("size", text="大小")

        # 设置列宽
        self._file_tree.column("select", width=60, anchor=tk.CENTER)
        self._file_tree.column("name", width=300, anchor=tk.W)
        self._file_tree.column("date", width=120, anchor=tk.CENTER)
        self._file_tree.column("type", width=100, anchor=tk.CENTER)
        self._file_tree.column("size", width=80, anchor=tk.E)

        # 添加滚动条
        scrollbar = ttk.Scrollbar(file_frame, orient=tk.VERTICAL, command=self._file_tree.yview)
        self._file_tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._file_tree.pack(fill=tk.BOTH, expand=True)

        # 绑定双击事件用于选择/取消选择和打开文件
        self._file_tree.bind("<Double-1>", self._on_file_double_click)

        # 绑定右键菜单
        self._file_tree.bind("<Button-3>", self._show_context_menu)

        # 右侧下部分：扩展区域
        expand_frame = ttk.LabelFrame(right_frame, text="爬取设置", padding=10)
        expand_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=10)

        # 扩展区域内容
        expand_content = ttk.Frame(expand_frame)
        expand_content.pack(fill=tk.BOTH, expand=True)

        # 第一行：关键词输入
        keyword_frame = ttk.Frame(expand_content)
        keyword_frame.pack(fill=tk.X, pady=5)
        ttk.Label(keyword_frame, text="爬取关键词:", width=12).pack(side=tk.LEFT)
        self._keyword_var = tk.StringVar()
        ttk.Entry(keyword_frame, textvariable=self._keyword_var, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 第二行：爬取页数和保存地址
        settings_frame = ttk.Frame(expand_content)
        settings_frame.pack(fill=tk.X, pady=5)

        # 爬取页数
        pages_frame = ttk.Frame(settings_frame)
        pages_frame.pack(side=tk.LEFT, padx=5)
        ttk.Label(pages_frame, text="爬取页数:", width=12).pack(side=tk.LEFT)
        self._pages_var = tk.StringVar(value="1")
        ttk.Entry(pages_frame, textvariable=self._pages_var, width=5).pack(side=tk.LEFT)

        # 保存地址
        save_frame = ttk.Frame(settings_frame)
        save_frame.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Label(save_frame, text="保存地址:", width=12).pack(side=tk.LEFT)
        self._save_path_var = tk.StringVar(value="html")
        ttk.Entry(save_frame, textvariable=self._save_path_var, width=30).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(save_frame, text="浏览", width=8, command=self._browse_save_path).pack(side=tk.LEFT, padx=5)

        # 第三行：运行按钮
        run_frame = ttk.Frame(expand_content)
        run_frame.pack(fill=tk.X, pady=5)
        ttk.Button(run_frame, text="运行爬取", width=15, command=self._run_crawl).pack(side=tk.LEFT)
        ttk.Button(run_frame, text="入库预览", width=12, command=self._preview_database_candidates).pack(side=tk.LEFT, padx=5)
        ttk.Button(run_frame, text="写入数据库", width=12, command=self._import_selected_to_database).pack(side=tk.LEFT, padx=5)
        ttk.Button(run_frame, text="推荐榜单", width=12, command=self._show_recommendations).pack(side=tk.LEFT, padx=5)
        ttk.Button(run_frame, text="商品池", width=12, command=self._show_product_pool).pack(side=tk.LEFT, padx=5)

        # 第四行：扩展管理入口
        task_frame = ttk.Frame(expand_content)
        task_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(task_frame, text="任务中心", width=12, command=self._show_task_center).pack(side=tk.LEFT)
        ttk.Button(task_frame, text="关键词机会", width=12, command=self._show_keyword_opportunities).pack(side=tk.LEFT, padx=5)
        ttk.Button(task_frame, text="同步分析仓库", width=14, command=self._sync_analytics_warehouse).pack(side=tk.LEFT, padx=5)

        # 第五行：评论相关离线工具
        review_tool_frame = ttk.Frame(expand_content)
        review_tool_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(review_tool_frame, text="解析评论HTML", width=14, command=self._open_review_html_parse_window).pack(side=tk.LEFT)
        ttk.Button(review_tool_frame, text="导入评论", width=12, command=self._open_review_import_window).pack(side=tk.LEFT, padx=5)
        ttk.Button(review_tool_frame, text="评论洞察", width=12, command=self._show_review_insights).pack(side=tk.LEFT, padx=5)

        # 初始化右键菜单
        self._context_menu = tk.Menu(self, tearoff=0)
        self._context_menu.add_command(label="新建文件夹", command=self._create_new_folder)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="复制", command=self._copy_file)
        self._context_menu.add_command(label="剪切", command=self._cut_file)
        self._context_menu.add_command(label="粘贴", command=self._paste_file)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="重命名", command=self._rename_file)
        self._context_menu.add_command(label="删除", command=self._delete_file)

        # 初始化文件夹
        self._init_folders()

        # 绑定文件选择事件
        self._file_tree.bind("<<TreeviewSelect>>", self._on_file_select)

        # 复制的文件路径
        self._copied_file = None
        # 剪切的文件路径
        self._cut_file_path = None
        # 当前路径栈
        self._path_stack = ["html"]

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center_window(self, window=None, width=None, height=None) -> None:
        """将窗口居中显示

        Args:
            window: 要居中的窗口，默认为当前窗口
            width: 窗口宽度，默认为1024
            height: 窗口高度，默认为768
        """
        # 使用当前窗口如果没有指定
        if window is None:
            window = self
            window_width = 1024
            window_height = 768
        else:
            # 使用指定的宽度和高度
            window_width = width or 1024
            window_height = height or 768

        # 获取屏幕宽度和高度
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()

        # 计算窗口居中位置
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2

        # 设置窗口位置
        window.geometry(f"{window_width}x{window_height}+{x}+{y}")

    def _init_folders(self) -> None:
        """初始化文件夹结构"""
        # 创建html文件夹
        html_dir = Path("html")
        html_dir.mkdir(exist_ok=True)

        # 创建数据结果文件夹
        data_dir = Path("数据结果")
        data_dir.mkdir(exist_ok=True)

        # 加载文件列表
        self._load_files("html")

    def _load_files(self, folder_path: str) -> None:
        """加载指定文件夹的文件"""
        # 清空文件列表
        for item in self._file_tree.get_children():
            self._file_tree.delete(item)

        # 确定文件夹路径
        path = Path(folder_path)

        # 加载文件夹
        for subfolder in path.iterdir():
            if subfolder.is_dir():
                # 获取文件夹信息
                name = subfolder.name
                date = subfolder.stat().st_mtime
                import datetime
                date_str = datetime.datetime.fromtimestamp(date).strftime("%Y-%m-%d %H:%M")
                file_type = "文件夹"
                size = "-"

                # 添加到文件树，默认未选中
                self._file_tree.insert("", tk.END, values=("□", name, date_str, file_type, size))

        # 加载文件
        for file_path in path.iterdir():
            if file_path.is_file():
                # 获取文件信息
                name = file_path.name
                date = file_path.stat().st_mtime
                import datetime
                date_str = datetime.datetime.fromtimestamp(date).strftime("%Y-%m-%d %H:%M")
                file_type = file_path.suffix[1:] if file_path.suffix else "文件"
                size = file_path.stat().st_size
                size_str = f"{size} B" if size < 1024 else f"{size/1024:.1f} KB"

                # 添加到文件树，默认未选中
                self._file_tree.insert("", tk.END, values=("□", name, date_str, file_type, size_str))

    def _on_folder_select(self, event) -> None:
        """文件夹选择事件"""
        selected = self._folder_list.selection()
        if selected:
            folder = selected[0]
            if folder == "html":
                self._path_stack = ["html"]
                self._path_var.set("html")
                self._load_files("html")
            elif folder == "data":
                self._path_stack = ["数据结果"]
                self._path_var.set("数据结果")
                self._load_files("数据结果")
            elif folder == "logs":
                self._path_var.set("报错日志")
                self._load_logs()

    def _go_back(self) -> None:
        """返回上级文件夹"""
        if len(self._path_stack) > 1:
            # 弹出当前路径
            self._path_stack.pop()
            # 获取上级路径
            parent_path = "/".join(self._path_stack)
            # 更新路径显示
            self._path_var.set(parent_path)
            # 加载上级文件夹
            self._load_files(parent_path)

    def _load_logs(self) -> None:
        """加载并显示报错日志"""
        # 清空文件树
        for item in self._file_tree.get_children():
            self._file_tree.delete(item)

        # 检查debug.log文件是否存在
        log_file = Path("debug.log")
        if log_file.exists():
            try:
                # 读取日志文件
                with open(log_file, "r", encoding="utf-8") as f:
                    logs = f.readlines()

                # 显示日志内容
                for i, log in enumerate(logs, 1):
                    log = log.strip()
                    if log:
                        # 简化显示，只显示前100个字符
                        display_log = log[:100] + "..." if len(log) > 100 else log
                        # 添加到文件树
                        self._file_tree.insert("", tk.END, values=("", f"日志 {i}", "", "log", display_log))
            except Exception as e:
                self.set_status(f"读取日志文件失败: {str(e)}")
        else:
            # 显示提示信息
            self._file_tree.insert("", tk.END, values=("", "无报错日志", "", "info", "当前没有报错日志文件"))

    def _on_file_double_click(self, event) -> None:
        """文件双击事件，用于选择/取消选择、打开文件和进入文件夹"""
        # 获取点击的行和列
        item = self._file_tree.identify_row(event.y)
        column = self._file_tree.identify_column(event.x)

        if item:
            values = self._file_tree.item(item, "values")
            if values:
                # 检查是否是日志条目
                if values[1].startswith("日志 "):
                    # 显示完整日志内容
                    self._show_log_detail(values[1])
                # 如果点击的是选择列，切换选择状态
                elif column == "#1":  # 选择列
                    if values[0] == "□":
                        # 选中
                        new_values = list(values)
                        new_values[0] = "☑"
                        self._file_tree.item(item, values=new_values)

                        # 如果是文件夹，全选该文件夹下的所有html文件
                        if values[3] == "文件夹":
                            current_path = "/".join(self._path_stack)
                            folder_path = f"{current_path}/{values[1]}"

                            # 保存当前路径栈
                            original_stack = self._path_stack.copy()

                            try:
                                # 进入文件夹
                                self._path_stack.append(values[1])
                                # 加载文件夹内容
                                temp_items = []
                                folder = Path(folder_path)

                                # 收集所有html文件
                                for file_path in folder.iterdir():
                                    if file_path.is_file() and file_path.suffix == ".html":
                                        temp_items.append(file_path.name)

                                # 全选这些文件（在实际处理时会被添加到选中列表）
                                # 注意：这里我们只是记录需要选中的文件，实际的选中状态会在处理时使用
                                print(f"全选文件夹 {values[1]} 下的 {len(temp_items)} 个html文件")
                            finally:
                                # 返回到原路径
                                self._path_stack = original_stack
                    else:
                        # 取消选中
                        new_values = list(values)
                        new_values[0] = "□"
                        self._file_tree.item(item, values=new_values)
                else:  # 点击的是其他列
                    if values[3] == "文件夹":
                        # 进入文件夹
                        current_path = "/".join(self._path_stack)
                        new_path = f"{current_path}/{values[1]}"
                        self._path_stack.append(values[1])
                        self._path_var.set(new_path)
                        self._load_files(new_path)
                    else:
                        # 打开文件
                        self._open_file(values[1])

    def _open_file(self, filename: str) -> None:
        """使用默认程序打开文件"""
        import os
        import subprocess

        # 构建完整的文件路径
        current_path = "/".join(self._path_stack)
        file_path = os.path.join(current_path, filename)

        # 使用默认程序打开文件
        try:
            if os.name == 'nt':  # Windows
                os.startfile(file_path)
            else:  # macOS/Linux
                subprocess.run(['open', file_path] if os.name == 'posix' else ['xdg-open', file_path])
        except Exception as e:
            self.set_status(f"打开文件失败: {str(e)}")

    def _show_log_detail(self, log_name: str) -> None:
        """显示完整的日志内容"""
        # 提取日志编号
        import re
        match = re.search(r'日志 (\d+)', log_name)
        if not match:
            return

        log_index = int(match.group(1)) - 1

        # 读取日志文件
        log_file = Path("debug.log")
        if log_file.exists():
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    logs = f.readlines()

                if 0 <= log_index < len(logs):
                    log_content = logs[log_index].strip()

                    # 创建日志详情窗口
                    detail_window = tk.Toplevel(self)
                    detail_window.title("日志详情")
                    detail_window.geometry("600x400")
                    detail_window.transient(self)
                    detail_window.grab_set()

                    # 窗口居中
                    self._center_window(detail_window, 600, 400)

                    # 添加文本框显示日志内容
                    text_frame = ttk.Frame(detail_window, padding=10)
                    text_frame.pack(fill=tk.BOTH, expand=True)

                    text = tk.Text(text_frame, wrap=tk.WORD, font=("Consolas", 10))
                    text.pack(fill=tk.BOTH, expand=True)
                    text.insert(tk.END, log_content)
                    text.config(state=tk.DISABLED)

                    # 添加滚动条
                    scrollbar = ttk.Scrollbar(text, orient=tk.VERTICAL, command=text.yview)
                    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
                    text.configure(yscrollcommand=scrollbar.set)

                    # 添加关闭按钮
                    button_frame = ttk.Frame(detail_window, padding=10)
                    button_frame.pack(side=tk.BOTTOM, fill=tk.X)
                    ttk.Button(button_frame, text="关闭", width=10, command=detail_window.destroy).pack(side=tk.RIGHT)
                else:
                    self.set_status("日志索引超出范围")
            except Exception as e:
                self.set_status(f"读取日志文件失败: {str(e)}")

    def _run_crawl(self) -> None:
        """运行爬取"""
        keyword = self._keyword_var.get().strip()
        pages = self._pages_var.get().strip()
        save_path = self._save_path_var.get().strip()

        if not keyword:
            self.set_status("请输入爬取关键词")
            return

        if not pages.isdigit() or int(pages) < 1:
            self.set_status("请输入有效的爬取页数")
            return

        if not save_path:
            self.set_status("请选择保存地址")
            return

        # 确保保存文件夹存在
        import os
        if not os.path.exists(save_path):
            try:
                os.makedirs(save_path)
            except Exception as e:
                error_msg = f"创建保存文件夹失败: {str(e)}"
                self.set_status(error_msg)
                self._log_error(error_msg)
                return

        # 开始爬取
        self.set_status(f"开始爬取关键词: {keyword}, 页数: {pages}")

        # 使用线程执行爬取操作，避免UI阻塞
        import threading

        def crawl_thread():
            try:
                # 调用控制器爬取
                filename = f"{keyword.replace(' ', '_')}_{pages}.html"
                self._controller.crawl_amazon(
                    f"https://www.amazon.com/s?k={keyword}",
                    Path(save_path) / filename,
                    int(pages)
                )
                # 在主线程中更新UI
                self.after(0, lambda: self.set_status("爬取完成"))
                # 重新加载文件列表
                if save_path == "html":
                    self.after(0, lambda: self._load_files("html"))
            except Exception as e:
                error_msg = f"爬取失败: {str(e)}"
                # 在主线程中更新UI
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))
            finally:
                # 停止浏览器
                self._controller.stop_browser()

        # 启动线程
        thread = threading.Thread(target=crawl_thread)
        thread.daemon = True
        thread.start()

    def _log_error(self, message: str) -> None:
        """记录错误信息到日志文件"""
        import datetime
        import traceback

        # 过滤掉不重要的错误信息
        ignore_patterns = [
            "libpng warning",  # libpng警告
            "DeprecationWarning",  # 弃用警告
            "FutureWarning",  # 未来警告
            "UserWarning",  # 用户警告
            "RuntimeWarning"  # 运行时警告
        ]

        # 检查是否需要忽略该错误
        for pattern in ignore_patterns:
            if pattern in message:
                return

        # 构建日志内容
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] ERROR: {message}"

        # 获取完整的错误堆栈
        stack_trace = traceback.format_exc()
        if stack_trace:
            # 过滤堆栈中的不重要信息
            filtered_stack = []
            for line in stack_trace.split('\n'):
                skip = False
                for pattern in ignore_patterns:
                    if pattern in line:
                        skip = True
                        break
                if not skip:
                    filtered_stack.append(line)
            filtered_stack_trace = '\n'.join(filtered_stack)
            if filtered_stack_trace:
                log_entry += f"\n{filtered_stack_trace}"

        # 写入日志文件
        try:
            with open("debug.log", "a", encoding="utf-8") as f:
                f.write(log_entry + "\n\n")
        except Exception as e:
            # 即使写入日志失败，也不要影响主程序运行
            print(f"写入日志文件失败: {str(e)}")

    def _browse_save_path(self) -> None:
        """浏览保存地址"""
        import tkinter.filedialog as fd
        import os

        # 打开文件夹选择对话框
        folder = fd.askdirectory(title="选择保存文件夹")
        if folder:
            # 转换为相对路径（如果在当前目录下）
            current_dir = os.getcwd()
            if folder.startswith(current_dir):
                relative_path = os.path.relpath(folder, current_dir)
                self._save_path_var.set(relative_path)
            else:
                self._save_path_var.set(folder)

    def _show_context_menu(self, event) -> None:
        """显示右键菜单"""
        # 定位到点击的项目
        item = self._file_tree.identify_row(event.y)
        if item:
            # 选择点击的项目
            self._file_tree.selection_set(item)
            # 显示上下文菜单
            self._context_menu.post(event.x_root, event.y_root)
        else:
            # 如果点击的是空区域，也显示菜单（用于新建文件夹）
            self._context_menu.post(event.x_root, event.y_root)

    def _create_new_folder(self) -> None:
        """新建文件夹"""
        import os

        # 确定当前路径
        current_path = "/".join(self._path_stack)

        # 创建新建文件夹对话框
        dialog = tk.Toplevel(self)
        dialog.title("新建文件夹")
        dialog.geometry("300x100")
        dialog.transient(self)
        dialog.grab_set()

        # 对话框居中
        self._center_dialog(dialog, 300, 100)

        # 添加标签和输入框
        ttk.Label(dialog, text="文件夹名称:").pack(pady=10)
        entry = ttk.Entry(dialog, width=30)
        entry.insert(0, "新建文件夹")
        entry.pack(pady=5)

        # 添加按钮
        def on_ok():
            folder_name = entry.get().strip()
            if folder_name:
                folder_path = os.path.join(current_path, folder_name)
                if not os.path.exists(folder_path):
                    try:
                        os.makedirs(folder_path)
                        self.set_status(f"新建文件夹成功: {folder_name}")
                        # 重新加载文件列表
                        self._load_files(current_path)
                        dialog.destroy()
                    except Exception as e:
                        self.set_status(f"新建文件夹失败: {str(e)}")
                else:
                    self.set_status("文件夹已存在")
            else:
                self.set_status("文件夹名称不能为空")

        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=10)
        ttk.Button(button_frame, text="确定", command=on_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

        # 绑定回车键
        dialog.bind("<Return>", lambda event: on_ok())

    def _center_dialog(self, dialog: tk.Toplevel, width: int, height: int) -> None:
        """将对话框居中显示"""
        # 获取屏幕宽度和高度
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        # 计算对话框居中位置
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2

        # 设置对话框位置
        dialog.geometry(f"{width}x{height}+{x}+{y}")

    def _copy_file(self) -> None:
        """复制文件"""
        import os

        # 获取选中的文件
        selected_items = self._file_tree.selection()
        if not selected_items:
            self.set_status("请选择要复制的文件")
            return

        # 确定当前路径
        current_path = "/".join(self._path_stack)

        # 获取第一个选中的文件
        item = selected_items[0]
        values = self._file_tree.item(item, "values")
        if values:
            filename = values[1]
            self._copied_file = os.path.join(current_path, filename)
            self._cut_file_path = None  # 清除剪切状态
            self.set_status(f"已复制文件: {filename}")

    def _cut_file(self) -> None:
        """剪切文件"""
        import os

        # 获取选中的文件
        selected_items = self._file_tree.selection()
        if not selected_items:
            self.set_status("请选择要剪切的文件")
            return

        # 确定当前路径
        current_path = "/".join(self._path_stack)

        # 获取第一个选中的文件
        item = selected_items[0]
        values = self._file_tree.item(item, "values")
        if values:
            filename = values[1]
            self._cut_file_path = os.path.join(current_path, filename)
            self._copied_file = None  # 清除复制状态
            self.set_status(f"已剪切文件: {filename}")

    def _paste_file(self) -> None:
        """粘贴文件"""
        import os
        import shutil

        if not self._copied_file and not self._cut_file_path:
            self.set_status("没有可粘贴的文件")
            return

        # 确定当前路径
        current_path = "/".join(self._path_stack)

        # 确定源文件路径
        if self._cut_file_path:
            source_path = self._cut_file_path
            is_cut = True
        else:
            source_path = self._copied_file
            is_cut = False

        # 生成目标文件名
        filename = os.path.basename(source_path)
        target_path = os.path.join(current_path, filename)

        # 如果文件已存在，添加数字后缀
        i = 1
        while os.path.exists(target_path):
            name, ext = os.path.splitext(filename)
            new_filename = f"{name} ({i}){ext}"
            target_path = os.path.join(current_path, new_filename)
            i += 1

        # 复制或移动文件
        try:
            if is_cut:
                shutil.move(source_path, target_path)
                self.set_status(f"剪切文件成功: {os.path.basename(target_path)}")
                # 清除剪切状态
                self._cut_file_path = None
                # 重新加载源文件夹
                source_dir = os.path.dirname(source_path)
                if source_dir == current_path:
                    # 如果是在同一文件夹内剪切，只需要重新加载一次
                    self._load_files(current_path)
                else:
                    # 如果是在不同文件夹间剪切，需要重新加载两个文件夹
                    self._load_files(source_dir)
                    self._load_files(current_path)
            else:
                shutil.copy2(source_path, target_path)
                self.set_status(f"粘贴文件成功: {os.path.basename(target_path)}")
                # 重新加载目标文件夹
                self._load_files(current_path)
        except Exception as e:
            self.set_status(f"粘贴文件失败: {str(e)}")

    def _rename_file(self) -> None:
        """重命名文件"""
        import os

        # 获取选中的文件
        selected_items = self._file_tree.selection()
        if not selected_items:
            self.set_status("请选择要重命名的文件")
            return

        # 确定当前路径
        current_path = "/".join(self._path_stack)

        # 获取选中的文件
        item = selected_items[0]
        values = self._file_tree.item(item, "values")
        if values:
            old_filename = values[1]
            old_path = os.path.join(current_path, old_filename)

            # 创建重命名对话框
            dialog = tk.Toplevel(self)
            dialog.title("重命名")
            dialog.geometry("300x100")
            dialog.transient(self)
            dialog.grab_set()

            # 对话框居中
            self._center_dialog(dialog, 300, 100)

            # 添加标签和输入框
            ttk.Label(dialog, text="新文件名:").pack(pady=10)
            entry = ttk.Entry(dialog, width=30)
            entry.insert(0, old_filename)
            entry.pack(pady=5)

            # 添加按钮
            def on_ok():
                new_filename = entry.get().strip()
                if new_filename:
                    new_path = os.path.join(current_path, new_filename)
                    if not os.path.exists(new_path):
                        try:
                            os.rename(old_path, new_path)
                            self.set_status(f"重命名成功: {old_filename} → {new_filename}")
                            # 重新加载文件列表
                            self._load_files(current_path)
                            dialog.destroy()
                        except Exception as e:
                            self.set_status(f"重命名失败: {str(e)}")
                    else:
                        self.set_status("文件名已存在")
                else:
                    self.set_status("文件名不能为空")

            button_frame = ttk.Frame(dialog)
            button_frame.pack(pady=10)
            ttk.Button(button_frame, text="确定", command=on_ok).pack(side=tk.LEFT, padx=5)
            ttk.Button(button_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

            # 绑定回车键
            dialog.bind("<Return>", lambda event: on_ok())

    def _delete_file(self) -> None:
        """删除文件"""
        import os
        import shutil

        # 获取选中的文件
        selected_items = self._file_tree.selection()
        if not selected_items:
            self.set_status("请选择要删除的文件")
            return

        # 确定当前路径
        current_path = "/".join(self._path_stack)

        # 获取选中的文件
        item = selected_items[0]
        values = self._file_tree.item(item, "values")
        if values:
            filename = values[1]
            file_path = os.path.join(current_path, filename)

            # 确认删除
            confirm = tk.messagebox.askyesno(
                "确认删除",
                f"确定要删除文件 '{filename}' 吗？"
            )

            if confirm:
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                    self.set_status(f"删除成功: {filename}")
                    # 重新加载文件列表
                    self._load_files(current_path)
                except Exception as e:
                    self.set_status(f"删除失败: {str(e)}")

    def _on_file_select(self, event) -> None:
        """文件选择事件"""
        pass

    def _get_selected_files(self) -> list[str]:
        """获取选中的文件"""
        selected_files = []
        # 获取当前路径
        current_path = "/".join(self._path_stack)

        for item in self._file_tree.get_children():
            values = self._file_tree.item(item, "values")
            if values and values[0] == "☑":
                # 构建完整的相对路径
                file_name = values[1]
                # 如果当前路径不是html根目录，需要包含子文件夹路径
                if current_path != "html":
                    full_path = f"{current_path}/{file_name}"
                else:
                    full_path = file_name

                if values[3] == "文件夹":
                    # 如果是文件夹，添加该文件夹下的所有html文件
                    folder_path = Path("html") / full_path
                    if folder_path.exists() and folder_path.is_dir():
                        for file_path in folder_path.iterdir():
                            if file_path.is_file() and file_path.suffix == ".html":
                                # 构建文件的完整路径
                                relative_path = str(file_path.relative_to("html"))
                                selected_files.append(relative_path)
                else:
                    # 如果是文件，直接添加
                    selected_files.append(full_path)
        return selected_files

    def _start_process(self) -> None:
        """开始处理"""
        # 获取选中的文件
        selected_files = self._get_selected_files()

        if not selected_files:
            self.set_status("请选择要处理的HTML文件")
            return

        # 选择保存文件夹
        save_folder = self._select_save_folder()
        if not save_folder:
            return

        # 如果选择了多个文件，询问是否合并分析
        merge_analysis = False
        if len(selected_files) > 1:
            merge_confirm = tk.messagebox.askyesno(
                "合并分析",
                "您选择了多个HTML文件，是否合并分析？\n\n合并分析会将所有文件的数据合并后进行分析，保存到以首个HTML文件名命名的结果文件中。\n\n不合并则会分别分析每个文件，保存各自的结果文件。"
            )
            merge_analysis = merge_confirm

        # 开始处理
        self.set_status(f"开始处理 {len(selected_files)} 个文件...")

        # 使用线程执行处理操作，避免UI阻塞
        import threading

        def process_thread():
            try:
                self._controller.process_files(selected_files, save_folder, merge_analysis)
                # 在主线程中更新UI
                self.after(0, lambda: self.set_status("处理完成"))
                # 重新加载数据结果文件夹
                self.after(0, lambda: self._load_files("data"))
            except Exception as e:
                error_msg = f"处理失败: {str(e)}"
                # 在主线程中更新UI
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))

        # 启动线程
        thread = threading.Thread(target=process_thread)
        thread.daemon = True
        thread.start()

    def _preview_database_candidates(self) -> None:
        """预览选中文件中可入库的完整商品。"""
        selected_files = self._get_selected_files()
        if not selected_files:
            self.set_status("请选择要预览入库的HTML文件")
            return

        save_folder = self._select_save_folder()
        if not save_folder:
            return

        keyword = self._keyword_var.get().strip() or None
        self.set_status(f"开始入库预览，共 {len(selected_files)} 个文件")

        import threading

        def preview_thread():
            try:
                summary = self._controller.preview_files_for_database(selected_files, save_folder, keyword)
                message = self._format_summary("入库预览完成", summary)
                self.after(0, lambda: self.set_status(message))
                self.after(0, lambda: messagebox.showinfo("入库预览", message))
                self.after(0, lambda: self._load_files("数据结果"))
            except Exception as e:
                error_msg = f"入库预览失败: {str(e)}"
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))

        thread = threading.Thread(target=preview_thread)
        thread.daemon = True
        thread.start()

    def _import_selected_to_database(self) -> None:
        """将选中文件中的完整商品写入 MySQL。"""
        selected_files = self._get_selected_files()
        if not selected_files:
            self.set_status("请选择要写入数据库的HTML文件")
            return

        confirm = messagebox.askyesno(
            "写入数据库",
            f"将严格过滤不完整商品，并写入 {len(selected_files)} 个HTML文件的有效数据。是否继续？"
        )
        if not confirm:
            return

        keyword = self._keyword_var.get().strip() or None
        self.set_status(f"开始写入数据库，共 {len(selected_files)} 个文件")

        import threading

        def import_thread():
            try:
                summary = self._controller.import_files_to_database(selected_files, keyword)
                message = self._format_summary("写入数据库完成", summary)
                self.after(0, lambda: self.set_status(message))
                self.after(0, lambda: messagebox.showinfo("写入数据库", message))
            except Exception as e:
                error_msg = f"写入数据库失败: {str(e)}"
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))

        thread = threading.Thread(target=import_thread)
        thread.daemon = True
        thread.start()

    def _sync_analytics_warehouse(self) -> None:
        """同步 MySQL 分析数据到本地 DuckDB/Parquet 仓库。"""
        self.set_status("正在同步分析仓库")

        import threading

        def sync_thread():
            try:
                summary = self._controller.sync_analytics_warehouse()
                message = self._format_warehouse_sync_summary(summary)
                self.after(0, lambda: self.set_status(f"分析仓库同步完成，总行数 {summary.get('总行数', 0)}"))
                self.after(0, lambda: messagebox.showinfo("同步分析仓库", message))
            except Exception as e:
                error_msg = f"同步分析仓库失败: {str(e)}"
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))

        thread = threading.Thread(target=sync_thread)
        thread.daemon = True
        thread.start()

    def _show_recommendations(self) -> None:
        """显示 MySQL 中的推荐榜单。"""
        self.set_status("正在读取推荐榜单")

        import threading

        def recommendation_thread():
            try:
                rows = self._controller.get_top_recommendations(limit=50)
                path = self._controller.export_top_recommendations("数据结果", limit=50)
                self.after(0, lambda: self._open_recommendation_window(rows, path))
            except Exception as e:
                error_msg = f"读取推荐榜单失败: {str(e)}"
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))

        thread = threading.Thread(target=recommendation_thread)
        thread.daemon = True
        thread.start()

    def _show_product_pool(self) -> None:
        """显示 MySQL 商品池。"""
        self.set_status("正在读取商品池")

        import threading

        def product_pool_thread():
            try:
                rows = self._controller.get_product_pool(limit=100)
                self.after(0, lambda: self._open_product_pool_window(rows))
            except Exception as e:
                error_msg = f"读取商品池失败: {str(e)}"
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))

        thread = threading.Thread(target=product_pool_thread)
        thread.daemon = True
        thread.start()

    def _show_keyword_opportunities(self) -> None:
        """显示关键词机会聚合视图。"""
        self.set_status("正在读取关键词机会")

        import threading

        def keyword_thread():
            try:
                rows = self._controller.get_keyword_opportunities(limit=100)
                self.after(0, lambda: self._open_keyword_opportunity_window(rows))
            except Exception as e:
                error_msg = f"读取关键词机会失败: {str(e)}"
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))

        thread = threading.Thread(target=keyword_thread)
        thread.daemon = True
        thread.start()

    def _open_review_html_parse_window(self) -> None:
        window = tk.Toplevel(self)
        window.title("解析评论HTML")
        window.geometry("820x470")
        window.transient(self)

        form = ttk.LabelFrame(window, text="本地评论页 HTML", padding=10)
        form.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))

        files_var = tk.StringVar()
        asin_var = tk.StringVar()
        output_var = tk.StringVar(value=str(Path("数据结果") / "评论HTML解析.csv"))
        format_var = tk.StringVar(value="csv")

        ttk.Label(form, text="HTML 文件").grid(row=0, column=0, sticky=tk.W, padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=files_var, width=78).grid(row=0, column=1, sticky=tk.EW, pady=4)

        def browse_html() -> None:
            import tkinter.filedialog as fd

            paths = fd.askopenfilenames(
                title="选择本地 Amazon 评论页 HTML",
                filetypes=(("HTML 文件", "*.html *.htm"), ("所有文件", "*.*")),
            )
            if paths:
                files_var.set(";".join(paths))

        ttk.Button(form, text="浏览", command=browse_html).grid(row=0, column=2, padx=(6, 0), pady=4)

        ttk.Label(form, text="默认 ASIN").grid(row=1, column=0, sticky=tk.W, padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=asin_var, width=24).grid(row=1, column=1, sticky=tk.W, pady=4)
        ttk.Label(form, text="HTML 中无法识别 ASIN 时使用").grid(row=1, column=2, sticky=tk.W, padx=(6, 0), pady=4)

        ttk.Label(form, text="输出格式").grid(row=2, column=0, sticky=tk.W, padx=(0, 6), pady=4)
        format_box = ttk.Combobox(form, textvariable=format_var, values=("csv", "json"), width=10, state="readonly")
        format_box.grid(row=2, column=1, sticky=tk.W, pady=4)

        ttk.Label(form, text="输出文件").grid(row=3, column=0, sticky=tk.W, padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=output_var, width=78).grid(row=3, column=1, sticky=tk.EW, pady=4)

        def browse_output() -> None:
            import tkinter.filedialog as fd

            fmt = format_var.get().strip().lower() or "csv"
            suffix = ".json" if fmt == "json" else ".csv"
            filetypes = (("JSON 文件", "*.json"), ("所有文件", "*.*")) if fmt == "json" else (("CSV 文件", "*.csv"), ("所有文件", "*.*"))
            path = fd.asksaveasfilename(
                title="选择导出文件",
                defaultextension=suffix,
                filetypes=filetypes,
                initialfile=f"评论HTML解析{suffix}",
            )
            if path:
                output_var.set(path)

        ttk.Button(form, text="另存为", command=browse_output).grid(row=3, column=2, padx=(6, 0), pady=4)
        form.columnconfigure(1, weight=1)

        result_frame = ttk.LabelFrame(window, text="解析结果", padding=8)
        result_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)
        result_text = tk.Text(result_frame, height=12, wrap=tk.WORD)
        result_scroll = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=result_text.yview)
        result_text.configure(yscrollcommand=result_scroll.set)
        result_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        result_text.configure(state=tk.DISABLED)

        def set_result(text: str) -> None:
            result_text.configure(state=tk.NORMAL)
            result_text.delete("1.0", tk.END)
            result_text.insert(tk.END, text)
            result_text.configure(state=tk.DISABLED)

        def parse_files() -> None:
            files = [item.strip() for item in files_var.get().split(";") if item.strip()]
            if not files:
                messagebox.showwarning("解析评论HTML", "请选择本地评论页 HTML 文件")
                return
            output_path = output_var.get().strip() or None
            output_format = format_var.get().strip().lower() or "csv"
            default_asin = asin_var.get().strip().upper() or None
            self.set_status("正在解析本地评论 HTML")
            set_result("正在解析，请稍候...")

            import threading

            def parse_thread():
                try:
                    summary = self._controller.export_review_html(
                        files,
                        output_path=output_path,
                        output_format=output_format,
                        default_asin=default_asin,
                    )
                    self.after(0, lambda: set_result(self._format_review_html_summary("评论 HTML 解析完成", summary)))
                    self.after(0, lambda: self.set_status("评论 HTML 解析完成，可继续导入评论"))
                except Exception as e:
                    error_msg = f"评论 HTML 解析失败: {str(e)}"
                    self.after(0, lambda: set_result(error_msg))
                    self.after(0, lambda: self.set_status(error_msg))
                    self.after(0, lambda: self._log_error(error_msg))

            thread = threading.Thread(target=parse_thread)
            thread.daemon = True
            thread.start()

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, text="只解析本地 HTML，不访问 Amazon；导出的 CSV/JSON 可在“导入评论”中写入数据库。").pack(side=tk.LEFT)
        ttk.Button(footer, text="关闭", command=window.destroy).pack(side=tk.RIGHT)
        ttk.Button(footer, text="解析导出", command=parse_files).pack(side=tk.RIGHT, padx=5)
        set_result("请选择手动保存的 Amazon 评论页 HTML，点击“解析导出”。")

    def _open_review_import_window(self) -> None:
        window = tk.Toplevel(self)
        window.title("导入评论")
        window.geometry("760x440")
        window.transient(self)

        form = ttk.LabelFrame(window, text="评论文件", padding=10)
        form.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))

        file_var = tk.StringVar()
        asin_var = tk.StringVar()

        ttk.Label(form, text="文件").grid(row=0, column=0, sticky=tk.W, padx=(0, 6), pady=4)
        file_entry = ttk.Entry(form, textvariable=file_var, width=72)
        file_entry.grid(row=0, column=1, sticky=tk.EW, pady=4)

        def browse_file() -> None:
            import tkinter.filedialog as fd

            path = fd.askopenfilename(
                title="选择评论 CSV/JSON",
                filetypes=(("评论文件", "*.csv *.json"), ("CSV", "*.csv"), ("JSON", "*.json"), ("所有文件", "*.*")),
            )
            if path:
                file_var.set(path)

        ttk.Button(form, text="浏览", command=browse_file).grid(row=0, column=2, padx=(6, 0), pady=4)
        ttk.Label(form, text="默认 ASIN").grid(row=1, column=0, sticky=tk.W, padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=asin_var, width=24).grid(row=1, column=1, sticky=tk.W, pady=4)
        ttk.Label(form, text="当文件内没有 asin 字段时使用").grid(row=1, column=2, sticky=tk.W, padx=(6, 0), pady=4)
        form.columnconfigure(1, weight=1)

        result_frame = ttk.LabelFrame(window, text="导入预览", padding=8)
        result_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)
        result_text = tk.Text(result_frame, height=12, wrap=tk.WORD)
        result_scroll = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=result_text.yview)
        result_text.configure(yscrollcommand=result_scroll.set)
        result_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        result_text.configure(state=tk.DISABLED)

        def set_result(text: str) -> None:
            result_text.configure(state=tk.NORMAL)
            result_text.delete("1.0", tk.END)
            result_text.insert(tk.END, text)
            result_text.configure(state=tk.DISABLED)

        def get_inputs() -> tuple[str, str | None] | None:
            file_path = file_var.get().strip()
            if not file_path:
                messagebox.showwarning("导入评论", "请选择评论 CSV/JSON 文件")
                return None
            return file_path, asin_var.get().strip().upper() or None

        def preview_import() -> None:
            inputs = get_inputs()
            if inputs is None:
                return
            file_path, default_asin = inputs
            self.set_status("正在预览评论导入")
            set_result("正在预览，请稍候...")

            import threading

            def preview_thread():
                try:
                    summary = self._controller.preview_review_import(file_path, default_asin)
                    self.after(0, lambda: set_result(self._format_review_import_summary("评论导入预览", summary)))
                    self.after(0, lambda: self.set_status("评论导入预览完成"))
                except Exception as e:
                    error_msg = f"评论导入预览失败: {str(e)}"
                    self.after(0, lambda: set_result(error_msg))
                    self.after(0, lambda: self.set_status(error_msg))
                    self.after(0, lambda: self._log_error(error_msg))

            thread = threading.Thread(target=preview_thread)
            thread.daemon = True
            thread.start()

        def do_import() -> None:
            inputs = get_inputs()
            if inputs is None:
                return
            file_path, default_asin = inputs
            if not messagebox.askyesno("导入评论", "确认写入评论并生成评论痛点摘要吗？"):
                return
            self.set_status("正在导入评论")
            set_result("正在导入，请稍候...")

            import threading

            def import_thread():
                try:
                    summary = self._controller.import_review_file(file_path, default_asin)
                    self.after(0, lambda: set_result(self._format_review_import_summary("评论导入完成", summary)))
                    self.after(0, lambda: self.set_status("评论导入完成，可在商品详情页查看评论痛点"))
                except Exception as e:
                    error_msg = f"评论导入失败: {str(e)}"
                    self.after(0, lambda: set_result(error_msg))
                    self.after(0, lambda: self.set_status(error_msg))
                    self.after(0, lambda: self._log_error(error_msg))

            thread = threading.Thread(target=import_thread)
            thread.daemon = True
            thread.start()

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, text="不会抓取 Amazon 页面；这里只导入本地 CSV/JSON 评论样本。").pack(side=tk.LEFT)
        ttk.Button(footer, text="关闭", command=window.destroy).pack(side=tk.RIGHT)
        ttk.Button(footer, text="确认导入", command=do_import).pack(side=tk.RIGHT, padx=5)
        ttk.Button(footer, text="预览", command=preview_import).pack(side=tk.RIGHT)
        set_result("请选择评论 CSV/JSON 文件后点击“预览”。")

    def _show_review_insights(self) -> None:
        """显示全局评论洞察。"""
        self.set_status("正在读取评论洞察")

        import threading

        def insight_thread():
            try:
                rows = self._controller.get_review_insights(limit=100)
                self.after(0, lambda: self._open_review_insight_window(rows))
            except Exception as e:
                error_msg = f"读取评论洞察失败: {str(e)}"
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))

        thread = threading.Thread(target=insight_thread)
        thread.daemon = True
        thread.start()

    def _open_review_insight_window(self, rows: list[dict]) -> None:
        window = tk.Toplevel(self)
        window.title("评论洞察")
        window.geometry("1220x620")
        window.transient(self)

        filter_frame = ttk.LabelFrame(window, text="筛选", padding=8)
        filter_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))

        keyword_var = tk.StringVar()
        limit_var = tk.StringVar(value="100")
        ttk.Label(filter_frame, text="ASIN/标题").pack(side=tk.LEFT)
        ttk.Entry(filter_frame, textvariable=keyword_var, width=24).pack(side=tk.LEFT, padx=(5, 12))
        ttk.Label(filter_frame, text="条数").pack(side=tk.LEFT)
        ttk.Entry(filter_frame, textvariable=limit_var, width=8).pack(side=tk.LEFT, padx=(5, 12))

        columns = ("asin", "title", "reviews", "negative", "negative_rate", "rating", "pain", "updated")
        table_frame = ttk.Frame(window)
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=12)
        headings = {
            "asin": "ASIN",
            "title": "商品标题",
            "reviews": "评论样本",
            "negative": "低分数",
            "negative_rate": "低分占比",
            "rating": "样本均分",
            "pain": "主要痛点",
            "updated": "更新时间",
        }
        widths = {
            "asin": 110,
            "title": 300,
            "reviews": 80,
            "negative": 70,
            "negative_rate": 80,
            "rating": 80,
            "pain": 300,
            "updated": 150,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor=tk.W)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        detail_frame = ttk.LabelFrame(window, text="洞察详情", padding=8)
        detail_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 10))
        detail_text = tk.Text(detail_frame, height=6, wrap=tk.WORD)
        detail_scroll = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL, command=detail_text.yview)
        detail_text.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_text.configure(state=tk.DISABLED)

        row_by_item: dict[str, dict] = {}
        current_rows: list[dict] = []

        def set_detail_text(text: str) -> None:
            detail_text.configure(state=tk.NORMAL)
            detail_text.delete("1.0", tk.END)
            detail_text.insert(tk.END, text)
            detail_text.configure(state=tk.DISABLED)

        def render_rows(next_rows: list[dict]) -> None:
            nonlocal current_rows
            current_rows = list(next_rows)
            row_by_item.clear()
            for item in tree.get_children():
                tree.delete(item)
            tree.configure(height=min(max(len(next_rows), 3), 14))
            for row in next_rows:
                item_id = tree.insert(
                    "",
                    tk.END,
                    values=(
                        row.get("asin", "--"),
                        self._truncate_text(row.get("title", "--"), 80),
                        self._format_integer(row.get("review_count")),
                        self._format_integer(row.get("negative_count")),
                        f"{self._format_decimal(row.get('negative_rate'), digits=1)}%",
                        self._format_decimal(row.get("avg_rating"), digits=1),
                        self._format_review_points(row.get("pain_points")) or "--",
                        self._format_datetime(row.get("updated_at")),
                    ),
                )
                row_by_item[item_id] = row
            self.set_status(f"评论洞察已加载，共 {len(next_rows)} 条")

        def show_selected_detail(_event=None) -> None:
            selected = tree.selection()
            row = row_by_item.get(selected[0]) if selected else None
            if row:
                base_text = self._build_review_insight_detail_text(row)
                set_detail_text(base_text + "\n低分样本: 正在读取...")
                load_low_rating_samples(row.get("asin"), base_text)
            else:
                set_detail_text("")

        def load_low_rating_samples(asin: str | None, base_text: str) -> None:
            if not asin:
                return

            import threading

            def sample_thread():
                try:
                    detail = self._controller.get_product_review_insight(asin)
                    samples = detail.get("low_rating_reviews") or []
                    if samples:
                        sample_text = "\n\n低分样本:\n" + "\n".join(
                            self._format_review_sample_detail(sample) for sample in samples
                        )
                    else:
                        sample_text = "\n\n低分样本: 暂无 3 星及以下评论样本。"

                    def update_if_current() -> None:
                        selected = tree.selection()
                        current = row_by_item.get(selected[0]) if selected else None
                        if current and current.get("asin") == asin:
                            set_detail_text(base_text + sample_text)

                    self.after(0, update_if_current)
                except Exception as e:
                    error_msg = f"读取低分样本失败: {str(e)}"
                    self.after(0, lambda: self._log_error(error_msg))

            thread = threading.Thread(target=sample_thread)
            thread.daemon = True
            thread.start()

        def open_product_detail(_event=None) -> None:
            selected = tree.selection()
            row = row_by_item.get(selected[0]) if selected else None
            if row and row.get("asin"):
                self._show_product_detail(row["asin"])

        def parse_int(value: str) -> int:
            value = value.strip()
            return int(value) if value else 100

        def refresh_rows() -> None:
            try:
                limit = parse_int(limit_var.get())
            except ValueError:
                messagebox.showwarning("评论洞察", "条数只能填写数字")
                return
            keyword = keyword_var.get().strip() or None
            self.set_status("正在刷新评论洞察")

            import threading

            def refresh_thread():
                try:
                    next_rows = self._controller.get_review_insights(limit=limit, keyword=keyword)
                    self.after(0, lambda: window.winfo_exists() and render_rows(next_rows))
                except Exception as e:
                    error_msg = f"评论洞察刷新失败: {str(e)}"
                    self.after(0, lambda: self.set_status(error_msg))
                    self.after(0, lambda: self._log_error(error_msg))

            thread = threading.Thread(target=refresh_thread)
            thread.daemon = True
            thread.start()

        def export_rows() -> None:
            if not current_rows:
                messagebox.showwarning("评论洞察", "当前没有可导出的评论洞察")
                return
            import csv

            output = Path("数据结果")
            output.mkdir(parents=True, exist_ok=True)
            path = output / "评论洞察.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=[
                        "ASIN",
                        "商品标题",
                        "评论样本",
                        "低分评论数",
                        "低分占比",
                        "样本均分",
                        "痛点主题",
                        "好评主题",
                        "评论风险",
                        "改良机会",
                        "更新时间",
                    ],
                )
                writer.writeheader()
                for row in current_rows:
                    writer.writerow(
                        {
                            "ASIN": row.get("asin", ""),
                            "商品标题": row.get("title", ""),
                            "评论样本": row.get("review_count", ""),
                            "低分评论数": row.get("negative_count", ""),
                            "低分占比": f"{self._format_decimal(row.get('negative_rate'), digits=1)}%",
                            "样本均分": self._format_decimal(row.get("avg_rating"), digits=1),
                            "痛点主题": self._format_review_points(row.get("pain_points")),
                            "好评主题": self._format_review_points(row.get("positive_points")),
                            "评论风险": row.get("risk_summary") or "",
                            "改良机会": row.get("opportunity_summary") or "",
                            "更新时间": self._format_datetime(row.get("updated_at")),
                        }
                    )
            self.set_status(f"评论洞察已导出: {path}")
            messagebox.showinfo("评论洞察", f"已导出: {path}")

        tree.bind("<<TreeviewSelect>>", show_selected_detail)
        tree.bind("<Double-1>", open_product_detail)
        ttk.Button(filter_frame, text="刷新", command=refresh_rows).pack(side=tk.LEFT, padx=5)
        ttk.Button(filter_frame, text="导出洞察", command=export_rows).pack(side=tk.LEFT)

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, text="双击商品可打开详情页；评论洞察基于已导入的本地评论样本。").pack(side=tk.LEFT)
        ttk.Button(footer, text="查看详情", command=open_product_detail).pack(side=tk.RIGHT, padx=5)
        ttk.Button(footer, text="关闭", command=window.destroy).pack(side=tk.RIGHT)
        render_rows(rows)

    def _open_keyword_opportunity_window(self, rows: list[dict]) -> None:
        window = tk.Toplevel(self)
        window.title("关键词机会")
        window.geometry("1280x640")
        window.transient(self)

        filter_frame = ttk.LabelFrame(window, text="关键词筛选", padding=8)
        filter_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))

        keyword_var = tk.StringVar()
        min_products_var = tk.StringVar()
        limit_var = tk.StringVar(value="100")

        ttk.Label(filter_frame, text="关键词").pack(side=tk.LEFT)
        ttk.Entry(filter_frame, textvariable=keyword_var, width=20).pack(side=tk.LEFT, padx=(5, 12))
        ttk.Label(filter_frame, text="最少商品数").pack(side=tk.LEFT)
        ttk.Entry(filter_frame, textvariable=min_products_var, width=8).pack(side=tk.LEFT, padx=(5, 12))
        ttk.Label(filter_frame, text="条数").pack(side=tk.LEFT)
        ttk.Entry(filter_frame, textvariable=limit_var, width=8).pack(side=tk.LEFT, padx=(5, 12))

        columns = (
            "keyword",
            "marketplace",
            "opportunity",
            "level",
            "products",
            "avg_score",
            "demand",
            "competition",
            "price",
            "reviews",
            "bought",
            "rank",
            "top10",
            "updated",
        )
        table_frame = ttk.Frame(window)
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=13)
        headings = {
            "keyword": "关键词",
            "marketplace": "站点",
            "opportunity": "机会分",
            "level": "机会等级",
            "products": "商品数",
            "avg_score": "均分",
            "demand": "需求",
            "competition": "竞争",
            "price": "均价",
            "reviews": "均评论",
            "bought": "总近月购买",
            "rank": "均排名",
            "top10": "Top10数",
            "updated": "最近采集",
        }
        widths = {
            "keyword": 170,
            "marketplace": 60,
            "opportunity": 75,
            "level": 80,
            "products": 70,
            "avg_score": 70,
            "demand": 70,
            "competition": 70,
            "price": 80,
            "reviews": 90,
            "bought": 110,
            "rank": 75,
            "top10": 70,
            "updated": 150,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor=tk.W)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        reason_frame = ttk.LabelFrame(window, text="机会判断", padding=8)
        reason_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 10))
        reason_text = tk.Text(reason_frame, height=5, wrap=tk.WORD)
        reason_scroll = ttk.Scrollbar(reason_frame, orient=tk.VERTICAL, command=reason_text.yview)
        reason_text.configure(yscrollcommand=reason_scroll.set)
        reason_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        reason_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        reason_text.configure(state=tk.DISABLED)

        row_by_item: dict[str, dict] = {}

        def render_rows(next_rows: list[dict]) -> None:
            row_by_item.clear()
            for item in tree.get_children():
                tree.delete(item)
            tree.configure(height=min(max(len(next_rows), 3), 14))
            for row in next_rows:
                item_id = tree.insert(
                    "",
                    tk.END,
                    values=(
                        row.get("keyword", "--"),
                        row.get("marketplace", "--"),
                        self._format_decimal(row.get("opportunity_score"), digits=1),
                        row.get("opportunity_level", "--"),
                        self._format_integer(row.get("product_count")),
                        self._format_decimal(row.get("avg_total_score"), digits=1),
                        self._format_decimal(row.get("avg_demand_score"), digits=1),
                        self._format_decimal(row.get("avg_competition_score"), digits=1),
                        self._format_money(row.get("avg_price")),
                        self._format_integer(row.get("avg_review_count")),
                        self._format_integer(row.get("total_monthly_bought")),
                        self._format_decimal(row.get("avg_organic_rank"), digits=1),
                        self._format_integer(row.get("top10_count")),
                        self._format_datetime(row.get("latest_snapshot_at")),
                    ),
                )
                row_by_item[item_id] = row
            self.set_status(f"关键词机会已加载，共 {len(next_rows)} 条")

        def show_selected_reason(_event=None) -> None:
            selected = tree.selection()
            row = row_by_item.get(selected[0]) if selected else None
            reason_text.configure(state=tk.NORMAL)
            reason_text.delete("1.0", tk.END)
            if row:
                reason_text.insert(tk.END, f"关键词: {row.get('keyword', '--')}\n")
                reason_text.insert(tk.END, f"机会判断: {row.get('opportunity_reason', '--')}\n")
                reason_text.insert(tk.END, f"风险提示: {row.get('risk_warnings', '--')}\n")
                reason_text.insert(tk.END, f"进入策略: {row.get('entry_strategy', '--')}\n")
                reason_text.insert(
                    tk.END,
                    "说明: 机会分综合需求、竞争、评分质量、价格带、自然排名和样本商品数，"
                    "用于发现值得继续验证的关键词市场。",
                )
            reason_text.configure(state=tk.DISABLED)

        def parse_int(value: str) -> int | None:
            value = value.strip()
            if not value:
                return None
            return int(value)

        def refresh_rows() -> None:
            try:
                limit = parse_int(limit_var.get()) or 100
                min_products = parse_int(min_products_var.get())
            except ValueError:
                messagebox.showwarning("关键词机会", "条数和最少商品数只能填写数字")
                return

            filters = {
                "keyword": keyword_var.get().strip() or None,
                "min_products": min_products,
            }
            self.set_status("正在刷新关键词机会")

            import threading

            def refresh_thread():
                try:
                    next_rows = self._controller.get_keyword_opportunities(limit=limit, **filters)
                    self.after(0, lambda: window.winfo_exists() and render_rows(next_rows))
                except Exception as e:
                    error_msg = f"关键词机会刷新失败: {str(e)}"
                    self.after(0, lambda: self.set_status(error_msg))
                    self.after(0, lambda: self._log_error(error_msg))

            thread = threading.Thread(target=refresh_thread)
            thread.daemon = True
            thread.start()

        def reset_filters() -> None:
            keyword_var.set("")
            min_products_var.set("")
            limit_var.set("100")
            refresh_rows()

        tree.bind("<<TreeviewSelect>>", show_selected_reason)
        ttk.Button(filter_frame, text="刷新", command=refresh_rows).pack(side=tk.LEFT, padx=5)
        ttk.Button(filter_frame, text="重置", command=reset_filters).pack(side=tk.LEFT)

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, text="关键词机会用于发现市场方向，最终仍需结合评论痛点、利润和供应链验证。").pack(side=tk.LEFT)
        ttk.Button(footer, text="关闭", command=window.destroy).pack(side=tk.RIGHT)
        render_rows(rows)

    def _show_task_center(self) -> None:
        """显示 MySQL 任务日志。"""
        self.set_status("正在读取任务中心")

        import threading

        def task_center_thread():
            try:
                rows = self._controller.get_task_jobs(limit=100)
                self.after(0, lambda: self._open_task_center_window(rows))
            except Exception as e:
                error_msg = f"读取任务中心失败: {str(e)}"
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))

        thread = threading.Thread(target=task_center_thread)
        thread.daemon = True
        thread.start()

    def _open_task_center_window(self, rows: list[dict]) -> None:
        window = tk.Toplevel(self)
        window.title("任务中心")
        window.geometry("1120x600")
        window.transient(self)

        filter_frame = ttk.LabelFrame(window, text="任务筛选", padding=8)
        filter_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))

        ttk.Label(filter_frame, text="状态").pack(side=tk.LEFT)
        status_var = tk.StringVar(value="全部")
        status_combo = ttk.Combobox(
            filter_frame,
            textvariable=status_var,
            values=("全部", "运行中", "完成", "失败"),
            width=10,
            state="readonly",
        )
        status_combo.pack(side=tk.LEFT, padx=(5, 12))

        ttk.Label(filter_frame, text="条数").pack(side=tk.LEFT)
        limit_var = tk.StringVar(value="100")
        ttk.Entry(filter_frame, textvariable=limit_var, width=8).pack(side=tk.LEFT, padx=(5, 12))

        columns = (
            "id",
            "keyword",
            "status",
            "pages",
            "found",
            "valid",
            "inserted",
            "started",
            "finished",
            "error",
        )
        table_frame = ttk.Frame(window)
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=12)
        headings = {
            "id": "任务ID",
            "keyword": "关键词",
            "status": "状态",
            "pages": "页数",
            "found": "解析商品数",
            "valid": "有效商品数",
            "inserted": "入库商品数",
            "started": "开始时间",
            "finished": "结束时间",
            "error": "错误信息",
        }
        widths = {
            "id": 70,
            "keyword": 160,
            "status": 80,
            "pages": 60,
            "found": 95,
            "valid": 95,
            "inserted": 95,
            "started": 150,
            "finished": 150,
            "error": 180,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor=tk.W)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        detail_frame = ttk.LabelFrame(window, text="任务详情", padding=8)
        detail_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 10))
        detail_text = tk.Text(detail_frame, height=5, wrap=tk.WORD)
        detail_scroll = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL, command=detail_text.yview)
        detail_text.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_text.configure(state=tk.DISABLED)

        row_by_item: dict[str, dict] = {}

        def display_value(value) -> str:
            return "--" if value in (None, "") else str(value)

        def render_rows(next_rows: list[dict]) -> None:
            row_by_item.clear()
            for item in tree.get_children():
                tree.delete(item)
            tree.configure(height=min(max(len(next_rows), 3), 14))
            for row in next_rows:
                error_text = display_value(row.get("error_message"))
                item_id = tree.insert(
                    "",
                    tk.END,
                    values=(
                        display_value(row.get("id")),
                        display_value(row.get("keyword")),
                        display_value(row.get("status")),
                        self._format_integer(row.get("pages")),
                        self._format_integer(row.get("total_found")),
                        self._format_integer(row.get("total_valid")),
                        self._format_integer(row.get("total_inserted")),
                        self._format_datetime(row.get("started_at")),
                        self._format_datetime(row.get("finished_at")),
                        self._truncate_text(error_text, 48),
                    ),
                )
                row_by_item[item_id] = row
            self.set_status(f"任务中心已加载，共 {len(next_rows)} 条")

        def show_selected_detail(_event=None) -> None:
            selected = tree.selection()
            row = row_by_item.get(selected[0]) if selected else None
            detail_text.configure(state=tk.NORMAL)
            detail_text.delete("1.0", tk.END)
            if row:
                detail_text.insert(tk.END, f"任务ID: {display_value(row.get('id'))}\n")
                detail_text.insert(tk.END, f"关键词: {display_value(row.get('keyword'))}\n")
                detail_text.insert(tk.END, f"状态: {display_value(row.get('status'))}\n")
                detail_text.insert(tk.END, f"采集链接: {display_value(row.get('url'))}\n")
                detail_text.insert(tk.END, f"错误信息: {display_value(row.get('error_message'))}")
            detail_text.configure(state=tk.DISABLED)

        def refresh_rows() -> None:
            try:
                limit = int(limit_var.get().strip() or "100")
            except ValueError:
                messagebox.showwarning("任务中心", "条数只能填写数字")
                return

            status = status_var.get().strip() or None
            if status == "全部":
                status = None

            self.set_status("正在刷新任务中心")

            import threading

            def refresh_thread():
                try:
                    next_rows = self._controller.get_task_jobs(limit=limit, status=status)
                    self.after(0, lambda: window.winfo_exists() and render_rows(next_rows))
                except Exception as e:
                    error_msg = f"任务中心刷新失败: {str(e)}"
                    self.after(0, lambda: self.set_status(error_msg))
                    self.after(0, lambda: self._log_error(error_msg))

            thread = threading.Thread(target=refresh_thread)
            thread.daemon = True
            thread.start()

        tree.bind("<<TreeviewSelect>>", show_selected_detail)
        ttk.Button(filter_frame, text="刷新", command=refresh_rows).pack(side=tk.LEFT, padx=5)

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, text="当前任务中心读取 MySQL 中的入库/采集日志，后续可继续接入实时爬虫进度。").pack(side=tk.LEFT)
        ttk.Button(footer, text="关闭", command=window.destroy).pack(side=tk.RIGHT)
        render_rows(rows)

    def _open_product_pool_window(self, rows: list[dict]) -> None:
        window = tk.Toplevel(self)
        window.title("商品池")
        window.geometry("1160x590")
        window.transient(self)

        filter_frame = ttk.LabelFrame(window, text="筛选条件", padding=8)
        filter_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))

        keyword_var = tk.StringVar()
        min_score_var = tk.StringVar()
        min_price_var = tk.StringVar()
        max_price_var = tk.StringVar()
        max_reviews_var = tk.StringVar()

        ttk.Label(filter_frame, text="关键词/ASIN").pack(side=tk.LEFT)
        ttk.Entry(filter_frame, textvariable=keyword_var, width=18).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(filter_frame, text="最低分").pack(side=tk.LEFT)
        ttk.Entry(filter_frame, textvariable=min_score_var, width=7).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(filter_frame, text="价格").pack(side=tk.LEFT)
        ttk.Entry(filter_frame, textvariable=min_price_var, width=7).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(filter_frame, text="-").pack(side=tk.LEFT)
        ttk.Entry(filter_frame, textvariable=max_price_var, width=7).pack(side=tk.LEFT, padx=(2, 10))
        ttk.Label(filter_frame, text="最大评论数").pack(side=tk.LEFT)
        ttk.Entry(filter_frame, textvariable=max_reviews_var, width=9).pack(side=tk.LEFT, padx=(4, 10))

        columns = ("asin", "title", "score", "price", "rating", "reviews", "bought", "rank", "updated")
        tree = ttk.Treeview(window, columns=columns, show="headings", selectmode="extended")
        headings = {
            "asin": "ASIN",
            "title": "商品标题",
            "score": "综合得分",
            "price": "价格",
            "rating": "评分",
            "reviews": "评论数",
            "bought": "近月购买量",
            "rank": "自然排名",
            "updated": "最近采集",
        }
        widths = {
            "asin": 105,
            "title": 330,
            "score": 80,
            "price": 70,
            "rating": 60,
            "reviews": 85,
            "bought": 100,
            "rank": 80,
            "updated": 145,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor=tk.W)

        scrollbar = ttk.Scrollbar(window, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

        row_by_item: dict[str, dict] = {}

        def render_rows(next_rows: list[dict]) -> None:
            row_by_item.clear()
            for item in tree.get_children():
                tree.delete(item)
            for row in next_rows:
                item_id = tree.insert(
                    "",
                    tk.END,
                    values=(
                        row.get("asin", ""),
                        row.get("title", ""),
                        row.get("total_score", ""),
                        row.get("price", ""),
                        row.get("rating", ""),
                        row.get("review_count", ""),
                        row.get("monthly_bought", ""),
                        row.get("organic_rank", ""),
                        row.get("snapshot_at", ""),
                    ),
                )
                row_by_item[item_id] = row
            self.set_status(f"商品池已加载，共 {len(next_rows)} 条")

        def open_selected(_event=None):
            selected = tree.selection()
            if not selected:
                return
            row = row_by_item.get(selected[0])
            if row and row.get("asin"):
                self._show_product_detail(row["asin"])

        tree.bind("<Double-1>", open_selected)

        def compare_selected() -> None:
            selected = tree.selection()
            rows_to_compare = [row_by_item[item] for item in selected if item in row_by_item]
            if len(rows_to_compare) < 2:
                messagebox.showwarning("商品对比", "请至少选择 2 个商品进行对比")
                return
            if len(rows_to_compare) > 5:
                rows_to_compare = rows_to_compare[:5]
                messagebox.showinfo("商品对比", "一次最多对比 5 个商品，已使用前 5 个选中项")
            self._open_product_compare_window(rows_to_compare)

        def parse_float(value: str) -> float | None:
            value = value.strip()
            if not value:
                return None
            return float(value)

        def parse_int(value: str) -> int | None:
            value = value.strip()
            if not value:
                return None
            return int(value)

        def apply_filters() -> None:
            try:
                filters = {
                    "keyword": keyword_var.get().strip() or None,
                    "min_score": parse_float(min_score_var.get()),
                    "min_price": parse_float(min_price_var.get()),
                    "max_price": parse_float(max_price_var.get()),
                    "max_reviews": parse_int(max_reviews_var.get()),
                }
            except ValueError:
                messagebox.showwarning("商品池筛选", "筛选条件只能填写数字")
                return

            self.set_status("正在筛选商品池")
            import threading

            def filter_thread():
                try:
                    next_rows = self._controller.get_product_pool(limit=100, **filters)
                    self.after(0, lambda: render_rows(next_rows))
                except Exception as e:
                    error_msg = f"商品池筛选失败: {str(e)}"
                    self.after(0, lambda: self.set_status(error_msg))
                    self.after(0, lambda: self._log_error(error_msg))

            thread = threading.Thread(target=filter_thread)
            thread.daemon = True
            thread.start()

        def reset_filters() -> None:
            keyword_var.set("")
            min_score_var.set("")
            min_price_var.set("")
            max_price_var.set("")
            max_reviews_var.set("")
            apply_filters()

        ttk.Button(filter_frame, text="筛选", command=apply_filters).pack(side=tk.LEFT, padx=5)
        ttk.Button(filter_frame, text="重置", command=reset_filters).pack(side=tk.LEFT)

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, text="双击商品查看详情；按 Ctrl/Shift 可多选商品进行对比").pack(side=tk.LEFT)
        ttk.Button(footer, text="对比选中", command=compare_selected).pack(side=tk.RIGHT, padx=5)
        ttk.Button(footer, text="查看详情", command=open_selected).pack(side=tk.RIGHT, padx=5)
        ttk.Button(footer, text="关闭", command=window.destroy).pack(side=tk.RIGHT)
        render_rows(rows)

    def _open_product_compare_window(self, rows: list[dict]) -> None:
        window = tk.Toplevel(self)
        window.title("商品对比")
        window.geometry("1180x620")
        window.transient(self)

        top = ttk.Frame(window, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(top, text=f"已选择 {len(rows)} 个商品进行对比", font=self._font_title).pack(anchor=tk.W)

        columns = ("metric",) + tuple(f"p{i}" for i in range(1, len(rows) + 1))
        tree = ttk.Treeview(window, columns=columns, show="headings")
        tree.heading("metric", text="指标")
        tree.column("metric", width=110, anchor=tk.W)

        for index, row in enumerate(rows, start=1):
            tree.heading(f"p{index}", text=f"{index}. {row.get('asin', '')}")
            tree.column(f"p{index}", width=200, anchor=tk.W)

        metrics = [
            ("ASIN", "asin"),
            ("商品标题", "title"),
            ("关键词", "keyword"),
            ("综合得分", "total_score"),
            ("价格", "price"),
            ("评分", "rating"),
            ("评论数", "review_count"),
            ("近月购买量", "monthly_bought"),
            ("自然排名", "organic_rank"),
            ("是否促销", "is_deal"),
            ("最近采集", "snapshot_at"),
        ]

        for label, key in metrics:
            tree.insert("", tk.END, values=(label,) + tuple(row.get(key, "") for row in rows))

        scrollbar = ttk.Scrollbar(window, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        reason_frame = ttk.LabelFrame(window, text="推荐理由", padding=8)
        reason_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=False, padx=10, pady=(0, 8))
        reason_text = tk.Text(reason_frame, height=8, wrap=tk.WORD)
        reason_scroll = ttk.Scrollbar(reason_frame, orient=tk.VERTICAL, command=reason_text.yview)
        reason_text.configure(yscrollcommand=reason_scroll.set)
        reason_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        reason_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for index, row in enumerate(rows, start=1):
            reason_text.insert(tk.END, f"{index}. {row.get('asin', '')} - {row.get('title', '')}\n")
            reason_text.insert(tk.END, f"{row.get('reason', '暂无推荐理由')}\n\n")
        reason_text.configure(state=tk.DISABLED)

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, text="对比用于辅助决策，最终仍需结合趋势、评论痛点和利润测算").pack(side=tk.LEFT)
        ttk.Button(footer, text="关闭", command=window.destroy).pack(side=tk.RIGHT)
        self.set_status(f"商品对比已打开，共 {len(rows)} 个商品")

    def _short_text(self, text: str, max_length: int) -> str:
        text = str(text or "")
        if len(text) <= max_length:
            return text
        return text[:max_length - 1] + "…"

    def _truncate_text(self, text: str, max_length: int) -> str:
        text = str(text or "")
        if len(text) <= max_length:
            return text
        return text[:max_length].rstrip() + "..."

    def _build_score_breakdown_text(self, product: dict) -> str:
        parts = [
            f"需求得分: {self._format_decimal(product.get('demand_score'))}",
            f"竞争得分: {self._format_decimal(product.get('competition_score'))}",
            f"评分质量得分: {self._format_decimal(product.get('rating_score'))}",
            f"价格带得分: {self._format_decimal(product.get('price_score'))}",
            f"排名得分: {self._format_decimal(product.get('rank_score'))}",
        ]
        return "；".join(parts) + "。综合得分由需求、竞争、评分质量、价格带和排名等子分数组成，用于辅助判断选品机会。"

    def _build_selection_conclusion(self, product: dict, snapshots: list[dict]) -> str:
        # 逻辑已下沉至 services.product_advice，GUI 与 Web 共享同一判定（DRY）。
        from services.product_advice import selection_conclusion

        return selection_conclusion(product, snapshots)

    def _build_product_risk_text(self, product: dict, snapshots: list[dict]) -> str:
        from services.product_advice import risk_text

        return risk_text(product, snapshots)

    def _build_entry_strategy_text(self, product: dict, snapshots: list[dict]) -> str:
        from services.product_advice import entry_strategy

        return entry_strategy(product, snapshots)

    def _build_review_pain_text(self, review_data: dict) -> str:
        if not review_data or review_data.get("status") == "empty":
            return review_data.get("message", "暂未采集评论内容，无法形成评论痛点分析。")

        insight = review_data.get("insight") or {}
        samples = review_data.get("low_rating_reviews") or []
        parts: list[str] = []

        if insight:
            review_count = self._format_integer(insight.get("review_count"))
            negative_count = self._format_integer(insight.get("negative_count"))
            avg_rating = self._format_decimal(insight.get("avg_rating"), digits=1)
            parts.append(f"评论样本: {review_count} 条；低分评论: {negative_count} 条；样本均分: {avg_rating}")

            pain_text = self._format_review_points(insight.get("pain_points"))
            if pain_text:
                parts.append(f"痛点主题: {pain_text}")
            positive_text = self._format_review_points(insight.get("positive_points"))
            if positive_text:
                parts.append(f"好评主题: {positive_text}")
            if insight.get("risk_summary"):
                parts.append(f"评论风险: {insight.get('risk_summary')}")
            if insight.get("opportunity_summary"):
                parts.append(f"改良机会: {insight.get('opportunity_summary')}")
        else:
            parts.append("已采集少量评论，但尚未生成痛点摘要。")

        if samples:
            sample_text = "；".join(self._format_review_sample(row) for row in samples[:3])
            parts.append(f"低分样本: {sample_text}")
        return "；".join(part for part in parts if part)

    def _format_review_points(self, points) -> str:
        if not points:
            return ""
        if isinstance(points, dict):
            points = points.items()
        result: list[str] = []
        for item in points:
            if isinstance(item, dict):
                label = item.get("theme") or item.get("name") or item.get("label") or "未命名主题"
                count = item.get("count")
                weighted_score = item.get("weighted_score")
                severity = item.get("severity_level")
                if weighted_score is not None:
                    level_text = f"，{severity}风险" if severity else ""
                    result.append(f"{label}({count}次，权重{weighted_score}{level_text})")
                else:
                    result.append(f"{label}({count})" if count is not None else str(label))
            elif isinstance(item, tuple) and len(item) == 2:
                result.append(f"{item[0]}({item[1]})")
            else:
                result.append(str(item))
        return "、".join(result[:6])

    def _format_review_sample(self, row: dict) -> str:
        rating = self._format_decimal(row.get("rating"), digits=1)
        title = self._truncate_text(row.get("title") or row.get("body") or "无标题评论", 42)
        return f"{rating}星 {title}"

    def _build_review_insight_detail_text(self, row: dict) -> str:
        return "\n".join(
            [
                f"ASIN: {row.get('asin', '--')}",
                f"商品标题: {row.get('title', '--')}",
                f"痛点主题: {self._format_review_points(row.get('pain_points')) or '--'}",
                f"好评主题: {self._format_review_points(row.get('positive_points')) or '--'}",
                f"评论风险: {row.get('risk_summary') or '--'}",
                f"改良机会: {row.get('opportunity_summary') or '--'}",
            ]
        )

    def _format_review_sample_detail(self, row: dict) -> str:
        rating = self._format_decimal(row.get("rating"), digits=1)
        title = self._truncate_text(row.get("title") or "无标题评论", 60)
        body = self._truncate_text(row.get("body") or "", 120)
        review_at = self._format_datetime(row.get("review_at"))
        verified = row.get("verified_purchase") or "--"
        helpful = self._format_integer(row.get("helpful_votes"))
        return f"- {rating}星 | {title} | {body} | 时间: {review_at} | 验证购买: {verified} | 有用票: {helpful}"

    def _build_trend_confidence_text(self, snapshots: list[dict]) -> str:
        """诚实展示趋势置信度：样本量、跨度、置信度、趋势摘要、促销提示。

        仅用于展示，growth_score 标注为"未计入综合得分"，不改评分口径。
        任何异常都降级为提示文案，避免影响详情页打开。
        """
        try:
            assessment = assess_product_trend(snapshots or [])
        except Exception:
            return "趋势置信度: 暂不可用"
        lines = [
            f"置信度: {assessment.confidence}"
            f"（样本 {assessment.sample_size} 个快照，跨度约 {assessment.span_days:.0f} 天）",
            assessment.summary,
        ]
        if assessment.confidence != "无法判断":
            lines.append(
                f"趋势分（参考，未计入综合得分）: {assessment.growth_score:.0f}"
            )
        return "\n".join(lines)

    def _build_trend_insight_text(self, snapshots: list[dict]) -> str:
        rows = sorted((row for row in snapshots if row), key=lambda row: str(row.get("snapshot_at") or ""))
        if not rows:
            return "暂无历史快照，暂无法判断趋势。"
        if len(rows) < 2:
            return "当前仅有 1 条采集记录，暂无法判断趋势。"

        first = rows[0]
        latest = rows[-1]
        parts = [
            self._describe_metric_change("价格", "price", first, latest, "money"),
            self._describe_metric_change("评分", "rating", first, latest, "decimal"),
            self._describe_metric_change("评论数", "review_count", first, latest, "integer"),
            self._describe_metric_change("近月购买量", "monthly_bought", first, latest, "integer"),
            self._describe_metric_change("自然排名", "organic_rank", first, latest, "rank"),
        ]
        parts = [part for part in parts if part]

        anomalies = self._detect_trend_anomalies(rows)
        if anomalies:
            parts.append("异常提示: " + "；".join(anomalies))
        else:
            parts.append("异常提示: 暂未发现明显突变。")
        return "；".join(parts)

    def _describe_metric_change(
        self,
        label: str,
        key: str,
        first: dict,
        latest: dict,
        value_type: str,
    ) -> str:
        start = self._to_number(first.get(key))
        end = self._to_number(latest.get(key))
        if start is None or end is None:
            return f"{label}: 数据不足"

        delta = end - start
        if value_type == "rank":
            start_text = self._format_integer(start)
            end_text = self._format_integer(end)
            if abs(delta) < 1:
                return f"{label}: {start_text} -> {end_text}，基本稳定"
            if delta < 0:
                return f"{label}: {start_text} -> {end_text}，排名改善 {self._format_integer(abs(delta))} 位"
            return f"{label}: {start_text} -> {end_text}，排名后退 {self._format_integer(abs(delta))} 位"

        start_text = self._format_trend_value(start, value_type)
        end_text = self._format_trend_value(end, value_type)
        if self._is_metric_stable(delta, start, value_type):
            return f"{label}: {start_text} -> {end_text}，基本稳定"

        direction = "上升" if delta > 0 else "下降"
        if value_type == "decimal":
            return f"{label}: {start_text} -> {end_text}，{direction} {abs(delta):.1f}"
        if start:
            return f"{label}: {start_text} -> {end_text}，{direction} {self._format_percent(abs(delta) / abs(start))}"
        return f"{label}: {start_text} -> {end_text}，{direction}"

    def _detect_trend_anomalies(self, snapshots: list[dict]) -> list[str]:
        anomalies: list[str] = []
        checks = [
            ("价格", "price", 0.20, 0),
            ("评论数", "review_count", 0.25, 100),
            ("近月购买量", "monthly_bought", 0.50, 1000),
            ("自然排名", "organic_rank", 0.50, 10),
        ]

        for previous, current in zip(snapshots, snapshots[1:]):
            time_text = self._format_datetime(current.get("snapshot_at"))
            for label, key, percent_threshold, absolute_threshold in checks:
                before = self._to_number(previous.get(key))
                after = self._to_number(current.get(key))
                if before in (None, 0) or after is None:
                    continue
                delta = after - before
                percent = abs(delta) / abs(before)
                if percent >= percent_threshold and abs(delta) >= absolute_threshold:
                    direction = self._trend_direction(label, delta)
                    anomalies.append(f"{time_text} {label}{direction} {self._format_percent(percent)}")

            previous_rating = self._to_number(previous.get("rating"))
            current_rating = self._to_number(current.get("rating"))
            if previous_rating is not None and current_rating is not None:
                rating_delta = current_rating - previous_rating
                if rating_delta <= -0.2:
                    anomalies.append(f"{time_text} 评分下降 {abs(rating_delta):.1f}")

        return anomalies[:4]

    def _trend_direction(self, label: str, delta: float) -> str:
        if label == "自然排名":
            return "改善" if delta < 0 else "后退"
        return "上升" if delta > 0 else "下降"

    def _format_trend_value(self, value: float, value_type: str) -> str:
        if value_type == "money":
            return self._format_money(value)
        if value_type == "integer":
            return self._format_integer(value)
        if value_type == "decimal":
            return self._format_decimal(value, digits=1)
        return str(value)

    def _is_metric_stable(self, delta: float, start: float, value_type: str) -> bool:
        if value_type == "money":
            return abs(delta) < 0.01
        if value_type == "decimal":
            return abs(delta) < 0.05
        return abs(delta) < 1 or (start != 0 and abs(delta) / abs(start) < 0.01)

    def _format_percent(self, value: float) -> str:
        return f"{value * 100:.1f}%"

    def _format_money(self, value) -> str:
        number = self._to_number(value)
        return "--" if number is None else f"${number:,.2f}"

    def _format_integer(self, value) -> str:
        number = self._to_number(value)
        return "--" if number is None else f"{int(number):,}"

    def _format_decimal(self, value, digits: int = 0) -> str:
        number = self._to_number(value)
        return "--" if number is None else f"{number:.{digits}f}"

    def _format_datetime(self, value) -> str:
        return "--" if value in (None, "") else str(value)

    def _format_yes_no(self, value) -> str:
        if value in (None, ""):
            return "--"
        if isinstance(value, str):
            if value in ("是", "否"):
                return value
            return "是" if value.strip().lower() in ("1", "true", "yes", "y") else "否"
        return "是" if bool(value) else "否"

    def _to_number(self, value) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _show_product_detail(self, asin: str) -> None:
        self.set_status(f"正在读取商品详情: {asin}")

        import threading

        def detail_thread():
            try:
                detail = self._controller.get_product_history(asin)
                self.after(0, lambda: self._open_product_detail_window(detail))
            except Exception as e:
                error_msg = f"读取商品详情失败: {str(e)}"
                self.after(0, lambda: self.set_status(error_msg))
                self.after(0, lambda: self._log_error(error_msg))

        thread = threading.Thread(target=detail_thread)
        thread.daemon = True
        thread.start()

    def _open_product_detail_window(self, detail: dict) -> None:
        product = detail.get("product")
        snapshots = detail.get("snapshots", [])
        if not product:
            messagebox.showwarning("商品详情", "未找到该商品")
            return

        window = tk.Toplevel(self)
        window.title(f"商品详情 - {product.get('asin', '')}")
        window.geometry("1120x760")
        window.transient(self)

        top = ttk.Frame(window, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)
        full_title = str(product.get("title", "") or "")
        ttk.Label(top, text=self._truncate_text(full_title, 96), font=self._font_title, wraplength=980).pack(anchor=tk.W)
        meta = (
            f"ASIN: {product.get('asin', '')}    "
            f"综合得分: {product.get('total_score', '')}    "
            f"首次采集: {product.get('first_seen_at', '')}    "
            f"最近采集: {product.get('last_seen_at', '')}"
        )
        ttk.Label(top, text=meta).pack(anchor=tk.W, pady=(6, 0))

        score_frame = ttk.LabelFrame(window, text="得分拆解", padding=8)
        score_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 8))
        ttk.Label(score_frame, text=self._build_score_breakdown_text(product), wraplength=1020).pack(anchor=tk.W)

        conclusion_frame = ttk.LabelFrame(window, text="推荐结论", padding=8)
        conclusion_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 8))
        ttk.Label(conclusion_frame, text=self._build_selection_conclusion(product, snapshots), wraplength=1020).pack(anchor=tk.W)
        ttk.Label(conclusion_frame, text=f"风险提示: {self._build_product_risk_text(product, snapshots)}", wraplength=1020).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(conclusion_frame, text=f"进入策略: {self._build_entry_strategy_text(product, snapshots)}", wraplength=1020).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(conclusion_frame, text=f"原始推荐理由: {product.get('reason', '--')}", wraplength=1020).pack(anchor=tk.W, pady=(4, 0))

        review_frame = ttk.LabelFrame(window, text="评论痛点", padding=8)
        review_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 8))
        ttk.Label(review_frame, text=self._build_review_pain_text(detail.get("review_insight", {})), wraplength=1020).pack(anchor=tk.W)

        trend_frame = ttk.LabelFrame(window, text="趋势观察", padding=8)
        trend_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 8))
        ttk.Label(trend_frame, text=self._build_trend_insight_text(snapshots), wraplength=1020).pack(anchor=tk.W)
        ttk.Label(
            trend_frame,
            text=self._build_trend_confidence_text(snapshots),
            wraplength=1020,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(6, 0))

        chart_frame = ttk.LabelFrame(window, text="趋势图", padding=8)
        chart_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 8))
        self._render_product_trend_chart(chart_frame, snapshots)

        columns = ("time", "price", "rating", "reviews", "bought", "rank", "deal")
        table_height = min(max(len(snapshots), 1), 8)
        table_frame = ttk.Frame(window)
        table_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=table_height)
        headings = {
            "time": "采集时间",
            "price": "价格",
            "rating": "评分",
            "reviews": "评论数",
            "bought": "近月购买量",
            "rank": "自然排名",
            "deal": "促销",
        }
        widths = {
            "time": 160,
            "price": 80,
            "rating": 80,
            "reviews": 100,
            "bought": 120,
            "rank": 100,
            "deal": 80,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor=tk.W)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        if len(snapshots) > table_height:
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.X, expand=True)

        for row in snapshots:
            tree.insert(
                "",
                tk.END,
                values=(
                    self._format_datetime(row.get("snapshot_at")),
                    self._format_money(row.get("price")),
                    self._format_decimal(row.get("rating"), digits=1),
                    self._format_integer(row.get("review_count")),
                    self._format_integer(row.get("monthly_bought")),
                    self._format_integer(row.get("organic_rank")),
                    self._format_yes_no(row.get("is_deal")),
                ),
            )

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, text=f"历史快照: {len(snapshots)} 条").pack(side=tk.LEFT)
        ttk.Button(footer, text="关闭", command=window.destroy).pack(side=tk.RIGHT)
        self.set_status(f"商品详情已加载: {product.get('asin', '')}")

    def _render_product_trend_chart(self, parent: ttk.Frame, snapshots: list[dict]) -> None:
        if not snapshots:
            ttk.Label(parent, text="暂无历史快照").pack(anchor=tk.W)
            return
        if len(snapshots) < 2:
            ttk.Label(parent, text="当前仅有 1 条采集记录，暂无法形成趋势分析").pack(anchor=tk.W)
            return

        try:
            import pandas as pd
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            import matplotlib.dates as mdates
        except Exception as e:
            ttk.Label(parent, text=f"趋势图加载失败: {str(e)}").pack(anchor=tk.W)
            return

        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False

        df = pd.DataFrame(snapshots)
        df["采集时间"] = pd.to_datetime(df.get("snapshot_at"), errors="coerce")
        df = df.dropna(subset=["采集时间"]).sort_values("采集时间")
        if len(df) < 2:
            ttk.Label(parent, text="当前有效采集时间少于 2 条，暂无法形成趋势分析").pack(anchor=tk.W)
            return

        times = list(df["采集时间"])
        chart_rows = df.to_dict("records")

        series = [
            ("价格", "price"),
            ("评分", "rating"),
            ("评论数", "review_count"),
            ("近月购买量", "monthly_bought"),
            ("自然排名", "organic_rank"),
        ]

        fig = Figure(figsize=(10.5, 4.8), dpi=100)
        axes = fig.subplots(2, 2)
        flat_axes = axes.flatten()
        twin_axes = []

        self._plot_metric(flat_axes[0], times, chart_rows, [series[0]])
        flat_axes[0].set_title("价格")

        twin_axis = self._plot_dual_metric(flat_axes[1], times, chart_rows, series[2], series[3])
        if twin_axis is not None:
            twin_axes.append(twin_axis)
        flat_axes[1].set_title("评论 / 购买量")

        self._plot_metric(flat_axes[2], times, chart_rows, [series[1]])
        flat_axes[2].set_title("评分")
        flat_axes[2].set_ylim(0, 5)

        self._plot_metric(flat_axes[3], times, chart_rows, [series[4]])
        flat_axes[3].set_title("自然排名（越小越好）")

        for axis in flat_axes:
            axis.grid(True, alpha=0.25)
            axis.tick_params(axis="x", labelrotation=25)
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
            handles, labels = axis.get_legend_handles_labels()
            if handles and not getattr(axis, "_combined_legend", False):
                axis.legend(handles, labels, fontsize=8)
        for axis in twin_axes:
            axis.grid(False)

        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.X, expand=False)

    def _plot_metric(
        self,
        axis,
        times: list,
        snapshots: list[dict],
        metrics: list[tuple[str, str]],
        color: str | None = None,
    ) -> list:
        handles = []
        for label, key in metrics:
            values = []
            for row in snapshots:
                value = row.get(key)
                try:
                    values.append(float(value) if value is not None and value != "" else None)
                except (TypeError, ValueError):
                    values.append(None)
            if any(value is not None for value in values):
                if color:
                    line = axis.plot(times, values, marker="o", linewidth=1.6, label=label, color=color)[0]
                else:
                    line = axis.plot(times, values, marker="o", linewidth=1.6, label=label)[0]
                handles.append(line)
        return handles

    def _plot_dual_metric(
        self,
        axis,
        times: list,
        snapshots: list[dict],
        left_metric: tuple[str, str],
        right_metric: tuple[str, str],
    ):
        left_handles = self._plot_metric(axis, times, snapshots, [left_metric], color="#1f77b4")
        axis.set_ylabel(left_metric[0])

        right_axis = axis.twinx()
        right_handles = self._plot_metric(right_axis, times, snapshots, [right_metric], color="#ff7f0e")
        right_axis.set_ylabel(right_metric[0])

        handles = left_handles + right_handles
        if handles:
            axis.legend(handles, [handle.get_label() for handle in handles], fontsize=8)
            axis._combined_legend = True
        return right_axis

    def _open_recommendation_window(self, rows: list[dict], csv_path: Path) -> None:
        window = tk.Toplevel(self)
        window.title("推荐榜单")
        window.geometry("1100x520")
        window.transient(self)

        columns = ("rank", "asin", "title", "score", "price", "rating", "reviews", "bought", "reason")
        tree = ttk.Treeview(window, columns=columns, show="headings")
        headings = {
            "rank": "排名",
            "asin": "ASIN",
            "title": "商品标题",
            "score": "综合得分",
            "price": "价格",
            "rating": "评分",
            "reviews": "评论数",
            "bought": "近月购买量",
            "reason": "推荐理由",
        }
        widths = {
            "rank": 50,
            "asin": 100,
            "title": 260,
            "score": 80,
            "price": 70,
            "rating": 60,
            "reviews": 80,
            "bought": 100,
            "reason": 320,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor=tk.W)

        scrollbar = ttk.Scrollbar(window, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

        for index, row in enumerate(rows, start=1):
            tree.insert(
                "",
                tk.END,
                values=(
                    index,
                    row.get("asin", ""),
                    row.get("title", ""),
                    row.get("total_score", ""),
                    row.get("price", ""),
                    row.get("rating", ""),
                    row.get("review_count", ""),
                    row.get("monthly_bought", ""),
                    row.get("reason", ""),
                ),
            )

        footer = ttk.Frame(window, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, text=f"已导出: {csv_path}").pack(side=tk.LEFT)
        ttk.Button(footer, text="关闭", command=window.destroy).pack(side=tk.RIGHT)
        self.set_status(f"推荐榜单已加载，共 {len(rows)} 条")

    def _format_summary(self, title: str, summary: dict) -> str:
        parts = [title]
        for key in ["解析商品数", "有效入库候选", "有效商品数", "过滤商品数", "入库商品数"]:
            if key in summary:
                parts.append(f"{key}: {summary[key]}")
        reasons = summary.get("过滤原因") or {}
        if reasons:
            reason_text = "；".join(f"{reason} {count}" for reason, count in reasons.items())
            parts.append(f"过滤原因: {reason_text}")
        return "\n".join(parts)

    def _format_warehouse_sync_summary(self, summary: dict) -> str:
        parts = [
            "分析仓库同步完成",
            f"总行数: {summary.get('总行数', 0)}",
            f"DuckDB: {summary.get('DuckDB', '')}",
            f"Parquet: {summary.get('Parquet', '')}",
        ]
        tables = summary.get("同步表") or {}
        if tables:
            parts.append("同步表:")
            parts.extend(f"- {name}: {rows} 行" for name, rows in tables.items())
        return "\n".join(parts)

    def _format_review_import_summary(self, title: str, summary: dict) -> str:
        parts = [title]
        for key in ["解析评论数", "有效评论数", "过滤评论数", "写入/更新评论数", "生成洞察商品数"]:
            if key in summary:
                parts.append(f"{key}: {summary[key]}")
        asins = summary.get("涉及 ASIN") or []
        if asins:
            parts.append("涉及 ASIN: " + "、".join(asins))
        reasons = summary.get("过滤原因") or {}
        if reasons:
            reason_text = "；".join(f"{reason} {count}" for reason, count in reasons.items())
            parts.append(f"过滤原因: {reason_text}")
        if not reasons and summary.get("过滤评论数") == 0:
            parts.append("过滤原因: 无")
        return "\n".join(parts)

    def _format_review_html_summary(self, title: str, summary: dict) -> str:
        parts = [title]
        for key in ["解析评论数", "有效评论数", "过滤评论数"]:
            if key in summary:
                parts.append(f"{key}: {summary[key]}")
        if summary.get("输出文件"):
            parts.append(f"输出文件: {summary['输出文件']}")
        if summary.get("过滤明细"):
            parts.append(f"过滤明细: {summary['过滤明细']}")
        asins = summary.get("涉及 ASIN") or []
        if asins:
            parts.append("涉及 ASIN: " + "、".join(asins))
        reasons = summary.get("过滤原因") or {}
        if reasons:
            reason_text = "；".join(f"{reason} {count}" for reason, count in reasons.items())
            parts.append(f"过滤原因: {reason_text}")
        elif summary.get("过滤评论数") == 0:
            parts.append("过滤原因: 无")
        parts.append("下一步: 使用“导入评论”导入输出文件，生成评论洞察。")
        return "\n".join(parts)

    def _select_save_folder(self) -> str:
        """选择保存文件夹"""
        import os

        # 创建选择保存文件夹对话框
        dialog = tk.Toplevel(self)
        dialog.title("选择保存文件夹")
        dialog.geometry("400x350")
        dialog.transient(self)
        dialog.grab_set()

        # 对话框居中
        self._center_dialog(dialog, 400, 350)

        # 添加标签
        ttk.Label(dialog, text="请选择保存结果的文件夹:").pack(pady=10)

        # 创建文件夹树
        tree_frame = ttk.Frame(dialog)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # 文件夹树
        folder_tree = ttk.Treeview(tree_frame, show="tree")
        folder_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 添加滚动条
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=folder_tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        folder_tree.configure(yscroll=scrollbar.set)

        # 加载文件夹结构
        self._load_folder_tree(folder_tree, "数据结果")

        # 选中默认文件夹
        folder_tree.selection_set("数据结果")

        # 变量存储选中的文件夹
        selected_folder = ["数据结果"]

        # 文件夹选择事件
        def on_folder_select(event):
            selected = folder_tree.selection()
            if selected:
                selected_folder[0] = selected[0]

        folder_tree.bind("<<TreeviewSelect>>", on_folder_select)

        # 新建文件夹按钮
        def create_new_folder():
            # 获取当前选中的文件夹
            current_folder = selected_folder[0]

            # 创建新建文件夹对话框
            new_folder_dialog = tk.Toplevel(dialog)
            new_folder_dialog.title("新建文件夹")
            new_folder_dialog.geometry("300x100")
            new_folder_dialog.transient(dialog)
            new_folder_dialog.grab_set()

            # 对话框居中
            self._center_dialog(new_folder_dialog, 300, 100)

            # 添加标签和输入框
            ttk.Label(new_folder_dialog, text="文件夹名称:").pack(pady=10)
            entry = ttk.Entry(new_folder_dialog, width=30)
            entry.insert(0, "新建文件夹")
            entry.pack(pady=5)

            # 添加按钮
            def on_ok():
                folder_name = entry.get().strip()
                if folder_name:
                    folder_path = os.path.join(current_folder, folder_name)
                    if not os.path.exists(folder_path):
                        try:
                            os.makedirs(folder_path)
                            # 重新加载文件夹树
                            for item in folder_tree.get_children():
                                folder_tree.delete(item)
                            self._load_folder_tree(folder_tree, "数据结果")
                            # 选中新建的文件夹
                            new_folder_id = folder_path
                            # 尝试选中新建的文件夹
                            try:
                                folder_tree.selection_set(new_folder_id)
                                selected_folder[0] = new_folder_id
                            except Exception:
                                # 如果选中失败，保持当前选择
                                pass
                            new_folder_dialog.destroy()
                        except Exception as e:
                            self.set_status(f"新建文件夹失败: {str(e)}")
                    else:
                        self.set_status("文件夹已存在")
                else:
                    self.set_status("文件夹名称不能为空")

            button_frame = ttk.Frame(new_folder_dialog)
            button_frame.pack(pady=10)
            ttk.Button(button_frame, text="确定", command=on_ok).pack(side=tk.LEFT, padx=5)
            ttk.Button(button_frame, text="取消", command=new_folder_dialog.destroy).pack(side=tk.LEFT, padx=5)

            # 绑定回车键
            new_folder_dialog.bind("<Return>", lambda event: on_ok())

        ttk.Button(dialog, text="新建文件夹", command=create_new_folder).pack(pady=5)

        # 确认按钮
        def on_confirm():
            dialog.destroy()

        button_frame = ttk.Frame(dialog)
        button_frame.pack(side=tk.BOTTOM, pady=10)
        ttk.Button(button_frame, text="确定", command=on_confirm).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="取消", command=lambda: selected_folder.__setitem__(0, None) or dialog.destroy()).pack(side=tk.LEFT, padx=5)

        # 等待对话框关闭
        dialog.wait_window()

        return selected_folder[0]

    def _load_folder_tree(self, tree: ttk.Treeview, folder_path: str, parent: str = "") -> None:
        """加载文件夹树"""
        import os

        # 创建当前文件夹节点
        node_id = folder_path if not parent else f"{parent}/{os.path.basename(folder_path)}"
        tree.insert(parent, tk.END, node_id, text=os.path.basename(folder_path))

        # 加载子文件夹
        try:
            for item in os.listdir(folder_path):
                item_path = os.path.join(folder_path, item)
                if os.path.isdir(item_path):
                    self._load_folder_tree(tree, item_path, node_id)
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        """设置状态"""
        self._status.configure(text=text or "")

    def _on_close(self) -> None:
        """关闭窗口"""
        self.destroy()
