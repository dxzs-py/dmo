# 19 Redis 与 Memcached / MySQL / PostgreSQL 对比

> 从架构、数据模型、持久化、性能、事务、高可用、场景、选型、迁移、组合十个维度系统对比 Redis、Memcached、MySQL、PostgreSQL，并给出 16 个场景化推荐与三库协同架构。

## 零基础前置认知

**这篇在讲什么**：把四个"存数据的东西"摆到同一张桌上横向比较——Redis（内存 KV）、Memcached（内存纯 KV）、MySQL（磁盘关系库）、PostgreSQL（磁盘关系库 + 扩展），从架构/数据模型/持久化/性能/事务/高可用/场景/选型/迁移/组合十个维度看清"谁在什么场景下该上场"。读完你能：面对"这个功能用什么库"给出有依据的结论（不再拍脑袋），读懂生产环境"Redis 缓存 + MySQL 主存 + PG 分析/向量"三库协同样式，也能讲清楚从 Memcached/MySQL/PG 迁到 Redis 要付出什么代价。*本篇是无代码对比篇，以 md 内联示例呈现，不设 codes 目录。*

> 辅助类比：像"选交通工具"——**Redis** 是"地铁"（高频短平快——缓存/排行榜/计数器，内存贵所以容量小）；**Memcached** 是"共享单车"（最轻量，但只载一个人——纯 KV、重启全没）；**MySQL** 是"私家车"（标准、皮实，载关系数据）；**PostgreSQL** 是"房车"（空间大能改装——JSONB/向量/时序扩展，运维也最重）。选库 = 先看的里程（数据量）多大、要不要"过夜"（持久化）、要不要"搬大件"（复杂查询/事务）。

**基础名词集**：

| 名词 | 一句话定义 | 与相邻概念的关系 |
|------|-----------|----------------|
| KV 存储 | 只按 key 存取 value 的存储 | Redis 的 value 是复杂类型；Memcached 的 value 只有字符串 |
| 关系数据库（RDBMS） | 表 + 行 + 列，SQL 查询，ACID 事务 | MySQL / PG 属此类，可扩展 JSON/向量类型 |
| ACID | 原子性 / 一致性 / 隔离性 / 持久性 | Redis 只有弱原子（MULTI/EXEC 无回滚），RDBMS 完整支持 |
| MVCC | 多版本并发控制（读写互不阻塞） | MySQL InnoDB 用 Undo 段；PG 用 xmin/xmax 元组版本 |
| 持久化 | 数据落地磁盘，重启不丢 | Redis RDB/AOF；MySQL Redo+Binlog；PG WAL；Memcached 无 |
| 分片/集群 | 把数据切到多节点存储 | Redis Cluster 16384 槽；MySQL 不原生（需中间件）；PG 用 Citus 扩展 |
| 向量检索 | 按语义相似度搜索（Embedding） | MySQL 9.0 才引入 VECTOR 类型；PG 用 pgvector；Redis 用 RediSearch / Vector Sets |

> 区分/注意：**"缓存"和"数据库"不是互斥**——Redis 既能当缓存也能做短期主存（AOF），但别指望它替代 MySQL/PG 做千万级持久业务；**Memcached 与 Redis 最大的差别不是性能而是数据类型和持久化**（Memcached 只有字符串、重启即丢；Redis 9 种类型 + RDB/AOF）；**MySQL 的 VECTOR 类型 9.0 才引入**（8.x 没有，用时先确认版本线）；**RedisGraph 自 2024 年起停止开发**（进入维护模式），图场景不要押注 Redis 模块化扩展。

## 学习目标

- 理解 Redis、Memcached、MySQL、PostgreSQL 在架构、数据模型、持久化、事务模型上的根本差异
- 掌握四类数据库的 QPS、延迟、容量量级与适用边界
- 能够基于 16 个典型业务场景给出选型决策并说明理由
- 了解从 Memcached/MySQL/PG 迁移到 Redis 的注意事项与改造点
- 设计 Redis + MySQL + PostgreSQL 三库协同的生产架构

## 前置知识

- 通读 [01 Redis 架构与核心概念](../01_Redis架构与核心概念/01_Redis架构与核心概念.md)、[02 数据类型与底层实现](../02_数据类型与底层实现/02_数据类型与底层实现.md)、[03 持久化机制](../03_持久化机制/03_持久化机制.md)
- 了解 [05 复制与高可用](../05_复制与高可用/05_复制与高可用.md)、[06 集群与分片](../06_集群与分片/06_集群与分片.md)、[07 事务与 Lua 脚本](../07_事务与Lua脚本/07_事务与Lua脚本.md)
- 基本的 MySQL InnoDB / PostgreSQL MVCC 概念
- 了解 [09 缓存模式与实战](../09_缓存模式与实战/09_缓存模式与实战.md) 中 Cache Aside 模式

---

## 一、架构对比：进程模型与存储介质

### 1.1 进程与线程模型

| 维度 | Redis | Memcached | MySQL | PostgreSQL |
|------|-------|-----------|-------|-----------|
| 进程模型 | 单进程多线程（主线程+IO线程+后台线程） | 单/多进程多线程（worker 线程池） | 单进程多线程（mysqld） | 单进程多进程（每连接一进程/线程池） |
| 命令执行线程 | 单线程（命令串行执行） | 多线程（命令并发执行，需加锁） | 多线程（8.0+ InnoDB 并发） | 单线程执行单条 SQL（每会话独立） |
| 网络 IO | 6.0+ IO 多线程，命令执行单线程 | 多线程 IO 与执行 | 多线程 IO | 多进程 IO |
| 并发控制 | 单线程无锁，事件循环 epoll | 分桶锁（item lock） | 行锁/表锁/间隙锁 | MVCC + 表锁/行锁 |
| 内存屏障 | 无锁访问，缓存友好 | 多核竞争 item lock | 缓冲池 LRU 加锁 | Buffer 加锁精细 |

### 1.2 存储介质与容量

| 维度 | Redis | Memcached | MySQL | PostgreSQL |
|------|-------|-----------|-------|-----------|
| 主存储介质 | 内存（可持久化到磁盘） | 内存（重启即丢） | 磁盘（Buffer Pool 缓存） | 磁盘（shared_buffers 缓存） |
| 单节点容量上限 | 通常 100GB 以内（受内存价格约束） | 通常 32GB 以内 | TB 级 | TB 级 |
| 冷热分层 | 不擅长（需配合磁盘 swap 但性能急剧下降） | 不支持 | 支持（InnoDB buffer pool） | 支持（shared_buffers + OS cache） |
| 内存利用率 | 高（SDS/listpack 紧凑编码） | 中（slab allocator，有浪费） | 中（页对齐 16KB） | 中（8KB 页） |

### 1.3 C/S 通信协议

| 维度 | Redis | Memcached | MySQL | PostgreSQL |
|------|-------|-----------|-------|-----------|
| 协议 | RESP3（二进制安全） | 自定义文本+二进制 | MySQL Protocol（二进制） | Frontend/Backend Protocol |
| 默认端口 | 6379 | 11211 | 3306 | 5432 |
| 客户端缓存 | 6.0+ 原生支持（Tracking 模式） | 不支持 | 不支持 | 不支持 |
| 推送能力 | RESP3 原生推送（Pub/Sub、invalidation） | 不支持 | 不支持 | LISTEN/NOTIFY |

### 1.4 架构图对比

```
Redis 单线程架构（6.0+ IO 多线程）：
┌─────────────────────────────────────────────┐
│  主线程（命令执行）                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ 命令解析 │→│ 命令执行 │→│ 结果返回 │  │
│  └──────────┘  └──────────┘  └──────────┘  │
│        ▲                                     │
│  IO 线程池（读/写 socket 并发）              │
│  IO Thread1 │ IO Thread2 │ ... │ IO ThreadN │
└─────────────────────────────────────────────┘

Memcached 多线程架构：
┌─────────────────────────────────────────────┐
│  主线程（监听+分发连接）                      │
│        │ 分发                                │
│  Worker1 │ Worker2 │ ... │ WorkerN           │
│  （各自处理命令，共享 slab，分桶锁）          │
└─────────────────────────────────────────────┘

MySQL InnoDB 架构：
┌─────────────────────────────────────────────┐
│  连接线程池 → SQL 解析/优化 → 执行器          │
│        ↓                                     │
│  InnoDB Buffer Pool（内存缓存页）            │
│        ↓                                     │
│  Redo Log / Undo Log / Binlog（磁盘）        │
│        ↓                                     │
│  表空间文件（.ibd）                           │
└─────────────────────────────────────────────┘

PostgreSQL 架构：
┌─────────────────────────────────────────────┐
│  Postmaster 主进程                           │
│   │ fork                                    │
│  Backend1 │ Backend2 │ ... （每连接一进程）  │
│   ↓ 共享                                    │
│  shared_buffers / WAL / 表文件               │
└─────────────────────────────────────────────┘
```

详见 [01 Redis 架构与核心概念](../01_Redis架构与核心概念/01_Redis架构与核心概念.md)。

---

## 二、数据模型对比

### 2.1 模型抽象层级

| 维度 | Redis | Memcached | MySQL | PostgreSQL |
|------|-------|-----------|-------|-----------|
| 数据模型 | KV（值可为 9 种复杂类型） | 纯 KV（值仅为字符串） | 关系表（行+列） | 关系表 + JSON/数组/向量/范围/枚举类型 |
| Schema | Schema-less（动态） | Schema-less | Schema-strong（DDL 强约束） | Schema-strong（DDL+继承+分区） |
| 复杂对象 | Hash/Stream/JSON（RedisJSON） | 不支持 | 行记录 + JSON 列 | 行 + JSONB + 复合类型 |
| 索引 | RediSearch 二级索引（模块） | 不支持 | B+Tree/Hash/全文/空间 | BTree/Hash/GiST/GIN/BRIN/向量 |
| 关系运算 | 不支持 JOIN | 不支持 JOIN | JOIN/子查询/视图/存储过程 | JOIN/CTE/窗口函数/递归查询 |

### 2.2 数据类型丰富度

| 类型 | Redis | Memcached | MySQL | PostgreSQL |
|------|-------|-----------|-------|-----------|
| 字符串 | String（512MB） | String（1MB） | VARCHAR/TEXT/BLOB | TEXT/BYTEA/VARCHAR |
| 列表 | List（listpack/quicklist） | 不支持 | 自建表 | 数组类型 `[]` |
| 哈希 | Hash（listpack/hashtable） | 不支持 | 行记录天然 | 行记录 / hstore 扩展 |
| 集合 | Set（intset/hashtable） | 不支持 | 自建表 | 数组去重 |
| 有序集合 | ZSet（listpack/skiplist） | 不支持 | 自建表+索引 | 自建表+索引 |
| 位图 | Bitmap | 不支持 | BIGINT 位运算 | BIT 类型 / BITString |
| 基数估计 | HyperLogLog | 不支持 | 不支持 | 不支持（需扩展） |
| 地理 | Geo（基于 ZSet） | 不支持 | POINT/POLYGON + SPATIAL | POINT/POLYGON + PostGIS |
| 消息流 | Stream（5.0+） | 不支持 | 不支持（需 Kafka） | 不支持（需 Kafka） |
| 时序 | RedisTimeSeries（模块） | 不支持 | TimescaleDB 扩展 | TimescaleDB 扩展 |
| 图 | RedisGraph（模块，**2024 起停止开发**） | 不支持 | 不支持 | Apache AGE 扩展 |
| 向量 | RediSearch VECTOR（模块） | 不支持 | 9.0 VECTOR（8.x 无） | pgvector 扩展 |
| JSON | RedisJSON（模块） | 不支持 | JSON 类型 | JSONB 类型 |

详见 [02 数据类型与底层实现](../02_数据类型与底层实现/02_数据类型与底层实现.md) 与 [16 Redis 向量存储与 RAG 应用](../16_Redis向量存储与RAG应用/16_Redis向量存储与RAG应用.md)。

### 2.3 命令示例：同类操作的不同表达

```bash
# Redis：用户信息存储
HSET user:1001 name "张三" age 28 city "北京"
HGET user:1001 name
HGETALL user:1001

# Memcached：仅能存字符串
set user:1001 0 0 64
{"name":"张三","age":28,"city":"北京"}
get user:1001
# 客户端需自行解析 JSON，无法只取某个字段
```

```sql
-- MySQL：关系建模
CREATE TABLE users (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(64) NOT NULL,
    age TINYINT,
    city VARCHAR(32),
    INDEX idx_city (city)
) ENGINE=InnoDB;
INSERT INTO users (name, age, city) VALUES ('张三', 28, '北京');
SELECT name FROM users WHERE id = 1001;
```

```sql
-- PostgreSQL：关系建模 + JSONB
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    age SMALLINT,
    city TEXT,
    extra JSONB
);
INSERT INTO users (name, age, city, extra)
VALUES ('张三', 28, '北京', '{"tags":["vip","active"]}');
-- JSONB 支持索引（GIN），可高效查询嵌套字段
CREATE INDEX idx_extra ON users USING GIN (extra);
SELECT name FROM users WHERE extra @> '{"tags":["vip"]}';
```

---

## 三、持久化对比

### 3.1 持久化机制

| 维度 | Redis | Memcached | MySQL | PostgreSQL |
|------|-------|-----------|-------|-----------|
| 机制 | RDB 快照 + AOF 日志 + 混合 | 无（纯内存） | Redo Log + Binlog + Undo Log | WAL + Checkpoint |
| 写入方式 | RDB fork 子进程，AOF 追加 | 不写入 | 顺序写 Redo + 二阶段提交 Binlog | 顺序写 WAL + Buffer 刷脏 |
| 同步策略 | always/everysec/no | N/A | innodb_flush_log_at_trx_commit=0/1/2 | synchronous_commit=on/off/remote_write/local |
| 数据安全 | everysec 最多丢 1 秒 | 重启全丢 | 双 1 配置不丢 | on + WAL 副本不丢 |
| 恢复速度 | RDB 快（秒级），AOF 慢 | 不可恢复 | 秒~分钟级 | 秒~分钟级 |
| 在线备份 | BGSAVE / 重写 AOF | 不支持 | mysqldump / xtrabackup | pg_dump / pg_basebackup |
| 时间点恢复（PITR） | 不支持 | 不支持 | Binlog 回放 | WAL 归档回放 |

### 3.2 Redis 持久化原理回顾

```
Redis RDB（快照）：
触发：save 配置 / BGSAVE / SHUTDOWN / 主从同步
方式：fork 子进程 → COW 写时复制 → 生成 dump.rdb
优点：紧凑、恢复快
缺点：fork 大内存慢，两次快照间数据可能丢

Redis AOF（日志）：
触发：每条写命令追加到 aof 文件
同步：appendfsync always/everysec/no
重写：BGREWRITEAOF 自动压缩历史命令
优点：数据安全（everysec 最多丢 1s）
缺点：文件大、恢复慢

Redis 混合持久化（4.0+，默认开启）：
AOF 文件 = RDB 全量 + 增量 AOF 命令
兼顾恢复速度与数据安全
```

详见 [03 持久化机制](../03_持久化机制/03_持久化机制.md)。

### 3.3 MySQL/PG 持久化原理

```sql
-- MySQL InnoDB 双 1 配置（最强持久化）
SET GLOBAL innodb_flush_log_at_trx_commit = 1;  -- 每事务刷 Redo
SET GLOBAL sync_binlog = 1;                       -- 每事务刷 Binlog
-- 丢失风险：0；性能：较低；适用：金融级数据

-- PostgreSQL WAL 同步提交
ALTER SYSTEM SET synchronous_commit = 'on';      -- 默认，每事务等 WAL 刷盘
ALTER SYSTEM SET wal_level = 'replica';          -- 至少 replica 才能做 PITR
SELECT pg_reload_conf();
```

### 3.4 持久化选型建议

| 场景 | Redis 选型 | MySQL/PG 选型 |
|------|-----------|--------------|
| 纯缓存（容忍丢失） | 关闭持久化（save ""） | N/A |
| 缓存 + 会话 | RDB only（恢复快） | N/A |
| 队列/计数器（弱一致） | AOF everysec | N/A |
| 业务持久化（不允许丢） | 不推荐（用 MySQL/PG） | 双 1 / sync_commit=on |
| 金融强一致 | 不推荐 | MySQL 双 1 + 半同步复制 |

---

## 四、性能对比

### 4.1 QPS 量级与延迟

| 指标 | Redis | Memcached | MySQL | PostgreSQL |
|------|-------|-----------|-------|-----------|
| 单节点 QPS（小包） | 10万~50万 | 10万~50万 | 1千~1万 | 1千~1万 |
| 单节点 QPS（复杂查询） | 5万~10万 | N/A | 100~1千 | 100~1千 |
| 单次延迟（本地） | 0.05~0.5ms | 0.05~0.5ms | 1~10ms | 1~10ms |
| 单次延迟（含网络） | 0.5~2ms | 0.5~2ms | 2~20ms | 2~20ms |
| 批量 Pipeline | 100万+ | 100万+ | 1万~10万（batch insert） | 1万~10万（COPY） |
| 写入吞吐（顺序写） | 10万/秒 | 10万/秒 | 1万~5万/秒 | 1万~5万/秒 |
| 范围查询 | 不擅长（ZSet 范围） | 不支持 | 优秀（B+Tree） | 优秀（BTree/GIN） |

### 4.2 性能瓶颈差异

```
Redis 瓶颈：
├── 内存容量（受物理内存限制）
├── 单核 CPU（命令串行）
├── 网络 IO（10Gbps 网卡约 100 万 QPS 上限）
└── 持久化 fork（大内存 fork 阻塞）

Memcached 瓶颈：
├── 内存容量
├── 分桶锁竞争
└── 无持久化（重启全丢）

MySQL 瓶颈：
├── 磁盘 IO（随机读）
├── Buffer Pool 命中率
├── 锁等待（行锁/间隙锁）
└── 复杂查询 CPU

PostgreSQL 瓶颈：
├── 磁盘 IO
├── shared_buffers 命中率
├── 长事务（VACUUM 阻塞）
└── 连接数（每连接一进程）
```

### 4.3 性能基准测试

```bash
# Redis 基准测试
redis-benchmark -h 127.0.0.1 -p 6379 -n 100000 -c 50 -t set,get,incr -q
# 输出示例：
# SET: 120481.92 requests per second
# GET: 145984.05 requests per second
# INCR: 138888.90 requests per second

# Pipeline 模式（10 命令/批）
redis-benchmark -h 127.0.0.1 -p 6379 -n 100000 -c 50 -P 10 -t set -q
# SET: 850340.31 requests per second

# Memcached 基准测试
memtier_benchmark -s 127.0.0.1 -p 11211 --protocol=memcache_text \
  -n 100000 -c 50 --ratio=1:1

# MySQL 基准测试（sysbench）
sysbench oltp_read_write --mysql-host=127.0.0.1 --mysql-port=3306 \
  --mysql-user=root --mysql-password=xxx --mysql-db=test \
  --tables=10 --table-size=100000 --threads=50 --time=60 run

# PostgreSQL 基准测试（pgbench）
pgbench -h 127.0.0.1 -p 5432 -U postgres -c 50 -j 4 -T 60 test
```

详见 [11 性能调优与监控](../11_性能调优与监控/11_性能调优与监控.md)。

### 4.4 延迟构成对比

```
Redis 单次读：
客户端 → 网络 RTT（0.5ms）→ 命令解析（<0.01ms）→ 内存查找（<0.01ms）→ 返回
总延迟 ≈ 0.5ms（瓶颈是网络）

MySQL 单次读（命中 Buffer Pool）：
客户端 → 网络 RTT（0.5ms）→ SQL 解析（0.5ms）→ 执行计划（0.5ms）→ B+Tree 查找（0.1ms）→ 返回
总延迟 ≈ 1.5ms（瓶颈是 SQL 处理）

MySQL 单次读（未命中 Buffer Pool）：
... → 磁盘随机读（5~10ms）→ 返回
总延迟 ≈ 10ms（瓶颈是磁盘 IO）

PostgreSQL 单次读（未命中 shared_buffers）：
总延迟 ≈ 10ms（同 MySQL，瓶颈是磁盘 IO）
```

---

## 五、事务对比

### 5.1 事务模型对比

| 维度 | Redis | Memcached | MySQL InnoDB | PostgreSQL |
|------|-------|-----------|-------------|-----------|
| 事务支持 | MULTI/EXEC（弱事务） | 不支持 | 完整 ACID | 完整 ACID |
| 原子性 | 命令全部执行或不执行（EXEC 前）；运行时错误不回滚 | N/A | 完全支持（ROLLBACK） | 完全支持（ROLLBACK） |
| 隔离级别 | 单线程天然隔离 | N/A | RU/RC/RR/Serializable | RU/RC/RR/Serializable |
| 默认隔离 | 串行（单线程） | N/A | RR（可重复读） | RC（读已提交） |
| MVCC | 不支持 | N/A | 支持（Undo 段） | 支持（xmin/xmax） |
| 锁粒度 | 无锁（单线程）+ WATCH 乐观锁 | 分桶锁 | 行锁/间隙锁 | 行锁/表锁/咨询锁 |
| 死锁检测 | 不存在死锁 | N/A | 自动检测+回滚 | 自动检测+回滚 |
| 嵌套事务 | 不支持 | N/A | SAVEPOINT | SAVEPOINT |
| 分布式事务 | 不支持 | N/A | XA | 两阶段提交（pg_twophase） |

### 5.2 Redis 事务的局限

```bash
# Redis 事务：MULTI/EXEC 不支持回滚
MULTI
SET k1 v1
INCR k1      # 运行时错误（字符串不能 INCR）
SET k2 v2
EXEC
# 结果：k1 被设为 v1（INCR 失败但事务继续），k2 被设为 v2
# 没有 ROLLBACK，需要业务自行处理

# 用 Lua 脚本实现原子复杂逻辑（推荐）
EVAL "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end" 1 lock:key myid
```

详见 [07 事务与 Lua 脚本](../07_事务与Lua脚本/07_事务与Lua脚本.md)。

### 5.3 MySQL/PG 事务示例

```sql
-- MySQL 转账事务（InnoDB，默认 RR）
START TRANSACTION;
UPDATE accounts SET balance = balance - 100 WHERE id = 1;
UPDATE accounts SET balance = balance + 100 WHERE id = 2;
-- 任意一条失败 → ROLLBACK，所有改动撤销
COMMIT;
-- 或 ROLLBACK;
```

```sql
-- PostgreSQL 转账事务（默认 RC）
BEGIN;
UPDATE accounts SET balance = balance - 100 WHERE id = 1;
UPDATE accounts SET balance = balance + 100 WHERE id = 2;
COMMIT;
-- 或 ROLLBACK;

-- PG 的 MVCC 特性：长事务不阻塞读
-- VACUUM 清理死元组，长事务会阻碍 VACUUM
SELECT pg_current_snapshot();
```

### 5.4 事务场景选型

| 场景 | 推荐 | 原因 |
|------|------|------|
| 转账/订单创建 | MySQL/PG | 强 ACID，支持回滚 |
| 库存扣减 | MySQL（行锁）+ Redis（预扣减） | Redis 缓冲瞬时流量 |
| 多 key 原子操作 | Redis Lua 脚本 | 单线程天然原子 |
| 复杂业务回滚 | MySQL/PG | 需要完整 ROLLBACK |

---

## 六、高可用对比

### 6.1 高可用方案

| 维度 | Redis | Memcached | MySQL | PostgreSQL |
|------|-------|-----------|-------|-----------|
| 主从复制 | 主从异步复制（PSYNC2） | 不支持 | 主从异步/半同步 | 流复制（同步/异步） |
| 自动故障转移 | Sentinel（哨兵） | 不支持（客户端故障转移） | MHA / Orchestrator / Group Replication | Patroni / repmgr |
| 数据分片 | Cluster（16384 槽） | 客户端分片 | 不原生（中间件 ShardingSphere） | 不原生（Citus 扩展） |
| 多主写入 | 不支持 | 不支持 | Group Replication 多主 | 不支持（BDR 商业版支持） |
| 共识协议 | Gossip（Cluster）/ Raft 类（Sentinel） | 无 | MGR Paxos | Patroni Etcd/Raft |
| 跨数据中心 | 不擅长 | 不支持 | MGR 跨 DC | 流复制跨 DC + 逻辑订阅 |
| 数据强一致 | 不保证（异步复制会丢） | N/A | MGR / 半同步 | 同步复制（synchronous_commit=remote_apply） |

### 6.2 Redis 高可用方案对比

```
Redis 高可用决策树：
                 需要高可用？
                 ├── 否 → 单机
                 └── 是 → 数据量？
                          ├── < 10GB → 需要自动故障转移？
                          │           ├── 是 → Sentinel（3+ 哨兵节点）
                          │           └── 否 → 主从复制
                          └── > 10GB → Cluster（6+ 节点，分片+高可用）
```

| 方案 | 最少节点 | 分片 | 自动故障转移 | 运维复杂度 | 适用规模 |
|------|---------|------|-------------|-----------|---------|
| 主从复制 | 2 | 否 | 否 | 低 | 读写分离 |
| Sentinel | 3+ | 否 | 是 | 中 | 中小规模 |
| Cluster | 6+ | 是 | 是 | 高 | 大规模 |

详见 [05 复制与高可用](../05_复制与高可用/05_复制与高可用.md)、[06 集群与分片](../06_集群与分片/06_集群与分片.md)。

### 6.3 MySQL/PG 高可用方案

```
MySQL 高可用方案对比：
1. 主从异步 + MHA
   - 异步复制（默认）
   - MHA 监控主节点故障，提升从节点
   - 数据丢失风险：丢失未复制的 binlog
2. 半同步复制
   - 至少一个从节点 ACK 后主节点才提交
   - 数据丢失风险：极低
3. Group Replication（MGR）
   - Paxos 协议强一致
   - 单主/多主模式
   - 自动故障转移

PostgreSQL 高可用方案对比：
1. 流复制 + Patroni
   - 异步/同步流复制
   - Patroni 基于 etcd/Zookeeper 自动故障转移
   - 最主流方案
2. 逻辑订阅
   - 跨版本/跨平台复制
   - 支持双向复制（多主写入）
3. Citus
   - 水平分片扩展
   - 适合大规模分析
```

### 6.4 复制延迟对比

| 系统 | 复制方式 | 典型延迟 | 数据丢失风险 |
|------|---------|---------|------------|
| Redis 主从 | 异步 | <1ms | 故障时丢未复制数据 |
| MySQL 异步 | 异步 | <10ms | 丢未复制 binlog |
| MySQL 半同步 | 半同步 | <50ms | 极低（至少 1 从 ACK） |
| MGR | Paxos 同步 | <100ms | 不丢 |
| PostgreSQL 异步 | 流异步 | <10ms | 丢未复制 WAL |
| PostgreSQL 同步 | 流同步 | <100ms | 不丢 |

---

## 七、使用场景对比

### 7.1 适用场景矩阵

| 场景 | Redis | Memcached | MySQL | PostgreSQL |
|------|-------|-----------|-------|-----------|
| 缓存 | ★★★★★ | ★★★★ | ★★ | ★ |
| 会话存储 | ★★★★★ | ★★★ | ★★★ | ★★ |
| 计数器 | ★★★★★ | ★★ | ★★★ | ★★★ |
| 排行榜 | ★★★★★ | ✗ | ★★ | ★★ |
| 消息队列 | ★★★★ | ✗ | ★ | ★ |
| 分布式锁 | ★★★★★ | ★★ | ★★ | ★★★ |
| 全文搜索 | ★★★ | ✗ | ★★ | ★★★★ |
| 向量搜索 | ★★★★ | ✗ | ★★ | ★★★★★ |
| 事务系统 | ✗ | ✗ | ★★★★★ | ★★★★★ |
| 复杂查询 | ✗ | ✗ | ★★★★★ | ★★★★★ |
| 关系建模 | ✗ | ✗ | ★★★★★ | ★★★★★ |
| 时序数据 | ★★★ | ✗ | ★★ | ★★★★（TimescaleDB） |
| 地理位置 | ★★★★ | ✗ | ★★ | ★★★★★（PostGIS） |
| 发布订阅 | ★★★★ | ✗ | ★ | ★★★ |
| 限流 | ★★★★★ | ★★ | ★ | ★ |
| 持久化业务 | ★ | ✗ | ★★★★★ | ★★★★★ |

### 7.2 详细场景分析

#### 场景 1：缓存（Redis 优势）

```bash
# Redis 缓存用户信息（支持字段级访问）
HSET user:1001 name "张三" age 28
HGET user:1001 name                # 仅取字段，高效
EXPIRE user:1001 3600              # TTL 自动过期

# Memcached 只能整体读写
set user:1001 0 3600 64
{"name":"张三","age":28}
get user:1001                       # 必须读整个 JSON
```

#### 场景 2：排行榜（Redis 优势）

```bash
# Redis ZSet 天然支持排序
ZADD leaderboard 100 "user1" 200 "user2" 150 "user3"
ZREVRANGE leaderboard 0 9 WITHSCORES    # Top 10
ZINCRBY leaderboard 50 "user1"          # 加分
ZRANK leaderboard "user1"               # 排名

# MySQL 实现相同功能（性能差）
# SELECT * FROM leaderboard ORDER BY score DESC LIMIT 10;
# 需要 B+Tree 索引，且无法高效增量更新
```

#### 场景 3：复杂查询（MySQL/PG 优势）

```sql
-- 多表 JOIN + 聚合，Redis 完全无法实现
SELECT u.name, COUNT(o.id) AS order_count, SUM(o.amount) AS total
FROM users u
LEFT JOIN orders o ON u.id = o.user_id
WHERE u.created_at >= '2026-01-01'
GROUP BY u.id
HAVING total > 1000
ORDER BY total DESC
LIMIT 100;
```

#### 场景 4：向量搜索（PG 略优，Redis 也可）

```bash
# Redis 向量检索（RediSearch 模块）
FT.CREATE idx ON HASH PREFIX 1 doc: SCHEMA content VECTOR HNSW 6 DIM 128 TYPE FLOAT32 DISTANCE_METRIC L2

# 用 -x 从文件读入 128 维 float32 二进制作为 content 字段
python3 -c "import numpy as np, sys; sys.stdout.buffer.write(np.zeros(128, dtype=np.float32).tobytes())" > /tmp/vec128.bin
redis-cli -x HSET doc:1 content < /tmp/vec128.bin

# 查询向量同样从文件读入
FT.SEARCH idx "*=>[KNN 10 @content $vec]" PARAMS 2 vec < /tmp/vec128.bin SORTBY __content_score
```

```sql
-- PostgreSQL pgvector 扩展
CREATE EXTENSION vector;
CREATE TABLE docs (id BIGSERIAL PRIMARY KEY, content vector(128));
CREATE INDEX ON docs USING hnsw (content vector_l2_ops);
SELECT id, content <-> '[0.1, 0.2, ...]'::vector AS distance
FROM docs
ORDER BY content <-> '[0.1, 0.2, ...]'::vector
LIMIT 10;
```

详见 [16 Redis 向量存储与 RAG 应用](../16_Redis向量存储与RAG应用/16_Redis向量存储与RAG应用.md)。

### 7.3 不适用场景

| 系统 | 不擅长 | 原因 |
|------|--------|------|
| Redis | 持久化业务数据、复杂查询、大数据量 | 内存成本高、无 JOIN、容量受限 |
| Memcached | 任何需要持久化的场景 | 重启全丢 |
| MySQL | 大规模分析、向量检索 | 列存弱、向量支持晚 |
| PostgreSQL | 极高 QPS 简单 KV | 单进程模型，连接数受限 |

---

## 八、选型决策矩阵

### 8.1 16 个场景化推荐

| 序号 | 场景 | 首选 | 次选 | 理由 |
|------|------|------|------|------|
| 1 | 缓存（热点数据） | Redis | Memcached | Redis 数据类型丰富，支持持久化与高可用 |
| 2 | 用户会话存储 | Redis | MySQL | Redis 自带 TTL，支持 Hash 结构化存储 |
| 3 | 计数器/统计 | Redis | MySQL | Redis INCR 原子、低延迟，适合高频更新 |
| 4 | 实时排行榜 | Redis | MySQL | ZSet 天然有序，O(logN) 更新与查询 |
| 5 | 消息队列（轻量） | Redis Stream | RabbitMQ | Stream 支持消费者组、ACK、回溯，足够轻量场景 |
| 6 | 分布式锁 | Redis（Redlock） | Zookeeper | Redis SET NX EX + Lua 简单高效 |
| 7 | 向量搜索 | PostgreSQL（pgvector） | Redis（RediSearch） | pgvector 生态成熟，适合持久化向量库 |
| 8 | 全文搜索 | Elasticsearch | PostgreSQL | 关系库全文搜索功能弱，专业场景用 ES |
| 9 | 事务系统 | MySQL | PostgreSQL | InnoDB 事务成熟，金融场景首选 |
| 10 | 复杂分析查询 | PostgreSQL | MySQL | PG 窗口函数、CTE、并行查询更强 |
| 11 | 关系建模 | PostgreSQL | MySQL | PG 支持继承、复合类型、JSONB |
| 12 | 时序数据 | PostgreSQL+TimescaleDB | Redis（RTS） | TimescaleDB 自动分区，适合长期存储 |
| 13 | 地理位置服务 | PostgreSQL+PostGIS | Redis Geo | PostGIS 功能完整，Redis Geo 适合简单场景 |
| 14 | 发布订阅 | Redis Pub/Sub | PostgreSQL LISTEN/NOTIFY | Redis 高吞吐，PG 适合数据库内事件 |
| 15 | API 限流 | Redis | MySQL | Redis 滑动窗口/令牌桶低延迟 |
| 16 | 持久化业务数据 | PostgreSQL/MySQL | Redis | 关系库 ACID 保证，Redis 不适合 |

### 8.2 选型决策流程

```
数据是否需要严格 ACID？
├── 是 → 写入频率？
│       ├── 高 + 关系复杂 → MySQL InnoDB
│       └── 中 + 复杂查询 → PostgreSQL
└── 否 → 数据量与访问频率？
        ├── 海量 + 持久 → MySQL/PG
        ├── 中等 + 高频 → Redis（主存）+ MySQL/PG（备份）
        └── 小 + 极高频 → Redis（纯缓存）

是否需要向量/全文检索？
├── 向量 → 持久化要求？
│        ├── 是 → PostgreSQL + pgvector
│        └── 否 → Redis RediSearch
└── 全文 → 规模？
         ├── 大 → Elasticsearch
         └── 小 → PostgreSQL 全文索引
```

### 8.3 综合选型表

| 业务需求 | 推荐 |
|---------|------|
| 高并发读缓存 | Redis Cluster |
| 金融级事务 | MySQL + MGR / PG 同步复制 |
| 大数据复杂分析 | PostgreSQL + Citus |
| 实时排行榜/计数 | Redis |
| 向量 RAG 应用 | Redis（实时）+ PostgreSQL pgvector（持久化） |
| 简单 KV 缓存（无持久化需求） | Memcached |
| 地理 LBS | PostgreSQL + PostGIS |
| 时序监控 | TimescaleDB |

---

## 九、迁移注意事项

### 9.1 Memcached → Redis 迁移

#### 改造点

```
1. 客户端 SDK 替换
   - pymemcache → redis-py
   - memcached-java-client → jedis/lettuce
   - node-memcached → ioredis/node-redis

2. 数据模型升级
   - 纯字符串 → Hash 结构化存储
   - JSON 字符串 → RedisJSON（如可用）

3. 持久化补足
   - Memcached 无持久化 → Redis AOF everysec
   - 重启不再丢数据

4. 高可用补足
   - Memcached 客户端分片 → Redis Cluster/Sentinel
   - 自动故障转移

5. 命令差异
   - get/set → GET/SET（兼容）
   - 没有原生计数 → INCR/INCRBY
   - 没有列表 → LPUSH/RPUSH
```

#### 迁移代码示例

```python
# 迁移前：Memcached
from pymemcache.client.base import Client
mc = Client(("localhost", 11211))
mc.set("user:1001", '{"name":"张三","age":28}', expire=3600)
data = mc.get("user:1001")

# 迁移后：Redis（利用 Hash 结构化）
import redis
r = redis.Redis(host="localhost", port=6379, decode_responses=True)
r.hset("user:1001", mapping={"name": "张三", "age": "28"})
r.expire("user:1001", 3600)
name = r.hget("user:1001", "name")  # 字段级访问
```

#### 迁移风险

| 风险 | 缓解 |
|------|------|
| Redis 单线程性能不如 Memcached 多线程 | 启用 IO 多线程（`io-threads 4`） |
| 内存利用率差异 | 监控 used_memory_rss，启用 activedefrag |
| 持久化导致延迟波动 | AOF everysec + no-appendfsync-on-rewrite yes |

### 9.2 MySQL → Redis 迁移

#### 改造点

```
1. 数据模型重构
   - 行记录 → Hash（字段映射）
   - 关系表 → Set（ID 集合）
   - 排序字段 → ZSet

2. 事务模型适配
   - MySQL ACID → Redis MULTI/EXEC 或 Lua
   - 不支持回滚 → 业务补偿事务

3. 查询能力降级
   - 无 JOIN → 应用层组装
   - 无复杂 WHERE → 业务预计算
   - 无聚合 → 定时任务预计算

4. 持久化策略
   - MySQL 主存 → Redis 缓存 + MySQL 持久化
   - 或 Redis 主存（容忍数据丢失）

5. 索引重建
   - MySQL 二级索引 → RediSearch FT.CREATE
```

#### 迁移示例：用户表

```sql
-- 原 MySQL 表
CREATE TABLE users (
    id BIGINT PRIMARY KEY,
    name VARCHAR(64),
    age INT,
    city VARCHAR(32),
    score INT,
    INDEX idx_city (city)
);
```

```bash
# 迁移后 Redis 数据结构
# 1. 用户基础信息 → Hash
HSET user:1001 name "张三" age 28 city "北京" score 100

# 2. 城市索引 → Set
SADD city:北京 user:1001

# 3. 排行榜 → ZSet
ZADD leaderboard 100 user:1001

# 4. 二级索引 → RediSearch
FT.CREATE idx:user ON HASH PREFIX 1 user: SCHEMA name TEXT age NUMERIC city TEXT score NUMERIC SORTABLE
FT.SEARCH idx:user "@city:{北京} @age:[20 30]"
```

#### 迁移注意事项

| 风险 | 应对 |
|------|------|
| 数据丢失（Redis 不持久化业务） | 保留 MySQL 主存，Redis 仅缓存 |
| 复杂查询无法实现 | 评估查询是否真需要，或保留 MySQL |
| 事务回滚需求 | 用 Lua 脚本或业务补偿 |
| 内存成本爆炸 | 大数据量保留 MySQL，仅热数据入 Redis |

### 9.3 PostgreSQL → Redis 迁移

#### 改造点

```
1. 关系模型 → KV 模型
   - 表 → 命名空间前缀（user:1001, user:1002）
   - JOIN → 应用层组装或冗余存储
   - 约束 → 应用层校验

2. 高级特性丢失
   - JSONB → Redis Hash 或 RedisJSON
   - 数组类型 → Redis List/Set
   - 递归查询（WITH RECURSIVE） → 不支持
   - 窗口函数 → 不支持

3. 索引能力差异
   - GIN/GiST → RediSearch
   - 部分索引、表达式索引 → 不支持
   - 向量索引 → RediSearch HNSW

4. 事务降级
   - 完整 ACID → MULTI/EXEC（无回滚）
   - SAVEPOINT → 不支持
```

#### 迁移示例：带 JSONB 的用户表

```sql
-- 原 PostgreSQL 表
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    name TEXT,
    extra JSONB
);
CREATE INDEX idx_extra ON users USING GIN (extra);
SELECT * FROM users WHERE extra @> '{"tags":["vip"]}';
```

```bash
# 迁移方案 A：Redis Hash + RedisJSON
JSON.SET user:1001 $ '{"name":"张三","extra":{"tags":["vip"]}}'
JSON.GET user:1001 $.name
# 全文/向量索引：
FT.CREATE idx:user ON JSON PREFIX 1 user: SCHEMA $.name TEXT $.extra.tags TAG

# 迁移方案 B：仅迁移热数据到 Redis，冷数据留 PostgreSQL
# 适合：80/20 法则，热数据量小
```

详见 [17 LangChain v1.4 与 Redis](../17_LangChain_v1.4与Redis/17_LangChain_v1.4与Redis.md) 中关于 Redis 在 AI 应用中替代 PG 向量库的讨论。

---

## 十、组合使用最佳实践

### 10.1 三库协同架构

```
┌──────────────────────────────────────────────────────────────┐
│                       应用层（Django/FastAPI）                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Cache Service│  │ Lock Service │  │ Vector RAG Agent │  │
│  └──────┬───────┘  └──────┬───────┘  └─────────┬────────┘  │
└─────────┼─────────────────┼────────────────────┼────────────┘
          │                 │                    │
          ▼                 ▼                    ▼
   ┌──────────────┐  ┌──────────────┐    ┌──────────────────┐
   │    Redis     │  │    Redis     │    │  Redis / PG      │
   │  缓存层      │  │  分布式锁    │    │  向量检索        │
   │  Session     │  │  限流计数    │    │  Chat History    │
   │  排行榜      │  │  Stream 队列 │    │  Embedding 缓存  │
   └──────┬───────┘  └──────────────┘    └──────────────────┘
          │ Cache Aside（先 DB 后删缓存）
          │ Canal 监听 binlog
          ▼
   ┌──────────────┐              ┌──────────────────┐
   │    MySQL     │              │   PostgreSQL     │
   │  业务主存    │              │   分析/向量      │
   │  ACID 事务   │              │   pgvector       │
   │  订单/账户   │              │   TimescaleDB    │
   └──────────────┘              └──────────────────┘
```

### 10.2 Redis + MySQL 协同（缓存模式）

```python
# Django 中 Cache Aside 实现
import redis
from myapp.models import Product

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def get_product(product_id: int) -> dict:
    cache_key = f"product:{product_id}"
    # 1. 先读缓存
    data = r.hgetall(cache_key)
    if data:
        return data
    # 2. 缓存未命中读 DB
    product = Product.objects.get(pk=product_id)
    data = {"id": str(product.id), "name": product.name, "price": str(product.price)}
    # 3. 写回缓存（带 TTL 防雪崩）
    r.hset(cache_key, mapping=data)
    r.expire(cache_key, 300)
    return data

def update_product(product_id: int, **kwargs):
    # 1. 先更新 DB
    Product.objects.filter(pk=product_id).update(**kwargs)
    # 2. 再删缓存（Cache Aside 推荐策略）
    r.delete(f"product:{product_id}")
```

### 10.3 Redis + PostgreSQL 协同（向量 RAG）

```python
# 实时向量检索：Redis（低延迟，热数据）
# 持久化向量库：PostgreSQL + pgvector（全量数据，长期存储）

import redis
import psycopg2
from pgvector.psycopg2 import register_vector

r = redis.Redis(host="localhost", port=6379, decode_responses=True)
conn = psycopg2.connect("dbname=ragdb user=postgres")
register_vector(conn)

def search_similar(query_vec: list[float], top_k: int = 5):
    # 1. 优先查 Redis 缓存（最近查询过的）
    cache_key = f"vec:{hash(tuple(query_vec))}"
    cached = r.get(cache_key)
    if cached:
        return __import__("json").loads(cached)
    # 2. 查 Redis 向量索引（热数据）
    # 3. 若未命中，查 PostgreSQL pgvector（全量）
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, content, content <-> %s AS dist FROM docs ORDER BY content <-> %s LIMIT %s",
            (query_vec, query_vec, top_k)
        )
        results = cur.fetchall()
    # 4. 写回 Redis 缓存
    r.setex(cache_key, 3600, __import__("json").dumps(results))
    return results
```

详见 [16 Redis 向量存储与 RAG 应用](../16_Redis向量存储与RAG应用/16_Redis向量存储与RAG应用.md)、[17 LangChain v1.4 与 Redis](../17_LangChain_v1.4与Redis/17_LangChain_v1.4与Redis.md)。

### 10.4 三库协同：电商场景

```
电商秒杀场景三库分工：

1. Redis（前置缓冲）
   ├── 商品库存预扣减（INCR/DECR 原子）
   ├── 限流（滑动窗口 ZSet）
   ├── 分布式锁（防止超卖）
   ├── 用户会话（Hash）
   └── 排行榜（ZSet）

2. MySQL（业务主存）
   ├── 订单表（ACID 强一致）
   ├── 用户表（关系建模）
   ├── 商品表（库存真值）
   └── 支付流水（金融级事务）

3. PostgreSQL（分析/向量）
   ├── 商品推荐向量库（pgvector）
   ├── 用户行为时序（TimescaleDB）
   ├── 销售分析报表（窗口函数/CTE）
   └── 商品搜索（GIN 索引）

数据流：
用户请求 → Redis 预扣减库存 → MQ（Stream）→ 异步落库 MySQL
                                  ↓
                          PG 同步分析（CDC）
```

### 10.5 一致性保障策略

| 一致性等级 | 策略 | 适用 |
|-----------|------|------|
| 弱一致 | 先写 Redis，异步写 DB | 日志、统计 |
| 最终一致 | Cache Aside + TTL | 大多数业务 |
| 强一致 | 先写 DB，同步删 Redis + 双删 | 重要业务 |
| 严格一致 | Canal 监听 binlog 异步删 Redis | 金融场景 |

详见 [09 缓存模式与实战](../09_缓存模式与实战/09_缓存模式与实战.md)。

---

## 📖 深入阅读

### 官方文档

- [Redis Documentation](https://redis.io/docs/) — Redis 官方手册
- [Memcached Wiki](https://github.com/memcached/memcached/wiki) — Memcached Wiki
- [MySQL Reference Manual](https://dev.mysql.com/doc/refman/8.4/en/) — MySQL 8.4 LTS 参考手册（8.0 已 EOL）
- [PostgreSQL Documentation](https://www.postgresql.org/docs/) — PostgreSQL 官方文档
- [pgvector](https://github.com/pgvector/pgvector) — PostgreSQL 向量扩展
- [TimescaleDB](https://docs.timescale.com/) — PostgreSQL 时序扩展
- [PostGIS](https://postgis.net/docs/) — PostgreSQL 地理扩展

### 本手册相关章节

- [01 Redis 架构与核心概念](../01_Redis架构与核心概念/01_Redis架构与核心概念.md) — 单线程模型、IO 多线程
- [03 持久化机制](../03_持久化机制/03_持久化机制.md) — RDB/AOF/混合持久化
- [05 复制与高可用](../05_复制与高可用/05_复制与高可用.md) — 主从、Sentinel
- [06 集群与分片](../06_集群与分片/06_集群与分片.md) — Cluster、哈希槽
- [07 事务与 Lua 脚本](../07_事务与Lua脚本/07_事务与Lua脚本.md) — MULTI/EXEC/Lua
- [09 缓存模式与实战](../09_缓存模式与实战/09_缓存模式与实战.md) — Cache Aside、一致性
- [16 Redis 向量存储与 RAG 应用](../16_Redis向量存储与RAG应用/16_Redis向量存储与RAG应用.md) — RediSearch 向量
- [17 LangChain v1.4 与 Redis](../17_LangChain_v1.4与Redis/17_LangChain_v1.4与Redis.md) — Agent Memory

### 扩展资料

- [Redis vs Memcached](https://redis.io/blog/redis-vs-memcached/) — Redis 官方对比
- [MySQL InnoDB Architecture](https://dev.mysql.com/doc/refman/8.4/en/innodb-architecture.html) — InnoDB 架构
- [PostgreSQL MVCC](https://www.postgresql.org/docs/current/mvcc.html) — PG 多版本并发控制

---

## 本章小结

- **架构差异**：Redis 单线程命令执行（6.0+ IO 多线程）；Memcached 多线程分桶锁；MySQL/PG 多线程/多进程 + 锁/MVCC
- **数据模型**：Redis 9 种原生 + 模块扩展；Memcached 仅 String；MySQL/PG 关系表 + 扩展类型（JSONB/向量）
- **持久化**：Redis RDB/AOF/混合；Memcached 无；MySQL Redo+Binlog；PG WAL
- **性能量级**：Redis/Memcached 10万~50万 QPS；MySQL/PG 1千~1万 QPS（复杂查询更低）
- **事务**：Redis 无回滚；MySQL/PG 完整 ACID + MVCC
- **高可用**：Redis Sentinel/Cluster；MySQL MHA/MGR；PG Patroni/流复制
- **场景边界**：Redis 缓存/锁/排行榜/计数；MySQL/PG 持久化业务/复杂查询
- **选型决策**：用 16 场景矩阵 + 决策树，避免单一系统解决所有问题
- **迁移要点**：Memcached→Redis 升级数据结构；MySQL→Redis 重构模型 + 保留 DB；PG→Redis 损失高级特性
- **三库协同**：Redis 缓冲 + MySQL 持久化 + PostgreSQL 分析/向量，是生产推荐架构

---

## 下一步导航

- 学完对比后回看核心原理 → [01 Redis 架构与核心概念](../01_Redis架构与核心概念/01_Redis架构与核心概念.md)
- 学习缓存与一致性细节 → [09 缓存模式与实战](../09_缓存模式与实战/09_缓存模式与实战.md)
- 掌握向量库对比 → [16 Redis 向量存储与 RAG 应用](../16_Redis向量存储与RAG应用/16_Redis向量存储与RAG应用.md)
- 实战三库协同架构 → [15 全栈项目实战](../15_全栈项目实战/15_全栈项目实战.md)
- 命令速查与面试题 → [99 附录](../99_附录/99_附录.md)
