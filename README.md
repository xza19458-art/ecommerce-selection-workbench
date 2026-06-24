# 本地电商选品分析工作台

面向电商卖家、选品团队和数据运营场景的本地化选品分析系统。项目围绕“采集可控、数据沉淀、趋势复盘、评论洞察、智能辅助”构建，从早期 Tkinter GUI 工具逐步演进为 FastAPI + 轻量 Web + pywebview 桌面壳的一体化工作台，并继续迭代中。

系统支持人工确认下的低频关键词采集、本地 HTML/评论文件导入、MySQL 主库沉淀、DuckDB/Parquet 分析仓库同步、商品池与关键词机会分析、趋势置信度判断、评论痛点归纳，以及内置应用 Agent 的本地数据问答和受控任务操作。

> 当前仓库是持续完善中的开发展示版本。GitHub 会继续更新适合公开展示的源码、文档和开发快照；内部设计讨论、任务看板和路线规划不在公开仓库披露。

## 项目定位

多数中小卖家在选品阶段会把数据分散在浏览器收藏、Excel、手动截图、第三方工具导出和临时文档里。短期看够用，长期会出现三个问题：

- 数据不可追踪：很难回看某个关键词或 ASIN 当时为什么被选中。
- 判断不可复盘：一次页面快照容易受促销、广告位、缺货、季节性和页面波动影响。
- 经验难沉淀：评论痛点、价格带、排名变化和任务记录没有统一结构。

本项目的目标是把这些调研动作转化为本地可维护的数据资产：卖家可以在自己的电脑或内网环境部署一套轻量数据仓库，把候选商品、关键词、评论样本、历史快照和分析结论逐步沉淀下来，用于选品、复盘和趋势分析。

## 应用场景

### 卖家自建本地选品数据仓库

适用于 Amazon、独立站或多平台卖家在本地部署自己的选品数据仓库。关键词搜索页、候选商品、评论样本、任务日志和历史快照进入 MySQL 主库，再同步到 DuckDB/Parquet 做分析查询。

这种方式保留了数据主权：数据库配置、模型 Key、业务数据和采集结果都留在本机；每次采集、导入、过滤和同步都有记录，便于团队回看和审计。

### 新品开发前的市场筛选

围绕若干关键词做低频搜索页采集，沉淀商品池，再从价格带、评分、评论数、近月购买量、自然排名和综合得分中筛出候选 ASIN。

适合回答：

- 哪些关键词下存在需求但竞争壁垒尚可？
- 哪些商品销量信号不错，但评论量还没有形成压倒性壁垒？
- 某个价格带是否过度拥挤，是否存在可切入区间？
- 候选 ASIN 在价格、评分、排名和评论结构上谁更健康？

### 商品趋势和快照复盘

单次页面数据不应直接等同于趋势。本项目支持围绕关键词和商品积累多轮快照，结合价格、评分、评论数、近月购买量和自然排名变化，判断一个商品是持续上升、短期波动、样本不足，还是异常拉升。

这类能力适合做周度/月度选品复盘，也适合把“当时为什么看好这个商品”的依据沉淀下来。

### 评论痛点和产品改良方向分析

低分评论往往比评分均值更能反映进入风险。项目支持导入评论 CSV/JSON，也支持离线解析本地保存的评论页 HTML，把低分评论整理成痛点、风险和改良机会。

适合用于：

- 找出竞品被集中吐槽的问题。
- 判断差评是否来自可改良缺陷。
- 提炼包装、说明书、配件、质量控制和售后策略机会。
- 避免只看星级均值，忽略真实用户不满。

### 运营团队的本地工作台

小团队可以把项目部署在本地或内网电脑上，运营人员通过 Web 页面完成关键词采集、HTML 入库、商品对比、评论导入和任务查看；技术人员可以继续扩展服务层、数据仓库和内部报表。

相比维护分散表格，本项目更适合长期运营：

- 数据结构固定，便于复查。
- 导入前有预览和过滤原因。
- 任务中心记录采集/导入/同步历史。
- Web 页面适合非技术人员使用。
- Python 服务层方便接入内部脚本、BI 或二次开发。

## 架构演进

项目不是一次性脚本，而是按真实业务流程逐步演进的本地数据产品。

### 阶段 1：Tkinter GUI 工具

早期版本以 Tkinter GUI 为主，提供文件管理器式界面，支持：

- 关键词爬取设置和“运行爬取”。
- 本地 HTML 文件查看与选择。
- 入库预览、写入数据库。
- 推荐榜单、商品池、任务中心和关键词机会。
- 评论 HTML 解析、评论导入和评论洞察。
- 趋势图、得分拆解、风险提示和进入策略。

GUI 版本仍保留在源码中，适合了解项目早期形态，也可以作为本地轻量入口使用。

运行方式：

```powershell
cd 2_1
..\.venv\Scripts\python.exe main_gui.py
```

### 阶段 2：FastAPI + 轻量 Web

随着页面和分析维度增加，项目从 GUI 过渡到 FastAPI + 原生 Web 前端。后端统一暴露商品池、推荐榜单、关键词机会、评论洞察、任务中心、导入预览和 Agent 接口；前端保持轻量，不引入 React/Electron 等重型框架。

这一阶段的重点是把“工具窗口”升级为“本地工作台”：更适合扩展页面、组织业务流程，也更利于后续桌面壳和打包。

### 阶段 3：pywebview 桌面壳

当前主要入口是 pywebview 桌面壳：双击启动本地 FastAPI 服务，等待 `/api/health` 就绪后打开桌面窗口加载 Web UI。关闭窗口时后端会优雅停止。

运行方式：

```powershell
cd 2_1
..\.venv\Scripts\python.exe desktop_app.py
```

### 阶段 4：PyInstaller 开发快照

项目已经提供 PyInstaller `onedir` 打包规格，用于生成 Windows 开发快照。当前 EXE 主要作为体验入口和部署参考，完整功能仍以源码运行方式为主。

构建方式：

```powershell
cd 2_1
..\.venv\Scripts\python.exe -m PyInstaller AmazonSelectionWorkbench.spec --noconfirm
```

## 核心能力

- **手动低频采集**：在 Web/GUI 中输入关键词和页数，使用 Selenium/Chrome 保存 Amazon 搜索页 HTML；单次最多 7 页，页间隔 5-10 秒，遇登录、验证码、空页或异常即停止并保留原因。
- **HTML 预览入库**：采集或手动保存的 HTML 先做解析预览，展示有效商品和过滤原因，再由用户确认写入 MySQL。
- **关键词追踪与自动化任务编排**：围绕关键词建立追踪任务，支持到期 dry-run 预览、人工确认执行、任务日志记录和快照积累。
- **商品池与商品对比**：按关键词、ASIN、价格区间、评分、评论数等维度查看商品，并支持多 ASIN 并排对比。
- **推荐榜单与关键词机会**：结合评分、价格、评论数、购买量、自然排名等指标，输出候选品和关键词层面的机会解释。
- **趋势置信度分析**：基于多轮历史快照判断趋势，不把单次页面波动直接当成增长。
- **评论痛点分析**：导入评论样本或离线解析评论页 HTML，聚合低分评论痛点、风险和改良机会。
- **分析仓库**：MySQL 作为主库，DuckDB/Parquet 作为只读分析仓库，用于加速分析查询和后续扩展。
- **内置应用 Agent**：通过统一 tool schema 查询本地数据、解释商品/关键词、创建追踪任务，并对写库或联网类操作执行服务端二次确认。
- **用户设置与安全边界**：支持本地 `settings.json` 管理界面偏好、采集节奏和自定义评分参考层；采集相关参数由服务端按安全边界强制校验，不替换标准评分口径。
- **桌面化与打包**：支持 pywebview 桌面壳和 PyInstaller 开发快照。

## 自动化与内置应用 Agent

自动化能力服务于“减少重复操作”和“保证过程可复盘”，不是无边界的无人值守采集器。

```text
关键词追踪任务
  -> 到期任务 dry-run 预览
  -> 人工确认执行
  -> Selenium 低频采集搜索页 HTML
  -> 阻断/空页自动停止并隔离保存
  -> 本地 HTML 预览入库
  -> MySQL 主库沉淀
  -> DuckDB/Parquet 分析仓库同步
  -> 趋势、推荐、关键词机会和评论痛点复盘
```

内置应用 Agent 是工作台里的自然语言数据助理。用户可以询问“最近哪些关键词机会更好”“某个 ASIN 的风险是什么”“帮我创建一个关键词追踪任务”，Agent 会调用本地工具获取数据或准备操作。

安全边界：

- 只读查询工具可以直接执行。
- 创建任务、修改任务状态、触发采集等操作类工具必须二次确认。
- 触发采集会联网打开浏览器访问 Amazon，确认前不会执行。
- API Key 只保存在本地 `agent.json`，接口返回时只展示掩码状态，不返回原始 Key。
- Agent 只是应用入口之一，核心逻辑仍在后端 service/controller 中。

## 技术架构

```text
手动采集 / 本地 HTML / 评论文件
        |
        v
解析器与导入服务
        |
        v
MySQL 主库（source of truth）
        |
        v
DuckDB / Parquet 分析仓库
        |
        v
FastAPI 服务层
        |
        +--> 轻量 Web 前端
        +--> pywebview 桌面壳
        +--> 内置应用 Agent
```

技术栈：

- Python 3.12
- FastAPI / Uvicorn
- pymysql / pandas / DuckDB / Parquet
- Selenium / webdriver-manager
- Tkinter / pywebview
- 原生 HTML / CSS / JavaScript
- PyInstaller
- OpenAI compatible / Anthropic provider 示例适配

## 目录结构

```text
.
├── 2_1/
│   ├── api/                 # FastAPI 应用
│   ├── core/                # 控制器与核心流程
│   ├── database/            # MySQL schema、迁移脚本和客户端
│   ├── parsers/             # 搜索页和评论页 HTML 解析器
│   ├── scripts/             # 初始化、导入、同步、检查等脚本
│   ├── services/            # 选品、趋势、推荐、评论、仓库、Agent 服务
│   ├── tests/               # 轻量回归测试
│   ├── ui/                  # Tkinter GUI
│   ├── web/                 # 轻量 Web 前端
│   ├── config/              # 示例配置
│   ├── desktop_app.py       # pywebview 桌面入口
│   ├── main_gui.py          # Tkinter GUI 入口
│   └── requirements.txt
├── docs/
│   └── AI_COLLABORATION.md  # 公开版 AI 协作说明
├── .gitignore
└── README.md
```

## 部署准备

建议环境：

- Windows 10/11
- Python 3.12
- MySQL 8.x
- Chrome 浏览器，用于手动关键词采集
- Node.js，可选，仅用于检查前端语法

克隆仓库：

```powershell
git clone https://github.com/xza19458-art/ecommerce-selection-workbench.git
cd ecommerce-selection-workbench
```

创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r 2_1\requirements.txt
```

## 配置本地数据库

复制示例配置：

```powershell
Copy-Item 2_1\config\database.example.json 2_1\config\database.json
Copy-Item 2_1\config\warehouse.example.json 2_1\config\warehouse.json
```

编辑 `2_1\config\database.json`，填入本机 MySQL 信息：

```json
{
  "host": "127.0.0.1",
  "port": 3306,
  "user": "root",
  "password": "your_password",
  "database": "amazon_selection",
  "charset": "utf8mb4"
}
```

创建数据库后初始化表结构：

```powershell
cd 2_1
..\.venv\Scripts\python.exe scripts\init_mysql.py
```

如果要启用分析仓库，保留默认 `warehouse.example.json` 路径即可。同步数据时会在本地生成 `data_warehouse/`，该目录不会提交到 Git。

## 启动方式

### Web 工作台

```powershell
cd 2_1
..\.venv\Scripts\python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000 --reload
```

浏览器访问：

```text
http://127.0.0.1:8000
```

### 桌面壳

```powershell
cd 2_1
..\.venv\Scripts\python.exe desktop_app.py
```

### 传统 GUI

```powershell
cd 2_1
..\.venv\Scripts\python.exe main_gui.py
```

常用页面/窗口包括运行爬取、关键词追踪、本地 HTML 入库、商品池、商品对比、商品详情、推荐榜单、关键词机会、评论痛点、任务中心和 AI 助手。

## 采集与准备本地数据

仓库不附带真实 Amazon 页面、评论样本或数据库内容。可以按下面方式准备自己的本地数据。

### Web 手动运行爬取

1. 启动 Web 工作台后，进入“运行爬取”页面。
2. 输入关键词和页数，点击执行前确认联网访问。
3. 系统会打开本机 Chrome，按真实搜索页流程翻页并保存 HTML。
4. 正常页面保存到 `2_1/html/<关键词>/`；登录、验证码、空页等异常页面会隔离保存到 `_blocked`，方便复查原因。
5. 保存完成后，进入“本地 HTML 入库”，先预览有效商品和过滤原因，再决定是否写入数据库。

手动爬取只负责保存 HTML，不会自动写库、不会自动评分，也不会绕过平台风控。

### 低频关键词追踪

```powershell
cd 2_1
..\.venv\Scripts\python.exe scripts\run_keyword_tracking_scheduler.py
```

默认是到期任务预览；需要真实执行时再显式确认执行参数。采集结果会进入任务日志，后续可用于商品快照和趋势分析。

### 导入已有本地文件

1. 将手动保存的搜索结果 HTML 放到 `2_1/html/`。
2. 在 Web 页面进入“本地 HTML 入库”，先预览，再确认写入数据库。
3. 将评论 CSV/JSON 放到本地目录，通过“评论导入”页面预览并导入。
4. 如已有评论页 HTML，可先运行离线解析脚本：

```powershell
cd 2_1
..\.venv\Scripts\python.exe scripts\parse_review_html.py html\reviews\B010NE2XPC_reviews.html --asin B010NE2XPC --output 数据结果\reviews.csv
```

本项目不提供验证码绕过、账号池、代理池规避或高频采集能力。采集和导入相关流程都应保留人工确认、日志记录、页数上限、页间隔和失败停止条件。

## 配置内置应用 Agent

复制示例配置：

```powershell
Copy-Item 2_1\config\agent.example.json 2_1\config\agent.json
```

编辑 `agent.json`，填入自己的模型服务地址和 API Key。也可以参考 `agent.anthropic.example.json` 使用 Anthropic 原生接口。

内置 Agent 默认适合做本地数据问答和任务辅助。涉及创建追踪、修改状态或触发采集的操作类工具，后端会要求二次确认。

## 开发快照与 Releases

[GitHub Releases](https://github.com/xza19458-art/ecommerce-selection-workbench/releases) 会放置可下载的 Windows 开发快照，版本号采用类似 `v0.1.0-dev.YYYYMMDD` 的形式。当前打包形态是 PyInstaller `onedir`，因此 Release 附件以 zip 形式提供，解压后运行：

```text
AmazonSelectionWorkbench/AmazonSelectionWorkbench.exe
```

注意：

- Release 附件是当前开发快照，便于快速体验桌面入口。
- EXE 仍需要本机 MySQL、配置文件和运行环境配合，完整功能以源码部署为准。
- 打包产物不包含真实密钥、本地数据库、采集结果或业务数据。
- 如果需要二次开发、排错或使用最新功能，建议直接从源码运行。

## 基础检查

无数据库也可以先做轻量检查：

```powershell
git diff --check
node --check 2_1\web\app.js
.\.venv\Scripts\python.exe -m py_compile 2_1\api\app.py 2_1\desktop_app.py 2_1\main_gui.py
```

部分测试不依赖真实数据库，可直接运行：

```powershell
cd 2_1
..\.venv\Scripts\python.exe tests\test_trend_analysis.py
..\.venv\Scripts\python.exe tests\test_llm_provider.py
..\.venv\Scripts\python.exe tests\test_agent_tools.py
..\.venv\Scripts\python.exe tests\test_agent_chat.py
```

完整健康检查需要本地 MySQL 配置和数据：

```powershell
cd 2_1
..\.venv\Scripts\python.exe scripts\smoke_check.py
```

## 本地文件与隐私

以下内容只应存在于本机，不应提交到仓库：

- `.venv/`
- `2_1/config/database.json`
- `2_1/config/warehouse.json`
- `2_1/config/agent.json`
- `2_1/config/translation.json`
- `2_1/config/settings.json`
- `2_1/html/`
- `2_1/reviews/`
- `2_1/data_warehouse/`
- `2_1/数据结果/`
- `2_1/logs/`
- `dist/` 和 `build/`

仓库只提供示例配置和源码。数据库密码、API Key、Amazon 账号信息、真实业务数据和采集结果都应保存在本地。

## 持续完善

项目仍在持续迭代。公开仓库会同步适合对外展示的稳定源码、示例配置、部署说明和开发快照；具体产品路线、内部评审、设计讨论和未公开创意会保留在私有工作区。

## 作品集亮点

- 从 Tkinter GUI 到 FastAPI Web，再到 pywebview 桌面壳和 PyInstaller 快照的产品演进路径。
- MySQL 主库与 DuckDB/Parquet 分析仓库的分层设计。
- Selenium 手动采集、HTML 预览入库和低频快照追踪之间的边界设计。
- 商品池、关键词机会、评论痛点和趋势置信度的业务建模。
- 自动化任务编排与内置应用 Agent 的组合。
- 操作类 Agent 工具必须二次确认的安全边界。
- 使用 Codex / Claude 辅助实现、审查、文档整理和版本发布，同时保留人类负责人终审与隐私边界。
