# 07 事务与 Lua 脚本

> Redis 通过 MULTI/EXEC 提供弱事务，借助 Lua 脚本与 Functions 实现真正的原子语义，配合 Pipeline 降低网络开销。

## 零基础前置认知

**这篇在讲什么**：Redis 的"事务"和数据库事务很不一样——**MULTI/EXEC** 只是"把几条命令排好队一次性执行"（命令之间不被插入=隔离，但出错不回滚）；要像写函数一样写出**带逻辑的原子操作**（先判断后执行）得靠 **Lua 脚本**（EVAL/EVALSHA）或其升级版 **Functions**（Redis 7 可持久化的函数库）；**Pipeline** 则纯粹是"省往返"（不原子）。读完你能写出"库存扣减""WATCH 转账""滑动窗口限流"这三种经典脚本，并知道哪种场景该用哪个。配套可运行代码见 [codes/](./codes/)（transaction_demo、pipeline_batch、run_lua、stock_deduct.lua、release_lock.lua）。

> 辅助类比：**MULTI/EXEC** 是"把几件事记在一张便笺上一次性完成"（串行不被打断，但写错字不能改——错一条不影响其他条，也不回滚）；**WATCH** 是"办事前先在门口贴告示：若这柜子被动过就作废"（CAS 乐观锁，失败重来）；**Lua 脚本**是"把整套流程写成一页程序交给 Redis 一个人一次算完"（完全原子、可判断可循环，但算太久会堵住柜台）；**Functions** 是"把这页程序正式装订成册存档"（重启不丢、可管理、从库按定义复制结果）；**Pipeline** 是"把多张便笺装一个信封一次寄出"（省邮费=省 RTT，但不保证一起办）。

**基础名词集**：

| 名词 | 一句话定义 | 与相邻概念的关系 |
|------|-----------|----------------|
| MULTI/EXEC | 开启事务排队 / 批量执行 | 执行时不被其他命令插入（隔离）；出错不回滚（非原子回滚） |
| DISCARD | 取消已入队的事务 | 与 EXEC 二选一；EXEC/DISCARD 后自动释放 WATCH |
| WATCH | 乐观锁：监视 key，被改则事务作废 | 配合 MULTI 实现 CAS；失效返回 nil，客户端重试 |
| EVAL/EVALSHA | 执行 Lua 脚本 / 按缓存 SHA1 执行 | 脚本**完全原子**；EVALSHA 减少网络传输 |
| KEYS[n]/ARGV[n] | 脚本的键参数 / 附加参数 | 键必须走 KEYS（Cluster 路由、ACL 审计），常量走 ARGV |
| Functions | Redis 7 持久化函数库（`FUNCTION LOAD`/`FCALL`） | 脚本内参数用 `keys[n]`/`args[n]`（小写）；替代长期复用 EVAL |
| Pipeline | 多条命令一次往返（不保证原子） | 与事务最大区别：只省 RTT 不做隔离 |
| lua-time-limit | 脚本超时（默认 5s） | 超时后可 SCRIPT KILL；写命令执行后不可杀 |

> 区分/注意：**事务 ≠ Lua ≠ Pipeline** 三者经常被混为一谈——事务保证"排队命令不被插入"（隔离）但不回滚；Lua 保证"整段逻辑原子"（更彻底，可分支）；Pipeline 只优化"网络往返"（既不隔离也不原子），三者可按需组合（Pipeline 包 MULTI、Lua 里 call 多条）。**EVAL 脚本不持久化**：重启后 SHA 缓存没了，客户端要重新 SCRIPT LOAD/EVAL——长期业务脚本务必用 Functions。**脚本别写"读全部"**：循环 `KEYS *`/大结构遍历会在单线程里堵死整个 Redis。

## 学习目标

| 级别 | 目标 | 验证方式 |
|------|------|----------|
| L1 认知 | 理解 Redis 事务的 ACID 特性与限制 | 能说出 Redis 不支持回滚的原因 |
| L2 操作 | 熟练使用 MULTI/EXEC/WATCH 与 EVAL/EVALSHA | 写出乐观锁转账与限流 Lua 脚本 |
| L3 架构 | 区分事务、Lua、Pipeline 三者的应用边界 | 根据场景选型并说明理由 |
| L4 实战 | 使用 Redis 7 Functions 管理可复用脚本 | 完成限流器函数库的加载与调用 |

## 前置知识

| 主题 | 文档 | 关键点 |
|------|------|--------|
| 单线程模型 | [01 Redis 架构与核心概念](../01_Redis架构与核心概念/01_Redis架构与核心概念.md) | 单线程天然保证命令串行执行 |
| String 与 INCR | [02 数据类型与底层实现](../02_数据类型与底层实现/02_数据类型与底层实现.md) | 原子计数是事务的基础 |
| 持久化机制 | [03 持久化机制](../03_持久化机制/03_持久化机制.md) | 持久性取决于 RDB/AOF 配置 |
| 哈希槽限制 | [06 集群与分片](../06_集群与分片/06_集群与分片.md) | Cluster 下事务 key 必须同槽 |

## 一、Redis 事务基础

### 1.1 事务三连命令

Redis 事务通过 `MULTI` 开启、`EXEC` 提交、`DISCARD` 取消，命令在 EXEC 前只入队不执行。

```
事务执行流程：
┌────────┐    ┌──────────────┐    ┌──────────┐
│ MULTI  │───►│ 命令入队      │───►│  EXEC    │
│(开启)  │    │ SET key1 v1  │    │ (执行)   │
│        │    │ INCR key2    │    │ 返回结果  │
│        │    │ GET key1     │    │          │
└────────┘    └──────────────┘    └──────────┘
                      │
                      ▼
                ┌──────────┐
                │ DISCARD  │
                │ (取消)   │
                └──────────┘
```

```bash
# 基本事务
MULTI
SET account:A 1000
SET account:B 500
INCRBY account:A 500
DECRBY account:B 500
EXEC
# 返回每条命令的结果数组

# 取消事务（队列中命令不会执行）
MULTI
SET key1 "value1"
DISCARD
```

### 1.2 ACID 特性分析

Redis 事务的 ACID 与传统关系数据库不同，需逐项拆解：

| 特性 | 支持情况 | 说明 |
|------|----------|------|
| 原子性 Atomicity | 部分支持 | EXEC 前全部入队成功才执行；EXEC 后某条失败，其余继续执行，**不支持回滚** |
| 一致性 Consistency | 满足 | 入队阶段语法错误会拒绝整个事务；运行错误不破坏数据完整性 |
| 隔离性 Isolation | 满足 | 单线程模型保证事务执行期间不被其他客户端命令插入 |
| 持久性 Durability | 取决于配置 | AOF `appendfsync always` 满足；`everysec` 存在 1 秒窗口；纯内存模式不满足 |

### 1.3 两类错误的不同处理

```
错误类型 1：入队错误（语法错误）
┌──────────────────────────────────────┐
│ MULTI                                │
│ SET key value                        │
│ WRONGCMD xxx        ← 命令不存在     │
│ INCR key                             │
│ EXEC → 报错，所有命令都不执行        │
└──────────────────────────────────────┘

错误类型 2：运行时错误（类型错误等）
┌──────────────────────────────────────┐
│ MULTI                                │
│ SET counter "abc"                    │
│ INCR counter        ← 运行时类型错误 │
│ GET counter                         │
│ EXEC → 返回 [OK, error, "abc"]       │
│        INCR 失败，其余命令仍执行     │
└──────────────────────────────────────┘
```

### 1.4 为什么 Redis 不支持回滚

Redis 作者 antirez 的设计观点：

| 观点 | 说明 |
|------|------|
| 错误来源 | Redis 命令错误通常是编程 bug（类型错误、参数错误），不应出现在生产环境 |
| 复杂度 | 回滚需要 undo 日志，与 Redis 简单高效的设计理念冲突 |
| 数据一致性 | 入队错误直接拒绝事务；运行错误不会污染数据（如 INCR 失败不影响其他 key） |
| 性能权衡 | 不维护回滚日志，事务开销极低 |

## 二、WATCH 乐观锁

### 2.1 WATCH 机制原理

`WATCH` 实现 CAS（Compare-And-Swap）乐观锁，监视一个或多个 key，事务执行前若被监视 key 发生变更，事务被拒绝。

```
WATCH 乐观锁流程：

客户端A                          客户端B
WATCH balance                    │
GET balance → 1000               │
MULTI                            │
SET balance 800                  │
                                 │ SET balance 500   ← 修改被 WATCH 的 key
                                 │
EXEC → nil（事务被取消）         │

实现机制：
1. WATCH 将 key 加入 watched_keys 字典，记录版本号
2. 任何写命令修改 key 时递增版本号
3. EXEC 检查所有 WATCH key 的版本号
   ├── 有变化 → 取消事务，返回 nil
   └── 无变化 → 执行事务
4. EXEC/DISCARD 自动释放 WATCH；UNWATCH 手动释放
```

### 2.2 WATCH 实战

```bash
# 监视键
WATCH balance

# 开始事务
MULTI

# 执行命令
SET balance 800

# 提交事务（如果 balance 被其他客户端修改，返回 nil）
EXEC

# 取消监视（不需要事务时手动释放）
UNWATCH
```

### 2.3 Python 乐观锁转账

```python
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def transfer(from_key: str, to_key: str, amount: int) -> bool:
    """基于 WATCH 的乐观锁转账，失败自动重试"""
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
                # 被监视 key 已变更，重试
                continue

r.set("account:A", 1000)
r.set("account:B", 500)
result = transfer("account:A", "account:B", 200)
print(f"转账结果：{result}")
print(f"A余额：{r.get('account:A')}, B余额：{r.get('account:B')}")
```

**应用场景**：商品库存扣减、账户余额变更、秒杀库存等需要防止并发冲突的场景。

## 三、Lua 脚本

### 3.1 Lua 脚本核心特性

Redis 内嵌 Lua 5.1 解释器，脚本在服务端原子执行，期间阻塞其他客户端命令。

```
Lua 脚本执行流程：
┌──────────┐    ┌──────────┐    ┌──────────┐
│ 客户端    │───►│ Redis    │───►│ Lua解释器 │
│ EVAL     │    │ 接收脚本  │    │ 原子执行  │
└──────────┘    └──────────┘    └──────────┘
                                     │
                                执行期间阻塞
                                其他客户端等待

优势：
1. 原子性：整个脚本原子执行，无需担心并发
2. 减少网络开销：多条命令一次发送，减少 RTT
3. 复用性：脚本可缓存为 SHA1，客户端只发哈希

限制：
1. 阻塞：脚本执行期间 Redis 无法处理其他请求
2. 超时：默认 5 秒（lua-time-limit），超时后可被 SCRIPT KILL 中断
3. 无外部访问：不能访问文件系统、网络
4. 随机性限制：禁用 SRANDMEMBER 等随机命令（保证主从复制一致）
```

### 3.2 EVAL 与 EVALSHA

```bash
# 基本语法：EVAL script numkeys key [key ...] arg [arg ...]
EVAL "return redis.call('SET', KEYS[1], ARGV[1])" 1 mykey myvalue

# 读取脚本
EVAL "return redis.call('GET', KEYS[1])" 1 mykey

# 脚本中区分 call 与 pcall
# redis.call：出错抛异常，脚本终止
# redis.pcall：出错返回错误对象，脚本继续
EVAL "local ok, err = pcall(redis.call, 'GET', KEYS[1]); return err" 1 missing

# 缓存脚本（返回 SHA1）
SCRIPT LOAD "return redis.call('GET', KEYS[1])"
# 返回：4e6d8fc8bb01276962cce5371fa60f0f2eb6d3a2

# 通过 SHA1 执行（减少网络传输）
EVALSHA 4e6d8fc8bb01276962cce5371fa60f0f2eb6d3a2 1 mykey

# 脚本管理
SCRIPT EXISTS 4e6d8fc8bb01276962cce5371fa60f0f2eb6d3a2 6b8a7c9d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b   # 检查脚本是否已缓存
SCRIPT FLUSH                  # 清空所有缓存的脚本
SCRIPT KILL                   # 杀死正在执行的脚本（仅未执行写命令前有效）
```

### 3.3 Lua 脚本 API 速查

| 函数/变量 | 说明 | 示例 |
|-----------|------|------|
| `redis.call(cmd, ...)` | 执行 Redis 命令，出错抛异常 | `redis.call('GET', KEYS[1])` |
| `redis.pcall(cmd, ...)` | 执行 Redis 命令，出错返回错误对象 | `local r = redis.pcall('GET', KEYS[1])` |
| `redis.status_reply(msg)` | 返回状态回复 | `return redis.status_reply('OK')` |
| `redis.error_reply(msg)` | 返回错误回复 | `return redis.error_reply('invalid')` |
| `KEYS[n]` | 脚本参数：键名（必须通过 KEYS 传入） | `KEYS[1]` |
| `ARGV[n]` | 脚本参数：附加参数 | `ARGV[1]` |
| `tonumber(x)` | 转换为数字 | `tonumber(ARGV[1])` |
| `tostring(x)` | 转换为字符串 | `tostring(now)` |

> **规范**：所有 key 必须通过 `KEYS` 传入，便于 Cluster 路由与 ACL 审计；常量参数通过 `ARGV` 传入。

### 3.4 限流器 Lua 脚本（滑动窗口）

滑动窗口限流是 Lua 脚本的经典场景，结合 ZSet 实现毫秒级精度：

```bash
# 滑动窗口限流脚本
# KEYS[1] = 限流 key（如 rate_limit:api:user:1001）
# ARGV[1] = 窗口大小（毫秒，如 60000）
# ARGV[2] = 最大请求数（如 100）
# ARGV[3] = 当前时间戳（毫秒）
# ARGV[4] = 唯一标识（用于 ZSet member）

EVAL "
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local member = ARGV[4]

-- 1. 清理窗口外的过期记录
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)

-- 2. 统计当前窗口内请求数
local count = redis.call('ZCARD', key)

-- 3. 未超限则记录请求
if count < limit then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, window / 1000)
    return 1
end
return 0
" 1 rate_limit:api:user:1001 60000 100 1700000000000:1
```

完整 Python 封装：

```python
import redis
import time
import uuid

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count < limit then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, window / 1000)
    return 1
end
return 0
"""

class SlidingWindowRateLimiter:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.sha = self.redis.script_load(SLIDING_WINDOW_SCRIPT)

    def allow(self, key: str, window_ms: int, limit: int) -> bool:
        now = int(time.time() * 1000)
        member = f"{now}:{uuid.uuid4().hex[:8]}"
        result = self.redis.evalsha(self.sha, 1, key, str(window_ms), str(limit), str(now), member)
        return result == 1

limiter = SlidingWindowRateLimiter(r)
for i in range(15):
    allowed = limiter.allow("api:user:1001", 60000, 10)
    print(f"请求{i+1}: {'允许' if allowed else '拒绝'}")
```

## 四、Redis 7 Functions

### 4.1 Functions vs EVAL

Redis 7.0 引入 Functions，替代传统 Lua 脚本作为可管理的服务端函数库。

| 维度 | EVAL/EVALSHA | Functions |
|------|--------------|-----------|
| 持久化 | 默认不持久化，重启需重新 LOAD | 可持久化（`FUNCTION LOAD` 默认持久化到 RDB/AOF） |
| 命名管理 | 仅靠 SHA1 标识 | 函数库 + 函数名，语义清晰 |
| 复用性 | 客户端各自缓存 SHA | 服务端统一注册，所有客户端共享 |
| 列出/检索 | 仅 `SCRIPT EXISTS` | `FUNCTION LIST`、`FUNCTION DUMP` |
| 主从复制 | 复制脚本执行结果 | 复制函数库定义，从库可直接调用 |
| 适用场景 | 临时性原子操作 | 长期维护的业务脚本（限流、扣库存等） |

### 4.2 Functions 完整实战

```bash
# 1. 加载函数库（#!lua name=<libname> 是 shebang，必须首行）
FUNCTION LOAD "#!lua name=mylib
redis.register_function('hello', function(keys, args)
    return redis.call('GET', keys[1])
end)

redis.register_function('setnx_ex', function(keys, args)
    return redis.call('SET', keys[1], args[1], 'NX', 'EX', args[2])
end)
"

# 2. 调用函数：FCALL 函数名 numkeys key [key ...] arg [arg ...]
FCALL hello 1 mykey
FCALL setnx_ex 1 lock:order:1 uuid-12345 10

# 3. 查看所有函数库
FUNCTION LIST
FUNCTION LIST WITHCODE

# 4. 查看指定库
FUNCTION LIST LIBRARYNAME mylib

# 5. 删除函数库
FUNCTION DELETE mylib

# 6. 导出与导入（用于备份/迁移）
FUNCTION DUMP > /tmp/redis_functions.rdb
FUNCTION RESTORE /tmp/redis_functions.rdb FLUSH

# 7. 异步刷盘函数库（与 RDB/AOF 协同）
FUNCTION FLUSH
```

### 4.3 限流器 Functions 实现

```bash
# 加载限流器函数库
FUNCTION LOAD "#!lua name=ratelimit
redis.register_function('sliding_window', function(keys, args)
    local key = keys[1]
    local window = tonumber(args[1])
    local limit = tonumber(args[2])
    local now = tonumber(args[3])
    local member = args[4]

    redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
    local count = redis.call('ZCARD', key)
    if count < limit then
        redis.call('ZADD', key, now, member)
        redis.call('EXPIRE', key, window / 1000)
        return 1
    end
    return 0
end)
"

# 调用：参数通过 ARGV 传入，注意 Functions 中用 args 而非 ARGV
FCALL sliding_window 1 rate_limit:api:user:1001 60000 100 1700000000000:req1
```

> **注意**：Functions 内部访问参数用 `keys[n]` 和 `args[n]`（小写），EVAL 中用 `KEYS[n]` 和 `ARGV[n]`（大写）。

## 五、Pipeline

### 5.1 Pipeline 原理

Pipeline 不是原子操作，而是把多条命令打包一次发送，减少网络往返（RTT）。

```
普通模式（每条命令一个 RTT）：
客户端 ──SET k1──► Redis        客户端 ◄──OK── Redis
客户端 ──SET k2──► Redis        客户端 ◄──OK── Redis
客户端 ──GET k1──► Redis        客户端 ◄──v1── Redis
3 次 RTT

Pipeline 模式（一次发送，一次接收）：
客户端 ──SET k1, SET k2, GET k1──► Redis
客户端 ◄──OK, OK, v1── Redis
1 次 RTT
```

### 5.2 Pipeline 实战

```bash
# 通过 redis-cli 管道模式批量导入
cat commands.txt | redis-cli --pipe

# commands.txt 内容示例
# SET key1 value1
# SET key2 value2
# INCR counter
# HSET user:1 name "张三"
```

```python
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

# Pipeline 批量写入
def batch_set(data: dict[str, str]) -> None:
    with r.pipeline() as pipe:
        for key, value in data.items():
            pipe.set(key, value)
        pipe.execute()

# Pipeline 批量读取
def batch_get(keys: list[str]) -> list[str | None]:
    with r.pipeline() as pipe:
        for key in keys:
            pipe.get(key)
        return pipe.execute()

# 事务 + Pipeline（pipe.multi() 后即事务模式）
def transactional_transfer(from_key: str, to_key: str, amount: int):
    with r.pipeline() as pipe:
        pipe.multi()  # 开启事务
        pipe.decrby(from_key, amount)
        pipe.incrby(to_key, amount)
        return pipe.execute()

data = {f"user:{i}:name": f"用户{i}" for i in range(1000)}
batch_set(data)
print(batch_get([f"user:{i}:name" for i in range(5)]))
```

### 5.3 Pipeline 使用建议

| 场景 | 建议 |
|------|------|
| 批量读写 | 单次 Pipeline 控制在 500-1000 条命令，避免阻塞其他客户端 |
| 大数据导入 | 使用 `redis-cli --pipe` 比 Python Pipeline 快 5-10 倍 |
| Cluster 环境 | 客户端需按 key 槽位分片，分别 Pipeline 到不同节点 |
| 原子性需求 | Pipeline 不保证原子性，需要时改用 MULTI 或 Lua |

## 六、对比与选型

### 6.1 事务 vs Lua vs Pipeline

| 特性 | 事务（MULTI） | Lua 脚本 | Pipeline |
|------|--------------|---------|----------|
| 原子性 | 命令间不被插入 | **完全原子** | 无原子性 |
| 逻辑控制 | 不支持条件分支 | 完整 Lua 语法 | 无 |
| 网络开销 | 多次 RTT（可结合 Pipeline） | 1 次 RTT | **1 次 RTT** |
| 错误处理 | 不支持回滚 | 完整错误处理 | 各自返回 |
| 持久化 | 不持久化命令本身 | EVAL 不持久化；Functions 持久化 | 不持久化 |
| Cluster 兼容 | key 必须同槽 | key 必须同槽 | 按节点分片 |
| 阻塞风险 | 低 | 高（脚本过长会阻塞） | 低 |
| 适用场景 | 简单批量 + 乐观锁 | 复杂原子操作 | 批量命令减少 RTT |

### 6.2 Redis vs MySQL 事务对比

| 维度 | Redis 事务 | MySQL InnoDB 事务 |
|------|------------|-------------------|
| 原子性 | 部分支持（不支持回滚） | 完全支持（基于 undo log） |
| 一致性 | 满足 | 满足（约束、外键、触发器） |
| 隔离性 | 完全隔离（单线程） | 4 级隔离（RU/RC/RR/Serializable） |
| 持久性 | 取决于持久化配置 | 默认满足（redo log + binlog） |
| 回滚机制 | 无 | `ROLLBACK` 回滚整个事务 |
| 隔离级别 | 无（隐式串行化） | RR（默认） / RC / RU / Serializable |
| 锁粒度 | 无锁（单线程） | 行锁、间隙锁、表锁 |
| MVCC | 无 | 支持（一致性视图） |
| 死锁 | 不可能 | 可能（需检测与回滚） |
| 分布式事务 | 不原生支持 | XA 事务 |
| 典型场景 | 缓存原子操作、库存扣减 | 金融转账、订单事务 |

### 6.3 选型决策树

```
需要保证多条命令原子性？
├── 否 → Pipeline（仅减少 RTT）
└── 是
    ├── 需要条件分支/复杂逻辑？
    │   ├── 是 → Lua 脚本 / Functions
    │   └── 否
    │       ├── 需要乐观锁防并发修改？
    │       │   └── 是 → MULTI + WATCH
    │       └── 否 → MULTI + EXEC
    └── 脚本需要长期维护与版本管理？
        ├── 是 → Redis 7 Functions
        └── 否 → EVAL / EVALSHA
```

## 小思考题帮你巩固

1. **Redis 事务里某条命令运行时报错，其他命令还会执行吗？入队阶段语法错误呢？两者结果为何不同？**（提示：入队错误→整个事务拒绝；运行错误→不影响其他条，见一 1.3。）
2. **MULTI/EXEC 为什么"能隔离但不是原子回滚"？要实现"要么全成要么全不"该用什么？**（提示：EXEC 前入队失败不执行=全不算；EXEC 中运行错误不回滚；要强原子用 Lua/Functions，见一 1.2/三。）
3. **WATCH 的一次失败后，客户端该怎么做？为什么建议 while 重试？**（提示：EXEC 返回 nil=被改过；重新 WATCH+读+MULTI+EXEC，见二 2.3。）
4. **同样是"多命令一次发"，Pipeline 和事务有什么本质区别？**（提示：Pipeline 只省网往返、命令可被打断执行；事务强制排队不被插入，见 5.1/6.1。）
5. **长期复用的业务脚本为什么该用 Functions 而不是 EVAL？**（提示：EVAL 重启丢缓存、靠 SHA 标识；Functions 持久化、有名字、从库按定义复制，见四 4.1。）

<details>
<summary>参考答案（点击展开）</summary>

1. 运行时报错不影响其他命令（返回结果数组里带 error，其余照常执行）；入队（语法）错误会让整个事务在 EXEC 时报错、**全部不执行**。区别在"错误发生在哪一阶段"：入队=提交前发现，直接整体拒绝；运行时=执行中才发现，Redis 不为此回滚。
2. 因为 EXEC 只是"把已排队的命令串行执行一遍"，没有 undo 日志；只有一个命令整体的原子性（单线程）。若"要么全成要么全不"，用 Lua 脚本在一个脚本里先校验、出错直接 `error()` 中断——脚本执行中抛错会导致已执行的写也保留吗？**不**：Lua 用 `redis.call` 出错会终止脚本并丢弃前序写（原子性由脚本保证）——所以强原子用脚本而不是 MULTI。
3. WATCH 失效后 EXEC 返回 nil，客户端清掉旧结果、重新 `WATCH → 读 → MULTI → EXEC` 重试；用 `while True` 实现（务必带最大重试/超时防死循环），这就是"乐观锁+自旋妥协"。
4. Pipeline 是把多命令打包一次发送（省 RTT），服务器**按到达顺序逐个执行**、中间可被其他客户端命令插入；事务则是"排队期间命令不执行、EXEC 后串行执行不被插入"。Pipeline 无隔离/无原子，事务有隔离。
5. EVAL 的脚本不持久化、重启即失 SHA、靠散列无版本管理；Functions 随 RDB/AOF 持久化、有库名/函数名语义、`FUNCTION LIST/DUMP` 可管理、主从按函数定义复制调用结果——长期业务逻辑（限流/扣库存）的生产标准选择。

</details>

## 七、生产实战注意事项

### 7.1 Lua 脚本性能红线

| 红线 | 后果 | 解决方案 |
|------|------|----------|
| 脚本执行超过 `lua-time-limit`（默认 5s） | 阻塞主线程，所有客户端等待 | 拆分脚本，避免循环大量 key |
| 脚本中调用 `KEYS *` 或 `SMEMBERS` 大 key | 阻塞 + 内存暴涨 | 用 `SCAN` 替代，但需注意 Lua 中 SCAN 会复制结果 |
| 脚本中执行写命令后被 `SCRIPT KILL` | 无效，必须 `SHUTDOWN NOSAVE` | 写命令放在脚本末尾，先算后写 |
| 随机命令（`SRANDMEMBER`、`RANDOMKEY`） | 主从复制结果不一致 | 用 `redis.replicate_commands()` 显式声明（5.0+ 默认开启） |

### 7.2 Cluster 下的事务限制

```bash
# 集群中事务的 key 必须在同一个槽
# 使用哈希标签 {} 强制同槽
MULTI
SET {user}.1001:name "张三"
SET {user}.1001:age 25
HINCRBY {user}.1001:stats login 1
EXEC

# 查看槽位
CLUSTER KEYSLOT {user}.1001:name
CLUSTER KEYSLOT {user}.1001:age
# 两者相同
```

### 7.3 Lua 脚本调试

```bash
# 进入脚本调试模式
SCRIPT DEBUG yes
EVAL "return redis.call('GET', KEYS[1])" 1 mykey

# 调试命令（在调试会话中）
# step / s      单步执行
# next / n      步过
# continue / c  继续执行
# list / l      查看当前行
# print / p var 打印变量
# break <line>  打断点

# 退出调试
SCRIPT DEBUG no
```

## 📖 深入阅读

| 主题 | 文档 | 关联点 |
|------|------|--------|
| 单线程模型与命令执行 | [01 Redis 架构与核心概念](../01_Redis架构与核心概念/01_Redis架构与核心概念.md) | 理解事务隔离性的根基 |
| Stream 消费者组 | [08 发布订阅与消息队列](../08_发布订阅与消息队列/08_发布订阅与消息队列.md) | Stream 的 XREADGROUP 与 XACK 也需配合 Lua 保证消费原子性 |
| 分布式锁释放 | [10 分布式锁与限流](../10_分布式锁与限流/10_分布式锁与限流.md) | 锁释放必须用 Lua 脚本保证"判断+删除"原子 |
| Pipeline 性能 | [11 性调优与监控](../11_性能调优与监控/11_性能调优与监控.md) | Pipeline 批量大小调优 |
| ACL 与脚本权限 | [12 安全与运维](../12_安全与运维/12_安全与运维.md) | 限制用户执行 EVAL/FCALL |
| Django 集成 | [13 Node.js 与 Django 集成 Redis](../13_Node.js与Django集成Redis/13_Node.js与Django集成Redis.md) | django-redis 中执行 Lua 脚本的方式 |
| 全栈秒杀实战 | [15 全栈项目实战](../15_全栈项目实战/15_全栈项目实战.md) | 秒杀扣库存的 Lua 完整实现 |

## 本章小结

| 知识点 | 核心结论 | 实战要点 |
|--------|---------|---------|
| 事务 ACID | 部分原子性、完全隔离性、配置依赖持久性 | 不要依赖回滚，写入前校验类型 |
| WATCH 乐观锁 | CAS 机制，失败需重试 | 配合 `while True` 循环重试，避免死循环加超时 |
| Lua 脚本 | 完全原子，适合复杂业务 | 控制执行时间 < 50ms，避免阻塞 |
| EVALSHA | 减少网络传输 | 客户端启动时 SCRIPT LOAD，运行期用 EVALSHA |
| Functions | 可持久化的函数库 | Redis 7+ 优先用于长期维护的业务脚本 |
| Pipeline | 仅减少 RTT，无原子性 | 单批 500-1000 条，Cluster 需按槽分片 |
| 选型 | 复杂原子 → Lua/Functions；简单批量 → Pipeline；并发冲突 → WATCH | 三者可组合使用 |

## 下一步导航

- 下一章：[08 发布订阅与消息队列](../08_发布订阅与消息队列/08_发布订阅与消息队列.md) — 学习 Stream 消费者组的可靠消息投递
- 进阶实战：[10 分布式锁与限流](../10_分布式锁与限流/10_分布式锁与限流.md) — 基于本章 Lua 脚本实现 Redlock
- 项目落地：[15 全栈项目实战](../15_全栈项目实战/15_全栈项目实战.md) — 秒杀场景下的事务+Lua 完整方案
- 性能调优：[11 性能调优与监控](../11_性能调优与监控/11_性能调优与监控.md) — Pipeline 与慢查询调优
