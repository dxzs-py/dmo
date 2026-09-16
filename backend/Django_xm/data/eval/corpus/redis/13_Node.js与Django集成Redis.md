# 13 Node.js 与 Django 集成 Redis

> 在 Node.js（ioredis）与 Django（django-redis / DRF）两侧分别封装 Redis 服务层，统一连接池、缓存、会话、限流、分布式锁与 Celery 队列的最佳实践。

## 零基础前置认知

**这篇在讲什么**：把 Redis 接进你的应用，本质是**选一个客户端 + 做一层服务层封装 + 接上框架能力**。Node.js 侧选 **ioredis**（成熟主推：连接池/集群/哨兵/Lua 全支持）或 node-redis v4+（官方原生，promise API）；Django 侧用 **django-redis**（把 cache/session/Celery broker 一键换成 Redis 后端）。读完你能：在 Node.js 封装"缓存/会话/限流/分布式锁"四件套服务层，在 Django 配好 `CACHES/SESSION/CELERY` 三处 Redis 配置并用信号驱动缓存失效。*本项目以最新稳定客户端为准（ioredis 5 / node-redis v4 / django-redis / Django 5.2），Node 运行环境按用户规范 nvm v20.19.5。*

> 辅助类比：把 Redis 客户端接入想成"连水电上门"——**选客户端** 是"选哪家水电公司"（ioredis=老牌全能、node-redis=官方直供）；**连接池** 是"小区稳压器"（复用 N 个固定连接，别每次新开）；**服务层封装** 是"统一的水电管家"（业务只调用 `cache.get/set`，不用关心 Redis 细节）；**Django 集成的三种姿势** 是"不同房间的接口"——cache（普通缓存）、session（登录会话）、Celery broker（异步任务队列），同一根 Redis 各用不同 db 即可。

**基础名词集**：

| 名词 | 一句话定义 | 与相邻概念的关系 |
|------|-----------|----------------|
| ioredis | Node.js 最流行的 Redis 客户端（v5） | 支持 cluster/sentinel/lua/连接池；API 丰富 |
| node-redis | Redis 官方 Node 客户端（v4+，全 promise） | 与 ioredis 二选一；API 更官方、升级更快 |
| 连接池 | 复用固定数量的连接，避免每次建连开销 | ioredis 内置（`maxRetriesPerRequest` 等）；Django 用 `CONNECTION_POOL_KWARGS` |
| django-redis | Django cache/session 的 Redis 后端 | `CACHES` 配置即用；支持压缩/序列化/连接池 |
| Celery broker | Celery 任务队列的中间件（Redis 作 broker+backend） | `CELERY_BROKER_URL/RESULT_BACKEND` 指向 Redis db1/db2 |
| 缓存失效（信号驱动） | Django 用 post_save 信号删除缓存 | 与"写 DB 后删缓存"（Cache-Aside）同一套路 |
| db 隔离 | 同一 Redis 用不同 db（0 缓存/1 session/2 broker） | 逻辑隔离；Cluster 除外（仅 db0） |
| API 对照 | ioredis `client.get` / node-redis `client.get` 语义差异 | 都返回 Promise；node-redis v4 用 `createClient()` |

> 区分/注意：**ioredis 与 node-redis 别混用在同一项目**——它们的 API/连接方式不同（ioredis 常驻单例、node-redis 需 `client.connect()` 后显式管理生命周期），混用会双连接、易混淆；选一个并统一封装。**Node 侧 Redis 连接默认不上 TLS/密码**：生产必须显式配（`password`/`tls`），否则连不上或裸奔。**Django 的 cache 与 session 可共用一 Redis 但要分 db 与前缀**：`KEY_PREFIX`/`VERSION` 避免键冲突；Cluster 环境下仅 db0 可用，需注意。

## 学习目标

1. 能够对比 ioredis 与 node-redis 的能力差异，按场景选型并完成封装。
2. 能够编写 Django 5.2 生产级 `settings.py`，包含 cache、session、Celery broker 与连接池。
3. 能够实现 Django 信号驱动的缓存失效、模板缓存与 `cache_page` 装饰器。
4. 能够在 Node.js 侧封装缓存、会话、限流、分布式锁四类服务。
5. 能够对比 Redis 与 MySQL 的驱动集成方式与一致性策略。

## 前置知识

- 数据类型与命令：[02 数据类型与底层实现](../02_数据类型与底层实现/02_数据类型与底层实现.md)
- 事务与 Lua 脚本：[07 事务与 Lua 脚本](../07_事务与Lua脚本/07_事务与Lua脚本.md)
- 分布式锁与限流：[10 分布式锁与限流](../10_分布式锁与限流/10_分布式锁与限流.md)
- 缓存模式与一致性：[09 缓存模式与实战](../09_缓存模式与实战/09_缓存模式与实战.md)
- 复制与高可用：[05 复制与高可用](../05_复制与高可用/05_复制与高可用.md)

## 一、客户端选型总览

Redis 客户端在不同语言生态下差异较大。Node.js 侧主流为 `ioredis`（社区）与 `node-redis`（官方），Django 侧则通过 `django-redis` 或 Django 5.2 内置的 `RedisCache` 后端接入。

```
应用层集成全景：

┌─────────────────────────────┐       ┌─────────────────────────────┐
│        Node.js 服务          │       │        Django 服务          │
│  ┌────────────────────┐    │       │  ┌────────────────────┐    │
│  │  ioredis 封装层     │    │       │  │ django-redis / DRF │    │
│  │  - cache            │    │       │  │ - cache_page       │    │
│  │  - session          │    │       │  │ - session           │    │
│  │  - rate limit       │    │       │  │ - signal + cache   │    │
│  │  - distributed lock │    │       │  │ - Celery broker    │    │
│  └─────────┬──────────┘    │       │  └─────────┬──────────┘    │
└────────────┼───────────────┘       └────────────┼───────────────┘
             │                                     │
             └──────────────┬──────────────────────┘
                            ▼
                  ┌──────────────────┐
                  │   Redis 7.x      │
                  │  (Standalone /   │
                  │   Sentinel /     │
                  │   Cluster)       │
                  └──────────────────┘
```

### 1.1 ioredis vs node-redis 对比

| 维度 | ioredis | node-redis |
|------|---------|------------|
| 维护方 | 社区（Luin 维护，广泛使用） | Redis 官方 |
| Cluster 支持 | 原生 `Cluster` 类，自动 MOVED/ASK | 4.0+ 提供 cluster 模块 |
| Sentinel 支持 | 内置 `sentinels(x)` 连接方式 | 不直接支持，需自实现 |
| Pipeline | 原生 `pipeline()`、`multi()` | `multi()` / `exec()` |
| 事务 | `multi().exec()` 返回结果数组 | `multi()` 链式 |
| Lua 脚本 | `evalsha`、`defineCommand` | `eval`、`EVALSHA` |
| Promise | 全 API 原生 Promise | Promise + callback 兼容 |
| 自动重连 | 内置，可配置重试策略 | 内置 |
| Key 命名空间 | 支持 `keyPrefix` | 支持 `keyPrefix`（4.1+） |
| RESP3 | 实验支持 | 4.x 默认 |
| 适用场景 | 复杂业务、Cluster/Sentinel、生产推荐 | 官方背书、轻量场景 |

> 结论：生产环境推荐 ioredis，复杂业务（Cluster/Sentinel/Pipeline/Lua）支持更完善；若仅做简单 KV 且偏好官方库，可使用 node-redis。

## 二、Node.js ioredis 完整封装

### 2.1 环境准备

```powershell
nvm use v20.19.5
npm install ioredis uuid
```

### 2.2 连接与服务层封装

`src/services/redis.js`：

```javascript
// src/services/redis.js
const Redis = require('ioredis');
const { randomUUID } = require('crypto');

// 单机 / Sentinel / Cluster 三种模式统一工厂
function createRedisClient(options = {}) {
  const {
    mode = 'standalone', // standalone | sentinel | cluster
    host = process.env.REDIS_HOST || '127.0.0.1',
    port = Number(process.env.REDIS_PORT || 6379),
    password = process.env.REDIS_PASSWORD,
    db = Number(process.env.REDIS_DB || 0),
    sentinels = [],
    sentinelName = 'mymaster',
    clusterNodes = [],
    maxConnections = 50,
  } = options;

  const common = {
    password,
    db,
    keyPrefix: options.keyPrefix || '',
    enableReadyCheck: true,
    retryStrategy: (times) => Math.min(times * 200, 2000),
    maxRetriesPerRequest: 3,
    reconnectOnError: (err) => {
      const readOnlyErrors = ['READONLY', 'NOREPLICAS'];
      return readOnlyErrors.some((c) => err.message.includes(c));
    },
    // 连接池（ioredis 内部维护，per-instance 一个连接）
    connectionName: `app:${process.pid}`,
    lazyConnect: false,
  };

  if (mode === 'cluster') {
    return new Redis.Cluster(clusterNodes, {
      redisOptions: common,
      scaleReads: 'slave',
      maxRedirections: 16,
      clusterRetryStrategy: common.retryStrategy,
    });
  }

  if (mode === 'sentinel') {
    return new Redis({
      ...common,
      sentinels,
      name: sentinelName,
      role: 'master',
      sentinelPassword: password,
      preferredSlaves: 'random',
    });
  }

  return new Redis({ ...common, host, port });
}

const client = createRedisClient({
  mode: process.env.REDIS_MODE || 'standalone',
  sentinels: [
    { host: '127.0.0.1', port: 26379 },
    { host: '127.0.0.1', port: 26380 },
    { host: '127.0.0.1', port: 26381 },
  ],
  clusterNodes: [
    { host: '127.0.0.1', port: 7001 },
    { host: '127.0.0.1', port: 7002 },
    { host: '127.0.0.1', port: 7003 },
  ],
  keyPrefix: 'app:',
});

client.on('ready', () => console.log('[redis] ready'));
client.on('error', (err) => console.error('[redis] error', err.message));
client.on('reconnecting', (delay) => console.warn(`[redis] reconnect in ${delay}ms`));

module.exports = { client, createRedisClient, randomUUID };
```

### 2.3 缓存服务

`src/services/cache.js`：

```javascript
// src/services/cache.js
const { client } = require('./redis');

class CacheService {
  /**
   * 读取缓存，未命中调用 loader 并回写
   * @param {string} key
   * @param {number} ttlSeconds
   * @param {() => Promise<any>} loader
   * @param {(v: any) => string} [encode]
   * @param {(v: string) => any} [decode]
   */
  static async getOrSet(key, ttlSeconds, loader, encode = JSON.stringify, decode = JSON.parse) {
    const cached = await client.get(key);
    if (cached !== null) {
      try {
        return decode(cached);
      } catch {
        await client.del(key);
      }
    }
    const fresh = await loader();
    // 防止缓存雪崩：在 TTL 上叠加 10% 随机抖动
    const jitter = Math.floor(ttlSeconds * 0.1 * Math.random());
    await client.set(key, encode(fresh), 'EX', ttlSeconds + jitter);
    return fresh;
  }

  static async set(key, value, ttlSeconds, encode = JSON.stringify) {
    return client.set(key, encode(value), 'EX', ttlSeconds);
  }

  static async get(key, decode = JSON.parse) {
    const v = await client.get(key);
    return v === null ? null : decode(v);
  }

  static async del(key) {
    return client.del(key);
  }

  /**
   * 按前缀批量失效（小批量场景）
   */
  static async invalidatePattern(pattern) {
    const keys = [];
    let cursor = '0';
    do {
      const [next, batch] = await client.scan(
        cursor, 'MATCH', pattern, 'COUNT', 200
      );
      cursor = next;
      keys.push(...batch);
    } while (cursor !== '0');
    if (keys.length === 0) return 0;
    return client.unlink(...keys);
  }

  /**
   * Pipeline 批量预热
   */
  static async msetBulk(pairs, ttlSeconds) {
    const pipeline = client.pipeline();
    for (const [k, v] of pairs) {
      pipeline.set(k, JSON.stringify(v), 'EX', ttlSeconds);
    }
    return pipeline.exec();
  }
}

module.exports = { CacheService };
```

### 2.4 会话服务

`src/services/session.js`：

```javascript
// src/services/session.js
const { client } = require('./redis');
const { randomUUID } = require('crypto');

class SessionService {
  constructor(prefix = 'sess:', ttl = 7 * 24 * 3600) {
    this.prefix = prefix;
    this.ttl = ttl;
  }

  async create(userId, meta = {}) {
    const sid = randomUUID();
    const data = { sid, userId, ...meta, createdAt: Date.now() };
    await client.set(this.prefix + sid, JSON.stringify(data), 'EX', this.ttl);
    return sid;
  }

  async get(sid) {
    const raw = await client.get(this.prefix + sid);
    if (!raw) return null;
    // 滑动续期：访问即续期
    await client.expire(this.prefix + sid, this.ttl);
    return JSON.parse(raw);
  }

  async destroy(sid) {
    return client.del(this.prefix + sid);
  }

  /**
   * 双端登录控制：限制同一账号最多 N 个有效会话
   */
  async addUserSession(userId, sid, maxSessions = 3) {
    const listKey = `sess:list:${userId}`;
    await client.lpush(listKey, sid);
    await client.ltrim(listKey, 0, maxSessions - 1);
    const all = await client.lrange(listKey, 0, -1);
    if (all.length > maxSessions) {
      // 淘汰多余的会话
      const expired = all.slice(maxSessions);
      const pipeline = client.pipeline();
      for (const s of expired) {
        pipeline.del(this.prefix + s);
      }
      await pipeline.exec();
    }
    await client.expire(listKey, this.ttl);
  }

  async listUserSessions(userId) {
    return client.lrange(`sess:list:${userId}`, 0, -1);
  }
}

module.exports = { SessionService };
```

### 2.5 限流服务（滑动窗口 + 令牌桶 Lua）

`src/services/rateLimit.js`：

```javascript
// src/services/rateLimit.js
const { client } = require('./redis');

const SLIDING_WINDOW_LUA = `
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, member)
  redis.call('PEXPIRE', key, window)
  return 1
end
return 0
`;

const TOKEN_BUCKET_LUA = `
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])  -- tokens per ms
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local info = redis.call('HMGET', key, 'tokens', 'last_time')
local tokens = tonumber(info[1]) or capacity
local last_time = tonumber(info[2]) or now

local elapsed = math.max(0, now - last_time)
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end
redis.call('HSET', key, 'tokens', tokens, 'last_time', now)
redis.call('PEXPIRE', key, math.ceil(capacity / rate) + 10000)
return allowed
`;

// 缓存脚本 SHA，减少传输
let slidingSha = null;
let tokenSha = null;

async function loadScripts() {
  if (!slidingSha) slidingSha = await client.script('LOAD', SLIDING_WINDOW_LUA);
  if (!tokenSha) tokenSha = await client.script('LOAD', TOKEN_BUCKET_LUA);
}

class RateLimiter {
  /**
   * 滑动窗口限流
   * @param {string} key 如 rate:api:user:1001
   * @param {number} windowMs 时间窗（毫秒）
   * @param {number} limit 窗口内最大请求数
   */
  static async slidingWindow(key, windowMs, limit) {
    await loadScripts();
    const now = Date.now();
    const member = `${now}:${Math.random().toString(36).slice(2, 8)}`;
    const res = await client.evalsha(slidingSha, 1, key, windowMs, limit, now, member);
    return res === 1;
  }

  /**
   * 令牌桶限流（允许突发）
   * @param {string} key
   * @param {number} capacity 桶容量
   * @param {number} ratePerSec 每秒令牌生成速率
   * @param {number} requested 本次请求令牌数
   */
  static async tokenBucket(key, capacity, ratePerSec, requested = 1) {
    await loadScripts();
    const now = Date.now();
    const res = await client.evalsha(
      tokenSha, 1, key, capacity, ratePerSec / 1000, now, requested
    );
    return res === 1;
  }
}

module.exports = { RateLimiter };
```

### 2.6 分布式锁（SET NX EX + Lua 释放 + 看门狗）

`src/services/distributedLock.js`：

```javascript
// src/services/distributedLock.js
const { client } = require('./redis');
const { randomUUID } = require('crypto');

const UNLOCK_LUA = `
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
`;

const RENEW_LUA = `
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
`;

class DistributedLock {
  /**
   * @param {string} key 锁名
   * @param {object} opts
   * @param {number} opts.ttl 锁过期秒数，默认 30
   * @param {number} opts.retryTimes 重试次数，默认 3
   * @param {number} opts.retryDelay 重试间隔毫秒，默认 100
   * @param {boolean} opts.watchdog 是否启动看门狗续期，默认 true
   */
  constructor(key, opts = {}) {
    this.key = `lock:${key}`;
    this.ttl = opts.ttl || 30;
    this.retryTimes = opts.retryTimes ?? 3;
    this.retryDelay = opts.retryDelay ?? 100;
    this.watchdog = opts.watchdog ?? true;
    this.identifier = randomUUID();
    this._timer = null;
  }

  async acquire() {
    for (let i = 0; i < this.retryTimes; i++) {
      const ok = await client.set(this.key, this.identifier, 'EX', this.ttl, 'NX');
      if (ok === 'OK') {
        if (this.watchdog) this._startRenewal();
        return true;
      }
      await new Promise((r) => setTimeout(r, this.retryDelay));
    }
    return false;
  }

  async release() {
    this._stopRenewal();
    const res = await client.eval(UNLOCK_LUA, 1, this.key, this.identifier);
    return res === 1;
  }

  _startRenewal() {
    const interval = (this.ttl * 1000) / 3;
    this._timer = setInterval(async () => {
      try {
        await client.eval(RENEW_LUA, 1, this.key, this.identifier, this.ttl);
      } catch (e) {
        console.error('[lock] renew failed', e.message);
      }
    }, interval);
    this._timer.unref?.();
  }

  _stopRenewal() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  /**
   * withLock 语法糖
   */
  static async run(key, fn, opts = {}) {
    const lock = new DistributedLock(key, opts);
    const got = await lock.acquire();
    if (!got) throw new Error(`acquire lock ${key} failed`);
    try {
      return await fn();
    } finally {
      await lock.release();
    }
  }
}

module.exports = { DistributedLock };
```

### 2.7 Pipeline / 事务 / Lua 示例

```javascript
// examples/usage.js
const { client } = require('./services/redis');
const { CacheService } = require('./services/cache');
const { RateLimiter } = require('./services/rateLimit');
const { DistributedLock } = require('./services/distributedLock');

async function pipelineExample() {
  // 批量写入，减少 RTT
  const pipeline = client.pipeline();
  for (let i = 0; i < 100; i++) {
    pipeline.set(`k:${i}`, i, 'EX', 60);
  }
  const results = await pipeline.exec();
  console.log('pipeline done', results.length);
}

async function transactionExample() {
  // 事务（MULTI/EXEC）
  const multi = client.multi();
  multi.set('acct:A', 1000);
  multi.set('acct:B', 500);
  multi.decrby('acct:A', 200);
  multi.incrby('acct:B', 200);
  const txResults = await multi.exec();
  console.log('transaction results', txResults);
}

async function luaExample() {
  // 限流 Lua（INCR + EXPIRE 原子）
  const sha = await client.script(
    'LOAD',
    `local c = redis.call('INCR', KEYS[1])
     if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
     if c > tonumber(ARGV[2]) then return 0 end
     return 1`
  );
  const allowed = await client.evalsha(sha, 1, 'rate:login:user:1001', 60, 5);
  console.log('login allowed', allowed === 1);
}

async function main() {
  await pipelineExample();
  await transactionExample();
  await luaExample();

  // 综合用法
  await CacheService.setOrSet('product:1', 300, async () => ({ id: 1, name: 'iPhone' }));
  const allowed = await RateLimiter.slidingWindow('rate:api:user:1', 60000, 10);
  const result = await DistributedLock.run('order:1001', async () => {
    return 'critical section';
  }, { ttl: 10 });
  console.log('lock result', result, 'rate allowed', allowed);
}

main().catch(console.error);
```

## 三、Django 集成 Redis

### 3.1 依赖安装

```powershell
conda activate langchain_xm
pip install "Django>=5.2" djangorestframework django-redis "redis>=5.0" celery django-celery-beat django-celery-results
```

### 3.2 完整 settings.py 配置

`config/settings/base.py`：

```python
# config/settings/base.py
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB_CACHE = int(os.getenv("REDIS_DB_CACHE", "0"))
REDIS_DB_SESSION = int(os.getenv("REDIS_DB_SESSION", "1"))
REDIS_DB_BROKER = int(os.getenv("REDIS_DB_BROKER", "2"))
REDIS_DB_RESULT = int(os.getenv("REDIS_DB_RESULT", "3"))

_redis_base = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}" if REDIS_PASSWORD \
    else f"redis://{REDIS_HOST}:{REDIS_PORT}"

# 1. 缓存：Django 5.2 内置 RedisCache 后端
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": [
            f"{_redis_base}/{REDIS_DB_CACHE}",
        ],
        "OPTIONS": {
            "db": REDIS_DB_CACHE,
            "parser_class": "redis.connection.HiredisParser",
            "pool": {
                "max_connections": 50,
                "timeout": 5,
            },
        },
        "KEY_PREFIX": "myapp",
        "TIMEOUT": 300,         # 默认 5 分钟
        "VERSION": 1,
    },
    "session": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": [f"{_redis_base}/{REDIS_DB_SESSION}"],
        "KEY_PREFIX": "session",
        "TIMEOUT": 7 * 24 * 3600,
    },
}

# 2. 会话：基于 Redis 缓存后端
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "session"
SESSION_COOKIE_AGE = 7 * 24 * 3600
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG  # 生产启用 HTTPS

# 3. Celery broker / backend
CELERY_BROKER_URL = f"{_redis_base}/{REDIS_DB_BROKER}"
CELERY_RESULT_BACKEND = f"{_redis_base}/{REDIS_DB_RESULT}"
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": 3600,      # 任务最长执行时间
    "fanout_prefix": True,
    "fanout_patterns": True,
    "master_name": "mymaster",        # Sentinel 时启用
}
CELERY_BROKER_POOL_LIMIT = 10
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "Asia/Shanghai"
CELERY_TASK_TIME_LIMIT = 600         # 任务硬超时 10 分钟
CELERY_TASK_SOFT_TIME_LIMIT = 540    # 软超时 9 分钟，触发 SoftTimeLimitExceeded
CELERY_TASK_ACKS_LATE = True         # 任务执行完才 ACK，崩溃可重试
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # 防止长任务饥饿
CELERY_TASK_REJECT_ON_WORKER_LOST = True

# 4. django-redis（可选，提供更精细的连接池控制）
# 若使用 django-redis，则将 BACKEND 改为 "django_redis.cache.RedisCache"
# 此处示例 Django 5.2 内置 RedisCache 的写法

# 5. 通用配置
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-key")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_celery_beat",
    "django_celery_results",
    # 业务 apps
    "apps.products",
    "apps.orders",
    "apps.users",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {"user": "100/min"},
    "EXCEPTION_HANDLER": "apps.common.exceptions.custom_exception_handler",
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": int(os.getenv("DB_PORT", "3306")),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"charset": "utf8mb4"},
    }
}

USE_TZ = True
TIME_ZONE = "Asia/Shanghai"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
```

### 3.3 缓存工具与装饰器

`apps/common/cache.py`：

```python
# apps/common/cache.py
import hashlib
import json
import uuid
from functools import wraps
from typing import Callable

from django.core.cache import cache
from django_redis import get_redis_connection  # 若用 django-redis
# Django 5.2 内置 RedisCache 可直接通过 cache.client 获取底层客户端


def _build_key(prefix: str, args: tuple, kwargs: dict) -> str:
    payload = json.dumps({"args": list(args), "kwargs": kwargs}, default=str, sort_keys=True)
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def cache_with_key(prefix: str, timeout: int = 300):
    """通用函数级缓存装饰器，自动带随机抖动防雪崩"""
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = _build_key(prefix, args, kwargs)
            cached = cache.get(key)
            if cached is not None:
                return cached
            result = func(*args, **kwargs)
            import random
            jitter = random.randint(0, max(1, timeout // 10))
            cache.set(key, result, timeout + jitter)
            return result
        return wrapper
    return decorator


def invalidate_prefix(prefix: str) -> int:
    """按前缀批量失效（小批量场景）"""
    client = cache.client.get_client()
    deleted = 0
    batch = []
    for key in client.scan_iter(match=f"{prefix}:*", count=200):
        batch.append(key)
        if len(batch) >= 200:
            deleted += client.unlink(*batch)
            batch.clear()
    if batch:
        deleted += client.unlink(*batch)
    return deleted


class DistributedLock:
    """基于 SET NX EX + Lua 释放的分布式锁"""

    _UNLOCK = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    end
    return 0
    """
    _RENEW = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('EXPIRE', KEYS[1], ARGV[2])
    end
    return 0
    """

    def __init__(self, lock_key: str, timeout: int = 30, retry_times: int = 3,
                 retry_delay: float = 0.1):
        self.lock_key = f"lock:{lock_key}"
        self.timeout = timeout
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self.identifier = str(uuid.uuid4())
        self._client = cache.client.get_client()

    def __enter__(self):
        import time
        for _ in range(self.retry_times):
            acquired = cache.set(self.lock_key, self.identifier, self.timeout, nx=True)
            if acquired:
                return self
            time.sleep(self.retry_delay)
        raise TimeoutError(f"acquire lock {self.lock_key} timeout")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._client.eval(self._UNLOCK, 1, self.lock_key, self.identifier)
        return False
```

### 3.4 视图缓存（cache_page + 模板缓存）

`apps/products/views.py`：

```python
# apps/products/views.py
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import viewsets, status

from apps.common.cache import cache_with_key, invalidate_prefix
from .models import Product
from .serializers import ProductSerializer


@api_view(["GET"])
@permission_classes([AllowAny])
@vary_on_headers("Authorization")
@cache_page(60 * 5, key_prefix="product_list")  # 5 分钟视图级缓存
def product_list_view(request):
    """列表页：用 cache_page 装饰器，缓存整个响应"""
    products = Product.objects.all().select_related("category")[:50]
    serializer = ProductSerializer(products, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([AllowAny])
def product_detail_view(request, pk: int):
    """详情页：手动缓存，更新时失效"""
    cache_key = f"product:detail:{pk}"
    data = cache.get(cache_key)
    if data is None:
        product = Product.objects.select_related("category").get(pk=pk)
        data = ProductSerializer(product).data
        cache.set(cache_key, data, 300)
    return Response(data)


@cache_with_key(prefix="product:hot", timeout=60)
def get_hot_products(category_id: int) -> list:
    """函数级缓存示例"""
    return list(
        Product.objects.filter(category_id=category_id)
        .order_by("-sales_count")
        .values("id", "name", "price")[:10]
    )
```

### 3.5 信号 + 缓存失效

`apps/products/signals.py`：

```python
# apps/products/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache

from .models import Product


@receiver(post_save, sender=Product)
def invalidate_product_cache_on_save(sender, instance: Product, **kwargs):
    """写库后立即失效相关缓存（Cache-Aside 模式）"""
    cache.delete(f"product:detail:{instance.pk}")
    cache.delete("product_list:*")  # 仅装饰性；前缀失效应使用 invalidate_prefix
    # 批量失效列表页缓存
    from apps.common.cache import invalidate_prefix
    invalidate_prefix("product:hot")
    invalidate_prefix("product_list")


@receiver(post_delete, sender=Product)
def invalidate_product_cache_on_delete(sender, instance: Product, **kwargs):
    cache.delete(f"product:detail:{instance.pk}")
    from apps.common.cache import invalidate_prefix
    invalidate_prefix("product:hot")
```

`apps/products/apps.py`：

```python
# apps/products/apps.py
from django.apps import AppConfig


class ProductsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.products"

    def ready(self):
        # 导入信号，确保注册
        from . import signals  # noqa: F401
```

### 3.6 Celery + Redis broker

`config/celery.py`：

```python
# config/celery.py
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

app = Celery("myapp")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
```

`config/__init__.py`：

```python
# config/__init__.py
from .celery import app as celery_app

__all__ = ("celery_app",)
```

`apps/orders/tasks.py`：

```python
# apps/orders/tasks.py
import logging
import time

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def process_order(self, order_id: int):
    """异步处理订单：扣库存、生成支付、发送通知"""
    try:
        logger.info("processing order %s", order_id)
        time.sleep(2)  # 模拟业务
        return {"order_id": order_id, "status": "done"}
    except SoftTimeLimitExceeded:
        logger.warning("order %s soft time limit", order_id)
        raise self.retry(exc=Exception("soft timeout"))
    except Exception as exc:
        logger.exception("order %s failed", order_id)
        raise self.retry(exc=exc, countdown=30)


@shared_task
def sync_hot_products():
    """定时任务：每日同步热点商品到缓存预热"""
    from apps.products.models import Product
    from django.core.cache import cache
    hot = list(
        Product.objects.order_by("-sales_count").values("id", "name", "price")[:100]
    )
    cache.set("product:hot:all", hot, 86400)
    return len(hot)
```

启动：

```bash
# 启动 worker
celery -A config worker -l info --concurrency=4

# 启动 beat（定时任务）
celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

### 3.7 Celery + Redis Stream（消费者组）

```python
# apps/orders/stream_consumer.py
import logging

import redis
from django.conf import settings
from celery import shared_task

logger = logging.getLogger(__name__)

STREAM_NAME = "orders:stream"
GROUP_NAME = "order_workers"


def get_redis():
    return redis.Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


def ensure_group():
    r = get_redis()
    try:
        r.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


@shared_task(bind=True)
def consume_order_stream(self, count: int = 10, block_ms: int = 5000):
    """消费 Stream 中的订单消息"""
    ensure_group()
    r = get_redis()
    consumer = f"worker-{self.request.hostname}"
    messages = r.xreadgroup(
        GROUP_NAME, consumer, {STREAM_NAME: ">"},
        count=count, block=block_ms,
    )
    processed = 0
    for _stream, msgs in messages:
        for msg_id, fields in msgs:
            try:
                logger.info("handling %s: %s", msg_id, fields)
                # 调用实际业务任务
                process_order.delay(int(fields["order_id"]))
                r.xack(STREAM_NAME, GROUP_NAME, msg_id)
                processed += 1
            except Exception as exc:
                logger.exception("handle %s failed: %s", msg_id, exc)
    return processed


@shared_task
def reclaim_stale_messages(min_idle_ms: int = 60_000, count: int = 10):
    """回收超时未 ACK 的消息，重新分配给当前消费者"""
    r = get_redis()
    consumer = "reclaimer"
    pending = r.xpending_range(STREAM_NAME, GROUP_NAME, "-", "+", count)
    reclaimed = 0
    for p in pending:
        if p.get("time_since_delivered", 0) > min_idle_ms:
            claimed = r.xclaim(
                STREAM_NAME, GROUP_NAME, consumer, min_idle_ms, [p["message_id"]]
            )
            reclaimed += len(claimed)
    return reclaimed
```

## 四、连接池与性能调优

### 4.1 ioredis 连接池

```
ioredis 连接模型：

每个 Redis 实例 = 一个长连接（默认）
   ├── 单实例连接自动重连
   └── 命令队列化，按 FIFO 顺序处理

Cluster 模式 = 每个节点一个连接池
   ├── maxRedirections 控制 ASK/MOVED 重试
   ├── scaleReads 控制读路由（master/slave/all）
   └── 节点状态通过 CLUSTER NODES 自动刷新

Sentinel 模式 = 通过 SENTINEL get-master-addr 自动定位
   ├── preferredSlaves 控制从节点选择
   └── 故障转移后自动重连新主
```

```javascript
// 集群模式连接池调优
const cluster = new Redis.Cluster(
  [
    { host: '127.0.0.1', port: 7001 },
    { host: '127.0.0.1', port: 7002 },
    { host: '127.0.0.1', port: 7003 },
  ],
  {
    redisOptions: {
      enableReadyCheck: true,
      maxRetriesPerRequest: 3,
      retryStrategy: (t) => Math.min(t * 200, 2000),
      reconnectOnError: (err) => err.message.includes('READONLY'),
      // ioredis 5.x 已默认按需创建连接，无需显式 pool
    },
    scaleReads: 'slave',
    maxRedirections: 16,
    slotsRefreshTimeout: 5000,
    slotsRefreshInterval: 5000,
  }
);
```

### 4.2 Django 连接池

```
Django RedisCache 连接池（基于 redis-py）：

ConnectionPool
   ├── max_connections：单实例最大连接数（默认 50）
   ├── timeout：获取连接等待时长（秒）
   └── connection_class：HiredisConnection（高性能解析器）

BlockConnectionPool（默认）
   ├── 线程安全
   └── 阻塞等待，避免连接风暴

生产建议：
   ├── max_connections = Celery worker 数 × 并发数
   ├── 不同业务用不同 db 或前缀，避免互相干扰
   └── Sentinel/Cluster 模式下禁用 connection_pool 的单点检测
```

### 4.3 Pipeline 批量与缓存预热

```python
# apps/products/preheat.py
from django.core.cache import cache
from .models import Product


def preheat_product_cache(batch_size: int = 200):
    """服务启动或低峰期批量预热商品缓存"""
    qs = Product.objects.select_related("category").all().iterator(chunk_size=batch_size)
    client = cache.client.get_client()
    pipeline = client.pipeline(transaction=False)
    count = 0
    for product in qs:
        key = f"product:detail:{product.pk}"
        # 注意：Django 5.2 RedisCache 的 value 格式需用 cache.set 而非底层 set
        cache.set(key, _serialize(product), 3600, _pipeline=pipeline)  # 伪代码
        count += 1
        if count % batch_size == 0:
            pipeline.execute()
            pipeline = client.pipeline(transaction=False)
    pipeline.execute()
    return count


def _serialize(product):
    from .serializers import ProductSerializer
    return ProductSerializer(product).data
```

> 真实生产中应使用 `cache.set_many` 或直接走 redis-py pipeline；上面示例展示思路。

## 五、Redis vs MySQL 驱动集成对比

| 维度 | Redis | MySQL |
|------|-------|-------|
| 通信协议 | RESP3（TCP） | MySQL Protocol（TCP） |
| 驱动 Node.js | ioredis / node-redis | mysql2 / knex |
| 驱动 Python | redis-py / django-redis | PyMySQL / mysqlclient / aiomysql |
| 连接模型 | 长连接 + 命令队列 + 自动重连 | 连接池（POOL_SIZE/maxOverflow） |
| 异步 | 原生 Promise / asyncio | 需 aiomysql / asyncmy |
| 事务 | MULTI/EXEC（无回滚） | BEGIN/COMMIT/ROLLBACK（ACID） |
| 一致性 | 最终一致（Cache-Aside） | 强一致（默认可重复读） |
| 持久化 | RDB/AOF（异步） | redo/undo log（同步） |
| 失败模式 | 缓存击穿/穿透/雪崩 | 死锁/慢查询/主从延迟 |
| Python ORM | 直接 redis.Redis 操作 | Django ORM |
| Django 集成 | RedisCache / django-redis | Django ORM（db.backends.mysql） |
| 配置项 | maxconnections / retryStrategy | CONN_MAX_AGE / CONN_HEALTH_CHECKS |
| 监控指标 | INFO /latency / --stat | slow_query_log / performance_schema |
| 限流 / 锁 | Lua 脚本（原子） | SELECT FOR UPDATE（悲观锁） |

## 小思考题帮你巩固

1. **ioredis 与 node-redis v4+ 的关键差异是什么？同项目里为什么不要混用？**（提示：ioredis 内置连接池/集群/哨兵/Lua 开箱即用；node-redis v4 是官方 promise 客户端、需 `createClient()+connect()` 显式管理；混用会双连接且 API 语义不同，见 1/2。）
2. **为什么 Redis 客户端要"连接池/单例复用"，而不是每次请求新建连接？**（提示：每次建连=TCP 握手+RTT，高并发下连接数爆炸、服务端 fd 耗尽；池化复用降低延迟与资源，见 2/4。）
3. **Django 里 `CACHES`、`SESSION_ENGINE`、`CELERY_BROKER_URL` 三处都连 Redis，它们各自管什么？如何避免键冲突？**（提示：缓存/会话/任务队列三套用途；用不同 db 或 `KEY_PREFIX`/`VERSION` 隔离，见 3。）
4. **为什么 Django 的"写 DB 后删缓存"用信号（post_save）而不是在视图里手动删？**（提示：信号全局生效、不遗漏（管理后台/脚本/其他写入口也能触发），符合 Cache-Aside，见 3.x。）
5. **Node.js 服务重启后与 Redis 的连接会自动重连吗？需要处理什么？**（提示：ioredis 有自动重连（`retryStrategy`）；但业务要处理"重连前短暂的失败"（重试/告警）与优雅关闭（`client.quit()`），见 2.2。）

<details>
<summary>参考答案（点击展开）</summary>

1. ioredis 是社区事实标准（内置 Cluster/Sentinel/Lua/连接池/自动重连，API 直白）；node-redis v4 是官方重写（全 promise、`createClient()` + `connect()` 显式生命周期、支持模块命令）。混用会导致双连接和 API 歧义（如 `get` 返回类型、`quit`/`close` 时机不同），项目只选其一并在服务层统一封装。
2. 连接复用省去了每次 `connect(TCP 握手+RTT)`，在高并发下若每请求建连，Redis 侧文件描述符和连接数会爆、主线程还要处理大量建连事件。Pooling（ioredis 单例即池化；Django 用 `CONNECTION_POOL_KWARGS.max_connections`）把连接控制在固定数量、请求排队复用，延迟和开销都最优。
3. `CACHES`：django-redis 管"任意缓存"（cache.set/get）；`SESSION_ENGINE`：django.contrib.sessions.backends.cache/redis 管登录会话；`CELERY_BROKER_URL`：Redis 作 Celery 任务队列 broker（+ result backend）。三者可同连一个 Redis 但分 db（0/1/2）或靠 `KEY_PREFIX`（django）与 `CELERY_TASK_PREFIX` 类隔离前缀，避免互相覆盖 key。
4. 在视图 controller 里手动删缓存容易漏——后台 admin、管理脚本、Celery 任务、第三方写入口都不会走你视图。用 `post_save`/`post_delete` 信号监听模型，任何写入口触发后统一删缓存，实现"写 DB 必失效"，与 Cache-Aside 原则一致、不漏不错删。
5. ioredis 自带自动重连与 `retryStrategy`（指数退避），但重连期间请求会失败——服务层需要做"短暂失败重试/告警"（如 `maxRetriesPerRequest`），并在进程退出时 `await client.quit()` 优雅关闭连接避免 FD 泄漏；这些细节正是"封装服务层"要处理的部分（见 2.2）。

</details>

## 📖 深入阅读

- [ioredis 官方文档](https://github.com/redis/ioredis)
- [redis-py 文档](https://redis-py.readthedocs.io/)
- [Django 缓存框架](https://docs.djangoproject.com/en/5.2/topics/cache/)
- [django-redis 文档](https://django-redis-cache.readthedocs.io/)
- [Celery with Redis](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
- 本手册 [09 缓存模式与实战](../09_缓存模式与实战/09_缓存模式与实战.md)
- 本手册 [10 分布式锁与限流](../10_分布式锁与限流/10_分布式锁与限流.md)
- 本手册 [14 Redis 与 Docker 容器化部署](../14_Redis与Docker容器化部署/14_Redis与Docker容器化部署.md)

## 本章小结

1. Node.js 侧推荐 ioredis（Cluster/Sentinel/Pipeline/Lua 一应俱全），封装为 `cache/session/rateLimit/distributedLock` 四个服务即可覆盖 80% 业务场景。
2. Django 5.2 内置 `RedisCache` 后端开箱即用，结合 `KEY_PREFIX/VERSION/OPTIONS.pool` 即可上生产；老旧项目可继续用 `django-redis`。
3. 缓存一致性遵循 **Cache-Aside**：写库后失效（不要更新）缓存，配合 `post_save` 信号自动化；高一致性场景叠加延迟双删或 Canal 监听 binlog。
4. Celery + Redis broker 需重点配置 `visibility_timeout`、`acks_late`、`prefetch_multiplier=1`，避免长任务被抢占或丢失。
5. 分布式锁用 `SET NX EX` + Lua 释放 + 看门狗续期三件套；切勿用 `SETNX + EXPIRE` 两步操作（非原子）。
6. Redis 与 MySQL 在驱动、事务、一致性模型上差异巨大，集成时不要把 Redis 当作"快一点的 MySQL"用。

## 下一步导航

- 学完集成，下一步进入容器化部署 → [14 Redis 与 Docker 容器化部署](../14_Redis与Docker容器化部署/14_Redis与Docker容器化部署.md)
- 想做完整电商秒杀项目 → [15 全栈项目实战](../15_全栈项目实战/15_全栈项目实战.md)
- 回顾底层原理 → [07 事务与 Lua 脚本](../07_事务与Lua脚本/07_事务与Lua脚本.md)、[10 分布式锁与限流](../10_分布式锁与限流/10_分布式锁与限流.md)
