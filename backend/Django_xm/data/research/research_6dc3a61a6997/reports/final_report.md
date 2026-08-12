# Redis 核心数据结构深度研究报告

## 摘要

Redis（Remote Dictionary Server）是一个基于内存的高性能键值存储系统，其强大之处在于**丰富的数据类型体系**。本报告基于知识库文档（`data/uploads/user_1_redis/` 教程系列，特别是 00 总览与导读、02 数据类型与底层实现）与 Redis 官方文档、官方 GitHub 源码等外部权威资料，系统梳理了 Redis 的**五大基础数据类型**（String、List、Hash、Set、ZSet）、**扩展数据类型**（Bitmap、HyperLogLog、Geo、Stream、Bitfield）、每种类型的**底层实现**（SDS、dict、quicklist、intset、skiplist、listpack）、**编码转换机制**与**典型应用场景**，并补充了 Redis 7.x/8.0 的最新演进。

**核心结论**：Redis 数据类型的设计遵循"**小数据用紧凑编码、大数据用完整结构**"的内存优化哲学，并通过**单向编码转换**在内存占用与操作性能之间动态权衡；五大基础类型 + 扩展类型共同构成覆盖缓存、消息队列、排行榜、统计分析、地理检索等全场景的数据结构工具箱。

---

## 一、知识库概述与研究来源

### 1.1 知识库文档体系

知识库中 Redis 教程系列（`data/uploads/user_1_redis/`）由两份源文档（原版 96.5KB + 增强版 157.9KB）重构为 21 篇结构化文档。与"核心数据结构"主题直接相关的文档包括：

| 文档 | 主题 | 关系 |
|---|---|---|
| **00 总览与导读** | 全局认知、学习路径、术语表 | 提供术语表（SDS/listpack/skiplist/Stream 等）与文档地图 |
| **02 数据类型与底层实现** | String/List/Hash/Set/ZSet/Bitmap/HLL/Geo/Stream + 编码转换 | **最核心文档** |
| 01 架构与核心概念 | 单线程模型、IO 多线程、RESP3 | 性能前提背景 |
| 08 发布订阅与消息队列 | Pub/Sub、Stream、消费者组 | Stream 应用延伸 |
| 15 全栈项目实战 | 秒杀、Feed 流、排行榜 | 类型应用落地 |

> ⚠️ **检索限制说明**：本次研究通过 `search_knowledge_base` 检索，命中内容均来自 00 号文档（术语表、文档地图）；02 号文档正文未能被检索工具命中，其细节由外部权威资料（redis.io 官方文档与源码）补充交叉验证，报告中已分别标注来源。

### 1.2 知识库关键原文（术语表，出处 00_总览与导读.md）

> SDS — Simple Dynamic String，Redis 自定义字符串（02）
> listpack — Redis 7.0 紧凑列表编码（替代 ziplist）（02）
> skiplist — 跳表，ZSet 底层结构（02）
> Stream — Redis 5.0 消息流数据类型，支持消费者组（08）
> 消费者组 — Stream 消费组，支持 ACK 与待处理消息（08）
> 大 Key — 单 key 内存占用过大（>10KB Hash/List）（11）

> 02 数据类型与底层实现 — String/List/Hash/Set/ZSet/Bitmap/HLL/Geo/Stream + 编码转换（核心原理）

> L1 认知：理解 Redis 单线程模型、**9 大数据类型**、持久化原理（01-04）

---

## 二、五大基础数据类型

### 2.1 String（字符串）— 一切的基础

**定义**：Redis 最基础的数据类型，表示二进制安全的字节序列，最大长度 512MB，可存储文本、整数、浮点数、序列化对象等（[redis.io](https://redis.io/technology/data-structures)）。

**核心命令**：`SET`/`GET`、`MSET`/`MGET`、`INCR`/`DECR`/`INCRBYFLOAT`（原子自增自减）、`SETNX`（分布式锁）、`SETEX`（带过期）、`APPEND`、`GETRANGE`/`SETRANGE`、`STRLEN`。

**内部编码**（`OBJECT ENCODING` 可见）：
- `int`：值可解析为 64 位整数时直接存储为 long；
- `embstr`：长度 ≤ 44 字节时，SDS 头与内容在一次 malloc 中分配（嵌入式）；
- `raw`：长度 > 44 字节时，SDS 头与内容分离分配。

**底层结构**：SDS（Simple Dynamic String）——详见 3.1 节。

**应用场景**：缓存（Cache Aside 等缓存模式，知识库 09）、计数器（点赞/访问量/限流）、分布式锁（`SETNX` + Redlock，知识库 10）、Session 存储、验证码/令牌（配合 TTL）。

### 2.2 List（列表）— 顺序与队列

**定义**：按插入顺序排序的字符串列表，针对**头尾元素的增删**做了专门优化（[redis.io](https://redis.io/technology/data-structures)）。

**核心命令**：`LPUSH`/`RPUSH`、`LPOP`/`RPOP`、`BLPOP`/`BRPOP`（阻塞弹出）、`LRANGE`、`LINDEX`、`LTRIM`（裁剪）、`LLEN`。

**内部编码**：`quicklist`（双向链表，节点内嵌 listpack，Redis 7.0+）——详见 3.3 节。

**应用场景**：消息队列（`LPUSH`+`BRPOP` 阻塞队列，知识库 08 消息队列方向）、时间线/Feed 流（`LTRIM` 裁剪）、最新 N 条记录、任务队列。

### 2.3 Hash（哈希）— 对象存储

**定义**：字符串字段到字符串值的映射（类似 HashMap），适合存储字段较少、结构不深嵌的对象（[redis.io](https://redis.io/docs/latest/develop/data-types/compare-data-types)）。**Redis 7.4+ 支持字段级过期**。

**核心命令**：`HSET`/`HGET`/`HMGET`、`HDEL`、`HLEN`、`HINCRBY`（字段级计数）、`HSCAN`、`HGETALL`。

**内部编码**：
- `listpack`（小 Hash，默认 entry ≤ 512 且字段/值 ≤ 64 字节）；
- `hashtable`（大 Hash，底层为 dict）——详见 3.2 节。

**应用场景**：存储对象/实体（用户信息、商品、配置项）、会话数据聚合、Hash 分桶优化小对象内存（官方推荐的内存优化模式）。

### 2.4 Set（集合）— 去重与运算

**定义**：无序的唯一字符串集合，添加、删除、成员检查均为 O(1)，支持交集、并集、差集运算（[redis.io](https://redis.io/docs/latest/develop/data-types)）。

**核心命令**：`SADD`/`SREM`/`SISMEMBER`、`SMEMBERS`、`SCARD`、`SINTER`/`SUNION`/`SDIFF`、`SRANDMEMBER`/`SPOP`。

**内部编码**：
- `intset`（全部成员为整数且 ≤ 512 个）；
- `listpack`（字符串成员且数量 ≤ 512，Redis 7.2+）；
- `hashtable`（其他情况）——详见 3.4 节。

**应用场景**：标签系统、社交关注/共同好友（交集）、去重集合、抽奖（`SRANDMEMBER`）、实时在线用户。

### 2.5 Sorted Set / ZSet（有序集合）— 排行榜之王

**定义**：唯一成员按关联的浮点 score 排序，score 相同时按字典序排列；官方描述为 **Set 与 Hash 的混合体**（元素唯一 + 每个元素映射一个 score）（[redis.io](https://redis.io/docs/latest/develop/data-types/sorted-sets)）。

**核心命令**：`ZADD`/`ZREM`、`ZRANGE`/`ZREVRANGE`、`ZRANGEBYSCORE`、`ZRANK`/`ZREVRANK`、`ZINCRBY`、`ZSCORE`、`ZCARD`。

**内部编码**：
- `listpack`（小 ZSet，元素 ≤ 128 且 member/score ≤ 64 字节）；
- `skiplist`（大 ZSet：跳表 + dict 双结构）——详见 3.5 节。

**应用场景**：排行榜/积分榜（游戏、直播送礼，知识库 15 全栈项目）、时间排序（score=时间戳）、**延迟队列**（score=执行时间）、范围查询、优先级队列、自动补全。

---

## 三、底层数据结构实现原理

### 3.1 SDS（Simple Dynamic String）

Redis 不使用 C 原生 `char[]`，原因是：`strlen()` 为 O(N)、非二进制安全（`\0` 截断）、追加需手动 realloc 有溢出风险。

```c
// Redis 3.2 之前的结构
struct sdshdr {
    int len;      // 当前长度 → O(1) 获取
    int free;     // 预分配未使用空间
    char buf[];   // 柔性数组，保留 \0 兼容 C
};
```

**优化**：Redis 3.2+ 引入 5 种头部（`sdshdr5/8/16/32/64`），按字符串长度选择最小头部，降低短字符串的内存开销。SDS 是 String 类型的基石，也是 Redis 中所有键名及部分内部结构的载体。

### 3.2 Hash 的底层：dict + 渐进式 rehash

- 大 Hash 编码为 `hashtable`，底层是 `dict`（全局键空间、Hash、Set 均依赖它）。
- `dict` 持有**两张哈希表** `ht[0]` 与 `ht[1]`：正常只使用 `ht[0]`，`ht[1]` 专用于 rehash。
- 采用 **SipHash** + 进程随机种子（防哈希碰撞攻击），冲突用链地址法解决。
- **渐进式 rehash**：负载因子超过 1 时扩容为 2 倍；rehash 不一次性完成（避免阻塞事件循环），而是每次 dict 操作迁移少量 bucket（每次 ≤ 10 个），后台定时任务每批 100 步、最多 1ms 推进。过程中读操作需查两张表，写操作只写新表。

### 3.3 List 的底层：quicklist（节点内嵌 listpack）

**演进**：Redis 3.2 之前为 ziplist（小）/ linkedlist（大）双编码 → Redis 3.2 引入 **quicklist**（双向链表，节点内嵌 ziplist，支持 LZF 压缩）→ **Redis 7.0** 节点内部由 ziplist 换成 **listpack**。

```
HEAD <-> [listpack: a,b,c,d] <-> [listpack: e,f,g,h] <-> [listpack: i,j,k] <-> TAIL
```

头尾 push/pop 为 O(1)，中间节点可按 `list-compress-depth` 做 LZF 压缩，被访问的压缩节点透明解压-读取-重压。

### 3.4 Set 的底层：intset / listpack / hashtable

- **intset**（整数集合）：本质是有序整数数组 + 二分查找；插入 O(N)（需 memmove），但内存比 hashtable 省 5~6 倍。插入超出当前位宽（16→32→64 bit）的整数时自动升级位宽。
- 出现字符串成员或超过阈值 → 转 `hashtable`；Redis 7.2+ 字符串小集合（≤ 512）可用 `listpack`。

### 3.5 ZSet 的底层：skiplist + dict 双结构

源码注释明确："ZSETs are ordered sets using **two data structures** to hold the same elements in order to get O(log(N)) INSERT and REMOVE operations"（[t_zset.c](https://github.com/redis/redis/blob/unstable/src/t_zset.c)）：

- **dict**：member → score 的 O(1) 查找（支撑 `ZSCORE`、`ZADD` 更新）；
- **skiplist**：按 score 排序，支撑范围/排名操作（O(log N)）；
- 两个结构**共享同一个 SDS 字符串**以节省内存。

跳表参数：最大层数 32，晋升概率 0.25，节点含 score、member、多层 forward 指针、backward 指针（支撑 `ZREVRANGE`）及 span（支撑 O(log N) rank 计算）。

### 3.6 ziplist vs listpack（Redis 7.0 移除 ziplist 的原因）

| 维度 | ziplist（旧） | listpack（新，7.0+） |
|---|---|---|
| entry 结构 | 记录 prevlen（前一个 entry 长度） | 只记录自身长度 + 末尾 backlen |
| 级联更新 | 中间插入/删除可能级联更新整表 O(N) | **彻底消除级联更新** |
| 头部 | 10 字节（含 zltail 尾指针） | 6 字节固定头 |
| 现状 | Redis 7.0 起被替换 | 5.0 用于 Stream，7.0 替换 Hash/List/ZSet，7.2 扩展到 Set |

---

## 四、编码转换机制（内存优化的核心）

### 4.1 设计哲学

Redis 为每个聚合类型提供**紧凑编码（小数据）**与**完整编码（大数据）**两种形态，在**内存占用**与**操作复杂度**之间动态权衡。知识库术语表确认了这一版本演进事实："listpack — Redis 7.0 紧凑列表编码（替代 ziplist）"。

### 4.2 编码转换路径与默认阈值（Redis 7.x）

| 类型 | 紧凑编码（小） | 完整编码（大） | 触发转换的条件（任一超限即转） |
|---|---|---|---|
| String | `int` / `embstr` | `raw` | 值非整数 / 长度 > 44B（embstr→raw 单向） |
| List | `listpack`（节点内） | `quicklist`（多节点链） | 单节点超过 `list-max-listpack-size`（默认 -2 = 8KB） |
| Hash | `listpack` | `hashtable` | entry > `hash-max-listpack-entries`(512) 或 字段/值 > `hash-max-listpack-value`(64) |
| Set | `intset` / `listpack`(7.2+) | `hashtable` | 出现非整数 / 数量超 `set-max-intset-entries`(512) 等 |
| ZSet | `listpack` | `skiplist` | 元素 > `zset-max-listpack-entries`(128) 或 member/score > `zset-max-listpack-value`(64) |

### 4.3 关键特性

1. **单向转换**：运行时一旦超阈值转为完整编码，即使之后数据缩小也**不会自动转回**紧凑编码（需重启/重写才可能降级）。
2. **官方数据**：小聚合类型采用紧凑编码可比完整结构**节省 5~10 倍内存**（[内存优化文档](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/memory-optimization)）。
3. 可用 `OBJECT ENCODING key` 查看实际编码，用 `CONFIG GET *-max-*` 查看当前阈值。

---

## 五、扩展数据类型

| 类型 | 本质/原理 | 核心用途 | 关键命令 |
|---|---|---|---|
| **Bitmap** | 定义在 String 之上的位操作集合（可表示最多 2^32 比特） | 签到、在线状态、日活统计 | `SETBIT`/`GETBIT`(O(1))、`BITCOUNT`、`BITOP`、`BITPOS` |
| **Bitfield** | 字符串上操作任意位长整数（无符号 1~63 位） | 单个 key 压缩多个计数器 | `BITFIELD`(3.2)、`BITFIELD_RO`(6.0) |
| **HyperLogLog** | 概率型结构，恒定 ~12KB/key 估算基数 | 海量 UV 去重统计 | `PFADD`/`PFCOUNT`/`PFMERGE` |
| **Geo** | 基于 ZSet 的 GeoHash 技术（经纬度线性化） | 附近的人/门店、LBS | `GEOADD`/`GEODIST`/`GEOSEARCH` |
| **Stream** | 追加式日志，自动生成时间有序 ID | 可靠消息队列、事件溯源 | `XADD`/`XREADGROUP`/`XACK`/`XCLAIM`/`XTRIM` |

**Stream 关键机制**（知识库术语表确认：Redis 5.0 消息流数据类型，支持消费者组、ACK 与待处理消息）：生产者 `XADD` 追加，消费者组 `XREADGROUP` 读取，每组维护单一 `last-delivered-id` 游标，每个消费者有独立 PEL（待确认消息列表），`XACK` 确认；崩溃消费者的在途消息可经 `XCLAIM`/`XAUTOCLAIM` 重新指派，实现**至少一次投递**。

---

## 六、Redis 8.0 最新演进

- **新增 8 种内置数据结构**：Vector Set（beta）、JSON、Time Series，以及 5 种概率型结构（Bloom filter、Cuckoo filter、Count-min sketch、Top-K、t-digest）。
- **Hash 新命令**（基于 7.4 字段级过期）：`HGETEX`、`HSETEX`、`HGETDEL`。
- **性能**：30+ 项优化，命令延迟最高降低 87%，吞吐最高 2 倍。
- 核心五类型的**底层编码机制在 7.x 已定型**，8.0 未改动其编码实现。

---

## 七、结论与建议

### 7.1 结论

1. **类型体系**：Redis 核心数据结构 = 五大基础类型（String/List/Hash/Set/ZSet）+ 扩展类型（Bitmap/HyperLogLog/Geo/Stream/Bitfield），知识库将其归纳为"9 大数据类型"。
2. **底层实现**：每种类型都由一套精心设计的底层结构支撑——SDS（字符串）、dict + 渐进式 rehash（Hash/Set 大对象）、quicklist + listpack（List）、intset/listpack（小 Set）、skiplist + dict（ZSet）。
3. **内存哲学**：紧凑编码（listpack/intset）与完整结构（hashtable/skiplist/quicklist）之间的**单向编码转换**，是 Redis 内存优化的核心机制，可比完整结构节省 5~10 倍内存。
4. **版本演进**：Redis 7.0 用 listpack 全面替代 ziplist（消除级联更新），7.2 扩展到 Set；8.0 新增向量/JSON/时序等 8 种结构，进一步扩展数据类型边界。

### 7.2 实践建议

1. **选型**：缓存→String；对象→Hash；排行榜/延迟队列→ZSet；标签/去重→Set；队列→List 或 Stream（需要可靠投递/消费者组时用 Stream）；UV 统计→HyperLogLog；位置服务→Geo。
2. **内存优化**：合理利用紧凑编码阈值（`hash-max-listpack-entries` 等），避免无意义的大对象；注意编码转换**单向性**，写入前评估数据规模。
3. **监控**：用 `OBJECT ENCODING` 观察实际编码，警惕大 Key（知识库定义 >10KB Hash/List）对性能和内存的影响。
4. **演进跟踪**：关注 8.0 新增结构（Vector Set 用于 AI 语义检索、JSON/Time Series 减少模块依赖）。

---

## 八、参考文献

**知识库**
1. 《00_总览与导读.md》— 术语表、文档地图、学习路径（data/uploads/user_1_redis/）
2. 《02 数据类型与底层实现.md》— 数据类型与编码转换（最核心文档，本次检索未命中正文，已由外部资料补充）
3. 《08 发布订阅与消息队列.md》《15 全栈项目实战.md》— 应用场景（间接引用）

**Redis 官方文档**
4. Redis 数据类型总览 — https://redis.io/docs/latest/develop/data-types
5. Redis 类型对比 — https://redis.io/docs/latest/develop/data-types/compare-data-types
6. Redis Sorted Sets — https://redis.io/docs/latest/develop/data-types/sorted-sets
7. Redis Bitmaps — https://redis.io/docs/latest/develop/data-types/strings/bitmaps
8. Redis Streams — https://redis.io/docs/latest/develop/data-types/streams
9. Redis 数据类词汇表 — https://redis.io/glossary/data-structures
10. Redis 内存优化 — https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/memory-optimization
11. Redis 8.0 What's New — https://redis.io/docs/latest/develop/whats-new/8-0
12. Redis Use Cases — https://redis.io/docs/latest/develop/use-cases

**官方源码与设计文档**
13. Redis t_zset.c（ZSet 双结构）— https://github.com/redis/redis/blob/unstable/src/t_zset.c
14. Redis dict.h（哈希表）— https://github.com/redis/redis/blob/unstable/src/dict.h
15. listpack 设计文档（antirez）— https://github.com/antirez/listpack/blob/master/listpack.md
16. Redis 7.0 发布说明 — https://raw.githubusercontent.com/redis/redis/7.0/00-RELEASENOTES

**权威技术文章**
17. Understanding Redis Source Code（dict 渐进式 rehash）— https://wangziqi2013.github.io/article/2023/02/12/redis-notes.html
18. Redis-Internals quicklist（zpoint）— https://github.com/zpoint/Redis-Internals/blob/5.0/Object/list/list.md
19. ByteByteGo: A Crash Course in Redis — https://blog.bytebytego.com/p/a-crash-course-in-redis

---

*本报告由深度研究智能体基于知识库文档分析与多源网络检索（redis.io 官方文档、官方源码、权威技术博客交叉验证）撰写。研究笔记详见 /notes/doc_analysis.md 与 /notes/web_research.md。*
