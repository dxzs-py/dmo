# 14 Redis 与 Docker 容器化部署

> 用 Docker / Docker Compose 一站式完成 Redis 单机、Sentinel、Cluster 三种形态的容器化部署，附带完整生产级 redis.conf、sentinel.conf、健康检查与监控栈。

## 零基础前置认知

**这篇在讲什么**：用 Docker 把 Redis 跑起来，从"一条命令起单机"到"Compose 编排"再到"Sentinel/Cluster 高可用"，核心是同一件事：**镜像 + 配置 + 数据卷 + 网络**四件套怎么组合。读完你能：用 `docker run redis:8.2-alpine` 起单机开发实例，用 Compose 拉起带数据卷/健康检查的开发栈与生产 Sentinel 栈，把 Redis 密码交给 env/Secret 而不烙进镜像。*生产 Sentinel/Cluster Compose 与监控栈属于"开发部署自研项目"必备，本篇完整保留；与 05（复制高可用）、06（集群分片）是"同一道理在 Docker 里的落地"。*

> 辅助类比：把 Docker 化 Redis 想成"装一台带水箱的净水设备"——**镜像** = 设备本体（redis:8.2-alpine，轻巧型）；**数据卷** = 外接水箱（容器删了水箱不丢，持久化就靠它挂载 /data）；**配置** = 设定档（redis.conf：内存上限、密码、AOF）；**Compose** = 安装说明书（一文件声明多台设备+依赖）；**Sentinel/Cluster** = 双机热备/多机扩容的排布方案。设备还是那台，排布方式决定是单机、热备还是集群。

## 学习目标
1. 能够区分 `redis:8.2` 与 `redis/redis-stack` 两种镜像，按 RediSearch/RedisJSON 等模块需求选型（Redis 8 起主要模块已内置）。
2. 能够编写单机 `docker run`、开发 Compose、生产 Sentinel Compose、Cluster Compose 四套部署方案。
3. 能够编写生产级 `redis.conf`（maxmemory / appendonly / io-threads / protected-mode）与 `sentinel.conf`。
4. 能够自定义健康检查脚本并集成 `redis-exporter + Prometheus + Grafana` 监控栈。
5. 能够落地容器安全（非 root 运行、网络隔离、密码环境变量）。

## 前置知识

- 持久化机制：[03 持久化机制](../03_持久化机制/03_持久化机制.md)
- 复制与高可用：[05 复制与高可用](../05_复制与高可用/05_复制与高可用.md)
- 集群与分片：[06 集群与分片](../06_集群与分片/06_集群与分片.md)
- 安全与运维：[12 安全与运维](../12_安全与运维/12_安全与运维.md)
- 性能调优与监控：[11 性能调优与监控](../11_性能调优与监控/11_性能调优与监控.md)

## 一、镜像选型

### 1.1 官方镜像对比

| 镜像 | 体积 | 内置模块 | 适用场景 |
|------|------|---------|---------|
| `redis:8.2` | ~50MB | 无 | 纯缓存、消息队列、分布式锁 |
| `redis:8.2-alpine` | ~15MB | 无 | 资源受限、CI 环境 |
| `redis:8.2-bookworm` | ~50MB | 无 | Debian 基础镜像 |
| `redis/redis-stack:8.0` | ~400MB | RedisJSON / RedisGraph 等全家桶（RediSearch/TimeSeries/Bloom **Redis 8 已内置**） | 需要 Stack 额外模块时 |
| `redis/redis-stack-server:8.0` | ~400MB | 同上，但不含 RedisInsight UI | 生产（无需 UI） |

> 版本注（2026-09 核实）：**Redis 8.0 起 RediSearch/Search/JSON/TimeSeries/Bloom 已内置**，纯向量/搜索场景直接用 `redis:8.2-alpine` 即可，不再强制 Redis Stack；`redis-stack` 镜像仍用于需要其全家桶（RedisGraph 等）或旧项目迁移的场景。

```
镜像选型决策：

是否需要 RediSearch/RedisJSON/RedisBloom/RedisTimeSeries？
├── 否 → redis:8.2-alpine（最小体积）
└── 是 → redis/redis-stack-server（生产，无 UI）
        └── 需要 Web UI 调试 → redis/redis-stack（含 RedisInsight :8001）

是否需要 ARM64？
├── 是（M1/M2 Mac）→ redis:8.2-alpine（多架构支持）
└── 否 → 任意镜像均可
```

### 1.2 端口约定

| 端口 | 用途 |
|------|------|
| 6379 | Redis 主端口 |
| 26379 | Sentinel 端口 |
| 7000-7005 | Cluster 节点端口 |
| 17000-17005 | Cluster 总线端口（端口+10000） |
| 8001 | RedisInsight Web UI |
| 9121 | redis-exporter metrics |

## 二、单机部署

### 2.1 docker run（最简）

```bash
# 拉取官方镜像
docker pull redis:8.2-alpine

# 单机启动，仅内存模式（重启数据丢失）
docker run -d \
  --name redis-dev \
  -p 6379:6379 \
  redis:8.2-alpine \
  redis-server --requirepass "devpass123" --maxmemory 256mb --maxmemory-policy allkeys-lru  # 仅本地演示；生产密码用 env/Secret 注入

# 验证
docker exec -it redis-dev redis-cli -a devpass123 ping   # PONG
docker exec -it redis-dev redis-cli -a devpass123 INFO memory | grep used_memory_human
```

### 2.2 docker run + Volume + 自定义 redis.conf

目录结构：

```
redis-standalone/
├── docker-compose.yml
├── conf/
│   └── redis.conf
└── data/
```

`conf/redis.conf`（生产调优，详细注释见后）：

```conf
# ===== 网络 =====
bind 0.0.0.0
protected-mode yes
port 6379
tcp-backlog 511
tcp-keepalive 60
timeout 0

# ===== 通用 =====
daemonize no
supervised no
loglevel notice
databases 16

# ===== 内存 =====
maxmemory 1gb
maxmemory-policy allkeys-lru
maxmemory-samples 10

# ===== 持久化 RDB =====
save 900 1
save 300 10
save 60 10000
stop-writes-on-bgsave-error yes
rdbcompression yes
rdbchecksum yes
dbfilename dump.rdb
dir /data

# ===== 持久化 AOF =====
appendonly yes
appendfilename "appendonly.aof"
appenddirname "appendonlydir"
appendfsync everysec
aof-use-rdb-preamble yes
no-appendfsync-on-rewrite yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# ===== 慢查询 =====
slowlog-log-slower-than 10000
slowlog-max-len 1024

# ===== 客户端 =====
maxclients 10000

# ===== 安全 =====
requirepass ${REDIS_PASSWORD}
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command DEBUG ""
rename-command KEYS ""

# ===== 多线程 I/O =====
io-threads 4
io-threads-do-reads yes

# ===== 复制（从节点才生效） =====
replica-read-only yes
repl-backlog-size 64mb
repl-diskless-sync yes
repl-diskless-sync-delay 5

# ===== 懒惰释放 =====
lazyfree-lazy-eviction yes
lazyfree-lazy-expire yes
lazyfree-lazy-server-del yes
lazyfree-lazy-user-del yes

# ===== 内存碎片整理 =====
activedefrag yes
active-defrag-threshold-lower 10
active-defrag-threshold-upper 100
active-defrag-cycle-min 1
active-defrag-cycle-max 25
```

`docker run` 启动：

```bash
docker run -d \
  --name redis-prod \
  --restart unless-stopped \
  -p 6379:6379 \
  -v $(pwd)/conf/redis.conf:/etc/redis/redis.conf:ro \
  -v $(pwd)/data:/data \
  -v $(pwd)/logs:/logs \
  --user "redis:redis" \
  --memory="2g" \
  --memory-swap="2g" \
  --cpus="2" \
  --network redis-net \
  -e REDIS_PASSWORD='ProdStrongPass!2026' \
  redis:8.2-alpine \
  redis-server /etc/redis/redis.conf
```

> 注意：`requirepass ${REDIS_PASSWORD}` 在 redis.conf 中不会自动展开环境变量，需通过 `sed` 或自定义 entrypoint 替换。下面 Compose 方案用 entrypoint 处理。

## 三、开发环境 Compose

`docker-compose.yml`（开发，含 RedisInsight）：

```yaml
# docker-compose.dev.yml
version: "3.9"

networks:
  redis-net:
    driver: bridge

volumes:
  redis-data:
    driver: local
  redis-insight-data:
    driver: local

services:
  redis:
    image: redis:8.2-alpine
    container_name: redis-dev
    restart: unless-stopped
    command: >
      sh -c '
        sed -i "s/__REDIS_PASSWORD__/$${REDIS_PASSWORD}/g" /etc/redis/redis.conf
        exec redis-server /etc/redis/redis.conf
      '
    environment:
      REDIS_PASSWORD: devpass123
    ports:
      - "6379:6379"
    volumes:
      - ./conf/redis.conf:/etc/redis/redis.conf:ro
      - redis-data:/data
    networks:
      - redis-net
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "$$REDIS_PASSWORD", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 10s
    sysctls:
      net.core.somaxconn: 1024

  redis-insight:
    image: redis/redis-insight:latest
    container_name: redis-insight
    restart: unless-stopped
    ports:
      - "8001:8001"
    volumes:
      - redis-insight-data:/data
    networks:
      - redis-net
    depends_on:
      redis:
        condition: service_healthy
```

启动：

```bash
docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml logs -f redis

# 访问 RedisInsight：http://localhost:8001
# 添加连接：redis://redis:6379，密码 devpass123
```

## 四、生产环境 Compose（Sentinel 高可用）

### 4.1 架构

```
                ┌─────────────┐
                │  Sentinel ×3 │  (quorum=2)
                └──────┬──────┘
                       │ monitor
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐    ┌─────────┐    ┌─────────┐
   │ redis-1 │◄───│ redis-2 │    │ redis-3 │
   │ Master  │    │  Slave  │    │  Slave  │
   │  6379   │    │  6379   │    │  6379   │
   └─────────┘    └─────────┘    └─────────┘
        │              │              │
        └──────────────┴──────────────┘
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   ┌─────────────┐             ┌─────────────┐
   │  exporter ×3│             │  Sentinel ×3│
   │  :9121      │             │  :26379     │
   └──────┬──────┘             └─────────────┘
          │ scrape
   ┌──────▼──────┐    ┌──────────────┐
   │ prometheus  │ ─► │   grafana    │
   │   :9090     │    │   :3000      │
   └─────────────┘    └──────────────┘
```

### 4.2 目录结构

```
redis-sentinel/
├── docker-compose.prod.yml
├── conf/
│   ├── redis-master.conf
│   ├── redis-slave.conf
│   ├── sentinel.conf
│   └── sentinel-entrypoint.sh
├── prometheus/
│   ├── prometheus.yml
│   └── alert.rules.yml
├── grafana/
│   └── provisioning/
│       ├── datasources/datasource.yml
│       └── dashboards/dashboard.yml
└── scripts/
    └── healthcheck.sh
```

### 4.3 配置文件

`conf/redis-master.conf`：

```conf
# 主节点配置
bind 0.0.0.0
protected-mode yes
port 6379
daemonize no
databases 16

# 持久化
save 900 1
save 300 10
save 60 10000
appendonly yes
appendfsync everysec
aof-use-rdb-preamble yes
dir /data

# 内存
maxmemory 2gb
maxmemory-policy allkeys-lru

# 安全（密码由 entrypoint 注入）
# requirepass __REDIS_PASSWORD__
# masterauth __REDIS_PASSWORD__

# 网络
tcp-keepalive 60
maxclients 10000
io-threads 4
io-threads-do-reads yes

# 慢查询
slowlog-log-slower-than 10000
slowlog-max-len 1024

# 懒惰释放
lazyfree-lazy-eviction yes
lazyfree-lazy-expire yes
lazyfree-lazy-server-del yes

# 禁用危险命令
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command DEBUG ""
```

`conf/redis-slave.conf`：

```conf
# 从节点配置（继承主节点配置）
include /etc/redis/redis-master.conf

# 主从（master 地址由 sentinel 动态调整，此处仅占位）
# replicaof redis-1 6379
replica-read-only yes
replica-priority 100
repl-backlog-size 64mb
repl-diskless-sync yes
```

`conf/sentinel.conf`：

```conf
# Sentinel 配置
port 26379
daemonize no
dir /data
pidfile /var/run/redis-sentinel.pid
logfile ""

# 监控主节点（master 地址由 entrypoint 注入）
# sentinel monitor mymaster __MASTER_HOST__ 6379 2
sentinel resolve-hostnames yes
sentinel announce-hostnames yes

# 主观下线 30s
sentinel down-after-milliseconds mymaster 30000
# 故障转移超时 180s
sentinel failover-timeout mymaster 180000
# 故障转移后并行同步的从节点数
sentinel parallel-syncs mymaster 1

# 认证
# sentinel auth-pass mymaster __REDIS_PASSWORD__
sentinel deny-scripts-reconfig yes

# 通知脚本（可选）
# sentinel notification-script mymaster /etc/redis/notify.sh
# sentinel client-reconfig-script mymaster /etc/redis/reconfig.sh
```

`conf/sentinel-entrypoint.sh`（动态注入环境变量）：

```bash
#!/bin/sh
set -e

# 替换 sentinel.conf 中的占位符
sed -i "s/__MASTER_HOST__/${SENTINEL_MASTER_HOST:-redis-1}/g" /etc/redis/sentinel.conf
sed -i "s/__REDIS_PASSWORD__/${REDIS_PASSWORD}/g" /etc/redis/sentinel.conf

# 替换 redis.conf 中的密码
if [ -f /etc/redis/redis-master.conf ]; then
  sed -i "s/__REDIS_PASSWORD__/${REDIS_PASSWORD}/g" /etc/redis/redis-master.conf
fi
if [ -f /etc/redis/redis-slave.conf ]; then
  sed -i "s/__REDIS_PASSWORD__/${REDIS_PASSWORD}/g" /etc/redis/redis-slave.conf
  # 设置初始 replicaof（Sentinel 会在故障转移时调整）
  echo "replicaof ${SENTINEL_MASTER_HOST:-redis-1} 6379" >> /etc/redis/redis-slave.conf
  echo "masterauth ${REDIS_PASSWORD}" >> /etc/redis/redis-slave.conf
fi

exec "$@"
```

赋予执行权限：

```bash
chmod +x conf/sentinel-entrypoint.sh
```

`scripts/healthcheck.sh`（自定义主从状态检查）：

```bash
#!/bin/sh
set -e

# 主节点健康检查
role=$(redis-cli -a "$REDIS_PASSWORD" ping 2>/dev/null)
if [ "$role" != "PONG" ]; then
  echo "redis ping failed"
  exit 1
fi

# 检查 INFO replication
repl_info=$(redis-cli -a "$REDIS_PASSWORD" INFO replication 2>/dev/null)
role=$(echo "$repl_info" | grep "^role:" | cut -d: -f2 | tr -d '[:space:]')

if [ "$ROLE_EXPECTED" = "master" ] && [ "$role" != "master" ]; then
  echo "expected master but got $role"
  exit 1
fi

if [ "$ROLE_EXPECTED" = "slave" ]; then
  if [ "$role" != "slave" ]; then
    echo "expected slave but got $role"
    exit 1
  fi
  # 检查主从连接状态
  status=$(echo "$repl_info" | grep "^master_link_status:" | cut -d: -f2 | tr -d '[:space:]')
  if [ "$status" != "up" ]; then
    echo "master link down"
    exit 1
  fi
fi

exit 0
```

`docker-compose.prod.yml`：

```yaml
# docker-compose.prod.yml
version: "3.9"

networks:
  redis-net:
    driver: bridge
  monitor-net:
    driver: bridge

volumes:
  redis-1-data:
  redis-2-data:
  redis-3-data:
  prometheus-data:
  grafana-data:

x-redis-common: &redis-common
  image: redis:8.2-alpine
  restart: unless-stopped
  command: ["redis-server", "/etc/redis/redis-master.conf"]
  environment: &redis-env
    REDIS_PASSWORD: ${REDIS_PASSWORD:-ProdStrongPass2026}
  entrypoint: ["/etc/redis/sentinel-entrypoint.sh"]
  volumes:
    - ./conf/redis-master.conf:/etc/redis/redis-master.conf:ro
    - ./conf/redis-slave.conf:/etc/redis/redis-slave.conf:ro
    - ./conf/sentinel-entrypoint.sh:/etc/redis/sentinel-entrypoint.sh:ro
    - ./scripts/healthcheck.sh:/usr/local/bin/healthcheck.sh:ro
  networks:
    - redis-net
  sysctls:
    net.core.somaxconn: 1024
  ulimits:
    nofile:
      soft: 10032
      hard: 10032
  deploy:
    resources:
      limits:
        cpus: "2"
        memory: 3G
      reservations:
        cpus: "1"
        memory: 2G

services:
  redis-1:
    <<: *redis-common
    container_name: redis-1
    hostname: redis-1
    ports:
      - "6379:6379"
    volumes:
      - redis-1-data:/data
      - ./conf/redis-master.conf:/etc/redis/redis-master.conf:ro
      - ./conf/sentinel-entrypoint.sh:/etc/redis/sentinel-entrypoint.sh:ro
      - ./scripts/healthcheck.sh:/usr/local/bin/healthcheck.sh:ro
    environment:
      <<: *redis-env
      ROLE_EXPECTED: master
    command: ["redis-server", "/etc/redis/redis-master.conf"]
    healthcheck:
      test: ["CMD-SHELL", "/usr/local/bin/healthcheck.sh"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 15s

  redis-2:
    <<: *redis-common
    container_name: redis-2
    hostname: redis-2
    ports:
      - "6380:6379"
    volumes:
      - redis-2-data:/data
      - ./conf/redis-master.conf:/etc/redis/redis-master.conf:ro
      - ./conf/redis-slave.conf:/etc/redis/redis-slave.conf:ro
      - ./conf/sentinel-entrypoint.sh:/etc/redis/sentinel-entrypoint.sh:ro
      - ./scripts/healthcheck.sh:/usr/local/bin/healthcheck.sh:ro
    environment:
      <<: *redis-env
      ROLE_EXPECTED: slave
      SENTINEL_MASTER_HOST: redis-1
    command: ["redis-server", "/etc/redis/redis-slave.conf"]
    depends_on:
      redis-1:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "/usr/local/bin/healthcheck.sh"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 15s

  redis-3:
    <<: *redis-common
    container_name: redis-3
    hostname: redis-3
    ports:
      - "6381:6379"
    volumes:
      - redis-3-data:/data
      - ./conf/redis-master.conf:/etc/redis/redis-master.conf:ro
      - ./conf/redis-slave.conf:/etc/redis/redis-slave.conf:ro
      - ./conf/sentinel-entrypoint.sh:/etc/redis/sentinel-entrypoint.sh:ro
      - ./scripts/healthcheck.sh:/usr/local/bin/healthcheck.sh:ro
    environment:
      <<: *redis-env
      ROLE_EXPECTED: slave
      SENTINEL_MASTER_HOST: redis-1
    command: ["redis-server", "/etc/redis/redis-slave.conf"]
    depends_on:
      redis-1:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "/usr/local/bin/healthcheck.sh"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 15s

  sentinel-1:
    <<: *redis-common
    image: redis:8.2-alpine
    container_name: sentinel-1
    hostname: sentinel-1
    ports:
      - "26379:26379"
    command: ["redis-sentinel", "/etc/redis/sentinel.conf"]
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD:-ProdStrongPass2026}
      SENTINEL_MASTER_HOST: redis-1
    volumes:
      - ./conf/sentinel.conf:/etc/redis/sentinel.conf:ro
      - ./conf/sentinel-entrypoint.sh:/etc/redis/sentinel-entrypoint.sh:ro
    entrypoint: ["/etc/redis/sentinel-entrypoint.sh"]
    depends_on:
      redis-1:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -p 26379 ping | grep -q PONG"]
      interval: 10s
      timeout: 3s
      retries: 5

  sentinel-2:
    <<: *redis-common
    image: redis:8.2-alpine
    container_name: sentinel-2
    hostname: sentinel-2
    ports:
      - "26380:26379"
    command: ["redis-sentinel", "/etc/redis/sentinel.conf"]
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD:-ProdStrongPass2026}
      SENTINEL_MASTER_HOST: redis-1
    volumes:
      - ./conf/sentinel.conf:/etc/redis/sentinel.conf:ro
      - ./conf/sentinel-entrypoint.sh:/etc/redis/sentinel-entrypoint.sh:ro
    entrypoint: ["/etc/redis/sentinel-entrypoint.sh"]
    depends_on:
      redis-1:
        condition: service_healthy

  sentinel-3:
    <<: *redis-common
    image: redis:8.2-alpine
    container_name: sentinel-3
    hostname: sentinel-3
    ports:
      - "26381:26379"
    command: ["redis-sentinel", "/etc/redis/sentinel.conf"]
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD:-ProdStrongPass2026}
      SENTINEL_MASTER_HOST: redis-1
    volumes:
      - ./conf/sentinel.conf:/etc/redis/sentinel.conf:ro
      - ./conf/sentinel-entrypoint.sh:/etc/redis/sentinel-entrypoint.sh:ro
    entrypoint: ["/etc/redis/sentinel-entrypoint.sh"]
    depends_on:
      redis-1:
        condition: service_healthy

  redis-exporter-1:
    image: oliver006/redis_exporter:latest
    container_name: redis-exporter-1
    restart: unless-stopped
    environment:
      REDIS_ADDR: redis://redis-1:6379
      REDIS_PASSWORD: ${REDIS_PASSWORD:-ProdStrongPass2026}
      REDIS_EXPORTER_LOG_FORMAT: json
    ports:
      - "9121:9121"
    networks:
      - redis-net
      - monitor-net
    depends_on:
      redis-1:
        condition: service_healthy

  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    restart: unless-stopped
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./prometheus/alert.rules.yml:/etc/prometheus/alert.rules.yml:ro
      - prometheus-data:/prometheus
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--storage.tsdb.retention.time=15d"
      - "--web.enable-lifecycle"
    networks:
      - monitor-net

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_USER: ${GRAFANA_USER:-admin}
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-admin}
      GF_INSTALL_PLUGINS: "redis-datasource"
    volumes:
      - grafana-data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    networks:
      - monitor-net
    depends_on:
      - prometheus
```

`prometheus/prometheus.yml`：

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - alert.rules.yml

scrape_configs:
  - job_name: redis
    static_configs:
      - targets:
          - redis-exporter-1:9121
        labels:
          instance: redis-1

  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
```

`prometheus/alert.rules.yml`：

```yaml
groups:
  - name: redis
    rules:
      - alert: RedisDown
        expr: redis_up == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Redis {{ $labels.instance }} is down"

      - alert: RedisMemoryHigh
        expr: redis_memory_used_bytes / redis_memory_max_bytes > 0.85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Redis {{ $labels.instance }} memory usage > 85%"

      - alert: RedisFragmentationHigh
        expr: redis_memory_fragmentation_ratio > 1.5
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Redis {{ $labels.instance }} fragmentation ratio > 1.5"

      - alert: RedisMasterMissing
        expr: redis_sentinel_status_master_status == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Sentinel reports master down"

      - alert: RedisEvictedKeys
        expr: increase(redis_evicted_keys_total[5m]) > 0
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Redis {{ $labels.instance }} evicting keys"
```

`grafana/provisioning/datasources/datasource.yml`：

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

启动：

```bash
# 创建 .env 文件
cat > .env <<'EOF'
REDIS_PASSWORD=ProdStrongPass2026
GRAFANA_USER=admin
GRAFANA_PASSWORD=Admin@2026
EOF

docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps

# 验证主从
docker exec -it redis-1 redis-cli -a ProdStrongPass2026 INFO replication | grep -E "role|connected_slaves"

# 验证 Sentinel
docker exec -it sentinel-1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster
docker exec -it sentinel-1 redis-cli -p 26379 SENTINEL master mymaster
docker exec -it sentinel-1 redis-cli -p 26379 SENTINEL slaves mymaster

# 故障转移测试
docker stop redis-1
sleep 30
docker exec -it sentinel-1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster

# Prometheus：http://localhost:9090
# Grafana：http://localhost:3000  （导入 Dashboard ID: 11835, 763, 12776）
```

## 五、Cluster 部署

### 5.1 架构

```
              ┌─────────────────────────────────────────┐
              │           Redis Cluster (3 主 3 从)      │
              │                                          │
              │  ┌───────┐  ┌───────┐  ┌───────┐        │
              │  │ M1    │  │ M2    │  │ M3    │        │
              │  │ 7001  │  │ 7002  │  │ 7003  │        │
              │  │ 槽0-  │  │ 槽5461-│  │ 槽10923-│       │
              │  │ 5460  │  │ 10922 │  │ 16383 │        │
              │  └───┬───┘  └───┬───┘  └───┬───┘        │
              │      │          │          │             │
              │  ┌───▼───┐  ┌───▼───┐  ┌───▼───┐        │
              │  │ S1    │  │ S2    │  │ S3    │        │
              │  │ 7004  │  │ 7005  │  │ 7006  │        │
              │  │(M1从) │  │(M2从) │  │(M3从) │        │
              │  └───────┘  └───────┘  └───────┘        │
              │                                          │
              │  Gossip 端口 17001-17006                  │
              └─────────────────────────────────────────┘
```

### 5.2 Cluster Compose

`docker-compose.cluster.yml`：

```yaml
# docker-compose.cluster.yml
version: "3.9"

networks:
  redis-cluster:
    driver: bridge

volumes:
  redis-cluster-data-1:
  redis-cluster-data-2:
  redis-cluster-data-3:
  redis-cluster-data-4:
  redis-cluster-data-5:
  redis-cluster-data-6:

x-cluster-common: &cluster-common
  image: redis:8.2-alpine
  restart: unless-stopped
  command: >
    redis-server
    --port 7000
    --cluster-enabled yes
    --cluster-config-file nodes.conf
    --cluster-node-timeout 5000
    --appendonly yes
    --appendfsync everysec
    --maxmemory 512mb
    --maxmemory-policy allkeys-lru
    --requirepass ${REDIS_PASSWORD:-ClusterPass2026}
    --masterauth ${REDIS_PASSWORD:-ClusterPass2026}
    --io-threads 2
    --io-threads-do-reads yes
    --protected-mode yes
    --bind 0.0.0.0
  networks:
    - redis-cluster
  sysctls:
    net.core.somaxconn: 1024

services:
  redis-node-1:
    <<: *cluster-common
    container_name: redis-node-1
    hostname: redis-node-1
    ports:
      - "7001:7000"
      - "17001:17000"
    volumes:
      - redis-cluster-data-1:/data

  redis-node-2:
    <<: *cluster-common
    container_name: redis-node-2
    hostname: redis-node-2
    ports:
      - "7002:7000"
      - "17002:17000"
    volumes:
      - redis-cluster-data-2:/data

  redis-node-3:
    <<: *cluster-common
    container_name: redis-node-3
    hostname: redis-node-3
    ports:
      - "7003:7000"
      - "17003:17000"
    volumes:
      - redis-cluster-data-3:/data

  redis-node-4:
    <<: *cluster-common
    container_name: redis-node-4
    hostname: redis-node-4
    ports:
      - "7004:7000"
      - "17004:17000"
    volumes:
      - redis-cluster-data-4:/data

  redis-node-5:
    <<: *cluster-common
    container_name: redis-node-5
    hostname: redis-node-5
    ports:
      - "7005:7000"
      - "17005:17000"
    volumes:
      - redis-cluster-data-5:/data

  redis-node-6:
    <<: *cluster-common
    container_name: redis-node-6
    hostname: redis-node-6
    ports:
      - "7006:7000"
      - "17006:17000"
    volumes:
      - redis-cluster-data-6:/data

  # 集群初始化器：一次性运行后退出
  redis-cluster-init:
    image: redis:8.2-alpine
    container_name: redis-cluster-init
    depends_on:
      - redis-node-1
      - redis-node-2
      - redis-node-3
      - redis-node-4
      - redis-node-5
      - redis-node-6
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        echo "Waiting for nodes to be ready..."
        for host in redis-node-1 redis-node-2 redis-node-3 redis-node-4 redis-node-5 redis-node-6; do
          until redis-cli -h $$host -p 7000 -a ${REDIS_PASSWORD:-ClusterPass2026} --no-auth-warning ping >/dev/null 2>&1; do
            echo "  $$host not ready, retry..."
            sleep 2
          done
          echo "  $$host ready"
        done
        echo "Creating cluster..."
        redis-cli --cluster create \
          redis-node-1:7000 redis-node-2:7000 redis-node-3:7000 \
          redis-node-4:7000 redis-node-5:7000 redis-node-6:7000 \
          --cluster-replicas 1 \
          --cluster-yes \
          -a ${REDIS_PASSWORD:-ClusterPass2026}
        echo "Cluster created."
        redis-cli -h redis-node-1 -p 7000 -a ${REDIS_PASSWORD:-ClusterPass2026} --no-auth-warning CLUSTER INFO
    networks:
      - redis-cluster
    restart: "no"

  redis-cluster-exporter:
    image: oliver006/redis_exporter:latest
    container_name: redis-cluster-exporter
    restart: unless-stopped
    environment:
      REDIS_ADDR: redis://redis-node-1:7000
      REDIS_PASSWORD: ${REDIS_PASSWORD:-ClusterPass2026}
      REDIS_EXPORTER_INCL_SYSTEM_METRICS: "true"
    ports:
      - "9122:9121"
    networks:
      - redis-cluster
```

启动：

```bash
docker compose -f docker-compose.cluster.yml up -d
docker compose -f docker-compose.cluster.yml logs -f redis-cluster-init

# 验证集群
docker exec -it redis-node-1 redis-cli -p 7000 -a ClusterPass2026 -c CLUSTER INFO
docker exec -it redis-node-1 redis-cli -p 7000 -a ClusterPass2026 -c CLUSTER NODES
docker exec -it redis-node-1 redis-cli -p 7000 -a ClusterPass2026 -c CLUSTER SLOTS

# 测试集群读写（-c 启用自动重定向）
docker exec -it redis-node-1 redis-cli -p 7000 -a ClusterPass2026 -c SET user:1001 "hello"
docker exec -it redis-node-1 redis-cli -p 7000 -a ClusterPass2026 -c GET user:1001

# 模拟主节点故障（自动故障转移）
docker stop redis-node-1
sleep 10
docker exec -it redis-node-2 redis-cli -p 7000 -a ClusterPass2026 CLUSTER NODES
```

## 六、容器安全

### 6.1 非 root 运行

```dockerfile
# Dockerfile.custom-redis
FROM redis:8.2-alpine

# 创建非 root 用户（redis 镜像内置 redis:redis，UID 999）
USER redis

# 但容器内 /data 目录需可写
# Compose 中通过 user 字段指定
```

Compose 中：

```yaml
services:
  redis:
    image: redis:8.2-alpine
    user: "redis:redis"        # 内置 redis 用户 UID 999
    read_only: true            # 根文件系统只读
    tmpfs:
      - /tmp
    cap_drop:
      - ALL                    # 丢弃所有 Linux capabilities
    cap_add:
      - CHOWN
      - SETUID
      - SETGID
    security_opt:
      - no-new-privileges:true
```

### 6.2 网络隔离

```yaml
networks:
  frontend:        # 对外暴露
    driver: bridge
  backend:         # 内部通信
    driver: bridge
    internal: true  # 完全隔离，无法访问外网

services:
  redis:
    image: redis:8.2-alpine
    networks:
      - backend     # 仅在内网，外部无法直接访问
  django:
    networks:
      - frontend
      - backend
  nginx:
    networks:
      - frontend
    ports:
      - "80:80"
```

### 6.3 密码与密钥管理

```bash
# .env 文件（不提交到 Git）
REDIS_PASSWORD=ProdStrongPass2026
GRAFANA_PASSWORD=Admin@2026

# Docker Secrets（更安全，多节点 Swarm 推荐）
echo "ProdStrongPass2026" | docker secret create redis_password -

# Compose 引用
```

```yaml
services:
  redis:
    secrets:
      - redis_password
    command: >
      sh -c '
        REDIS_PASSWORD=$$(cat /run/secrets/redis_password)
        exec redis-server --requirepass "$$REDIS_PASSWORD" --masterauth "$$REDIS_PASSWORD"
      '
secrets:
  redis_password:
    external: true
```

### 6.4 安全清单

| 项 | 推荐值 | 说明 |
|------|------|------|
| `protected-mode` | yes | 拒绝远程无密码访问 |
| `requirepass` | 强密码（>16 位） | 含大小写数字符号 |
| `bind` | 内网 IP | 不要 `0.0.0.0` 暴露公网 |
| `rename-command` | FLUSHDB/FLUSHALL/DEBUG/KEYS | 禁用危险命令 |
| `maxmemory` | 物理内存 60% | 防止 OOM |
| `maxclients` | 10000 | 限制连接数 |
| 容器 user | 非 root | `user: "redis:redis"` |
| `read_only` | true | 根文件系统只读 |
| `cap_drop` | ALL | 丢弃所有 capabilities |
| 网络 | internal | 内部网络隔离 |
| 端口 | 不暴露 6379 到公网 | 仅通过应用层访问 |

## 七、健康检查详解

### 7.1 健康检查类型

```
健康检查层次：

L1 进程存活：    docker ps                       (容器是否运行)
L2 端口响应：    redis-cli ping                   (TCP+RESP)
L3 鉴权可用：    redis-cli -a $pwd ping            (密码正确)
L4 主从状态：    INFO replication                  (角色与同步)
L5 持久化状态：  INFO persistence / LASTSAVE       (RDB/AOF 健康)
L6 集群状态：    CLUSTER INFO / CLUSTER NODES      (集群完整性)
L7 业务指标：    自定义业务 key 是否可读写          (端到端)
```

### 7.2 各形态健康检查脚本

`scripts/healthcheck-standalone.sh`：

```bash
#!/bin/sh
set -e
if ! redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping | grep -q PONG; then
  echo "ping failed"; exit 1
fi
# AOF 状态检查
status=$(redis-cli -a "$REDIS_PASSWORD" --no-auth-warning INFO persistence | grep aof_last_bgrewrite_status | cut -d: -f2 | tr -d '[:space:]')
if [ "$status" = "err" ]; then
  echo "aof rewrite error"; exit 1
fi
exit 0
```

`scripts/healthcheck-cluster.sh`：

```bash
#!/bin/sh
set -e
PWD="$REDIS_PASSWORD"
if ! redis-cli -a "$PWD" --no-auth-warning -p 7000 ping | grep -q PONG; then
  echo "ping failed"; exit 1
fi
state=$(redis-cli -a "$PWD" --no-auth-warning -p 7000 cluster info | grep cluster_state | cut -d: -f2 | tr -d '[:space:]')
if [ "$state" != "ok" ]; then
  echo "cluster state: $state"; exit 1
fi
slots=$(redis-cli -a "$PWD" --no-auth-warning -p 7000 cluster info | grep cluster_slots_ok | cut -d: -f2 | tr -d '[:space:]')
if [ "$slots" != "16384" ]; then
  echo "slots not complete: $slots"; exit 1
fi
exit 0
```

## 八、Redis vs PostgreSQL Docker 部署对比

| 维度 | Redis | PostgreSQL |
|------|-------|------------|
| 官方镜像 | `redis:8.2-alpine` (~15MB) | `postgres:16-alpine` (~80MB) |
| 数据目录 | `/data` | `/var/lib/postgresql/data` |
| 配置文件 | `redis.conf`（命令式） | `postgresql.conf` + `pg_hba.conf`（声明式） |
| 持久化 | RDB / AOF / 混合 | WAL（默认）+ 归档 |
| 端口 | 6379 | 5432 |
| 默认认证 | requirepass（可选） | 用户名密码（强制） |
| 主从复制 | 异步（异步复制） | 流复制（同步/异步） |
| 高可用 | Sentinel / Cluster | Patroni / repmgr / stolon |
| 分片 | Cluster（哈希槽） | Citus / pg_partman |
| 容器初始化 | `redis-server` 启动 | `initdb` + `POSTGRES_DB/USER` 首启 |
| 健康检查 | `redis-cli ping` | `pg_isready` |
| 监控导出器 | redis-exporter (9121) | postgres-exporter (9187) |
| 备份 | RDB 文件复制 | `pg_basebackup` / `pg_dump` |
| 数据卷大小 | 受 maxmemory 控制 | 取决于数据量 |
| 内存限制 | 容易（maxmemory） | 通过 shared_buffers + cgroup |
| 多实例 | 多 db（16） | 多 database（CREATE DATABASE） |
| Compose 复杂度 | Sentinel 4 容器 / Cluster 6+容器 | Patroni + etcd + haproxy（更复杂） |

## 九、生产部署 Checklist

### 9.1 部署前

- [ ] 选择镜像：`redis:8.2-alpine`（纯 KV）或 `redis/redis-stack-server`（含模块）
- [ ] 设置强密码：`requirepass` + `masterauth`
- [ ] 配置 `maxmemory` 不超过物理内存 60%，留出 fork buffer
- [ ] 开启 AOF（`appendonly yes` + `appendfsync everysec`）+ 混合持久化
- [ ] 设置 `io-threads 4` + `io-threads-do-reads yes`
- [ ] 禁用危险命令：`rename-command FLUSHDB ""` 等
- [ ] 配置 `lazyfree-lazy-*` 全部开启
- [ ] 设置 `activedefrag yes` 自动碎片整理

### 9.2 部署中

- [ ] 使用 named volume 持久化 `/data`
- [ ] 配置 `healthcheck`（不只是 ping，含 replication/cluster 检查）
- [ ] `user: "redis:redis"` 非 root 运行
- [ ] `read_only: true` + `tmpfs: [/tmp]`
- [ ] `cap_drop: ALL` + 最小 `cap_add`
- [ ] `security_opt: no-new-privileges:true`
- [ ] 内部网络 `internal: true` 隔离
- [ ] `sysctls: net.core.somaxconn=1024`
- [ ] `ulimits: nofile` 提升（10032）
- [ ] `restart: unless-stopped`
- [ ] 资源限制：`cpus` / `memory`

### 9.3 部署后

- [ ] 验证 `INFO server`、`INFO memory`、`INFO replication`
- [ ] 配置 Prometheus + redis-exporter
- [ ] Grafana 导入 Dashboard 11835 / 763 / 12776
- [ ] 配置告警：RedisDown / MemoryHigh / FragmentationHigh / EvictedKeys
- [ ] 测试故障转移：`docker stop redis-master`，观察 Sentinel 是否切主
- [ ] 配置 RDB 定时备份（crontab：`BGSAVE` + `cp dump.rdb`）
- [ ] 配置日志收集（`--loglevel notice`）

## 十、典型踩坑表

| 问题 | 原因 | 解决 |
|------|------|------|
| Sentinel 容器内无法解析主机名 | `sentinel resolve-hostnames no` | 改为 `yes`，使用 hostname |
| Cluster 总线端口未开放 | 只映射了 7001，未映射 17001 | Compose 中同时映射 17000-17005 |
| 容器重启后 Cluster 失联 | `cluster-config-file` 在临时目录 | 挂载 named volume 持久化 |
| AOF 文件损坏无法启动 | 容器强制 kill | `redis-check-aof --fix appendonly.aof` |
| `bind 0.0.0.0` 触发 `protected-mode` | 未设密码 | 必须设 `requirepass` |
| Fork 慢导致 BGSAVE 卡顿 | 大内存 + 容器内存限制 | 关闭 THP：`echo never > /sys/kernel/mm/transparent_hugepage/enabled` |
| Sentinel 误判主下线 | `down-after-milliseconds` 过小 | 调大至 30s+ |
| Cluster slot 不全 | 初始化时节点未全部就绪 | 等待所有节点 ready 后再 `--cluster create` |
| 容器内 `redis-cli` 无法连接 | `--protected-mode yes` + 远程访问 | 设置 `requirepass` + `bind 内网 IP` |
| 监控指标抓不到 | exporter 网络不通 | exporter 加入 redis 网络 |
| `EVALSHA` 跨节点失败 | 脚本未在所有节点加载 | Cluster 模式下脚本需带 `{tag}` 路由到同槽 |
| 容器 OOM kill | `maxmemory` 与容器 `memory` 配置不匹配 | 容器 memory > Redis maxmemory + fork buffer（30%） |

## 小思考题帮你巩固

1. **单机、Sentinel、Cluster 三种容器形态下，`redis:8.2-alpine` 镜像本身有何不同？**（提示：同一个镜像跑不同配置（redis.conf/sentinel.conf/cluster-enabled），形态由配置决定，不是三个镜像，见一/三/四/五。）
2. **为什么容器里 Redis 密码要经环境变量/Secret 注入，而不是写死在 Dockerfile `ENV`？**（提示：写死会烙进镜像层可被 docker history 看到；env/Secret 运行期注入可换不可（见 6.3）。）
3. **生产 Sentinel Compose 为什么通常 3 个哨兵而不 1 个？quorum 该怎么设？**（提示：≥3 才能过半投票防脑裂；quorum = 判定客观下线所需数（如 2），leader 需过半 max(quorum, n/2+1)，见 4。）
4. **容器里 Redis 不做持久化会怎样？生产如何做容器化持久化？**（提示：容器删除数据即失；挂 named volume 到 /data 由 Redis 自己写 RDB/AOF，见 3/4 的 volumes。）
5. **`redis/redis-stack` 与 `redis:8.2` 的主要差异是什么？现在选哪个？**（提示：Stack 带模块全家桶；Redis 8.0 起 Search/JSON/TimeSeries/Bloom 已内置，纯 Redis 场景用官方 `redis:8.2-alpine`，见 1.1 版本注。）

<details>
<summary>参考答案（点击展开）</summary>

1. 三种形态共用同一个官方镜像（redis:8.2-alpine），区别在于**启动参数/挂载配置**：单机=裸跑；Sentinel=以 `sentinel.conf` 启动（`redis-server sentinel.conf --sentinel`）；Cluster=开 `cluster-enabled yes` 并配置槽位。镜像内部还是同一个 redis-server 二进制。
2. 写死进 Dockerfile 的 `ENV REDIS_PASSWORD=...` 会留在镜像层元数据（`docker history`/`docker inspect` 可见），且所有复用该镜像的容器密码一致、无法按环境区分。正确做法：compose `environment: REDIS_PASSWORD=${REDIS_PASSWORD}`，密码放在宿主 `.env`/Docker Secret 运行期注入。
3. 哨兵要能"过半投票"判定客观下线并选出 Leader，至少 3 个才满足 `max(quorum, n/2+1)` 过半（2 个哨兵在分区时可能各投各的→脑裂两个主）。生产惯例 3 哨兵、`sentinel monitor mymaster ... quorum=2`。
4. 不挂卷的话，容器删除/重建后所有数据（含 RDB/AOF）随可写层消失。生产用 named volume（如 `redis-data:/data`），Redis 自身按配置写 `dump.rdb`/`appendonlydir` 进 /data；重建容器自动复用卷（见 compose volumes）。
5. Stack 镜像预装 RedisGraph 等**额外**模块全家桶、体积 ~400MB；Redis 8.0 起核心模块（Search/JSON/TimeSeries/Bloom）已内置官方镜像，因此新环境优先 `redis:8.2-alpine`（体积小、官方维护）；只有需要 Graph 等 Stack 独有模块时才用 Stack（见 1.1 版本注）。

</details>

## 📖 深入阅读

- [Redis Docker Hub 官方镜像](https://hub.docker.com/_/redis)
- [redis/redis-stack 镜像](https://hub.docker.com/r/redis/redis-stack)
- [Redis 配置文件参考](https://redis.io/topics/config)
- [Redis Sentinel 文档](https://redis.io/docs/management/sentinel/)
- [Redis Cluster 文档](https://redis.io/docs/management/scaling/)
- [redis_exporter GitHub](https://github.com/oliver006/redis_exporter)
- 本手册 [05 复制与高可用](../05_复制与高可用/05_复制与高可用.md)
- 本手册 [06 集群与分片](../06_集群与分片/06_集群与分片.md)
- 本手册 [12 安全与运维](../12_安全与运维/12_安全与运维.md)

## 本章小结

1. 镜像选型：纯 KV 用 `redis:8.2-alpine`；需要 RediSearch/JSON/Bloom 等模块用 `redis/redis-stack-server`，RedisInsight 仅用于开发。
2. 单机开发用最简 Compose（redis + redis-insight + healthcheck）；生产环境必须 Sentinel（3 哨兵 + 1 主 2 从）或 Cluster（3 主 3 从）。
3. 生产 `redis.conf` 必含：`maxmemory`、`maxmemory-policy`、`appendonly yes` + `aof-use-rdb-preamble yes`、`io-threads 4`、`lazyfree-lazy-*`、`rename-command`。
4. 容器安全三件套：非 root 运行 + 只读根文件系统 + 网络隔离；密码用 Docker Secrets 或 `.env`（不入库）。
5. 健康检查不只看 PING：主从模式查 `INFO replication`，Cluster 模式查 `CLUSTER INFO` + slot 完整性。
6. 监控栈标配 `redis-exporter + Prometheus + Grafana`，Grafana 推荐导入 Dashboard ID：11835、763、12776。
7. Redis 与 PostgreSQL 容器化差异：Redis 单文件配置、多 db、轻量；PostgreSQL 多文件、强认证、复制与高可用方案更重。

## 下一步导航

- 学完部署，下一步做全栈项目 → [15 全栈项目实战](../15_全栈项目实战/15_全栈项目实战.md)
- 回顾集群原理 → [06 集群与分片](../06_集群与分片/06_集群与分片.md)
- 回顾复制与高可用 → [05 复制与高可用](../05_复制与高可用/05_复制与高可用.md)
- 集成 Node.js/Django → [13 Node.js 与 Django 集成 Redis](../13_Node.js与Django集成Redis/13_Node.js与Django集成Redis.md)
