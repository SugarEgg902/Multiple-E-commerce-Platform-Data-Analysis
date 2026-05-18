# M_P_Agent — 多平台电商竞品分析系统

基于 LLM 的电商竞品分析 Agent，支持 9 大平台关键词搜索、商品数据采集、AI 评论摘要、CSV 导出，并将结果持久化到 MySQL 数据库供历史趋势分析。

---

## 支持平台

| 平台 | 地区 | 销量字段 |
|------|------|----------|
| Amazon | 全球 | 月销量估算、月销售额、BSR 排名 |
| eBay | 全球 | 月销量估算、月销售额 |
| Temu | 全球 | 月销量估算、月销售额 |
| OZON | 俄罗斯 | 月销量估算、月销售额 |
| OTTO | 德国 | 月销量估算、月销售额 |
| Allegro | 波兰 | 月销量估算、月销售额 |
| TikTok Shop | 东南亚/美国 | 月销量估算、月销售额 |
| Cdiscount | 法国 | 月销量估算、月销售额 |
| AliExpress | 全球 | 累计销量、累计销售额、折扣率 |

---

## 技术栈

- **后端**：FastAPI + Python 3.10+
- **LLM**：GLM-4.6（DashScope）
- **数据库**：MySQL 8 + SQLAlchemy 2.x (async) + Alembic
- **爬虫**：Playwright（Amazon/eBay/Temu 等）、Apify Actor（AliExpress）
- **前端**：静态 HTML/JS（`frontend/`）

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置

复制并填写 `config/config.py`：

```python
DASHSCOPE_API_KEY = "..."       # GLM-4.6 API Key
DASHSCOPE_BASE_URL = "..."
MYSQL_URL = "mysql+asyncmy://user:pass@host/dbname"
APIFY_API_TOKEN = "..."         # AliExpress 爬虫所需
APIFY_ALIEXPRESS_ACTOR = "bkYbOC0TL11Z6lmBl"
```

### 3. 初始化数据库

```bash
alembic upgrade head
```

### 4. 启动服务

```bash
uvicorn app:app --reload
```

访问 `http://localhost:8000` 打开前端界面。

---

## 使用方式

在对话框中输入自然语言，例如：

```
帮我分析一下 doogee 在 Amazon 上的竞品，要 10 个
查一下 blackview 在 eBay 的竞品 5 个
分析速卖通上 ulefone 的竞品，5 个
```

支持强制刷新（绕过缓存）：

```
帮我重新获取 doogee 在 Amazon 的最新数据，10 个
给我实时的 blackview eBay 竞品数据
```

---

## 数据库结构

```
platform_product          # 商品主表（最新状态，upsert）
platform_product_detail   # 平台扩展字段（BSR、卖点、折扣率等）
platform_product_snapshot # 历史快照（每次爬取追加，用于趋势分析）
analysis_result           # LLM 分析结果（优缺点、竞品定位）
crawl_task                # 爬取任务记录
global_product            # 跨平台商品去重（语义匹配）
review                    # 买家评论
```

### 趋势分析查询示例

```sql
-- 某商品价格走势
SELECT snapshotted_at, price_usd
FROM platform_product_snapshot
WHERE platform_product_id = 'B0XXXXX'
ORDER BY snapshotted_at;

-- 某关键词下所有商品的月销量变化
SELECT s.snapshotted_at, p.title, s.extra->>'$.monthly_sales_estimate' AS sales
FROM platform_product_snapshot s
JOIN platform_product p ON p.id = s.product_id
WHERE p.keyword = 'doogee' AND p.platform = 'amazon'
ORDER BY s.snapshotted_at, p.id;
```

每隔 3 天爬取一次，一个月可积累约 10 个批次，可分析：价格涨降节奏、销量趋势、评分变化、竞品进出情况。

---

## 输出文件

每次分析完成后在 `artifacts/` 目录生成 CSV，文件名格式：

```
amazon_{brand}_{count}_{timestamp}.csv
ebay_{brand}_{count}_{timestamp}.csv
aliexpress_{brand}_{count}_{timestamp}.csv
...
```

---

## 缓存策略

- 默认缓存有效期：3 天（同平台 + 关键词 + 数量）
- 用户输入含"实时"、"全新数据"、"重新获取"等关键词时自动绕过缓存
- 重新爬取后若数据日期与数据库相同，执行 UPDATE 而非 INSERT（避免重复）
