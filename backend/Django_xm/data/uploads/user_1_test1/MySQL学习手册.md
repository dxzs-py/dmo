# 零基础到进阶的MySQL系统学习手册

## 前言

本手册旨在帮助用户系统学习MySQL数据库，从基础操作到高级特性，循序渐进地掌握MySQL的核心知识点。手册中的所有实战案例均基于MySQL 8.0版本，可直接复制运行。

**手册特色**：

- **原理驱动**：每个知识点先讲底层原理，再给实战SQL，知其然更知其所以然
- **场景导向**：结合真实业务场景，说明何时用、怎么用、为什么用
- **对比选型**：关键决策点提供对比表格，帮助做出正确选择

## 准备工作

在开始学习前，请确保你已经安装了MySQL 8.0。可以通过以下命令检查MySQL版本：

```sql
SELECT VERSION();
```

***

## 模块一：基础操作

### 1.1 连接MySQL

**功能说明**：连接到MySQL服务器。

**实战案例**：

```bash
# 命令行连接
mysql -u root -p

# 指定主机和端口
mysql -h 127.0.0.1 -P 3306 -u root -p

# 指定数据库连接
mysql -u root -p -D test_db
```

### 1.2 数据库操作

**功能说明**：创建、查看、删除数据库。

**实战案例**：

```sql
CREATE DATABASE test_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

SHOW DATABASES;

USE test_db;

DROP DATABASE IF EXISTS test_db;
```

### 1.3 表操作

**功能说明**：创建、查看、修改、删除表。

**实战案例**：

```sql
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    age INT,
    email VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

SHOW TABLES;

DESCRIBE users;

ALTER TABLE users ADD COLUMN phone VARCHAR(20);

DROP TABLE IF EXISTS users;
```

### 1.4 关闭连接

```bash
EXIT;

QUIT;
```

***

## 模块二：核心架构原理

> 理解MySQL的架构原理，是正确使用索引、优化SQL、排查问题的根基。

### 2.1 MySQL整体架构

MySQL采用**分层架构**，从上到下分为四层：

```
┌─────────────────────────────────────────────────────────┐
│                    客户端连接层                           │
│  连接管理、认证授权、连接池（最大连接数max_connections）    │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    Server层（核心服务层）                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ SQL接口   │ │ 解析器   │ │ 优化器   │ │ 执行器   │  │
│  │(DML/DDL) │ │(词法/语法)│ │(选择索引) │ │(调用引擎) │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │ 查询缓存（8.0已移除）│ Buffer Pool（InnoDB核心） │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    存储引擎层                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ InnoDB   │ │ MyISAM   │ │ Memory   │ │ Archive  │  │
│  │(默认引擎)│ │          │ │          │ │          │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    文件系统层                             │
│  数据文件(.ibd)、日志文件(redo log、undo log、binlog)     │
└─────────────────────────────────────────────────────────┘
```

**SQL执行流程**：

```
客户端发送SQL
    │
    ▼
连接器（认证、权限校验）
    │
    ▼
解析器（词法分析 → 语法分析 → 生成AST）
    │
    ▼
优化器（选择执行计划、选择索引）
    │
    ▼
执行器（校验权限 → 调用存储引擎接口）
    │
    ▼
存储引擎（读写数据 → 返回结果）
```

### 2.2 InnoDB存储引擎

InnoDB是MySQL 5.5+的默认存储引擎，支持事务、行级锁、外键、MVCC。

**InnoDB内存架构**：

```
┌─────────────────────────────────────────────────────────┐
│                  InnoDB内存结构                          │
│                                                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │              Buffer Pool（缓冲池）                  │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐            │  │
│  │  │数据页    │ │索引页    │ │自适应哈希 │            │  │
│  │  │(Data    │ │(Index   │ │索引(AHI) │            │  │
│  │  │ Page)   │ │ Page)   │ │         │            │  │
│  │  └─────────┘ └─────────┘ └─────────┘            │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐            │  │
│  │  │Undo页   │ │插入缓冲  │ │锁信息    │            │  │
│  │  │         │ │(Change  │ │         │            │  │
│  │  │         │ │ Buffer) │ │         │            │  │
│  │  └─────────┘ └─────────┘ └─────────┘            │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────┐  ┌────────────────────────────────┐  │
│  │ Log Buffer   │  │ Additional Memory Pool         │  │
│  │ (日志缓冲区)  │  │ (额外内存池)                    │  │
│  └──────────────┘  └────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**Buffer Pool核心参数**：

| 参数                              | 默认值   | 说明                     |
| ------------------------------- | ----- | ---------------------- |
| innodb\_buffer\_pool\_size      | 128MB | 缓冲池大小，建议设为物理内存的60%-80% |
| innodb\_buffer\_pool\_instances | 1     | 缓冲池实例数，≥1GB时建议设为4-8    |
| innodb\_old\_blocks\_pct        | 37    | Old sublist占比          |
| innodb\_old\_blocks\_time       | 1000  | Old页存活时间(ms)           |

```sql
SHOW STATUS LIKE 'Innodb_buffer_pool%';

SHOW VARIABLES LIKE 'innodb_buffer_pool%';

SET GLOBAL innodb_buffer_pool_size = 4294967296;
```

**Buffer Pool LRU算法**：

InnoDB对标准LRU进行了优化，采用**改良版LRU**，将链表分为Young区和Old区，防止全表扫描污染热数据：

```
Buffer Pool LRU结构：
┌──────────────────────────────────────────────────┐
│  Young区（热数据，占5/8）    │  Old区（冷数据，占3/8）│
│  ┌───┬───┬───┬───┬───┬───┬─┴─┬───┬───┬───┬───┐  │
│  │MRU←                              →LRU │  │
│  └───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┘  │
│  ▲新数据从Midpoint插入                            │
│  在Old区存活超过innodb_old_blocks_time后           │
│  再次被访问才移入Young区                           │
└──────────────────────────────────────────────────┘
```

### 2.3 InnoDB磁盘架构

```
┌─────────────────────────────────────────────────────────┐
│                  InnoDB磁盘结构                          │
│                                                          │
│  系统表空间（ibdata1）                                    │
│  ├── 数据字典（表结构、索引定义）                         │
│  ├── 双写缓冲（Doublewrite Buffer）                      │
│  ├── Change Buffer                                       │
│  └── Undo Log（5.6+可独立表空间）                        │
│                                                          │
│  独立表空间（.ibd文件，每表一个）                         │
│  ├── 数据（B+树组织的页）                                │
│  └── 索引                                                │
│                                                          │
│  Redo Log（ib_logfile0、ib_logfile1）                    │
│  ├── 环形写入，循环使用                                   │
│  └── 保证崩溃恢复的WAL日志                               │
└─────────────────────────────────────────────────────────┘
```

**页结构（InnoDB最小I/O单位，默认16KB）**：

```
┌──────────────────────────────────────────┐
│            数据页结构（16KB）              │
│  ┌────────────────────────────────────┐  │
│  │ File Header（38字节，页信息）       │  │
│  ├────────────────────────────────────┤  │
│  │ Page Header（56字节，页状态）       │  │
│  ├────────────────────────────────────┤  │
│  │ Infimum + Supremum（最小/最大记录） │  │
│  ├────────────────────────────────────┤  │
│  │ User Records（用户数据，链表组织）  │  │
│  ├────────────────────────────────────┤  │
│  │ Free Space（空闲空间）              │  │
│  ├────────────────────────────────────┤  │
│  │ Page Directory（页目录，二分查找）  │  │
│  ├────────────────────────────────────┤  │
│  │ File Trailer（8字节，校验信息）     │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

### 2.4 Doublewrite Buffer（双写缓冲）

InnoDB页大小16KB，而操作系统文件系统通常以4KB为I/O单位。如果写入16KB页时发生系统崩溃（如只写了4KB），会导致**页撕裂（partial page write）**，此时Redo Log无法恢复（因为Redo Log记录的是"对页的修改"而非完整页）。

**Doublewrite Buffer解决此问题**：

```
Doublewrite Buffer工作流程：
┌──────────┐     1.先写双写缓冲      ┌──────────────────────┐
│ Buffer   │ ─────────────────────► │ Doublewrite Buffer   │
│ Pool脏页  │                        │ （共享表空间中，2MB）  │
│          │                        │ 连续的128个页          │
│          │     2.再写数据文件      └──────────────────────┘
│          │ ─────────────────────►  数据文件（.ibd）
└──────────┘

崩溃恢复流程：
1. 检查数据文件中页的checksum
2. 如果checksum不匹配（页撕裂）→ 从Doublewrite Buffer恢复完整页
3. 再用Redo Log重放该页的修改

┌──────────────────────────────────────────────────┐
│  Doublewrite Buffer结构（2MB = 128页 × 16KB）     │
│  ┌────────────────────────────────────────────┐  │
│  │ Batch 1：64页（连续写入）                    │  │
│  ├────────────────────────────────────────────┤  │
│  │ Batch 2：64页（连续写入）                    │  │
│  └────────────────────────────────────────────┘  │
│  写入方式：顺序写，一次fsync，性能开销约5%-10%    │
└──────────────────────────────────────────────────┘
```

```sql
SHOW STATUS LIKE 'Innodb_dblwr%';

SHOW VARIABLES LIKE 'innodb_doublewrite';
```

> **注意**：如果文件系统支持原子写入（如ZFS），可关闭双写缓冲以提升性能：`SET GLOBAL innodb_doublewrite = OFF;`

### 2.5 Change Buffer（写缓冲）

当对**非唯一二级索引**页执行DML操作时，如果该页不在Buffer Pool中，InnoDB不会立即从磁盘读取该页，而是将修改缓存在Change Buffer中，等到该页被读取时再合并（merge）。

```
Change Buffer工作流程：
┌──────────┐                              ┌──────────┐
│ DML操作   │                              │ 二级索引页 │
│ INSERT   │                              │ (不在BP中) │
│ UPDATE   │                              └──────────┘
│ DELETE   │                                   ▲
└────┬─────┘                                   │
     │                                         │
     ▼                                         │
┌──────────────────┐    后台异步merge    ┌──────┴───┐
│ Change Buffer    │ ─────────────────► │ Buffer   │
│ (在Buffer Pool中) │                    │ Pool    │
│ 缓存索引修改记录    │                    │ 合并后刷盘│
└──────────────────┘                    └──────────┘

适用条件：
✅ 非唯一二级索引（唯一索引需要立即检查唯一性）
✅ 页不在Buffer Pool中
✅ 适合写多读少的场景（如日志表）

不适用场景：
❌ 唯一索引（必须立即验证唯一性）
❌ 读多写少（页频繁被读入BP，merge频繁反而降低性能）
```

```sql
SHOW STATUS LIKE 'Innodb_ibuf%';

SHOW VARIABLES LIKE 'innodb_change_buffer%';

SHOW VARIABLES LIKE 'innodb_change_buffer_max_size';
```

| 参数                                | 默认值 | 说明                                           |
| --------------------------------- | --- | -------------------------------------------- |
| innodb\_change\_buffering         | all | 缓冲类型：all/none/inserts/deletes/changes/purges |
| innodb\_change\_buffer\_max\_size | 25  | Change Buffer占Buffer Pool的最大比例(%)            |

### 2.6 自适应哈希索引（AHI）

InnoDB自动监控索引页的访问模式，如果发现某些索引页被频繁以**等值查询**方式访问，会自动为这些页构建哈希索引。

```
AHI工作原理：
┌──────────────────────────────────────────────────┐
│  B+树索引查找：需要从根→叶，3-4次页访问               │
│                                                  │
│  AHI查找：直接哈希定位，1次页访问                     │
│                                                  │
│  触发条件：                                        │
│  - 同一索引页被等值查询访问 ≥ N次（自适应阈值）         │
│  - 查询模式稳定（访问模式不频繁变化）                  │
│                                                  │
│  ┌──────────────────────────────────────────┐    │
│  │ 哈希表结构：                              │     │
│  │ key = 索引列值 + 索引ID + 页号            │      │
│  │ value = 数据页指针                        │     │
│  └──────────────────────────────────────────┘    │
│                                                  │
│  适用场景：✅ 等值查询多、✅ 内存大                  │
│  不适用：❌ 范围查询、❌ 写多读少、❌ 内存小          │
└──────────────────────────────────────────────────┘
```

```sql
SHOW STATUS LIKE 'Innodb_adaptive_hash%';

SHOW VARIABLES LIKE 'innodb_adaptive_hash_index';

SET GLOBAL innodb_adaptive_hash_index = OFF;
```

> **生产建议**：AHI在特定场景下可提升等值查询5-10倍性能，但在高并发写入时可能因哈希表锁竞争成为瓶颈。如果发现`Innodb_adaptive_hash_wait`很高，建议关闭。

### 2.7 InnoDB vs MyISAM对比

| 特性        | InnoDB     | MyISAM      |
| --------- | ---------- | ----------- |
| 事务        | ✅ ACID     | ❌           |
| 行级锁       | ✅          | ❌ 表级锁       |
| 外键        | ✅          | ❌           |
| MVCC      | ✅          | ❌           |
| 崩溃恢复      | ✅ Redo Log | ❌           |
| 全文索引      | ✅（5.6+）    | ✅           |
| COUNT(\*) | 遍历最小二级索引   | 存储行数（O(1)）  |
| 存储文件      | .ibd       | .MYD + .MYI |
| 适用场景      | OLTP（增删改多） | OLAP（查询多）   |

```sql
SHOW TABLE STATUS LIKE 'users';

ALTER TABLE users ENGINE = InnoDB;

SHOW ENGINES;
```

***

## 模块三：SQL语法

### 3.1 插入数据

```sql
INSERT INTO users (name, age, email) VALUES ('张三', 25, 'zhangsan@example.com');

INSERT INTO users (name, age, email) VALUES
('李四', 30, 'lisi@example.com'),
('王五', 28, 'wangwu@example.com');

INSERT INTO users_backup SELECT * FROM users WHERE age > 25;
```

### 3.2 查询数据

```sql
SELECT * FROM users;

SELECT name, age FROM users;

SELECT * FROM users WHERE age > 25;

SELECT * FROM users ORDER BY age DESC;

SELECT * FROM users LIMIT 10 OFFSET 20;

SELECT * FROM users WHERE name LIKE '张%';

SELECT * FROM users WHERE age BETWEEN 25 AND 30;

SELECT * FROM users WHERE age IN (25, 28, 30);

SELECT * FROM users WHERE email IS NOT NULL;
```

### 3.3 更新数据

```sql
UPDATE users SET age = 26 WHERE name = '张三';

UPDATE users SET age = age + 1 WHERE age > 25;

UPDATE users SET age = 26, email = 'new@example.com' WHERE id = 1;
```

### 3.4 删除数据

```sql
DELETE FROM users WHERE name = '张三';

DELETE FROM users;

TRUNCATE TABLE users;
```

### 3.5 聚合函数

```sql
SELECT AVG(age) AS avg_age FROM users;

SELECT MAX(age) AS max_age, MIN(age) AS min_age FROM users;

SELECT COUNT(*) AS total_users FROM users;

SELECT SUM(amount) AS total_amount FROM orders;
```

### 3.6 分组查询

```sql
SELECT age, COUNT(*) AS count FROM users GROUP BY age;

SELECT age, COUNT(*) AS count FROM users GROUP BY age HAVING count > 1;

SELECT dept_id, age, COUNT(*) AS count FROM employees GROUP BY dept_id, age;
```

### 3.7 连接查询

```sql
SELECT u.name, o.amount
FROM users u
INNER JOIN orders o ON u.id = o.user_id;

SELECT u.name, o.amount
FROM users u
LEFT JOIN orders o ON u.id = o.user_id;

SELECT u.name, o.amount
FROM users u
RIGHT JOIN orders o ON u.id = o.user_id;

SELECT e1.name AS employee, e2.name AS manager
FROM employees e1
LEFT JOIN employees e2 ON e1.manager_id = e2.id;
```

**JOIN类型对比**：

```
┌─────────────────────────────────────────────────────┐
│  INNER JOIN      LEFT JOIN      RIGHT JOIN          │
│  ┌───┬───┐      ┌───┬───┐      ┌───┬───┐          │
│  │ A │ B │      │ A │ B │      │ A │ B │          │
│  └─┬─┴─┬─┘      └─┬─┴─┬─┘      └─┬─┴─┬─┘          │
│    │   │          │     │          │   │            │
│  只取交集        左表全保留      右表全保留          │
└─────────────────────────────────────────────────────┘
```

### 3.8 子查询

```sql
SELECT * FROM users WHERE age > (SELECT AVG(age) FROM users);

SELECT * FROM users WHERE id IN (SELECT user_id FROM orders WHERE amount > 100);

SELECT * FROM users u WHERE EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id);

SELECT dept_id, avg_age
FROM (SELECT dept_id, AVG(age) AS avg_age FROM employees GROUP BY dept_id) AS dept_avg
WHERE avg_age > 28;
```

***

## 模块四：数据类型与约束

### 4.1 常用数据类型

**整数类型**：

| 类型       | 字节 | 范围（有符号）         | 范围（无符号）     | 适用场景      |
| -------- | -- | --------------- | ----------- | --------- |
| TINYINT  | 1  | -128\~127       | 0\~255      | 状态值、布尔    |
| SMALLINT | 2  | -32768\~32767   | 0\~65535    | 小范围计数     |
| INT      | 4  | -21亿\~21亿       | 0\~42亿      | 主键、数量     |
| BIGINT   | 8  | -2^63 \~ 2^63-1 | 0 \~ 2^64-1 | 大ID、金额(分) |

**字符串类型**：

| 类型         | 最大长度    | 适用场景        |
| ---------- | ------- | ----------- |
| CHAR(N)    | 255字符   | 定长字符串（如手机号） |
| VARCHAR(N) | 65535字节 | 变长字符串（如姓名）  |
| TEXT       | 64KB    | 长文本         |
| MEDIUMTEXT | 16MB    | 文章内容        |
| LONGTEXT   | 4GB     | 超大文本        |

**时间类型**：

| 类型        | 格式                  | 范围               | 适用场景            |
| --------- | ------------------- | ---------------- | --------------- |
| DATE      | YYYY-MM-DD          | 1000\~9999       | 生日              |
| TIME      | HH:MM:SS            | -838\~838小时      | 持续时间            |
| DATETIME  | YYYY-MM-DD HH:MM:SS | 1000\~9999       | 创建时间            |
| TIMESTAMP | 同上                  | 1970\~2038-01-19 | 自动更新时间（Y2K38问题） |

```sql
CREATE TABLE time_test (
    id INT PRIMARY KEY,
    created_at DATETIME,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

### 4.2 约束

```sql
CREATE TABLE products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    price DECIMAL(10,2)
);

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    email VARCHAR(100) UNIQUE,
    age INT
);

CREATE TABLE employees (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    dept_id INT,
    FOREIGN KEY (dept_id) REFERENCES departments(id) ON DELETE SET NULL
);

CREATE TABLE users_check (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    age INT CHECK (age >= 0 AND age <= 150)
);

ALTER TABLE users ADD CONSTRAINT uk_email UNIQUE (email);
ALTER TABLE employees ADD CONSTRAINT fk_dept FOREIGN KEY (dept_id) REFERENCES departments(id);
```

***

## 模块五：索引原理与优化

### 5.1 B+树索引原理

InnoDB使用**B+树**作为索引结构，这是理解MySQL性能的关键。

**B+树结构**：

```
B+树结构（3层，可存储约2000万行数据）：

                    ┌─────────────┐
                    │  根节点      │
                    │ [10 | 20 | 30] │
                    └──┬──┬──┬──┘
              ┌────────┘  │  └────────┐
              ▼           ▼           ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ 中间节点  │ │ 中间节点  │ │ 中间节点  │
        │[1|5|10]  │ │[11|15|20]│ │[21|25|30]│
        └──┬─┬─┬──┘ └──┬─┬─┬──┘ └──┬─┬─┬──┘
           │ │ │        │ │ │       │ │ │
           ▼ ▼ ▼        ▼ ▼ ▼       ▼ ▼ ▼
     ┌──────────────────────────────────────┐
     │          叶子节点（双向链表）          │
     │ [1][2][5][10] ↔ [11][13][15] ↔ ...  │
     │ 每个叶子节点 = 一个数据页（16KB）     │
     └──────────────────────────────────────┘
```

**B+树关键特性**：

| 特性        | 说明                |
| --------- | ----------------- |
| 非叶子节点只存键值 | 单个页可存更多键，树更矮      |
| 叶子节点存数据   | 所有查询都要到叶子节点，查询稳定  |
| 叶子节点双向链表  | 范围查询高效（找到起点后顺序扫描） |
| 树高度通常3层   | 3次I/O即可定位数据       |

**为什么不用B树、哈希、红黑树？**

| 数据结构 | 范围查询    | 树高度  | 磁盘I/O | 适用场景   |
| ---- | ------- | ---- | ----- | ------ |
| B+树  | ✅ 高效    | 3-4层 | 少     | 数据库索引  |
| B树   | ❌ 需中序遍历 | 较高   | 多     | 文件系统   |
| 哈希   | ❌ 不支持   | O(1) | 1次    | 等值查询   |
| 红黑树  | ❌ 需中序遍历 | 很高   | 极多    | 内存数据结构 |

### 5.2 聚簇索引与二级索引

**聚簇索引（主键索引）**：叶子节点存储**完整的行数据**，一张表只有一个。

**二级索引（非主键索引）**：叶子节点存储**主键值**，查询非索引列需要**回表**。

```
聚簇索引：                     二级索引（name列）：
┌──────────┐                  ┌──────────┐
│ id=1     │                  │ name='张三' │
│ name='张三'│                  │ id=1     │
│ age=25   │                  ├──────────┤
│ email=...│                  │ name='李四' │
├──────────┤                  │ id=2     │
│ id=2     │                  ├──────────┤
│ name='李四'│                  │ name='王五' │
│ age=30   │                  │ id=3     │
│ email=...│                  └──────────┘
└──────────┘
                  回表：先查二级索引得到id → 再查聚簇索引得到完整行
```

**覆盖索引**：如果查询的列都在二级索引中，就不需要回表。

```sql
SELECT * FROM users WHERE name = '张三';

SELECT id, name FROM users WHERE name = '张三';
```

### 5.3 索引类型与创建

```sql
CREATE UNIQUE INDEX uk_email ON users(email);

CREATE INDEX idx_name ON users(name);

CREATE INDEX idx_name_age ON users(name, age);

CREATE INDEX idx_content ON articles(content(20));

CREATE INDEX idx_created_year ON users((YEAR(created_at)));

SHOW INDEX FROM users;

DROP INDEX idx_name ON users;

SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_NAME = 'users';
```

### 5.4 最左前缀原则

复合索引 `(name, age, email)` 的匹配规则：

```sql
SELECT * FROM users WHERE name = '张三';

SELECT * FROM users WHERE name = '张三' AND age = 25;

SELECT * FROM users WHERE name = '张三' AND age = 25 AND email = 'a@b.com';

SELECT * FROM users WHERE age = 25;

SELECT * FROM users WHERE age = 25 AND email = 'a@b.com';

SELECT * FROM users WHERE name = '张三' AND email = 'a@b.com';

SELECT * FROM users WHERE name = '张三' AND age > 25 AND email = 'a@b.com';
-- name用索引等值查找，age用索引范围扫描，email无法用索引查找
-- 但MySQL 5.6+的ICP（索引下推）可在索引层面过滤email，减少回表次数
```

**最左前缀原则图解**：

```
复合索引 (name, age, email) 的B+树排序规则：
先按name排序 → name相同按age排序 → age相同按email排序

┌─────────────────────────────────────────┐
│  name='张三' age=20 email='a@b.com'     │
│  name='张三' age=25 email='c@d.com'     │
│  name='张三' age=25 email='e@f.com'     │
│  name='李四' age=22 email='g@h.com'     │
│  name='李四' age=30 email='i@j.com'     │
└─────────────────────────────────────────┘
  跳过name直接查age？→ 无法利用索引有序性
```

### 5.5 索引失效场景

| 场景          | 示例                                         | 原因                   |
| ----------- | ------------------------------------------ | -------------------- |
| 索引列使用函数     | `WHERE YEAR(created_at) = 2023`            | 破坏了B+树有序性            |
| 隐式类型转换      | `WHERE varchar_col = 123`                  | 转换后无法使用索引            |
| LIKE左模糊     | `WHERE name LIKE '%三'`                     | 无法确定前缀               |
| OR连接非索引列    | `WHERE indexed_col = 1 OR non_indexed = 2` | 全表扫描                 |
| 不等于         | `WHERE age != 25`                          | 优化器可能选择全表扫描（取决于数据分布） |
| IS NOT NULL | `WHERE col IS NOT NULL`                    | 取决于数据分布，可能不使用索引      |

```sql
SELECT * FROM users WHERE YEAR(created_at) = 2023;

SELECT * FROM users WHERE created_at >= '2023-01-01' AND created_at < '2024-01-01';

SELECT * FROM users WHERE name LIKE '%张';

ALTER TABLE users ADD FULLTEXT INDEX ft_name(name);
SELECT * FROM users WHERE MATCH(name) AGAINST('张');

SELECT * FROM users WHERE phone = 13800138000;

SELECT * FROM users WHERE phone = '13800138000';
```

***

## 模块六：事务与MVCC

### 6.1 事务ACID特性

| 特性               | 说明              | 实现机制     |
| ---------------- | --------------- | -------- |
| 原子性（Atomicity）   | 事务中的操作全部成功或全部失败 | Undo Log |
| 一致性（Consistency） | 事务前后数据满足完整性约束   | ACID共同保证 |
| 隔离性（Isolation）   | 并发事务之间互不干扰      | MVCC + 锁 |
| 持久性（Durability）  | 事务提交后数据永久保存     | Redo Log |

```sql
START TRANSACTION;

UPDATE users SET age = 26 WHERE id = 1;
INSERT INTO orders (user_id, amount) VALUES (1, 150.00);

COMMIT;

-- ROLLBACK;
```

### 6.2 事务隔离级别

| 隔离级别             | 脏读  | 不可重复读 | 幻读             | 性能 |
| ---------------- | --- | ----- | -------------- | -- |
| READ UNCOMMITTED | ❌可能 | ❌可能   | ❌可能            | 最高 |
| READ COMMITTED   | ✅安全 | ❌可能   | ❌可能            | 高  |
| REPEATABLE READ  | ✅安全 | ✅安全   | ⚠️可能(InnoDB优化) | 中  |
| SERIALIZABLE     | ✅安全 | ✅安全   | ✅安全            | 最低 |

```sql
SELECT @@transaction_isolation;

SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;

SET GLOBAL TRANSACTION ISOLATION LEVEL REPEATABLE READ;
```

**并发问题图解**：

```
脏读：                          不可重复读：
事务A          事务B            事务A          事务B
BEGIN         BEGIN            BEGIN         BEGIN
READ x=10                      READ x=10
              WRITE x=20                      WRITE x=20
READ x=20←脏读                 COMMIT
ROLLBACK                       READ x=20←同一事务两次读结果不同
x恢复为10

幻读：
事务A              事务B
BEGIN             BEGIN
SELECT COUNT(*)=5
                  INSERT row6
                  COMMIT
SELECT COUNT(*)=6←同一事务两次查询行数不同
```

### 6.3 MVCC（多版本并发控制）

MVCC是InnoDB实现高并发读写的核心机制，让读操作不加锁，读写不冲突。

**MVCC三大组件**：

```
┌─────────────────────────────────────────────────────────┐
│                    MVCC实现机制                          │
│                                                          │
│  1. 隐藏列（每行记录）                                   │
│  ┌──────────┬──────────┬──────────┐                     │
│  │ DB_TRX_ID│ DB_ROLL_PTR│ DB_ROW_ID│                    │
│  │(事务ID)  │(回滚指针) │(隐藏主键¹)│                    │
│  └──────────┴──────────┴──────────┘                     │
│  ¹DB_ROW_ID仅在表未定义主键且无非空唯一索引时使用          │
│                                                          │
│  2. Undo Log版本链                                       │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │ 当前版本  │───►│ 旧版本2  │───►│ 旧版本1  │          │
│  │ trx_id=5 │    │ trx_id=3 │    │ trx_id=1 │          │
│  └──────────┘    └──────────┘    └──────────┘          │
│                                                          │
│  3. Read View（读视图）                                   │
│  ┌──────────────────────────────────────────┐           │
│  │ m_ids：活跃事务列表 [3,5]                 │           │
│  │ min_trx_id：最小活跃事务ID = 3            │           │
│  │ max_trx_id：下一个分配的事务ID = 6         │           │
│  │ creator_trx_id：创建者事务ID              │           │
│  └──────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────┘
```

**可见性判断规则**：

```
对于版本链中的某个版本（trx_id）：
1. trx_id < min_trx_id → 该版本在Read View创建前已提交 → ✅可见
2. trx_id >= max_trx_id → 该版本在Read View创建后才出现 → ❌不可见
3. min_trx_id <= trx_id < max_trx_id：
   a. trx_id在m_ids中 → 该事务还未提交 → ❌不可见
   b. trx_id不在m_ids中 → 该事务已提交 → ✅可见
```

**RC vs RR的MVCC差异**：

| 隔离级别            | Read View创建时机          | 效果              |
| --------------- | ---------------------- | --------------- |
| READ COMMITTED  | 每次SELECT都创建新的Read View | 能看到其他事务已提交的最新数据 |
| REPEATABLE READ | 事务中第一次SELECT时创建，后续复用   | 事务内多次读取结果一致     |

### 6.4 Redo Log与Undo Log

**Redo Log（重做日志）**：保证事务的持久性，WAL（Write-Ahead Logging）机制。

```
Redo Log工作流程：
┌──────────┐    1.修改数据    ┌──────────┐    2.写Redo Log    ┌──────────┐
│ 事务执行  │ ──────────────► │ Buffer   │ ────────────────► │ Redo Log │
│          │                 │ Pool修改  │    (先写日志)       │ Buffer   │
│          │                 └──────────┘                    └────┬─────┘
│          │                                                      │
│          │    3.提交事务                                        │ 3.fsycn
│          │ ──────────────────────────────────────────────────► │
│          │                                                      ▼
│          │                                               ┌──────────┐
│          │                                               │ Redo Log │
│          │                                               │ File     │
│          │                                               └──────────┘
│          │    4.异步刷脏页
│          │ ──────────────► 磁盘数据文件
└──────────┘

崩溃恢复：从Redo Log重放已提交但未刷盘的数据
```

**Undo Log（回滚日志）**：保证事务的原子性，保存数据修改前的版本。

```
Undo Log作用：
1. 事务回滚：根据Undo Log恢复到修改前的状态
2. MVCC：通过Undo Log版本链实现快照读
3. 崩溃恢复：回滚未提交的事务

UPDATE操作产生的Undo Log：
┌──────────────────────────────────────────────┐
│ 原始行：id=1, name='张三', age=25, trx_id=1  │
│                                              │
│ UPDATE users SET age=26 WHERE id=1;          │
│                                              │
│ 修改后：id=1, name='张三', age=26, trx_id=5  │
│         roll_ptr → Undo Log: age=25, trx_id=1│
└──────────────────────────────────────────────┘
```

```sql
SHOW VARIABLES LIKE 'innodb_log%';

SHOW VARIABLES LIKE 'innodb_undo%';
```

***

## 模块七：锁机制

### 7.1 锁分类

```
MySQL锁体系：
┌─────────────────────────────────────────────────────┐
│                                                      │
│  全局锁：FLUSH TABLES WITH READ LOCK                 │
│  ├── 用途：全库逻辑备份                               │
│                                                      │
│  表级锁：                                            │
│  ├── 表锁：LOCK TABLES ... READ/WRITE                │
│  ├── 元数据锁（MDL）：自动加，防DDL冲突               │
│  ├── 意向锁：IS/IX，快速判断表是否有行锁              │
│  └── AUTO-INC锁：自增ID保护                          │
│                                                      │
│  行级锁（InnoDB）：                                   │
│  ├── Record Lock：锁定索引记录                        │
│  ├── Gap Lock：锁定索引记录之间的间隙                  │
│  └── Next-Key Lock：Record + Gap（左开右闭区间）      │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### 7.2 行级锁详解

```sql
SELECT * FROM users WHERE id = 1 LOCK IN SHARE MODE;

-- MySQL 8.0+推荐语法
SELECT * FROM users WHERE id = 1 FOR SHARE;

SELECT * FROM users WHERE id = 1 FOR UPDATE;

-- MySQL 8.0+支持NOWAIT和SKIP LOCKED
SELECT * FROM users WHERE id = 1 FOR UPDATE NOWAIT;
SELECT * FROM users WHERE id = 1 FOR UPDATE SKIP LOCKED;

START TRANSACTION;
SELECT * FROM users WHERE id = 1 FOR UPDATE;
UPDATE users SET age = 26 WHERE id = 1;
COMMIT;
```

**Next-Key Lock防幻读**：

```
假设users表id有记录：5, 10, 15, 20

SELECT * FROM users WHERE id = 10 FOR UPDATE;

加锁范围（Next-Key Lock）：
(5, 10]  ← 锁住id=10的记录 + (5,10)的间隙

这样其他事务无法在(5,10)间隙中插入新记录，防止幻读
```

### 7.3 锁等待与死锁

```sql
SHOW ENGINE INNODB STATUS;

SELECT * FROM PERFORMANCE_SCHEMA.DATA_LOCKS;

SELECT * FROM PERFORMANCE_SCHEMA.DATA_LOCK_WAITS;

SET innodb_lock_wait_timeout = 50;

SET innodb_deadlock_detect = ON;
```

**死锁示例与解决**：

```sql
-- 会话1
START TRANSACTION;
UPDATE users SET age = 26 WHERE id = 1;

-- 会话2
START TRANSACTION;
UPDATE users SET age = 27 WHERE id = 2;

-- 会话1（等待会话2释放id=2的锁）
UPDATE users SET age = 28 WHERE id = 2;

-- 会话2（等待会话1释放id=1的锁 → 死锁！）
UPDATE users SET age = 29 WHERE id = 1;
```

**死锁预防策略**：

| 策略     | 说明                          |
| ------ | --------------------------- |
| 固定加锁顺序 | 所有事务按相同顺序访问表和行              |
| 缩小事务范围 | 事务尽量短，减少持锁时间                |
| 合理使用索引 | 避免行锁升级为表锁                   |
| 降低隔离级别 | RC级别没有Gap Lock              |
| 设置锁超时  | innodb\_lock\_wait\_timeout |

***

## 模块八：高级特性

### 8.1 视图

**功能说明**：虚拟表，不存储数据，查询时动态生成。

```sql
CREATE VIEW v_user_orders AS
SELECT u.id, u.name, o.amount, o.created_at
FROM users u
INNER JOIN orders o ON u.id = o.user_id;

SELECT * FROM v_user_orders WHERE amount > 100;

CREATE OR REPLACE VIEW v_user_orders AS
SELECT u.id, u.name, o.amount, o.created_at, u.email
FROM users u
INNER JOIN orders o ON u.id = o.user_id;

DROP VIEW IF EXISTS v_user_orders;
```

**视图应用场景**：

| 场景   | 说明              |
| ---- | --------------- |
| 简化查询 | 封装复杂JOIN为简单视图   |
| 权限控制 | 只暴露必要列给用户       |
| 数据兼容 | 表结构变更时用视图保持接口兼容 |

### 8.2 触发器

**功能说明**：在INSERT/UPDATE/DELETE操作前后自动执行的SQL。

```sql
DELIMITER //
CREATE TRIGGER after_order_insert
AFTER INSERT ON orders
FOR EACH ROW
BEGIN
    UPDATE products SET stock = stock - NEW.quantity
    WHERE id = NEW.product_id;
END //
DELIMITER ;

DELIMITER //
CREATE TRIGGER before_user_delete
BEFORE DELETE ON users
FOR EACH ROW
BEGIN
    INSERT INTO users_backup (id, name, age, email, deleted_at)
    VALUES (OLD.id, OLD.name, OLD.age, OLD.email, NOW());
END //
DELIMITER ;

SHOW TRIGGERS;

DROP TRIGGER IF EXISTS after_order_insert;
```

**触发器NEW与OLD**：

| 触发事件   | NEW     | OLD     |
| ------ | ------- | ------- |
| INSERT | ✅ 新插入的行 | ❌       |
| UPDATE | ✅ 修改后的行 | ✅ 修改前的行 |
| DELETE | ❌       | ✅ 被删除的行 |

### 8.3 存储过程

```sql
DELIMITER //
CREATE PROCEDURE get_user_by_id(IN user_id INT)
BEGIN
    SELECT * FROM users WHERE id = user_id;
END //
DELIMITER ;

CALL get_user_by_id(1);

DELIMITER //
CREATE PROCEDURE get_user_count(OUT total INT)
BEGIN
    SELECT COUNT(*) INTO total FROM users;
END //
DELIMITER ;

SET @total = 0;
CALL get_user_count(@total);
SELECT @total;

DELIMITER //
CREATE PROCEDURE transfer_money(
    IN from_id INT,
    IN to_id INT,
    IN amount DECIMAL(10,2),
    OUT result VARCHAR(100)
)
BEGIN
    DECLARE from_balance DECIMAL(10,2);

    START TRANSACTION;

    SELECT balance INTO from_balance FROM accounts WHERE id = from_id FOR UPDATE;

    IF from_balance < amount THEN
        SET result = '余额不足';
        ROLLBACK;
    ELSE
        UPDATE accounts SET balance = balance - amount WHERE id = from_id;
        UPDATE accounts SET balance = balance + amount WHERE id = to_id;
        SET result = '转账成功';
        COMMIT;
    END IF;
END //
DELIMITER ;

DROP PROCEDURE IF EXISTS get_user_by_id;
```

### 8.4 分区表

**功能说明**：将大表按规则拆分为多个物理分区，对应用透明。

```sql
CREATE TABLE orders_range (
    id BIGINT AUTO_INCREMENT,
    order_date DATE,
    amount DECIMAL(10,2),
    PRIMARY KEY (id, order_date)
)
PARTITION BY RANGE (YEAR(order_date)) (
    PARTITION p2022 VALUES LESS THAN (2023),
    PARTITION p2023 VALUES LESS THAN (2024),
    PARTITION p2024 VALUES LESS THAN (2025),
    PARTITION pmax VALUES LESS THAN MAXVALUE
);

CREATE TABLE logs_hash (
    id BIGINT AUTO_INCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    content TEXT,
    PRIMARY KEY (id, created_at)
)
PARTITION BY HASH(id)
PARTITIONS 8;

CREATE TABLE users_list (
    id INT AUTO_INCREMENT,
    name VARCHAR(50),
    region VARCHAR(20),
    PRIMARY KEY (id, region)
)
PARTITION BY LIST COLUMNS(region) (
    PARTITION p_north VALUES IN ('北京', '天津', '河北'),
    PARTITION p_south VALUES IN ('广东', '福建', '海南'),
    PARTITION p_west VALUES IN ('四川', '重庆', '云南')
);

SELECT PARTITION_NAME, TABLE_ROWS
FROM INFORMATION_SCHEMA.PARTITIONS
WHERE TABLE_NAME = 'orders_range';

ALTER TABLE orders_range DROP PARTITION p2022;

ALTER TABLE orders_range ADD PARTITION (PARTITION p2025 VALUES LESS THAN (2026));
```

**分区类型对比**：

| 分区类型  | 分区依据   | 适用场景         |
| ----- | ------ | ------------ |
| RANGE | 范围     | 按时间分区（日志、订单） |
| LIST  | 枚举值    | 按地区、类型分区     |
| HASH  | 哈希取模   | 均匀分布数据       |
| KEY   | 类似HASH | 主键分区         |

### 8.5 窗口函数（MySQL 8.0+）

**功能说明**：在不减少行数的情况下进行聚合计算。

```sql
SELECT name, score,
    ROW_NUMBER() OVER (ORDER BY score DESC) AS row_num,
    RANK() OVER (ORDER BY score DESC) AS rank_val,
    DENSE_RANK() OVER (ORDER BY score DESC) AS dense_rank_val
FROM students;

SELECT name, dept, salary,
    ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary DESC) AS dept_rank
FROM employees;

SELECT month, revenue,
    SUM(revenue) OVER (ORDER BY month) AS cumulative_revenue
FROM monthly_sales;

SELECT date, price,
    AVG(price) OVER (ORDER BY date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS moving_avg
FROM stock_prices;

SELECT date, price,
    LAG(price, 1) OVER (ORDER BY date) AS prev_price,
    price - LAG(price, 1) OVER (ORDER BY date) AS price_change
FROM stock_prices;
```

**排名函数对比**：

| 函数          | 相同分数处理   | 示例（分数：90,90,80） |
| ----------- | -------- | --------------- |
| ROW\_NUMBER | 不同排名     | 1, 2, 3         |
| RANK        | 相同排名，跳号  | 1, 1, 3         |
| DENSE\_RANK | 相同排名，不跳号 | 1, 1, 2         |

### 8.6 CTE（公用表表达式，MySQL 8.0+）

```sql
WITH dept_avg AS (
    SELECT dept_id, AVG(salary) AS avg_salary
    FROM employees
    GROUP BY dept_id
)
SELECT e.name, e.salary, d.avg_salary
FROM employees e
JOIN dept_avg d ON e.dept_id = d.dept_id
WHERE e.salary > d.avg_salary;

WITH RECURSIVE org_tree AS (
    SELECT id, name, manager_id, 1 AS level
    FROM employees
    WHERE manager_id IS NULL
    UNION ALL
    SELECT e.id, e.name, e.manager_id, t.level + 1
    FROM employees e
    JOIN org_tree t ON e.manager_id = t.id
)
SELECT * FROM org_tree ORDER BY level;
```

***

## 模块九：性能优化

### 9.1 EXPLAIN执行计划

```sql
EXPLAIN SELECT * FROM users WHERE age > 25;
```

**EXPLAIN输出字段**：

| 字段             | 含义      | 关注点               |
| -------------- | ------- | ----------------- |
| id             | 查询序号    | 子查询的执行顺序          |
| select\_type   | 查询类型    | SIMPLE最优          |
| table          | 访问的表    | -                 |
| type           | 访问类型    | 从好到差见下表           |
| possible\_keys | 可能使用的索引 | -                 |
| key            | 实际使用的索引 | NULL表示未用索引        |
| key\_len       | 索引长度    | 越短越好              |
| rows           | 预估扫描行数  | 越少越好              |
| Extra          | 额外信息    | 关注Using filesort等 |

**type字段（访问类型，从优到差）**：

```
system > const > eq_ref > ref > range > index > ALL

┌──────────────────────────────────────────────────────────┐
│ system  ：单行表（const的特例）                           │
│ const   ：主键/唯一索引等值查询，最多1行                   │
│ eq_ref  ：JOIN时主键/唯一索引等值查询                      │
│ ref     ：非唯一索引等值查询                              │
│ range   ：索引范围扫描（BETWEEN、>、<）                    │
│ index   ：全索引扫描（比ALL好，但仍扫描全部索引）           │
│ ALL     ：全表扫描（最差，必须优化）                       │
└──────────────────────────────────────────────────────────┘
```

**Extra字段关键值**：

| 值                     | 含义        | 优化建议                |
| --------------------- | --------- | ------------------- |
| Using index           | 覆盖索引，不回表  | ✅ 最优                |
| Using where           | Server层过滤 | 检查是否可下推到引擎层         |
| Using filesort        | 额外排序      | 检查是否可利用索引排序         |
| Using temporary       | 使用临时表     | 检查GROUP BY/ORDER BY |
| Using index condition | 索引下推(ICP) | ✅ 5.6+优化            |

### 9.2 索引优化实战

```sql
EXPLAIN SELECT * FROM users WHERE name = '张三' ORDER BY age;

CREATE INDEX idx_name_age ON users(name, age);
EXPLAIN SELECT * FROM users WHERE name = '张三' ORDER BY age;

EXPLAIN SELECT * FROM users WHERE name = '张三';

EXPLAIN SELECT id, name FROM users WHERE name = '张三';

SET optimizer_switch = 'index_condition_pushdown=on';
```

### 9.3 SQL优化最佳实践

```sql
SELECT id, name, age FROM users WHERE age > 25;

SELECT * FROM users WHERE created_at >= '2023-01-01' AND created_at < '2024-01-01';

SELECT * FROM users WHERE id > 100000 ORDER BY id LIMIT 10;

SELECT * FROM users u WHERE EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id);

INSERT INTO users (name, age) VALUES ('A', 20), ('B', 25), ('C', 30);

DELETE FROM logs WHERE created_at < '2023-01-01' LIMIT 1000;
-- 注意：created_at必须有索引，否则每次删除都全表扫描
```

### 9.4 慢查询日志

```sql
SET GLOBAL slow_query_log = ON;

SET GLOBAL long_query_time = 1;

SHOW VARIABLES LIKE 'slow_query_log_file';

SHOW VARIABLES LIKE 'slow_query%';
SHOW VARIABLES LIKE 'long_query_time';
```

```bash
mysqldumpslow -s t -t 10 /var/log/mysql/slow.log
```

### 9.5 性能优化流程图

```
SQL性能优化流程：
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ 发现慢SQL │───►│ EXPLAIN  │───►│ 分析type │───►│ 优化方案 │
│          │    │ 分析     │    │ 和Extra  │    │          │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                    │
                    ┌───────────────────────────────┤
                    │                               │
                    ▼                               ▼
            ┌──────────────┐               ┌──────────────┐
            │ type=ALL     │               │ filesort/    │
            │ 全表扫描     │               │ temporary    │
            │ → 加索引     │               │ → 调整索引/  │
            │ → 优化条件   │               │   改写SQL    │
            └──────────────┘               └──────────────┘
```

### 9.6 参数调优

**InnoDB核心参数**：

```sql
SET GLOBAL innodb_buffer_pool_size = 4294967296;

SET GLOBAL innodb_flush_log_at_trx_commit = 1;

SET GLOBAL innodb_io_capacity = 10000;

SET GLOBAL innodb_io_capacity_max = 20000;

SET GLOBAL innodb_flush_method = 'O_DIRECT';
-- 注意：O_DIRECT仅Linux支持，Windows使用async_io
```

**innodb\_flush\_log\_at\_trx\_commit 详解**：

```
┌─────────────────────────────────────────────────────────────┐
│  innodb_flush_log_at_trx_commit 取值对比                     │
│                                                              │
│  = 1（最安全，默认）                                         │
│  每次事务提交 → Log Buffer fsync到磁盘                       │
│  崩溃不丢数据，性能最低                                      │
│  适用：金融、订单等核心业务                                   │
│                                                              │
│  = 2（折中）                                                 │
│  每次事务提交 → Log Buffer写到OS缓存                         │
│  每秒fsync一次                                               │
│  MySQL崩溃不丢数据，OS崩溃可能丢1秒数据                      │
│  适用：日志、统计等非核心业务                                 │
│                                                              │
│  = 0（最快，最不安全）                                       │
│  每秒写入OS缓存并fsync，事务提交不做任何操作                  │
│  MySQL崩溃可能丢1秒数据                                      │
│  适用：可容忍数据丢失的场景                                   │
└─────────────────────────────────────────────────────────────┘
```

**sync\_binlog 详解**：

```
sync_binlog = 1  ：每次提交都fsync binlog（最安全，与flush_log=1配合实现双1）
sync_binlog = 0  ：OS决定何时fsync（最快，崩溃可能丢binlog）
sync_binlog = 100：每100次提交fsync一次（折中）
```

**性能与安全平衡矩阵**：

| 场景   | flush\_log\_at\_trx\_commit | sync\_binlog | 数据安全 | 性能 |
| ---- | --------------------------- | ------------ | ---- | -- |
| 金融核心 | 1                           | 1            | 最高   | 最低 |
| 一般业务 | 1                           | 100          | 高    | 中  |
| 日志统计 | 2                           | 100          | 中    | 较高 |
| 批量导入 | 0                           | 0            | 低    | 最高 |

**连接相关参数**：

```sql
SET GLOBAL max_connections = 2000;

SET GLOBAL thread_cache_size = 200;

SET GLOBAL table_open_cache = 8000;

SET GLOBAL wait_timeout = 28800;

SET GLOBAL interactive_timeout = 28800;

SHOW STATUS LIKE 'Threads%';
SHOW STATUS LIKE 'Max_used_connections';
SHOW STATUS LIKE 'Open_tables';
```

**排序与JOIN缓冲**：

```sql
SET SESSION sort_buffer_size = 4194304;

SET SESSION join_buffer_size = 4194304;

SET SESSION read_rnd_buffer_size = 4194304;

SET SESSION tmp_table_size = 67108864;

SET SESSION max_heap_table_size = 67108864;
```

### 9.7 连接池优化

```
连接池工作原理：
┌──────────┐     ┌──────────────────────────┐     ┌──────────┐
│ 应用线程  │────►│     连接池               │────►│ MySQL    │
│          │◄────│  ┌───┬───┬───┬───┬───┐  │◄────│ Server   │
│          │     │  │C1 │C2 │C3 │C4 │C5 │  │     │          │
│          │     │  └───┴───┴───┴───┴───┘  │     │          │
│          │     │  空闲连接复用，避免频繁    │     │          │
│          │     │  创建/销毁TCP连接         │     │          │
└──────────┘     └──────────────────────────┘     └──────────┘
```

**连接池关键参数（以HikariCP为例）**：

| 参数                  | 建议值              | 说明             |
| ------------------- | ---------------- | -------------- |
| maximumPoolSize     | CPU核数×2 + 磁盘数    | 最大连接数          |
| minimumIdle         | 同maximumPoolSize | 最小空闲连接         |
| connectionTimeout   | 30000ms          | 获取连接超时         |
| idleTimeout         | 600000ms         | 空闲连接存活时间       |
| maxLifetime         | 1800000ms        | 连接最大存活时间(30分钟) |
| connectionTestQuery | SELECT 1         | 连接有效性检查        |

**连接数计算公式**：

```
最大连接数 = ((核心数 * 2) + 有效磁盘数)

示例：
8核CPU + 2块SSD → maximumPoolSize = 8*2 + 2 = 18

注意：连接数不是越多越好
- 连接数过多 → 线程调度开销大 → 上下文切换频繁 → 反而降低吞吐
- 连接数过少 → 请求排队等待 → 响应时间增加
```

### 9.8 分库分表

当单表数据量超过**5000万行**或单表数据文件超过**20GB**时，应考虑分库分表。

**分库分表策略**：

```
┌──────────────────────────────────────────────────────────────┐
│                    分库分表策略选择                            │
│                                                               │
│  1. 垂直分库：按业务拆分                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                     │
│  │ 用户库   │ │ 订单库   │ │ 商品库   │                     │
│  │ users    │ │ orders   │ │ products │                     │
│  │ profiles │ │ payments │ │ category │                     │
│  └──────────┘ └──────────┘ └──────────┘                     │
│                                                               │
│  2. 垂直分表：按列拆分（冷热分离）                             │
│  ┌──────────────────┐  ┌──────────────────┐                 │
│  │ users_core       │  │ users_detail     │                 │
│  │ id, name, phone  │  │ id, bio, avatar  │                 │
│  │ (频繁访问)       │  │ (偶尔访问)       │                 │
│  └──────────────────┘  └──────────────────┘                 │
│                                                               │
│  3. 水平分表：按行拆分                                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                     │
│  │ order_0  │ │ order_1  │ │ order_2  │                     │
│  │ id%3==0  │ │ id%3==1  │ │ id%3==2  │                     │
│  └──────────┘ └──────────┘ └──────────┘                     │
└──────────────────────────────────────────────────────────────┘
```

**分片键选择原则**：

| 原则     | 说明          | 示例               |
| ------ | ----------- | ---------------- |
| 高基数    | 分片键值域大，分布均匀 | user\_id优于status |
| 避免跨片查询 | 查询条件尽量包含分片键 | 按user\_id查询订单    |
| 数据均匀   | 各分片数据量大致相等  | HASH取模           |
| 业务关联   | 关联数据尽量在同一分片 | 同一用户的订单放同一库      |

**常见分片算法**：

```sql
-- HASH取模：均匀分布
-- 分片 = user_id % N

-- RANGE范围：按时间或ID范围
-- 1-100万 → 分片0，100万-200万 → 分片1

-- 一致性哈希：减少扩容数据迁移
-- 虚拟节点环，扩容只需迁移1/N的数据
```

**分库分表后的问题与解决方案**：

| 问题     | 说明           | 解决方案               |
| ------ | ------------ | ------------------ |
| 分布式ID  | 自增ID跨表冲突     | Snowflake雪花算法、号段模式 |
| 跨片JOIN | 不同分片无法JOIN   | 冗余字段、应用层组装、宽表      |
| 跨片事务   | 分库后无法本地事务    | 分布式事务(Seata)、最终一致性 |
| 聚合查询   | COUNT/SUM需合并 | 各分片聚合后汇总           |
| 排序分页   | 全局排序需合并      | 禁止深度分页、ES辅助        |
| 扩容迁移   | 增加分片需迁移数据    | 一致性哈希、双写迁移         |

**ShardingSphere配置示例（YAML）**：

```yaml
dataSources:
  ds_0:
    url: jdbc:mysql://localhost:3306/db_0
    username: root
    password: root
  ds_1:
    url: jdbc:mysql://localhost:3306/db_1
    username: root
    password: root

shardingRule:
  tables:
    orders:
      actualDataNodes: ds_${0..1}.orders_${0..3}
      databaseStrategy:
        standard:
          shardingColumn: user_id
          shardingAlgorithmName: orders_db_mod
      tableStrategy:
        standard:
          shardingColumn: id
          shardingAlgorithmName: orders_table_mod
  shardingAlgorithms:
    orders_db_mod:
      type: MOD
      props:
        sharding-count: 2
    orders_table_mod:
      type: MOD
      props:
        sharding-count: 4
```

**分库分表决策流程**：

```
┌──────────────┐
│ 单表数据量    │
│ < 500万行    │──── 不需要分表，优化索引即可
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 单表数据量    │
│ 500万-5000万 │──── 优先考虑分区表(PARTITION)
└──────┬───────┘     或冷热分离(归档旧数据)
       │
       ▼
┌──────────────┐
│ 单表数据量    │
│ > 5000万行   │──── 必须分库分表
└──────┬───────┘     或迁移到TiDB等分布式DB
       │
       ▼
┌──────────────┐
│ 需要全局事务  │──── 考虑TiDB/OceanBase
│ 复杂聚合查询  │     替代MySQL分库分表
└──────────────┘
```

***

## 模块十：应用场景与实战

### 10.1 电商系统

**表设计**：

```sql
CREATE TABLE products (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    category_id INT NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    stock INT NOT NULL DEFAULT 0,
    status TINYINT DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_category (category_id),
    INDEX idx_status_price (status, price)
) ENGINE=InnoDB;

CREATE TABLE orders (
    id BIGINT AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    total_amount DECIMAL(10,2) NOT NULL,
    status TINYINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, created_at),
    INDEX idx_user_id (user_id),
    INDEX idx_status (status)
) ENGINE=InnoDB
PARTITION BY RANGE (YEAR(created_at)) (
    PARTITION p2023 VALUES LESS THAN (2024),
    PARTITION p2024 VALUES LESS THAN (2025),
    PARTITION pmax VALUES LESS THAN MAXVALUE
);

CREATE TABLE order_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    quantity INT NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    INDEX idx_order_id (order_id),
    INDEX idx_product_id (product_id)
) ENGINE=InnoDB;
```

**库存扣减（乐观锁）**：

```sql
UPDATE products SET stock = stock - 1 WHERE id = 1 AND stock >= 1;

SELECT ROW_COUNT();
```

**订单超时取消**：

```sql
SELECT id FROM orders
WHERE status = 0 AND created_at < DATE_SUB(NOW(), INTERVAL 30 MINUTE);

UPDATE orders SET status = 4
WHERE status = 0 AND created_at < DATE_SUB(NOW(), INTERVAL 30 MINUTE);
```

### 10.2 SaaS多租户

```sql
CREATE TABLE tenant_data (
    id BIGINT AUTO_INCREMENT,
    tenant_id INT NOT NULL,
    data_key VARCHAR(100),
    data_value TEXT,
    PRIMARY KEY (id, tenant_id),
    INDEX idx_tenant (tenant_id)
) ENGINE=InnoDB;
```

**多租户方案对比**：

| 方案             | 隔离性 | 成本 | 运维复杂度 | 适用规模 |
| -------------- | --- | -- | ----- | ---- |
| 共享表+tenant\_id | 低   | 低  | 低     | 中小   |
| 独立Schema       | 中   | 中  | 中     | 中大   |
| 独立数据库          | 高   | 高  | 高     | 大    |

### 10.3 日志与审计系统

```sql
CREATE TABLE audit_logs (
    id BIGINT AUTO_INCREMENT,
    user_id INT NOT NULL,
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50),
    resource_id BIGINT,
    detail JSON,
    ip_address VARCHAR(45),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, created_at),
    INDEX idx_user_action (user_id, action),
    INDEX idx_resource (resource_type, resource_id)
) ENGINE=InnoDB
PARTITION BY RANGE (YEAR(created_at) * 100 + MONTH(created_at)) (
    PARTITION p202301 VALUES LESS THAN (202302),
    PARTITION p202302 VALUES LESS THAN (202303),
    PARTITION p202303 VALUES LESS THAN (202304),
    PARTITION pmax VALUES LESS THAN MAXVALUE
);

SELECT * FROM audit_logs WHERE user_id = 1001 AND action = 'DELETE' ORDER BY created_at DESC LIMIT 20;

SELECT * FROM audit_logs WHERE detail->>'$.table' = 'users';
```

### 10.4 社交系统

**好友关系与关注模型**：

```sql
CREATE TABLE user_follows (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    follower_id BIGINT NOT NULL,
    following_id BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_follow (follower_id, following_id),
    INDEX idx_following (following_id)
) ENGINE=InnoDB;

INSERT INTO user_follows (follower_id, following_id) VALUES (1, 2);

SELECT following_id FROM user_follows WHERE follower_id = 1;

SELECT follower_id FROM user_follows WHERE following_id = 1;

SELECT a.following_id
FROM user_follows a
JOIN user_follows b ON a.following_id = b.follower_id
WHERE a.follower_id = 1 AND b.following_id = 1;
```

**朋友圈/动态流**：

```sql
CREATE TABLE moments (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    content TEXT,
    images JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_created (user_id, created_at DESC)
) ENGINE=InnoDB;

CREATE TABLE moment_likes (
    moment_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (moment_id, user_id),
    INDEX idx_user (user_id)
) ENGINE=InnoDB;

SELECT m.*, 
    (SELECT COUNT(*) FROM moment_likes WHERE moment_id = m.id) AS like_count,
    (SELECT COUNT(*) FROM moment_comments WHERE moment_id = m.id) AS comment_count
FROM moments m
WHERE m.user_id IN (SELECT following_id FROM user_follows WHERE follower_id = 1)
ORDER BY m.created_at DESC
LIMIT 20;
```

**社交场景技术选型**：

| 功能   | MySQL方案    | 替代方案                  |
| ---- | ---------- | --------------------- |
| 好友关系 | 邻接表+JOIN   | 图数据库(Neo4j)           |
| 动态流  | 推拉结合+分页    | Redis Timeline        |
| 点赞   | 批量写入+计数缓存  | Redis Set+HyperLogLog |
| 消息   | 离线消息表      | 消息队列                  |
| 全文搜索 | FULLTEXT索引 | Elasticsearch         |

### 10.5 金融系统

**账户与交易**：

```sql
CREATE TABLE accounts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    balance DECIMAL(18,2) NOT NULL DEFAULT 0.00,
    version INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id)
) ENGINE=InnoDB;

CREATE TABLE transactions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    from_account_id BIGINT NOT NULL,
    to_account_id BIGINT NOT NULL,
    amount DECIMAL(18,2) NOT NULL,
    status TINYINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_from (from_account_id),
    INDEX idx_to (to_account_id),
    INDEX idx_status (status)
) ENGINE=InnoDB;
```

**安全转账（悲观锁）**：

```sql
START TRANSACTION;

SELECT balance FROM accounts WHERE id = 1 FOR UPDATE;

SELECT balance FROM accounts WHERE id = 2 FOR UPDATE;

UPDATE accounts SET balance = balance - 100.00 WHERE id = 1 AND balance >= 100.00;

UPDATE accounts SET balance = balance + 100.00 WHERE id = 2;

INSERT INTO transactions (from_account_id, to_account_id, amount, status)
VALUES (1, 2, 100.00, 1);

COMMIT;
```

**安全转账（乐观锁）**：

```sql
START TRANSACTION;

SELECT balance, version FROM accounts WHERE id = 1;

UPDATE accounts SET balance = balance - 100.00, version = version + 1
WHERE id = 1 AND version = 上一步查询到的version值 AND balance >= 100.00;

SELECT ROW_COUNT();

SELECT balance, version FROM accounts WHERE id = 2;

UPDATE accounts SET balance = balance + 100.00, version = version + 1
WHERE id = 2 AND version = 上一步查询到的version值;

COMMIT;
```

**对账查询**：

```sql
SELECT
    a.id AS account_id,
    a.balance AS current_balance,
    COALESCE(SUM(CASE WHEN t.to_account_id = a.id THEN t.amount ELSE 0 END), 0) -
    COALESCE(SUM(CASE WHEN t.from_account_id = a.id THEN t.amount ELSE 0 END), 0) AS calculated_balance
FROM accounts a
LEFT JOIN transactions t ON t.status = 1
    AND (t.from_account_id = a.id OR t.to_account_id = a.id)
    AND t.created_at >= '2024-01-01'
GROUP BY a.id, a.balance
HAVING current_balance != calculated_balance;
```

**金融场景关键约束**：

| 约束    | 实现方式          | 说明              |
| ----- | ------------- | --------------- |
| 金额精度  | DECIMAL(18,2) | 绝不用FLOAT/DOUBLE |
| 转账原子性 | 事务+行锁         | FOR UPDATE保证串行  |
| 幂等性   | 唯一业务流水号       | 防重复扣款           |
| 可追溯   | 交易流水表         | 每笔操作留痕          |
| 对账    | 定时任务比对        | 余额与流水一致性        |

### 10.6 实时分析系统

**事件采集表**：

```sql
CREATE TABLE events (
    id BIGINT AUTO_INCREMENT,
    event_type VARCHAR(50) NOT NULL,
    user_id BIGINT,
    properties JSON,
    event_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, event_time),
    INDEX idx_type_time (event_type, event_time),
    INDEX idx_user_time (user_id, event_time)
) ENGINE=InnoDB
PARTITION BY RANGE (UNIX_TIMESTAMP(event_time)) (
    PARTITION p202401 VALUES LESS THAN (UNIX_TIMESTAMP('2024-02-01')),
    PARTITION p202402 VALUES LESS THAN (UNIX_TIMESTAMP('2024-03-01')),
    PARTITION p202403 VALUES LESS THAN (UNIX_TIMESTAMP('2024-04-01')),
    PARTITION pmax VALUES LESS THAN MAXVALUE
);
```

**实时统计（窗口函数）**：

```sql
SELECT
    event_type,
    DATE(event_time) AS event_date,
    COUNT(*) AS daily_count,
    SUM(COUNT(*)) OVER (PARTITION BY event_type ORDER BY DATE(event_time)) AS cumulative_count,
    COUNT(*) - LAG(COUNT(*), 1) OVER (PARTITION BY event_type ORDER BY DATE(event_time)) AS day_over_day
FROM events
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY event_type, DATE(event_time)
ORDER BY event_type, event_date;
```

**漏斗分析**：

```sql
WITH step1 AS (
    SELECT user_id, MIN(event_time) AS step1_time
    FROM events WHERE event_type = 'page_view'
    AND event_time >= '2024-01-01' GROUP BY user_id
),
step2 AS (
    SELECT e.user_id, MIN(e.event_time) AS step2_time
    FROM events e JOIN step1 s ON e.user_id = s.user_id
    WHERE e.event_type = 'add_to_cart' AND e.event_time > s.step1_time
    GROUP BY e.user_id
),
step3 AS (
    SELECT e.user_id, MIN(e.event_time) AS step3_time
    FROM events e JOIN step2 s ON e.user_id = s.user_id
    WHERE e.event_type = 'purchase' AND e.event_time > s.step2_time
    GROUP BY e.user_id
)
SELECT
    (SELECT COUNT(*) FROM step1) AS page_view_count,
    (SELECT COUNT(*) FROM step2) AS add_to_cart_count,
    (SELECT COUNT(*) FROM step3) AS purchase_count,
    ROUND((SELECT COUNT(*) FROM step2) / (SELECT COUNT(*) FROM step1) * 100, 2) AS cvr_step1_to_2,
    ROUND((SELECT COUNT(*) FROM step3) / (SELECT COUNT(*) FROM step2) * 100, 2) AS cvr_step2_to_3;
```

**留存分析**：

```sql
WITH first_login AS (
    SELECT user_id, MIN(DATE(event_time)) AS first_date
    FROM events WHERE event_type = 'login'
    GROUP BY user_id
),
login_dates AS (
    SELECT DISTINCT user_id, DATE(event_time) AS login_date
    FROM events WHERE event_type = 'login'
)
SELECT
    f.first_date,
    COUNT(DISTINCT f.user_id) AS new_users,
    COUNT(DISTINCT CASE WHEN DATEDIFF(l.login_date, f.first_date) = 1 THEN f.user_id END) AS day1_retention,
    COUNT(DISTINCT CASE WHEN DATEDIFF(l.login_date, f.first_date) = 7 THEN f.user_id END) AS day7_retention,
    COUNT(DISTINCT CASE WHEN DATEDIFF(l.login_date, f.first_date) = 30 THEN f.user_id END) AS day30_retention
FROM first_login f
LEFT JOIN login_dates l ON f.user_id = l.user_id AND l.login_date >= f.first_date
GROUP BY f.first_date
ORDER BY f.first_date;
```

### 10.7 内容管理系统（CMS）

**文章与分类**：

```sql
CREATE TABLE categories (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    parent_id INT,
    sort_order INT DEFAULT 0,
    INDEX idx_parent (parent_id)
) ENGINE=InnoDB;

CREATE TABLE articles (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    content LONGTEXT,
    author_id BIGINT NOT NULL,
    category_id INT,
    status TINYINT DEFAULT 0,
    view_count INT DEFAULT 0,
    published_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status_published (status, published_at DESC),
    INDEX idx_author (author_id),
    INDEX idx_category (category_id),
    FULLTEXT INDEX ft_title_content (title, content)
) ENGINE=InnoDB;

CREATE TABLE tags (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE
) ENGINE=InnoDB;

CREATE TABLE article_tags (
    article_id BIGINT NOT NULL,
    tag_id INT NOT NULL,
    PRIMARY KEY (article_id, tag_id),
    INDEX idx_tag (tag_id)
) ENGINE=InnoDB;
```

**全文搜索**：

```sql
SELECT id, title,
    MATCH(title, content) AGAINST('MySQL优化' IN NATURAL LANGUAGE MODE) AS relevance
FROM articles
WHERE MATCH(title, content) AGAINST('MySQL优化' IN NATURAL LANGUAGE_MODE)
ORDER BY relevance DESC
LIMIT 20;

SELECT id, title
FROM articles
WHERE MATCH(title, content) AGAINST('+MySQL +索引 -删除' IN BOOLEAN MODE);
```

**分类树（递归CTE）**：

```sql
WITH RECURSIVE category_tree AS (
    SELECT id, name, parent_id, 0 AS level, CAST(name AS CHAR(500)) AS path
    FROM categories WHERE parent_id IS NULL
    UNION ALL
    SELECT c.id, c.name, c.parent_id, t.level + 1,
        CONCAT(t.path, ' > ', c.name)
    FROM categories c JOIN category_tree t ON c.parent_id = t.id
)
SELECT * FROM category_tree ORDER BY path;
```

### 10.8 应用场景速查表

| 场景   | 关键技术          | 核心SQL/方案                           |
| ---- | ------------- | ---------------------------------- |
| 电商库存 | 乐观锁           | UPDATE ... WHERE stock >= N        |
| 订单超时 | 定时任务+分区       | 扫描未支付订单+分区删除                       |
| 多租户  | tenant\_id隔离  | 共享表/独立Schema/独立库                   |
| 审计日志 | 分区表+JSON      | RANGE分区+JSON函数查询                   |
| 排行榜  | 窗口函数          | DENSE\_RANK() OVER()               |
| 树形结构 | 递归CTE         | WITH RECURSIVE                     |
| 数据归档 | 分区交换          | ALTER TABLE ... EXCHANGE PARTITION |
| 读写分离 | 主从复制          | 主写从读                               |
| 好友关系 | 邻接表+JOIN      | 双向关注查询+互关判断                        |
| 动态流  | 推拉结合          | 关注表JOIN+分页                         |
| 金融转账 | 事务+行锁         | FOR UPDATE悲观锁/version乐观锁           |
| 对账   | CASE WHEN聚合   | 余额与流水比对                            |
| 漏斗分析 | CTE+JOIN      | 逐步过滤+转化率计算                         |
| 留存分析 | DATEDIFF+CASE | 首日+回访日差值统计                         |
| 全文搜索 | FULLTEXT索引    | MATCH AGAINST布尔模式                  |
| 分类树  | 递归CTE         | 路径拼接+层级计算                          |

***

## 模块十一：运维监控与备份恢复

### 11.1 监控指标

**核心监控指标**：

| 类别  | 指标                        | 查询命令                                     | 告警阈值                  |
| --- | ------------------------- | ---------------------------------------- | --------------------- |
| 连接  | Threads\_connected        | SHOW STATUS LIKE 'Threads%'              | >80% max\_connections |
| 连接  | Threads\_running          | SHOW STATUS LIKE 'Threads%'              | 持续>CPU核数\*2           |
| QPS | Queries                   | SHOW STATUS LIKE 'Queries'               | 接近瓶颈                  |
| TPS | Com\_commit+Com\_rollback | SHOW STATUS LIKE 'Com%'                  | -                     |
| 慢查询 | Slow\_queries             | SHOW STATUS LIKE 'Slow\_queries'         | >0需关注                 |
| 缓冲池 | Buffer\_pool\_hit\_rate   | SHOW STATUS LIKE 'Innodb\_buffer\_pool%' | <95%                  |
| 锁   | Innodb\_row\_lock\_waits  | SHOW STATUS LIKE 'Innodb\_row\_lock%'    | 突增需关注                 |

```sql
SELECT
    ROUND(
        (1 - Innodb_buffer_pool_reads / Innodb_buffer_pool_read_requests) * 100,
        2
    ) AS hit_rate
FROM (
    SELECT
        (SELECT VARIABLE_VALUE FROM PERFORMANCE_SCHEMA.GLOBAL_STATUS
         WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads') AS Innodb_buffer_pool_reads,
        (SELECT VARIABLE_VALUE FROM PERFORMANCE_SCHEMA.GLOBAL_STATUS
         WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests') AS Innodb_buffer_pool_read_requests
) t;

SHOW PROCESSLIST;

SHOW ENGINE INNODB STATUS;
```

### 11.2 备份策略

```
备份策略选择：
┌─────────────────────────────────────────────────────┐
│                                                      │
│  数据量 < 10GB                                       │
│  └── mysqldump逻辑备份（简单可靠）                    │
│                                                      │
│  数据量 10GB~100GB                                   │
│  └── Percona XtraBackup物理备份（在线热备）           │
│                                                      │
│  数据量 > 100GB                                      │
│  └── XtraBackup + 从库备份（不影响主库）              │
│                                                      │
└─────────────────────────────────────────────────────┘
```

**mysqldump逻辑备份**：

```bash
mysqldump -u root -p test_db > test_db_backup.sql

mysqldump -u root -p test_db users > users_backup.sql

mysqldump -u root -p --all-databases > all_backup.sql

mysqldump -u root -p --no-data test_db > schema.sql

mysqldump -u root -p test_db | gzip > test_db_backup.sql.gz

mysql -u root -p test_db < test_db_backup.sql

gunzip < test_db_backup.sql.gz | mysql -u root -p test_db
```

**XtraBackup物理备份**：

```bash
xtrabackup --backup --target-dir=/backup/full -u root -p

xtrabackup --backup --target-dir=/backup/inc1 --incremental-basedir=/backup/full -u root -p

xtrabackup --prepare --target-dir=/backup/full

xtrabackup --copy-back --target-dir=/backup/full
```

### 11.3 Binlog与数据恢复

```sql
SHOW VARIABLES LIKE 'log_bin%';
SHOW VARIABLES LIKE 'binlog_format%';

SHOW BINARY LOGS;

SHOW BINLOG EVENTS IN 'mysql-bin.000001';
```

```bash
mysqlbinlog --start-datetime="2023-01-01 10:00:00" --stop-datetime="2023-01-01 11:00:00" mysql-bin.000001 | mysql -u root -p
```

**Binlog三种格式**：

| 格式        | 说明      | 优缺点          |
| --------- | ------- | ------------ |
| STATEMENT | 记录SQL语句 | 日志小，但主从不一致风险 |
| ROW       | 记录行变更   | 数据一致，日志大     |
| MIXED     | 混合模式    | 折中方案         |

***

## 模块十二：高可用方案

### 12.1 主从复制

**复制原理**：

```
主从复制流程：
┌──────────┐                    ┌──────────┐
│  主库     │                    │  从库     │
│  Master  │                    │  Slave   │
└────┬─────┘                    └────┬─────┘
     │                               │
     │  1. 事务提交，写入Binlog       │
     │  ────────────────────────►    │
     │                               │
     │  2. Dump线程发送Binlog事件     │
     │  ────────────────────────►    │
     │                               │
     │                    3. I/O线程写入Relay Log
     │                               │
     │                    4. SQL线程重放Relay Log
     │                               │
     │                               ▼
     │                          数据同步完成
```

**配置主从复制**：

```ini
[mysqld]
server-id = 1
log-bin = mysql-bin
binlog-format = ROW
gtid-mode = ON
enforce-gtid-consistency = ON
```

```sql
CREATE USER 'repl'@'%' IDENTIFIED BY 'password';
GRANT REPLICATION SLAVE ON *.* TO 'repl'@'%';
FLUSH PRIVILEGES;

SHOW MASTER STATUS;
```

```ini
[mysqld]
server-id = 2
relay-log = mysql-relay-bin
gtid-mode = ON
enforce-gtid-consistency = ON
```

```sql
CHANGE REPLICATION SOURCE TO
SOURCE_HOST = '主库IP',
SOURCE_USER = 'repl',
SOURCE_PASSWORD = 'password',
SOURCE_AUTO_POSITION = 1;

START REPLICA;

SHOW REPLICA STATUS\G;

-- 注意：MySQL 8.0.23+推荐使用上述新语法
-- 旧语法（仍兼容但已废弃）：CHANGE MASTER TO / START SLAVE / SHOW SLAVE STATUS
```

### 12.2 读写分离

```
读写分离架构：
┌──────────────────────────────────────────────────┐
│                                                   │
│  ┌──────────┐                                    │
│  │ 应用服务  │                                    │
│  └────┬─────┘                                    │
│       │                                           │
│  ┌────┴─────┐                                    │
│  │ 代理中间件│  ← ProxySQL / MyCat / ShardingSphere│
│  └──┬────┬──┘                                    │
│     │    │                                        │
│  写操作  读操作                                    │
│     │    │                                        │
│     ▼    ▼                                        │
│  ┌──────┐ ┌──────┐ ┌──────┐                     │
│  │主库   │ │从库1  │ │从库2  │                     │
│  │(写)   │ │(读)   │ │(读)   │                     │
│  └──┬───┘ └──────┘ └──────┘                     │
│     │ 复制                                        │
│     └─────────────────────────────────►           │
└──────────────────────────────────────────────────┘
```

### 12.3 高可用方案对比

| 方案                | 架构                     | 自动故障转移 | 适用规模 |
| ----------------- | ---------------------- | ------ | ---- |
| MHA               | 主从+Manager             | ✅ 30秒内 | 中小   |
| MGR               | Paxos协议组复制             | ✅      | 中大   |
| InnoDB Cluster    | MGR+MySQL Shell+Router | ✅      | 中大   |
| MySQL NDB Cluster | 分布式存储                  | ✅      | 超大   |

### 12.4 MGR（MySQL Group Replication）详解

MGR基于Paxos协议实现多主一致性复制，是MySQL官方推荐的高可用方案。

**MGR架构**：

```
MGR单主模式架构：
┌──────────────────────────────────────────────────────┐
│                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │ Primary  │  │ Secondary│  │ Secondary│           │
│  │ (读写)   │  │ (只读)   │  │ (只读)   │           │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘           │
│       │              │              │                  │
│       └──────────────┼──────────────┘                  │
│                      │                                 │
│              ┌───────┴───────┐                        │
│              │ Group         │                        │
│              │ Communication │                        │
│              │ (Paxos协议)   │                        │
│              └───────────────┘                        │
│                                                       │
│  特性：                                                │
│  - 自动故障检测与转移                                  │
│  - 强一致性（所有节点确认后才提交）                     │
│  - 支持单主/多主模式切换                               │
│  - 内置流量控制防止单点过载                            │
└──────────────────────────────────────────────────────┘
```

**MGR配置**：

```ini
[mysqld]
server-id = 1
gtid-mode = ON
enforce-gtid-consistency = ON
log-bin = mysql-bin
binlog-format = ROW
master-info-repository = TABLE
relay-log-info-repository = TABLE
binlog-checksum = NONE
log-slave-updates = ON

plugin-load = group_replication.so
group_replication_group_name = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
group_replication_start_on_boot = OFF
group_replication_local_address = "node1:33061"
group_replication_group_seeds = "node1:33061,node2:33062,node3:33063"
group_replication_single_primary_mode = ON
group_replication_enforce_update_everywhere_checks = OFF
```

```sql
CHANGE MASTER TO MASTER_USER='repl', MASTER_PASSWORD='password' FOR CHANNEL 'group_replication_recovery';

-- 首次启动MGR的节点需要执行bootstrap引导（仅首次！）
SET GLOBAL group_replication_bootstrap_group = ON;
START GROUP_REPLICATION;
SET GLOBAL group_replication_bootstrap_group = OFF;

-- 其他节点只需启动（无需bootstrap）
START GROUP_REPLICATION;

SELECT * FROM PERFORMANCE_SCHEMA.REPLICATION_GROUP_MEMBERS;

SELECT * FROM PERFORMANCE_SCHEMA.REPLICATION_GROUP_MEMBER_STATS;
```

### 12.5 MHA架构详解

MHA（Master High Availability）是一套成熟的MySQL故障切换方案，适用于传统主从复制架构。

```
MHA架构：
┌──────────────────────────────────────────────────────┐
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │ MHA Manager                                  │    │
│  │ - 监控Master存活                             │    │
│  │ - 故障时协调切换                             │    │
│  │ - 补全Slave差异relay log                     │    │
│  └────────────────────┬─────────────────────────┘    │
│                       │ 监控                          │
│       ┌───────────────┼───────────────┐              │
│       ▼               ▼               ▼              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │ Master   │  │ Slave1   │  │ Slave2   │           │
│  │ (当前主) │  │ (候选主) │  │ (从库)   │           │
│  └──────────┘  └──────────┘  └──────────┘           │
│                                                       │
│  故障切换流程：                                        │
│  1. Manager检测Master不可达                           │
│  2. 验证Slave差异，尝试补全relay log                  │
│  3. 选择最新Slave作为新Master                         │
│  4. 其他Slave指向新Master                             │
│  5. VIP漂移到新Master                                 │
│  6. 整个过程约10-30秒                                 │
└──────────────────────────────────────────────────────┘
```

**MHA配置**：

```ini
[server default]
manager_workdir=/var/log/mha
manager_log=/var/log/mha/manager.log
user=mha_monitor
password=mha_password
repl_user=repl
repl_password=repl_password
ssh_user=root

[server1]
hostname=192.168.1.101
port=3306
candidate_master=1

[server2]
hostname=192.168.1.102
port=3306
candidate_master=1

[server3]
hostname=192.168.1.103
port=3306
no_master=1
```

### 12.6 容灾与数据安全

**RPO与RTO**：

```
┌─────────────────────────────────────────────────────────────┐
│  RPO（Recovery Point Objective）：恢复点目标                  │
│  → 可容忍丢失的最大数据量（时间维度）                         │
│                                                              │
│  RTO（Recovery Time Objective）：恢复时间目标                 │
│  → 从故障到恢复服务的最大时间                                 │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  方案              │  RPO      │  RTO               │    │
│  ├────────────────────┼───────────┼────────────────────┤    │
│  │  异地冷备          │  小时级    │  天级               │    │
│  │  主从复制          │  秒级      │  分钟级             │    │
│  │  MGR               │  0(不丢)  │  秒级               │    │
│  │  跨机房MGR         │  0(不丢)  │  分钟级             │    │
│  │  异地双活          │  0(不丢)  │  秒级               │    │
│  └────────────────────┴───────────┴────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

**数据安全最佳实践**：

| 措施       | 说明                            | 配置                                   |
| -------- | ----------------------------- | ------------------------------------ |
| 双1配置     | flush\_log=1 + sync\_binlog=1 | 最安全，性能折中                             |
| 定期备份     | 全量+增量，异地存储                    | crontab + XtraBackup                 |
| Binlog保留 | 保留7-30天                       | binlog\_expire\_logs\_seconds=604800 |
| 延迟从库     | 延迟1小时，防误操作                    | CHANGE MASTER TO MASTER\_DELAY=3600  |
| 闪回       | 基于Binlog的逆向操作                 | binlog2sql工具                         |
| 权限最小化    | 只授予必要权限                       | GRANT SELECT ON db.\*                |

**误操作恢复（闪回）**：

```bash
python binlog2sql.py -h127.0.0.1 -P3306 -uroot -p \
    --start-file='mysql-bin.000001' \
    --start-datetime='2024-01-01 10:00:00' \
    --stop-datetime='2024-01-01 11:00:00' \
    -B \
    -o /tmp/rollback.sql

mysql -uroot -p < /tmp/rollback.sql
```

***

## 总结

本手册从MySQL基础操作出发，系统性地覆盖了以下核心知识体系：

```
MySQL知识体系总览：
┌─────────────────────────────────────────────────────────┐
│                                                          │
│  基础层：连接操作、SQL语法、数据类型、约束                │
│     │                                                    │
│  原理层：分层架构、InnoDB引擎、B+树索引、Buffer Pool      │
│     │    Doublewrite Buffer、Change Buffer、AHI           │
│     │                                                    │
│  事务层：ACID、隔离级别、MVCC、Redo/Undo Log              │
│     │                                                    │
│  锁层：全局锁、表锁、行锁、Next-Key Lock、死锁            │
│     │                                                    │
│  高级层：视图、触发器、存储过程、分区、窗口函数、CTE       │
│     │                                                    │
│  优化层：EXPLAIN、索引优化、SQL调优、慢查询                │
│     │    参数调优、连接池、分库分表                        │
│     │                                                    │
│  应用层：电商、SaaS、日志、社交、金融、分析、CMS           │
│     │                                                    │
│  运维层：监控指标、备份恢复、Binlog、高可用                │
│     │    MGR、MHA、容灾、闪回                             │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

**学习路径建议**：

| 阶段 | 目标          | 重点模块     | 建议时长 |
| -- | ----------- | -------- | ---- |
| 入门 | 能写SQL完成CRUD | 模块一\~四   | 1-2周 |
| 进阶 | 理解索引与事务原理   | 模块五\~七   | 2-3周 |
| 高级 | 掌握高级特性与优化   | 模块八\~九   | 2-3周 |
| 实战 | 能设计业务数据库    | 模块十      | 2周   |
| 运维 | 能保障生产稳定     | 模块十一\~十二 | 2周   |

***

## 附录A：MySQL版本特性对照

| 版本     | 发布年份 | 核心特性                          |
| ------ | ---- | ----------------------------- |
| 5.6    | 2013 | GTID复制、在线DDL、ICP、MRR          |
| 5.7    | 2015 | JSON类型、GTID增强、在线修改Buffer Pool |
| 8.0    | 2018 | 窗口函数、CTE、角色、降序索引、JSON增强、不可见索引 |
| 8.0.14 | 2019 | 直连组复制、DNS SRV支持               |
| 8.0.17 | 2019 | 多值索引、HASH JOIN                |
| 8.0.22 | 2020 | 异步复制增强、EXPLAIN ANALYZE        |
| 8.0.30 | 2022 | INI格式配置增强、压缩LOB               |
| 8.4    | 2024 | HeatWave向量索引、惰性加载、改进的直方图      |

***

## 附录B：InnoDB关键参数速查

| 参数                                  | 默认值   | 建议值                  | 说明           |
| ----------------------------------- | ----- | -------------------- | ------------ |
| innodb\_buffer\_pool\_size          | 128MB | 物理内存60%-80%          | 缓冲池大小        |
| innodb\_buffer\_pool\_instances     | 1     | ≥1GB时4-8             | 缓冲池实例数       |
| innodb\_log\_file\_size             | 48MB  | 1-4GB                | Redo Log文件大小 |
| innodb\_log\_buffer\_size           | 16MB  | 64-256MB             | Log Buffer大小 |
| innodb\_flush\_log\_at\_trx\_commit | 1     | 1(安全)/2(折中)          | Redo Log刷盘策略 |
| innodb\_flush\_method               | fsync | O\_DIRECT            | 刷盘方式         |
| innodb\_io\_capacity                | 200   | SSD:5000-20000       | 后台刷脏页I/O能力   |
| innodb\_io\_capacity\_max           | 2000  | SSD:20000-40000      | 紧急刷脏页I/O上限   |
| innodb\_lock\_wait\_timeout         | 50    | 业务决定                 | 行锁等待超时(秒)    |
| innodb\_deadlock\_detect            | ON    | ON                   | 死锁检测         |
| innodb\_print\_all\_deadlocks       | OFF   | 生产环境ON               | 打印死锁日志       |
| innodb\_file\_per\_table            | ON    | ON                   | 独立表空间        |
| innodb\_stats\_persistent           | ON    | ON                   | 持久化统计信息      |
| sync\_binlog                        | 1     | 1(安全)/100(性能)        | Binlog刷盘策略   |
| max\_connections                    | 151   | 500-5000             | 最大连接数        |
| thread\_cache\_size                 | 8     | max\_connections的10% | 线程缓存         |
| table\_open\_cache                  | 4000  | 2000-10000           | 表缓存          |
| sort\_buffer\_size                  | 256KB | 1-4MB                | 排序缓冲         |
| join\_buffer\_size                  | 256KB | 1-4MB                | JOIN缓冲       |
| read\_rnd\_buffer\_size             | 256KB | 1-4MB                | MRR读取缓冲      |

***

## 附录C：EXPLAIN type字段速查

| type             | 含义            | 扫描行数  | 优化优先级  |
| ---------------- | ------------- | ----- | ------ |
| system           | 单行系统表         | 1     | ✅ 最优   |
| const            | 主键/唯一索引等值查询   | 1     | ✅      |
| eq\_ref          | JOIN主键/唯一索引   | 每行1条  | ✅      |
| ref              | 非唯一索引等值查询     | 少量    | ✅      |
| fulltext         | 全文索引          | -     | ✅      |
| ref\_or\_null    | ref + IS NULL | 少量    | ✅      |
| index\_merge     | 索引合并          | 少量    | ⚠️     |
| unique\_subquery | IN子查询+唯一索引    | 少量    | ⚠️     |
| index\_subquery  | IN子查询+普通索引    | 少量    | ⚠️     |
| range            | 索引范围扫描        | 中等    | ⚠️     |
| index            | 全索引扫描         | 全部索引行 | ❌      |
| ALL              | 全表扫描          | 全部行   | ❌ 必须优化 |

***

## 附录D：MySQL vs 其他数据库对比

| 特性     | MySQL              | PostgreSQL   | Oracle   | SQL Server      |
| ------ | ------------------ | ------------ | -------- | --------------- |
| 开源     | ✅ GPL              | ✅ PostgreSQL | ❌ 商业     | ❌ 商业(Express免费) |
| 存储引擎   | 可插拔(InnoDB/MyISAM) | 单一           | 单一       | 单一              |
| JSON支持 | ✅ 5.7+             | ✅ 原生JSONB    | ✅        | ✅               |
| 全文索引   | ✅                  | ✅ GIN/GiST   | ✅        | ✅               |
| 分区表    | ✅ RANGE/LIST/HASH  | ✅ 声明式+继承     | ✅        | ✅               |
| 递归CTE  | ✅ 8.0+             | ✅ 8.4+       | ✅        | ✅               |
| 窗口函数   | ✅ 8.0+             | ✅ 8.4+       | ✅        | ✅               |
| 物化视图   | ❌                  | ✅            | ✅        | ✅ 索引视图          |
| 并行查询   | ✅ 8.0+             | ✅            | ✅        | ✅               |
| GIS    | ✅ 5.7+             | ✅ PostGIS    | ✅        | ✅               |
| 适用场景   | Web应用、OLTP         | 复杂查询、GIS     | 企业级、大型系统 | Windows生态       |
| 生态丰富度  | ⭐⭐⭐⭐⭐              | ⭐⭐⭐⭐         | ⭐⭐⭐⭐⭐    | ⭐⭐⭐⭐            |

***

## 附录E：常用运维命令速查

```sql
-- 实例状态
SHOW STATUS LIKE 'Threads%';
SHOW STATUS LIKE 'Queries';
SHOW STATUS LIKE 'Slow_queries';
SHOW STATUS LIKE 'Innodb_row_lock%';
SHOW STATUS LIKE 'Innodb_buffer_pool%';

-- 连接管理
SHOW PROCESSLIST;
SHOW PROCESSLIST\G;
KILL <id>;

-- 表状态
SHOW TABLE STATUS LIKE 'users';
SHOW TABLE STATUS\G;

-- 索引信息
SHOW INDEX FROM users;

-- 变量查看
SHOW VARIABLES LIKE 'innodb%';
SHOW VARIABLES LIKE 'max_connections';
SHOW VARIABLES LIKE 'slow_query%';

-- 复制状态
SHOW MASTER STATUS;
SHOW SLAVE STATUS\G;

-- 引擎状态
SHOW ENGINE INNODB STATUS\G;

-- 二进制日志
SHOW BINARY LOGS;
SHOW BINLOG EVENTS IN 'mysql-bin.000001';

-- 字符集
SHOW CHARACTER SET;
SHOW COLLATION;
```

```bash
# 逻辑备份
mysqldump -u root -p --single-transaction --routines --triggers test_db > backup.sql

# 物理备份
xtrabackup --backup --target-dir=/backup/full -u root -p

# Binlog解析
mysqlbinlog --start-datetime="2024-01-01 00:00:00" mysql-bin.000001

# 慢查询分析
mysqldumpslow -s t -t 10 /var/log/mysql/slow.log

# 检查修复表
mysqlcheck -u root -p --check test_db
mysqlcheck -u root -p --repair test_db
```

***

## 附录F：MySQL 8.0 JSON高级用法

```sql
CREATE TABLE user_profiles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50),
    profile JSON,
    INDEX idx_profile_name ((CAST(profile->>'$.name' AS CHAR(50))))
);

INSERT INTO user_profiles (name, profile) VALUES
('张三', '{"name": "张三", "age": 25, "tags": ["Java", "MySQL"], "address": {"city": "北京", "district": "海淀"}}');

SELECT profile->>'$.name' AS name, profile->>'$.age' AS age FROM user_profiles;

SELECT profile->'$.tags' AS tags FROM user_profiles;

SELECT * FROM user_profiles WHERE profile->>'$.address.city' = '北京';

SELECT
    JSON_EXTRACT(profile, '$.name') AS name,
    JSON_EXTRACT(profile, '$.tags[0]') AS first_tag
FROM user_profiles;

SELECT * FROM user_profiles WHERE JSON_CONTAINS(profile->'$.tags', '"MySQL"');

UPDATE user_profiles SET profile = JSON_SET(profile, '$.age', 26) WHERE id = 1;

UPDATE user_profiles SET profile = JSON_INSERT(profile, '$.email', 'zs@example.com') WHERE id = 1;

UPDATE user_profiles SET profile = JSON_REPLACE(profile, '$.age', 27) WHERE id = 1;

UPDATE user_profiles SET profile = JSON_REMOVE(profile, '$.email') WHERE id = 1;

SELECT
    jt.tag
FROM user_profiles,
     JSON_TABLE(profile->'$.tags', '$[*]' COLUMNS (tag VARCHAR(50) PATH '$')) AS jt;

SELECT id, name, jt.*
FROM user_profiles,
     JSON_TABLE(profile, '$.address' COLUMNS (
         city VARCHAR(50) PATH '$.city',
         district VARCHAR(50) PATH '$.district'
     )) AS jt;
```

**JSON函数速查**：

| 函数                    | 用途                | 示例                                      |
| --------------------- | ----------------- | --------------------------------------- |
| JSON\_EXTRACT         | 提取值（返回JSON类型）     | JSON\_EXTRACT(col, '$.key')             |
| ->                    | JSON\_EXTRACT简写   | col->'$.key'                            |
| ->>                   | 提取值（返回文本，去引号）     | col->>'$.key'                           |
| JSON\_SET             | 设置值（存在则更新，不存在则新增） | JSON\_SET(col, '$.key', 'val')          |
| JSON\_INSERT          | 仅新增（存在则忽略）        | JSON\_INSERT(col, '$.key', 'val')       |
| JSON\_REPLACE         | 仅更新（不存在则忽略）       | JSON\_REPLACE(col, '$.key', 'val')      |
| JSON\_REMOVE          | 删除指定路径            | JSON\_REMOVE(col, '$.key')              |
| JSON\_CONTAINS        | 判断是否包含指定值         | JSON\_CONTAINS(col, '"val"')            |
| JSON\_SEARCH          | 搜索值返回路径           | JSON\_SEARCH(col, 'one', 'val')         |
| JSON\_KEYS            | 返回所有key           | JSON\_KEYS(col)                         |
| JSON\_LENGTH          | 返回元素数量            | JSON\_LENGTH(col, '$.arr')              |
| JSON\_MERGE\_PRESERVE | 合并JSON            | JSON\_MERGE\_PRESERVE(a, b)             |
| JSON\_TABLE           | JSON转关系表（8.0+）    | JSON\_TABLE(col, '$\[\*]' COLUMNS(...)) |

***

## 附录G：面试高频问题

**Q1：InnoDB为什么用B+树而不是B树做索引？**
B+树非叶子节点只存键值不存数据，单个页能存更多键，树更矮（3层约存2000万行），I/O次数少。叶子节点双向链表，范围查询高效。

**Q2：什么是回表？如何避免？**
通过二级索引查到主键值，再回聚簇索引查完整行数据叫回表。覆盖索引（查询列都在索引中）可避免回表。

**Q3：MySQL事务隔离级别怎么选？**
生产默认RR（REPEATABLE READ），InnoDB通过MVCC+Next-Key Lock防止幻读。RC级别没有Gap Lock，并发更好但可能出现不可重复读。一般不建议用RU和Serializable。

**Q4：MVCC怎么实现的？**
每行有隐藏列（trx\_id、roll\_ptr），修改时通过Undo Log形成版本链。SELECT时创建Read View，按可见性规则沿版本链查找可见版本。RC每次SELECT创建新Read View，RR只在首次SELECT创建。

**Q5：Redo Log和Binlog有什么区别？**
Redo Log是InnoDB引擎层日志，循环写入，保证崩溃恢复；Binlog是Server层日志，追加写入，用于主从复制和数据恢复。两阶段提交保证两者一致性。

**Q6：如何优化一条慢SQL？**

1. EXPLAIN查看执行计划；2. 关注type（避免ALL）和Extra（避免filesort/temporary）；3. 添加合适索引（遵循最左前缀）；4. 避免索引失效场景；5. 覆盖索引减少回表；6. 深分页用游标方案。

**Q7：分库分表后怎么做跨片查询？**
尽量通过分片键路由到单分片。必须跨片时：聚合查询各分片汇总；JOIN用冗余字段或应用层组装；深度分页用ES辅助或禁止。

**Q8：MySQL主从延迟怎么处理？**
原因：从库单线程重放慢、大事务、网络延迟。方案：并行复制（多线程SQL线程）、拆分大事务、半同步复制、读写分离时容忍短暂延迟（强制走主库读关键数据）。

***

## 附录H：MySQL与Django集成最佳实践

### H.1 Django数据库配置

```python
# settings/base.py
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "myapp_db",
        "USER": "myapp_user",
        "PASSWORD": os.environ.get("DB_PASSWORD"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            "connect_timeout": 5,
            "read_timeout": 10,
            "write_timeout": 10,
        },
        "CONN_MAX_AGE": 60,
        "ATOMIC_REQUESTS": True,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
```

### H.2 读写分离配置

```python
# settings/base.py
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "myapp_db",
        "USER": "myapp_writer",
        "PASSWORD": os.environ.get("DB_PASSWORD"),
        "HOST": os.environ.get("DB_MASTER_HOST", "localhost"),
        "PORT": "3306",
        "CONN_MAX_AGE": 60,
    },
    "replica": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "myapp_db",
        "USER": "myapp_reader",
        "PASSWORD": os.environ.get("DB_PASSWORD"),
        "HOST": os.environ.get("DB_REPLICA_HOST", "replica-host"),
        "PORT": "3306",
        "CONN_MAX_AGE": 60,
    },
}

DATABASE_ROUTERS = ["myapp.routers.ReadWriteRouter"]
```

```python
# myapp/routers.py
class ReadWriteRouter:
    def db_for_read(self, model, **hints):
        return "replica"

    def db_for_write(self, model, **hints):
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        db_list = ("default", "replica")
        if obj1._state.db in db_list and obj2._state.db in db_list:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return True
```

### H.3 Django模型索引优化

```python
from django.db import models

class Product(models.Model):
    name = models.CharField(max_length=200, db_index=True)
    category = models.ForeignKey("Category", on_delete=models.PROTECT, db_index=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"], name="idx_status_created"),
            models.Index(fields=["category", "status"], name="idx_category_status"),
            models.Index(fields=["-created_at"], name="idx_created_desc"),
            models.Index(
                fields=["name"],
                name="idx_name_prefix",
                opclasses=["varchar_pattern_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(fields=["name", "category"], name="uq_name_category"),
            models.CheckConstraint(check=models.Q(price__gte=0), name="ck_price_nonneg"),
        ]
```

### H.4 Django查询优化技巧

```python
from django.db.models import Prefetch, F, Value, Case, When, Count, Sum

Product.objects.select_related("category").filter(status=1)

Product.objects.prefetch_related(
    Prefetch("reviews", queryset=Review.objects.filter(status="approved"))
).filter(status=1)

Product.objects.filter(category__name="电子").only("name", "price")

Product.objects.filter(price__gt=100).defer("description", "specifications")

Product.objects.annotate(
    review_count=Count("reviews"),
    avg_rating=Avg("reviews__rating"),
).filter(review_count__gt=0)

Product.objects.bulk_create([
    Product(name=f"商品{i}", price=i * 10, category_id=1)
    for i in range(1000)
], batch_size=100)

Product.objects.filter(status=1).update(price=F("price") * 1.1)

Product.objects.filter(status=0).delete()
```

***

## 附录I：MySQL分库分表实战

### I.1 分库分表策略

```
分库分表演进路径：

单库单表 → 读写分离 → 垂直分库 → 水平分库分表

┌─────────────────────────────────────────────────────────┐
│  垂直分库：按业务模块拆分                                 │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ 用户库    │  │ 订单库    │  │ 商品库    │              │
│  │ user_db  │  │ order_db │  │ product_db│              │
│  └──────────┘  └──────────┘  └──────────┘              │
│  每个库独立部署，业务解耦                                 │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  水平分表：按分片键将数据分散到多个表                      │
│                                                          │
│  order_db                                               │
│  ├── orders_0  (user_id % 4 = 0)                        │
│  ├── orders_1  (user_id % 4 = 1)                        │
│  ├── orders_2  (user_id % 4 = 2)                        │
│  └── orders_3  (user_id % 4 = 3)                        │
└─────────────────────────────────────────────────────────┘
```

### I.2 分片键选择

| 分片键类型 | 示例               | 优点    | 缺点    | 适用场景   |
| ----- | ---------------- | ----- | ----- | ------ |
| 取模分片  | user\_id % 4     | 数据均匀  | 扩容需迁移 | 用户相关数据 |
| 范围分片  | create\_time按月   | 易扩展   | 热点问题  | 日志/订单  |
| 一致性哈希 | hash(key) % 2^32 | 扩容迁移少 | 实现复杂  | 通用场景   |
| 枚举分片  | region=CN→db0    | 业务直观  | 不均匀   | 地域相关   |

### I.3 分库分表中间件对比

| 维度    | ShardingSphere-JDBC | ShardingSphere-Proxy | MyCat  | Vitess |
| ----- | ------------------- | -------------------- | ------ | ------ |
| 类型    | Client端             | Proxy端               | Proxy端 | Proxy端 |
| 语言    | Java                | Java                 | Java   | Go     |
| 性能损耗  | 低                   | 中                    | 中      | 低      |
| 侵入性   | 需改代码                | 无需改代码                | 无需改代码  | 无需改代码  |
| 运维复杂度 | 低                   | 中                    | 中      | 高      |
| 生态    | Java生态              | 语言无关                 | 语言无关   | K8s生态  |
| 推荐场景  | Java项目              | 多语言项目                | 遗留系统   | 云原生    |

### I.4 Django中的分库分表方案

```python
import hashlib
from django.db import connections

def get_shard_key(user_id: int) -> int:
    return user_id % 4

def get_shard_table(base_table: str, shard_key: int) -> str:
    return f"{base_table}_{shard_key}"

def get_shard_db(shard_key: int) -> str:
    db_map = {0: "shard_0", 1: "shard_1", 2: "shard_2", 3: "shard_3"}
    return db_map[shard_key]


class ShardedQuerySet:
    def __init__(self, model_class, shard_key: int):
        self.model_class = model_class
        self.shard_key = shard_key
        self.db = get_shard_db(shard_key)

    def create(self, **kwargs):
        kwargs["shard_key"] = self.shard_key
        return self.model_class.objects.using(self.db).create(**kwargs)

    def filter(self, **kwargs):
        return self.model_class.objects.using(self.db).filter(**kwargs)

    def get(self, **kwargs):
        return self.model_class.objects.using(self.db).get(**kwargs)
```

***

## 附录J：MySQL索引原理深度解析

### J.1 B+树索引结构

```
B+树索引结构（3层，约存2000万行）：

                    ┌─────────────┐
                    │  根节点(页)   │
                    │  [10|20|30]  │  ← 非叶子节点只存键值
                    └──┬───┬───┬──┘
               ┌──────┘   │   └──────┐
               ▼          ▼          ▼
         ┌──────────┐ ┌──────────┐ ┌──────────┐
         │中间节点(页)│ │中间节点(页)│ │中间节点(页)│
         │[1|5|10]  │ │[11|15|20]│ │[21|25|30]│
         └──┬──┬──┬─┘ └──┬──┬──┬─┘ └──┬──┬──┬─┘
            │  │  │       │  │  │       │  │  │
            ▼  ▼  ▼       ▼  ▼  ▼       ▼  ▼  ▼
         ┌─────────────────────────────────────────┐
         │         叶子节点（双向链表）               │
         │  [1→2→3→5] ⇄ [10→11→12] ⇄ [20→21→30]  │
         │   ↑每个叶子节点=一个数据页(16KB)          │
         │   ↑包含完整行数据(聚簇索引)或主键值(二级索引)│
         └─────────────────────────────────────────┘

关键参数：
  InnoDB页大小：16KB
  非叶子节点每个键值+指针≈12字节
  一个页可存约 16KB/12B ≈ 1360 个键值
  3层B+树可存：1360 × 1360 × N行 ≈ 2000万行
```

### J.2 聚簇索引 vs 二级索引

```
聚簇索引（主键索引）：
┌──────────────────────────────────────────┐
│  叶子节点存储完整行数据                    │
│  一张表只有一个聚簇索引                    │
│  默认使用主键，无主键用唯一非空索引        │
│  都没有则生成隐藏row_id                   │
│                                          │
│  优势：主键查询极快（无需回表）            │
│  劣势：二级索引需回表查询                  │
│        插入顺序依赖主键顺序                │
└──────────────────────────────────────────┘

二级索引（非主键索引）：
┌──────────────────────────────────────────┐
│  叶子节点存储主键值+索引列值               │
│  查询非索引列需回表到聚簇索引              │
│                                          │
│  回表过程：                               │
│  1. 在二级索引B+树查找 → 得到主键值        │
│  2. 在聚簇索引B+树查找 → 得到完整行数据    │
│  3. 需要两次B+树查找                      │
│                                          │
│  覆盖索引：查询列都在索引中，无需回表       │
│  SELECT name, age FROM users              │
│  WHERE name = '张三'                      │
│  INDEX (name, age) ← 覆盖索引             │
└──────────────────────────────────────────┘
```

### J.3 索引失效场景

| 场景      | 示例                              | 原因         | 解决方案                               |
| ------- | ------------------------------- | ---------- | ---------------------------------- |
| 函数计算    | `WHERE YEAR(created_at) = 2024` | 破坏索引有序性    | `WHERE created_at >= '2024-01-01'` |
| 隐式类型转换  | `WHERE varchar_col = 123`       | 类型不匹配      | 保持类型一致                             |
| 模糊查询前缀% | `WHERE name LIKE '%张'`          | 无法利用B+树有序性 | 全文索引或ES                            |
| OR条件    | `WHERE a=1 OR b=2`              | 可能无法用索引    | UNION ALL拆分                        |
| 不等于     | `WHERE status != 1`             | 选择性差       | 改为IN查询                             |
| 索引列运算   | `WHERE id + 1 = 10`             | 破坏有序性      | `WHERE id = 9`                     |
| 最左前缀违反  | INDEX(a,b,c)查b                  | 跳过a列       | 遵循最左前缀                             |
| NOT IN  | `WHERE id NOT IN (...)`         | 优化器放弃索引    | 改为LEFT JOIN                        |

### J.4 索引设计原则

```
索引设计核心原则：

1. 选择性原则
   选择性 = DISTINCT(col) / COUNT(*)
   选择性越高，索引效果越好
   选择性<0.1的列不适合单独建索引

2. 最左前缀原则
   INDEX(a, b, c) 可覆盖：
   ✅ WHERE a = ?
   ✅ WHERE a = ? AND b = ?
   ✅ WHERE a = ? AND b = ? AND c = ?
   ❌ WHERE b = ?              （跳过a）
   ❌ WHERE c = ?              （跳过a和b）
   ✅ WHERE a = ? AND c = ?    （a走索引，c不走）

3. 覆盖索引原则
   将查询需要的列都包含在索引中，避免回表
   SELECT a, b FROM t WHERE a = ?
   INDEX(a, b) ← 覆盖索引，无需回表

4. 避免冗余索引
   INDEX(a) 和 INDEX(a, b) → INDEX(a) 冗余
   保留 INDEX(a, b) 即可覆盖 a 的查询

5. 联合索引列顺序
   等值查询列在前，范围查询列在后
   WHERE a = ? AND b > ? → INDEX(a, b) ✅
   WHERE a > ? AND b = ? → INDEX(b, a) ✅
```

