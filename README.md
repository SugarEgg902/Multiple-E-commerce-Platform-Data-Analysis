# M_P_Agent — 多平台电商竞品分析系统

基于 LLM 的电商竞品分析 Agent，支持 12 大平台自然语言驱动的竞品搜索、数据采集、AI 评论摘要、CSV 导出，并将结果持久化到 MySQL 数据库供历史趋势分析。

---
## 运行实例
![logo](/example_img/97ad7eaadb461f2309bede04d9fb1dae.png)
![logo2](/example_img/a4f22a22ed7387826f37fa02bbdca7dc.png)
![logo2](/example_img/bd422c4f46f7932bacbc6e8c1fb701bb.png)
![logo2](/example_img/d2b72682ac8aaffcabd4ab59dbab78b4.png)
## 功能列表

| 平台 | 采集方式 | 评论摘要 | 缓存 | 备注 |
|---|---|---|---|---|
| Amazon | Playwright + stealth | ✅ | ✅ 3天 | BSR 排名、月销量估算 |
| eBay | Playwright + stealth | ✅ | ✅ 3天 | 月销量估算 |
| Temu | Playwright + stealth | ✅ | ✅ 3天 | 月销量估算 |
| AliExpress | Apify Actor | ✅ | ✅ 3天 | 订单数、折扣率 |
| OZON | Apify Actor | ✅ | ✅ 3天 | 总销量估算 |
| Allegro | Apify Actor | ✅ | ✅ 3天 | 月销量估算 |
| TikTok Shop | Apify Actor | ✅ | ✅ 3天 | 月销量估算 |
| Cdiscount | Apify Actor | ✅ | ✅ 3天 | 月销量估算 |
| OTTO | httpx | ✅ | ✅ 3天 | 总销量估算 |
| MercadoLibre | 本地 MySQL 读取 | ✅ | ✅ 3天 | 30天销量、转化率 |
| Worten | FlareSolverr | ✅ | ✅ 3天 | 葡萄牙/西班牙，EUR/USD 双价 |
| ePrice | FlareSolverr | ✅ | ✅ 3天 | 意大利，EUR/USD 双价，折扣率 |

## 数据库结构

共 7 张表，关系如下：

```
global_product
    └── platform_product (多个平台商品 → 同一个 global_product)
            ├── platform_product_detail  (1:1，平台扩展字段)
            ├── platform_product_snapshot (1:N，历史快照，只追加)
            ├── analysis_result          (1:1，LLM 分析结果)
            └── review                   (1:N，买家评论)

crawl_task  (独立，记录每次爬取任务)
```

### 各表说明

**`platform_product`** — 商品主表，存储最新状态
- 唯一键：`(platform, platform_product_id)`
- 每次爬取执行 UPSERT：同一商品更新，新商品插入
- 字段：`title`、`price_usd`、`price_original`、`rating`、`review_count`、`url`、`crawl_time`

**`platform_product_detail`** — 平台扩展字段，与主表 1:1
- 每次爬取执行 UPSERT，覆盖旧值
- 通过 JSON `extra` 字段存储各平台差异化数据：
  - Amazon：`bsr_rank`、`bsr_category`、`monthly_sales_range`、`monthly_sales_estimate`、`monthly_revenue_estimate`、`bullets`
  - eBay/Temu/Allegro/TikTokShop/Cdiscount：`monthly_sales_estimate`、`monthly_revenue_estimate`
  - OZON/OTTO：`total_sales_estimate`、`total_revenue_estimate`
  - AliExpress：`orders_count`、`total_sales_estimate`、`total_revenue_estimate`、`discount_percentage`、`selling_points`
  - MercadoLibre：从本地 `shadowcraw_db.mercadolibre` 表读取，含 30 天销量、总销量、增长率、转化率等
  - Worten：`price_usd`、`stock_status`、`brand`
  - ePrice：`price_usd`、`original_price_eur`、`discount_pct`、`brand`、`seller`、`stock_status`、`specs`

**`platform_product_snapshot`** — 历史快照，**只追加，不覆盖**
- 每次爬取无论是否命中缓存，都会 INSERT 一行新快照
- 是趋势分析的唯一数据来源
- 字段：`price_usd`、`rating`、`review_count`、`extra`（含销量数据）、`snapshotted_at`

**`analysis_result`** — LLM 分析结果，与主表 1:1
- 每次爬取执行 UPSERT，覆盖旧值
- 字段：`core_selling_points`、`pros`（JSON 数组）、`cons`（JSON 数组）、`overall`、`positioning`、`category`

**`crawl_task`** — 爬取任务记录，独立存在
- 每次用户发起请求时创建，记录平台、关键词、目标数量、状态、耗时
- 状态：`pending` → `running` → `done` / `failed`

**`global_product`** — 跨平台商品去重
- 通过标题语义匹配，将不同平台的同款商品关联到同一条记录
- `platform_product.global_product_id` 外键指向此表

**`review`** — 买家评论，与主表 1:N
- 存储原始评论文本、评分、情感标签（positive/negative/neutral）
- 目前 LLM 摘要直接存入 `analysis_result`，此表供后续精细化分析使用

### 存储策略对比

| 表 | 写入方式 | 历史数据 |
|---|---|---|
| `platform_product` | UPSERT（覆盖） | 只保留最新 |
| `platform_product_detail` | UPSERT（覆盖） | 只保留最新 |
| `platform_product_snapshot` | 纯 INSERT（追加） | 全部保留 |
| `analysis_result` | UPSERT（覆盖） | 只保留最新 |
| `crawl_task` | 纯 INSERT | 全部保留 |
| `review` | UPSERT（按 platform_review_id） | 全部保留 |

### 趋势分析查询示例

```sql
-- 某商品近一个月价格走势
SELECT snapshotted_at, price_usd
FROM platform_product_snapshot
WHERE platform_product_id = 'B0XXXXX'
  AND snapshotted_at >= NOW() - INTERVAL 30 DAY
ORDER BY snapshotted_at;

-- 某关键词下所有商品的月销量变化
SELECT s.snapshotted_at, p.title,
       s.extra->>'$.monthly_sales_estimate' AS sales
FROM platform_product_snapshot s
JOIN platform_product p ON p.id = s.product_id
WHERE p.keyword = 'doogee' AND p.platform = 'amazon'
ORDER BY s.snapshotted_at, p.id;

-- 同一商品在多平台的最新价格对比
SELECT pp.platform, pp.title, pp.price_usd
FROM platform_product pp
JOIN global_product gp ON gp.id = pp.global_product_id
WHERE gp.id = 123
ORDER BY pp.price_usd;
```

---

## 技术栈

- **后端**：FastAPI + Python 3.10+
- **LLM**：GLM-4.6（DashScope）
- **数据库**：MySQL 8 + SQLAlchemy 2.x (async) + Alembic
- **爬虫**：Playwright + playwright-stealth（Amazon/eBay/Temu）、Apify Actor（AliExpress/Ozon/Allegro/TikTokShop/Cdiscount）、httpx（OTTO）、FlareSolverr（Worten/ePrice，Cloudflare 绕过）、本地 MySQL 读取（MercadoLibre）
- **并发保护**：FlareSolverr 请求通过 per-session `asyncio.Lock` + 全局 `asyncio.Semaphore` 双层保护，防止 Chrome 驱动竞态和 OOM
- **前端**：静态 HTML/JS（`frontend/`）

---

## 项目结构

```
mp_agent/
├── application/
│   ├── primary_agent.py        # 对话槽位提取与平台路由
│   ├── competitor_workflows.py # 各平台分析工作流（含缓存逻辑）
│   ├── workflow_registry.py    # 工作流注册表
│   └── agent_service.py        # 会话管理与运行调度
├── domain/
│   └── analysis.py             # LLM 分析行构建
├── infrastructure/
│   ├── amazon.py / ebay.py / temu.py / ozon.py
│   ├── otto.py / allegro.py / tiktokshop.py / cdiscount.py / aliexpress.py / mercadolibre.py
│   ├── worten.py / eprice.py
│   ├── _flaresolverr.py        # FlareSolverr 共享客户端（并发保护 + session 恢复）
│   └── artifacts.py            # CSV 写入与数据库导出
└── dao/
    ├── models.py               # SQLAlchemy ORM 模型（7 张表）
    ├── db.py                   # 数据库连接
    └── repository.py           # 数据访问层

frontend/                       # 单页前端（原生 JS + SSE）
config/
└── config.py                   # API 密钥、数据库地址（已加入 .gitignore）
alembic/                        # 数据库迁移脚本
```

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
APIFY_API_TOKEN = "..."
APIFY_ALIEXPRESS_ACTOR = "bkYbOC0TL11Z6lmBl"
FLARESOLVERR_URL = "http://localhost:8191/v1"   # Worten/ePrice 需要
FLARESOLVERR_MAX_CONCURRENT = "3"               # 最大并发 Chrome 实例数（可选）
EUR_TO_USD = 1.08                               # EUR→USD 汇率（可选，默认 1.08）
# MercadoLibre 从本地 shadowcraw_db 读取，无需额外配置
```

### 3. 初始化数据库

```bash
alembic upgrade head
```

### 4. 启动 FlareSolverr（Worten/ePrice 需要）

```bash
docker run -d --name flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest
```

### 5. 启动服务

```bash
uvicorn app:app --reload
```

访问 `http://localhost:8000` 打开前端界面。

---

## 使用示例

```
帮我分析一下 doogee 在 Amazon 上的竞品，要 10 个
查一下 blackview 在 eBay 的竞品 5 个
分析速卖通上 ulefone 的竞品，5 个
帮我看一下美客多上 blackview 的竞品，5 个
分析 worten 上 blackview 的竞品，5 个
分析 eprice 上 blackview 的竞品，5 个
帮我重新获取 doogee 在 Amazon 的最新数据，10 个   ← 强制刷新
```

---

## 输出文件

每次分析完成后在 `artifacts/` 目录生成 CSV，文件名格式：

```
amazon_{brand}_{count}_{timestamp}.csv
ebay_{brand}_{count}_{timestamp}.csv
aliexpress_{brand}_{count}_{timestamp}.csv
worten_{brand}_{count}_{timestamp}.csv
eprice_{brand}_{count}_{timestamp}.csv
```

---

## 运行测试

```bash
pytest tests/
```
