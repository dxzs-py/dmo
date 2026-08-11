# 网络研究笔记（web_research.md）

## 研究主题
Redis 的核心数据结构 —— 网络权威资料补充研究

## 检索范围与来源
基于互联网权威来源（redis.io 官方文档、Redis 官方博客、GitHub redis/redis 仓库及可信技术博客）整理。

核心来源 URL：
1. Redis data types 官方文档 — https://redis.io/docs/latest/develop/data-types
2. Redis Strings 官方文档 — https://redis.io/docs/latest/develop/data-types/strings
3. Compare data types 官方文档 — https://redis.io/docs/latest/develop/data-types/compare-data-types
4. Memory optimization 官方文档（编码阈值参数）— https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/memory-optimization
5. Redis data structures 技术页 — https://redis.io/technology/data-structures
6. Redis 8.0 What's New — https://redis.io/docs/latest/develop/whats-new/8-0
7. Redis 8.0 Commands Reference — https://redis.io/docs/latest/commands/redis-8-0-commands
8. Redis 7.0 GA 官方博客 — https://redis.io/blog/redis-7-generally-available
9. Redis 8 GA 官方博客 — https://redis.io/blog/redis-8-ga
10. GitHub redis/redis issue #8702（listpack 替换 ziplist）— https://github.com/redis/redis/issues/8702
11. 小林coding《Redis 常见数据类型和应用场景》— https://www.xiaolincoding.com/redis/data_struct/command.html
12. 铁蕾《Redis内部数据结构详解(6)——skiplist》— https://zhangtielei.com/posts/blog-redis-skiplist.html

---

## 一、数据类型全景

官方将 Redis 定位为"数据结构服务器"，数据类型分两类：
- **通用型**：String、List、Set、Sorted Set（ZSet）、Hash、Stream
- **专用型**：Bitmap、Bitfield、HyperLogLog、Geospatial、JSON、Bloom filter、Count-min sketch、Cuckoo filter、t-digest、Top-K、Time series、Vector set

## 二、五大核心数据结构要点

### 1. String
- 存储字节序列，单值上限 **512MB**，二进制安全；多数操作 O(1)
- 命令：SET/GET/APPEND/INCR/DECR/SETNX/MSET/MGET/STRLEN/SETRANGE/GETRANGE
- 场景：缓存、计数器、分布式锁（SET NX+EX）、Session、验证码
- 底层编码：int / embstr / raw（SDS）

### 2. List
- 按插入顺序的字符串列表，头尾高效增删；队列/栈/双端队列
- 命令：LPUSH/RPUSH/LPOP/RPOP/BRPOP/BLPOP/LRANGE/LINDEX/LTRIM/LLEN
- 场景：消息队列（简单版）、时间轴、最新 N 条、定长列表
- 底层：quicklist（双向链表 + 每节点 listpack）

### 3. Hash
- field-value 键值对集合；7.4+ 支持字段级过期（HEXPIRE 等）
- 命令：HSET/HGET/HGETALL/HMGET/HDEL/HLEN/HINCRBY/HSCAN；8.0 新增 HGETEX/HSETEX/HGETDEL
- 场景：缓存对象、购物车、Session、计数器集合
- 底层：listpack（小）→ hashtable（大）

### 4. Set
- 唯一字符串无序集合；支持交集/并集/差集
- 命令：SADD/SREM/SPOP/SMEMBERS/SCARD/SISMEMBER/SINTER/SUNION/SDIFF/SRANDMEMBER
- 场景：聚合计算、抽奖、独立 IP 统计、好友推荐
- 底层：intset（全整数且少）→ listpack（7.2+）→ hashtable

### 5. Sorted Set（ZSet）
- 成员唯一 + score 排序；score 相同时按字典序
- 命令：ZADD/ZSCORE/ZRANGE/ZREVRANGE/ZRANGEBYSCORE/ZINCRBY/ZCARD/ZRANK/ZUNIONSTORE
- 场景：排行榜、延迟队列（score=执行时间戳）、电话簿、滑动窗口限流
- 底层：listpack（小）→ zskiplist + dict（大，dict 保证 O(1) ZSCORE，跳表保证 O(logN) 范围查询）

## 三、扩展数据结构要点

### 1. Stream（Redis 5.0）
- 追加式日志结构；消息 ID 为 `<毫秒时间戳>-<序列号>` 128 位自增 ID
- 底层：**Radix Tree（rax）+ listpack**；消费组状态与 PEL 也用 rax
- 命令：XADD/XREAD/XGROUP/XREADGROUP/XACK/XPENDING/XCLAIM/XRANGE/XTRIM
- 场景：可靠消息队列（有 ACK、消费组、可回溯）

### 2. Bitmap（Redis 2.2）
- 字符串按位操作；SETBIT/GETBIT/BITCOUNT/BITPOS/BITOP/BITFIELD
- 场景：签到、在线状态、活跃用户统计（1 位/用户）

### 3. Bitfield（Redis 3.2）
- 单字符串中编码多个计数器，原子操作，支持 WRAP/SAT/FAIL 溢出策略
- 命令：BITFIELD / BITFIELD_RO

### 4. HyperLogLog（Redis 2.8）
- 概率型基数统计；每 key 约 12KB，标准误差 0.81%
- 命令：PFADD/PFCOUNT/PFMERGE；场景：UV 统计

### 5. GEO（Redis 3.2）
- 底层就是 ZSet：经纬度交错编码为 52 位整数作为 score（Geohash 技术）
- 命令：GEOADD/GEOSEARCH（替代 GEORADIUS）/GEODIST/GEOPOS/GEOHASH；删除用 ZREM
- 场景：附近的人/门店、LBS

### 6. Vector Set（Redis 8.0 beta）
- 由 antirez 主导开发；HNSW 算法 + 余弦相似度；AI 语义搜索/推荐

## 四、底层编码实现原理

### SDS（简单动态字符串）
- 结构：sdshdr{len; alloc; flags; buf[]}，sdshdr5/8/16/32/64 五种头
- 相比 C 字符串：O(1) 取长度、二进制安全、空间预分配+惰性释放

### ziplist（压缩列表，已废弃）
- 连续内存 + 变长整数编码，极致省内存
- **致命缺点：级联更新（cascading update），最坏 O(n²)**；查找 O(n)

### listpack（ziplist 继任者，Redis 7.0）
- entry 自带 backlen（本 entry 长度），从尾向头遍历无需 prevlen
- **彻底消除级联更新问题**；小集合内存比 hashtable 省 3–10 倍

### quicklist
- 双向链表 + 每节点一个 listpack
- 参数：list-max-listpack-size（默认 -2=8KB/节点）、list-compress-depth（默认 0 不压缩，1=压缩中间节点，可省约 40–50% 内存）

### skiplist（跳表）
- 多层有序链表，随机层数，默认最大层 32；score 相同按 member 字典序
- antirez 选型理由：内存可控、链表式顺序遍历缓存局部性好、实现调试简单
- O(logN) 查找/插入/删除；配合 dict 使 ZSCORE 达 O(1)

### intset（整数集合）
- {encoding; length; contents[]}，int16/32/64；有序且唯一
- **只升不降**的升级机制；受 set-max-intset-entries（默认 512）约束

### hashtable / dict
- 链地址法解决冲突；**渐进式 rehash**（rehash 分摊到后续操作）
- 装载因子 >1 扩容（约 2 倍）；<0.1 缩容

### Radix Tree（rax）
- 压缩前缀树，共享前缀合并；Stream 中按 ID 索引 → 叶子指向 listpack

## 五、编码转换阈值（配置参数）

**Redis ≤ 6.2（ziplist 时代）**
```
hash-max-ziplist-entries 512
hash-max-ziplist-value   64
zset-max-ziplist-entries 128
zset-max-ziplist-value   64
set-max-intset-entries   512
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

**List / quicklist**
```
list-max-listpack-size  -2   # 默认每节点 ≤8KB（-1=4KB…-5=64KB，正数=元素个数）
list-compress-depth      0   # 默认不压缩
```

## 六、版本演进（7.x / 8.x）

- **Redis 7.0（2022.04 GA）**：listpack 全面替换 ziplist（Hash/ZSet/quicklist 节点）；Redis Functions；ACL v2；Sharded Pub/Sub
- **Redis 7.2**：Set 引入 listpack 编码
- **Redis 7.4**：Hash 字段级过期（HEXPIRE/HPEXPIRE/HPEXPIREAT/HPERSIST）
- **Redis 8.0（2025.05 GA）**：Vector Set（beta，HNSW+余弦）；I/O 线程（io-threads，8 核下吞吐最高 +112%）；新 Hash 命令 HGETEX/HSETEX/HGETDEL；命令执行最快 +87%；AGPLv3 许可选项

## 七、关键结论（网络侧）
Redis 核心竞争力 = "数据类型即 API"：通过 redisObject 对象系统 + 多编码机制，小数据量用紧凑编码（listpack/intset）省内存，大数据量用高性能编码（hashtable/skiplist/quicklist）保吞吐，并以配置参数自动切换。
