# Redis 跨平台差异：Windows 与 Linux

> Redis 官方仅支持 Linux/macOS，Windows 需通过 WSL2/Docker/Memurai 运行。本篇汇总关键差异与跨平台实践。

## 零基础前置认知

**这篇在讲什么**：Redis 官方只支持 Linux/macOS，Windows 得靠 WSL2、Docker 或商业软件运行。本篇回答三件事：为什么 Redis 不原生支持 Windows（fork/epoll 的 POSIX 依赖）、Windows 上有哪 4 种跑法怎么选（WSL2 / Docker / Memurai / 旧 fork）、以及跨平台开发时配置路径与服务管理的差异。读完你在 Windows 上开发、Linux 上部署能顺畅切换。*Windows 开发推荐 Docker 或 WSL2（见 2.2/2.3），生产永远 Linux。*

> 辅助类比：Redis 是"为 Linux 量身定制的跑车"——**fork()** 是它的"换挡机构"（写时复制出子进程做快照，Windows 的 CreateProcess 没有 COW 这项能力），**epoll** 是"赛道导航"（I/O 多路复用，Windows 用 IOCP、是完全不同的一套）。没有这两样，车在 Windows 上要么开不动、要么严重阉割——所以主流做法是"在 Windows 上装一个 Linux 环境（WSL2/Docker）来开这辆车"。

**基础名词集**：

| 名词 | 一句话定义 | 与相邻概念的关系 |
|------|-----------|----------------|
| fork() / COW | POSIX 复制进程（内存页写时复制） | RDB 快照、AOF 重写、后台任务依赖 |
| epoll | Linux 的 I/O 多路复用（事件驱动） | Windows 对应 IOCP；两者 API 完全不同 |
| WSL2 | Windows 子系统 for Linux 2（轻量虚拟机） | 在 Windows 上跑真 Linux 内核 |
| Docker Desktop | Windows/macOS 上的容器运行时（WSL2 后端） | 容器与宿主机隔离，Redis 镜像跨平台 |
| Memurai | Windows 原生 Redis 兼容缓存（商业） | 不保证 100% 兼容，生产不推荐 |

> 区分/注意：**"WSL2 里的 Redis" ≠ "Windows 原生 Redis"**——前者是真 Linux 进程（只是跑在虚拟机里），后者是商业移植；**Windows 上的 Docker 方案底层其实也是 WSL2 后端**，两种推荐方案内核一致。

## 前言与本篇目标

Redis 官方不提供 Windows 原生构建。Windows 上的 Redis 方案（Memurai、Microsoft 维护的旧版 fork、WSL2、Docker）各有局限。本篇目标：

1. 理解 Redis 为何不支持 Windows 原生（fork()、epoll、文件权限）
2. 掌握 Windows 上运行 Redis 的 4 种方案及选型
3. 掌握 Linux 与 Windows 下 Redis 配置文件路径、服务管理的差异
4. 了解持久化（RDB/AOF）在不同文件系统下的性能差异
5. 掌握跨平台 Redis 客户端配置最佳实践

📖 深入阅读：Redis 核心原理见 [01_Redis架构与核心概念](../01_Redis架构与核心概念/01_Redis架构与核心概念.md)，Docker 部署见 [14_Redis与Docker容器化部署](../14_Redis与Docker容器化部署/14_Redis与Docker容器化部署.md)。

---

## 一、Redis 为何不支持 Windows 原生

### 1.1 核心依赖的 POSIX 特性

Redis 持久化与高性能依赖以下 Linux/POSIX 特性，Windows 原生不支持：

| 特性 | Linux | Windows | 影响 |
|------|-------|---------|------|
| `fork()` | ✅ 写时复制（COW） | ❌ 无（CreateProcess 不支持 COW） | RDB 持久化、AOF 重写依赖 fork 创建子进程 |
| `epoll` | ✅ O(1) I/O 多路复用 | ❌（IOCP 模型不同） | 事件循环性能 |
| 文件权限 | ✅ rwx | ❌ ACL | 安全模型 |
| 信号机制 | ✅ SIGTERM/SIGINT | ⚠️ 部分支持 | 优雅关闭 |
| `mmap()` | ✅ 内存映射文件 | ⚠️ 有但不完全兼容 | AOF 重写 |

### 1.2 fork() 与 RDB 持久化

Redis RDB 持久化的核心流程是：主进程 `fork()` 创建子进程 → 子进程遍历数据写 RDB 文件 → 主进程继续处理命令。`fork()` 利用**写时复制（COW）**，子进程共享父进程内存页，只有修改时才复制，避免全量内存拷贝。

Windows 的 `CreateProcess` 不支持 COW，无法高效实现这一机制。Microsoft 曾维护的 Redis Windows fork（止于 3.x）通过替换 fork 为线程模拟，但存在内存一致性和性能问题，已停止维护。

---

## 二、Windows 上运行 Redis 的 4 种方案

### 2.1 方案对比

| 方案 | 性能 | 生产可用 | 维护状态 | 推荐场景 |
|------|------|---------|---------|---------|
| WSL2 | ⭐⭐⭐⭐ | ✅ | 活跃 | Windows 开发环境 |
| Docker Desktop | ⭐⭐⭐⭐ | ✅ | 活跃 | Windows 开发/测试 |
| Memurai | ⭐⭐⭐ | ⚠️（商业） | 活跃 | Windows 原生产（不推荐） |
| Redis Windows Fork | ⭐⭐ | ❌ | 已停止 | ❌ 不推荐 |

### 2.2 WSL2 方案（推荐）

```powershell
# Windows PowerShell（管理员）
wsl --install -d Ubuntu-24.04
wsl --set-default-version 2
```

```bash
# WSL2 Ubuntu 内
sudo apt update
sudo apt install redis-server
sudo systemctl enable --now redis-server

# 验证
redis-cli ping  # PONG
redis-cli info server | grep redis_version
```

WSL2 注意事项：
- WSL2 网络默认为 NAT，Windows 访问 WSL2 Redis 需绑定 `0.0.0.0` 或使用 WSL2 IP
- WSL2 文件系统为 ext4，性能优于直接访问 NTFS

```bash
# WSL2 中修改 Redis 绑定地址，允许 Windows 访问
sudo sed -i 's/^bind 127.0.0.1/bind 0.0.0.0/' /etc/redis/redis.conf
sudo systemctl restart redis-server

# Windows 中访问（获取 WSL2 IP）
wsl hostname -I  # 获取 IP，如 172.20.0.2
# 在 Windows 中：redis-cli -h 172.20.0.2 -p 6379
```

### 2.3 Docker 方案

```bash
# Windows PowerShell
docker run -d --name redis-dev -p 6379:6379 redis:8.2-alpine

# 带持久化与配置
docker run -d --name redis-prod \
  -p 6379:6379 \
  -v redis-data:/data \
  redis:8.2-alpine redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
```

### 2.4 Memurai（商业替代）

Memurai 是专为 Windows 开发的 Redis 兼容缓存，但：
- 商业许可，免费版有限制
- 不保证 100% Redis 兼容性
- 生产环境仍推荐 Linux

---

## 三、配置文件与服务管理差异

### 3.1 配置文件路径

| 平台 | 配置文件路径 | 数据目录 |
|------|-------------|---------|
| Linux (apt) | `/etc/redis/redis.conf` | `/var/lib/redis/` |
| Linux (源码) | `redis.conf`（自定义） | 自定义 |
| WSL2 | `/etc/redis/redis.conf` | `/var/lib/redis/` |
| Docker | 容器内 `/usr/local/etc/redis/redis.conf` | 容器 volume |
| Windows (Memurai) | `C:\Program Files\Memurai\memurai.conf` | `C:\ProgramData\Memurai\` |

### 3.2 服务管理

```bash
# Linux: systemd 管理
sudo systemctl start redis-server
sudo systemctl stop redis-server
sudo systemctl restart redis-server
sudo systemctl status redis-server
sudo systemctl enable redis-server  # 开机自启

# Linux: 源码编译版手动管理
redis-server /path/to/redis.conf          # 前台启动
redis-server /path/to/redis.conf --daemonize yes  # 后台启动
redis-cli shutdown                        # 优雅关闭

# Windows (Docker)
docker start redis-dev
docker stop redis-dev
docker restart redis-dev

# Windows (WSL2)
sudo systemctl start redis-server  # 在 WSL2 内
```

### 3.3 日志文件

| 平台 | 日志路径 |
|------|---------|
| Linux (apt) | `/var/log/redis/redis-server.log` |
| Linux (源码) | 配置文件中 `logfile` 指定 |
| WSL2 | `/var/log/redis/redis-server.log` |
| Docker | `docker logs redis-dev` |

---

## 四、持久化性能差异

### 4.1 文件系统对持久化的影响

| 文件系统 | RDB 性能 | AOF 性能 | 说明 |
|---------|---------|---------|------|
| ext4 (Linux) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 原生支持 fsync，最佳 |
| XFS (Linux) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 大文件性能优 |
| ext4 (WSL2) | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 接近原生，略有损耗 |
| NTFS (Windows) | ⭐⭐ | ⭐⭐ | fsync 性能差，小文件 I/O 慢 |

### 4.2 AOF fsync 策略跨平台差异

```bash
# Linux: 推荐 everysec（平衡性能与安全）
appendonly yes
appendfsync everysec

# Windows (Memurai): fsync 性能差，可能需要降级
appendfsync no  # 交由 OS 刷新，性能最好但可能丢数据
```

### 4.3 fork() 性能

```bash
# Linux: fork() 快速（COW），大内存实例也只需毫秒级
# INFO memory 中的 mem_fragmentation_ratio 可监控

# WSL2: fork() 可用，性能接近原生

# Windows 原生: 无 fork()，RDB/AOF 重写不可用或性能差
```

---

## 五、跨平台客户端配置

### 5.1 Python (redis-py)

```python
import redis

# Linux / WSL2 / Docker（通用配置）
r = redis.Redis(
    host='127.0.0.1',  # Linux/WSL2 本机
    # host='172.20.0.2',  # Windows 访问 WSL2 时用 WSL2 IP
    port=6379,
    db=0,
    decode_responses=True,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True,
)

# 跨平台连接池
pool = redis.ConnectionPool(
    host='127.0.0.1',
    port=6379,
    max_connections=20,
)
r = redis.Redis(connection_pool=pool)
```

### 5.2 Node.js (ioredis)

```typescript
import Redis from 'ioredis';

// 跨平台通用配置
const redis = new Redis({
  host: process.env.REDIS_HOST || '127.0.0.1',
  port: 6379,
  retryStrategy: (times) => Math.min(times * 50, 2000),
  maxRetriesPerRequest: 3,
});

// Windows 开发时连接 WSL2 Redis
// const redis = new Redis({ host: '172.20.0.2', port: 6379 });
```

### 5.3 环境变量管理

```bash
# Linux: export
export REDIS_HOST=127.0.0.1
export REDIS_PORT=6379

# Windows PowerShell: $env
$env:REDIS_HOST = "127.0.0.1"
$env:REDIS_PORT = "6379"

# Windows CMD: set
set REDIS_HOST=127.0.0.1
set REDIS_PORT=6379

# 跨平台 .env 文件（推荐）
# .env
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=
```

---

## 六、跨平台部署最佳实践

### 6.1 Docker Compose 统一方案（推荐）

```yaml
# docker-compose.yml（Windows / Linux 通用）
services:
  redis:
    image: redis:8.2-alpine
    container_name: redis
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    command: >
      redis-server
      --appendonly yes
      --maxmemory 256mb
      --maxmemory-policy allkeys-lru
      --requirepass ${REDIS_PASSWORD:-}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3

volumes:
  redis-data:
```

```bash
# Linux / Windows（Docker Desktop）通用启动
docker compose up -d
```

### 6.2 开发环境选型决策

```
是否需要 Windows 原生 Redis？
├── 否 → Docker Desktop（最简单）或 WSL2（最接近生产）
└── 是 → Memurai（商业，不推荐生产）
```

---

## 最佳实践与常见陷阱

### 最佳实践

1. **生产环境用 Linux**：Redis 官方支持，fork/epoll/fsync 性能最佳。
2. **Windows 开发用 Docker**：`docker run -d -p 6379:6379 redis:8.2-alpine`，一行启动。
3. **统一用 Docker Compose**：跨平台一致，避免环境差异。
4. **配置走环境变量**：`.env` 文件 + `os.environ`，避免硬编码 IP/端口。
5. **路径用 path.join()**：Node.js/Python 跨平台路径处理。

### 常见陷阱

1. **WSL2 网络隔离**：Windows 默认无法通过 `127.0.0.1` 访问 WSL2 Redis，需绑定 `0.0.0.0` 或用 WSL2 IP。
2. **NTFS 持久化性能差**：Windows 原生文件系统 fsync 慢，AOF everysec 可能成为瓶颈。
3. **Redis Windows Fork 过旧**：Microsoft 维护的版本止于 3.x，缺少 Stream、ACL 等新特性，不要用。
4. **大小写敏感**：Linux 文件名大小写敏感，Windows 不敏感，配置文件路径注意一致性。
5. **Docker Desktop 资源**：WSL2 后端默认占用内存可能不足，需在 `.wslconfig` 调整。

---

## 下一步导航

📖 深入阅读：
- Redis 核心架构：[01_Redis架构与核心概念](../01_Redis架构与核心概念/01_Redis架构与核心概念.md)
- Docker 部署：[14_Redis与Docker容器化部署](../14_Redis与Docker容器化部署/14_Redis与Docker容器化部署.md)
- Linux 基础：[Linux/03_安装配置与命令行入门](../../Linux/03_安装配置与命令行入门.md)
- 全局跨平台差异速查：[00_学习文档总目录](../../00_学习文档总目录.md) 第三章
