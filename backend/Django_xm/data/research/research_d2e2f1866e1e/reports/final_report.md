# Redis 核心数据结构深度研究报告

## 摘要

Redis 被官方定位为"数据结构服务器"（a data structure server），其核心竞争力正在于"数据类型即 API"——通过 `redisObject` 对象系统与多编码（encoding）机制，对外提供丰富的数据类型，对内按数据规模自动切换紧凑编码与高性能编码。本报告基于知识库文档分析（`/notes/doc_analysis.md`）与互联网权威资料（`/notes/web_research.md`）深度整合而成，系统阐述：

1. **9 大数据类型**（知识库表述）：String、List、Hash、Set、ZSet、Bitmap、HyperLogLog、GEO、Stream；
2. 各类型的**底层编码实现**：SDS、intset、listpack、ziplist、quicklist、skiplist、hashtable、Radix Tree；
3. **编码转换规则**与配置参数（如 `hash-max-listpack-entries` 等）；
4. Redis 7.x/8.x 的版本演进（listpack 取代 ziplist、Hash 字段级过期、Vector Set 等）；
5. 各数据结构的**应用场景**与选型建议。

> **研究说明**：知识库当前仅收录《00 总览与导读》1 份文档（对应一套 21 篇 Redis 手册的导读篇），其中的数据结构信息为骨架级摘要；详细正文文档（02 数据类型与底层实现、08 消息队列、15 项目实战、99 附录）未收录。因此本报告以知识库骨架信息为锚点，以 Redis 官方文档与权威技术资料为深度补充，并已明确区分信息来源。

---

## 一、Redis 数据类型全景

### 1.1 知识库视角：9 大数据类型

知识库总览文档将 Redis 数据类型概括为 **9 大数据类型**，对应文档 02《数据类型与底层实现》的主题描述：

> "02 数据类型与底层实现 String/List/Hash/Set/ZSet/Bitmap/HLL/Geo/Stream + 编码转换"

即：**String、List、Hash、Set、ZSet、Bitmap、HyperLogLog（HLL）、GEO、Stream**。学习目标 L1 亦要求"理解 Redis 单线程模型、9 大数据类型、持久化原理"。

### 1.2 官方视角：通用型 + 专用型

Redis 官方文档将数据类型划分为两类 [1][5]：

| 类别 | 类型 |
|------|------|
| **通用型（general-purpose）** | String、List、Set、Sorted Set（ZSet）、Hash、Stream |
| **专用型（highly specialized）** | Bitmap、Bitfield、HyperLogLog、Geospatial、JSON、Bloom filter、Count-min sketch、Cuckoo filter、t-digest、Top-K、Time series、Vector set（8.0 beta） |

两者互补：知识库的"9 大数据类型"涵盖了通用 6 类（String/List/Hash/Set/ZSet/Stream）中的全部，加上 Bitmap/HLL/GEO 三个专用型；官方分类另包含 Bitfield、JSON、概率数据结构、时序等模块化扩展。

---

## 二、核心数据类型详解

### 2.1 String（字符串）—— 最基础的构建块

- **特性**：存储任意字节序列，可容纳文本、序列化对象、二进制数据；**单值上限 512MB**；二进制安全（不依赖 `\0` 结尾）；多数操作 O(1) [2][3]。
- **常用命令**：`SET`（支持 EX/PX/NX/XX）、`GET`、`APPEND`、`INCR/DECR/INCRBY/INCRBYFLOAT`（原子计数）、`SETNX`、`GETSET`、`MSET/MGET`、`STRLEN`、`SETRANGE/GETRANGE`。
- **应用场景**：缓存对象/页面片段、计数器（点赞、访问量）、分布式锁（`SET key val NX EX`）、共享 Session、验证码与限流。
- **底层编码**：`int`（整数值直接存储）→ `embstr`（短字符串，一次性分配）→ `raw`（长字符串，SDS）。编码随内容与长度自动切换。

### 2.2 List（列表）—— 双端队列

- **特性**：按插入顺序排列的字符串列表，头尾增删高效；官方明确其"针对在头部或尾部添加/移除少量元素进行了优化"，天然实现队列、栈、双端队列 [1][7]。
- **常用命令**：`LPUSH/RPUSH`、`LPOP/RPOP`、`BRPOP/BLPOP`（阻塞弹出，可用于简单消息队列）、`LRANGE`（分页）、`LTRIM`（定长裁剪）、`LINDEX`、`LLEN`。
- **应用场景**：最新 N 条记录/时间轴（`LTRIM` 维持定长）、评论列表分页、走马灯中奖名单、简单消息队列。
- **底层编码**：≤3.2 为 ziplist 或双向链表；3.2 起统一为 **quicklist**（双向链表 + 每节点一个 ziplist，7.0 起节点内部为 listpack）[3][6]。

### 2.3 Hash（哈希）—— 对象模型

- **特性**：field-value 键值对集合，类似 Python dict / Java HashMap [1]；可独立读写单个字段（相比整对象 JSON 序列化存 String 的显著优势）；字段与值均为字符串，支持整数/浮点运算。
- **常用命令**：`HSET/HGET/HGETALL/HMGET`、`HDEL`、`HLEN`、`HINCRBY/HINCRBYFLOAT`、`HEXISTS`、`HSCAN`；**7.4+ 字段级过期** `HEXPIRE/HPEXPIRE/HPERSIST`；**8.0 新增** `HGETEX/HSETEX/HGETDEL` [9][12]。
- **应用场景**：缓存对象（用户信息、商品详情）、购物车（user id → 商品/数量）、Session 存储、计数器集合。
- **底层编码**：小对象用 **listpack**（≤6.2 为 ziplist），超过阈值转 **hashtable**（字典）。

### 2.4 Set（集合）—— 唯一性与聚合运算

- **特性**：唯一字符串的无序集合；支持成员判定、增删及**交集/并集/差集**运算 [7]。
- **常用命令**：`SADD/SREM`、`SPOP`、`SMEMBERS`、`SCARD`、`SISMEMBER`、`SRANDMEMBER`、`SINTER/SUNION/SDIFF`（可 `STORE` 结果）、`SMOVE`。
- **应用场景**：聚合计算（共同关注、二度好友、点赞集合）、抽奖（`SPOP/SRANDMEMBER`）、独立 IP 统计（唯一性）、标签交集推荐。
- **底层编码**：全整数且数量少时用 **intset**（整数集合，有序去重、可二分）；否则转 **hashtable**；7.2 起小集合可先走 **listpack** [8]。

### 2.5 Sorted Set / ZSet（有序集合）—— Redis 的招牌

- **特性**：成员唯一 + 排序属性 **score**（分值，可重复）；按 score 排序，score 相同时按字典序；支持按分数范围与排名查询。官方称之为"计算机科学教材中不存在的人造数据结构" [16]。
- **常用命令**：`ZADD`（支持 NX/XX/CH/INCR）、`ZSCORE`、`ZRANGE/ZREVRANGE`、`ZRANGEBYSCORE`、`ZINCRBY`、`ZCARD`、`ZCOUNT`、`ZRANK/ZREVRANK`、`ZUNIONSTORE/ZINTERSTORE`。
- **应用场景**：**排行榜**（按分数排序取排名）、延迟队列（score = 执行时间戳，消费者按 `ZRANGEBYSCORE` 取到期任务）、电话簿/姓名排序、滑动窗口限流。
- **底层编码**（知识库术语表确认"skiplist 是 ZSet 底层结构"）：小数据量用 **listpack**（≤6.2 为 ziplist），大数据量用 **zskiplist（跳表）+ dict（哈希表）** 组合——dict 支撑 O(1) 的 `ZSCORE` 单点查询，跳表支撑 O(log N) 的范围查询与排名计算 [3][5][14]。

### 2.6 五大类型对比速览

| 类型 | 核心特性 | 底层编码（Redis 7.0+） | 典型场景 |
|------|----------|------------------------|----------|
| String | 二进制安全字节串，≤512MB | int / embstr / SDS | 缓存、计数器、锁 |
| List | 双端队列 | quicklist（listpack 节点） | 消息队列、时间轴 |
| Hash | field-value 对象 | listpack / hashtable | 对象缓存、购物车 |
| Set | 唯一无序集合 + 集合运算 | intset / listpack / hashtable | 共同关注、抽奖 |
| ZSet | 唯一成员 + score 排序 | listpack / skiplist+dict | 排行榜、延迟队列 |

---

## 三、扩展数据结构详解

### 3.1 Stream（流，Redis 5.0 引入）

- **特性**：追加式（append-only）日志型结构，按事件顺序记录与分发 [1][7]；官方称其"可用于消息队列，原生支持分区、复制与持久化，亚毫秒延迟每秒处理数百万数据点" [10]。知识库文档 08 将其归入"发布订阅与消息队列：Pub/Sub、Stream、消费者组、延迟队列、可靠消息"。
- **底层实现**：**Radix Tree（基数树，rax）+ listpack**。消息 ID 为 `<毫秒时间戳>-<序列号>` 的 128 位自增 ID；rax 以 16 字节大端编码 ID 为键（字节序与逻辑顺序一致），叶子节点用 listpack 连续存储一批消息；消费组状态与 PEL（Pending Entries List，待处理消息列表）也用 rax 维护 [13][17][18]。
- **核心命令**：`XADD`（追加）、`XREAD`（独立消费）、`XGROUP CREATE/XREADGROUP`（消费组）、`XACK`（确认）、`XPENDING/XCLAIM`（积压/死信）、`XRANGE/XREVRANGE`（回溯）、`XDEL/XTRIM`。
- **场景**：可靠消息队列（对比 List 的"弹出即消失"，Stream 有 ACK、消费组水平扩展、消息可回溯）、订单事件流、日志采集。知识库术语表确认：消费者组支持 **ACK 与待处理消息**机制。

### 3.2 Bitmap（位图，Redis 2.2 引入）

- **特性**：本质是字符串上的按位操作，紧凑存储二进制逻辑与状态。
- **命令**：`SETBIT/GETBIT`、`BITCOUNT`（统计 1 个数）、`BITPOS`、`BITOP AND/OR/XOR/NOT`（多键位运算）、`BITFIELD`。
- **场景**：用户签到/在线状态、活跃用户统计（1 位/用户，极大省内存）、布隆式存在性标记。

### 3.3 Bitfield（位域，Redis 3.2 引入）

- **特性**：在单个字符串值中编码多个计数器，提供原子的 get/set/自增，支持 WRAP/SAT/FAIL 溢出策略 [1][10]。
- **命令**：`BITFIELD`（GET/SET/INCRBY 子命令）、`BITFIELD_RO`（只读）。
- **场景**：多个紧凑计数器合并存储、游戏积分/配置打包。

### 3.4 HyperLogLog（基数统计，Redis 2.8 引入）

- **特性**：概率型去重计数结构，**固定内存**（每 key 约 12KB），标准误差 0.81%，支持多结构合并 [10]。
- **命令**：`PFADD`、`PFCOUNT`、`PFMERGE`。
- **场景**：UV（独立访客）统计等允许近似的去重计数。

### 3.5 GEO（地理空间，Redis 3.2 引入）

- **特性**：地理坐标索引，**底层就是有序集合**——用 Geohash 技术将经纬度交错编码为 52 位整数作为 score 存入 ZSet（double 可无损表示 52 位整数）[11]。
- **命令**：`GEOADD`、`GEOSEARCH`（圆/矩形范围查询，替代已弃用的 `GEORADIUS`）、`GEODIST`（距离，M/KM/FT/MI）、`GEOPOS`、`GEOHASH`；无 GEODEL，删除用 `ZREM` [11][12]。
- **场景**：附近的人/门店、配送范围判定、LBS 推荐。

### 3.6 Vector Set（向量集，Redis 8.0 beta）

- **特性**：由 antirez（Redis 原作者）主导开发的高维向量相似度检索类型，灵感来自 Sorted Set；支持 **HNSW 算法 + 余弦相似度**，配合结构化过滤器实现混合检索 [9][14][16]。当前为 beta。
- **场景**：AI 语义搜索、推荐系统向量检索。知识库文档 16/17/18 亦表明 Redis 在 AI/Agent 场景用作向量库与记忆存储（RedisVL、RediSearch）。

---

## 四、底层编码实现原理

Redis 每种数据类型都有多种编码（encoding），通过 `OBJECT ENCODING key` 可查看。以下是核心编码实现：

### 4.1 SDS（Simple Dynamic String，简单动态字符串）

- **结构**：`struct sdshdr { len; alloc; flags; buf[] }`，含 sdshdr5/8/16/32/64 五种头类型，按字符串长度分级 [5][6]。
- **为何不用 C 字符串**：C 字符串以 `\0` 结尾 → ① 非二进制安全；② 取长度 O(n)；③ 修改需频繁内存重分配 [5][6]。
- **优点**：O(1) 取长度、二进制安全、**空间预分配 + 惰性释放**（扩容预留、缩短不立即回收，减少内存分配次数）、杜绝缓冲区溢出。
- **知识库锚点**：术语表确认 SDS = "Simple Dynamic String，Redis 自定义字符串"。

### 4.2 ziplist（压缩列表，已废弃）

- **结构**：一整块连续内存：`zlbytes/zltail/zllen` 头 + 多个 entry + `0xFF` 结尾；entry 含 `prevlen`（<254 用 1 字节，否则 5 字节）、`encoding`、`data`，支持变长整数编码 [5][6]。
- **优点**：连续内存、无指针开销、变长编码，极致省内存。
- **致命缺点**：**级联更新（cascading update）**——中间插入/删除引起 prevlen 长度变化时可引发连锁内存重分配，最坏 O(n²) [8][6]。
- **结论**：查找 O(n)，因级联更新缺陷，在 Redis 7.0 被 listpack 全面取代。

### 4.3 listpack（紧凑列表，ziplist 继任者，Redis 7.0）

- **结构**：连续字节数组：总字节数(4B) + 元素个数(2B) + entries + 0xFF；每个 entry 为 `encoding + data`，**末尾附带 backlen（该 entry 自身总长度）**，据此可从尾向头遍历 [8]。
- **改进**：entry 长度信息只依赖自身（backlen），**彻底消除级联更新问题** [8]。
- **性能**：小集合下内存比 hashtable 省 3–10 倍；查找 O(n)，但因缓存友好，小集合顺序读优于 hashtable 的指针跳转 [8]。
- **知识库锚点**：术语表确认 listpack = "Redis 7.0 紧凑列表编码（替代 ziplist）"。

### 4.4 quicklist（快速列表，List 的编码）

- **结构**：**双向链表 + 每节点一个 ziplist/listpack**，兼顾"连续内存省内存"与"双端 O(1) 增删" [5][6]。
- **参数**：
  - `list-max-listpack-size`：单节点大小上限。负值按字节（-1=4KB、-2=8KB 默认、-3=16KB、-4=32KB、-5=64KB）；正值按元素个数。
  - `list-compress-depth`：中部节点压缩（0=不压缩默认；1=除头尾外全部压缩，队列场景可省约 40–50% 内存）。访问压缩节点时透明解压→读取→重压缩 [19][20][21]。

### 4.5 skiplist（跳表，ZSet 的底层）

- **结构**：多层有序链表，节点随机层数，Redis 默认最大层 **32**（`ZSKIPLIST_MAXLEVEL`），含 backward 回退指针；score 相同时按 member 字典序比较 [14][6]。
- **antirez 为什么用跳表而非红黑树/平衡树**（原话要点）：① 内存占用可控且比 B 树更省；② ZSet 大量操作是 ZRANGE/ZREVRANGE 这类链表式顺序遍历，跳表缓存局部性好；③ 实现、调试简单（如增强跳表实现 O(log N) 的 ZRANK 只需少量改动）[14]。
- **复杂度**：查找/插入/删除 O(log N)；范围查询 O(log N + M)；配合 dict 使 `ZSCORE` 达 O(1) [6][14]。
- **知识库锚点**：术语表确认 skiplist = "跳表，ZSet 底层结构"。

### 4.6 intset（整数集合，Set 的编码）

- **结构**：`{ encoding; length; contents[] }`，encoding 为 int16/int32/int64；元素从小到大有序且唯一 [6]。
- **升级机制**：插入更大类型整数时整体升级（扩空间 → 转类型 → 保持有序插入），**只升不降** [6]。
- **性能**：可二分查找；小整数集合极省内存；受 `set-max-intset-entries`（默认 512）约束。

### 4.7 hashtable / dict（字典/哈希表，Hash 与大 Set/ZSet 的编码）

- **结构**：`dictht`（table 数组 + size + sizemask + used），`dictEntry` 保存键值对，**链地址法**解决冲突 [6]。
- **渐进式 rehash**：扩容/缩容不一次性完成，维护新旧两张表，将 rehash 分摊到后续每次增删改查，避免单请求响应时间剧增 [5][6]。
- **触发条件**：装载因子 > 1 扩容（约 2 倍）；< 0.1 缩容（约为数据量的 2 倍大小）[6]。
- **复杂度**：平均 O(1) 查找/插入/删除。

### 4.8 Radix Tree（rax，基数树，Stream 专用）

- **特性**：压缩前缀树，每个"唯一子节点"与父节点合并，天然适合大量**共享前缀**的数据（如连续消息 ID），显著省内存并支持高效范围查询 [10][17][18]。
- **在 Stream 中的角色**：rax 按 ID 索引 → 叶子指向 listpack；消费组、PEL 也各自用 rax 存储 [17][18]。

### 4.9 编码全景表

| 数据类型 | 小数据量编码 | 大数据量编码 | 转换阈值参数 |
|----------|--------------|--------------|--------------|
| String | int / embstr | raw（SDS） | 长度/整型判断 |
| List | quicklist（listpack 节点） | quicklist | `list-max-listpack-size` |
| Hash | listpack（≤6.2 ziplist） | hashtable | `hash-max-listpack-entries/value` |
| Set | intset → listpack(7.2+) | hashtable | `set-max-intset-entries` / `set-max-listpack-entries` |
| ZSet | listpack（≤6.2 ziplist） | skiplist + dict | `zset-max-listpack-entries/value` |
| Stream | rax + listpack | rax + listpack | — |

---

## 五、编码转换规则与配置参数

Redis 依据数据规模在"紧凑编码"与"标准编码"间自动切换，官方 Memory optimization 文档给出默认阈值 [7]：

**Redis ≤ 6.2（ziplist 时代）**
```
hash-max-ziplist-entries 512     # Hash 元素数超阈值 → 转 hashtable
hash-max-ziplist-value   64      # 任一 field/value 超 64B → 转 hashtable
zset-max-ziplist-entries 128     # ZSet 元素数超阈值 → 转 skiplist+dict
zset-max-ziplist-value   64
set-max-intset-entries   512     # Set 全整数且数量 ≤ 阈值 → intset
```

**Redis ≥ 7.0（listpack 取代 ziplist）**
```
hash-max-listpack-entries 512
hash-max-listpack-value   64
zset-max-listpack-entries 128
zset-max-listpack-value   64
set-max-intset-entries   512
```

**Redis ≥ 7.2（Set 新增 listpack 编码）**
```
set-max-listpack-entries 128
set-max-listpack-value   64
```

**List / quicklist 参数** [19][20][21]
```
list-max-listpack-size  -2   # 默认：每节点 ≤8KB（-1=4KB…-5=64KB，正数=元素个数）
list-compress-depth      0   # 默认不压缩（1=压缩除头尾外所有中间节点）
```

**转换规则小结** [6][8]：
- Hash / ZSet：元素个数或单元素大小任一超阈值 → 由 listpack(ziplist) 转 hashtable / skiplist+dict；
- Set：非全整数或元素数超阈值 → intset 转 hashtable（7.2+ 可先走 listpack）；
- List：元素累计超过节点容量 → 追加为 quicklist 新节点；7.0 后 List 的 `OBJECT ENCODING` 恒为 quicklist [8][21]。

---

## 六、版本演进（Redis 7.x / 8.x）

### Redis 7.0（2022 年 4 月 GA）
- **listpack 全面替换 ziplist**（Hash、ZSet、quicklist 节点），修复级联更新问题；RDB 加载时按需惰性转换 [8][22][23]。
- 新增 Redis Functions（替代 Lua 脚本）、ACL v2、Sharded Pub/Sub，新增近 50 个命令与选项 [22]。

### Redis 7.2
- Set 类型引入 listpack 紧凑编码（`set-max-listpack-entries`）[7][8]。

### Redis 7.4
- **Hash 字段级过期（field expiration）**：新增 `HEXPIRE/HPEXPIRE/HPEXPIREAT/HPERSIST` 等命令 [9][12]。

### Redis 8.0（2025 年 5 月 GA）
- **Vector Set（beta）**：全新数据类型，HNSW 算法 + 余弦相似度，支持高维向量检索（AI 语义搜索/推荐）[9][16][24]。
- **I/O 线程**（`io-threads`，默认 1，8 核下吞吐提升最高 112%）[24]。
- **新 Hash 命令**：`HGETEX`、`HSETEX`、`HGETDEL`，简化缓存/Session 模式 [9][12]。
- **性能**：命令执行最快提升 87%、复制内存占用最高减少 35%、30+ 项性能优化 [12][24]。
- **许可变更**：新增 AGPLv3 选项（RSAL/SSPL/AGPL 三许可），Redis Community Edition 更名为 Redis Open Source [16][24]。

---

## 七、应用场景选型建议

| 需求 | 推荐类型 | 理由 |
|------|----------|------|
| 缓存对象 / 页面片段 | String 或 Hash | 简单值用 String；需改单字段用 Hash |
| 计数器 / 限流 / 分布式锁 | String | INCR 原子性；SET NX EX 原子加锁 |
| 简单消息队列 | List | LPUSH+BRPOP 阻塞消费（无 ACK，适合可丢场景） |
| 可靠消息队列（需 ACK/回溯/消费组） | Stream | XACK、消费组、PEL、消息可回溯 |
| 排行榜 / Top N / 延迟任务 | ZSet | score 排序 + O(logN) 排名查询 |
| 去重 / 交集并集差集 / 抽奖 | Set | 唯一性 + 集合运算 + SPOP |
| UV 等近似去重计数 | HyperLogLog | 固定内存 ~12KB、0.81% 误差 |
| 签到 / 活跃用户位图 | Bitmap | 1 位/用户，内存极小 |
| 附近的人 / LBS | GEO | 底层 ZSet，范围与距离查询 |
| 对象多字段部分更新 | Hash | 独立字段 CRUD，7.4+ 字段级过期 |
| AI 向量检索（8.0+） | Vector Set（beta） | HNSW + 余弦相似度 |

**运维提醒**（源自知识库术语表）：大 Key 判定标准为单 key 内存占用过大（Hash/List 超过约 10KB 即视为大 Key 风险），需结合 `OBJECT ENCODING`、`MEMORY USAGE` 与 `--bigkeys` 扫描进行治理。

---

## 八、结论

1. **Redis 的核心是"数据类型即 API"**：通过 redisObject 对象系统 + 多编码机制，小数据量用紧凑编码（intset/listpack）极致省内存，大数据量用高性能编码（hashtable/skiplist/quicklist）保吞吐，并以配置参数自动切换——这一设计贯穿 7.0 的 listpack 化和 8.0 的 Vector Set。
2. **知识库贡献**：提供了"9 大数据类型"的权威框架、SDS/listpack/skiplist/Stream 等术语锚点、大 Key 判定标准（>10KB Hash/List）以及消息队列（Stream、消费者组、ACK）与项目实战（排行榜、秒杀、Feed 流）的应用背景。
3. **网络资料补充**：完整补齐了各类型的命令与特性、底层编码实现细节（SDS 结构、listpack backlen 消除级联更新、跳表层数 32 与选型理由、渐进式 rehash 等）、编码转换阈值参数，以及 7.0–8.0 的版本演进。
4. **选型核心**：String 打底、Hash 管对象、List 管队列、Set 管去重聚合、ZSet 管排序、Stream 管可靠消息——先判断需求特性（是否需要排序/去重/消息确认），再选择类型，最后关注编码阈值与大 Key 治理。

---

## 参考文献

**知识库来源**
- [KB-00] 《00 总览与导读》— `data/uploads/user_1_redis/00_总览与导读.md`（文档地图、学习路径、术语表、环境准备）；分析笔记见 `/notes/doc_analysis.md`

**官方文档与博客**
1. Redis data types — https://redis.io/docs/latest/develop/data-types
2. Redis Strings — https://redis.io/docs/latest/develop/data-types/strings
3. Compare data types — https://redis.io/docs/latest/develop/data-types/compare-data-types
4. Memory optimization（编码阈值）— https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/memory-optimization
5. Redis data structures 技术页 — https://redis.io/technology/data-structures
6. Redis 8.0 What's New — https://redis.io/docs/latest/develop/whats-new/8-0
7. Redis 8.0 Commands Reference — https://redis.io/docs/latest/commands/redis-8-0-commands
8. GEOADD / GEOSEARCH 等命令文档 — https://redis.io/docs/latest/commands/geoadd
9. Redis 7.0 GA 博客 — https://redis.io/blog/redis-7-generally-available
10. Redis 8 GA 博客 — https://redis.io/blog/redis-8-ga
11. Welcome back to Redis, antirez — https://redis.io/blog/welcome-back-to-redis-antirez
12. Redis Open Source 8.0 release notes — https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/release-notes/redisce/redisos-8.0-release-notes

**GitHub 与源码**
13. redis/redis issue #8702（listpack 替换 ziplist）— https://github.com/redis/redis/issues/8702
14. listpack.c 源码 — https://download.redis.io/redis-stable/src/listpack.c
15. zpoint/Redis-Internals Stream 源码图解 — https://github.com/zpoint/Redis-Internals/blob/5.0/Object/streams/streams.md

**技术博客**
16. 小林coding《Redis 常见数据类型和应用场景》— https://www.xiaolincoding.com/redis/data_struct/command.html
17. pdai《Redis 底层数据结构详解》— https://pdai.tech/md/db/nosql-redis/db-redis-x-redis-ds.html
18. 铁蕾《Redis内部数据结构详解(6)——skiplist》— https://zhangtielei.com/posts/blog-redis-skiplist.html
19. JavaGuide《Redis为什么用跳表实现有序集合》— https://javaguide.cn/database/redis/redis-skiplist.html
20. 博客园《吊打面试官之 Redis 底层数据结构详解》— https://www.cnblogs.com/kuangtf/articles/15459957.html
21. 腾讯云《Redis Stream 数据结构实现原理》— https://cloud.tencent.com/developer/article/2331486
22. OneUptime《How Redis Listpack / Quicklist Works》— https://oneuptime.com/blog/post/2026-03-31-redis-how-redis-listpack-data-structure-works/view
23. D瓜哥《Redis 核心数据结构（四）》（list-max-listpack-size 实测）— https://www.diguage.com/post/redis-core-data-structure-4

---

*本报告由深度研究智能体生成：研究计划见 `/plans/research_plan.md`，知识库分析笔记见 `/notes/doc_analysis.md`，网络研究笔记见 `/notes/web_research.md`。*
