# 零基础到进阶的Redis系统学习手册

## 前言

本手册旨在帮助零基础用户系统学习Redis数据库，从基础操作到高级特性，循序渐进地掌握Redis的核心知识点。手册中的所有实战案例均基于Redis 7.0+版本，可直接复制运行。

**手册特色**：
- **原理驱动**：每个知识点先讲底层原理，再给实战命令，知其然更知其所以然
- **场景导向**：结合真实业务场景，说明何时用、怎么用、为什么用
- **对比选型**：关键决策点提供对比表格，帮助做出正确选择

## 准备工作

在开始学习前，请确保你已经安装了Redis 7.0+。可以通过以下命令检查Redis版本：

```bash
redis-server --version
```

---

## 模块一：基础操作

### 1.1 启动Redis

**功能说明**：启动Redis服务器。

**实战案例**：

```bash
# 前台启动Redis服务器（适用于调试）
redis-server

# 后台启动Redis服务器
redis-server --daemonize yes

# 指定配置文件启动
redis-server /etc/redis/redis.conf

# 指定端口启动
redis-server --port 6380 --daemonize yes
```

### 1.2 连接Redis

**功能说明**：连接到Redis服务器。

**实战案例**：

```bash
# 默认连接（127.0.0.1:6379）
redis-cli

# 连接指定主机和端口
redis-cli -h 127.0.0.1 -p 6379

# 使用密码连接
redis-cli -a your_password

# 指定数据库编号连接（共16个库，编号0-15）
redis-cli -n 1
```

### 1.3 测试连接

**功能说明**：测试Redis连接是否正常。

**实战案例**：

```bash
# 测试连接
redis-cli ping
# 预期输出：PONG

# 在客户端内测试
127.0.0.1:6379> PING
# 预期输出：PONG

# 带消息的PING
127.0.0.1:6379> PING "hello"
# 预期输出："hello"
```

### 1.4 关闭Redis

**功能说明**：安全关闭Redis服务器（会先持久化数据）。

**实战案例**：

```bash
# 安全关闭（会触发RDB持久化）
redis-cli shutdown

# 指定密码关闭
redis-cli -a your_password shutdown

# 在Redis客户端中执行
127.0.0.1:6379> SHUTDOWN
```

---

## 模块二：核心架构原理

> 理解Redis的架构原理，是正确使用Redis、排查问题、性能调优的基础。

### 2.1 Redis整体架构

Redis采用**单进程单线程模型**（核心命令执行线程），整体架构如下：

```
┌─────────────────────────────────────────────────┐
│                   Redis Server                   │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐ │
│  │ 客户端1   │  │ 客户端2   │  │ 客户端N       │ │
│  └────┬─────┘  └────┬─────┘  └──────┬────────┘ │
│       │              │               │           │
│       └──────────────┼───────────────┘           │
│                      ▼                           │
│  ┌───────────────────────────────────────────┐   │
│  │         I/O 多路复用（epoll/kqueue）       │   │
│  └───────────────────┬───────────────────────┘   │
│                      ▼                           │
│  ┌───────────────────────────────────────────┐   │
│  │          事件分派器（Event Dispatcher）     │   │
│  └───────┬─────────────────┬─────────────────┘   │
│          ▼                 ▼                      │
│  ┌──────────────┐  ┌──────────────┐              │
│  │ 文件事件处理器 │  │ 时间事件处理器 │              │
│  │ （命令执行）   │  │ （定时任务）   │              │
│  └──────┬───────┘  └──────────────┘              │
│         ▼                                        │
│  ┌───────────────────────────────────────────┐   │
│  │            命令执行器（单线程）              │   │
│  └───────────────────┬───────────────────────┘   │
│                      ▼                           │
│  ┌───────────────────────────────────────────┐   │
│  │              内存数据库（DB 0-15）          │   │
│  └───────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

**核心组件说明**：

| 组件 | 作用 | 说明 |
|------|------|------|
| I/O多路复用 | 同时监听多个客户端连接 | 使用epoll(Linux)/kqueue(Mac)/select(通用) |
| 文件事件处理器 | 处理客户端命令 | 命令请求→命令读取→命令执行→结果返回 |
| 时间事件处理器 | 处理定时任务 | serverCron（每100ms执行一次，负责过期清理等） |
| 命令执行器 | 执行Redis命令 | 单线程串行执行，保证原子性 |

### 2.2 单线程模型与事件驱动

**为什么Redis选择单线程？**

Redis的核心设计目标是**低延迟**和**高吞吐**，单线程模型在此目标下具有独特优势：

| 优势 | 说明 |
|------|------|
| 无锁竞争 | 避免了多线程加锁/解锁的开销和死锁风险 |
| 无上下文切换 | 省去了线程切换的CPU开销（每次切换约1-5μs） |
| 原子性保证 | 所有命令串行执行，天然保证原子性 |
| 实现简单 | 代码逻辑清晰，易于维护和调试 |

**单线程为什么还这么快？**

1. **纯内存操作**：数据存储在内存中，读写速度约100ns级别
2. **I/O多路复用**：单线程监听多个连接，非阻塞I/O
3. **高效的数据结构**：针对不同场景设计了专用数据结构
4. **避免无谓的系统调用**：单线程无需锁操作和线程切换

**注意**：Redis并非完全单线程，以下操作使用多线程：
- Redis 4.0+：后台删除大Key（`UNLINK`）、AOF fsync（后台线程）
- Redis 6.0+：网络I/O多线程（`io-threads`），命令执行仍为单线程

```bash
# 查看Redis是否开启I/O多线程（Redis 6.0+）
CONFIG GET io-threads
CONFIG GET io-threads-do-reads
```

### 2.3 内存管理机制

Redis所有数据存储在内存中，内存管理至关重要。

**内存分配器**：Redis默认使用jemalloc，相比glibc malloc减少内存碎片率约30%。

```bash
# 查看内存使用详情
INFO memory
```

**关键内存指标**：

| 指标 | 含义 | 关注点 |
|------|------|--------|
| used_memory | Redis分配器分配的内存总量 | 实际数据占用 |
| used_memory_rss | 操作系统分配给Redis的内存 | 包含碎片 |
| mem_fragmentation_ratio | used_memory_rss / used_memory | >1.5表示碎片严重 |
| maxmemory | 最大内存限制 | 超出后触发淘汰策略 |

**内存碎片处理**：

```bash
# 查看碎片率
INFO memory | grep mem_fragmentation_ratio

# 主动整理碎片（Redis 4.0+）
CONFIG SET activedefrag yes

# 查看碎片整理配置
CONFIG GET activedefrag
CONFIG GET active-defrag-threshold-lower
CONFIG GET active-defrag-threshold-upper
```

### 2.4 Redis通信协议（RESP）

Redis使用**RESP（REdis Serialization Protocol）**协议进行客户端与服务端通信，特点是人类可读、解析高效。

**协议格式**：

```
简单字符串：+OK\r\n
错误信息：-ERR unknown command\r\n
整数：:1\r\n
批量字符串：$6\r\nfoobar\r\n
数组：*2\r\n$3\r\nfoo\r\n$3\r\nbar\r\n
```

**一次SET命令的完整通信过程**：

```
客户端发送：*3\r\n$3\r\nSET\r\n$3\r\nkey\r\n$5\r\nvalue\r\n
服务端返回：+OK\r\n
```

**RESP3协议（Redis 6.0+）**：

RESP3在RESP2基础上新增了更多数据类型，支持更丰富的语义：

| 类型 | 前缀 | 说明 |
|------|------|------|
| Blob error | `!` | 详细错误信息（如`!21\r\nSYNTAX invalid syntax\r\n`） |
| Verbatim string | `=` | 原样字符串（如文本格式标注） |
| Big number | `(` | 大整数（超过有符号64位范围） |
| Map | `%` | 键值对集合（如`%2\r\n+foo\r\n:1\r\n+bar\r\n:2\r\n`） |
| Set | `~` | 集合类型 |
| Attribute | `|` | 属性/元数据 |
| Push | `>` | 推送消息（如键空间通知） |
| Boolean | `#` | 布尔值（`#t\r\n`或`#f\r\n`） |
| Double | `,` | 浮点数（如`,1.23\r\n`） |
| Null | `_` | 统一空值表示 |

```bash
# 切换到RESP3协议（客户端握手时协商）
HELLO 3
```

---

## 模块三：数据类型与底层实现

> Redis的强大之处在于为不同场景设计了专用的数据结构。理解底层实现，才能做出正确的数据类型选型。

### 3.1 字符串（String）

**功能说明**：存储字符串、数字、二进制数据（最大512MB）。

**底层实现：SDS（Simple Dynamic String）**

Redis没有直接使用C语言字符串，而是自定义了SDS：

```
C字符串：
┌─────┬─────┬─────┬─────┬─────┐
│ 'R' │ 'e' │ 'd' │ 'i' │ '\0'│
└─────┴─────┴─────┴─────┴─────┘

SDS：
┌──────────┬──────────┬─────┬─────┬─────┬─────┬─────┐
│ len = 4  │ free = 0 │ 'R' │ 'e' │ 'd' │ 'i' │ '\0'│
└──────────┴──────────┴─────┴─────┴─────┴─────┴─────┘
```

**SDS vs C字符串**：

| 特性 | C字符串 | SDS |
|------|---------|-----|
| 获取长度 | O(N)遍历 | O(1)直接读取len |
| 缓冲区溢出 | 可能溢出 | 空间预分配，自动扩容 |
| 修改内存重分配 | 每次N次 | 最多N次（预分配+惰性释放） |
| 二进制安全 | 靠'\0'判断结束 | 靠len判断结束 |
| 兼容C字符串 | - | 末尾保留'\0' |

**三种编码方式**：

| 编码 | 条件 | 说明 |
|------|------|------|
| int | 值为整数且≤long范围 | 直接存储在ptr指针位置 |
| embstr | 字符串长度≤44字节 | SDS和RedisObject一次分配，紧凑存储 |
| raw | 字符串长度>44字节 | SDS和RedisObject两次分配 |

**实战案例**：

```bash
# 设置字符串
SET name "张三"

# 获取字符串
GET name

# 自增（值必须为整数）
SET counter 0
INCR counter
INCRBY counter 10

# 自减
DECR counter
DECRBY counter 5

# 浮点数增减
SET price 10.50
INCRBYFLOAT price 0.5

# 设置过期时间（10秒）
SETEX key 10 "value"

# 仅当key不存在时设置（常用于分布式锁）
SET lock:order:1 "uuid" EX 10 NX

# 仅当key存在时设置
SET key "new_value" XX

# 追加字符串
SET msg "hello"
APPEND msg " world"

# 获取字符串长度
STRLEN msg

# 获取子串
GETRANGE msg 0 4

# 检查键是否存在
EXISTS name

# 删除键
DEL name

# 同时设置多个键值
MSET key1 "value1" key2 "value2" key3 "value3"

# 同时获取多个键值
MGET key1 key2 key3
```

### 3.2 列表（List）

**功能说明**：存储有序的字符串列表，支持从两端插入和弹出。

**底层实现：quicklist（双向链表 + listpack）**

Redis 7.0中，List的底层实现为quicklist（由listpack节点组成的双向链表）：

```
quicklist结构：
┌─────────────────────────────────────────────────┐
│  prev ──► ┌──────────┐ ◄── next ──► ...        │
│           │ listpack │                           │
│           │(压缩节点) │                           │
│           └──────────┘                           │
└─────────────────────────────────────────────────┘

单个listpack节点：
┌───────┬───────┬───────┬───────┬───────┬───────┐
│ elem1 │ elem2 │ elem3 │ elem4 │  ...  │ end   │
└───────┴───────┴───────┴───────┴───────┴───────┘
```

**编码方式**：

| 编码 | 条件 | 说明 |
|------|------|------|
| quicklist（listpack节点） | 元素数量≤128且每个元素≤64字节 | listpack节点不压缩，紧凑存储 |
| quicklist（压缩listpack节点） | 不满足上述条件 | listpack节点启用LZF压缩，兼顾内存和性能 |

> **注意**：Redis 7.0中List统一使用quicklist编码（由listpack节点组成的双向链表），不再有单独的listpack编码。quicklist根据节点大小自动决定是否压缩。

**实战案例**：

```bash
# 从左侧添加元素（结果：orange banana apple）
LPUSH fruits "apple"
LPUSH fruits "banana"
LPUSH fruits "orange"

# 从右侧添加元素
RPUSH fruits "grape"

# 获取列表所有元素
LRANGE fruits 0 -1

# 从左侧弹出元素
LPOP fruits

# 从右侧弹出元素
RPOP fruits

# 阻塞式弹出（超时0表示永久等待，单位秒）
BLPOP fruits 30

# 获取列表长度
LLEN fruits

# 根据索引获取元素
LINDEX fruits 0

# 根据索引设置元素
LSET fruits 0 "pear"

# 在指定元素前后插入
LINSERT fruits BEFORE "apple" "mango"
LINSERT fruits AFTER "apple" "peach"

# 裁剪列表，只保留指定范围
LTRIM fruits 0 2

# 将元素从一个列表移到另一个列表
RPOPLPUSH fruits fruits_backup
```

### 3.3 哈希（Hash）

**功能说明**：存储字段-值对的集合，适合存储对象。

**底层实现：listpack + hashtable**

```
编码选择逻辑：
元素数量 ≤ 128 且每个值 ≤ 64字节？
    ├── 是 → listpack（紧凑存储）
    └── 否 → hashtable（哈希表）

listpack结构（连续内存）：
┌────────┬────────┬────────┬────────┬────────┐
│ field1 │ val1   │ field2 │ val2   │  end   │
└────────┴────────┴────────┴────────┴────────┘

hashtable结构：
┌─────────────────────────────────────┐
│              哈希表                  │
│  ┌─────┬─────┬─────┬─────┬─────┐   │
│  │桶0  │桶1  │桶2  │桶3  │ ... │   │
│  └──┬──┴─────┴──┬──┴─────┴─────┘   │
│     ▼           ▼                   │
│  ┌──────┐    ┌──────┐              │
│  │dictEntry│  │dictEntry│            │
│  │key→val │  │key→val │            │
│  └──────┘    └──────┘              │
└─────────────────────────────────────┘
```

**渐进式rehash**：当哈希表需要扩容/缩容时，Redis采用渐进式rehash，将迁移工作分摊到每次操作中，避免一次性迁移导致阻塞。

```
rehash过程：
┌──────────────┐     ┌──────────────┐
│   旧哈希表    │ ──► │   新哈希表    │
│  (ht[0])     │     │  (ht[1])     │
│  逐步迁移 ←──┤     ├──→ 逐步接收  │
└──────────────┘     └──────────────┘
```

**实战案例**：

```bash
# 设置哈希字段
HSET user:1 name "张三" age 25 email "zhangsan@example.com"

# 获取单个哈希字段
HGET user:1 name

# 获取多个哈希字段
HMGET user:1 name age email

# 获取所有哈希字段和值
HGETALL user:1

# 获取所有哈希字段
HKEYS user:1

# 获取所有哈希值
HVALS user:1

# 检查哈希字段是否存在
HEXISTS user:1 age

# 删除哈希字段
HDEL user:1 email

# 获取哈希字段数量
HLEN user:1

# 字段值自增
HINCRBY user:1 age 1

# 仅当字段不存在时设置
HSETNX user:1 phone "13800138000"
```

### 3.4 集合（Set）

**功能说明**：存储无序的唯一元素集合，支持集合运算。

**底层实现：intset + hashtable**

| 编码 | 条件 | 说明 |
|------|------|------|
| intset | 元素都是整数且数量≤128 | 有序整数数组，内存紧凑 |
| hashtable | 不满足intset条件 | 哈希表，O(1)查找 |

```
intset结构（有序整数数组）：
┌───────┬───────┬───────┬───────┐
│  1    │  3    │  5    │  7    │
└───────┴───────┴───────┴───────┘
（二分查找，O(logN)）
```

**实战案例**：

```bash
# 添加元素
SADD tags "java" "python" "javascript"

# 获取所有元素
SMEMBERS tags

# 检查元素是否存在
SISMEMBER tags "java"

# 删除元素
SREM tags "javascript"

# 获取集合大小
SCARD tags

# 随机获取一个元素
SRANDMEMBER tags

# 随机弹出一个元素
SPOP tags

# 将元素从一个集合移到另一个集合
SMOVE tags tags_backup "python"

# 交集
SADD set1 "a" "b" "c"
SADD set2 "b" "c" "d"
SINTER set1 set2
# 结果：b c

# 交集存储到新集合
SINTERSTORE set3 set1 set2

# 并集
SUNION set1 set2

# 差集（set1有但set2没有的）
SDIFF set1 set2
```

### 3.5 有序集合（Sorted Set）

**功能说明**：存储有序的唯一元素集合，每个元素关联一个分数（score），按分数排序。

**底层实现：listpack + skiplist + hashtable**

```
编码选择逻辑：
元素数量 ≤ 128 且每个元素 ≤ 64字节？
    ├── 是 → listpack
    └── 否 → skiplist + hashtable

skiplist结构（多层索引链表）：
Level 4:  ─────────────────────────────────► header → N4
Level 3:  ─────────────────────► N1 ──────► N4
Level 2:  ──────────► N1 ─────► N3 ──────► N4
Level 1:  ► N1 ────► N2 ────► N3 ──────► N4
（查找O(logN)，插入/删除O(logN)）

同时维护一个hashtable，用于O(1)获取元素分数
```

**为什么用跳表而不是红黑树？**

| 对比项 | 跳表 | 红黑树 |
|--------|------|--------|
| 实现复杂度 | 简单，易于理解和调试 | 复杂，旋转操作多 |
| 范围查询 | 天然支持（链表遍历） | 需要中序遍历 |
| 内存占用 | 额外指针（每层2个） | 额外指针（3个+颜色位） |
| 并发友好 | 更容易实现无锁并发 | 需要复杂的锁策略 |
| 插入/删除 | O(logN)，概率平衡 | O(logN)，旋转平衡 |

**实战案例**：

```bash
# 添加元素（分数，值）
ZADD scores 90 "张三" 85 "李四" 95 "王五" 88 "赵六"

# 按分数升序获取元素
ZRANGE scores 0 -1

# 按分数降序获取元素
ZREVRANGE scores 0 -1

# 带分数获取
ZRANGE scores 0 -1 WITHSCORES

# 按分数范围获取元素
ZRANGEBYSCORE scores 80 90

# 按分数范围获取（不含边界）
ZRANGEBYSCORE scores (80 90

# 获取元素分数
ZSCORE scores "张三"

# 增加元素分数
ZINCRBY scores 5 "张三"

# 获取元素排名（升序，从0开始）
ZRANK scores "张三"

# 获取元素排名（降序）
ZREVRANK scores "张三"

# 获取分数范围内的元素数量
ZCOUNT scores 80 90

# 获取集合大小
ZCARD scores

# 删除元素
ZREM scores "李四"

# 按排名范围删除
ZREMRANGEBYRANK scores 0 1

# 按分数范围删除
ZREMRANGEBYSCORE scores 0 60

# 有序集合交集
ZADD math 90 "张三" 80 "李四"
ZADD english 85 "张三" 90 "李四"
ZINTERSTORE total 2 math english WEIGHTS 0.5 0.5
```

### 3.6 位图（Bitmap）

**功能说明**：基于String类型实现的位级别操作，适合布尔型数据统计。

**底层实现**：直接使用String类型，将字符串视为位数组。

**实战案例**：

```bash
# 设置位（用户0和用户2在20230101登录了）
SETBIT user:login:20230101 0 1
SETBIT user:login:20230101 1 0
SETBIT user:login:20230101 2 1

# 获取位
GETBIT user:login:20230101 0
# 结果：1

# 统计置位的位数（登录人数）
BITCOUNT user:login:20230101
# 结果：2

# 指定范围统计
BITCOUNT user:login:20230101 0 1

# 位操作
SETBIT bitmap1 0 1
SETBIT bitmap1 1 1
SETBIT bitmap2 1 1
SETBIT bitmap2 2 1

# AND运算（连续两天都登录的用户）
BITOP AND result bitmap1 bitmap2

# OR运算（任意一天登录的用户）
BITOP OR result bitmap1 bitmap2

# XOR运算
BITOP XOR result bitmap1 bitmap2

# 查找第一个置位/清位的位置
BITPOS user:login:20230101 1
```

**典型场景**：用户签到、在线状态、特征标记。

### 3.7 HyperLogLog

**功能说明**：用于基数统计（去重计数），标准误差0.81%，固定消耗12KB内存。

**底层原理**：基于概率统计算法，通过哈希值的前导零个数来估算基数。

**实战案例**：

```bash
# 添加元素
PFADD unique_visitors "user1" "user2" "user3"
PFADD unique_visitors "user1"

# 统计基数
PFCOUNT unique_visitors
# 结果：3（user1重复不计）

# 合并多个HyperLogLog
PFADD unique_visitors2 "user4" "user5"
PFMERGE merged_visitors unique_visitors unique_visitors2
PFCOUNT merged_visitors
# 结果：5
```

**HyperLogLog vs Set 去重对比**：

| 对比项 | Set | HyperLogLog |
|--------|-----|-------------|
| 内存占用 | 随元素数量增长 | 固定12KB |
| 精确度 | 100%精确 | 0.81%标准误差 |
| 适用场景 | 需要精确计数 | 允许误差的大规模统计 |

### 3.8 地理位置（Geo）

**功能说明**：存储地理位置信息，支持距离计算和范围查询。

**底层实现**：基于Sorted Set，使用GeoHash编码将经纬度转换为分数。

**实战案例**：

```bash
# 添加地理位置（经度 纬度 成员）
GEOADD cities 116.4074 39.9042 "北京"
GEOADD cities 121.4737 31.2304 "上海"
GEOADD cities 113.2644 23.1291 "广州"
GEOADD cities 114.0579 22.5431 "深圳"

# 计算两个位置之间的距离
GEODIST cities "北京" "上海" km
# 结果：约1067.5km

# 根据坐标范围获取位置（北京周围1000km内的城市）
GEOSEARCH cities FROMMEMBER "北京" BYRADIUS 1000 km WITHDIST WITHCOORD

# 根据矩形范围搜索
GEOSEARCH cities FROMLONLAT 116.4 39.9 BYBOX 1000 1000 km

# 根据成员获取坐标
GEOPOS cities "北京"

# 获取成员的GeoHash值
GEOHASH cities "北京"
```

### 3.9 流（Stream）— Redis 5.0+

**功能说明**：类似Kafka的消息流，支持消费组、消息确认、持久化，是Redis中最完善的消息队列实现。

**底层实现：Radix Tree（基数树）**

```
Radix Tree结构（压缩前缀树）：
        root
         │
    ┌────┴────┐
    │         │
  152694   152695
  (entry)  (entry)
    │         │
  next ───► next ───► ...
```

**实战案例**：

```bash
# 添加消息（*表示自动生成ID）
XADD mystream * name "张三" action "登录"
XADD mystream * name "李四" action "下单"

# 查看消息数量
XLEN mystream

# 读取消息（从开头读取2条）
XRANGE mystream - + COUNT 2

# 按ID范围读取
XRANGE mystream 152694-0 152695-0

# 创建消费组
XGROUP CREATE mystream group1 0

# 从消费组读取消息（未被消费的消息，阻塞等待5秒）
XREADGROUP GROUP group1 consumer1 COUNT 1 BLOCK 5000 STREAMS mystream >

# 确认消息已处理
XACK mystream group1 <message_id>

# 查看待处理消息
XPENDING mystream group1

# 读取待处理消息（重新分配给当前消费者）
XAUTOCLAIM mystream group1 consumer1 60000 0-0 COUNT 1

# 限制Stream长度（保留最近1000条）
XTRIM mystream MAXLEN 1000

# 添加消息时限制长度（更高效）
XADD mystream MAXLEN 1000 * name "王五" action "退出"
```

**Stream vs List vs Pub/Sub 消息队列对比**：

| 特性 | Stream | List | Pub/Sub |
|------|--------|------|---------|
| 消息持久化 | ✅ | ✅ | ❌ |
| 消费组 | ✅ | ❌ | ❌ |
| 消息确认 | ✅ | ❌ | ❌ |
| 消息回溯 | ✅ | ❌ | ❌ |
| 阻塞读取 | ✅ | ✅ | ✅ |
| 历史消息 | ✅ 可回溯 | ❌ 弹出即删 | ❌ 不存储 |
| 适用场景 | 可靠消息队列 | 简单任务队列 | 实时通知广播 |

### 3.10 数据类型选型对比表

| 数据类型 | 底层编码 | 时间复杂度 | 典型场景 | 内存效率 |
|----------|----------|------------|----------|----------|
| String | int/embstr/raw | O(1) | 缓存、计数器、分布式锁 | 中 |
| List | quicklist(listpack) | 头尾O(1)，中间O(N) | 消息队列、最新列表 | 高 |
| Hash | listpack/hashtable | O(1) | 对象存储、配置 | 高 |
| Set | intset/hashtable | O(1) | 标签、去重、共同好友 | 中 |
| Sorted Set | listpack/skiplist+hashtable | O(logN) | 排行榜、延迟队列 | 中 |
| Bitmap | String | O(1) | 签到、在线状态 | 极高 |
| HyperLogLog | 稀疏/密集 | O(1) | UV统计 | 极高(12KB) |
| Geo | Sorted Set | O(logN) | 位置服务 | 中 |
| Stream | Radix Tree | O(logN) | 消息队列 | 高 |

---

## 模块四：持久化

> 持久化是Redis将内存数据保存到磁盘的机制，决定了Redis在重启后数据是否丢失。

### 4.1 RDB持久化

**功能说明**：将Redis某一时刻的内存数据以二进制快照形式保存到磁盘。

**原理**：Redis通过fork子进程来生成RDB文件，利用操作系统的**写时复制（Copy-On-Write）**机制，父进程继续处理命令，子进程遍历内存生成快照。

```
RDB生成过程：
┌──────────────┐    fork()    ┌──────────────┐
│  父进程       │ ──────────► │  子进程       │
│  (处理命令)   │   COW机制   │  (生成RDB)    │
│  修改数据时   │ 共享内存页   │  读取内存     │
│  才复制页     │             │  写入磁盘     │
└──────────────┘             └──────────────┘
```

**触发方式**：

| 触发方式 | 命令/配置 | 说明 |
|----------|-----------|------|
| 手动同步 | `SAVE` | 阻塞主线程，不推荐 |
| 手动异步 | `BGSAVE` | fork子进程，推荐 |
| 自动触发 | `save 900 1` | 900秒内至少1个key变更 |
| 自动触发 | `save 300 10` | 300秒内至少10个key变更 |
| 自动触发 | `save 60 10000` | 60秒内至少10000个key变更 |
| 关闭触发 | `SHUTDOWN` | 安全关闭时自动执行 |

**实战案例**：

```bash
# 手动触发RDB持久化（后台执行）
BGSAVE

# 查看RDB配置
CONFIG GET dir
CONFIG GET dbfilename

# 修改RDB自动保存策略
CONFIG SET save "900 1 300 10 60 10000"

# 查看最近一次RDB保存状态
LASTSAVE

# 禁用RDB（删除所有save配置）
CONFIG SET save ""

# 从RDB文件恢复数据
# 将dump.rdb文件放到Redis的dir目录下，启动Redis即可自动加载
```

**RDB文件结构**：

```
┌────────┬──────────┬──────────┬──────────┬──────────┬──────────┐
│ REDIS  │ db_version│ databases│  EOF    │  check_sum │
│(魔数)  │ (版本号)  │ (数据)   │(结束标记)│ (校验和)   │
└────────┴──────────┴──────────┴──────────┴──────────┴──────────┘
```

### 4.2 AOF持久化

**功能说明**：将Redis写命令以追加方式写入日志文件，实时性更高。

**原理**：每执行一条写命令，就将该命令追加到AOF缓冲区，再根据同步策略写入磁盘。

```
AOF写入流程：
命令执行 → 写入AOF缓冲区 → 根据策略同步到磁盘 → AOF重写（压缩）

┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ 命令执行  │───►│ AOF缓冲区 │───►│ OS缓冲区  │───►│  磁盘文件  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
                                 │
                          fsync策略控制
```

**三种同步策略**：

| 策略 | 说明 | 数据安全性 | 性能 |
|------|------|-----------|------|
| always | 每条命令都fsync | 最高，最多丢1条 | 最低 |
| everysec | 每秒fsync一次 | 最多丢1秒数据 | 折中（推荐） |
| no | 由OS决定何时fsync | 可能丢较多数据 | 最高 |

**AOF重写**：随着命令不断追加，AOF文件会越来越大。AOF重写通过读取当前数据库状态，用最少的命令重新生成AOF文件。

```
重写前AOF：
SET counter 1
INCR counter
INCR counter
INCR counter
（4条命令）

重写后AOF：
SET counter 4
（1条命令，等效结果）
```

**实战案例**：

```bash
# 开启AOF持久化
CONFIG SET appendonly yes

# 设置AOF同步策略
CONFIG SET appendfsync everysec

# 手动触发AOF重写
BGREWRITEAOF

# 查看AOF配置
CONFIG GET appendonly
CONFIG GET appendfsync
CONFIG GET auto-aof-rewrite-percentage
CONFIG GET auto-aof-rewrite-min-size

# AOF重写时是否禁用fsync（避免大量磁盘I/O）
CONFIG SET no-appendfsync-on-rewrite yes
```

### 4.3 混合持久化

**功能说明**：结合RDB和AOF的优点，AOF重写时前半部分用RDB格式（加载快），后半部分用AOF格式（增量数据不丢失）。

```
混合持久化AOF文件结构：
┌────────────────────┬─────────────────────┐
│   RDB格式数据       │   AOF格式增量数据     │
│  （全量快照，加载快） │  （增量命令，数据完整） │
└────────────────────┴─────────────────────┘
```

**实战案例**：

```bash
# 开启混合持久化
CONFIG SET aof-use-rdb-preamble yes

# 查看混合持久化配置
CONFIG GET aof-use-rdb-preamble
```

### 4.4 持久化方案对比与选型

| 对比项 | RDB | AOF | 混合持久化 |
|--------|-----|-----|-----------|
| 数据安全性 | 可能丢失数分钟数据 | 最多丢1秒 | 最多丢1秒 |
| 文件大小 | 小（二进制压缩） | 大（命令日志） | 中 |
| 恢复速度 | 快（直接加载） | 慢（重放命令） | 快（RDB部分快） |
| 系统资源 | fork时可能影响性能 | 写入频繁影响性能 | 折中 |
| 适用场景 | 备份、灾备 | 数据安全要求高 | 推荐生产使用 |

**生产环境推荐配置**：

```bash
# 开启混合持久化
CONFIG SET appendonly yes
CONFIG SET aof-use-rdb-preamble yes
CONFIG SET appendfsync everysec

# RDB作为辅助备份
CONFIG SET save "900 1 300 10 60 10000"
```

---

## 模块五：发布订阅

### 5.1 基本发布订阅

**功能说明**：实现消息的发布与订阅，支持一对多的消息广播。

**原理**：发布者将消息发送到频道，所有订阅该频道的客户端都能收到消息。Redis内部维护一个 `pubsub_channels` 字典，key为频道名，value为订阅该频道的客户端链表。

```
发布订阅模型：
┌──────────┐                    ┌──────────┐
│ 发布者    │ ── PUBLISH ──────► │ 频道      │
└──────────┘                    └────┬─────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
              ┌──────────┐    ┌──────────┐    ┌──────────┐
              │ 订阅者1   │    │ 订阅者2   │    │ 订阅者3   │
              └──────────┘    └──────────┘    └──────────┘
```

**实战案例**：

```bash
# 终端1：订阅频道
SUBSCRIBE channel1 channel2

# 终端2：发布消息
PUBLISH channel1 "Hello Redis"

# 终端1：收到消息
# 1) "message"
# 2) "channel1"
# 3) "Hello Redis"

# 取消订阅
UNSUBSCRIBE channel1

# 查看活跃频道
PUBSUB CHANNELS

# 查看频道订阅数
PUBSUB NUMSUB channel1
```

### 5.2 模式订阅

**功能说明**：使用通配符订阅匹配的频道。

```bash
# 订阅所有以news开头的频道
PSUBSCRIBE news.*

# 发布到具体频道
PUBLISH news.sports "体育新闻"
PUBLISH news.tech "科技新闻"

# 取消模式订阅
PUNSUBSCRIBE news.*
```

### 5.3 Stream与Pub/Sub对比

| 特性 | Pub/Sub | Stream |
|------|---------|--------|
| 消息持久化 | ❌ 离线丢失 | ✅ 持久存储 |
| 消费确认 | ❌ | ✅ ACK机制 |
| 消息回溯 | ❌ | ✅ 按ID读取 |
| 消费组 | ❌ | ✅ 多消费者 |
| 适用场景 | 实时通知、事件广播 | 可靠消息队列 |

> **重要提示**：Pub/Sub的消息不会持久化，如果订阅者离线，消息将丢失。需要可靠消息传递请使用Stream。

---

## 模块六：复制

### 6.1 主从复制原理

**功能说明**：将主服务器的数据复制到从服务器，实现读写分离和数据备份。

**复制过程**：

```
全量同步流程：
┌──────────┐                          ┌──────────┐
│  从服务器  │                          │  主服务器  │
└─────┬────┘                          └─────┬────┘
      │  1. PSYNC ? -1 (首次连接)            │
      │ ──────────────────────────────────► │
      │                                     │
      │  2. FULLRESYNC <runid> <offset>     │
      │ ◄────────────────────────────────── │
      │                                     │
      │  3. 主服务器执行BGSAVE               │
      │     生成RDB文件                      │
      │                                     │
      │  4. 发送RDB文件                      │
      │ ◄────────────────────────────────── │
      │                                     │
      │  5. 发送缓冲区增量命令               │
      │ ◄────────────────────────────────── │
      │                                     │
      │  6. 加载RDB + 执行增量命令           │
      │     数据同步完成                     │
      └─────────────────────────────────────┘

增量同步流程（Redis 2.8+）：
┌──────────┐                          ┌──────────┐
│  从服务器  │                          │  主服务器  │
└─────┬────┘                          └─────┬────┘
      │  1. PSYNC <runid> <offset>          │
      │ ──────────────────────────────────► │
      │                                     │
      │  2. CONTINUE (offset在复制缓冲区内)  │
      │ ◄────────────────────────────────── │
      │                                     │
      │  3. 发送offset之后的增量数据          │
      │ ◄────────────────────────────────── │
      └─────────────────────────────────────┘
```

**复制积压缓冲区**：主服务器维护一个固定长度的FIFO队列（默认1MB），用于增量复制。如果从服务器断开时间过长，offset超出缓冲区范围，则需要全量同步。

```bash
# 调整复制积压缓冲区大小
CONFIG SET repl-backlog-size 64mb
```

### 6.2 主从复制配置

**实战案例**：

```bash
# 在从服务器上执行（Redis 5.0+使用REPLICAOF）
REPLICAOF 127.0.0.1 6379

# 查看复制状态
INFO replication

# 取消复制（从服务器变为主服务器）
REPLICAOF NO ONE

# 从服务器默认只读
CONFIG SET replica-read-only yes
```

**配置文件方式**：

```conf
# 主服务器配置（redis.conf）
bind 0.0.0.0
protected-mode no

# 从服务器配置（redis.conf）
replicaof 127.0.0.1 6379
replica-read-only yes
```

### 6.3 复制拓扑结构

```
一主一从：                一主多从：                级联复制：
┌──────┐                ┌──────┐                ┌──────┐
│ Master│                │ Master│                │ Master│
└──┬───┘                └─┬──┬─┘                └──┬───┘
   │                      │  │                      │
   ▼                   ┌──┘  └──┐                ┌──┘
┌──────┐               ▼       ▼                ▼
│ Slave│           ┌──────┐ ┌──────┐        ┌──────┐
└──────┘           │Slave1│ │Slave2│        │Slave1│
                   └──────┘ └──────┘        └──┬───┘
                                              │
                                           ┌──┘
                                           ▼
                                       ┌──────┐
                                       │Slave2│
                                       └──────┘
```

**拓扑选型建议**：

| 拓扑 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| 一主一从 | 简单 | 从故障无备份 | 开发测试 |
| 一主多从 | 读扩展性好 | 主节点同步压力大 | 读多写少 |
| 级联复制 | 减轻主节点同步压力 | 延迟增大 | 大规模部署 |

---

## 模块七：哨兵（Sentinel）

### 7.1 哨兵架构原理

**功能说明**：监控Redis主从集群，自动进行故障检测和转移。

**哨兵三大任务**：

| 任务 | 说明 |
|------|------|
| 监控（Monitoring） | 定期向主从服务器发送PING命令检测存活 |
| 通知（Notification） | 被监控的Redis实例出问题时通知管理员或应用 |
| 自动故障转移（Failover） | 主服务器故障时自动将从服务器升级为主服务器 |

**故障检测机制**：

```
主观下线（SDOWN）：
单个哨兵认为实例下线（PING超时）

客观下线（ODOWN）：
超过quorum数量的哨兵认为主服务器下线
→ 触发故障转移

┌────────┐  ┌────────┐  ┌────────┐
│Sentinel1│  │Sentinel2│  │Sentinel3│
│ SDOWN   │  │ SDOWN   │  │ SDOWN   │
└────┬───┘  └────┬───┘  └────┬───┘
     │           │           │
     └───────────┼───────────┘
                 ▼
           ODOWN（2/3同意）
                 │
                 ▼
           故障转移开始
```

### 7.2 配置哨兵

**实战案例**：

```conf
# sentinel.conf配置文件
# 监控主服务器（名称 IP 端口 quorum）
sentinel monitor mymaster 127.0.0.1 6379 2

# 主服务器无响应超时时间（毫秒）
sentinel down-after-milliseconds mymaster 30000

# 故障转移超时时间（毫秒）
sentinel failover-timeout mymaster 180000

# 故障转移后同时向新主服务器发起复制的从服务器数量
sentinel parallel-syncs mymaster 1
```

**quorum参数说明**：quorum表示需要多少个哨兵同意才能判定主服务器客观下线。建议哨兵数量为奇数（3或5），quorum设为 `n/2 + 1`。

### 7.3 启动哨兵

**实战案例**：

```bash
# 启动哨兵
redis-sentinel sentinel.conf

# 后台启动哨兵
redis-sentinel sentinel.conf --daemonize yes

# 使用redis-server启动哨兵模式
redis-server sentinel.conf --sentinel
```

### 7.4 故障转移流程

```
故障转移步骤：
1. 哨兵检测到主服务器客观下线（ODOWN）
2. 哨兵集群选举Leader哨兵执行故障转移
3. Leader哨兵选择一个从服务器升级为主服务器
   选择规则：
   a. 排除已下线或断线的从服务器
   b. 优先选择复制偏移量最大的（数据最新）
   c. 偏移量相同选择runid最小的
4. 其他从服务器改为复制新主服务器
5. 更新哨兵监控配置
6. 旧主服务器恢复后变为新主服务器的从服务器
```

### 7.5 查看哨兵状态

**实战案例**：

```bash
# 连接哨兵
redis-cli -p 26379

# 查看哨兵状态
INFO sentinel

# 查看主服务器信息
SENTINEL get-master-addr-by-name mymaster

# 查看主服务器下从服务器列表
SENTINEL slaves mymaster

# 查看其他哨兵信息
SENTINEL sentinels mymaster

# 重置主服务器（清除故障转移状态）
SENTINEL reset mymaster

# 强制故障转移（手动切换）
SENTINEL failover mymaster
```

---

## 模块八：集群（Cluster）

### 8.1 集群架构原理

**功能说明**：Redis Cluster提供自动数据分片和高可用性，支持水平扩展。

**核心特性**：
- **去中心化**：无中心节点，所有节点互相通信（Gossip协议）
- **数据分片**：16384个哈希槽分布在不同节点
- **高可用**：每个主节点可以有从节点，主节点故障自动故障转移

```
Redis Cluster架构（3主3从）：
┌─────────────────────────────────────────────────┐
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Master1  │  │ Master2  │  │ Master3  │      │
│  │槽0-5460  │  │槽5461-   │  │槽10923-  │      │
│  │          │  │  10922   │  │  16383   │      │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘      │
│       │              │              │            │
│  ┌────┴─────┐  ┌────┴─────┐  ┌────┴─────┐      │
│  │ Slave1   │  │ Slave2   │  │ Slave3   │      │
│  │(Master1  │  │(Master2  │  │(Master3  │      │
│  │ 的从节点) │  │ 的从节点) │  │ 的从节点) │      │
│  └──────────┘  └──────────┘  └──────────┘      │
│                                                  │
│  节点间通过Gossip协议通信                         │
└─────────────────────────────────────────────────┘
```

### 8.2 数据分片与哈希槽

**哈希槽分配算法**：

```
slot = CRC16(key) % 16384

示例：
key = "user:1001"
CRC16("user:1001") = 12567
slot = 12567 % 16384 = 12567
→ 路由到负责槽12567的节点
```

**哈希标签**：使用 `{}` 将key的部分内容作为哈希计算依据，确保相关key在同一个槽。

```bash
# 这两个key会在同一个槽（只计算{}内的部分）
SET {user}.1001:name "张三"
SET {user}.1001:age 25

# 查看key所在的槽
CLUSTER KEYSLOT {user}.1001:name
CLUSTER KEYSLOT {user}.1001:age
# 结果相同
```

### 8.3 创建集群

**实战案例**：

```bash
# 准备6个Redis实例的配置文件（redis-7000.conf ~ redis-7005.conf）
# 每个配置文件核心内容：
# port 7000
# cluster-enabled yes
# cluster-config-file nodes-7000.conf
# cluster-node-timeout 5000
# appendonly yes

# 启动6个Redis实例
redis-server redis-7000.conf
redis-server redis-7001.conf
redis-server redis-7002.conf
redis-server redis-7003.conf
redis-server redis-7004.conf
redis-server redis-7005.conf

# 创建集群（--cluster-replicas 1表示每个主节点1个从节点）
redis-cli --cluster create \
  127.0.0.1:7000 127.0.0.1:7001 127.0.0.1:7002 \
  127.0.0.1:7003 127.0.0.1:7004 127.0.0.1:7005 \
  --cluster-replicas 1
```

### 8.4 连接集群

**实战案例**：

```bash
# 连接集群（-c启用集群模式，自动重定向）
redis-cli -c -p 7000

# 查看集群信息
CLUSTER INFO

# 查看集群节点
CLUSTER NODES

# 查看哈希槽分布
CLUSTER SLOTS
```

### 8.5 集群操作

**实战案例**：

```bash
# 设置键值（集群会自动路由到正确节点）
SET key value

# 获取键值
GET key

# 添加新主节点
redis-cli --cluster add-node 127.0.0.1:7006 127.0.0.1:7000

# 添加新从节点（指定主节点ID）
redis-cli --cluster add-node 127.0.0.1:7007 127.0.0.1:7000 --cluster-slave --cluster-master-id <master_id>

# 重新分片（将槽迁移到新节点）
redis-cli --cluster reshard 127.0.0.1:7000

# 删除节点
redis-cli --cluster del-node 127.0.0.1:7000 <node_id>

# 检查集群状态
redis-cli --cluster check 127.0.0.1:7000

# 修复集群
redis-cli --cluster fix 127.0.0.1:7000
```

### 8.6 集群限制与注意事项

| 限制 | 说明 | 解决方案 |
|------|------|----------|
| 批量操作限制 | `MGET`等批量命令的key必须在同一槽 | 使用哈希标签`{}` |
| 事务限制 | 事务中的key必须在同一槽 | 使用哈希标签`{}` |
| 数据库限制 | 只能使用DB0 | 无 |
| key批量操作 | `KEYS *`只能看到当前节点的key | 使用`CLUSTER GETKEYSINSLOT` |
| 集群规模 | 官方建议不超过500个节点，实际生产建议控制在百级 | 合理规划分片 |

---

## 模块九：事务与Lua脚本

### 9.1 基本事务

**功能说明**：将多个命令打包，按顺序一次性执行，中间不会被其他命令插入。

**原理**：事务通过`MULTI`命令开启，之后的命令进入队列，`EXEC`时按顺序执行。

```
事务执行流程：
┌────────┐    ┌──────────────┐    ┌──────────┐
│ MULTI  │───►│ 命令入队      │───►│  EXEC    │
│(开启)  │    │ SET key1 v1  │    │ (执行)   │
│        │    │ SET key2 v2  │    │ 返回结果  │
│        │    │ GET key1     │    │          │
└────────┘    └──────────────┘    └──────────┘
```

**注意**：Redis事务**不支持回滚**。如果事务中某条命令执行失败，其他命令仍会继续执行。

**实战案例**：

```bash
# 开始事务
MULTI

# 执行命令（进入队列）
SET key1 "value1"
SET key2 "value2"
GET key1

# 提交事务
EXEC

# 放弃事务（队列中的命令不会执行）
# DISCARD
```

### 9.2 事务冲突（WATCH）

**功能说明**：监视一个或多个key，如果在事务执行前这些key被其他客户端修改，事务将失败。

**原理**：WATCH实现乐观锁，基于CAS（Compare-And-Swap）思想。

```
WATCH机制：
客户端A                    客户端B
WATCH key
MULTI
SET key "new_A"
                            SET key "new_B"  ← 修改了被WATCH的key
EXEC → 返回nil（事务失败）
```

**实战案例**：

```bash
# 监视键
WATCH key

# 开始事务
MULTI

# 执行命令
SET key "new value"

# 提交事务（如果key被其他客户端修改，返回nil）
EXEC

# 取消监视
UNWATCH
```

**应用场景**：商品库存扣减、账户余额变更等需要防止并发冲突的场景。

### 9.3 Lua脚本

**功能说明**：在Redis中原子性执行Lua脚本，保证脚本中的命令不会被其他命令插入。

**原理**：Redis内置Lua解释器，脚本执行期间Redis主线程被占用，保证原子性。

**实战案例**：

```bash
# 执行简单Lua脚本
EVAL "return redis.call('SET', KEYS[1], ARGV[1])" 1 mykey myvalue

# 执行复杂Lua脚本（限流器）
EVAL "
local current = redis.call('GET', KEYS[1])
if current and tonumber(current) >= tonumber(ARGV[1]) then
    return 0
end
current = redis.call('INCR', KEYS[1])
if tonumber(current) == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 1
" 1 rate_limit:api 10 60

# 缓存Lua脚本（返回SHA1校验和）
SCRIPT LOAD "return redis.call('GET', KEYS[1])"

# 执行缓存的脚本（减少网络传输）
EVALSHA <script_sha> 1 mykey

# 查看脚本是否存在
SCRIPT EXISTS <script_sha>

# 清除所有缓存的脚本
SCRIPT FLUSH
```

**Lua脚本中可用的Redis调用**：

| 函数 | 说明 |
|------|------|
| `redis.call(cmd, ...)` | 执行Redis命令，出错时抛出异常 |
| `redis.pcall(cmd, ...)` | 执行Redis命令，出错时返回错误对象 |
| `KEYS[n]` | 脚本参数：键名（必须通过KEYS传入） |
| `ARGV[n]` | 脚本参数：附加参数 |

### 9.4 事务 vs Lua脚本 vs Pipeline对比

| 特性 | 事务(MULTI) | Lua脚本 | Pipeline |
|------|------------|---------|----------|
| 原子性 | 命令间不被插入 | 完全原子 | 无原子性 |
| 逻辑控制 | 不支持条件 | 完整Lua语法 | 无 |
| 网络开销 | 多次RTT | 1次RTT | 1次RTT |
| 错误处理 | 不支持回滚 | 完整错误处理 | 各自返回 |
| 适用场景 | 简单批量操作 | 复杂原子操作 | 批量命令减少RTT |

**Pipeline实战**：

```bash
# 使用管道（pipeline）执行多个命令
redis-cli << EOF
SET key1 value1
SET key2 value2
GET key1
GET key2
EOF
```

---

## 模块十：性能优化

### 10.1 内存管理

**内存淘汰策略**：

| 策略 | 说明 | 适用场景 |
|------|------|----------|
| noeviction | 不淘汰，写入报错 | 数据不能丢失 |
| allkeys-lru | 所有key中淘汰最久未使用的 | 通用缓存 |
| allkeys-lfu | 所有key中淘汰使用频率最低的 | 热点数据缓存 |
| allkeys-random | 所有key中随机淘汰 | 无访问热点 |
| volatile-lru | 设置了过期时间的key中淘汰最久未使用的 | 混合使用 |
| volatile-lfu | 设置了过期时间的key中淘汰频率最低的 | 混合使用 |
| volatile-random | 设置了过期时间的key中随机淘汰 | 混合使用 |
| volatile-ttl | 淘汰TTL最短的key | 业务有明确TTL |

```bash
# 查看内存使用情况
INFO memory

# 设置最大内存
CONFIG SET maxmemory 2gb

# 设置内存淘汰策略
CONFIG SET maxmemory-policy allkeys-lru

# 查看内存淘汰策略
CONFIG GET maxmemory-policy

# 查看淘汰统计
INFO stats | grep evicted
```

### 10.2 命令优化

**批量操作减少网络开销**：

```bash
# 不推荐：多次单条操作
SET key1 value1
SET key2 value2
SET key3 value3

# 推荐：批量操作
MSET key1 value1 key2 value2 key3 value3
MGET key1 key2 key3

# Hash批量操作
HMSET user:1 name "张三" age 25 email "zhangsan@example.com"
HMGET user:1 name age email
```

**避免使用危险命令**：

| 命令 | 危险原因 | 替代方案 |
|------|----------|----------|
| `KEYS *` | 阻塞主线程，O(N) | `SCAN 0 MATCH pattern* COUNT 100` |
| `HGETALL` | 大Hash阻塞 | `HSCAN key 0 COUNT 100` |
| `SMEMBERS` | 大Set阻塞 | `SSCAN key 0 COUNT 100` |
| `DEL` 大Key | 阻塞主线程 | `UNLINK`（异步删除） |
| `FLUSHALL` | 清空所有数据 | 禁用或重命名 |

```bash
# 使用SCAN代替KEYS
SCAN 0 MATCH user:* COUNT 100

# 使用UNLINK代替DEL删除大Key
UNLINK bigkey

# 设置慢查询阈值（微秒）
CONFIG SET slowlog-log-slower-than 10000

# 查看慢查询日志
SLOWLOG GET 10

# 查看慢查询长度
SLOWLOG LEN
```

### 10.3 连接管理

```bash
# 查看当前连接数
INFO clients

# 设置最大连接数
CONFIG SET maxclients 10000

# 查看客户端列表
CLIENT LIST

# 关闭空闲连接
CLIENT KILL <ip:port>

# 设置客户端空闲超时（秒）
CONFIG SET timeout 300
```

### 10.4 大Key与热Key优化

**大Key问题**：单个Key的Value过大（如>10KB），会导致：
- 内存不连续，增加碎片
- 操作阻塞主线程（DEL、HGETALL等）
- 网络传输慢

**大Key检测**：

```bash
# 查看Key的内存占用（Redis 4.0+）
MEMORY USAGE key

# 查看Key的类型和编码
DEBUG OBJECT key

# 使用redis-cli扫描大Key
redis-cli --bigkeys
```

**大Key解决方案**：

| 场景 | 方案 |
|------|------|
| 大String | 拆分为多个小String |
| 大Hash | 拆分为多个小Hash（如按ID范围分片） |
| 大List | 拆分为多个小List |
| 大Set | 拆分为多个小Set |
| 删除大Key | 使用`UNLINK`异步删除 |

```bash
# 大Hash拆分示例
# 原始：HSET user:all 1001 "{...}" 1002 "{...}" ... 100000 "{...}"
# 拆分后（按ID范围分片）：
HSET user:1 1001 "{...}" 1002 "{...}"    # 1001-2000
HSET user:2 2001 "{...}" 2002 "{...}"    # 2001-3000
# 或按哈希分片：
HSET user:shard:1 1001 "{...}" 3001 "{...}"  # hash(id) % 4 == 0
HSET user:shard:2 1002 "{...}" 3002 "{...}"  # hash(id) % 4 == 1
```

**热Key问题**：某个Key被高频访问，导致单个节点压力过大。

**热Key解决方案**：

| 方案 | 说明 |
|------|------|
| 本地缓存 | 在应用层缓存热Key数据 |
| 热Key分散 | 将热Key复制到多个Key，客户端随机访问 |
| 读写分离 | 从节点分担读压力 |
| 集群分片 | 将热Key分散到不同节点 |

### 10.5 缓存常见问题

#### 缓存穿透

**问题**：查询不存在的数据，请求直接打到数据库。

```
客户端 → 缓存（未命中）→ 数据库（未命中）→ 返回空
                ↑                          │
                └── 不写入缓存 ←────────────┘
                （下次仍穿透）
```

**解决方案**：

```bash
# 方案1：缓存空值
SET user:9999 "NULL" EX 60

# 方案2：布隆过滤器（在应用层实现，Redis可通过模块支持）
# 在查询缓存前先查布隆过滤器，不存在则直接返回
```

#### 缓存击穿

**问题**：热点Key过期瞬间，大量请求同时打到数据库。

**解决方案**：

```bash
# 方案1：设置热点Key永不过期
SET hot:key "value"

# 方案2：分布式锁，只允许一个请求重建缓存
SET lock:hot:key "1" EX 10 NX
# 获取锁的线程查数据库并重建缓存，其他线程等待或返回旧值
```

#### 缓存雪崩

**问题**：大量Key同时过期，请求全部打到数据库。

**解决方案**：

```bash
# 方案1：设置随机过期时间
SET product:1:name "iPhone 13" EX 3600
SET product:2:name "iPhone 14" EX 3660
SET product:3:name "iPhone 15" EX 3720

# 方案2：设置核心数据永不过期，由应用层更新
SET config:app "..."    # 不设置过期时间
```

**三种缓存问题对比**：

| 问题 | 原因 | 核心方案 |
|------|------|----------|
| 穿透 | 查询不存在的数据 | 缓存空值 + 布隆过滤器 |
| 击穿 | 热点Key过期 | 分布式锁 + 永不过期 |
| 雪崩 | 大量Key同时过期 | 随机过期时间 + 多级缓存 |

---

## 模块十一：安全

### 11.1 设置密码

**实战案例**：

```bash
# 设置密码
CONFIG SET requirepass your_password

# 验证密码
AUTH your_password

# 查看密码配置
CONFIG GET requirepass

# 在配置文件中设置密码（redis.conf）
# requirepass your_password
```

### 11.2 绑定地址

**实战案例**：

```bash
# 在配置文件中设置绑定地址（redis.conf）
# 仅本机访问
bind 127.0.0.1

# 允许指定IP访问
bind 192.168.1.100 127.0.0.1

# 允许所有地址访问（生产环境不推荐）
bind 0.0.0.0
```

### 11.3 保护模式

**实战案例**：

```bash
# 开启保护模式
CONFIG SET protected-mode yes

# 查看保护模式配置
CONFIG GET protected-mode
```

### 11.4 ACL访问控制（Redis 6.0+）

**功能说明**：细粒度的用户权限控制，可以为不同用户设置不同的命令和Key访问权限。

**实战案例**：

```bash
# 查看所有用户
ACL LIST

# 查看当前用户
ACL WHOAMI

# 创建用户（只允许读操作，只能访问cache:开头的key）
ACL SETUSER readonly_user on >password123 ~cache:* +GET +HGET +MGET +LRANGE +SCARD +SMEMBERS +ZSCORE +ZRANGE +EXISTS +TYPE +TTL

# 创建用户（只允许写操作）
ACL SETUSER write_user on >password456 ~cache:* +SET +HSET +MSET +LPUSH +SADD +ZADD +DEL +EXPIRE

# 使用指定用户连接
redis-cli --user readonly_user --pass password123

# 删除用户
ACL DELUSER readonly_user

# 查看用户详情
ACL GETUSER readonly_user

# 将ACL配置保存到文件
ACL SAVE
```

**ACL规则语法**：

| 规则 | 说明 | 示例 |
|------|------|------|
| `on/off` | 启用/禁用用户 | `on` |
| `>password` | 设置密码 | `>mypass123` |
| `~pattern` | Key模式匹配 | `~cache:*` |
| `+command` | 允许命令 | `+GET` |
| `-command` | 禁止命令 | `-DEL` |
| `+@category` | 允许命令类别 | `+@read` |
| `-@category` | 禁止命令类别 | `-@dangerous` |
| `allcommands` | 所有命令 | `+@all` |
| `allkeys` | 所有Key | `~*` |

---

## 模块十二：应用场景与实战

### 12.1 缓存

**场景描述**：将数据库查询结果缓存到Redis，减少数据库压力，提升响应速度。

**缓存模式**：

```
Cache-Aside（旁路缓存）模式：
┌────────┐    1.查询缓存    ┌────────┐
│  应用   │ ──────────────► │ Redis  │
│        │ ◄── 2.命中返回 ── │ 缓存   │
│        │                  └────────┘
│        │    3.未命中
│        │ ──────────────► ┌────────┐
│        │ ◄── 4.查询DB ── │  DB    │
│        │ ──────────────► │        │
│        │  5.写入缓存      └────────┘
└────────┘
```

**实战案例**：

```bash
# 商品信息缓存（带过期时间）
SET product:1:name "iPhone 13" EX 3600
SET product:1:price 5999 EX 3600

# 获取缓存
GET product:1:name

# 缓存更新策略：先更新数据库，再删除缓存
DEL product:1:name
DEL product:1:price
```

### 12.2 计数器与限流

**场景描述**：使用Redis的原子自增实现计数器和限流。

**实战案例**：

```bash
# 页面访问计数
INCR page:views:home

# 每日访问计数
INCR page:views:home:20230101
EXPIRE page:views:home:20230101 86400

# 固定窗口限流（每分钟最多10次请求）
SET rate_limit:user:1001 0 EX 60 NX
INCR rate_limit:user:1001
GET rate_limit:user:1001

# 滑动窗口限流（使用Sorted Set）
# 记录每次请求的时间戳
ZADD rate_limit:user:1001 <timestamp> <request_id>
# 清理窗口外的记录
ZREMRANGEBYSCORE rate_limit:user:1001 0 <timestamp - window>
# 统计窗口内请求数
ZCARD rate_limit:user:1001
```

### 12.3 分布式锁

**场景描述**：在分布式系统中，多个节点需要对共享资源进行互斥访问。

**基本实现**：

```bash
# 获取锁（设置过期时间，避免死锁）
SET lock:order:1 "uuid-12345" EX 10 NX

# 释放锁（Lua脚本保证原子性，防止误删别人的锁）
EVAL "
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
" 1 lock:order:1 "uuid-12345"
```

**Redisson分布式锁（推荐生产使用）**：

Redisson提供了更完善的分布式锁实现，内置看门狗（Watchdog）自动续期机制：

```
Redisson分布式锁特性：
┌──────────────────────────────────────────────┐
│ 1. 可重入锁：同一线程可多次获取同一把锁        │
│ 2. 看门狗续期：默认30秒，每10秒自动续期        │
│ 3. 公平锁：按请求顺序获取锁                    │
│ 4. 读写锁：支持读写分离锁                      │
│ 5. 联锁（MultiLock）：同时加多把锁             │
│ 6. 红锁（RedLock）：多节点分布式锁             │
└──────────────────────────────────────────────┘
```

**RedLock算法**：在多个独立的Redis实例上同时获取锁，超过半数成功才算获取锁，解决单点故障问题。

```
RedLock流程（5个Redis实例）：
1. 获取当前时间
2. 依次向5个实例请求加锁（设置超时时间）
3. 计算成功获取锁的实例数（需≥3，即N/2+1）
4. 计算获取锁的总耗时（需<锁过期时间）
5. 满足条件→加锁成功；否则→向所有实例释放锁
```

### 12.4 排行榜

**场景描述**：游戏积分排行、销售排行、热搜排行等需要实时排序的场景。

**实战案例**：

```bash
# 游戏积分排行榜
ZADD game:rank 9500 "玩家A"
ZADD game:rank 8800 "玩家B"
ZADD game:rank 9200 "玩家C"
ZADD game:rank 7600 "玩家D"

# 增加积分
ZINCRBY game:rank 500 "玩家B"

# 获取Top10（降序）
ZREVRANGE game:rank 0 9 WITHSCORES

# 获取玩家排名
ZREVRANK game:rank "玩家A"

# 获取玩家分数
ZSCORE game:rank "玩家A"

# 获取分数段内的玩家（9000分以上）
ZRANGEBYSCORE game:rank 9000 +inf WITHSCORES

# 每日排行榜（使用日期作为key）
ZADD game:rank:20230101 9500 "玩家A"
ZADD game:rank:20230102 9800 "玩家A"

# 合并多日排行（周榜）
ZUNIONSTORE game:rank:week 7 game:rank:20230101 game:rank:20230102 ... WEIGHTS 1 1 ...
```

**排行榜架构设计**：

```
┌─────────────────────────────────────────────┐
│              排行榜系统架构                    │
│                                              │
│  ┌──────────┐    写入     ┌──────────────┐  │
│  │ 游戏服务  │ ─────────► │ Redis        │  │
│  │          │             │ Sorted Set   │  │
│  │          │    读取     │ (实时排行)    │  │
│  │          │ ◄───────── │              │  │
│  └──────────┘             └──────┬───────┘  │
│                                  │           │
│                           定时落库 │           │
│                                  ▼           │
│                           ┌──────────────┐  │
│                           │ MySQL        │  │
│                           │ (历史排行)    │  │
│                           └──────────────┘  │
└─────────────────────────────────────────────┘
```

### 12.5 消息队列

**场景描述**：异步任务处理、系统解耦、流量削峰。

**三种实现方式对比**：

| 方式 | 适用场景 | 可靠性 | 复杂度 |
|------|----------|--------|--------|
| List + LPUSH/RPOP | 简单任务队列 | 低（无ACK） | 低 |
| List + BRPOP | 阻塞式任务队列 | 低（无ACK） | 低 |
| Stream | 可靠消息队列 | 高（ACK机制） | 中 |
| Pub/Sub | 实时广播通知 | 低（不持久化） | 低 |

**Stream消息队列实战**：

```bash
# 生产者：发送订单消息
XADD order:queue * orderId "ORD001" userId "U1001" amount 99.9
XADD order:queue * orderId "ORD002" userId "U1002" amount 199.9

# 创建消费组
XGROUP CREATE order:queue order-processors 0 MKSTREAM

# 消费者1：读取并处理消息
XREADGROUP GROUP order-processors consumer1 COUNT 1 BLOCK 5000 STREAMS order:queue >

# 处理完成后确认
XACK order:queue order-processors <message_id>

# 查看待处理消息（消费者崩溃后可重新分配）
XPENDING order:queue order-processors

# 超时消息重新分配（60秒未确认的消息）
XAUTOCLAIM order:queue order-processors consumer1 60000 0-0 COUNT 10

# 限制队列长度（防止内存溢出）
XADD order:queue MAXLEN 10000 * orderId "ORD003" userId "U1003" amount 299.9
XTRIM order:queue MAXLEN 10000
```

**延迟队列实现（Sorted Set）**：

```bash
# 添加延迟任务（score为执行时间戳）
ZADD delay:queue 1672531200 '{"task":"send_email","to":"user@example.com"}'
ZADD delay:queue 1672531260 '{"task":"cancel_order","orderId":"ORD001"}'

# 消费者轮询获取到期任务
ZRANGEBYSCORE delay:queue 0 <current_timestamp> LIMIT 0 10

# 处理完成后删除
ZREM delay:queue '{"task":"send_email","to":"user@example.com"}'
```

### 12.6 社交网络

**场景描述**：好友关系、共同好友、关注/粉丝、动态Feed流。

**实战案例**：

```bash
# 关注关系（使用Set存储）
SADD user:1001:following 1002 1003 1004
SADD user:1002:followers 1001
SADD user:1003:followers 1001

# 共同好友（交集运算）
SADD user:1001:friends 1002 1003 1005
SADD user:1002:friends 1003 1005 1006
SINTER user:1001:friends user:1002:friends
# 结果：1003 1005

# 可能认识的人（好友的好友 - 已有好友）
SDIFF user:1002:friends user:1001:friends
# 结果：1006

# Feed流（使用List或Sorted Set存储）
# 推模式：写扩散，主动推送给所有粉丝
LPUSH feed:user:1001 "post:20230101:001"
LPUSH feed:user:1002 "post:20230101:001"

# 拉模式：读扩散，用户主动拉取关注人的动态
ZADD feed:timeline:user:1001 <timestamp> "post:20230101:001"

# 获取Feed流（按时间排序）
ZREVRANGE feed:timeline:user:1001 0 19 WITHSCORES
```

**推拉结合模式**：

```
┌─────────────────────────────────────────────────┐
│              Feed流推拉结合模式                    │
│                                                  │
│  普通用户（粉丝少）：推模式（写扩散）              │
│  ┌──────────┐  发帖   ┌──────────────┐          │
│  │ 普通用户  │ ──────► │ 推送到粉丝收件箱│          │
│  └──────────┘         └──────────────┘          │
│                                                  │
│  大V用户（粉丝多）：拉模式（读扩散）              │
│  ┌──────────┐  发帖   ┌──────────────┐          │
│  │ 大V用户   │ ──────► │ 写入发帖列表   │          │
│  └──────────┘         └──────────────┘          │
│                              │                   │
│  粉丝读取时 ◄────────────────┘                   │
│  实时拉取大V的发帖列表合并                        │
└─────────────────────────────────────────────────┘
```

### 12.7 GEO位置服务

**场景描述**：附近的人、附近的商家、距离计算、地理围栏。

**实战案例**：

```bash
# 添加商家位置
GEOADD nearby:restaurants 116.4074 39.9042 "餐厅A"
GEOADD nearby:restaurants 116.4080 39.9050 "餐厅B"
GEOADD nearby:restaurants 116.4100 39.9020 "餐厅C"

# 查找附近3km内的餐厅
GEOSEARCH nearby:restaurants FROMLONLAT 116.4074 39.9042 BYRADIUS 3 km WITHDIST WITHCOORD ASC COUNT 10

# 查找矩形区域内的餐厅
GEOSEARCH nearby:restaurants FROMLONLAT 116.4074 39.9042 BYBOX 5 5 km WITHDIST

# 计算两个位置的距离
GEODIST nearby:restaurants "餐厅A" "餐厅B" km

# 获取商家坐标
GEOPOS nearby:restaurants "餐厅A"
```

**GEO系统架构**：

```
┌─────────────────────────────────────────────────┐
│              附近的人/商家架构                     │
│                                                  │
│  ┌──────────┐   写入    ┌──────────────┐        │
│  │ 用户APP  │ ────────► │ Redis GEO    │        │
│  │ 上报位置  │          │ (实时位置)    │        │
│  └──────────┘          └──────┬───────┘        │
│                               │                 │
│  ┌──────────┐   查询    ┌────┴────────┐        │
│  │ 用户APP  │ ◄──────── │ GEOSEARCH   │        │
│  │ 附近搜索  │          │ 范围查询     │        │
│  └──────────┘          └─────────────┘        │
│                                                  │
│  位置更新策略：                                   │
│  - 用户移动距离>500m时更新                        │
│  - 位置数据设置过期时间（如30分钟）                │
│  - 使用定时任务清理过期位置                        │
└─────────────────────────────────────────────────┘
```

### 12.8 应用场景速查表

| 场景 | 推荐数据类型 | 关键命令 | 典型业务 |
|------|------------|----------|----------|
| 缓存 | String/Hash | SET/GET/HSET/HGET | 商品信息、用户Session |
| 计数器 | String | INCR/INCRBY | 点赞数、浏览量 |
| 限流 | String/Sorted Set | INCR/ZADD | API限流、防刷 |
| 分布式锁 | String+Lua | SET NX/Lua | 订单防重复提交 |
| 排行榜 | Sorted Set | ZADD/ZREVRANGE | 积分榜、热搜 |
| 消息队列 | Stream/List | XADD/XREADGROUP | 异步任务、订单处理 |
| 社交关系 | Set | SADD/SINTER | 共同好友、关注 |
| 位置服务 | Geo | GEOADD/GEOSEARCH | 附近的人、外卖配送 |
| 签到 | Bitmap | SETBIT/BITCOUNT | 每日签到、在线状态 |
| UV统计 | HyperLogLog | PFADD/PFCOUNT | 页面UV、日活统计 |
| 延迟队列 | Sorted Set | ZADD/ZRANGEBYSCORE | 订单超时取消、延时通知 |

---

## 模块十三：高级特性

### 13.1 LRU与LFU缓存淘汰机制

**LRU（Least Recently Used）最近最少使用**：

Redis的LRU并非严格LRU，而是**近似LRU**——随机采样N个Key，淘汰其中最久未使用的。

```
LRU淘汰流程：
┌─────────────────────────────────────────────────┐
│ 1. 内存达到maxmemory阈值                          │
│ 2. 随机采样N个Key（由maxmemory-samples控制，默认5）│
│ 3. 淘汰其中最久未访问的Key                        │
│ 4. 重复直到内存低于阈值                           │
└─────────────────────────────────────────────────┘

采样数与精确度关系：
samples=5  → 接近理论LRU的80%精确度
samples=10 → 接近理论LRU的90%精确度
samples=20 → 接近理论LRU的95%精确度（CPU开销增大）
```

**LFU（Least Frequently Used）最不经常使用**（Redis 4.0+）：

LFU基于访问频率淘汰，比LRU更适合访问模式变化的场景。

```
LFU计数器机制：
┌─────────────────────────────────────────────────┐
│ 每个Key的RedisObject中有24bit用于LFU：            │
│ ┌──────────────┬───────────────────────┐         │
│ │ 高16bit：衰减时间 │ 低8bit：访问计数器  │         │
│ └──────────────┴───────────────────────┘         │
│                                                  │
│ 访问计数器增长：                                  │
│ 计数器最大255，使用对数增长策略                    │
│ 新值 = 当前值 + 1/概率                            │
│ 概率 = 1 / (1.0 ^ (当前值 * lfu-log-factor / 255))│
│ lfu-log-factor默认10                              │
│                                                  │
│ 计数器衰减：                                      │
│ 每lfu-decay-time分钟（默认1分钟）衰减1            │
│ 防止历史高频Key永远不被淘汰                        │
└─────────────────────────────────────────────────┘
```

```bash
# 查看LFU配置
CONFIG GET lfu-log-factor
CONFIG GET lfu-decay-time

# 调整LFU参数
CONFIG SET lfu-log-factor 10
CONFIG SET lfu-decay-time 1

# 查看Key的LFU信息（Redis 4.0+）
OBJECT FREQ key

# 设置LFU淘汰策略
CONFIG SET maxmemory-policy allkeys-lfu
```

**LRU vs LFU选型**：

| 场景 | 推荐策略 | 原因 |
|------|----------|------|
| 访问模式稳定，热点明显 | allkeys-lfu | 频率更能反映热点 |
| 访问模式变化快 | allkeys-lru | 最近访问更有参考价值 |
| 有冷热数据区分 | volatile-lfu | 只淘汰有过期时间的冷数据 |

### 13.2 Redis模块系统

Redis模块系统允许开发者用C语言编写扩展模块，添加新的数据类型和命令。

**常用模块**：

| 模块 | 功能 | 安装方式 |
|------|------|----------|
| RedisJSON | 原生JSON数据类型 | `loadmodule redisjson.so` |
| RedisBloom | 布隆过滤器、Cuckoo过滤器 | `loadmodule redisbloom.so` |
| RedisSearch | 全文搜索、二级索引 | `loadmodule redisearch.so` |
| RedisTimeSeries | 时间序列数据 | `loadmodule redistimeseries.so` |
| RedisCell | 基于漏桶算法的限流 | `loadmodule redis-cell.so` |

**RedisBloom实战（布隆过滤器）**：

```bash
# 创建布隆过滤器（容量100万，误判率0.1%）
BF.RESERVE user_filter 0.001 1000000

# 添加元素
BF.ADD user_filter "user1001"
BF.ADD user_filter "user1002"

# 批量添加
BF.MADD user_filter "user1003" "user1004" "user1005"

# 检查元素是否存在（可能误判，但不会漏判）
BF.EXISTS user_filter "user1001"
# 结果：1（存在）

BF.EXISTS user_filter "user9999"
# 结果：0（一定不存在）
```

**RedisSearch实战（全文搜索）**：

```bash
# 创建索引
FT.CREATE product_idx ON HASH PREFIX 1 product: SCHEMA name TEXT SORTABLE price NUMERIC SORTABLE category TAG

# 添加文档（通过Hash）
HSET product:1 name "iPhone 15 Pro" price 8999 category "手机"
HSET product:2 name "MacBook Pro" price 14999 category "电脑"

# 全文搜索
FT.SEARCH product_idx "iPhone"

# 组合搜索（全文 + 数值范围 + 标签过滤）
FT.SEARCH product_idx "@category:{手机} @price:[5000 10000]"

# 聚合查询
FT.AGGREGATE product_idx "*" GROUPBY 1 @category REDUCE COUNT 0 AS count
```

### 13.3 Redis 7.0新特性

| 特性 | 说明 |
|------|------|
| Redis Functions | 替代Lua脚本，支持持久化的函数库 |
| Multi-part AOF | AOF文件拆分为base+incr+manifest，提升AOF可靠性和性能 |
| ACL v2 | 支持Selector，更细粒度的权限控制 |
| Cluster Slots | 优化集群槽位管理命令 |
| Client-Side Cache增强 | 基于Redis 6.0引入的客户端缓存机制，7.0进行了优化增强 |

**Redis Functions实战**：

```bash
# 创建函数库
FUNCTION LOAD "#!lua name=mylib\nredis.register_function('my_func', function(keys, args) return redis.call('GET', keys[1]) end)"

# 调用函数
FCALL my_func 1 mykey

# 查看所有函数库
FUNCTION LIST

# 删除函数库
FUNCTION DELETE mylib
```

---

## 模块十四：运维监控与问题排查

### 14.1 监控指标

**核心监控指标**：

| 类别 | 指标 | 命令 | 告警阈值建议 |
|------|------|------|------------|
| 内存 | used_memory | INFO memory | >80% maxmemory |
| 内存 | mem_fragmentation_ratio | INFO memory | >1.5 |
| 连接 | connected_clients | INFO clients | >maxclients*80% |
| 命令 | instantaneous_ops_per_sec | INFO stats | 接近瓶颈 |
| 持久化 | rdb_last_bgsave_status | INFO persistence | failed |
| 持久化 | aof_last_bgrewrite_status | INFO persistence | failed |
| 复制 | replication_offset_diff | INFO replication | 延迟>10s |
| 键空间 | evicted_keys | INFO stats | >0需关注 |
| 键空间 | expired_keys | INFO stats | 突增需关注 |

```bash
# 一键获取关键指标
INFO memory | grep -E "used_memory_human|mem_fragmentation_ratio|maxmemory_human"
INFO clients | grep -E "connected_clients|maxclients"
INFO stats | grep -E "instantaneous_ops_per_sec|evicted_keys|expired_keys"
INFO replication | grep -E "role|connected_slaves|master_repl_offset"
```

### 14.2 慢查询排查

```bash
# 设置慢查询阈值（微秒，0记录所有）
CONFIG SET slowlog-log-slower-than 10000

# 查看慢查询日志
SLOWLOG GET 20

# 查看慢查询条数
SLOWLOG LEN

# 重置慢查询日志
SLOWLOG RESET
```

**慢查询分析流程**：

```
发现慢查询 → 分析命令类型 → 定位原因 → 优化方案

┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ SLOWLOG  │───►│ 命令类型判断  │───►│ 原因定位     │───►│ 优化方案     │
│ GET 20   │    │              │    │              │    │              │
│          │    │ KEYS *      │    │ O(N)全量扫描  │    │ 改用SCAN     │
│          │    │ HGETALL大Key│    │ 大Key阻塞    │    │ 拆分/UNLINK  │
│          │    │ SUNION等    │    │ 集合运算量大  │    │ 控制集合大小  │
│          │    │ DEL大Key    │    │ 同步删除阻塞  │    │ 改用UNLINK   │
└──────────┘    └──────────────┘    └──────────────┘    └──────────────┘
```

### 14.3 内存问题排查

```bash
# 查看内存使用详情
MEMORY DOCTOR

# 查看Key的内存占用
MEMORY USAGE key

# 扫描大Key
redis-cli --bigkeys -i 0.1

# 查看内存碎片率
INFO memory | grep mem_fragmentation_ratio

# 开启自动碎片整理
CONFIG SET activedefrag yes
CONFIG SET active-defrag-threshold-lower 10
CONFIG SET active-defrag-threshold-upper 100
```

### 14.4 生产环境配置清单

```conf
# redis.conf 生产环境推荐配置

# 网络
bind 0.0.0.0
protected-mode yes
port 6379
tcp-backlog 511
timeout 300
tcp-keepalive 60

# 通用
daemonize yes
pidfile /var/run/redis_6379.pid
loglevel notice
logfile /var/log/redis/redis.log
databases 16

# 内存
maxmemory 4gb
maxmemory-policy allkeys-lru
maxmemory-samples 10

# 持久化
save 900 1
save 300 10
save 60 10000
appendonly yes
appendfilename "appendonly.aof"
appendfsync everysec
aof-use-rdb-preamble yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# 安全
requirepass your_strong_password
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command DEBUG ""

# 慢查询
slowlog-log-slower-than 10000
slowlog-max-len 128

# 客户端
maxclients 10000

# 复制
repl-backlog-size 64mb
replica-read-only yes

# RDB压缩
rdbcompression yes
rdbchecksum yes
```

---

## 总结

本手册从Redis基础操作出发，系统性地覆盖了以下核心知识体系：

```
Redis知识体系总览：
┌─────────────────────────────────────────────────────────┐
│                                                          │
│  基础层：启动连接、数据类型、基本命令                      │
│     │                                                    │
│  原理层：单线程模型、I/O多路复用、SDS/跳表/渐进式rehash   │
│     │                                                    │
│  持久化：RDB快照、AOF日志、混合持久化                     │
│     │                                                    │
│  高可用：主从复制、哨兵监控、集群分片                      │
│     │                                                    │
│  高级层：事务与Lua、Stream、模块、ACL、Functions          │
│     │                                                    │
│  应用层：缓存、锁、排行榜、消息队列、社交、GEO             │
│     │                                                    │
│  运维层：监控指标、慢查询、内存排查、配置优化              │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## 附录A：Redis版本特性对照

| 版本 | 发布年份 | 核心特性 |
|------|----------|----------|
| 2.6 | 2012 | Lua脚本、有限期Key事件通知 |
| 2.8 | 2013 | PSYNC增量复制、Sentinel稳定版 |
| 3.0 | 2015 | Redis Cluster正式发布 |
| 3.2 | 2016 | GEO数据类型、SPOP支持count参数 |
| 4.0 | 2017 | 模块系统、LFU淘汰、PSYNC2、多线程异步删除 |
| 5.0 | 2018 | Stream数据类型、HAProxy支持 |
| 6.0 | 2020 | ACL、多线程I/O、Client-Side Cache |
| 6.2 | 2021 | 新增命令（ZMSCORE、XAUTOCLAIM等）、GEOSEARCH |
| 7.0 | 2022 | Redis Functions、Multi-part AOF、ACL v2 |

## 附录B：Redis vs Memcached对比

| 对比项 | Redis | Memcached |
|--------|-------|-----------|
| 数据结构 | 丰富（9种类型） | 仅String |
| 持久化 | RDB+AOF | 无 |
| 集群 | 原生Cluster | 客户端分片 |
| 内存管理 | 多种淘汰策略 | LRU |
| 线程模型 | 单线程（6.0+I/O多线程） | 多线程 |
| 最大Key | 512MB | 1MB |
| 事务 | MULTI+Lua | 无 |
| 发布订阅 | 支持 | 不支持 |
| 适用场景 | 缓存+数据存储+消息队列 | 纯缓存 |

## 附录C：面试高频问题

**Q1：Redis为什么用单线程还这么快？**
纯内存操作 + I/O多路复用 + 高效数据结构 + 无锁无切换开销。

**Q2：Redis和MySQL如何保证数据一致性？**
常用方案：先更新数据库，再删除缓存（Cache-Aside）。延迟双删可进一步降低不一致窗口。极端场景可用Canal监听binlog。

**Q3：Redis集群方案怎么选？**
主从复制→读写分离；Sentinel→自动故障转移；Cluster→数据分片+高可用。小规模用Sentinel，大规模用Cluster。

**Q4：如何解决缓存穿透？**
缓存空值（设短TTL）+ 布隆过滤器（前置拦截不存在Key）。

**Q5：RDB和AOF怎么选？**
生产推荐混合持久化：AOF保证数据安全（everysec），RDB加速恢复。两者同时开启。

**Q6：Redis的跳表为什么比红黑树更适合Sorted Set？**
实现简单、范围查询天然高效、内存可控、并发友好。Sorted Set的核心操作是范围查询，跳表更适合。

**Q7：Redis如何实现分布式锁？**
基础：SET NX EX + Lua释放。生产推荐Redisson（看门狗续期、可重入）。强一致性要求用RedLock。

---

## 附录D：Redis Stream深度解析

> Redis 5.0引入的Stream数据类型，是Redis对消息队列场景的原生支持，功能上对标Kafka的消费者组模型。

### D.1 Stream核心概念

```
Stream结构：
┌─────────────────────────────────────────────────────────┐
│  Stream (有序消息队列)                                   │
│                                                          │
│  Entry1          Entry2          Entry3                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐           │
│  │ID: 1630-0│   │ID: 1630-1│   │ID: 1631-0│           │
│  │field:val │   │field:val │   │field:val │           │
│  └──────────┘   └──────────┘   └──────────┘           │
│       ▲                                                │
│       │  Consumer Group A                              │
│       │  ┌──────────────────────────────────────┐     │
│       │  │ Consumer1: last_id=1630-1             │     │
│       │  │ Consumer2: last_id=1630-0             │     │
│       │  │ Pending: [未ACK的消息列表]             │     │
│       │  └──────────────────────────────────────┘     │
│       │                                                  │
│       │  Consumer Group B                              │
│       │  ┌──────────────────────────────────────┐     │
│       │  │ Consumer3: last_id=1631-0             │     │
│       │  └──────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────┘

消息ID格式：<毫秒时间戳>-<序列号>
  例：1630000000000-0、1630000000000-1
  自动生成，全局递增，保证有序
```

### D.2 Stream vs 其他消息队列

| 维度 | Redis Stream | Kafka | RabbitMQ | Redis List+Pub/Sub |
|------|-------------|-------|----------|-------------------|
| 消息持久化 | AOF/RDB | 磁盘日志 | 可选持久化 | AOF/RDB |
| 消费者组 | ✅ 原生支持 | ✅ 原生支持 | ❌ 需插件 | ❌ 不支持 |
| 消息确认 | ✅ ACK | ✅ Offset | ✅ ACK | ❌ 无 |
| 消息回溯 | ✅ 按ID范围 | ✅ 按Offset | ❌ | ❌ |
| 消息堆积 | 受内存限制 | 磁盘无限 | 队列限制 | 受内存限制 |
| 吞吐量 | 10万+/秒 | 百万/秒 | 万/秒 | 10万+/秒 |
| 适用场景 | 轻量消息队列 | 大数据流 | 复杂路由 | 简单通知 |

### D.3 Stream实战

```bash
# 添加消息
XADD orders * user_id 1001 product_id 5001 amount 99.9
XADD orders * user_id 1002 product_id 5002 amount 199.9

# 查看Stream信息
XINFO STREAM orders

# 读取消息（范围查询）
XRANGE orders - +
XRANGE orders 1630000000000-0 1630000000000-5
XREVRANGE orders + - COUNT 5

# 创建消费者组
XGROUP CREATE orders order_group $ MKSTREAM
XGROUP CREATE orders notify_group 0

# 消费者组读取（阻塞等待新消息）
XREADGROUP GROUP order_group consumer1 COUNT 10 BLOCK 5000 STREAMS orders >

# 确认消息
XACK orders order_group 1630000000000-0

# 查看待处理消息
XPENDING orders order_group

# 认领超时消息（转移给其他消费者）
XCLAIM orders order_group consumer2 3600 1630000000000-0

# 删除消息
XDEL orders 1630000000000-0

# 修剪Stream长度
XTRIM orders MAXLEN 1000
XTRIM orders MINID 1630000000000-0
```

### D.4 Django中集成Redis Stream

```python
import redis
import json
import uuid

r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

class StreamProducer:
    def __init__(self, stream_name: str):
        self.stream_name = stream_name

    def send(self, data: dict, maxlen: int = 10000) -> str:
        msg_id = r.xadd(self.stream_name, data, maxlen=maxlen, approximate=True)
        return msg_id

    def send_order(self, order_id: str, user_id: int, amount: float) -> str:
        return self.send({
            "order_id": order_id,
            "user_id": str(user_id),
            "amount": str(amount),
            "event": "order_created",
        })


class StreamConsumer:
    def __init__(self, stream_name: str, group_name: str, consumer_name: str):
        self.stream_name = stream_name
        self.group_name = group_name
        self.consumer_name = consumer_name
        try:
            r.xgroup_create(stream_name, group_name, id="0", mkstream=True)
        except redis.ResponseError:
            pass

    def consume(self, count: int = 10, block: int = 5000) -> list[dict]:
        messages = r.xreadgroup(
            self.group_name,
            self.consumer_name,
            {self.stream_name: ">"},
            count=count,
            block=block,
        )
        results = []
        if messages:
            for stream, msgs in messages:
                for msg_id, data in msgs:
                    results.append({"id": msg_id, "data": data})
        return results

    def ack(self, msg_id: str):
        r.xack(self.stream_name, self.group_name, msg_id)

    def claim_pending(self, min_idle_time: int = 60000) -> list[dict]:
        pending = r.xpending_range(
            self.stream_name, self.group_name, min=min_idle_time, count=10
        )
        results = []
        for p in pending:
            claimed = r.xclaim(
                self.stream_name,
                self.group_name,
                self.consumer_name,
                min_idle_time,
                [p["message_id"]],
            )
            for msg_id, data in claimed:
                results.append({"id": msg_id, "data": data})
        return results
```

---

## 附录E：Redis与Django集成实战

### E.1 Django缓存配置

```python
# settings/base.py
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://localhost:6379/0",
        "OPTIONS": {
            "CLIENT_CLASS": "django.core.cache.backends.redis.RedisCacheClient",
        },
        "KEY_PREFIX": "myapp",
        "TIMEOUT": 300,
    },
    "session": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://localhost:6379/1",
        "KEY_PREFIX": "session",
    },
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "session"
```

### E.2 缓存工具类

```python
import json
import hashlib
from django.core.cache import cache
from functools import wraps

def cache_with_key(key_prefix: str, timeout: int = 300):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f"{key_prefix}:{hashlib.md5(str((args, kwargs)).encode()).hexdigest()}"
            result = cache.get(cache_key)
            if result is not None:
                return result
            result = func(*args, **kwargs)
            cache.set(cache_key, result, timeout)
            return result
        return wrapper
    return decorator


def invalidate_cache(key_prefix: str, *args):
    pattern = f"{key_prefix}:*"
    keys = cache.keys(pattern)
    if keys:
        cache.delete_many(keys)


class DistributedLock:
    def __init__(self, lock_key: str, timeout: int = 10, retry_times: int = 3, retry_delay: float = 0.1):
        self.lock_key = f"lock:{lock_key}"
        self.timeout = timeout
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self.identifier = str(uuid.uuid4())

    def __enter__(self):
        import time
        for _ in range(self.retry_times):
            acquired = cache.set(self.lock_key, self.identifier, self.timeout, nx=True)
            if acquired:
                return self
            time.sleep(self.retry_delay)
        raise TimeoutError(f"获取锁 {self.lock_key} 超时")

    def __exit__(self, exc_type, exc_val, exc_tb):
        lua_script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        else
            return 0
        end
        """
        cache.client.get_client().eval(lua_script, 1, self.lock_key, self.identifier)
        return False
```

### E.3 Django视图缓存实战

```python
from django.views.decorators.cache import cache_page
from django.core.cache import cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(["GET"])
@cache_page(60 * 5, key_prefix="product_list")
def product_list(request):
    products = Product.objects.all()[:50]
    serializer = ProductSerializer(products, many=True)
    return Response(serializer.data)


@api_view(["GET"])
def product_detail(request, pk):
    cache_key = f"product:{pk}"
    data = cache.get(cache_key)
    if data is None:
        product = Product.objects.get(pk=pk)
        serializer = ProductSerializer(product)
        data = serializer.data
        cache.set(cache_key, data, 300)
    return Response(data)


@api_view(["POST"])
def product_update(request, pk):
    product = Product.objects.get(pk=pk)
    serializer = ProductSerializer(product, data=request.data)
    serializer.save()
    cache.delete(f"product:{pk}")
    cache.delete_many(cache.keys("product_list:*"))
    return Response(serializer.data)
```

---

## 附录F：Redis缓存一致性方案对比

### F.1 四种缓存一致性策略

```
策略1：先更新数据库，再删除缓存（Cache-Aside，推荐）
┌────────┐                    ┌────────┐
│  应用   │ ──1.更新DB──────▶ │  DB    │
│        │ ◀─2.返回成功────── │        │
│        │ ──3.删除缓存────▶ │ Redis  │
└────────┘                    └────────┘
优点：实现简单，一致性较好
缺点：删除缓存失败会导致不一致
适用：大多数场景

策略2：先删除缓存，再更新数据库
┌────────┐                    ┌────────┐
│  应用   │ ──1.删除缓存────▶ │ Redis  │
│        │ ──2.更新DB──────▶ │  DB    │
└────────┘                    └────────┘
优点：实现简单
缺点：并发时可能读到旧数据写入缓存
适用：一致性要求不高的场景

策略3：延迟双删
┌────────┐                    ┌────────┐
│  应用   │ ──1.删除缓存────▶ │ Redis  │
│        │ ──2.更新DB──────▶ │  DB    │
│        │ ──3.延迟N毫秒───▶ │        │
│        │ ──4.再次删除缓存─▶ │ Redis  │
└────────┘                    └────────┘
优点：解决策略2的并发问题
缺点：延迟时间难以确定
适用：中等一致性要求

策略4：监听Binlog异步更新（Canal）
┌────────┐  Binlog  ┌───────┐  消息  ┌────────┐
│  DB    │ ────────▶│ Canal │ ─────▶ │  应用   │ ──删除缓存──▶ Redis
└────────┘          └───────┘        └────────┘
优点：最终一致性保证强
缺点：架构复杂，引入Canal
适用：强一致性要求
```

| 策略 | 一致性 | 复杂度 | 性能影响 | 推荐场景 |
|------|--------|--------|---------|---------|
| Cache-Aside | 较好 | 低 | 低 | 大多数业务 |
| 先删后更新 | 一般 | 低 | 低 | 低一致性要求 |
| 延迟双删 | 好 | 中 | 中 | 中等一致性 |
| Canal监听 | 最好 | 高 | 低 | 强一致性 |

---

## 附录G：Redis高可用架构选型决策树

```
Redis高可用方案选择：

                    需要Redis高可用？
                    ┌─── 是 ───┐
                    │          │
              数据量<10GB？   数据量>10GB？
              ┌─── 是 ───┐   ┌─── 是 ───┐
              │          │   │          │
         需要自动故障转移？  需要自动故障转移？
         ┌── 是 ──┐  │   ┌── 是 ──┐  │
         │       │  │   │       │  │
      Sentinel  主从  │  Cluster  主从+分片
      (3+节点)  复制  │  (6+节点) (客户端路由)
```

| 方案 | 最少节点 | 数据分片 | 自动故障转移 | 运维复杂度 | 适用规模 |
|------|---------|---------|------------|-----------|---------|
| 主从复制 | 2 | ❌ | ❌ | 低 | 读写分离 |
| Sentinel | 3+ | ❌ | ✅ | 中 | 中小规模 |
| Cluster | 6+ | ✅ | ✅ | 高 | 大规模 |