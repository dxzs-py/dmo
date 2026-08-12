# 网络研究笔记：Redis 核心数据结构

> 研究人：网络搜索专家 ｜ 来源优先级：redis.io 官方文档 > 官方 GitHub 源码/发布说明 > antirez 设计文档 > 权威技术博客

## 一、五大基础类型（官方定义要点）

依据 [Redis 官方数据类型总览](https://redis.io/docs/latest/develop/data-types)：

1. **String（字符串）**：最基础类型，表示字节序列（binary-safe），最大 512MB。可存文本/整数/浮点数/图片/序列化对象。支持 INCR/DECR 原子自增自减。
2. **List（列表）**：按插入顺序排序的字符串列表，针对头尾操作优化，适合队列/栈/双端队列。支持 LPUSH/RPUSH/LPOP/RPOP/BLPOP/BRPOP/LTRIM。
3. **Hash（哈希）**：字符串字段→字符串值映射（类似 HashMap），适合存储浅层对象。Redis 7.4+ 支持字段级过期。
4. **Set（集合）**：无序唯一字符串集合，增删查 O(1)，支持 SINTER/SUNION/SDIFF 集合运算。
5. **Sorted Set（ZSet）**：唯一成员按 score 排序，score 相同按字典序；是 Set 与 Hash 的混合体。核心命令 ZADD/ZRANGE/ZRANK/ZINCRBY。

## 二、扩展数据类型

| 类型 | 本质 | 核心用途 | 关键命令 |
|---|---|---|---|
| Bitmap | 定义在 String 上的位操作集合（非独立类型） | 签到/在线状态/日活统计 | SETBIT/GETBIT/BITCOUNT/BITOP |
| Bitfield | 字符串上操作任意位长整数 | 压缩多计数器 | BITFIELD(v3.2)/BITFIELD_RO(v6.0) |
| HyperLogLog | 概率型结构，恒定 ~12KB/key | 海量 UV 去重 | PFADD/PFCOUNT/PFMERGE |
| Geo | 基于 ZSet 的 GeoHash 技术 | 附近的人/LBS | GEOADD/GEODIST/GEOSEARCH |
| Stream | 追加式日志，ID 时间有序 | 可靠消息队列/事件溯源 | XADD/XREADGROUP/XACK/XCLAIM |

Stream 机制：消费者组 + last-delivered-id 游标 + PEL（未确认消息）+ ACK + XCLAIM 重新指派（至少一次投递）。

## 三、底层实现原理

### SDS（所有字符串基石）
- 不用 C 原生 char[]：strlen 是 O(N)、非二进制安全、有 realloc 溢出风险
- Redis 3.2+ 引入 5 种头部（sdshdr5/8/16/32/64）按长度选择，降低开销
- String 三种编码：int（64 位整数）、embstr（≤44B 一次 malloc）、raw（>44B 两次 malloc）

### Hash：dict + 渐进式 rehash
- dict 持有两张表 ht[0]/ht[1]，SipHash + 随机种子防碰撞攻击，链地址法解决冲突
- 负载因子 >1 触发扩容 2 倍；渐进式 rehash 每次操作迁移 ≤10 个 bucket，后台任务 100 步/批、最多 1ms
- 过程：读查两张表，写只写新表；rehashidx 记录进度

### List：quicklist（3.2+）与 listpack（7.0+）
- 演进：ziplist/linkedlist 双编码 → quicklist（双向链表+节点内嵌 ziplist，支持 LZF 压缩）→ 7.0 节点内换 listpack
- 头尾 O(1)，中间遍历 O(N/M+M)

### Set：intset 与 hashtable（7.2+ 引入 listpack）
- intset：有序整数数组 + 二分查找，内存省 5~6 倍；插入超位宽自动升级（16→32→64）
- 非纯整数/超阈值转 hashtable；7.2+ 字符串小集合可用 listpack

### ZSet：skiplist + dict 双结构
- dict：member→score O(1)；skiplist：按 score 排序 O(log N) 范围查询
- 跳表：最大层数 32、晋升概率 0.25、含 span 支撑 O(log N) rank
- 两个结构共享 SDS 字符串省内存；小 ZSet 用 ziplist（<7.0）/listpack（7.0+）

### ziplist vs listpack（7.0 移除 ziplist 原因）
- ziplist 每个 entry 含 prevlen，中间插入/删除可能级联更新整表 O(N)
- listpack 只记录自身长度 + 末尾 backlen，彻底消除级联更新；6 字节固定头 vs ziplist 10 字节
- 时间线：listpack 5.0 用于 Stream，7.0 替换 Hash/List/ZSet 中的 ziplist，7.2 扩展到 Set

## 四、编码转换参数与阈值表（Redis 7.x 默认值）

| 参数 | 默认值 | 含义 |
|---|---|---|
| hash-max-listpack-entries | 512 | Hash 最大 entry 数 |
| hash-max-listpack-value | 64 | Hash 单字段/值最大字节 |
| list-max-listpack-size | -2 | 单节点上限（-2=8KB） |
| list-compress-depth | 0 | 两端不压缩节点数 |
| set-max-intset-entries | 512 | 纯整数 Set intset 上限 |
| set-max-listpack-entries | 128 | 字符串 Set listpack 上限（7.2+） |
| set-max-listpack-value | 64 | Set 成员最大字节（7.2+） |
| zset-max-listpack-entries | 128 | ZSet listpack 最大元素数 |
| zset-max-listpack-value | 64 | ZSet 成员最大字节 |

⚠️ 转换是**单向的**：超阈值转完整编码后，数据缩小也不会自动转回。

## 五、Redis 8.0 变化
- 新增 8 种数据结构：Vector Set(beta)、JSON、Time Series、Bloom/Cuckoo filter、Count-min sketch、Top-K、t-digest
- Hash 新命令：HGETEX/HSETEX/HGETDEL（基于 7.4 字段级过期）
- 性能：延迟最高降 87%、吞吐最高 2 倍
- 核心五类型的底层编码机制在 7.x 已定型，8.0 未改动

## 六、应用场景总结
- String：缓存/计数器/分布式锁/Session/验证码
- List：消息队列(LPUSH+BRPOP)/Feed 流/最新 N 条
- Hash：对象存储/会话聚合/小对象内存优化
- Set：标签/共同好友/去重/抽奖
- ZSet：排行榜/时间排序/延迟队列/自动补全
- Bitmap：签到/在线状态/日活
- HLL：UV 统计
- Geo：附近的人/LBS
- Stream：可靠消息队列/事件溯源/审计日志

## 七、主要来源
1. https://redis.io/docs/latest/develop/data-types
2. https://redis.io/docs/latest/develop/data-types/compare-data-types
3. https://redis.io/docs/latest/develop/data-types/sorted-sets
4. https://redis.io/docs/latest/develop/data-types/strings/bitmaps
5. https://redis.io/docs/latest/develop/data-types/strings/bitfields
6. https://redis.io/docs/latest/develop/data-types/streams
7. https://redis.io/glossary/data-structures
8. https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/memory-optimization
9. https://redis.io/docs/latest/develop/whats-new/8-0
10. https://github.com/redis/redis/blob/unstable/src/t_zset.c
11. https://github.com/redis/redis/blob/unstable/src/dict.h
12. https://github.com/antirez/listpack/blob/master/listpack.md
13. https://redis.io/docs/latest/develop/use-cases
