# 零基础到进阶的Redis系统学习手册（增强版）

## 前言

本手册在原版基础上，为每个操作步骤增加了**详细执行说明**、**底层原理深度解析**、**代码示例**和**背景知识与扩展信息**，形成一份既能指导实际操作，又能帮助深入理解技术原理的增强版学习资料。

**增强版特色**：
- **原理驱动**：每个知识点先讲底层原理，再给实战命令，知其然更知其所以然
- **场景导向**：结合真实业务场景，说明何时用、怎么用、为什么用
- **深度解析**：对关键技术点补充源码级原理、核心逻辑和应用场景
- **代码示例**：提供Python/Django集成代码，帮助理解在实际项目中的使用方式
- **扩展信息**：补充背景知识，帮助理解技术在整体体系中的作用和价值

**环境要求**：Redis 7.0+，Python 3.10+，Django 5.2+

---

## 模块一：基础操作

### 1.1 启动Redis

**功能说明**：启动Redis服务器，使其开始监听客户端连接请求。

**执行说明与预期效果**：

| 命令 | 执行说明 | 预期效果 | 适用场景 |
|------|----------|----------|----------|
| `redis-server` | 前台启动，占用当前终端 | 终端显示Redis启动日志，包括端口、PID等信息 | 开发调试，可直观查看日志输出 |
| `redis-server --daemonize yes` | 后台启动，释放终端 | 返回提示，Redis在后台运行 | 生产环境、日常开发 |
| `redis-server /etc/redis/redis.conf` | 读取配置文件启动 | 按配置文件参数运行 | 生产环境标准方式 |
| `redis-server --port 6380 --daemonize yes` | 指定端口后台启动 | 在6380端口监听 | 多实例运行 |

**底层原理**：

Redis启动时经历以下核心阶段：

```
1. 初始化服务器状态结构（redisServer）
   ├── 设置默认配置值
   ├── 初始化数据库数组（默认16个DB）
   └── 初始化LRU时钟、命令表等

2. 加载配置（命令行参数覆盖配置文件）
   ├── 解析命令行参数
   └── 合并配置文件设置

3. 初始化数据结构
   ├── 创建共享对象（小整数、常用字符串缓存）
   ├── 初始化事件循环（aeEventLoop）
   ├── 创建I/O多路复用句柄（epoll/kqueue）
   └── 注册时间事件（serverCron，每100ms执行）

4. 监听网络端口
   ├── 创建TCP套接字
   ├── 绑定地址和端口
   └── 注册文件事件（监听新连接）

5. 加载持久化数据（如果存在）
   ├── 优先加载AOF文件（数据更完整）
   └── 其次加载RDB文件

6. 进入事件循环（aeMain）
   └── 持续监听并处理事件
```

**扩展知识**：

- **daemonize原理**：Redis通过`fork()`创建子进程，父进程退出，子进程脱离终端成为守护进程。子进程调用`setsid()`创建新会话，关闭标准输入输出，将日志重定向到文件。
- **多实例运行**：同一台机器可运行多个Redis实例，需使用不同端口和配置文件。生产中常用于隔离不同业务的数据。
- **共享对象**：Redis启动时预创建0-9999的整数对象和常用响应字符串（OK、ERR等），避免重复分配，提升性能。

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

**Python启动检查示例**：

```python
import subprocess
import redis

def check_redis_running(host="localhost", port=6379):
    try:
        r = redis.Redis(host=host, port=port)
        return r.ping()
    except redis.ConnectionError:
        return False

if not check_redis_running():
    subprocess.Popen(["redis-server", "--daemonize", "yes"])
    print("Redis已启动")
else:
    print("Redis已在运行")
```

### 1.2 连接Redis

**功能说明**：使用redis-cli客户端连接到Redis服务器，建立通信通道。

**执行说明与预期效果**：

| 命令 | 执行说明 | 预期效果 |
|------|----------|----------|
| `redis-cli` | 使用默认配置连接 | 显示`127.0.0.1:6379>`提示符 |
| `redis-cli -h 127.0.0.1 -p 6379` | 指定主机和端口连接 | 显示指定地址的提示符 |
| `redis-cli -a your_password` | 使用密码认证连接 | 直接认证成功，显示提示符 |
| `redis-cli -n 1` | 连接并切换到1号数据库 | 提示符变为`127.0.0.1:6379[1]>` |

**底层原理**：

客户端连接Redis的完整过程：

```
1. TCP三次握手
   客户端 ──SYN──► Redis
   Redis ──SYN+ACK──► 客户端
   客户端 ──ACK──► Redis

2. Redis接受连接
   ├── 创建client结构体（fd、查询缓冲区、输出缓冲区等）
   ├── 注册文件事件（监听该客户端的可读事件）
   └── 更新连接数统计

3. 认证协商（如果设置了requirepass）
   ├── 客户端发送AUTH命令
   ├── Redis验证密码
   └── 返回OK或错误

4. 进入命令交互模式
   └── 等待用户输入命令
```

**扩展知识**：

- **16个数据库**：Redis默认配置16个数据库（DB0-DB15），通过`SELECT n`切换。不同数据库之间数据隔离，但共享内存配额。**注意**：Redis Cluster模式下只能使用DB0。
- **连接数限制**：通过`maxclients`配置（默认10000），超出后新连接被拒绝。每个连接约占用几KB内存。
- **`-a`参数安全提示**：使用`-a`参数会在进程列表中暴露密码，生产环境推荐使用`REDISCLI_AUTH`环境变量：

```bash
# 安全方式：通过环境变量传递密码
export REDISCLI_AUTH=your_password
redis-cli
```

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

**Python连接示例**：

```python
import redis

r = redis.Redis(
    host="localhost",
    port=6379,
    password="your_password",
    db=0,
    decode_responses=True,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True,
)

if r.ping():
    print("连接成功")
```

### 1.3 测试连接

**功能说明**：通过PING命令测试客户端与Redis服务器之间的连接是否正常。

**执行说明与预期效果**：

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

**底层原理**：

PING命令的执行路径：

```
1. 客户端发送PING命令（RESP协议编码）
   *1\r\n$4\r\nPING\r\n

2. Redis接收并解析命令
   ├── I/O多路复用检测到可读事件
   ├── 读取数据到查询缓冲区
   ├── 解析RESP协议，提取命令和参数
   └── 查找命令表，定位PING命令的处理函数

3. 执行PING命令
   └── 直接返回PONG（或回显消息），无任何副作用

4. 返回结果（RESP协议编码）
   +PONG\r\n
```

**扩展知识**：

- **PING的用途**：①连接健康检查 ②测量网络延迟 ③客户端保活（防止连接超时被断开）
- **PING是轻量命令**：不涉及数据操作，执行时间在微秒级，适合高频调用
- **连接池保活**：在连接池实现中，通常用PING检测空闲连接是否有效

```python
# 连接池中PING保活示例
import redis

pool = redis.ConnectionPool(
    host="localhost",
    port=6379,
    max_connections=50,
    socket_timeout=5,
    health_check_interval=30,
)
r = redis.Redis(connection_pool=pool)

# health_check_interval=30 表示每30秒对空闲连接执行PING检查
```

### 1.4 关闭Redis

**功能说明**：安全关闭Redis服务器，确保数据持久化后再停止进程。

**执行说明与预期效果**：

| 命令 | 执行说明 | 预期效果 |
|------|----------|----------|
| `redis-cli shutdown` | 发送SHUTDOWN命令 | 触发RDB持久化后安全退出 |
| `redis-cli -a pwd shutdown` | 带密码关闭 | 认证后执行关闭 |
| `SHUTDOWN` | 在客户端内执行 | 同上 |

**底层原理**：

SHUTDOWN命令的完整执行流程：

```
1. 接收SHUTDOWN命令

2. 执行持久化（确保数据安全）
   ├── 如果开启AOF：执行AOF重写，刷盘
   ├── 如果开启RDB：执行BGSAVE，等待完成
   └── 如果两者都开启：先RDB后AOF

3. 清理资源
   ├── 关闭所有客户端连接
   ├── 删除PID文件
   ├── 释放监听套接字
   └── 释放内存

4. 退出进程（exit(0)）
```

**扩展知识**：

- **为什么不能用`kill -9`**：`kill -9`（SIGKILL）无法被捕获，Redis来不及执行持久化就直接终止，可能导致数据丢失。应使用`kill -15`（SIGTERM）或`redis-cli shutdown`。
- **SHUTDOWN NOSAVE**：`SHUTDOWN NOSAVE`不执行持久化直接关闭，用于测试环境或确认无需保存的场景。
- **优雅关闭脚本**：

```bash
#!/bin/bash
# 优雅关闭Redis的脚本
REDIS_CLI="redis-cli"
PASSWORD="your_password"

echo "正在关闭Redis..."
$REDIS_CLI -a $PASSWORD SHUTDOWN

sleep 2

if pgrep redis-server > /dev/null; then
    echo "Redis仍在运行，等待..."
    sleep 5
fi

if pgrep redis-server > /dev/null; then
    echo "强制关闭Redis..."
    kill -15 $(pgrep redis-server)
else
    echo "Redis已安全关闭"
fi
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

**深度解析：命令执行的完整生命周期**

```
一次SET命令的完整执行路径：

1. 客户端发送命令
   redis-cli → TCP连接 → Redis服务器

2. I/O多路复用检测到可读事件
   epoll_wait()返回，告知某个fd有数据可读

3. 文件事件处理器回调
   ├── readQueryFromClient()：从fd读取数据
   ├── 数据写入客户端的查询缓冲区（querybuf）
   └── 调用协议解析器

4. 协议解析
   ├── processInputBuffer()：解析RESP协议
   ├── 提取命令名和参数
   └── 查找命令表（redisCommandTable），定位SET处理函数

5. 命令执行前检查
   ├── 检查命令参数个数
   ├── 检查客户端认证状态
   ├── 检查内存限制（maxmemory）
   ├── 检查集群状态
   └── 检查ACL权限

6. 调用SET命令处理函数
   ├── setCommand() → genericSet()
   ├── 查找或创建key对应的RedisObject
   ├── 设置值（SDS编码）
   ├── 设置过期时间（如果有EX/PX参数）
   └── 更新LRU/LFU时钟

7. 命令执行后处理
   ├── 写入AOF缓冲区（如果开启AOF）
   ├── 传播到从服务器（如果开启复制）
   ├── 通知键空间事件（如果开启通知）
   └── 更新统计信息

8. 返回结果
   ├── 构造RESP响应（+OK\r\n）
   ├── 写入客户端输出缓冲区
   └── 注册可写事件，等待发送
```

**扩展知识**：

- **aeEventLoop**：Redis自实现的事件循环库，封装了epoll/kqueue/select，提供统一的事件驱动接口。核心数据结构是事件循环（aeEventLoop），包含文件事件表和时间事件链表。
- **命令表**：Redis启动时构建一个命令字典（`redisCommandTable`），每个命令注册了处理函数、参数个数、标志位（是否写操作、是否阻塞等）。通过命令名O(1)查找处理函数。
- **为什么是16个数据库**：`databases 16`是默认配置，实际可修改。每个数据库是一个独立的键空间字典（`dict *dict`），共享同一内存配额。

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

**深度解析：I/O多路复用机制**

I/O多路复用是Redis高性能的核心，它让单线程能够同时处理数千个并发连接：

```
传统阻塞I/O（每个连接一个线程）：
Thread1: read(fd1) → 阻塞等待 → 处理 → write(fd1)
Thread2: read(fd2) → 阻塞等待 → 处理 → write(fd2)
...
问题：线程开销大，上下文切换频繁

I/O多路复用（单线程管理所有连接）：
epoll_create() → 创建epoll实例
epoll_ctl(ADD, fd1) → 注册fd1
epoll_ctl(ADD, fd2) → 注册fd2
...
epoll_wait() → 返回就绪的fd列表 → 依次处理
优势：无需多线程，无需锁，O(1)检测就绪事件
```

**epoll vs select vs poll**：

| 特性 | select | poll | epoll |
|------|--------|------|-------|
| 最大连接数 | 1024（FD_SETSIZE） | 无限制 | 无限制 |
| 就绪检测 | O(N)遍历所有fd | O(N)遍历所有fd | O(1)返回就绪fd |
| 内存拷贝 | 每次调用需要拷贝fd集合 | 每次调用需要拷贝fd集合 | mmap共享内存 |
| 触发方式 | 水平触发 | 水平触发 | 水平/边缘触发 |
| 适用场景 | 少量连接 | 中等连接 | 大量连接（Redis默认） |

**注意**：Redis并非完全单线程，以下操作使用多线程：
- Redis 4.0+：后台删除大Key（`UNLINK`）、AOF fsync（后台线程）
- Redis 6.0+：网络I/O多线程（`io-threads`），命令执行仍为单线程

```bash
# 查看Redis是否开启I/O多线程（Redis 6.0+）
CONFIG GET io-threads
CONFIG GET io-threads-do-reads
```

**深度解析：Redis 6.0 I/O多线程**

```
Redis 6.0 I/O多线程架构：
┌──────────────────────────────────────────────────┐
│                   主线程                          │
│  ┌────────────────────────────────────────────┐  │
│  │  接收连接 → 读取请求 → 执行命令 → 写入响应  │  │
│  │         ↕              ↕                    │  │
│  │    I/O线程池        I/O线程池               │  │
│  │  (读取请求)       (写入响应)                │  │
│  └────────────────────────────────────────────┘  │
│                                                   │
│  关键：命令执行仍然是单线程，只有I/O是多线程      │
└──────────────────────────────────────────────────┘

配置建议：
- io-threads：建议设为CPU核心数的一半（最少4，最多8）
- io-threads-do-reads yes：开启读操作多线程
- 4核CPU建议：io-threads 4
- 8核CPU建议：io-threads 6
```

**扩展知识**：

- **Redis的性能瓶颈不在CPU**：在大多数场景下，Redis的瓶颈是内存大小和网络I/O。单线程足以将CPU利用率提升到网络带宽的极限。
- **为什么Redis 6.0才引入I/O多线程**：随着网络硬件升级（10Gbps+网卡），网络I/O成为瓶颈，单线程读写网络数据跟不上网络速度，因此将I/O操作交给多线程处理。
- **线程安全保证**：I/O线程只负责读写网络数据，命令执行仍在主线程，因此不需要对数据结构加锁。

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

**深度解析：RedisObject结构**

Redis中每个键值对都通过RedisObject封装，理解它对理解内存使用至关重要：

```
RedisObject结构（共16字节）：
┌─────────────────────────────────────────────────────────┐
│ type(4bit) │ encoding(4bit) │ lru(24bit) │ refcount(4B) │ ptr(8B) │
└─────────────────────────────────────────────────────────┘

- type：数据类型（String/List/Hash/Set/ZSet/Stream等）
- encoding：编码方式（同一类型可有多种编码，如String的int/embstr/raw）
- lru：LRU时钟或LFU计数器（用于内存淘汰）
- refcount：引用计数（共享对象机制，如小整数缓存）
- ptr：指向实际数据的指针

内存开销示例：
SET name "张三"
实际内存 = RedisObject(16B) + SDS头部(3B) + "张三"(6B UTF-8) = 25B
加上键的RedisObject + SDS = 约50B+
```

**深度解析：内存碎片产生原因与处理**

```
内存碎片产生原因：
1. 操作系统分配策略
   ├── jemalloc按大小分级分配（8B/16B/32B/.../4KB/...）
   └── 请求10B实际分配16B，产生6B碎片

2. 键值对频繁修改和删除
   ├── 删除key后释放的空间可能不连续
   └── 新分配的key大小与释放的空间不匹配

3. 不同编码转换
   ├── Hash从listpack转为hashtable
   └── 旧内存释放，新内存分配，产生间隙

碎片率判断：
< 1.0  ：Redis使用了超过操作系统分配的内存（SWAP，严重问题）
1.0-1.5：正常范围
1.5-2.0：碎片较多，建议开启自动整理
> 2.0  ：碎片严重，需要立即处理
```

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

**扩展知识**：

- **jemalloc的分配策略**：jemalloc将内存按大小分为Small（<8KB）、Large（8KB-4MB）、Huge（>4MB）三类，每类再细分大小等级。这种策略减少了碎片但无法完全消除。
- **共享对象**：Redis预缓存0-9999的整数对象，当值是这个范围内的整数时，RedisObject的ptr直接指向共享对象，不需要额外分配SDS，节省内存。
- **内存对齐**：RedisObject占16字节，jemalloc分配时按16字节对齐，确保CPU缓存行友好。

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

**深度解析：RESP协议设计哲学**

```
RESP的设计目标：
1. 简单易实现：5种数据类型覆盖所有通信需求
2. 人类可读：方便调试和排查问题
3. 解析高效：前缀字符即可判断类型，无需复杂解析

RESP2的5种数据类型：
┌──────────┬──────┬──────────────────────────────┐
│ 类型     │ 前缀 │ 说明                         │
├──────────┼──────┼──────────────────────────────┤
│ 简单字符串│ +    │ 状态响应（OK、PONG等）        │
│ 错误     │ -    │ 错误信息                      │
│ 整数     │ :    │ 整数结果                      │
│ 批量字符串│ $    │ 二进制安全字符串（可含\r\n）   │
│ 数组     │ *    │ 命令参数列表/多值返回          │
└──────────┴──────┴──────────────────────────────┘

为什么批量字符串是二进制安全的？
$6\r\nfoo\r\nbar\r\n  ← 长度前缀，按长度读取，不依赖分隔符
而简单字符串 +OK\r\n  ← 依赖\r\n分隔，不能包含\r\n
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

**扩展知识**：

- **RESP vs 其他协议**：相比Memcached的二进制协议，RESP更易调试；相比HTTP/2的二进制帧，RESP更轻量。RESP的缺点是文本协议占用带宽略大，但Redis通常在内网使用，带宽不是瓶颈。
- **Pipeline与RESP**：Pipeline本质是将多个RESP命令一次性发送，减少TCP往返次数。服务端按顺序处理并返回结果。
- **客户端库实现**：Python的`redis-py`等客户端库负责将Python数据类型编码为RESP协议，并将RESP响应解码为Python对象。

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

SDS（Redis 5.0+，sdshdr8）：
┌──────────┬───────────┬────────┬─────┬─────┬─────┬─────┬─────┐
│ len = 4  │ alloc = 4 │flags=1 │ 'R' │ 'e' │ 'd' │ 'i' │ '\0'│
└──────────┴───────────┴────────┴─────┴─────┴─────┴─────┴─────┘
```

**SDS vs C字符串**：

| 特性 | C字符串 | SDS |
|------|---------|-----|
| 获取长度 | O(N)遍历 | O(1)直接读取len |
| 缓冲区溢出 | 可能溢出 | 空间预分配，自动扩容 |
| 修改内存重分配 | 每次N次 | 最多N次（预分配+惰性释放） |
| 二进制安全 | 靠'\0'判断结束 | 靠len判断结束 |
| 兼容C字符串 | - | 末尾保留'\0' |

**深度解析：SDS的空间预分配与惰性释放**

```
SDS多类型头部（Redis 5.0+）：
├── sdshdr5：未使用（flags仅3bit，不够存len和alloc）
├── sdshdr8：len/alloc各1字节，最大255字节字符串
├── sdshdr16：len/alloc各2字节，最大65KB字符串
├── sdshdr32：len/alloc各4字节，最大4GB字符串
└── sdshdr64：len/alloc各8字节，最大2^64字符串

空间预分配策略（减少内存重分配次数）：
当SDS修改后长度 < 1MB：
  预分配空间 = 修改后长度（即len和alloc各占一半）
  例：修改后len=20，则分配40字节（len=20, alloc=40）

当SDS修改后长度 ≥ 1MB：
  预分配空间 = 1MB
  例：修改后len=5MB，则分配6MB（len=5MB, alloc=6MB）

惰性释放策略（减少内存重分配次数）：
当SDS缩短时，不立即释放多余内存，而是记录在alloc中
例：SDS有30字节，截断到20字节 → len=20, alloc=30
后续追加时可直接使用alloc-len的空间，无需重新分配
```

**三种编码方式**：

| 编码 | 条件 | 说明 |
|------|------|------|
| int | 值为整数且≤long范围 | 直接存储在ptr指针位置 |
| embstr | 字符串长度≤44字节 | SDS和RedisObject一次分配，紧凑存储 |
| raw | 字符串长度>44字节 | SDS和RedisObject两次分配 |

**深度解析：为什么embstr的阈值是44字节？**

```
RedisObject = 16字节
SDS头部 = 3字节（sdshdr8：len 1B + alloc 1B + flags 1B）
字符串结尾'\0' = 1字节
剩余可用空间 = 64 - 16 - 3 - 1 = 44字节

embstr优势：
1. 一次内存分配（RedisObject + SDS连续存储），减少malloc调用
2. 缓存行友好（64字节刚好一个缓存行），一次加载到CPU缓存
3. 释放时一次free即可

embstr限制：
1. 只读，修改时转为raw编码
2. 适合短字符串（如配置值、状态标记）
```

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

**深度解析：INCR的原子性保证**

```
INCR命令执行流程：
1. 读取key的值（O(1)）
2. 检查是否为整数（如果不是，返回错误）
3. 加1（O(1)）
4. 写回新值（O(1)）

由于Redis单线程执行命令，INCR天然原子：
- 不会有并发问题（两个客户端同时INCR，结果一定正确）
- 不需要加锁
- 这使得Redis成为理想的分布式计数器

INCR的典型应用：
1. 文章阅读数：INCR article:1001:views
2. API限流：INCR rate_limit:api:1001（配合EXPIRE）
3. 分布式ID生成：INCR global:id（每秒可生成10万+ID）
4. 库存扣减：DECR stock:product:1001
```

**Python代码示例**：

```python
import redis
import uuid
import time

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

# 分布式计数器
article_id = "1001"
views = r.incr(f"article:{article_id}:views")
print(f"文章阅读数：{views}")

# API限流器（固定窗口）
def is_rate_limited(user_id: str, limit: int = 10, window: int = 60) -> bool:
    key = f"rate_limit:{user_id}"
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, window)
    results = pipe.execute()
    current = results[0]
    if current == 1:
        r.expire(key, window)
    return current > limit

# 分布式锁
def acquire_lock(lock_name: str, timeout: int = 10) -> str:
    identifier = str(uuid.uuid4())
    end_time = time.time() + timeout
    while time.time() < end_time:
        if r.set(f"lock:{lock_name}", identifier, nx=True, ex=timeout):
            return identifier
        time.sleep(0.001)
    return ""

def release_lock(lock_name: str, identifier: str) -> bool:
    lua_script = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    else
        return 0
    end
    """
    result = r.eval(lua_script, 1, f"lock:{lock_name}", identifier)
    return result == 1
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
| quicklist（listpack节点不压缩） | 节点内元素数量≤128且每个元素≤64字节 | listpack节点紧凑存储，不启用LZF压缩 |
| quicklist（listpack节点压缩） | 不满足上述条件 | listpack节点启用LZF压缩，兼顾内存和性能 |

> **注意**：Redis 7.0中List统一使用quicklist编码（由listpack节点组成的双向链表），不再有单独的listpack编码。quicklist根据`list-compress-depth`和`list-max-listpack-size`配置自动决定节点是否压缩。

**深度解析：从ziplist到listpack的演进**

```
ziplist的问题（Redis 7.0之前使用）：
1. 连锁更新：ziplist的prevlen字段用1字节或5字节表示
   当中间节点从<254字节扩展到≥254字节时
   → prevlen从1字节变为5字节 → 当前节点变大
   → 下一个节点的prevlen也需要扩展 → 连锁反应
   → 最坏O(N²)时间复杂度

2. listpack的改进：
   - 不再存储前一节点的长度
   - 每个节点存储自身长度
   - 反向遍历时通过自身长度回退
   - 彻底解决连锁更新问题

listpack节点结构：
┌──────────┬──────────┬──────────┐
│ element  │ element  │ backlen  │
│ data     │ encoding │ (自身长度)│
└──────────┴──────────┴──────────┘
```

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

**深度解析：List的典型应用场景与实现原理**

```
1. 消息队列（简单版）
   LPUSH queue:task "task_data"    # 生产者：左推入
   BRPOP queue:task 30             # 消费者：右弹出（阻塞等待）
   优势：简单可靠，支持阻塞等待
   劣势：无消费组、无ACK、消息不可回溯

2. 最新列表（如最新文章、最新动态）
   LPUSH latest:articles "article:1001"
   LTRIM latest:articles 0 99      # 只保留最新100条
   LRANGE latest:articles 0 9      # 获取最新10条

3. 栈结构（后进先出）
   LPUSH stack "item1"
   LPOP stack

4. 队列结构（先进先出）
   LPUSH queue "item1"
   RPOP queue

BLPOP的阻塞原理：
1. 客户端发送BLPOP命令
2. 如果列表为空，将客户端加入阻塞等待列表
3. 其他客户端LPUSH新元素时，唤醒阻塞客户端
4. 被唤醒的客户端执行LPOP并返回结果
5. 超时后自动返回nil
```

**Python代码示例**：

```python
import redis
import json

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class SimpleMessageQueue:
    def __init__(self, queue_name: str):
        self.queue_name = queue_name

    def produce(self, data: dict):
        r.lpush(self.queue_name, json.dumps(data))

    def consume(self, timeout: int = 30) -> dict | None:
        result = r.brpop(self.queue_name, timeout=timeout)
        if result:
            return json.loads(result[1])
        return None

class LatestList:
    def __init__(self, name: str, max_size: int = 100):
        self.name = name
        self.max_size = max_size

    def add(self, item: str):
        pipe = r.pipeline()
        pipe.lpush(self.name, item)
        pipe.ltrim(self.name, 0, self.max_size - 1)
        pipe.execute()

    def get_latest(self, count: int = 10) -> list[str]:
        return r.lrange(self.name, 0, count - 1)
```

### 3.3 哈希（Hash）

**功能说明**：存储字段-值对的集合，适合存储对象。

**底层实现：listpack + hashtable**

```
编码选择逻辑：
元素数量 ≤ 128 且每个字段值 ≤ 64字节？
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

**深度解析：渐进式rehash的完整机制**

```
rehash触发条件：
1. 扩容：
   - 服务器没有执行BGSAVE/BGREWRITEAOF时，负载因子≥1
   - 服务器正在执行BGSAVE/BGREWRITEAOF时，负载因子≥5
   （BGSAVE期间使用COW，尽量避免页面分裂，提高扩容阈值）

2. 缩容：
   - 负载因子<0.1时触发

渐进式rehash步骤：
1. 为ht[1]分配空间（扩容：2倍；缩容：最小2的幂次方）
2. 设置rehashidx=0，表示rehash开始
3. 每次CRUD操作时，顺带迁移ht[0]中rehashidx位置的桶
   - 查找：先查ht[0]，再查ht[1]
   - 新增：只写入ht[1]
   - 修改/删除：在ht[0]或ht[1]中操作
4. serverCron每100ms也会迁移一部分桶（每次100个）
5. 全部迁移完成后，ht[0]←ht[1]，释放旧表，rehashidx=-1

为什么需要渐进式rehash？
- Redis单个哈希表可能有数百万个键
- 一次性迁移会导致长时间阻塞（秒级）
- 渐进式将阻塞分摊到毫秒级操作中
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

**深度解析：Hash存储对象 vs String存储对象**

```
方案1：String + JSON
SET user:1 '{"name":"张三","age":25,"email":"zhangsan@example.com"}'
优势：简单，一次获取全部字段
劣势：修改单个字段需要读取→修改→写回，非原子，内存开销大

方案2：Hash
HSET user:1 name "张三" age 25 email "zhangsan@example.com"
优势：单独读写字段，HINCRBY原子自增，内存效率高（listpack编码时）
劣势：不能嵌套（字段值只能是字符串），获取全部字段用HGETALL

内存对比（存储10000个用户，每个3个字段）：
String+JSON：约 10000 × (16+3+50) = 690KB
Hash(listpack)：约 10000 × (16+3+3×30) = 109KB
Hash节省约84%内存！

选型建议：
- 对象字段少（<1000）、不需要嵌套 → Hash
- 对象结构复杂、需要嵌套 → String + JSON
- 需要单独操作字段 → Hash
```

**Python代码示例**：

```python
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class UserCache:
    def __init__(self):
        self.prefix = "user"

    def set_user(self, user_id: int, data: dict):
        key = f"{self.prefix}:{user_id}"
        r.hset(key, mapping=data)

    def get_user(self, user_id: int) -> dict | None:
        key = f"{self.prefix}:{user_id}"
        data = r.hgetall(key)
        return data if data else None

    def update_field(self, user_id: int, field: str, value):
        key = f"{self.prefix}:{user_id}"
        r.hset(key, field, value)

    def incr_age(self, user_id: int):
        key = f"{self.prefix}:{user_id}"
        r.hincrby(key, "age", 1)

    def delete_field(self, user_id: int, *fields: str):
        key = f"{self.prefix}:{user_id}"
        r.hdel(key, *fields)

cache = UserCache()
cache.set_user(1, {"name": "张三", "age": "25", "email": "zhangsan@example.com"})
print(cache.get_user(1))
cache.incr_age(1)
```

### 3.4 集合（Set）

**功能说明**：存储无序的唯一元素集合，支持集合运算。

**底层实现：intset + hashtable**

| 编码 | 条件 | 说明 |
|------|------|------|
| intset | 元素都是整数且数量≤512 | 有序整数数组，内存紧凑 |
| hashtable | 不满足intset条件 | 哈希表，O(1)查找 |

```
intset结构（有序整数数组）：
┌───────┬───────┬───────┬───────┐
│  1    │  3    │  5    │  7    │
└───────┴───────┴───────┴───────┘
（二分查找，O(logN)）
```

**深度解析：intset的升级机制**

```
intset有三种编码：int16、int32、int64
当插入的整数超出当前编码范围时，自动升级：

初始：int16编码 [1, 3, 5]
插入32768（超出int16范围-32768~32767）→ 升级为int32
插入2147483648（超出int32范围）→ 升级为int64

升级过程：
1. 根据新元素类型，扩展底层数组大小
2. 将现有元素转换为新类型，从后往前放置
3. 插入新元素
4. 修改encoding属性

注意：intset只升级不降级
即使删除了大整数，编码也不会回退
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

**深度解析：集合运算的应用场景**

```
1. 共同好友（交集）
   SADD friends:1001 "u1" "u2" "u3"
   SADD friends:1002 "u2" "u3" "u4"
   SINTER friends:1001 friends:1002
   → 共同好友：u2, u3

2. 可能认识的人（差集）
   SDIFF friends:1002 friends:1001
   → 1002有但1001没有的好友：u4（推荐给1001）

3. 标签系统
   SADD article:1001:tags "java" "redis" "backend"
   SADD tag:java:articles "1001" "1002" "1003"
   SINTER tag:java:articles tag:redis:articles
   → 同时有java和redis标签的文章

4. 唯一性保证
   SADD online:users "user1" "user2"
   SISMEMBER online:users "user1" → 1（在线）
```

**Python代码示例**：

```python
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class FriendService:
    def add_friend(self, user_id: int, friend_id: int):
        r.sadd(f"friends:{user_id}", str(friend_id))

    def get_common_friends(self, user_id1: int, user_id2: int) -> list[str]:
        return list(r.sinter(f"friends:{user_id1}", f"friends:{user_id2}"))

    def get_maybe_friends(self, user_id: int, target_id: int) -> list[str]:
        return list(r.sdiff(f"friends:{target_id}", f"friends:{user_id}"))

    def is_friend(self, user_id: int, friend_id: int) -> bool:
        return bool(r.sismember(f"friends:{user_id}", str(friend_id)))

    def remove_friend(self, user_id: int, friend_id: int):
        r.srem(f"friends:{user_id}", str(friend_id))
```

### 3.5 有序集合（Sorted Set）

**功能说明**：存储有序的唯一元素集合，每个元素关联一个分数（score），按分数排序。

**底层实现：listpack + skiplist + hashtable**

```
编码选择逻辑：
元素数量 ≤ 128 且每个元素值 ≤ 64字节？
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

**深度解析：跳表（Skiplist）的完整实现原理**

```
跳表核心思想：通过多层索引加速查找

查找过程（查找score=88的元素）：
1. 从最高层开始，向右查找
2. 如果右节点score ≤ 目标score，继续右移
3. 如果右节点score > 目标score，向下层移动
4. 重复直到第1层找到目标元素

插入过程：
1. 查找插入位置（同查找过程）
2. 在第1层插入新节点
3. 随机决定是否提升到更高层
   - 提升概率：1/4（Redis使用1/4而非1/2，减少内存）
   - 最高层：32层（Redis限制）
4. 更新各层的前后指针

为什么用跳表而不是红黑树？

| 对比项 | 跳表 | 红黑树 |
|--------|------|--------|
| 实现复杂度 | 简单，易于理解和调试 | 复杂，旋转操作多 |
| 范围查询 | 天然支持（链表遍历） | 需要中序遍历 |
| 内存占用 | 额外指针（每层2个） | 额外指针（3个+颜色位） |
| 并发友好 | 更容易实现无锁并发 | 需要复杂的锁策略 |
| 插入/删除 | O(logN)，概率平衡 | O(logN)，旋转平衡 |

Redis选择跳表的核心原因：
1. ZRANGE/ZRANGEBYSCORE等范围查询是Sorted Set最常用的操作
2. 跳表的范围查询只需找到起点后沿链表遍历，极其高效
3. 红黑树的范围查询需要中序遍历，实现复杂且效率低
```

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

# 按分数范围获取元素（Redis 6.2+推荐使用 ZRANGE scores 80 90 BYSCORE）
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

**Python代码示例**：

```python
import redis
import time

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class Leaderboard:
    def __init__(self, name: str):
        self.name = name

    def add_score(self, player: str, score: float):
        r.zadd(self.name, {player: score})

    def incr_score(self, player: str, increment: float):
        return r.zincrby(self.name, increment, player)

    def get_top_n(self, n: int = 10) -> list[tuple[str, float]]:
        return r.zrevrange(self.name, 0, n - 1, withscores=True)

    def get_rank(self, player: str) -> int | None:
        rank = r.zrevrank(self.name, player)
        return rank + 1 if rank is not None else None

    def get_score(self, player: str) -> float | None:
        return r.zscore(self.name, player)

class DelayQueue:
    def __init__(self, name: str):
        self.name = name

    def add_task(self, task_id: str, execute_at: float):
        r.zadd(self.name, {task_id: execute_at})

    def get_ready_tasks(self, now: float = None) -> list[str]:
        now = now or time.time()
        return r.zrangebyscore(self.name, 0, now)

    def remove_task(self, task_id: str):
        r.zrem(self.name, task_id)
```

### 3.6 位图（Bitmap）

**功能说明**：基于String类型实现的位级别操作，适合布尔型数据统计。

**底层实现**：直接使用String类型，将字符串视为位数组。

**深度解析：Bitmap的存储与计算原理**

```
Bitmap本质是String的位视图：
SETBIT user:login:20230101 0 1
实际操作：将String的第0位设置为1

String "A" = 0x41 = 01000001（二进制）
第0位=0, 第1位=1, 第2位=0, ..., 第7位=1

BITCOUNT原理（统计1的个数）：
使用查表法，每次处理8位
预计算256种字节值的1的个数（如0xFF=8, 0x01=1）
遍历字符串的每个字节，查表累加
时间复杂度：O(N)，N为字节数

BITOP原理（位运算）：
对两个String的每个字节执行AND/OR/XOR/NOT
结果写入新的String
```

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

**Python代码示例**：

```python
import redis
from datetime import date, timedelta

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class SignInService:
    def sign(self, user_id: int, day: date = None):
        day = day or date.today()
        key = f"sign:{user_id}:{day.strftime('%Y%m')}"
        r.setbit(key, day.day, 1)

    def is_signed(self, user_id: int, day: date = None) -> bool:
        day = day or date.today()
        key = f"sign:{user_id}:{day.strftime('%Y%m')}"
        return bool(r.getbit(key, day.day))

    def get_month_sign_count(self, user_id: int, year_month: str) -> int:
        key = f"sign:{user_id}:{year_month}"
        return r.bitcount(key)

    def get_continuous_sign_days(self, user_id: int) -> int:
        today = date.today()
        count = 0
        for i in range(365):
            day = today - timedelta(days=i)
            if self.is_signed(user_id, day):
                count += 1
            else:
                break
        return count
```

### 3.7 HyperLogLog

**功能说明**：用于基数统计（去重计数），标准误差0.81%，固定消耗12KB内存。

**深度解析：HyperLogLog算法原理**

```
HyperLogLog核心思想：
1. 对每个元素计算哈希值（64位）
2. 观察哈希值二进制表示中前导零的个数
3. 前导零越多，说明不同的值越多（概率统计原理）

具体实现：
1. 将64位哈希值分为两部分：
   - 前14位：确定桶编号（2^14 = 16384个桶）
   - 后50位：计算前导零个数

2. 每个桶记录观察到的最大前导零个数

3. 基数估算公式（调和平均数）：
   E = α_m × m² × (∑2^(-M[j]))^(-1)
   其中 m=16384，α_m为修正系数

4. 修正：
   - 小基数修正：估算值<2.5m时，使用线性计数
   - 大基数修正：估算值>2^32时，使用修正公式

内存结构：
- 稀疏编码：元素少时，只存储非零桶（节省内存）
- 密集编码：元素多时，每个桶6bit，共16384×6/8=12288B=12KB
```

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

**扩展知识**：

- **0.81%误差的含义**：如果实际UV为100万，HyperLogLog的估算值在99.19万~100.81万之间。对于业务统计来说，这个精度完全够用。
- **合并操作**：PFMERGE将多个HLL的桶取最大值合并，合并后的HLL仍然是12KB，这是HLL的独特优势。

### 3.8 地理位置（Geo）

**功能说明**：存储地理位置信息，支持距离计算和范围查询。

**底层实现**：基于Sorted Set，使用GeoHash编码将经纬度转换为分数。

**深度解析：GeoHash编码原理**

```
GeoHash将二维经纬度编码为一维值：

1. 分别对经度和纬度进行二分编码：
   纬度范围[-90, 90]：
   - 39.9042在[-90, 0)? 否 → 1，范围缩为[0, 90]
   - 39.9042在[0, 45)?  是 → 0，范围缩为[0, 45]
   - 39.9042在[0, 22.5)? 否 → 1，范围缩为[22.5, 45]
   - ...继续二分

   经度范围[-180, 180]：同理

2. 交替合并经纬度二进制位：
   经度: 1101001000...
   纬度: 1011100001...
   合并: 11101 11011 00010 01000 ...
         (奇数位经度，偶数位纬度)

3. 转换为52位整数作为Sorted Set的score

GeoHash特性：
- 前缀相同的GeoHash值在地理上相近
- 编码越长，精度越高
- Redis使用52位GeoHash，精度约0.6米
```

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

**Python代码示例**：

```python
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class GeoService:
    def add_location(self, name: str, lng: float, lat: float):
        r.geoadd("locations", (lng, lat, name))

    def get_distance(self, place1: str, place2: str, unit: str = "km") -> float | None:
        return r.geodist("locations", place1, place2, unit=unit)

    def search_nearby(self, lng: float, lat: float, radius: float, unit: str = "km"):
        return r.geosearch(
            "locations",
            longitude=lng,
            latitude=lat,
            radius=radius,
            unit=unit,
            withdist=True,
            withcoord=True,
            sort="ASC",
        )

    def search_nearby_member(self, member: str, radius: float, unit: str = "km"):
        return r.geosearch(
            "locations",
            member=member,
            radius=radius,
            unit=unit,
            withdist=True,
            withcoord=True,
            sort="ASC",
        )
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
  "152694"  "152695"
  (entry1)  (entry2)
    │         │
  next ───► next ───► ...

注：相邻消息ID共享公共前缀，前缀被压缩存储
如 1630000000000-0 和 1630000000000-1 共享 "1630000000000-" 前缀
```

**深度解析：Radix Tree原理**

```
Radix Tree（基数树/压缩前缀树）是Trie的优化版本：

Trie的问题：每个字符一个节点，内存浪费
Radix Tree优化：合并只有一个子节点的路径

消息ID作为key（如1630000000000-0）：
- 按字节分割，共享公共前缀
- 相邻ID共享前缀，压缩存储
- 查找、插入、删除均为O(k)，k为key长度

Stream使用Radix Tree存储消息：
- key：消息ID（毫秒时间戳-序列号）
- value：消息内容（field-value对）
- 优势：内存效率高，范围查询快，自动按ID排序
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

**执行说明**：RDB生成的是时间点快照，适合备份和灾难恢复。文件紧凑、加载速度快，但可能丢失最近的数据变更。

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

**深度解析：写时复制（COW）机制**

```
COW（Copy-On-Write）是操作系统提供的内存管理机制：

1. fork()后，父子进程共享同一块物理内存
   父进程和子进程的页表指向相同的物理页

2. 当父进程修改某个内存页时：
   - 操作系统检测到写操作
   - 复制该页到新的物理页
   - 父进程页表指向新页
   - 子进程页表仍指向旧页

3. 子进程遍历内存时：
   - 读取的是fork时刻的内存快照
   - 不受父进程后续修改影响

COW的内存开销：
- 如果fork期间几乎没有写操作：额外内存≈0
- 如果fork期间大量写操作：额外内存≈修改的页数×4KB
- 典型场景：fork耗时约1-2秒，期间少量写入，额外内存约10-20%

COW的注意事项：
- 避免在RDB生成期间执行大量写操作
- 大Key的修改会复制整个页（4KB），增加内存压力
- 可通过info memory的used_memory_rss监控COW导致的内存增长
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

**深度解析：SAVE vs BGSAVE**

```
SAVE：同步保存
├── 调用rdbSave()直接在主线程执行
├── 阻塞所有客户端请求
├── 保存期间Redis无法响应任何命令
└── 仅在调试或紧急场景使用

BGSAVE：异步保存
├── fork()创建子进程
├── 子进程调用rdbSave()生成RDB
├── 主线程继续处理请求
├── 保存完成后子进程退出，主线程收到信号
└── 生产环境唯一推荐方式

自动保存的判断逻辑（serverCron每100ms检查）：
1. 检查save配置条件（如900秒内1次变更）
2. 如果满足任一条件且没有BGSAVE/BGREWRITEAOF在执行
3. 执行BGSAVE
```

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
┌────────┬──────────┬──────────┬──────────┬──────────┐
│ REDIS  │db_version│ databases│  EOF     │check_sum │
│(魔数)  │ (版本号)  │ (数据)   │(结束标记)│ (校验和)  │
└────────┴──────────┴──────────┴──────────┴──────────┘

深度解析各部分：
1. REDIS（5字节）：魔数，标识这是RDB文件
2. db_version（4字节）：RDB版本号（如0011）
3. databases：各数据库的键值对数据
   ├── SELECTDB：数据库编号
   ├── key_value_pairs：键值对
   │   ├── EXPIRETIME_MS：过期时间（如果有）
   │   ├── key：键名
   │   └── value：值（含类型和编码信息）
   └── EOF：数据库结束标记
4. EOF（1字节）：RDB文件结束标记
5. check_sum（8字节）：CRC64校验和，用于检测文件完整性
```

**扩展知识**：

- **RDB文件压缩**：`rdbcompression yes`开启LZF压缩，对字符串类型数据压缩存储，减少文件大小，但增加CPU开销。
- **RDB校验**：`rdbchecksum yes`在文件末尾写入CRC64校验和，加载时验证文件完整性，约10%性能开销。
- **RDB的局限性**：①可能丢失最近几分钟的数据 ②fork子进程在大内存实例上可能耗时较长 ③不同Redis版本的RDB格式可能不兼容

### 4.2 AOF持久化

**功能说明**：将Redis写命令以追加方式写入日志文件，实时性更高。

**执行说明**：AOF记录所有写操作命令，重启时重放命令恢复数据。数据安全性高，但文件体积大、恢复速度慢。

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

**深度解析：AOF的三种fsync策略对比**

```
always策略：
每条写命令执行后立即调用fsync()
├── 数据安全性最高：最多丢失一条命令
├── 性能影响最大：每条命令都等待磁盘I/O
├── 磁盘I/O成为瓶颈（SSD约10万IOPS，HDD约100-200IOPS）
└── 适用于数据安全性要求极高的场景（金融交易）

everysec策略：
每秒调用一次fsync()
├── 由后台线程每秒执行fsync
├── 最多丢失1秒数据（可接受）
├── 性能影响小（fsync在后台线程执行）
└── 生产环境推荐配置

no策略：
从不主动调用fsync，由操作系统决定
├── 操作系统通常每30秒刷一次
├── 可能丢失30秒数据
├── 性能最好
└── 仅适用于缓存场景（数据可丢失）
```

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

深度解析：AOF重写流程

1. 触发重写
   ├── 手动：BGREWRITEAOF
   └── 自动：AOF文件大小超过阈值
       ├── auto-aof-rewrite-percentage 100（当前大小是上次重写后大小的200%）
       └── auto-aof-rewrite-min-size 64mb（最小重写大小）

2. 执行重写
   ├── fork()创建子进程
   ├── 子进程遍历内存，生成新的AOF命令
   ├── 主进程将重写期间的新命令写入AOF重写缓冲区
   └── 子进程完成重写后，主进程将缓冲区命令追加到新AOF

3. 替换旧AOF
   ├── 原子性地用新AOF替换旧AOF
   └── 新AOF包含重写时的全量数据+重写期间的增量命令
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

**扩展知识**：

- **AOF文件损坏修复**：`redis-check-aof --fix appendonly.aof`可修复AOF文件中损坏的部分（截断到最近的有效命令）。
- **AOF重写期间的内存开销**：fork子进程+重写缓冲区，内存开销约为原AOF大小的2倍，需确保内存充足。
- **no-appendfsync-on-rewrite**：AOF重写期间禁用fsync，避免磁盘I/O竞争，但可能丢失数据。生产环境需权衡。

### 4.3 混合持久化

**功能说明**：结合RDB和AOF的优点，AOF重写时前半部分用RDB格式（加载快），后半部分用AOF格式（增量数据不丢失）。

```
混合持久化AOF文件结构：
┌────────────────────┬─────────────────────┐
│   RDB格式数据       │   AOF格式增量数据     │
│  （全量快照，加载快） │  （增量命令，数据完整） │
└────────────────────┴─────────────────────┘

深度解析：混合持久化的加载过程

1. 读取AOF文件头部
2. 识别到RDB格式标记
3. 先加载RDB部分（全量快照，速度快）
4. 再重放AOF增量部分（数据完整）
5. 加载完成，数据与重写时刻完全一致

优势：
- 加载速度接近纯RDB（RDB部分是二进制格式）
- 数据完整性接近纯AOF（增量部分记录所有变更）
- 文件大小介于RDB和AOF之间
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

**深度解析：生产环境持久化策略**

```
生产环境推荐配置：
1. 同时开启RDB和AOF
   ├── RDB：用于定时备份和快速恢复
   └── AOF：保证数据安全（everysec）

2. 混合持久化
   └── aof-use-rdb-preamble yes

3. RDB配置
   ├── save 900 1
   ├── save 300 10
   └── save 60 10000

4. AOF配置
   ├── appendonly yes
   ├── appendfsync everysec
   ├── auto-aof-rewrite-percentage 100
   └── auto-aof-rewrite-min-size 64mb

5. 定期备份
   ├── 每天将RDB文件备份到远程存储
   └── 保留最近7天的备份

数据恢复优先级：
Redis启动时 → 优先加载AOF（数据更完整）
AOF不存在 → 加载RDB
两者都不存在 → 空库启动
```

```bash
# 生产环境推荐配置
CONFIG SET appendonly yes
CONFIG SET aof-use-rdb-preamble yes
CONFIG SET appendfsync everysec
CONFIG SET save "900 1 300 10 60 10000"
```

---

## 模块五：发布订阅

### 5.1 基本发布订阅

**功能说明**：实现消息的发布与订阅，支持一对多的消息广播。

**执行说明**：发布者将消息发送到频道，所有订阅该频道的客户端都能收到消息。**注意：离线订阅者会丢失消息。**

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

**深度解析：Pub/Sub的实现机制**

```
Redis内部数据结构：
1. pubsub_channels字典
   ├── key：频道名（如"channel1"）
   └── value：订阅该频道的客户端链表

2. pubsub_patterns链表
   ├── 每个节点包含：模式（如"news.*"）+ 客户端
   └── 模式订阅使用glob风格通配符

PUBLISH命令执行流程：
1. 在pubsub_channels中查找频道
2. 遍历客户端链表，向每个客户端发送消息
3. 遍历pubsub_patterns，匹配模式，向匹配的客户端发送消息

性能注意事项：
- 订阅者过多时，PUBLISH会遍历所有订阅者，耗时增加
- 消息不持久化，离线客户端无法收到
- 适合实时通知、配置变更广播等场景
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

**Python代码示例**：

```python
import redis
import threading

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class PubSubService:
    def __init__(self):
        self.pubsub = r.pubsub()

    def publish(self, channel: str, message: str):
        r.publish(channel, message)

    def subscribe(self, channel: str, handler):
        self.pubsub.subscribe(**{channel: handler})
        thread = threading.Thread(target=self.pubsub.run_in_thread, daemon=True)
        thread.start()

def message_handler(message):
    print(f"收到消息：频道={message['channel']}, 数据={message['data']}")

service = PubSubService()
service.subscribe("notifications", message_handler)
service.publish("notifications", "系统启动完成")
```

---

## 模块六：复制

### 6.1 主从复制原理

**功能说明**：将主服务器的数据复制到从服务器，实现读写分离和数据备份。

**执行说明**：从服务器连接主服务器后，首次进行全量同步，之后通过增量同步保持数据一致。从服务器默认只读。

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

**深度解析：PSYNC协议的完整机制**

```
PSYNC <runid> <offset> 参数说明（Redis 2.8-4.0）：
- runid：主服务器实例的唯一ID（每次重启变化）
- offset：从服务器当前的复制偏移量

PSYNC2改进（Redis 4.0+）：
- 引入复制ID（replid）替代runid
- 主从切换后，新主继承旧主的replid和offset
- 从服务器可能通过PSYNC2实现部分重同步（即使主服务器变了）
- 减少了全量同步的概率

主服务器判断逻辑：
1. runid/replid不匹配 → 全量同步（主服务器重启过）
2. runid/replid匹配但offset不在复制缓冲区内 → 全量同步（断开太久）
3. runid/replid匹配且offset在缓冲区内 → 增量同步

复制积压缓冲区（repl-backlog）：
- 固定长度的FIFO队列（默认1MB）
- 主服务器每执行一条写命令，就追加到缓冲区
- 增量同步时，从offset位置开始发送

缓冲区大小计算：
- 写命令频率：每秒1000条，每条平均100字节 = 100KB/s
- 断开60秒：需要6MB缓冲区
- 建议设置：repl-backlog-size 64mb
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

**深度解析：主从复制的拓扑结构选择**

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

选型建议：
- 一主一从：简单备份，适合小规模
- 一主多从：读压力大时，多从分担读请求
- 级联复制：减轻主服务器RDB传输负担（中间层从服务器转发RDB给下层）

注意：
- 从服务器不要太多（建议≤5），否则主服务器fork和传输RDB压力大
- 级联复制可降低主服务器负载，但增加数据延迟
- 读写分离时注意复制延迟（从服务器数据可能落后于主服务器）
```

**Python代码示例**：

```python
import redis

master = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
slave = redis.Redis(host="127.0.0.1", port=6380, decode_responses=True)

class ReadWriteSplit:
    def __init__(self, master: redis.Redis, slaves: list[redis.Redis]):
        self.master = master
        self.slaves = slaves

    def write(self, key: str, value: str):
        self.master.set(key, value)

    def read(self, key: str) -> str | None:
        import random
        slave = random.choice(self.slaves)
        return slave.get(key)

    def get_master_info(self) -> dict:
        info = self.master.info("replication")
        return {
            "role": info["role"],
            "connected_slaves": info.get("connected_slaves", 0),
            "repl_offset": info.get("master_repl_offset", 0),
        }

rw = ReadWriteSplit(master, [slave])
rw.write("test_key", "test_value")
print(rw.read("test_key"))
```

---

## 模块七：哨兵

### 7.1 哨兵架构原理

**功能说明**：Redis Sentinel是Redis官方提供的高可用方案，自动监控主从复制状态，在主服务器故障时自动执行故障转移。

**执行说明**：至少部署3个Sentinel节点（奇数个，确保选举多数派），Sentinel通过心跳检测监控主从节点状态，故障时自动选举新主并通知客户端。

**原理**：Sentinel通过向主从节点发送INFO命令和PING命令来监控节点状态，当主节点下线时，通过Raft协议选举Leader Sentinel执行故障转移。

```
哨兵架构：
┌──────────────────────────────────────────────────────┐
│                    Sentinel集群                       │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐        │
│  │ Sentinel1 │  │ Sentinel2 │  │ Sentinel3 │        │
│  │ (端口26379)│  │ (端口26380)│  │ (端口26381)│        │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘        │
│        │              │              │               │
│        └──────┬───────┴──────┬───────┘               │
│               │              │                       │
└───────────────┼──────────────┼───────────────────────┘
                │              │
        ┌───────▼──────┐ ┌────▼───────┐
        │   Master      │ │  Slave1    │
        │  (端口6379)   │ │ (端口6380) │
        └──────────────┘ └────────────┘
                         ┌────────────┐
                         │  Slave2    │
                         │ (端口6381) │
                         └────────────┘
```

**深度解析：Sentinel的核心任务**

```
1. 监控（Monitoring）
   ├── 每秒向主从节点发送PING命令
   ├── 每秒向其他Sentinel发送PING命令
   ├── 每十秒向主节点发送INFO命令（发现从节点）
   └── 每两秒通过Pub/Sub交换节点信息

2. 通知（Notification）
   ├── 主观下线（SDOWN）：单个Sentinel认为节点下线
   │   └── 超过down-after-milliseconds未响应PING
   └── 客观下线（ODOWN）：多数Sentinel认为主节点下线
       └── 触发故障转移的前提条件

3. 自动故障转移（Automatic Failover）
   ├── 选举Leader Sentinel（Raft协议）
   ├── 选举新主节点
   ├── 通知从节点复制新主节点
   └── 通知客户端新主节点地址

4. 配置提供者（Configuration Provider）
   └── 客户端通过Sentinel获取主节点地址
```

### 7.2 故障转移流程

```
故障转移完整流程：

1. 主观下线检测
   Sentinel1 ──PING──► Master (无响应)
   Sentinel1标记Master为SDOWN

2. 客观下线确认
   Sentinel1询问其他Sentinel：Master是否下线？
   Sentinel2确认 ✓
   Sentinel3确认 ✓
   超过半数确认 → 标记Master为ODOWN

3. 选举Leader Sentinel
   ├── 使用类Raft选举协议（简化版，先到先得投票）
   ├── 超过半数Sentinel投票
   └── Leader Sentinel负责执行故障转移

4. 选举新主节点（选择条件优先级）
   ├── 排除已下线或断开的从节点
   ├── 优先级：replica-priority最小的优先
   ├── 复制偏移量：offset最大的优先（数据最新）
   └── runid：最小的优先（兜底条件）

5. 执行故障转移
   ├── Leader Sentinel向新主节点发送SLAVEOF NO ONE
   ├── 向其他从节点发送SLAVEOF <new_master>
   ├── 更新Sentinel配置
   └── 通过Pub/Sub通知客户端

6. 旧主节点恢复
   └── 旧主节点变为新主的从节点
```

**深度解析：类Raft选举Leader Sentinel**

```
Sentinel选举机制（简化版Raft）：
1. 每个Sentinel都有选举权
2. 选举规则：先到先得（FIFO），一个Sentinel只能投一票
3. 获得超过半数票的Sentinel成为Leader
4. Leader负责执行故障转移

选举过程：
Sentinel1发起选举 → 向Sentinel2、Sentinel3请求投票
├── Sentinel2先收到Sentinel1的请求 → 投票给Sentinel1
├── Sentinel3先收到Sentinel1的请求 → 投票给Sentinel1
└── Sentinel1获得2票（3个节点中的多数）→ 成为Leader

为什么需要奇数个Sentinel？
- 3个Sentinel：容忍1个故障，需2票通过
- 4个Sentinel：容忍1个故障，需3票通过（和3个一样）
- 5个Sentinel：容忍2个故障，需3票通过
- 结论：奇数个Sentinel性价比最高
```

### 7.3 哨兵配置与部署

**实战案例**：

```conf
# sentinel.conf（Sentinel1配置）
port 26379
sentinel monitor mymaster 127.0.0.1 6379 2
sentinel down-after-milliseconds mymaster 30000
sentinel failover-timeout mymaster 180000
sentinel parallel-syncs mymaster 1

# 配置说明：
# sentinel monitor <master-name> <ip> <port> <quorum>
#   quorum：判定客观下线所需的Sentinel数量（建议=节点数/2+1）
# down-after-milliseconds：主观下线超时时间（毫秒）
# failover-timeout：故障转移超时时间（毫秒）
# parallel-syncs：故障转移后同时向新主发起复制的从节点数
```

```bash
# 启动Sentinel
redis-sentinel /path/to/sentinel.conf

# 查看Sentinel状态
redis-cli -p 26379 SENTINEL master mymaster
redis-cli -p 26379 SENTINEL slaves mymaster
redis-cli -p 26379 SENTINEL sentinels mymaster

# 手动故障转移
redis-cli -p 26379 SENTINEL failover mymaster

# 查看当前主节点地址
redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster
```

**Python代码示例**：

```python
import redis
from redis.sentinel import Sentinel

sentinel_nodes = [
    ("127.0.0.1", 26379),
    ("127.0.0.1", 26380),
    ("127.0.0.1", 26381),
]

sentinel = Sentinel(sentinel_nodes, socket_timeout=0.5)

class SentinelClient:
    def __init__(self, sentinel: Sentinel, master_name: str = "mymaster"):
        self.sentinel = sentinel
        self.master_name = master_name

    def get_master(self) -> redis.Redis:
        return self.sentinel.master_for(
            self.master_name,
            socket_timeout=0.5,
            decode_responses=True,
        )

    def get_slave(self) -> redis.Redis:
        return self.sentinel.slave_for(
            self.master_name,
            socket_timeout=0.5,
            decode_responses=True,
        )

    def discover_master(self) -> tuple:
        return self.sentinel.discover_master(self.master_name)

client = SentinelClient(sentinel)
master = client.get_master()
master.set("key", "value")
slave = client.get_slave()
print(slave.get("key"))
```

---

## 模块八：集群

### 8.1 Redis Cluster架构原理

**功能说明**：Redis Cluster是Redis官方的分布式方案，支持自动数据分片、高可用和水平扩展。

**执行说明**：集群将数据划分为16384个哈希槽，每个主节点负责一部分槽。客户端通过MOVED重定向找到正确的节点。

**原理**：Redis Cluster使用**哈希槽（Hash Slot）**进行数据分片，每个key通过CRC16校验后对16384取模来确定所属的槽。

```
Redis Cluster架构：
┌─────────────────────────────────────────────────────┐
│                  Redis Cluster                       │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ Node A   │  │ Node B   │  │ Node C   │          │
│  │ 槽0~5460 │  │槽5461~10922│ │槽10923~16383│       │
│  │ (主)     │  │ (主)     │  │ (主)     │          │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘          │
│       │              │              │               │
│  ┌────▼─────┐  ┌────▼─────┐  ┌────▼─────┐          │
│  │ Node A1  │  │ Node B1  │  │ Node C1  │          │
│  │ (从)     │  │ (从)     │  │ (从)     │          │
│  └──────────┘  └──────────┘  └──────────┘          │
│                                                      │
│  节点间通过Gossip协议通信                              │
└─────────────────────────────────────────────────────┘

哈希槽计算：
key → CRC16(key) % 16384 → 槽编号 → 负责该槽的节点
```

**深度解析：16384个哈希槽的设计**

```
为什么是16384个槽？

1. 槽数量的权衡
   ├── 槽太少：节点多时每个节点分配的槽太少，迁移不灵活
   ├── 槽太多：Gossip消息中节点信息占用空间大
   └── 16384是折中选择

2. Gossip消息大小
   ├── 每个节点用2字节表示一个槽的状态（bitmap）
   ├── 16384个槽 = 16384/8 = 2048字节 = 2KB
   └── 如果是65536个槽 = 8KB，Gossip消息过大

3. 集群规模
   ├── 16384个槽支持最多16384个主节点
   ├── 实际推荐不超过1000个主节点
   └── 16384完全够用

哈希标签（Hash Tag）：
- 语法：{...}，只对大括号内的内容计算哈希
- 用途：确保相关key分配到同一个槽
- 示例：user:{1000}:name 和 user:{1000}:age 在同一个槽
```

### 8.2 Gossip协议

**功能说明**：节点间通过Gossip协议交换集群状态信息。

```
Gossip协议消息类型：

1. PING：节点间心跳检测
   ├── 每秒随机选择5个节点发送PING
   ├── 包含发送者已知的集群信息
   └── 确保所有节点最终一致

2. PONG：响应PING/MEET
   ├── 包含自身状态信息
   └── 包含已知的其他节点信息

3. MEET：加入集群
   ├── 新节点通过CLUSTER MEET加入集群
   └── 类似PING但表示握手请求

4. FAIL：标记节点下线
   ├── 检测到节点PFAIL（疑似下线）
   ├── 超过半数主节点确认后标记为FAIL
   └── 广播FAIL消息给所有节点
```

**深度解析：Gossip协议的工作机制**

```
Gossip协议特点：
1. 最终一致性：所有节点最终会达到一致状态
2. 去中心化：没有中心节点，每个节点地位平等
3. 容错性：部分节点故障不影响协议运行

Gossip传播模型：
节点A知道信息X
├── 第1轮：A告诉B、C → 3个节点知道
├── 第2轮：A、B、C各告诉2个节点 → 9个节点知道
├── 第3轮：9个节点各告诉2个 → 信息快速扩散
└── O(logN)轮后所有节点都知道

节点通信频率：
- 每秒随机选择5个节点
- 最长时间：N/5秒内与所有节点通信一次
- 100个节点：约20秒内所有节点通信一次
```

### 8.3 请求路由与重定向

**功能说明**：客户端请求可能被重定向到正确的节点。

```
请求路由流程：

客户端 ──GET key──► Node A (槽0~5460)
                     │
          key属于槽8000（Node B负责）
                     │
          ◄──MOVED 8000 NodeB:6380── 客户端
                     │
客户端 ──GET key──► Node B (槽5461~10922)
                     │
          ◄──"value"── 客户端

重定向类型：
1. MOVED：永久重定向
   ├── 槽已迁移到新节点
   ├── 客户端应更新槽映射表
   └── 后续请求直接发到新节点

2. ASK：临时重定向
   ├── 槽正在迁移中
   ├── 仅本次请求重定向
   └── 不更新槽映射表
```

### 8.4 集群配置与部署

**实战案例**：

```bash
# 创建集群（至少6个节点：3主3从）
redis-cli --cluster create \
  127.0.0.1:7001 127.0.0.1:7002 127.0.0.1:7003 \
  127.0.0.1:7004 127.0.0.1:7005 127.0.0.1:7006 \
  --cluster-replicas 1

# 查看集群信息
redis-cli -p 7001 CLUSTER INFO

# 查看集群节点
redis-cli -p 7001 CLUSTER NODES

# 查看槽分配
redis-cli -p 7001 CLUSTER SLOTS

# 添加主节点
redis-cli --cluster add-node 127.0.0.1:7007 127.0.0.1:7001

# 重新分配槽
redis-cli --cluster reshard 127.0.0.1:7001

# 添加从节点
redis-cli --cluster add-node 127.0.0.1:7008 127.0.0.1:7007 \
  --cluster-slave --cluster-master-id <master-node-id>

# 删除节点
redis-cli --cluster del-node 127.0.0.1:7007 <node-id>

# 检查集群状态
redis-cli --cluster check 127.0.0.1:7001

# 修复集群
redis-cli --cluster fix 127.0.0.1:7001
```

**节点配置文件示例**：

```conf
# redis-7001.conf
port 7001
cluster-enabled yes
cluster-config-file nodes-7001.conf
cluster-node-timeout 15000
appendonly yes
daemonize yes
```

**Python代码示例**：

```python
from redis.cluster import RedisCluster, ClusterNode

nodes = [
    ClusterNode("127.0.0.1", 7001),
    ClusterNode("127.0.0.1", 7002),
    ClusterNode("127.0.0.1", 7003),
]

rc = RedisCluster(startup_nodes=nodes, decode_responses=True)

rc.set("user:1000:name", "张三")
rc.set("user:1000:age", "25")
print(rc.get("user:1000:name"))

for key in rc.scan_iter("user:*"):
    print(f"key={key}, value={rc.get(key)}")

print(rc.cluster_info())
print(rc.cluster_nodes())
```

### 8.5 集群 vs 哨兵

| 对比项 | 哨兵 | 集群 |
|--------|------|------|
| 数据分片 | ❌ 不支持 | ✅ 16384个哈希槽 |
| 高可用 | ✅ 自动故障转移 | ✅ 自动故障转移 |
| 扩展性 | 只能读扩展 | 读写均可扩展 |
| 客户端 | 简单 | 需支持重定向 |
| 多key操作 | 支持 | 需同一槽（hash tag） |
| 适用规模 | 中小规模 | 大规模 |
| 运维复杂度 | 中等 | 较高 |

**深度解析：集群的局限性**

```
1. 多key操作限制
   ├── MGET/SDEL等命令要求所有key在同一槽
   ├── 解决方案：使用Hash Tag {tag}
   └── 示例：{user:1000}:name 和 {user:1000}:age

2. 事务限制
   ├── 事务中的key必须在同一槽
   └── 使用Hash Tag确保同一业务key在同一槽

3. Lua脚本限制
   ├── 脚本中访问的key必须在同一槽
   └── 通过KEYS数组传入key

4. 数据迁移
   ├── 迁移过程中性能下降
   ├── 槽迁移使用ASK重定向
   └── 建议在低峰期执行

5. 集群总线端口
   ├── 每个节点额外开放一个端口（客户端端口+10000）
   ├── 如7001端口的集群总线端口为17001
   └── 防火墙需同时放行两个端口
```

---

## 模块九：事务与Lua脚本

### 9.1 Redis事务

**功能说明**：将多个命令打包，按顺序一次性执行，保证命令不被其他客户端打断。

**执行说明**：事务通过MULTI开始，EXEC执行，DISCARD取消。Redis事务**不支持回滚**，某条命令失败后其余命令仍会继续执行。

**原理**：事务执行时，命令先进入队列，EXEC时按顺序原子执行。执行期间不会被其他客户端命令插入。

```
Redis事务流程：
┌────────┐    ┌────────┐    ┌────────┐    ┌────────┐
│ MULTI  │───►│ 命令入队 │───►│ EXEC  │───►│ 顺序执行 │
│(开始)  │    │(QUEUED)│    │(执行) │    │(返回结果)│
└────────┘    └────────┘    └────────┘    └────────┘
                  │
                  ▼
            ┌────────┐
            │DISCARD │
            │(取消)  │
            └────────┘

深度解析：Redis事务的ACID特性

1. 原子性（Atomicity）
   ├── 部分满足：命令要么全部执行，要么都不执行（EXEC前）
   ├── 不完全满足：EXEC后某条命令失败，其余命令继续执行
   └── 不支持回滚：与MySQL事务不同，Redis没有rollback机制

2. 一致性（Consistency）
   ├── 满足：事务执行前后，数据始终符合约束
   └── 入队错误（语法错误）会导致整个事务被拒绝执行

3. 隔离性（Isolation）
   ├── 满足：事务执行期间不会被其他客户端命令插入
   └── 单线程模型保证了隔离性

4. 持久性（Durability）
   ├── 取决于持久化配置
   ├── RDB/AOF开启时满足
   └── 纯内存模式不满足
```

**深度解析：为什么Redis不支持回滚？**

```
Redis设计者antirez的观点：
1. Redis命令错误通常是编程错误（类型错误等），不应出现在生产环境
2. 回滚需要额外的undo日志，增加复杂度
3. Redis追求简单和性能，回滚与设计理念冲突
4. 错误命令不会破坏数据一致性（入队错误拒绝执行，运行错误跳过）

对比MySQL：
- MySQL支持回滚 → 因为SQL操作可能因约束冲突而失败
- Redis不支持回滚 → 命令失败通常是代码bug，不应靠回滚兜底
```

**实战案例**：

```bash
# 基本事务
MULTI
SET account:A 1000
SET account:B 500
INCRBY account:A 500
DECRBY account:B 500
EXEC

# 取消事务
MULTI
SET key1 "value1"
DISCARD

# WATCH实现乐观锁
WATCH account:A
val = GET account:A
MULTI
SET account:A (val + 100)
EXEC
# 如果account:A在WATCH后被其他客户端修改，EXEC返回nil
```

### 9.2 WATCH乐观锁

**功能说明**：监视一个或多个key，如果在事务执行前这些key被其他客户端修改，事务将拒绝执行。

```
WATCH乐观锁流程：

客户端A                          客户端B
WATCH balance                    │
GET balance → 1000               │
MULTI                            │
SET balance 800                  │
                                 │ SET balance 500
                                 │ （修改了被WATCH的key）
EXEC → nil（事务被取消）          │

深度解析：WATCH的实现机制

1. WATCH命令
   ├── 将key加入watched_keys字典
   └── 记录key的当前版本号（修改计数器）

2. 修改检测
   ├── 任何对key的修改都会递增版本号
   └── 包括SET/DEL/INCR等写命令

3. EXEC检查
   ├── 检查所有WATCH的key版本号是否变化
   ├── 版本号变化 → 事务取消，返回nil
   └── 版本号未变 → 事务执行

4. WATCH的释放
   ├── EXEC/DISCARD自动释放所有WATCH
   └── UNWATCH手动释放所有WATCH
```

**Python代码示例**：

```python
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def transfer(from_key: str, to_key: str, amount: int) -> bool:
    with r.pipeline() as pipe:
        while True:
            try:
                pipe.watch(from_key)
                balance = int(pipe.get(from_key) or 0)
                if balance < amount:
                    pipe.unwatch()
                    return False
                pipe.multi()
                pipe.decrby(from_key, amount)
                pipe.incrby(to_key, amount)
                pipe.execute()
                return True
            except redis.WatchError:
                continue

r.set("account:A", 1000)
r.set("account:B", 500)
result = transfer("account:A", "account:B", 200)
print(f"转账结果：{result}")
print(f"A余额：{r.get('account:A')}, B余额：{r.get('account:B')}")
```

### 9.3 Lua脚本

**功能说明**：在Redis服务端原子执行Lua脚本，适合需要原子性的复杂操作。

**执行说明**：Lua脚本在Redis中是原子执行的，执行期间不会被其他命令打断。适合实现分布式锁、限流器等需要原子性的场景。

**原理**：Redis内嵌Lua解释器，脚本在服务端执行，整个脚本作为一个原子操作，不会被其他客户端命令打断。

```
Lua脚本执行流程：
┌──────────┐    ┌──────────┐    ┌──────────┐
│ 客户端    │───►│ Redis    │───►│ Lua解释器 │
│ EVAL     │    │ 接收脚本  │    │ 原子执行  │
└──────────┘    └──────────┘    └──────────┘
                                     │
                                执行期间阻塞
                                其他客户端等待

深度解析：Lua脚本的优势与限制

优势：
1. 原子性：整个脚本原子执行，无需担心并发问题
2. 减少网络开销：多条命令一次发送，减少RTT
3. 复用性：脚本可缓存（EVALSHA），客户端只需发送SHA1

限制：
1. 阻塞：脚本执行期间Redis无法处理其他请求
2. 超时：默认5秒超时（lua-time-limit），超时后可被BUSY中断
3. 无外部访问：不能访问文件系统、网络等外部资源
4. 随机性限制：不能调用SRANDMEMBER等随机命令（影响主从复制一致性）
```

**实战案例**：

```bash
# 基本Lua脚本
EVAL "return redis.call('SET', KEYS[1], ARGV[1])" 1 mykey myvalue

# 限流器脚本
EVAL "
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
if current > tonumber(ARGV[2]) then
    return 0
end
return 1
" 1 rate_limit:api 60 100

# 使用EVALSHA（脚本缓存）
SCRIPT LOAD "return redis.call('GET', KEYS[1])"
# 返回SHA1：4e6d8fc8bb01276962cce5371fa60f0f2eb6d3a2
EVALSHA 4e6d8fc8bb01276962cce5371fa60f0f2eb6d3a2 1 mykey

# 脚本管理
SCRIPT EXISTS <sha1>
SCRIPT FLUSH
SCRIPT KILL
```

**Python代码示例**：

```python
import redis
import time
import uuid

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class DistributedLock:
    def __init__(self, redis_client: redis.Redis, lock_name: str, timeout: int = 10):
        self.redis = redis_client
        self.lock_name = f"lock:{lock_name}"
        self.timeout = timeout
        self.identifier = str(uuid.uuid4())
        self._lock_script = """
        if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
            return 1
        end
        return 0
        """
        self._unlock_script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        end
        return 0
        """

    def acquire(self, retry_interval: float = 0.1, max_retries: int = 100) -> bool:
        for _ in range(max_retries):
            result = self.redis.eval(self._lock_script, 1, self.lock_name, self.identifier, str(self.timeout))
            if result == 1:
                return True
            time.sleep(retry_interval)
        return False

    def release(self) -> bool:
        result = self.redis.eval(self._unlock_script, 1, self.lock_name, self.identifier)
        return result == 1

lock = DistributedLock(r, "order:12345")
if lock.acquire():
    try:
        print("获取锁成功，执行业务逻辑")
    finally:
        lock.release()
else:
    print("获取锁失败")
```

---

## 模块十：性能优化

### 10.1 内存优化

**功能说明**：合理使用内存，减少内存碎片和浪费。

**深度解析：Redis内存结构**

```
Redis内存使用 = 自身内存 + 对象内存 + 缓冲内存 + 内存碎片

1. 自身内存：Redis进程本身占用的内存（约几MB，可忽略）

2. 对象内存：所有键值对占用的内存
   ├── RedisObject头部（16字节）
   ├── key的SDS
   └── value的数据

3. 缓冲内存：
   ├── 客户端缓冲区（client-output-buffer）
   ├── 复制积压缓冲区（repl-backlog）
   └── AOF缓冲区

4. 内存碎片：
   ├── 操作系统分配的内存 > Redis实际使用的内存
   ├── 碎片率 = used_memory_rss / used_memory
   ├── 碎片率 > 1.5：碎片严重，需要清理
   └── 碎片率 < 1.0：Redis使用了Swap，性能严重下降
```

**实战案例**：

```bash
# 查看内存使用
INFO memory

# 关键指标：
# used_memory：Redis分配的内存
# used_memory_rss：操作系统分配给Redis的内存
# mem_fragmentation_ratio：碎片率
# used_memory_peak：内存使用峰值

# 开启自动碎片清理（Redis 4.0+）
CONFIG SET activedefrag yes

# 碎片清理阈值
CONFIG SET active-defrag-threshold-lower 10
CONFIG SET active-defrag-threshold-upper 100

# 设置最大内存
CONFIG SET maxmemory 4gb

# 设置淘汰策略
CONFIG SET maxmemory-policy allkeys-lru
```

**内存优化技巧**：

```
1. 编码优化
   ├── Hash：field数量少时用listpack（省内存）
   ├── List：元素少时用listpack
   ├── Set：全是整数且数量少时用intset
   └── Sorted Set：元素少时用listpack

2. 数据结构选型
   ├── 小对象用Hash代替多个String（省RedisObject头部开销）
   ├── 位图代替Set（存储布尔值集合）
   └── HyperLogLog代替Set（去重计数）

3. key设计
   ├── 缩短key名（但保持可读性）
   ├── 设置合理的过期时间
   └── 避免大key（>10KB）

4. 共享对象池
   ├── Redis预创建0-9999的整数对象
   ├── 引用这些整数不分配新内存
   └── 超出范围的整数才创建新对象
```

### 10.2 命令优化

**深度解析：O(N)命令的危险性**

```
时间复杂度与风险：

O(1)命令：安全
├── GET/SET/DEL/HGET/HSET
└── 单次操作，时间固定

O(logN)命令：较安全
├── ZADD/ZREM/ZSCORE
└── 跳表查找，对数时间

O(N)命令：需谨慎
├── KEYS *：遍历所有key，生产环境禁用
├── HGETALL：遍历Hash所有field
├── SMEMBERS：遍历Set所有member
├── LRANGE 0 -1：遍历List所有元素
└── SORT：排序操作

O(N)命令的替代方案：
├── KEYS * → SCAN（渐进遍历）
├── HGETALL → HSCAN
├── SMEMBERS → SSCAN
├── DEL bigkey → UNLINK（异步删除）
└── FLUSHALL → FLUSHALL ASYNC
```

**实战案例**：

```bash
# 使用SCAN代替KEYS
SCAN 0 MATCH user:* COUNT 100

# 使用HSCAN代替HGETALL
HSCAN myhash 0 COUNT 100

# 使用UNLINK代替DEL（异步删除大key）
UNLINK bigkey

# Pipeline批量执行
redis-cli --pipe < commands.txt

# 批量操作示例
MSET key1 val1 key2 val2 key3 val3
MGET key1 key2 key3
```

**Python Pipeline示例**：

```python
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def batch_set(data: dict[str, str]) -> None:
    with r.pipeline() as pipe:
        for key, value in data.items():
            pipe.set(key, value)
        pipe.execute()

def batch_get(keys: list[str]) -> list[str | None]:
    with r.pipeline() as pipe:
        for key in keys:
            pipe.get(key)
        return pipe.execute()

data = {f"user:{i}:name": f"用户{i}" for i in range(1000)}
batch_set(data)
results = batch_get([f"user:{i}:name" for i in range(10)])
print(results)
```

### 10.3 BigKey与HotKey优化

**深度解析：BigKey问题**

```
BigKey定义：
├── String类型：value > 10KB
├── Hash类型：field数量 > 5000
├── List类型：元素数量 > 5000
├── Set类型：member数量 > 5000
└── Sorted Set类型：member数量 > 5000

BigKey的危害：
1. 内存不均匀：集群中某节点内存远高于其他节点
2. 阻塞：删除/序列化大key耗时，阻塞主线程
3. 网络拥塞：传输大key占用大量带宽
4. 超时：客户端操作大key可能超时

BigKey的发现：
1. redis-cli --bigkeys：扫描发现最大的key
2. redis-cli --memkeys：扫描发现内存占用最大的key
3. DEBUG OBJECT key：查看key的编码和大小
4. MEMORY USAGE key：查看key占用的内存字节数

BigKey的删除：
1. UNLINK：异步删除（Redis 4.0+）
2. 分批删除：HSCAN + HDEL、SSCAN + SREM
3. 序列化后删除：DUMP + RESTORE替换
```

**实战案例**：

```bash
# 扫描大key
redis-cli --bigkeys -i 0.1

# 查看key内存占用
MEMORY USAGE mykey

# 异步删除大key
UNLINK bigkey

# 分批删除Hash大key
HSCAN bighash 0 COUNT 200
# 然后对返回的field执行HDEL
```

**深度解析：HotKey问题**

```
HotKey定义：
├── 被高频访问的key（QPS > 1000）
└── 导致单个Redis节点压力过大

HotKey的危害：
1. 热点瓶颈：单个key的QPS过高，超过单节点处理能力
2. 集群倾斜：某节点QPS远高于其他节点
3. 连接数耗尽：热点key占用大量连接

HotKey的发现：
1. redis-cli --hotkeys：需要开启LFU淘汰策略
2. MONITOR命令：实时监控命令（慎用，影响性能）
3. 代理层统计：在Twemproxy/Redis代理层统计
4. 客户端统计：在应用层统计key访问频率

HotKey的解决方案：
1. 本地缓存：在应用层缓存热点数据（如Guava Cache）
2. 读写分离：增加从节点分担读压力
3. 分片：将热点key拆分为多个子key
4. 集群扩容：增加主节点数量
```

```python
# 依赖安装：pip install cryptography
import redis
import hashlib

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class HotKeySharding:
    def __init__(self, redis_client: redis.Redis, shard_count: int = 4):
        self.redis = redis_client
        self.shard_count = shard_count

    def _get_shard_key(self, key: str) -> str:
        shard = int(hashlib.md5(key.encode()).hexdigest(), 16) % self.shard_count
        return f"{key}:shard:{shard}"

    def set(self, key: str, value: str, ex: int | None = None):
        shard_key = self._get_shard_key(key)
        self.redis.set(shard_key, value, ex=ex)

    def get(self, key: str) -> str | None:
        shard_key = self._get_shard_key(key)
        return self.redis.get(shard_key)

sharding = HotKeySharding(r, shard_count=8)
sharding.set("hot_key", "value", ex=3600)
print(sharding.get("hot_key"))
```

### 10.4 缓存常见问题

**深度解析：缓存穿透、击穿、雪崩**

```
1. 缓存穿透（Cache Penetration）
   定义：查询不存在的数据，缓存和数据库都没有
   ├── 攻击者大量请求不存在的key
   └── 每次请求都穿透到数据库

   解决方案：
   ├── 缓存空值：查询为空时缓存null，设置短过期时间
   ├── 布隆过滤器：在缓存前加布隆过滤器，过滤不存在的key
   └── 参数校验：在应用层校验请求参数合法性

2. 缓存击穿（Cache Breakdown）
   定义：热点key过期瞬间，大量请求同时打到数据库
   ├── 某个热点key突然过期
   └── 大量并发请求同时查询数据库

   解决方案：
   ├── 互斥锁：只允许一个请求查询数据库，其余等待
   ├── 逻辑过期：不设置TTL，在value中存逻辑过期时间
   └── 预热：在过期前主动刷新缓存

3. 缓存雪崩（Cache Avalanche）
   定义：大量key同时过期，或Redis宕机，请求全部打到数据库
   ├── 大量key设置了相同的过期时间
   └── Redis节点故障导致大面积缓存不可用

   解决方案：
   ├── 过期时间加随机值：避免同时过期
   ├── 多级缓存：本地缓存 + Redis + 数据库
   ├── 高可用：哨兵/集群保证Redis可用
   └── 限流降级：数据库前加限流保护
```

**Python代码示例**：

```python
import redis
import time
import hashlib
import threading

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class CachePenetrationDefense:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def get_with_null_cache(self, key: str, db_query, null_expire: int = 60) -> str | None:
        value = self.redis.get(key)
        if value is not None:
            if value == "__NULL__":
                return None
            return value
        value = db_query(key)
        if value is None:
            self.redis.set(key, "__NULL__", ex=null_expire)
        else:
            self.redis.set(key, value, ex=3600)
        return value

class CacheBreakdownDefense:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self._lock_script = """
        if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
            return 1
        end
        return 0
        """
        self._unlock_script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        end
        return 0
        """

    def get_with_mutex(self, key: str, db_query, lock_timeout: int = 10) -> str | None:
        value = self.redis.get(key)
        if value is not None:
            return value
        lock_key = f"lock:cache:{key}"
        identifier = str(time.time())
        locked = self.redis.eval(self._lock_script, 1, lock_key, identifier, str(lock_timeout))
        if locked == 1:
            try:
                value = db_query(key)
                expire = 3600 + hash(key) % 300
                self.redis.set(key, value, ex=expire)
                return value
            finally:
                self.redis.eval(self._unlock_script, 1, lock_key, identifier)
        else:
            time.sleep(0.05)
            return self.get_with_mutex(key, db_query, lock_timeout)

class CacheAvalancheDefense:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def set_with_random_expire(self, key: str, value: str, base_expire: int = 3600):
        import random
        expire = base_expire + random.randint(0, base_expire // 5)
        self.redis.set(key, value, ex=expire)
```

---

## 模块十一：安全

### 11.1 认证与访问控制

**功能说明**：通过密码认证和ACL控制客户端对Redis的访问权限。

**执行说明**：Redis 6.0+引入ACL（Access Control List），支持细粒度的用户权限管理。早期版本仅支持requirepass全局密码。

**深度解析：Redis ACL机制**

```
Redis 6.0之前的安全模型：
├── 仅支持全局密码（requirepass）
├── 所有通过认证的客户端拥有全部权限
└── 无法限制特定用户只能执行特定命令

Redis 6.0+ ACL安全模型：
├── 支持多用户，每个用户独立配置
├── 可限制用户可执行的命令（command）
├── 可限制用户可访问的key（key pattern）
├── 可限制用户可访问的通道（channel pattern）
└── 默认用户：default（配置了密码则拥有全部权限）

ACL规则语法：
user <username> on|off >password ~pattern +@category +command -@category -command

常用规则：
├── on/off：启用/禁用用户
├── >password：设置密码
├── ~pattern：允许访问的key模式（~* 表示所有key）
├── +@all：允许所有命令类别
├── -@dangerous：禁止危险命令类别
├── +command：允许特定命令
└── -command：禁止特定命令

命令类别：
├── @admin：管理命令（CONFIG, SHUTDOWN等）
├── @write：写命令（SET, DEL等）
├── @read：读命令（GET, HGET等）
├── @dangerous：危险命令（FLUSHALL, DEBUG等）
├── @string：String类型命令
├── @hash：Hash类型命令
└── @pubsub：发布订阅命令
```

**实战案例**：

```bash
# 设置全局密码（Redis 6.0之前）
CONFIG SET requirepass "your_strong_password"
AUTH your_strong_password

# ACL操作（Redis 6.0+）
# 查看所有用户
ACL LIST

# 查看当前用户
ACL WHOAMI

# 创建只读用户
ACL SETUSER readonly on >readonly_pass ~* +@read -@write -@admin -@dangerous

# 创建只能访问特定key前缀的用户
ACL SETUSER app_user on >app_pass ~app:* +@read +@write -@admin -@dangerous

# 创建只能执行特定命令的用户
ACL SETUSER monitor on >monitor_pass ~* +INFO +PING +CLIENT +SLOWLOG

# 删除用户
ACL DELUSER readonly

# 持久化ACL配置到文件
CONFIG SET aclfile /etc/redis/users.acl
ACL SAVE
```

**Python代码示例**：

```python
import redis

admin = redis.Redis(host="localhost", port=6379, username="default", password="your_strong_password", decode_responses=True)

def create_redis_user(admin_client: redis.Redis, username: str, password: str, key_pattern: str = "*", commands: str = "+@read"):
    acl_rule = f"on >{password} ~{key_pattern} {commands}"
    admin_client.acl_setuser(username, acl_rule)
    print(f"用户 {username} 创建成功")

create_redis_user(admin, "app_reader", "reader_pass", "app:*", "+@read -@write -@admin")
create_redis_user(admin, "app_writer", "writer_pass", "app:*", "+@read +@write -@admin -@dangerous")

reader = redis.Redis(host="localhost", port=6379, username="app_reader", password="reader_pass", decode_responses=True)
print(reader.get("app:test"))
```

### 11.2 网络安全

**深度解析：Redis网络安全最佳实践**

```
1. 绑定网络接口
   ├── bind 127.0.0.1：仅本地访问
   ├── bind 0.0.0.0：所有接口（需配合防火墙）
   └── bind 10.0.0.1：仅内网访问

2. 保护模式
   ├── protected-mode yes（默认）
   ├── 未设置密码且绑定所有接口时，仅允许本地连接
   └── 生产环境必须设置密码或绑定内网

3. 防火墙规则
   ├── 仅允许应用服务器IP访问Redis端口
   ├── 禁止Redis端口暴露到公网
   └── 使用iptables/安全组限制访问

4. TLS加密（Redis 6.0+）
   ├── 支持TLS加密通信
   ├── 防止数据在传输过程中被窃听
   └── 配置：tls-cert-file, tls-key-file, tls-ca-cert-file

5. 端口安全
   ├── 修改默认端口6379（降低扫描风险）
   ├── 使用非特权端口（>1024）
   └── 集群模式需开放总线端口（客户端端口+10000）

6. 禁用危险命令
   ├── rename-command FLUSHALL ""
   ├── rename-command CONFIG ""
   ├── rename-command DEBUG ""
   └── rename-command SHUTDOWN ""（或重命名为复杂名称）
```

**实战案例**：

```conf
# redis.conf 安全配置
bind 10.0.0.1
protected-mode yes
requirepass your_strong_password
port 6380

# 禁用危险命令
rename-command FLUSHALL ""
rename-command CONFIG ""
rename-command DEBUG ""

# TLS配置（Redis 6.0+）
tls-cert-file /etc/redis/tls/redis.crt
tls-key-file /etc/redis/tls/redis.key
tls-ca-cert-file /etc/redis/tls/ca.crt
tls-port 6381
```

### 11.3 数据安全

**深度解析：Redis数据安全策略**

```
1. 数据加密
   ├── Redis不提供数据加密功能
   ├── 敏感数据应在应用层加密后存储
   └── 使用AES等对称加密算法

2. 数据脱敏
   ├── 日志中不输出敏感数据
   ├── MONITOR命令可能泄露敏感数据（生产环境禁用）
   └── 使用ACL限制MONITOR命令

3. 数据备份安全
   ├── RDB/AOF文件可能包含敏感数据
   ├── 备份文件需加密存储
   └── 传输备份文件时使用加密通道（SCP/SFTP）

4. 数据销毁
   ├── FLUSHALL/FLUSHDB：删除所有数据
   ├── 退役实例需彻底清除数据
   └── 云环境需确保磁盘被安全擦除
```

```python
import redis
import hashlib
import base64
from cryptography.fernet import Fernet

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class SecureRedis:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.key = Fernet.generate_key()
        self.cipher = Fernet(self.key)

    def set_encrypted(self, key: str, value: str, ex: int | None = None):
        encrypted = self.cipher.encrypt(value.encode())
        self.redis.set(key, encrypted.decode(), ex=ex)

    def get_decrypted(self, key: str) -> str | None:
        encrypted = self.redis.get(key)
        if encrypted is None:
            return None
        return self.cipher.decrypt(encrypted.encode()).decode()

secure = SecureRedis(r)
secure.set_encrypted("ssn:user:1001", "110101199001011234", ex=3600)
print(secure.get_decrypted("ssn:user:1001"))
```

---

## 模块十二：应用场景

### 12.1 缓存

**功能说明**：Redis最常见的应用场景，作为数据库前的缓存层，加速数据访问。

**深度解析：缓存架构设计**

```
缓存架构：
客户端 → 应用服务器 → Redis缓存 → 数据库
                      │            │
                      │ 命中       │ 未命中
                      ▼            ▼
                   返回数据     查询数据库
                               写入缓存
                               返回数据

缓存模式：
1. Cache-Aside（旁路缓存，最常用）
   ├── 读：先查缓存，未命中查数据库并写入缓存
   └── 写：先更新数据库，再删除缓存

2. Read-Through/Write-Through（穿透缓存）
   ├── 应用只与缓存交互
   └── 缓存层负责读写数据库

3. Write-Behind（异步写入）
   ├── 先写缓存，异步批量写入数据库
   └── 性能最好，但可能丢数据

Cache-Aside的并发问题：
1. 先删缓存再更新数据库
   ├── 线程A删除缓存
   ├── 线程B读取缓存未命中，查数据库（旧值）
   ├── 线程B将旧值写入缓存
   ├── 线程A更新数据库
   └── 结果：缓存与数据库不一致

2. 先更新数据库再删缓存（推荐）
   ├── 线程A更新数据库
   ├── 线程A删除缓存
   ├── 线程B读取缓存未命中，查数据库（新值）
   └── 结果：数据一致（极端并发下仍有短暂不一致）
```

**Python代码示例**：

```python
import redis
import json
import random

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class CacheAside:
    def __init__(self, redis_client: redis.Redis, default_expire: int = 3600):
        self.redis = redis_client
        self.default_expire = default_expire

    def get(self, key: str, db_query) -> str | None:
        value = self.redis.get(key)
        if value is not None:
            return value
        value = db_query(key)
        if value is not None:
            expire = self.default_expire + random.randint(0, 300)
            self.redis.set(key, value, ex=expire)
        return value

    def set(self, key: str, value: str, expire: int | None = None):
        self.redis.set(key, value, ex=expire or self.default_expire)

    def delete(self, key: str):
        self.redis.delete(key)

    def invalidate_after_write(self, key: str, db_write, value: str):
        db_write(key, value)
        self.redis.delete(key)
```

### 12.2 分布式锁

**功能说明**：在分布式系统中，多个进程需要互斥访问共享资源。

**深度解析：分布式锁的实现与注意事项**

```
分布式锁的核心要求：
1. 互斥性：同一时刻只有一个客户端持有锁
2. 防死锁：锁必须有过期时间，防止持有者崩溃后锁无法释放
3. 可重入：同一客户端可多次获取同一把锁
4. 高可用：锁服务不能成为单点故障

Redis分布式锁实现（RedLock算法）：
1. 获取当前时间
2. 依次向N个Redis实例请求加锁（使用相同的key和随机value）
3. 计算加锁耗时
4. 如果超过半数实例加锁成功且耗时小于锁过期时间，则加锁成功
5. 加锁失败则向所有实例释放锁

RedLock的争议：
├── Martin Kleppmann认为RedLock不安全（时钟跳跃、GC暂停）
├── antirez反驳认为这些场景在实际中极少发生
└── 建议：对正确性要求极高的场景使用ZooKeeper/etcd
```

**Python代码示例**：

```python
import redis
import time
import uuid
import threading

class RedisDistributedLock:
    def __init__(self, redis_client: redis.Redis, lock_name: str, timeout: int = 30, retry_delay: float = 0.1):
        self.redis = redis_client
        self.lock_key = f"dlock:{lock_name}"
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.identifier = str(uuid.uuid4())
        self._stop_renewal = True
        self._lock_script = """
        if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
            return 1
        end
        return 0
        """
        self._unlock_script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        end
        return 0
        """
        self._renew_script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('EXPIRE', KEYS[1], ARGV[2])
        end
        return 0
        """

    def acquire(self, max_wait: float = 10.0) -> bool:
        end_time = time.time() + max_wait
        while time.time() < end_time:
            result = self.redis.eval(self._lock_script, 1, self.lock_key, self.identifier, str(self.timeout))
            if result == 1:
                self._start_renewal_thread()
                return True
            time.sleep(self.retry_delay)
        return False

    def release(self) -> bool:
        self._stop_renewal = True
        result = self.redis.eval(self._unlock_script, 1, self.lock_key, self.identifier)
        return result == 1

    def _start_renewal_thread(self):
        self._stop_renewal = False
        def renew():
            while not self._stop_renewal:
                time.sleep(self.timeout / 3)
                if not self._stop_renewal:
                    self.redis.eval(self._renew_script, 1, self.lock_key, self.identifier, str(self.timeout))
        self._renewal_thread = threading.Thread(target=renew, daemon=True)
        self._renewal_thread.start()

lock = RedisDistributedLock(r, "order:process", timeout=30)
if lock.acquire(max_wait=5.0):
    try:
        print("执行业务逻辑")
        time.sleep(5)
    finally:
        lock.release()
```

### 12.3 排行榜

**功能说明**：利用Sorted Set实现实时排行榜，支持按分数排序和范围查询。

```python
import redis
import time

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class Leaderboard:
    def __init__(self, redis_client: redis.Redis, name: str):
        self.redis = redis_client
        self.key = f"leaderboard:{name}"

    def add_score(self, member: str, score: float):
        self.redis.zadd(self.key, {member: score})

    def incr_score(self, member: str, increment: float) -> float:
        return self.redis.zincrby(self.key, increment, member)

    def get_top(self, count: int = 10) -> list[tuple[str, float]]:
        results = self.redis.zrevrange(self.key, 0, count - 1, withscores=True)
        return [(m, s) for m, s in results]

    def get_rank(self, member: str) -> int | None:
        rank = self.redis.zrevrank(self.key, member)
        return rank + 1 if rank is not None else None

    def get_score(self, member: str) -> float | None:
        return self.redis.zscore(self.key, member)

    def get_around(self, member: str, count: int = 5) -> list[tuple[str, float]]:
        rank = self.redis.zrevrank(self.key, member)
        if rank is None:
            return []
        start = max(0, rank - count)
        end = rank + count
        results = self.redis.zrevrange(self.key, start, end, withscores=True)
        return [(m, s) for m, s in results]

lb = Leaderboard(r, "game_score")
for i in range(100):
    lb.add_score(f"player:{i}", i * 10 + (i % 7) * 3)

print("Top 10:", lb.get_top(10))
print("player:50 rank:", lb.get_rank("player:50"))
print("Around player:50:", lb.get_around("player:50", 3))
```

### 12.4 限流器

**功能说明**：限制单位时间内的请求次数，保护后端服务不被过载。

**深度解析：常见限流算法**

```
1. 固定窗口计数器
   ├── 将时间划分为固定窗口（如每分钟）
   ├── 每个窗口内计数
   └── 问题：窗口边界可能出现2倍流量

2. 滑动窗口计数器
   ├── 将窗口细分为更小的格子
   ├── 滑动计算窗口内的请求数
   └── 比固定窗口更平滑

3. 令牌桶（Token Bucket）
   ├── 以固定速率向桶中放入令牌
   ├── 请求消耗令牌，桶空则拒绝
   ├── 允许突发流量（桶中有令牌时）
   └── 最常用的限流算法

4. 漏桶（Leaky Bucket）
   ├── 请求进入桶中，以固定速率流出
   ├── 超出桶容量则拒绝
   └── 输出速率恒定，适合平滑流量
```

**Python代码示例**：

```python
import redis
import time

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class RateLimiter:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self._sliding_window_script = """
        local key = KEYS[1]
        local window = tonumber(ARGV[1])
        local limit = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])
        local member = now .. ':' .. ARGV[4]

        redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
        local count = redis.call('ZCARD', key)
        if count < limit then
            redis.call('ZADD', key, now, member)
            redis.call('EXPIRE', key, window / 1000)
            return 1
        end
        return 0
        """
        self._token_bucket_script = """
        local key = KEYS[1]
        local capacity = tonumber(ARGV[1])
        local rate = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])
        local requested = tonumber(ARGV[4])

        local info = redis.call('HMGET', key, 'tokens', 'last_time')
        local tokens = tonumber(info[1]) or capacity
        local last_time = tonumber(info[2]) or now

        local elapsed = now - last_time
        tokens = math.min(capacity, tokens + elapsed * rate)

        if tokens >= requested then
            tokens = tokens - requested
            redis.call('HMSET', key, 'tokens', tokens, 'last_time', now)
            redis.call('EXPIRE', key, capacity / rate + 10)
            return 1
        end
        redis.call('HMSET', key, 'tokens', tokens, 'last_time', now)
        return 0
        """

    def sliding_window_allow(self, key: str, window_ms: int, limit: int) -> bool:
        now = int(time.time() * 1000)
        uid = str(id(key)) + str(now)
        result = self.redis.eval(self._sliding_window_script, 1, key, str(window_ms), str(limit), str(now), uid)
        return result == 1

    def token_bucket_allow(self, key: str, capacity: int, rate_per_sec: float, requested: int = 1) -> bool:
        now = int(time.time() * 1000)
        rate_per_ms = rate_per_sec / 1000.0
        result = self.redis.eval(self._token_bucket_script, 1, key, str(capacity), str(rate_per_ms), str(now), str(requested))
        return result == 1

limiter = RateLimiter(r)
for i in range(15):
    allowed = limiter.sliding_window_allow("api:user:123", 60000, 10)
    print(f"请求{i+1}: {'允许' if allowed else '拒绝'}")
```

### 12.5 消息队列

**功能说明**：使用Redis Stream实现可靠的消息队列。

```python
import redis
import time
import uuid

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class StreamQueue:
    def __init__(self, redis_client: redis.Redis, stream_name: str, group_name: str = "default"):
        self.redis = redis_client
        self.stream = stream_name
        self.group = group_name
        self.consumer = str(uuid.uuid4())[:8]
        try:
            self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.ResponseError:
            pass

    def produce(self, fields: dict) -> str:
        msg_id = self.redis.xadd(self.stream, fields, maxlen=10000)
        return msg_id

    def consume(self, count: int = 1, block: int = 5000) -> list[dict]:
        messages = self.redis.xreadgroup(
            self.group, self.consumer,
            {self.stream: ">"},
            count=count, block=block
        )
        results = []
        if messages:
            for stream, msgs in messages:
                for msg_id, fields in msgs:
                    results.append({"id": msg_id, **fields})
                    self.redis.xack(self.stream, self.group, msg_id)
        return results

    def get_pending(self, min_idle_ms: int = 60000, count: int = 10) -> list[dict]:
        pending = self.redis.xpending_range(self.stream, self.group, "-", "+", count)
        results = []
        for p in pending:
            if p.get("time_since_delivered", 0) > min_idle_ms:
                msgs = self.redis.xrange(self.stream, p["message_id"], p["message_id"])
                for msg_id, fields in msgs:
                    results.append({"id": msg_id, **fields})
                    self.redis.xclaim(self.stream, self.group, self.consumer, min_idle_ms, [msg_id])
        return results

queue = StreamQueue(r, "orders", "order_workers")
queue.produce({"order_id": "1001", "amount": "99.9", "status": "created"})
queue.produce({"order_id": "1002", "amount": "199.9", "status": "created"})

msgs = queue.consume(count=5, block=1000)
for msg in msgs:
    print(f"处理订单: {msg}")
```

### 12.6 应用场景速查表

| 场景 | 推荐数据类型 | 关键命令 | 说明 |
|------|-------------|---------|------|
| 缓存 | String/Hash | SET/GET/HSET/HGET | 最常用场景 |
| 分布式锁 | String+Lua | SET NX EX | 需配合Lua保证原子性 |
| 排行榜 | Sorted Set | ZADD/ZREVRANGE | 实时排序 |
| 限流 | Sorted Set/Lua | ZADD/ZREMRANGEBYSCORE | 滑动窗口/令牌桶 |
| 消息队列 | Stream | XADD/XREADGROUP | 可靠消费确认 |
| 计数器 | String/HyperLogLog | INCR/PFADD | 精确/去重计数 |
| 会话存储 | Hash | HSET/HGETALL | 用户会话信息 |
| 社交关系 | Set/Sorted Set | SADD/SINTER | 好友/关注关系 |
| 地理位置 | Geo | GEOADD/GEOSEARCH | 附近的人/店 |
| 延迟队列 | Sorted Set | ZADD/ZRANGEBYSCORE | 定时任务 |

---

## 模块十三：高级特性

### 13.1 Redis Modules

**功能说明**：Redis 4.0+支持模块扩展，允许开发者用C语言编写自定义命令和数据结构。

**深度解析：常用Redis模块**

```
1. RedisJSON
   ├── 原生JSON数据类型
   ├── 支持JSON Path查询和修改
   ├── 命令：JSON.SET, JSON.GET, JSON.ARRAPPEND等
   └── 适用场景：文档存储、配置管理

2. RediSearch
   ├── 全文搜索引擎
   ├── 支持中文分词（需配置分词器）
   ├── 支持聚合、排序、 Suggestions
   └── 适用场景：站内搜索、自动补全

3. RedisBloom
   ├── 布隆过滤器（Bloom Filter）
   ├── 布谷鸟过滤器（Cuckoo Filter）
   ├── Count-Min Sketch
   ├── Top-K
   └── 适用场景：去重判断、频率统计

4. RedisTimeSeries
   ├── 时序数据类型
   ├── 支持降采样、聚合
   ├── 支持标签（label）
   └── 适用场景：监控指标、IoT数据

5. RedisGraph
   ├── 图数据库
   ├── 支持Cypher查询语言
   └── 适用场景：社交关系、知识图谱
```

**实战案例**：

```bash
# RedisBloom - 布隆过滤器
BF.RESERVE my_bloom 0.001 1000000
BF.ADD my_bloom "user:1001"
BF.EXISTS my_bloom "user:1001"
BF.EXISTS my_bloom "user:9999"

# RediSearch - 全文搜索
FT.CREATE idx:products ON HASH PREFIX 1 product: SCHEMA name TEXT SORTABLE price NUMERIC SORTABLE
HSET product:1 name "Redis实战" price 89.9
HSET product:2 name "Redis设计与实现" price 69.9
FT.SEARCH idx:products "Redis" SORTBY price ASC
FT.SEARCH idx:products "*" FILTER price 50 100

# RedisJSON
JSON.SET user:1001 $ '{"name":"张三","age":25,"address":{"city":"北京"}}'
JSON.GET user:1001 $.name
JSON.GET user:1001 $.address.city
JSON.SET user:1001 $.age 26
```

**Python代码示例**：

```python
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class BloomFilterService:
    def __init__(self, redis_client: redis.Redis, name: str, error_rate: float = 0.001, capacity: int = 1000000):
        self.redis = redis_client
        self.name = name
        try:
            self.redis.execute_command("BF.RESERVE", name, error_rate, capacity)
        except redis.ResponseError:
            pass

    def add(self, item: str) -> bool:
        return self.redis.execute_command("BF.ADD", self.name, item)

    def exists(self, item: str) -> bool:
        return bool(self.redis.execute_command("BF.EXISTS", self.name, item))

    def add_batch(self, items: list[str]) -> list:
        return self.redis.execute_command("BF.MADD", self.name, *items)

bf = BloomFilterService(r, "user_filter")
bf.add("user:1001")
print(bf.exists("user:1001"))
print(bf.exists("user:9999"))
```

### 13.2 Redis Stream高级用法

**深度解析：Stream的消费者组机制**

```
消费者组（Consumer Group）核心概念：

1. 消费者组
   ├── 一组消费者协同消费同一个Stream
   ├── 每条消息只会被组内一个消费者处理
   └── 不同组独立消费，互不影响

2. 消息投递
   ├── XREADGROUP：从组中读取未消费的消息
   ├── XACK：确认消息已处理
   └── XPENDING：查看已投递但未确认的消息

3. 消息重试
   ├── 超时未ACK的消息进入Pending列表
   ├── XCLAIM：其他消费者认领超时消息
   └── 实现至少一次消费（At-Least-Once）

4. 消息持久化
   ├── XADD的MAXLEN参数限制Stream长度
   ├── XTRIM手动裁剪
   └── 建议设置MAXLEN避免内存无限增长

Stream ID格式：<毫秒时间戳>-<序列号>
示例：1638360000000-0, 1638360000000-1

消息流转：
XADD → Stream → XREADGROUP → 消费者处理 → XACK
                    │
                    ▼
               XPENDING（未确认）
                    │
                    ▼
               XCLAIM（重新投递）
```

```bash
# 创建消费者组
XGROUP CREATE mystream mygroup 0 MKSTREAM

# 生产消息
XADD mystream * field1 value1 field2 value2

# 消费消息
XREADGROUP GROUP mygroup consumer1 COUNT 1 BLOCK 5000 STREAMS mystream >

# 确认消息
XACK mystream mygroup 1638360000000-0

# 查看待处理消息
XPENDING mystream mygroup

# 认领超时消息
XCLAIM mystream mygroup consumer2 60000 1638360000000-0

# 查看Stream信息
XINFO STREAM mystream
XINFO GROUPS mystream
XINFO CONSUMERS mystream mygroup
```

### 13.3 Redis多线程I/O

**深度解析：Redis 6.0多线程模型**

```
Redis 6.0之前：单线程处理所有操作
├── 命令接收 → 命令执行 → 结果返回
└── 全部在主线程完成

Redis 6.0+：多线程I/O（默认关闭）
├── 网络读写由I/O线程并行处理
├── 命令执行仍在主线程（保证原子性）
└── 整体流程：

读取阶段（多线程）：              执行阶段（单线程）：        写入阶段（多线程）：
┌──────────┐                     ┌──────────┐            ┌──────────┐
│I/O线程1   │──读取请求──┐        │          │            │I/O线程1   │──写响应──┐
│I/O线程2   │──读取请求──┼──►主线程──执行命令──┼──►主线程──┤I/O线程2   │──写响应──┼──►客户端
│I/O线程3   │──读取请求──┘        │          │            │I/O线程3   │──写响应──┘
└──────────┘                     └──────────┘            └──────────┘

配置参数：
├── io-threads 4：I/O线程数（建议≤CPU核心数）
├── io-threads-do-reads yes：读操作也使用多线程
└── 注意：线程数不宜过多，4-8个即可

为什么命令执行仍是单线程？
├── 避免并发竞争和锁的开销
├── 保证命令的原子性
├── Redis的瓶颈通常在网络I/O而非CPU
└── 单线程执行已经足够快（10万+QPS）
```

```bash
# 开启多线程I/O
CONFIG SET io-threads 4
CONFIG SET io-threads-do-reads yes
```

---

## 模块十四：运维监控

### 14.1 监控指标

**功能说明**：系统化监控Redis运行状态，及时发现和预防问题。

**深度解析：关键监控指标**

```
1. 性能指标
   ├── instantaneous_ops_per_sec：每秒执行命令数
   ├── latency_percentiles_usec：延迟百分位
   ├── connected_clients：当前连接数
   └── used_memory：内存使用量

2. 内存指标
   ├── used_memory：Redis分配的内存
   ├── used_memory_rss：操作系统分配的内存
   ├── mem_fragmentation_ratio：内存碎片率
   │   ├── < 1.0：使用了Swap（严重）
   │   ├── 1.0~1.5：正常
   │   └── > 1.5：碎片严重
   ├── maxmemory：最大内存限制
   └── evicted_keys：因淘汰而删除的key数

3. 持久化指标
   ├── rdb_last_bgsave_status：最近RDB保存状态
   ├── rdb_last_bgsave_time_sec：最近RDB保存耗时
   ├── aof_last_bgrewrite_status：最近AOF重写状态
   ├── aof_current_size：AOF文件当前大小
   └── aof_base_size：AOF重写后基础大小

4. 复制指标
   ├── connected_slaves：已连接的从节点数
   ├── master_repl_offset：主节点复制偏移量
   ├── slave_repl_offset：从节点复制偏移量
   └── repl_backlog_size：复制积压缓冲区大小

5. 集群指标
   ├── cluster_state：集群状态（ok/fail）
   ├── cluster_slots_assigned：已分配的槽数
   ├── cluster_slots_ok：正常槽数
   └── cluster_known_nodes：已知节点数
```

**实战案例**：

```bash
# 实时监控命令
redis-cli --stat
redis-cli --latency
redis-cli --latency-history
redis-cli --latency-dist
redis-cli --intrinsic-latency 5

# 查看慢查询
SLOWLOG GET 10
SLOWLOG LEN
CONFIG SET slowlog-log-slower-than 10000
CONFIG SET slowlog-max-len 128

# 查看客户端连接
CLIENT LIST
CLIENT SETNAME myapp
CLIENT KILL ADDR 127.0.0.1:52341
```

**Python监控代码示例**：

```python
import redis
import time
import json

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

class RedisMonitor:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def get_metrics(self) -> dict:
        info = self.redis.info()
        return {
            "ops_per_sec": info.get("instantaneous_ops_per_sec", 0),
            "connected_clients": info.get("connected_clients", 0),
            "used_memory_mb": round(info.get("used_memory", 0) / 1024 / 1024, 2),
            "used_memory_peak_mb": round(info.get("used_memory_peak", 0) / 1024 / 1024, 2),
            "fragmentation_ratio": info.get("mem_fragmentation_ratio", 1.0),
            "keys": info.get("db0", {}).get("keys", 0) if "db0" in info else 0,
            "hits": info.get("keyspace_hits", 0),
            "misses": info.get("keyspace_misses", 0),
            "hit_rate": self._calc_hit_rate(info),
            "evicted_keys": info.get("evicted_keys", 0),
            "rdb_last_status": info.get("rdb_last_bgsave_status", "unknown"),
            "aof_last_status": info.get("aof_last_bgrewrite_status", "unknown"),
        }

    def _calc_hit_rate(self, info: dict) -> str:
        hits = info.get("keyspace_hits", 0)
        misses = info.get("keyspace_misses", 0)
        total = hits + misses
        if total == 0:
            return "N/A"
        return f"{hits / total * 100:.1f}%"

    def check_health(self) -> dict:
        metrics = self.get_metrics()
        alerts = []
        if metrics["fragmentation_ratio"] > 1.5:
            alerts.append(f"内存碎片率过高: {metrics['fragmentation_ratio']}")
        if metrics["fragmentation_ratio"] < 1.0:
            alerts.append("正在使用Swap，性能严重下降")
        if metrics["evicted_keys"] > 0:
            alerts.append(f"有key被淘汰: {metrics['evicted_keys']}")
        if metrics["rdb_last_status"] != "ok":
            alerts.append(f"RDB保存异常: {metrics['rdb_last_status']}")
        return {"metrics": metrics, "alerts": alerts}

monitor = RedisMonitor(r)
health = monitor.check_health()
print(json.dumps(health, indent=2, ensure_ascii=False))
```

### 14.2 备份与恢复

**深度解析：Redis备份策略**

```
1. RDB备份
   ├── 定时执行BGSAVE
   ├── 将dump.rdb复制到备份存储
   ├── 保留多个时间点的备份
   └── 恢复：将RDB文件放到数据目录，重启Redis

2. AOF备份
   ├── 定时复制appendonly.aof
   ├── 注意：AOF可能正在被写入
   └── 恢复：将AOF文件放到数据目录，重启Redis

3. 备份脚本示例
   ├── 每小时执行BGSAVE
   ├── 复制RDB到备份目录（按时间命名）
   ├── 保留最近7天的备份
   └── 定期验证备份文件完整性

4. 云环境备份
   ├── AWS ElastiCache：自动快照
   ├── 阿里云Redis：自动备份
   └── 腾讯云Redis：数据备份
```

```bash
# 手动备份
BGSAVE
cp /var/lib/redis/dump.rdb /backup/dump_$(date +%Y%m%d_%H%M%S).rdb

# 验证RDB文件
redis-check-rdb /backup/dump_20260508_120000.rdb

# 恢复步骤
# 1. 停止Redis
redis-cli SHUTDOWN NOSAVE
# 2. 替换RDB文件
cp /backup/dump_20260508_120000.rdb /var/lib/redis/dump.rdb
# 3. 启动Redis
redis-server /etc/redis/redis.conf
```

### 14.3 常见故障排查

**深度解析：Redis故障排查流程**

```
1. 连接问题
   ├── 无法连接
   │   ├── 检查Redis进程是否运行
   │   ├── 检查端口是否监听
   │   ├── 检查防火墙规则
   │   └── 检查bind配置
   ├── 连接超时
   │   ├── 检查网络延迟
   │   ├── 检查连接数是否达到上限
   │   └── 检查是否有慢查询阻塞
   └── 认证失败
       ├── 检查密码是否正确
       └── 检查ACL配置

2. 性能问题
   ├── 响应变慢
   │   ├── 检查慢查询日志
   │   ├── 检查是否使用了O(N)命令
   │   ├── 检查是否在使用Swap
   │   ├── 检查fork耗时（RDB/AOF重写）
   │   └── 检查网络延迟
   ├── 内存持续增长
   │   ├── 检查key数量和大小
   │   ├── 检查是否设置了过期时间
   │   ├── 检查客户端输出缓冲区
   │   └── 检查是否有内存泄漏
   └── CPU使用率高
       ├── 检查是否有大量复杂命令
       ├── 检查是否在执行RDB/AOF重写
       └── 检查连接数是否过多

3. 数据问题
   ├── 数据丢失
   │   ├── 检查持久化配置
   │   ├── 检查RDB/AOF文件是否正常
   │   ├── 检查是否使用了FLUSHALL
   │   └── 检查淘汰策略
   ├── 主从不一致
   │   ├── 检查复制偏移量差异
   │   ├── 检查网络延迟
   │   └── 检查复制积压缓冲区
   └── 集群故障
       ├── 检查CLUSTER INFO
       ├── 检查节点状态
       └── 检查槽分配
```

```bash
# 故障排查常用命令
# 1. 检查Redis状态
INFO all
INFO memory
INFO replication
INFO persistence
INFO clients

# 2. 检查慢查询
SLOWLOG GET 20

# 3. 检查客户端连接
CLIENT LIST

# 4. 检查大key
redis-cli --bigkeys -i 0.1

# 5. 检查内存
MEMORY DOCTOR

# 6. 检查延迟
redis-cli --latency-history -i 1

# 7. 检查集群状态
CLUSTER INFO
CLUSTER NODES

# 8. 检查Sentinel状态
redis-cli -p 26379 SENTINEL master mymaster
```

### 14.4 运维最佳实践

```
1. 部署规范
   ├── 物理机/虚拟机部署（推荐）
   │   ├── 避免虚拟化带来的延迟
   │   └── 独占机器资源
   ├── 容器部署
   │   ├── 注意内存限制
   │   ├── 禁用THP（Transparent Huge Pages）
   │   └── 设置合理的OOM策略
   └── 云服务
       ├── 使用托管服务（减少运维成本）
       └── 注意网络延迟

2. 系统优化
   ├── 禁用THP：echo never > /sys/kernel/mm/transparent_hugepage/enabled
   ├── 调整vm.overcommit_memory=1：允许Redis使用更多内存
   ├── 调整net.core.somaxconn=65535：增加TCP连接队列
   ├── 调整net.ipv4.tcp_max_syn_backlog=65535
   └── 关闭swap：swapoff -a

3. 配置规范
   ├── bind：绑定内网IP
   ├── protected-mode yes
   ├── requirepass：设置强密码
   ├── maxmemory：设置内存上限
   ├── maxmemory-policy：选择合适的淘汰策略
   ├── timeout 300：客户端超时时间
   ├── tcp-keepalive 60：TCP心跳
   ├── loglevel notice：日志级别
   └── slowlog-log-slower-than 10000：慢查询阈值

4. 日常巡检
   ├── 每日：检查内存使用、连接数、慢查询
   ├── 每周：检查RDB/AOF状态、复制延迟
   ├── 每月：检查集群健康、备份完整性
   └── 每季度：压力测试、容量规划

5. 容量规划
   ├── 内存：预留30%缓冲（COW、碎片等）
   ├── CPU：单节点QPS上限约10万
   ├── 网络：估算带宽需求（命令大小×QPS）
   └── 磁盘：RDB/AOF文件大小×保留份数
```

---

## 附录

### 附录A：Redis命令速查表

| 分类 | 命令 | 说明 | 时间复杂度 |
|------|------|------|-----------|
| **通用** | DEL key | 删除key | O(1)~O(N) |
| | EXISTS key | 判断key是否存在 | O(1) |
| | EXPIRE key seconds | 设置过期时间 | O(1) |
| | TTL key | 查看剩余过期时间 | O(1) |
| | TYPE key | 查看key类型 | O(1) |
| | RENAME key newkey | 重命名key | O(1) |
| **String** | SET key value | 设置值 | O(1) |
| | GET key | 获取值 | O(1) |
| | INCR key | 自增1 | O(1) |
| | INCRBY key n | 自增n | O(1) |
| | MGET key [key...] | 批量获取 | O(N) |
| | MSET key val [key val...] | 批量设置 | O(N) |
| | SETNX key value | 不存在时设置 | O(1) |
| | SET key val EX sec NX | 分布式锁 | O(1) |
| **Hash** | HSET key field val | 设置字段 | O(1) |
| | HGET key field | 获取字段 | O(1) |
| | HMGET key field [field...] | 批量获取 | O(N) |
| | HMSET key f v [f v...] | 批量设置 | O(N) |
| | HGETALL key | 获取所有字段 | O(N) |
| | HDEL key field [field...] | 删除字段 | O(N) |
| | HINCRBY key field n | 字段自增 | O(1) |
| **List** | LPUSH key val [val...] | 左侧插入 | O(N) |
| | RPUSH key val [val...] | 右侧插入 | O(N) |
| | LPOP key | 左侧弹出 | O(1) |
| | RPOP key | 右侧弹出 | O(1) |
| | LRANGE key start stop | 范围查询 | O(S+N) |
| | LLEN key | 列表长度 | O(1) |
| **Set** | SADD key member [member...] | 添加成员 | O(N) |
| | SREM key member [member...] | 删除成员 | O(N) |
| | SMEMBERS key | 获取所有成员 | O(N) |
| | SISMEMBER key member | 判断成员是否存在 | O(1) |
| | SINTER key [key...] | 交集 | O(N*M) |
| | SUNION key [key...] | 并集 | O(N) |
| | SCARD key | 成员数量 | O(1) |
| **Sorted Set** | ZADD key score member | 添加成员 | O(logN) |
| | ZREM key member | 删除成员 | O(logN) |
| | ZSCORE key member | 获取分数 | O(1) |
| | ZRANGE key start stop | 按排名范围查询 | O(logN+S) |
| | ZREVRANGE key start stop | 按排名倒序查询 | O(logN+S) |
| | ZRANGEBYSCORE key min max | 按分数范围查询 | O(logN+S) |
| | ZRANK key member | 获取排名 | O(logN) |
| | ZCARD key | 成员数量 | O(1) |
| | ZINCRBY key incr member | 增加分数 | O(logN) |

### 附录B：Redis配置参数速查

| 参数 | 默认值 | 说明 |
|------|--------|------|
| bind | 127.0.0.1 | 绑定IP |
| port | 6379 | 监听端口 |
| protected-mode | yes | 保护模式 |
| requirepass | 无 | 认证密码 |
| maxmemory | 0（无限制） | 最大内存 |
| maxmemory-policy | noeviction | 淘汰策略 |
| timeout | 0（无超时） | 客户端超时 |
| tcp-keepalive | 300 | TCP心跳间隔 |
| loglevel | notice | 日志级别 |
| databases | 16 | 数据库数量 |
| save | 900 1 / 300 10 / 60 10000 | RDB自动保存策略 |
| appendonly | no | 是否开启AOF |
| appendfsync | everysec | AOF同步策略 |
| auto-aof-rewrite-percentage | 100 | AOF重写百分比 |
| auto-aof-rewrite-min-size | 64mb | AOF重写最小大小 |
| aof-use-rdb-preamble | yes | 混合持久化 |
| slowlog-log-slower-than | 10000 | 慢查询阈值（微秒） |
| slowlog-max-len | 128 | 慢查询最大条数 |
| replica-read-only | yes | 从节点只读 |
| repl-backlog-size | 1mb | 复制积压缓冲区大小 |
| cluster-enabled | no | 是否开启集群 |
| cluster-node-timeout | 15000 | 集群节点超时 |
| lua-time-limit | 5000 | Lua脚本超时（毫秒） |
| io-threads | 1 | I/O线程数 |
| io-threads-do-reads | no | 读操作多线程 |

### 附录C：淘汰策略详解

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| noeviction | 不淘汰，写入报错 | 数据不能丢失 |
| allkeys-lru | 所有key中淘汰最久未使用的 | 通用缓存 |
| allkeys-lfu | 所有key中淘汰使用频率最低的 | 热点数据缓存 |
| allkeys-random | 所有key中随机淘汰 | 无访问热点 |
| volatile-lru | 有过期时间的key中淘汰最久未使用的 | 混合存储 |
| volatile-lfu | 有过期时间的key中淘汰频率最低的 | 混合存储 |
| volatile-random | 有过期时间的key中随机淘汰 | 混合存储 |
| volatile-ttl | 有过期时间的key中淘汰TTL最短的 | 混合存储 |

```
淘汰策略选择决策树：
是否所有数据都可被淘汰？
├── 是 → 访问模式是否有热点？
│        ├── 是 → allkeys-lfu（推荐）
│        └── 否 → allkeys-lru
└── 否 → 部分数据有过期时间？
         ├── 是 → volatile-lru 或 volatile-lfu
         └── 否 → noeviction
```

### 附录D：Redis版本特性

| 版本 | 发布时间 | 重要特性 |
|------|---------|---------|
| 2.8 | 2013 | PSYNC部分重同步 |
| 3.0 | 2015 | Redis Cluster正式版 |
| 3.2 | 2016 | GEO命令、SPOP支持count参数 |
| 4.0 | 2017 | Module系统、PSYNC2、混合持久化、异步删除 |
| 5.0 | 2018 | Stream数据类型、新列表编码(listpack) |
| 6.0 | 2020 | ACL、多线程I/O、RESP3、TLS |
| 6.2 | 2021 | 新增命令(FUNCTION、LPOS等)、客户端缓存优化 |
| 7.0 | 2022 | Function特性、多AOF文件、集群支持Function |
| 7.2 | 2023 | 性能优化、新增命令 |

### 附录E：推荐学习资源

| 资源 | 说明 |
|------|------|
| 《Redis设计与实现》 | 深入理解Redis内部实现 |
| 《Redis开发与运维》 | 实战运维经验总结 |
| Redis官方文档 | https://redis.io/docs/ |
| Redis源码 | https://github.com/redis/redis |
| Redis命令参考 | https://redis.io/commands/ |

---

> **本增强版学习手册**在原手册基础上，为每个操作步骤补充了详细的执行说明、技术原理、底层机制解析、代码示例和扩展知识，旨在帮助读者既能指导实际操作，又能深入理解Redis的技术原理。