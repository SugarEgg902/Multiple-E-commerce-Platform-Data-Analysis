# 数据存储层设计

**日期：** 2026-05-15  
**状态：** 已批准  
**技术栈：** MySQL · SQLAlchemy 2.x · Alembic

---

## 背景

当前系统用 JSON 文件（`product_cache.py`）做去重缓存，用 CSV 文件（`artifacts.py`）做输出。没有持久化数据库层，无法支持历史追踪、跨平台商品归一、任务状态管理。

本设计引入 MySQL 作为主存储，CSV 变为"从 DB 查询后按需导出"的功能，JSON 缓存废弃。

---

## 表结构

### 1. `global_product` — 跨平台商品归一表

同一款实体商品在不同平台的多个 listing 归入同一条 `global_product`。

```sql
CREATE TABLE global_product (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    canonical_title VARCHAR(512) NOT NULL,
    brand           VARCHAR(128),
    category        VARCHAR(256),
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_brand (brand)
);
```

### 2. `platform_product` — 商品主表（统一商品池）

每行代表某平台上的一个 listing。所有平台共有字段存在此表，价格统一换算为 USD。

```sql
CREATE TABLE platform_product (
    id                  BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    platform            VARCHAR(32)  NOT NULL,
    platform_product_id VARCHAR(128) NOT NULL,
    keyword             VARCHAR(256) NOT NULL,
    title               VARCHAR(512),
    price_usd           DECIMAL(10,2),
    price_original      VARCHAR(64),
    currency            VARCHAR(8),
    rating              DECIMAL(3,2),
    review_count        INT UNSIGNED,
    url                 VARCHAR(1024),
    is_valid            TINYINT(1)      NOT NULL DEFAULT 1,
    global_product_id   BIGINT UNSIGNED,
    match_confidence    DECIMAL(4,3),
    crawl_time          DATETIME        NOT NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_platform_product (platform, platform_product_id),
    INDEX idx_keyword          (keyword),
    INDEX idx_platform         (platform),
    INDEX idx_crawl_time       (crawl_time),
    INDEX idx_global_product   (global_product_id),
    CONSTRAINT fk_product_global FOREIGN KEY (global_product_id)
        REFERENCES global_product(id) ON DELETE SET NULL
);
```

**`platform` 枚举值：** `amazon` · `ebay` · `temu` · `ozon` · `otto` · `allegro` · `tiktokshop` · `cdiscount`

**`global_product_id` 填入时机：** 商品入库后异步触发 TF-IDF 匹配（见第 6 节）。

### 3. `platform_product_detail` — 扩展字段表

平台特有字段存入 `extra` JSON 列，加新平台无需 ALTER TABLE。

```sql
CREATE TABLE platform_product_detail (
    id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    product_id  BIGINT UNSIGNED NOT NULL,
    extra       JSON            NOT NULL,
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_product (product_id),
    CONSTRAINT fk_detail_product FOREIGN KEY (product_id)
        REFERENCES platform_product(id) ON DELETE CASCADE
);
```

**`extra` 各平台字段说明：**

| 平台 | 主要字段 |
|------|---------|
| Amazon | `bsr_rank`, `bsr_category`, `bsr_display`, `monthly_sales_range`, `monthly_sales_estimate`, `monthly_revenue_estimate`, `bullets` |
| eBay | `sold_count`, `condition`, `seller_feedback`, `bullets` |
| Temu | `goods_id`, `sold_count`, `bullets` |
| Ozon | `sku`, `total_sales_estimate`, `total_revenue_estimate`, `breadcrumbs`, `short_characteristics` |
| Otto | `variation_id`, `total_sales_estimate`, `total_revenue_estimate`, `description`, `bullets` |
| Allegro | `condition`, `seller`, `seller_rating`, `category`, `parameters` |
| TikTok Shop | `seller`, `sold_count` |
| Cdiscount | `original_price`, `seller`, `category`, `bullet_points` |

### 4. `platform_product_snapshot` — 历史快照表

每次爬取写一条快照，保留价格、评分随时间的变化轨迹。`global_product` 关系通过 `platform_product` JOIN 获取，不在快照表冗余。

```sql
CREATE TABLE platform_product_snapshot (
    id                  BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    product_id          BIGINT UNSIGNED NOT NULL,
    platform            VARCHAR(32)     NOT NULL,
    platform_product_id VARCHAR(128)    NOT NULL,
    title               VARCHAR(512),
    price_usd           DECIMAL(10,2),
    price_original      VARCHAR(64),
    rating              DECIMAL(3,2),
    review_count        INT UNSIGNED,
    extra               JSON,
    crawl_task_id       BIGINT UNSIGNED,
    snapshotted_at      DATETIME        NOT NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_product_id     (product_id),
    INDEX idx_snapshotted_at (snapshotted_at),
    CONSTRAINT fk_snap_product FOREIGN KEY (product_id)
        REFERENCES platform_product(id) ON DELETE CASCADE
);
```

**查询某 global_product 的历史价格走势：**
```sql
SELECT s.snapshotted_at, s.price_usd, s.rating, p.platform
FROM platform_product_snapshot s
JOIN platform_product p ON s.product_id = p.id
WHERE p.global_product_id = ?
ORDER BY s.snapshotted_at;
```

### 5. `crawl_task` — 爬虫任务表

每次用户发起分析请求创建一条任务，对应 `agent_service.py` 中的 `AgentRun`。

```sql
CREATE TABLE crawl_task (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    platform        VARCHAR(32)  NOT NULL,
    keyword         VARCHAR(256) NOT NULL,
    target_count    SMALLINT UNSIGNED NOT NULL DEFAULT 5,
    status          ENUM('pending','running','done','failed') NOT NULL DEFAULT 'pending',
    products_found  SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      DATETIME,
    finished_at     DATETIME,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status   (status),
    INDEX idx_platform (platform),
    INDEX idx_keyword  (keyword)
);
```

### 6. `analysis_result` — AI 分析结果表

每个商品每次分析写一条，允许多版本（不同模型、不同时间）。

```sql
CREATE TABLE analysis_result (
    id                   BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    product_id           BIGINT UNSIGNED NOT NULL,
    crawl_task_id        BIGINT UNSIGNED,
    core_selling_points  TEXT,
    pros                 JSON,
    cons                 JSON,
    overall              TEXT,
    positioning          TEXT,
    category             VARCHAR(256),
    llm_model            VARCHAR(128),
    created_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_product_id    (product_id),
    INDEX idx_crawl_task_id (crawl_task_id),
    CONSTRAINT fk_analysis_product FOREIGN KEY (product_id)
        REFERENCES platform_product(id) ON DELETE CASCADE,
    CONSTRAINT fk_analysis_task    FOREIGN KEY (crawl_task_id)
        REFERENCES crawl_task(id) ON DELETE SET NULL
);
```

### 7. `review` — 评论表（后期）

```sql
CREATE TABLE review (
    id                  BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    product_id          BIGINT UNSIGNED NOT NULL,
    platform_review_id  VARCHAR(128),
    rating              TINYINT UNSIGNED,
    title               VARCHAR(512),
    body                TEXT,
    author              VARCHAR(256),
    posted_at           DATETIME,
    country             VARCHAR(64),
    helpful_count       INT UNSIGNED DEFAULT 0,
    sentiment           ENUM('positive','negative','neutral'),
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_review (product_id, platform_review_id),
    INDEX idx_product_id (product_id),
    INDEX idx_rating     (rating),
    CONSTRAINT fk_review_product FOREIGN KEY (product_id)
        REFERENCES platform_product(id) ON DELETE CASCADE
);
```

---

## 表关系图

```
global_product (1)
    └──── (N) platform_product (1) ──── (1) platform_product_detail
                    │
                    ├──── (N) platform_product_snapshot
                    │           └── crawl_task (N:1)
                    │
                    ├──── (N) analysis_result
                    │           └── crawl_task (N:1)
                    │
                    └──── (N) review  [后期]
```

---

## global_product 匹配策略（Stage 1 only）

新商品入库后，异步执行 TF-IDF 余弦相似度匹配：

1. 对新商品 `title` 分词（jieba 中文 + 英文 tokenizer）
2. 与已有 `global_product.canonical_title` 计算余弦相似度
3. 相似度 ≥ 0.85 → 归入已有 `global_product`，写入 `match_confidence`
4. 相似度 < 0.85 → 新建 `global_product`，`canonical_title` = 新商品 title

匹配在后台异步执行，不阻塞主爬取/分析流程。`global_product_id` 初始为 NULL，匹配完成后回填。

---

## 与现有代码的对接

| 现有模块 | 变更 |
|---------|------|
| `product_cache.py` | 废弃，去重逻辑改为查询 `platform_product` 表 |
| `artifacts.py` | CSV 写入保留，改为从 DB 查询后导出 |
| `competitor_workflows.py` | 入库写 `platform_product` + `platform_product_detail` + `platform_product_snapshot` + `analysis_result` |
| `agent_service.py` | `AgentRun` 创建时同步写 `crawl_task`，完成时更新 `status` |

新增模块：
- `mp_agent/dao/db.py` — SQLAlchemy engine + session factory
- `mp_agent/dao/models.py` — ORM 模型定义
- `mp_agent/dao/repository.py` — 数据访问层（CRUD 函数）
- `alembic/` — 数据库迁移脚本

---

## 依赖

```
sqlalchemy>=2.0
alembic
pymysql
cryptography   # pymysql 连接 MySQL 8.x 需要
jieba          # TF-IDF 中文分词
scikit-learn   # TfidfVectorizer + cosine_similarity
```
