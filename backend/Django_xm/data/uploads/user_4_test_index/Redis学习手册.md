# 零基础到进阶的Redis系统学习手册

## 前言

本手册旨在帮助零基础用户系统学习Redis数据库，从基础操作到高级特性，循序渐进地掌握Redis的核心知识点。手册中的所有实战案例均基于Redis 7.0+版本，可直接复制运行。

## 准备工作

在开始学习前，请确保你已经安装了Redis 7.0+。可以通过以下命令检查Redis版本：

```bash
redis-server --version
```

## 模块一：基础操作

### 1.1 启动Redis

**功能说明**：启动Redis服务器。

**实战案例**：

```bash
# 启动Redis服务器
redis-server

# 后台启动Redis服务器
redis-server --daemonize yes
```

### 1.2 连接Redis

**功能说明**：连接到Redis服务器。

**实战案例**：

```bash
# 命令行连接
redis-cli

# 连接指定主机和端口
redis-cli -h 127.0.0.1 -p 6379

# 使用密码连接
redis-cli -a your_password
```

### 1.3 测试连接

**功能说明**：测试Redis连接是否正常。

**实战案例**：

```bash
# 测试连接
redis-cli ping

# 预期输出：PONG
```

### 1.4 关闭Redis

**功能说明**：关闭Redis服务器。

**实战案例**：

```bash
# 关闭Redis服务器
redis-cli shutdown

# 或者在Redis客户端中执行
SHUTDOWN
```

## 模块二：数据类型

### 2.1 字符串（String）

**功能说明**：存储字符串、数字等基本类型数据。

**实战案例**：

```bash
# 设置字符串
SET name "张三"

# 获取字符串
GET name

# 自增
SET counter 0
INCR counter

# 自减
DECR counter

# 增加指定值
INCRBY counter 10

# 减少指定值
DECRBY counter 5

# 设置过期时间（10秒）
SETEX key 10 "value"

# 检查键是否存在
EXISTS name

# 删除键
DEL name
```

### 2.2 列表（List）

**功能说明**：存储有序的字符串列表。

**实战案例**：

```bash
# 从左侧添加元素
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

# 获取列表长度
LLEN fruits

# 根据索引获取元素
LINDEX fruits 0

# 根据索引设置元素
LSET fruits 0 "pear"
```

### 2.3 哈希（Hash）

**功能说明**：存储键值对的集合。

**实战案例**：

```bash
# 设置哈希字段
HSET user:1 name "张三"
HSET user:1 age 25
HSET user:1 email "zhangsan@example.com"

# 获取单个哈希字段
HGET user:1 name

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
```

### 2.4 集合（Set）

**功能说明**：存储无序的唯一元素集合。

**实战案例**：

```bash
# 添加元素
SADD tags "java"
SADD tags "python"
SADD tags "javascript"

# 获取所有元素
SMEMBERS tags

# 检查元素是否存在
SISMEMBER tags "java"

# 删除元素
SREM tags "javascript"

# 获取集合大小
SCARD tags

# 交集
SADD set1 "a"
SADD set1 "b"
SADD set2 "b"
SADD set2 "c"
SINTER set1 set2

# 并集
SUNION set1 set2

# 差集
SDIFF set1 set2
```

### 2.5 有序集合（Sorted Set）

**功能说明**：存储有序的唯一元素集合，每个元素关联一个分数。

**实战案例**：

```bash
# 添加元素（分数，值）
ZADD scores 90 "张三"
ZADD scores 85 "李四"
ZADD scores 95 "王五"

# 按分数升序获取元素
ZRANGE scores 0 -1

# 按分数降序获取元素
ZREVRANGE scores 0 -1

# 按分数范围获取元素
ZRANGEBYSCORE scores 80 90

# 获取元素分数
ZSCORE scores "张三"

# 增加元素分数
ZINCRBY scores 5 "张三"

# 获取元素排名（升序）
ZRANK scores "张三"

# 获取元素排名（降序）
ZREVRANK scores "张三"

# 删除元素
ZREM scores "李四"
```

### 2.6 位图（Bitmap）

**功能说明**：存储位级别的数据。

**实战案例**：

```bash
# 设置位
SETBIT user:login:20230101 0 1
SETBIT user:login:20230101 1 0
SETBIT user:login:20230101 2 1

# 获取位
GETBIT user:login:20230101 0

# 统计置位的位数
BITCOUNT user:login:20230101

# 位操作（AND）
SETBIT bitmap1 0 1
SETBIT bitmap1 1 1
SETBIT bitmap2 1 1
SETBIT bitmap2 2 1
BITOP AND result bitmap1 bitmap2
GETBIT result 1
```

### 2.7  HyperLogLog

**功能说明**：用于统计基数（去重计数）。

**实战案例**：

```bash
# 添加元素
PFADD unique_visitors "user1"
PFADD unique_visitors "user2"
PFADD unique_visitors "user3"
PFADD unique_visitors "user1"  # 重复元素

# 统计基数
PFCOUNT unique_visitors

# 合并多个HyperLogLog
PFADD unique_visitors2 "user4"
PFADD unique_visitors2 "user5"
PFMERGE merged_visitors unique_visitors unique_visitors2
PFCOUNT merged_visitors
```

### 2.8 地理位置（Geo）

**功能说明**：存储地理位置信息。

**实战案例**：

```bash
# 添加地理位置
GEOADD cities 116.4074 39.9042 "北京"
GEOADD cities 121.4737 31.2304 "上海"
GEOADD cities 113.2644 23.1291 "广州"

# 计算两个位置之间的距离
GEODIST cities "北京" "上海" km

# 根据坐标范围获取位置
GEORADIUS cities 116.4074 39.9042 1000 km

# 根据成员获取坐标
GEOPOS cities "北京"

# 获取成员的哈希值
GEOHASH cities "北京"
```

## 模块三：持久化

### 3.1 RDB持久化

**功能说明**：将Redis数据以快照形式保存到磁盘。

**实战案例**：

```bash
# 手动触发RDB持久化
SAVE

# 后台触发RDB持久化
BGSAVE

# 查看RDB配置
CONFIG GET dir
CONFIG GET dbfilename

# 修改RDB配置
CONFIG SET save "900 1 300 10 60 10000"
```

### 3.2 AOF持久化

**功能说明**：将Redis命令以日志形式追加到文件。

**实战案例**：

```bash
# 开启AOF持久化
CONFIG SET appendonly yes

# 设置AOF同步策略（always、everysec、no）
CONFIG SET appendfsync everysec

# 手动触发AOF重写
BGREWRITEAOF

# 查看AOF配置
CONFIG GET appendonly
CONFIG GET appendfsync
```

### 3.3 混合持久化

**功能说明**：结合RDB和AOF的优点。

**实战案例**：

```bash
# 开启混合持久化
CONFIG SET aof-use-rdb-preamble yes

# 查看混合持久化配置
CONFIG GET aof-use-rdb-preamble
```

## 模块四：复制

### 4.1 主从复制

**功能说明**：将主服务器的数据复制到从服务器。

**实战案例**：

```bash
# 在从服务器上执行
SLAVEOF 127.0.0.1 6379

# 查看复制状态
INFO replication

# 取消复制
SLAVEOF NO ONE
```

### 4.2 复制配置

**功能说明**：配置主从复制参数。

**实战案例**：

```bash
# 修改主服务器配置（redis.conf）
# bind 0.0.0.0
# protected-mode no

# 修改从服务器配置（redis.conf）
# replicaof 127.0.0.1 6379
# replica-read-only yes
```

## 模块五：哨兵（Sentinel）

### 5.1 配置哨兵

**功能说明**：监控Redis主从集群，自动进行故障转移。

**实战案例**：

```conf
# sentinel.conf配置文件
sentinel monitor mymaster 127.0.0.1 6379 2
sentinel down-after-milliseconds mymaster 30000
sentinel failover-timeout mymaster 180000
sentinel parallel-syncs mymaster 1
```

### 5.2 启动哨兵

**功能说明**：启动Redis哨兵。

**实战案例**：

```bash
# 启动哨兵
redis-sentinel sentinel.conf

# 后台启动哨兵
redis-sentinel sentinel.conf --daemonize yes
```

### 5.3 查看哨兵状态

**功能说明**：查看哨兵监控状态。

**实战案例**：

```bash
# 连接哨兵
redis-cli -p 26379

# 查看哨兵状态
INFO sentinel

# 查看主服务器信息
SENTINEL get-master-addr-by-name mymaster
```

## 模块六：集群（Cluster）

### 6.1 创建集群

**功能说明**：创建Redis集群。

**实战案例**：

```bash
# 启动多个Redis实例
redis-server redis-7000.conf
redis-server redis-7001.conf
redis-server redis-7002.conf
redis-server redis-7003.conf
redis-server redis-7004.conf
redis-server redis-7005.conf

# 创建集群
redis-cli --cluster create 127.0.0.1:7000 127.0.0.1:7001 127.0.0.1:7002 127.0.0.1:7003 127.0.0.1:7004 127.0.0.1:7005 --cluster-replicas 1
```

### 6.2 连接集群

**功能说明**：连接到Redis集群。

**实战案例**：

```bash
# 连接集群
redis-cli -c -p 7000

# 查看集群信息
CLUSTER INFO

# 查看集群节点
CLUSTER NODES
```

### 6.3 集群操作

**功能说明**：在集群中执行操作。

**实战案例**：

```bash
# 设置键值（集群会自动路由）
SET key value

# 获取键值
GET key

# 查看键所在的槽
CLUSTER KEYSLOT key

# 添加新节点
redis-cli --cluster add-node 127.0.0.1:7006 127.0.0.1:7000

# 重新分片
redis-cli --cluster reshard 127.0.0.1:7000
```

## 模块七：事务

### 7.1 基本事务

**功能说明**：执行一组命令，要么全部成功，要么全部失败。

**实战案例**：

```bash
# 开始事务
MULTI

# 执行命令
SET key1 "value1"
SET key2 "value2"
GET key1

# 提交事务
EXEC

# 或者放弃事务
# DISCARD
```

### 7.2 事务冲突

**功能说明**：处理事务执行过程中的冲突。

**实战案例**：

```bash
# 监视键
WATCH key

# 开始事务
MULTI

# 执行命令
SET key "new value"

# 提交事务（如果key被修改，事务会失败）
EXEC
```

## 模块八：Lua脚本

### 8.1 执行Lua脚本

**功能说明**：在Redis中执行Lua脚本。

**实战案例**：

```bash
# 执行简单Lua脚本
EVAL "return redis.call('SET', KEYS[1], ARGV[1])" 1 mykey myvalue

# 执行复杂Lua脚本
EVAL "local sum = 0; for i=1,10 do sum = sum + i end; return sum" 0

# 缓存Lua脚本
SCRIPT LOAD "return redis.call('GET', KEYS[1])"

# 执行缓存的脚本
EVALSHA <script_sha> 1 mykey

# 查看脚本是否存在
SCRIPT EXISTS <script_sha>

# 清除所有缓存的脚本
SCRIPT FLUSH
```

## 模块九：性能优化

### 9.1 内存管理

**功能说明**：优化Redis内存使用。

**实战案例**：

```bash
# 查看内存使用情况
INFO memory

# 设置最大内存
CONFIG SET maxmemory 2gb

# 设置内存淘汰策略
CONFIG SET maxmemory-policy allkeys-lru

# 查看内存淘汰策略
CONFIG GET maxmemory-policy
```

### 9.2 命令优化

**功能说明**：优化Redis命令执行。

**实战案例**：

```bash
# 使用批量命令减少网络开销
MSET key1 value1 key2 value2 key3 value3
MGET key1 key2 key3

# 使用管道（pipeline）执行多个命令
redis-cli << EOF
SET key1 value1
SET key2 value2
GET key1
EOF

# 避免使用KEYS命令（会阻塞Redis）
# 推荐使用SCAN命令
SCAN 0 MATCH key*
```

### 9.3 连接管理

**功能说明**：优化Redis连接。

**实战案例**：

```bash
# 查看当前连接数
INFO clients

# 设置最大连接数
CONFIG SET maxclients 10000

# 使用连接池管理连接（在应用程序中）
# 例如，使用Jedis连接池（Java）
# JedisPool pool = new JedisPool(new JedisPoolConfig(), "localhost", 6379);
```

## 模块十：安全

### 10.1 设置密码

**功能说明**：为Redis设置访问密码。

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

### 10.2 绑定地址

**功能说明**：限制Redis只监听指定地址。

**实战案例**：

```bash
# 在配置文件中设置绑定地址（redis.conf）
bind 127.0.0.1

# 或者允许所有地址访问（生产环境不推荐）
bind 0.0.0.0
```

### 10.3 保护模式

**功能说明**：启用Redis保护模式。

**实战案例**：

```bash
# 开启保护模式
CONFIG SET protected-mode yes

# 查看保护模式配置
CONFIG GET protected-mode

# 在配置文件中设置保护模式（redis.conf）
# protected-mode yes
```

## 模块十一：应用场景

### 11.1 缓存

**功能说明**：使用Redis作为缓存。

**实战案例**：

```bash
# 设置缓存（带过期时间）
SET product:1:name "iPhone 13" EX 3600
SET product:1:price 5999 EX 3600

# 获取缓存
GET product:1:name

# 缓存雪崩处理（设置随机过期时间）
SET product:1:name "iPhone 13" EX 3600
SET product:2:name "iPhone 14" EX 3660
SET product:3:name "iPhone 15" EX 3720
```

### 11.2 计数器

**功能说明**：使用Redis实现计数器。

**实战案例**：

```bash
# 页面访问计数
INCR page:views:home

# 获取访问数
GET page:views:home

# 每日访问计数
INCR page:views:home:20230101
```

### 11.3 分布式锁

**功能说明**：使用Redis实现分布式锁。

**实战案例**：

```bash
# 获取锁（设置过期时间，避免死锁）
SET lock:order:1 "1" EX 10 NX

# 释放锁（使用Lua脚本确保原子性）
EVAL "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end" 1 lock:order:1 "1"
```

### 11.4 消息队列

**功能说明**：使用Redis实现简单的消息队列。

**实战案例**：

```bash
# 生产者：向队列添加消息
LPUSH queue:tasks "task1"
LPUSH queue:tasks "task2"

# 消费者：从队列获取消息
RPOP queue:tasks

# 阻塞式获取消息
BRPOP queue:tasks 0
```

### 11.5 会话管理

**功能说明**：使用Redis存储用户会话。

**实战案例**：

```bash
# 存储用户会话
SET session:user:123 "{\"user_id\": 123, \"username\": \"张三\"}" EX 1800

# 获取用户会话
GET session:user:123

# 刷新会话过期时间
EXPIRE session:user:123 1800
```

## 总结

本手册涵盖了Redis从基础到进阶的所有核心知识点，每个知识点都提供了可直接运行的实战案例。通过系统学习本手册，你将能够掌握Redis的基本操作、数据类型、持久化、复制、哨兵、集群、事务、Lua脚本、性能优化、安全和应用场景等知识点，为你的Redis学习和应用打下坚实的基础。

## 附录：常用命令

### 服务器管理

```bash
# 查看服务器信息
INFO

# 查看命令统计
INFO commandstats

# 查看客户端连接
CLIENT LIST

# 关闭客户端连接
CLIENT KILL <ip:port>

# 查看配置
CONFIG GET *

# 修改配置
CONFIG SET <key> <value>

# 保存配置
CONFIG REWRITE
```

### 键管理

```bash
# 查看所有键
SCAN 0

# 查看匹配的键
SCAN 0 MATCH pattern*

# 查看键的类型
TYPE key

# 查看键的过期时间
TTL key

# 设置键的过期时间
EXPIRE key seconds

# 移除键的过期时间
PERSIST key

# 重命名键
RENAME key newkey

# 随机获取一个键
RANDOMKEY
```

### 发布订阅

```bash
# 订阅频道
SUBSCRIBE channel1

# 发布消息
PUBLISH channel1 "Hello Redis"

# 取消订阅
UNSUBSCRIBE channel1

# 订阅模式
PSUBSCRIBE channel*

# 取消订阅模式
PUNSUBSCRIBE channel*
```