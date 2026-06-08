# 零基础到进阶的MySQL系统学习手册（增强版）

## 前言

本手册在原版基础上进行全面增强，为每个操作步骤提供详细的执行说明、技术原理深度解析、代码示例辅助说明和扩展背景知识。手册中的所有实战案例均基于MySQL 8.0版本，可直接复制运行。

**手册特色**：

- **原理驱动**：每个知识点先讲底层原理，再给实战SQL，知其然更知其所以然
- **场景导向**：结合真实业务场景，说明何时用、怎么用、为什么用
- **对比选型**：关键决策点提供对比表格，帮助做出正确选择
- **增强版新增**：每个操作步骤附带【执行说明】【技术原理】【扩展知识】三大增强板块

---

## 准备工作

在开始学习前，请确保你已经安装了MySQL 8.0。可以通过以下命令检查MySQL版本：

```sql
SELECT VERSION();
```

> **【执行说明】** 在MySQL客户端（命令行或图形工具如Navicat、DBeaver）中执行此SQL，返回当前MySQL服务器的版本号，如 `8.0.35`。确认版本号以8.0开头，才能使用本手册中涉及窗口函数、CTE等8.0新特性。

> **【技术原理】** `SELECT VERSION()` 是MySQL内置函数，读取的是编译时写入服务器的版本常量。MySQL服务器启动时将版本信息加载到内存中的全局变量中，调用此函数直接从内存读取，不涉及磁盘I/O。

> **【扩展知识】** MySQL 8.0相比5.7的主要改进：窗口函数、CTE递归查询、降序索引、不可见索引、JSON增强、角色管理、更细粒度的权限控制、默认认证插件从mysql_native_password变为caching_sha2_password。

---

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

> **【执行说明】**
> - `-u root`：指定用户名为root（MySQL管理员账户）
> - `-p`：提示输入密码（密码不会回显，安全考虑）
> - `-h 127.0.0.1`：指定MySQL服务器主机地址，默认为localhost
> - `-P 3306`：指定端口号，MySQL默认端口3306（注意大写P，小写p是密码）
> - `-D test_db`：连接后直接使用test_db数据库，等同于连接后执行`USE test_db`
>
> 执行后，终端提示符变为 `mysql>`，表示已成功连接。

> **【技术原理】**
> MySQL连接过程本质是TCP三次握手+MySQL认证协议：
> 1. 客户端向服务器发起TCP连接请求（默认端口3306）
> 2. TCP三次握手建立连接
> 3. 服务器发送握手包（包含服务器版本、加密盐值salt）
> 4. 客户端使用salt对密码进行SHA1加密后发送认证包
> 5. 服务器验证用户名、密码、权限（从mysql.user表读取）
> 6. 认证通过，连接建立，分配线程处理该连接的请求
>
> MySQL 8.0默认使用 `caching_sha2_password` 认证插件（SHA-256加密），5.7使用 `mysql_native_password`（SHA-1加密）。如果客户端不支持新认证方式，可在my.cnf中设置 `default_authentication_plugin=mysql_native_password`。

> **【扩展知识】**
> - 连接数限制：由 `max_connections` 参数控制，默认151。生产环境建议500-5000
> - 连接超时：`wait_timeout`（默认28800秒=8小时），非交互式连接空闲超时；`interactive_timeout`，交互式连接空闲超时
> - 连接池：应用层使用连接池（如HikariCP）复用连接，避免频繁创建/销毁TCP连接的开销
> - SSL连接：生产环境建议启用SSL加密传输，`mysql -u root -p --ssl-mode=REQUIRED`

### 1.2 数据库操作

**功能说明**：创建、查看、删除数据库。

**实战案例**：

```sql
CREATE DATABASE test_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

SHOW DATABASES;

USE test_db;

DROP DATABASE IF EXISTS test_db;
```

> **【执行说明】**
>
> - `CREATE DATABASE test_db`：创建名为test_db的数据库
> - `CHARACTER SET utf8mb4`：指定字符集为utf8mb4（支持4字节UTF-8，包括emoji）
> - `COLLATE utf8mb4_unicode_ci`：指定排序规则为unicode通用排序，ci表示大小写不敏感
> - `SHOW DATABASES`：列出所有数据库
> - `USE test_db`：切换到test_db数据库，后续SQL默认在此库执行
> - `DROP DATABASE IF EXISTS test_db`：如果存在则删除，避免报错

> **【技术原理】**
> - **数据库在文件系统中的体现**：MySQL每个数据库对应数据目录下的一个子目录。在Linux默认路径 `/var/lib/mysql/` 下，创建test_db会生成 `/var/lib/mysql/test_db/` 目录
> - **字符集与排序规则**：utf8mb4是utf8的超集。MySQL中的utf8实际是utf8mb3（最多3字节），无法存储emoji等4字节字符。utf8mb4才是真正的UTF-8。排序规则后缀：`_ci`(大小写不敏感)、`_cs`(大小写敏感)、`_bin`(二进制比较)
> - **CREATE DATABASE的底层操作**：在数据目录创建子目录，并在该目录下生成db.opt文件（存储字符集和排序规则信息）

> **【扩展知识】**
> - 系统数据库说明：
>   - `information_schema`：元数据信息（表结构、索引、权限等），虚拟库，不占磁盘
>   - `mysql`：系统权限表、时区信息、帮助信息
>   - `performance_schema`：性能监控数据（锁等待、I/O统计等）
>   - `sys`：基于performance_schema的易读视图，DBA诊断利器
> - 生产环境创建数据库的完整建议：
>   ```sql
>   CREATE DATABASE app_db
>     CHARACTER SET utf8mb4
>     COLLATE utf8mb4_unicode_ci
>     DEFAULT ENCRYPTION='N';
>   ```

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

> **【执行说明】**
> - `CREATE TABLE users (...)`：创建users表，定义各列的名称、类型和约束
> - `AUTO_INCREMENT`：id列自动递增，插入数据时无需指定id值
> - `PRIMARY KEY`：主键约束，id列值唯一且不为NULL
> - `NOT NULL`：name列不允许为空
> - `DEFAULT CURRENT_TIMESTAMP`：created_at列默认值为当前时间
> - `SHOW TABLES`：列出当前数据库的所有表
> - `DESCRIBE users`：查看users表的结构（列名、类型、是否允许NULL、键、默认值、额外信息）
> - `ALTER TABLE users ADD COLUMN phone VARCHAR(20)`：给users表添加phone列
> - `DROP TABLE IF EXISTS users`：如果存在则删除表及所有数据

> **【技术原理】**
> - **CREATE TABLE的底层操作**：
>   1. InnoDB在数据目录下创建 `.ibd` 文件（独立表空间模式，`innodb_file_per_table=ON`）
>   2. 在聚簇索引B+树中创建空的根节点页
>   3. 在数据字典（mysql系统表空间）中注册表结构信息
>   4. `.ibd`文件内部按16KB页组织，初始文件大小约96KB-112KB（含文件头、区管理信息等）
>
> - **AUTO_INCREMENT原理**：
>   - InnoDB在内存中维护一个自增计数器，每张表对应一个
>   - 插入时先获取当前自增值，然后+1写回计数器
>   - MySQL 8.0将自增计数器持久化到Redo Log中，重启后不会重置（5.7及之前版本重启后可能重置为max(id)+1，导致主键冲突）
>   - `innodb_autoinc_lock_mode`控制自增锁模式：0(传统)、1(连续，默认)、2(交叉，性能最高但可能不连续)
>
> - **ALTER TABLE的执行方式**：
>   - MySQL 8.0支持Online DDL（在线DDL），大部分ALTER操作不阻塞DML
>   - 底层策略：COPY（创建临时表复制数据，锁表）、INPLACE（原地修改，不锁表）、INSTANT（仅修改元数据，8.0.12+支持，瞬间完成）
>   - `ALTER TABLE ... ADD COLUMN` 在8.0中是INSTANT操作（有限制：只能加到表末尾）

> **【扩展知识】**
>
> - 查看建表完整语句：`SHOW CREATE TABLE users\G`（\G表示竖向显示）
> - 查看表状态：`SHOW TABLE STATUS LIKE 'users'\G`（包含引擎、行数、数据大小、索引大小等）
> - 临时表：`CREATE TEMPORARY TABLE ...`，会话结束后自动删除，对其他会话不可见
> - 表名大小写：由 `lower_case_table_names` 参数控制。0=区分大小写(Linux默认)，1=不区分(Windows默认)，2=存储区分但查找不区分(macOS默认)。**此参数必须在初始化时设置，之后修改会导致数据不一致**

### 1.4 关闭连接

```bash
EXIT;

QUIT;
```

> **【执行说明】** EXIT和QUIT效果相同，都会关闭当前MySQL连接并返回操作系统命令行。也可使用快捷键 `Ctrl+D`。

> **【技术原理】** 关闭连接时MySQL服务器执行：1. 释放该连接占用的线程资源；2. 回滚未提交的事务；3. 释放该连接持有的所有锁；4. 将线程放回线程缓存（如果 `thread_cache_size > 0`）或销毁线程。

---

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

> **【执行说明】** 当你在客户端输入一条SQL并回车时，这条SQL就经历了上述完整流程。理解这个流程是排查SQL性能问题的基础。

> **【技术原理】** 各层职责详解：
>
> **1. 连接器**：负责与客户端建立连接、获取权限、维持和管理连接。连接建立后，权限信息缓存在内存中，修改权限后需重新连接才生效。
>
> **2. 解析器**：
> - **词法分析**：将SQL字符串拆分为token（关键字、表名、列名、运算符等）。例如 `SELECT name FROM users WHERE id=1` 被拆分为 [SELECT, name, FROM, users, WHERE, id, =, 1]
> - **语法分析**：根据MySQL语法规则将token构建为抽象语法树(AST)。如果SQL语法错误，在此阶段报错，如 `ERROR 1064 (42000): You have an error in your SQL syntax`
>
> **3. 优化器**：决定使用哪个索引、JOIN表的连接顺序、是否使用临时表等。优化器基于成本估算(Cost-Based Optimizer)选择执行计划，成本 = I/O成本 + CPU成本。但优化器并不总是最优的，有时需要通过hint或改写SQL引导优化器。
>
> **4. 执行器**：校验当前用户对目标表的权限，然后根据执行计划调用存储引擎的API接口读写数据。
>
> **5. 存储引擎**：真正负责数据的存储和读取。InnoDB、MyISAM等引擎有不同的存储格式和特性。Server层通过统一的Handler API与引擎层交互。

> **【扩展知识】**
> - **查询缓存为何在8.0中被移除**：查询缓存以SQL文本为key缓存结果集，但任何对表的修改都会使该表所有缓存失效。在高并发写入场景下，缓存命中率极低，且维护缓存的锁竞争成为性能瓶颈。8.0推荐使用Redis等外部缓存替代。
> - **Prepared Statement**：预编译语句可以跳过解析器的词法/语法分析阶段，提升重复执行相同结构SQL的性能，同时防止SQL注入。

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

> **【执行说明】**
>
> - `SHOW STATUS LIKE 'Innodb_buffer_pool%'`：查看Buffer Pool的运行状态，包括总页数、空闲页数、脏页数、读请求次数等
> - `SHOW VARIABLES LIKE 'innodb_buffer_pool%'`：查看Buffer Pool相关配置参数
> - `SET GLOBAL innodb_buffer_pool_size = 4294967296`：动态调整Buffer Pool大小为4GB（8.0支持在线调整）。调整过程是chunk级别的渐进式缩放，不会阻塞正常操作

> **【技术原理】** Buffer Pool是InnoDB最重要的内存结构，其核心思想是**用内存换磁盘I/O**：
>
> - **页式缓存**：InnoDB以页(默认16KB)为单位管理数据。读取数据时，先将整个页从磁盘加载到Buffer Pool，再从页中提取所需行。修改数据时，先修改Buffer Pool中的页（标记为脏页），再由后台线程异步刷回磁盘
> - **缓存命中率**：理想状态应>99%。计算公式：`1 - Innodb_buffer_pool_reads / Innodb_buffer_pool_read_requests`。命中率低说明Buffer Pool太小，频繁读磁盘
> - **多实例**：当Buffer Pool > 1GB时，建议设置多个实例（`innodb_buffer_pool_instances`），每个实例有独立的LRU链表和互斥锁，减少锁竞争
> - **预读机制**：InnoDB支持线性预读（连续读取一个extent的页时，预读下一个extent）和随机预读（一个extent中某些页被访问时，预读整个extent）

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

> **【技术原理】** 为什么不用标准LRU？
>
> 标准LRU的问题：当执行全表扫描时（如 `SELECT * FROM big_table`），会将大量只访问一次的冷数据页加载到LRU头部，把真正的热数据挤出去，导致后续查询大量缓存未命中。
>
> InnoDB改良LRU的解决方案：
> 1. 新读入的页插入到Midpoint（Young区和Old区的交界处），而非LRU头部
> 2. 页必须在Old区存活超过 `innodb_old_blocks_time`（默认1000ms）后，再次被访问才能移入Young区
> 3. 这样，全表扫描时读入的页在1000ms内不会被再次访问，不会进入Young区，不会污染热数据
>
> **Young区保护机制**：为防止频繁访问某个页导致Young区频繁调整，InnoDB规定页移到Young区头部时，只有距离上次移动超过 `innodb_lru_scan_depth` 间隔才会真正移动。

> **【扩展知识】**
> - Buffer Pool预热：MySQL 8.0支持 `innodb_buffer_pool_dump_pct`（默认25%），关闭时将25%的热页信息保存到 `ib_buffer_pool` 文件，启动时自动加载预热
> - 脏页刷盘策略：由 `innodb_io_capacity` 控制刷脏速度，SSD建议设为5000-20000。后台线程每秒按此速率刷脏页

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

> **【执行说明】** 每个表对应一个 `.ibd` 文件（当 `innodb_file_per_table=ON` 时），可用 `ls -lh /var/lib/mysql/数据库名/` 查看文件大小。

> **【技术原理】**
>
> **页结构各部分详解**：
> - **File Header**（38字节）：记录页的通用信息，包括页号(FIL_PAGE_OFFSET)、上一页/下一页指针(FIL_PAGE_PREV/NEXT)、页类型(FIL_PAGE_TYPE)、校验和等
> - **Page Header**（56字节）：记录页的状态信息，包括本页槽位数(PAGE_N_DIR_SLOTS)、已用记录数(PAGE_N_RECS)、第一条记录地址(PAGE_FIRST)等
> - **Infimum + Supremum**：两条虚拟记录，Infimum表示"比任何记录都小"，Supremum表示"比任何记录都大"，构成记录链表的边界
> - **User Records**：实际存储的数据行，以单链表组织（按主键排序），每条记录包含隐藏列(DB_TRX_ID, DB_ROLL_PTR, DB_ROW_ID)
> - **Free Space**：未使用的空间，新记录从此处分配。Free Space耗尽时，该页满
> - **Page Directory**：将记录分组，每组最后一条记录的偏移量存入槽位。查找时先在Page Directory中二分查找定位到组，再在组内遍历，将O(N)降为O(logN)
> - **File Trailer**（8字节）：存储页的校验和和LSN，用于检测页是否完整写入磁盘（检测页撕裂）
>
> **区(Extent)与段(Segment)**：
> - **区**：连续的64个页（1MB），InnoDB按区分配空间
> - **段**：一个索引占用两个段——叶子节点段和非叶子节点段。段由多个区组成
> - 这种分层管理方式使得大表的空间分配效率更高

> **【扩展知识】**
> - `innodb_page_size`：可配置页大小为4K/8K/16K/32K/64K，默认16K。修改后需重新初始化数据目录
> - 页压缩：MySQL 8.0支持 `COMPRESSION="zlib"` 对页进行透明压缩，减少磁盘占用

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

> **【执行说明】**
> - `SHOW STATUS LIKE 'Innodb_dblwr%'`：查看双写缓冲的写入次数和页数
>   - `Innodb_dblwr_pages_written`：已写入双写缓冲的页数
>   - `Innodb_dblwr_writes`：双写缓冲写入次数
>   - 正常情况下 pages_written/writes ≈ 64（每次批量写64页）
> - `SHOW VARIABLES LIKE 'innodb_doublewrite'`：查看双写缓冲是否开启（默认ON）

> **【技术原理】**
> 为什么Redo Log不能恢复页撕裂？
> - Redo Log记录的是"对某页某偏移量的修改操作"（如：将页号100偏移量50处的4字节从0x00000001改为0x00000002）
> - 如果页本身已经撕裂（部分写入），页的起始状态就不确定，Redo Log无法正确重放
> - Doublewrite Buffer保存了页的完整副本，先恢复完整页，再重放Redo Log
>
> 性能影响：
> - 双写缓冲是顺序写（连续的128个页），一次fsync即可完成
> - 相比随机写数据文件，顺序写的性能开销很小
> - 整体性能影响约5%-10%，换来的是崩溃恢复的可靠性

> **【扩展知识】**
> - 如果文件系统支持原子写入（如ZFS的Copy-On-Write），可关闭双写缓冲：`SET GLOBAL innodb_doublewrite = OFF;`
> - MySQL 8.0.20+支持 `innodb_doublewrite_files` 和 `innodb_doublewrite_dir` 自定义双写文件位置
> - 某些SSD设备支持原子写入（保证16KB写入的原子性），也可关闭双写缓冲

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

> **【执行说明】**
> - `SHOW STATUS LIKE 'Innodb_ibuf%'`：查看Change Buffer的大小和操作次数
>   - `Innodb_ibuf_size`：Change Buffer中缓存的记录数
>   - `Innodb_ibuf_merges`：merge操作次数
>   - `Innodb_ibuf_merges / Innodb_ibuf_inserts` 比值越高，说明merge效率越低
> - `innodb_change_buffer_max_size`：可动态调整，写多读少场景可增大到50

> **【技术原理】**
> 为什么Change Buffer只适用于非唯一二级索引？
> - **唯一索引**在插入时必须检查唯一性约束，必须将索引页读入Buffer Pool进行查找，无法延迟
> - **非唯一二级索引**不需要检查唯一性，且二级索引的修改通常不是查询的关键路径，可以延迟合并
> - **聚簇索引**的修改直接影响数据行，不能延迟
>
> Change Buffer在Buffer Pool中占用空间，如果Buffer Pool较小，Change Buffer可能挤占数据页空间。因此内存小的服务器建议减小 `innodb_change_buffer_max_size`。

> **【扩展知识】**
> - Change Buffer的merge触发时机：1) 查询访问该页时；2) 后台线程定期merge；3) 服务器空闲时；4) 关闭时全部merge
> - 在SSD上，随机读性能大幅提升，Change Buffer的收益相对减小，但仍可减少写放大

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

> **【执行说明】**
> - `SHOW STATUS LIKE 'Innodb_adaptive_hash%'`：查看AHI的使用情况
>   - `Innodb_adaptive_hash_hash_searches`：通过AHI查找的次数
>   - `Innodb_adaptive_hash_non_hash_searches`：未通过AHI查找的次数
>   - 如果 hash_searches / (hash_searches + non_hash_searches) 很低，说明AHI利用率不高
> - `Innodb_adaptive_hash_wait`：等待AHI锁的次数，如果很高说明AHI成为瓶颈

> **【技术原理】**
> AHI的哈希表是全局的（所有Buffer Pool实例共享），使用读写锁保护。在高并发写入场景下：
> - 写操作需要获取排他锁重建哈希表
> - 读操作需要获取共享锁
> - 大量并发读写导致锁等待，反而降低性能
>
> 这就是为什么高并发写入场景建议关闭AHI。

> **【扩展知识】**
> - AHI是InnoDB自动管理的，无法手动指定对哪些索引建哈希
> - 关闭AHI后，已建立的哈希索引会被逐步淘汰，不影响数据正确性
> - Percona Server提供了更细粒度的AHI控制

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

> **【执行说明】**
> - `SHOW TABLE STATUS LIKE 'users'`：查看表的引擎、行数、数据大小等信息。Engine列显示当前引擎
> - `ALTER TABLE users ENGINE = InnoDB`：将表引擎从MyISAM转为InnoDB。此操作会重建表（COPY方式），大表耗时较长，建议在低峰期执行
> - `SHOW ENGINES`：查看MySQL支持的所有存储引擎及状态

> **【技术原理】**
> - **MyISAM的COUNT(*)为什么是O(1)**：MyISAM表的数据文件头部存储了表的行数，SELECT COUNT(*)直接读取这个值。但前提是没有WHERE条件，有WHERE条件时仍需全表扫描
> - **InnoDB的COUNT(*)需要遍历**：因为InnoDB支持MVCC，不同事务可能看到不同版本的行数，无法存储一个全局行数。InnoDB会选择最小的二级索引来遍历（非聚簇索引更小，I/O更少）
> - **引擎转换的底层过程**：ALTER TABLE ... ENGINE=InnoDB 实际是：1) 创建新结构的临时表；2) 逐行复制数据；3) 删除旧表；4) 将临时表重命名为原表名

> **【扩展知识】**
> - MySQL 8.0中MyISAM已不推荐使用，仅保留兼容性
> - 替代方案：对于只读/读多写少的分析场景，推荐使用ColumnStore（MariaDB）或ClickHouse等列式存储引擎
> - Memory引擎：数据存储在内存中，重启丢失，适合临时表。但只支持表级锁，不支持BLOB/TEXT

---

## 模块三：SQL语法

### 3.1 插入数据

```sql
INSERT INTO users (name, age, email) VALUES ('张三', 25, 'zhangsan@example.com');

INSERT INTO users (name, age, email) VALUES
('李四', 30, 'lisi@example.com'),
('王五', 28, 'wangwu@example.com');

INSERT INTO users_backup SELECT * FROM users WHERE age > 25;
```

> **【执行说明】**
> - 第1条：单行插入，指定列名和值
> - 第2条：批量插入，一条SQL插入多行，比多次单行插入效率高（减少SQL解析和网络往返次数）
> - 第3条：从查询结果插入，将users表中age>25的行复制到users_backup表

> **【技术原理】**
> - **批量插入为什么更快**：1) 一次SQL解析代替N次解析；2) 一次网络往返代替N次；3) InnoDB可以将多行写入同一个数据页，减少页分裂；4) 一次事务提交代替N次（减少fsync次数）
> - **INSERT的执行流程**：1) 优化器确定插入位置（通过主键索引B+树定位）；2) 在聚簇索引中插入行数据；3) 在每个二级索引中插入索引记录（可能触发Change Buffer）；4) 写Undo Log（用于回滚）；5) 写Redo Log（用于持久化）
> - **INSERT ... SELECT的锁行为**：在REPEATABLE READ隔离级别下，MySQL会对SELECT的源表加共享Next-Key Lock，防止其他事务插入导致幻读。如果不想锁源表，可将隔离级别设为READ COMMITTED

> **【扩展知识】**
> - 插入冲突处理：
>   ```sql
>   INSERT INTO users (id, name) VALUES (1, '张三')
>     ON DUPLICATE KEY UPDATE name = '张三';
>   INSERT IGNORE INTO users (id, name) VALUES (1, '张三');
>   REPLACE INTO users (id, name) VALUES (1, '张三');
>   ```
>   - `ON DUPLICATE KEY UPDATE`：主键/唯一键冲突时执行UPDATE
>   - `INSERT IGNORE`：冲突时跳过，不报错
>   - `REPLACE`：冲突时先DELETE再INSERT（注意：会触发DELETE触发器，且自增ID会变）
> - 批量插入优化：`SET GLOBAL innodb_flush_log_at_trx_commit = 0;` 可大幅提升批量插入性能，但崩溃可能丢数据

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

> **【执行说明】**
> - `SELECT *`：返回所有列，生产环境不推荐（增加网络传输和I/O开销，且表结构变更时可能影响应用）
> - `LIMIT 10 OFFSET 20`：跳过前20行，返回10行（第3页，每页10条）。注意OFFSET很大时性能差
> - `LIKE '张%'`：前缀匹配可以使用索引；`LIKE '%张'` 后缀匹配无法使用索引
> - `BETWEEN 25 AND 30`：等价于 `age >= 25 AND age <= 30`，包含边界值
> - `IN (25, 28, 30)`：等价于 `age = 25 OR age = 28 OR age = 30`，IN列表中的值数量建议不超过500

> **【技术原理】**
> - **SELECT执行流程**：1) 连接器验证权限；2) 解析器生成AST；3) 优化器选择执行计划；4) 执行器调用存储引擎接口逐行获取数据；5) 若有WHERE条件，在Server层过滤不满足条件的行；6) 返回结果集
> - **LIMIT OFFSET性能问题**：`OFFSET 100000` 意味着MySQL需要扫描并丢弃前100000行，再返回后续行。优化方案：
>   ```sql
>   SELECT * FROM users WHERE id > 100000 ORDER BY id LIMIT 10;
>   ```
>   利用索引的有序性，直接定位到起始位置，避免扫描丢弃大量行
> - **LIKE与索引**：B+树按前缀排序，`LIKE '张%'` 可以利用B+树的前缀有序性走索引范围扫描；`LIKE '%张'` 无法确定前缀，只能全表扫描

> **【扩展知识】**
> - 深度分页优化方案对比：
>   | 方案 | 原理 | 优缺点 |
>   |------|------|--------|
>   | 游标分页(WHERE id > ?) | 利用索引有序性 | ✅性能好 ❌不支持跳页 |
>   | 子查询优化 | 先查主键再回表 | ✅比OFFSET好 ❌仍需扫描 |
>   | ES辅助 | 用ES做分页查询 | ✅性能好 ❌架构复杂 |
>   | 禁止深度分页 | 限制最大页数 | ✅简单 ❌用户体验差 |

### 3.3 更新数据

```sql
UPDATE users SET age = 26 WHERE name = '张三';

UPDATE users SET age = age + 1 WHERE age > 25;

UPDATE users SET age = 26, email = 'new@example.com' WHERE id = 1;
```

> **【执行说明】**
> - UPDATE必须带WHERE条件，否则会更新全表数据
> - `SET age = age + 1`：基于当前值做增量更新，不是替换
> - 多列更新用逗号分隔

> **【技术原理】**
> - **UPDATE的执行流程**：1) 优化器通过WHERE条件定位到目标行（走索引或全表扫描）；2) 对目标行加X锁（排他锁）；3) 将旧值写入Undo Log；4) 修改Buffer Pool中的数据页；5) 写Redo Log；6) 提交后释放锁
> - **WHERE条件无索引时的后果**：InnoDB会对每一行加锁，然后在Server层判断是否满足条件，不满足则释放锁。这实际上等同于表级锁，严重影响并发性能
> - **UPDATE的原子性**：单条UPDATE语句本身就是一个事务，要么全部更新成功，要么全部回滚

> **【扩展知识】**
> - 多表UPDATE：`UPDATE users u JOIN orders o ON u.id = o.user_id SET u.status = 1 WHERE o.amount > 1000;`
> - UPDATE与ORDER BY/LIMIT：`UPDATE users SET status = 1 WHERE status = 0 ORDER BY id LIMIT 100;` 可用于分批更新，避免长事务

### 3.4 删除数据

```sql
DELETE FROM users WHERE name = '张三';

DELETE FROM users;

TRUNCATE TABLE users;
```

> **【执行说明】**
> - `DELETE FROM users WHERE name = '张三'`：删除满足条件的行，返回删除的行数
> - `DELETE FROM users`：删除表中所有行，但保留表结构。逐行删除，记录Undo Log和Redo Log，可回滚
> - `TRUNCATE TABLE users`：清空表，DDL操作。不逐行删除，直接释放数据页，不可回滚，速度极快

> **【技术原理】**
> - **DELETE vs TRUNCATE 本质区别**：
>   | 维度 | DELETE | TRUNCATE |
>   |------|--------|----------|
>   | 类型 | DML | DDL |
>   | 逐行删除 | 是 | 否，直接释放页 |
>   | 事务 | 可回滚 | 不可回滚 |
>   | 触发器 | 触发 | 不触发 |
>   | 自增ID | 保留当前值 | 重置为1 |
>   | 速度 | 慢（逐行+日志） | 快（直接释放） |
>   | 空间回收 | 不回收（产生碎片） | 回收 |
> - **DELETE的性能问题**：大表DELETE会产生大量Undo Log和Redo Log，且删除后空间不会立即回收（产生碎片），需要 `OPTIMIZE TABLE` 或 `ALTER TABLE ... ENGINE=InnoDB` 重建表来回收空间

> **【扩展知识】**
> - 分批删除大表数据：
>   ```sql
>   DELETE FROM logs WHERE created_at < '2023-01-01' LIMIT 1000;
>   ```
>   循环执行直到影响行数为0，避免长事务和锁持有时间过长
> - 安全删除大表：先硬链接，再TRUNCATE
>   ```bash
>   ln /var/lib/mysql/db/large_table.ibd /var/lib/mysql/db/large_table.ibd.hardlink
>   ```
>   TRUNCATE只删除文件名（inode引用计数-1），实际磁盘空间在删除硬链接后才释放

### 3.5 聚合函数

```sql
SELECT AVG(age) AS avg_age FROM users;

SELECT MAX(age) AS max_age, MIN(age) AS min_age FROM users;

SELECT COUNT(*) AS total_users FROM users;

SELECT SUM(amount) AS total_amount FROM orders;
```

> **【执行说明】**
> - `COUNT(*)`：统计总行数，包括NULL值。InnoDB推荐使用 `COUNT(*)` 或 `COUNT(1)`，两者等价
> - `COUNT(列名)`：统计该列非NULL的行数
> - `AVG/MAX/MIN/SUM`：自动忽略NULL值

> **【技术原理】**
> - **COUNT(*)的执行方式**：InnoDB选择最小的二级索引来遍历（而非聚簇索引），因为二级索引占用空间更小，I/O更少。如果没有二级索引，则遍历聚簇索引
> - **聚合函数的执行流程**：1) 存储引擎逐行返回数据；2) Server层对每行数据应用聚合函数累加；3) 最终返回聚合结果。如果没有WHERE条件，需要全表/全索引扫描
> - **COUNT的优化**：如果只需要知道是否存在，用 `EXISTS` 或 `LIMIT 1` 代替 `COUNT(*)`

> **【扩展知识】**
> - 精确COUNT的替代方案：对于超大表，实时COUNT(*)开销大，可用：1) Redis维护计数器；2) 额外统计表定期更新；3) INFORMATION_SCHEMA的TABLE_ROWS（估算值，不精确）

### 3.6 分组查询

```sql
SELECT age, COUNT(*) AS count FROM users GROUP BY age;

SELECT age, COUNT(*) AS count FROM users GROUP BY age HAVING count > 1;

SELECT dept_id, age, COUNT(*) AS count FROM employees GROUP BY dept_id, age;
```

> **【执行说明】**
> - `GROUP BY age`：按age列分组，每组返回一行聚合结果
> - `HAVING count > 1`：对分组后的结果进行过滤（类似WHERE，但WHERE在分组前过滤，HAVING在分组后过滤）
> - 多列分组：按dept_id和age的组合分组

> **【技术原理】**
> - **GROUP BY执行流程**：1) 优化器选择使用索引排序或临时表排序；2) 如果GROUP BY列有索引，利用B+树有序性直接分组；3) 如果没有索引，创建内部临时表（内存tmp_table_size或磁盘），按分组键排序后聚合
> - **SQL执行顺序**：FROM → WHERE → GROUP BY → HAVING → SELECT → ORDER BY → LIMIT
> - **临时表的性能影响**：`EXPLAIN` 中出现 `Using temporary` 表示使用了临时表，需要优化。解决方案：给GROUP BY列加索引，或调整 `tmp_table_size` 和 `max_heap_table_size` 增大内存临时表容量

> **【扩展知识】**
> - MySQL 8.0对GROUP BY不再默认排序（5.7及之前版本按GROUP BY列排序）。如果需要排序，显式加ORDER BY
> - `ONLY_FULL_GROUP_BY` 模式（8.0默认开启）：SELECT中的非聚合列必须出现在GROUP BY中，否则报错

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

> **【执行说明】**
> - `INNER JOIN`：只返回两表中匹配的行（交集）
> - `LEFT JOIN`：返回左表所有行，右表无匹配则为NULL
> - `RIGHT JOIN`：返回右表所有行，左表无匹配则为NULL
> - 自连接：表与自己连接，用于处理层级关系（如员工-经理）

> **【技术原理】**
> - **Nested-Loop Join（嵌套循环连接）**：MySQL最基本的JOIN算法。对外表（驱动表）的每一行，在内表（被驱动表）中查找匹配行。如果内表有索引，使用Index Nested-Loop Join（索引查找）；如果没有索引，使用Block Nested-Loop Join（BNL，将外表数据加载到join_buffer，批量匹配）
> - **MySQL 8.0.18+支持Hash Join**：对于等值JOIN且无索引的情况，Hash Join比BNL快得多。构建阶段：将小表加载到内存哈希表；探测阶段：逐行扫描大表在哈希表中查找匹配
> - **驱动表选择**：优化器通常选择小表作为驱动表。因为驱动表需要全表扫描，被驱动表可以用索引查找

> **【扩展知识】**
> - JOIN优化原则：1) 确保JOIN条件列有索引；2) 小表做驱动表；3) 避免JOIN过多表（建议不超过5张）；4) 用EXPLAIN检查JOIN类型
> - LEFT JOIN的WHERE条件位置：放在ON中——右表不满足时右表列为NULL，左表行仍保留；放在WHERE中——右表不满足时整行被过滤

### 3.8 子查询

```sql
SELECT * FROM users WHERE age > (SELECT AVG(age) FROM users);

SELECT * FROM users WHERE id IN (SELECT user_id FROM orders WHERE amount > 100);

SELECT * FROM users u WHERE EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id);

SELECT dept_id, avg_age
FROM (SELECT dept_id, AVG(age) AS avg_age FROM employees GROUP BY dept_id) AS dept_avg
WHERE avg_age > 28;
```

> **【执行说明】**
> - 标量子查询：返回单个值的子查询，如 `(SELECT AVG(age) FROM users)`
> - IN子查询：返回一列值的子查询
> - EXISTS子查询：判断子查询是否有结果，返回TRUE/FALSE
> - 派生表：子查询在FROM子句中，必须起别名

> **【技术原理】**
> - **IN vs EXISTS 的选择**：
>   - IN：先执行子查询得到结果集，再对外表逐行检查是否在结果集中。适合子查询结果集小、外表大的场景
>   - EXISTS：先遍历外表，对每行执行子查询判断是否存在。适合外表小、子查询表大的场景
>   - MySQL 8.0的优化器对两者有较好的自动优化，性能差异不大
> - **子查询的执行方式**：
>   - MySQL 8.0对IN子查询使用半连接(Semi-Join)优化，将子查询改写为JOIN执行，避免逐行执行子查询
>   - 派生表在8.0中支持条件下推(derived_condition_pushdown)，将外部WHERE条件下推到派生表内部执行，减少中间结果集

> **【扩展知识】**
> - 相关子查询 vs 非相关子查询：相关子查询依赖外部查询的值（如EXISTS示例），每行都要执行一次；非相关子查询独立执行一次（如IN示例）。相关子查询性能通常更差
> - MySQL 8.0.14+支持LATERAL派生表：`SELECT ... FROM t1, LATERAL (SELECT ... FROM t2 WHERE t2.id = t1.id) AS sub;` 允许派生表引用前面的表

---

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

> **【执行说明】**
> - `TINYINT` 用于状态值：0=禁用, 1=启用, 2=删除等
> - `BIGINT` 用于金额(分)：避免浮点精度问题，1.23元存为123分
> - `VARCHAR(N)` 的N是字符数不是字节数，VARCHAR(100)可存100个字符（无论中英文）
> - `TIMESTAMP` 自动更新：`ON UPDATE CURRENT_TIMESTAMP` 在行被修改时自动更新时间

> **【技术原理】**
> - **CHAR vs VARCHAR 存储差异**：
>   - CHAR(N)：固定分配N个字符的空间，不足用空格填充，检索时去除尾部空格。适合定长数据（手机号、MD5等）
>   - VARCHAR(N)：使用1-2字节存储实际长度，然后存储实际数据。节省空间，但更新时可能行溢出（数据变长超过页大小）
> - **TIMESTAMP vs DATETIME**：
>   - TIMESTAMP：4字节存储，内部以UTC时间戳存储，显示时转换为当前时区。范围1970-2038（Y2K38问题）
>   - DATETIME：8字节存储，不涉及时区转换，存储的就是字面值。范围1000-9999
>   - 生产建议：需要时区转换用TIMESTAMP，不需要用DATETIME
> - **DECIMAL精度**：`DECIMAL(10,2)` 表示总共10位数字，其中2位小数。MySQL使用二进制编码存储DECIMAL，精确计算不丢失精度。FLOAT/DOUBLE使用IEEE 754浮点数，存在精度问题

> **【扩展知识】**
> - **Y2K38问题**：TIMESTAMP最大到2038-01-19 03:14:07 UTC。MySQL 8.0.28+已开始支持扩展TIMESTAMP范围
> - **INT(11)的含义**：INT(11)中的11是显示宽度，不影响存储范围。MySQL 8.0.17+已废弃显示宽度语法
> - **JSON类型**：MySQL 8.0的JSON类型在存储时自动验证JSON格式，并支持对JSON内部路径创建索引（通过虚拟列+函数索引）
> - **BLOB类型**：存储二进制数据（图片、文件），但生产环境不建议在数据库中存大文件，应使用对象存储(OSS/S3)

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

> **【执行说明】**
> - `PRIMARY KEY`：主键约束，唯一标识每一行，自动创建聚簇索引
> - `NOT NULL`：非空约束，插入时该列必须有值
> - `UNIQUE`：唯一约束，该列值不能重复，自动创建唯一二级索引
> - `FOREIGN KEY`：外键约束，确保引用完整性。`ON DELETE SET NULL` 表示被引用行删除时，外键列设为NULL
> - `CHECK`：检查约束，MySQL 8.0.16+真正强制执行（之前版本解析但不执行）

> **【技术原理】**
> - **主键的选择原则**：
>   - 自增INT/BIGINT最佳：顺序插入，B+树叶子节点顺序追加，不会导致页分裂
>   - UUID不推荐：随机值导致B+树频繁页分裂和碎片，且36字符占用空间大
>   - 如果必须用UUID，建议使用有序UUID（MySQL 8.0的UUID_TO_BIN()函数可转换并优化排序）
> - **外键的性能影响**：每次INSERT/UPDATE/DELETE都需要检查外键约束，涉及额外的索引查找。高并发场景下，外键检查可能成为瓶颈。很多互联网公司选择在应用层保证引用完整性，不使用外键
> - **UNIQUE与PRIMARY KEY的区别**：一张表只能有一个PRIMARY KEY（不允许NULL），但可以有多个UNIQUE（允许NULL）

> **【扩展知识】**
> - 外键的ON DELETE/ON UPDATE选项：CASCADE（级联操作）、SET NULL（设为NULL）、RESTRICT（拒绝操作，默认）、NO ACTION（同RESTRICT）
> - 命名约束的好处：`ADD CONSTRAINT uk_email UNIQUE (email)` 中的uk_email是约束名，方便后续通过名称管理约束

---

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

> **【执行说明】** 理解B+树是理解索引优化的基础。3层B+树只需3次磁盘I/O就能定位到任何一行数据，这是MySQL高效查询的核心。

> **【技术原理】**
> **B+树为什么适合数据库索引**：
>
> 1. **矮胖结构减少I/O**：每个页16KB，非叶子节点只存键值+指针（约12字节/条），一个页可存约1360个键值。3层B+树可存 1360×1360×N ≈ 2000万行。每次查询最多3次I/O
>
> 2. **范围查询高效**：叶子节点通过双向链表连接。找到范围起点后，沿链表顺序扫描即可，无需回溯上层节点。例如 `WHERE id BETWEEN 10 AND 30`，先定位到10，然后沿链表扫描到30
>
> 3. **查询性能稳定**：所有数据都在叶子节点，任何查询都需要从根到叶遍历相同层数，时间复杂度稳定为O(logN)
>
> 4. **对比B树**：B树的非叶子节点也存数据，导致单个页能存的键值更少，树更高，I/O更多。且B树的范围查询需要中序遍历整棵树
>
> 5. **对比红黑树**：红黑树是二叉树，高度远大于B+树。2000万行数据的红黑树高度约24层，需要24次I/O，不可接受
>
> 6. **对比哈希**：哈希索引只支持等值查询(O(1))，不支持范围查询、排序、最左前缀匹配

> **【扩展知识】**
> - **B+树的页分裂**：当插入数据导致叶子节点空间不足时，需要分裂为两个页，可能导致性能抖动。自增主键可以避免页分裂（顺序追加）
> - **B+树的页合并**：删除数据导致页面使用率低于50%时，可能触发页合并
> - **索引的维护成本**：每次INSERT/UPDATE/DELETE都需要维护B+树结构，索引越多，写入越慢。建议单表索引数不超过5-6个

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

> **【执行说明】**
> - 第1条：需要回表。二级索引只存name和id，查询*需要所有列，必须回聚簇索引取完整行
> - 第2条：覆盖索引，不需要回表。查询的id和name都在二级索引中，EXPLAIN的Extra列会显示 `Using index`

> **【技术原理】**
> - **回表的代价**：每次回表都是一次聚簇索引B+树的查找（3次I/O）。如果二级索引匹配1000行，就需要1000次回表，代价极大
> - **覆盖索引的优化效果**：避免了回表，直接从二级索引中获取所有需要的列。二级索引通常比聚簇索引小得多（只存索引列+主键），I/O更少
> - **为什么二级索引存主键值而非指针**：如果存指针，当聚簇索引发生页分裂导致行移动时，所有二级索引的指针都需要更新。存主键值则不需要更新，代价是回表时需要一次额外的B+树查找

> **【扩展知识】**
> - **索引下推(ICP)**：MySQL 5.6+的优化。对于复合索引(name, age)，查询 `WHERE name = '张三' AND age > 25` 时，在5.6之前，存储引擎只根据name从索引中筛选，age条件在Server层过滤（需回表后判断）；5.6+的ICP在索引层面就过滤age条件，减少回表次数
> - **MRR(Multi-Range Read)**：MySQL 5.6+的优化。将二级索引查到的主键值排序后再回表，将随机I/O转为顺序I/O，提升性能

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

> **【执行说明】**
> - `UNIQUE INDEX`：唯一索引，保证列值唯一，允许NULL
> - `INDEX idx_name_age ON users(name, age)`：复合索引，遵循最左前缀原则
> - `content(20)`：前缀索引，只对TEXT/VARCHAR列的前20个字符建索引，节省空间
> - `(YEAR(created_at))`：函数索引（MySQL 8.0+），对函数计算结果建索引
> - `SHOW INDEX FROM users`：查看表的所有索引信息
> - `DROP INDEX idx_name ON users`：删除索引（在线操作，8.0支持不锁表）

> **【技术原理】**
> - **前缀索引的取舍**：前缀索引可以节省空间，但会丢失排序信息（前缀相同但完整值不同的行无法区分），因此不支持ORDER BY和覆盖索引。前缀长度选择：`SELECT COUNT(DISTINCT LEFT(content, N)) / COUNT(*) FROM articles;` 找到选择性接近完整列选择性的最小N
> - **函数索引的底层实现**：MySQL 8.0的函数索引本质是创建一个隐藏的虚拟列（Generated Column），然后对该虚拟列建索引
> - **不可见索引**（MySQL 8.0+）：`CREATE INDEX idx_test ON users(name) INVISIBLE;` 索引存在但对优化器不可见，用于测试索引删除后的影响，确认安全后再真正删除

> **【扩展知识】**
> - **降序索引**（MySQL 8.0+）：`CREATE INDEX idx_name_age_desc ON users(name ASC, age DESC);` 8.0真正支持降序索引存储，之前版本的DESC关键字只是语法兼容，实际仍按ASC存储
> - **全文索引**：`CREATE FULLTEXT INDEX ft_content ON articles(title, content);` 适合文本搜索，使用倒排索引实现

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

> **【执行说明】**
> - 第1-3条：完全匹配索引列的左前缀，索引生效
> - 第4-5条：跳过最左列name，索引失效，全表扫描
> - 第6条：name走索引，email不走索引（跳过了age），但ICP可在索引层过滤email
> - 第7条：name等值查找+age范围扫描后，email无法用索引查找（因为age范围扫描后的email不再有序），但ICP可优化

> **【技术原理】**
> - **为什么必须最左前缀**：B+树按照索引列定义的顺序逐层排序。先按name排序，name相同的再按age排序。如果跳过name直接查age，age在B+树中并不全局有序，无法利用索引
> - **范围查询后的列无法用索引**：当某一列使用范围查询（>, <, BETWEEN, LIKE前缀）后，该列之后的索引列不再有序。因为范围查询匹配了多行，这些行在后续列上的值是分散的
> - **ICP优化**：虽然范围查询后的列无法用索引查找，但索引中确实包含了这些列的值。ICP在存储引擎层就对这些列进行条件判断，不满足的行直接跳过，不需要回表后再判断

> **【扩展知识】**
> - **索引列顺序设计原则**：1) 等值查询列放前面，范围查询列放后面；2) 选择性高的列放前面；3) 考虑查询频率，最常用的查询条件对应的列放前面
> - **避免冗余索引**：如果已有INDEX(a, b)，则INDEX(a)是冗余的，因为最左前缀已经覆盖了a的查询

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

> **【执行说明】**
> - 第1条：索引失效。YEAR()函数破坏了created_at的有序性
> - 第2条：索引生效。改写为范围查询，B+树可以按范围扫描
> - 第3条：索引失效。左模糊无法利用B+树前缀有序性
> - 第4条：使用全文索引替代LIKE模糊查询
> - 第5条：索引可能失效。phone是VARCHAR，传入整数13800138000时MySQL会隐式转换
> - 第6条：索引生效。类型匹配，无需转换

> **【技术原理】**
> - **隐式类型转换的底层机制**：当比较的两边类型不一致时，MySQL会将字符串转为数字。`WHERE phone = 13800138000` 等价于 `WHERE CAST(phone AS SIGNED) = 13800138000`，对phone列应用了CAST函数，破坏了索引有序性
> - **OR条件索引失效**：`WHERE a = 1 OR b = 2`，如果b没有索引，优化器可能放弃索引选择全表扫描。解决方案：1) 给b也加索引（可能触发index_merge）；2) 用UNION ALL改写
> - **优化器选择全表扫描的场景**：当索引的选择性很低时（如status只有0/1两个值，WHERE status=1匹配50%的行），全表扫描比索引+回表更快（顺序读优于随机读）

> **【扩展知识】**
> - **索引条件下推(ICP)**：MySQL 5.6+优化，即使索引列的某些条件无法用于查找，也可以在索引层面进行过滤，减少回表次数
> - **索引合并(Index Merge)**：MySQL可以使用多个索引分别扫描，然后合并结果。但效率不如复合索引，建议用复合索引替代

---

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

> **【执行说明】**
> - `START TRANSACTION`：显式开启事务（也可以用 `BEGIN`）
> - `COMMIT`：提交事务，所有修改永久生效
> - `ROLLBACK`：回滚事务，撤销所有未提交的修改
> - 如果不显式开启事务，每条SQL都是一个自动提交的事务（`autocommit=1`，默认）

> **【技术原理】**
> - **原子性的实现（Undo Log）**：事务中每条修改SQL执行前，先将旧值写入Undo Log。如果需要回滚，根据Undo Log逆向恢复数据。Undo Log以链表形式组织（版本链），也是MVCC的基础
> - **持久性的实现（Redo Log）**：采用WAL(Write-Ahead Logging)机制，先写日志再写数据。事务提交时，将Redo Log Buffer刷盘（fsync），保证即使崩溃也能通过Redo Log恢复已提交的数据
> - **隔离性的实现（MVCC+锁）**：读操作通过MVCC实现快照读（不加锁），写操作通过锁实现互斥。MVCC让读写不冲突，锁让写写不冲突
> - **一致性的实现**：是原子性+隔离性+持久性的最终结果，加上应用层的约束（主键、外键、CHECK等）共同保证

> **【扩展知识】**
> - **隐式提交**：DDL语句（CREATE/ALTER/DROP）会隐式提交当前事务
> - **保存点(Savepoint)**：`SAVEPOINT sp1; ... ROLLBACK TO sp1;` 可以部分回滚，而不是回滚整个事务
> - **分布式事务**：跨数据库的事务需要使用XA协议，MySQL支持XA事务但性能较差，生产环境推荐使用Seata等框架

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

> **【执行说明】**
> - `SELECT @@transaction_isolation`：查看当前隔离级别，MySQL默认REPEATABLE READ
> - `SET SESSION`：仅当前会话生效；`SET GLOBAL`：全局生效（新连接生效）
> - 大多数互联网应用使用READ COMMITTED（Oracle默认），MySQL默认REPEATABLE READ

> **【技术原理】**
> - **脏读**：事务A读到了事务B未提交的数据。如果事务B回滚，事务A读到的就是脏数据（不存在的数据）
> - **不可重复读**：事务A两次读取同一行数据，中间事务B修改并提交了该行，导致两次读取结果不同
> - **幻读**：事务A两次执行相同的范围查询，中间事务B插入并提交了新行，导致第二次查询多出了行
> - **InnoDB对幻读的优化**：在REPEATABLE READ级别下，InnoDB通过Next-Key Lock（行锁+间隙锁）防止幻读。普通SELECT是快照读（MVCC），不会看到其他事务新插入的行；当前读（FOR UPDATE/LOCK IN SHARE MODE）会加Next-Key Lock阻止其他事务插入

> **【扩展知识】**
> - **RC vs RR的选择**：RR级别有Gap Lock，可能导致死锁；RC级别没有Gap Lock，并发更好但不可重复读。很多互联网公司选择RC+乐观锁的方案
> - **SERIALIZABLE的代价**：所有SELECT自动加共享锁，完全串行化，性能极差，几乎不在生产环境使用

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

> **【执行说明】** MVCC是InnoDB默认的并发控制机制，无需手动配置。理解MVCC有助于排查数据一致性问题。

> **【技术原理】**
> - **隐藏列详解**：
>   - `DB_TRX_ID`（6字节）：最后修改该行的事务ID
>   - `DB_ROLL_PTR`（7字节）：回滚指针，指向Undo Log中该行的上一个版本
>   - `DB_ROW_ID`（6字节）：隐藏自增主键，仅当表没有主键且没有非空唯一索引时使用
>
> - **快照读 vs 当前读**：
>   - 快照读：普通SELECT，通过MVCC读取历史版本，不加锁
>   - 当前读：SELECT FOR UPDATE/LOCK IN SHARE MODE、INSERT/UPDATE/DELETE，读取最新已提交版本，加锁
>
> - **RC级别每次SELECT创建新Read View的后果**：每次SELECT都能看到其他事务已提交的最新修改，因此可能出现不可重复读
>
> - **RR级别复用Read View的后果**：整个事务中使用同一个Read View，因此即使其他事务提交了修改，当前事务也看不到，保证了可重复读

> **【扩展知识】**
> - **MVCC的内存开销**：长事务会导致Undo Log无法及时清除（因为可能有其他事务需要读取历史版本），占用大量存储空间。应避免长事务
> - **MVCC的局限**：只能解决快照读的幻读问题，当前读仍需依赖Next-Key Lock防幻读

### 6.4 Redo Log与Undo Log

**Redo Log（重做日志）**：保证事务的持久性，WAL（Write-Ahead Logging）机制。

```
Redo Log工作流程：
┌──────────┐    1.修改数据    ┌──────────┐    2.写Redo Log    ┌──────────┐
│ 事务执行  │ ──────────────► │ Buffer   │ ────────────────► │ Redo Log │
│          │                 │ Pool修改  │    (先写日志)       │ Buffer   │
│          │                 └──────────┘                    └────┬─────┘
│          │                                                      │
│          │    3.提交事务                                        │ 3.fsync
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

> **【执行说明】**
> - `SHOW VARIABLES LIKE 'innodb_log%'`：查看Redo Log相关参数，包括文件大小、数量、缓冲区大小等
> - `SHOW VARIABLES LIKE 'innodb_undo%'`：查看Undo Log相关参数，包括表空间、保留时间等

> **【技术原理】**
> - **WAL机制**：Write-Ahead Logging，先写日志再写数据。这是数据库持久性的核心保证：
>   1. 修改数据时，先写Redo Log Buffer
>   2. 事务提交时，将Redo Log Buffer fsync到磁盘
>   3. 数据页（脏页）由后台线程异步刷盘
>   4. 如果崩溃，通过Redo Log重放已提交但未刷盘的修改
>
> - **Redo Log的环形写入**：Redo Log是固定大小的文件组（如2个文件各1GB），写满后从头覆盖。通过checkpoint标记已刷盘的位置，避免覆盖未刷盘的日志
>
> - **Undo Log的清理**：当没有事务需要读取某个Undo Log版本时（即该版本对所有活跃事务都不可见），Purge线程会清理该Undo Log。长事务会阻止Undo Log清理，导致空间膨胀
>
> - **Redo Log与Binlog的一致性**：两阶段提交保证Redo Log和Binlog的一致性：1) 先写Redo Log（prepare状态）；2) 再写Binlog；3) 最后提交Redo Log（commit状态）。崩溃恢复时，如果Redo Log是prepare状态且Binlog完整，则提交；否则回滚

> **【扩展知识】**
> - **innodb_flush_log_at_trx_commit** 的三种取值对性能和安全的影响极大，详见模块九参数调优部分
> - **Undo Log的独立表空间**：MySQL 8.0默认将Undo Log放在独立表空间中（`innodb_undo_directory`），不再放在系统表空间中，方便管理和回收空间

---

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

> **【执行说明】** MySQL锁体系从粗粒度到细粒度：全局锁 > 表级锁 > 行级锁。粒度越细，并发度越高，但开销也越大。

> **【技术原理】**
> - **全局锁**：`FLUSH TABLES WITH READ LOCK` 将整个数据库设为只读状态，所有DML和DDL都被阻塞。用于mysqldump --single-transaction无法使用的场景（如MyISAM表备份）
> - **MDL（元数据锁）**：MySQL 5.5+自动管理。执行DML时加MDL读锁，执行DDL时加MDL写锁。防止DML执行期间表结构被修改。**常见问题**：长事务持有MDL读锁，导致DDL操作等待，后续所有DML也被阻塞（因为DDL的MDL写锁优先级更高）
> - **意向锁**：InnoDB自动管理。事务想加行锁时，先在表级别加意向锁（IS或IX），表示"我打算在这个表上加行锁"。这样其他事务想加表锁时，只需检查意向锁，而不需要逐行检查行锁
> - **AUTO-INC锁**：INSERT时保护自增ID的分配。`innodb_autoinc_lock_mode=2`(交织模式)下使用轻量级互斥锁代替AUTO-INC锁，性能更好但可能导致ID不连续

> **【扩展知识】**
> - MDL锁排查：`SHOW PROCESSLIST` 中出现 `Waiting for table metadata lock` 表示有MDL锁等待。可通过 `performance_schema.metadata_locks` 查看详细信息
> - 在线DDL与MDL：MySQL 8.0的Online DDL在执行期间只短暂持有MDL写锁（开始和结束阶段），大部分时间不阻塞DML

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

> **【执行说明】**
> - `FOR SHARE`：加共享锁(S锁)，允许其他事务读但不允许写
> - `FOR UPDATE`：加排他锁(X锁)，其他事务不能读（当前读）也不能写
> - `NOWAIT`：如果获取不到锁立即返回错误，而不是等待
> - `SKIP LOCKED`：跳过被锁住的行，返回未被锁住的行。适合队列消费等场景

> **【技术原理】**
> - **Record Lock**：锁定索引上的一条记录。如果表没有索引，InnoDB使用隐藏的聚簇索引，行锁实际上退化为表锁
> - **Gap Lock**：锁定索引记录之间的间隙，防止其他事务在间隙中插入新记录。Gap Lock之间不冲突（多个事务可以同时持有同一间隙的Gap Lock），只与INSERT冲突
> - **Next-Key Lock**：Record Lock + Gap Lock的组合，左开右闭区间。InnoDB在RR级别下的默认加锁方式
> - **加锁规则**（InnoDB RR级别）：
>   1. 加锁的基本单位是Next-Key Lock
>   2. 查找过程中访问到的对象才会加锁
>   3. 等值查询中，唯一索引命中记录时，Next-Key Lock退化为Record Lock
>   4. 等值查询中，最后一个不满足条件的值，Next-Key Lock退化为Gap Lock
>   5. 范围查询中，会对扫描到的范围加Next-Key Lock

> **【扩展知识】**
> - **NOWAIT和SKIP LOCKED的应用场景**：
>   - 电商库存扣减：`SELECT * FROM inventory WHERE product_id = 1 FOR UPDATE NOWAIT;` 如果锁等待立即返回，让用户重试
>   - 消息队列消费：`SELECT * FROM task_queue WHERE status = 'pending' FOR UPDATE SKIP LOCKED LIMIT 10;` 跳过正在被其他消费者处理的行
> - **锁的查看**：MySQL 8.0通过 `performance_schema.data_locks` 和 `performance_schema.data_lock_waits` 查看锁信息（替代5.7的 `information_schema.innodb_locks`）

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

> **【执行说明】**
> - `SHOW ENGINE INNODB STATUS`：查看InnoDB状态，包括最近一次死锁信息（LATEST DETECTED DEADLOCK部分）
> - `innodb_lock_wait_timeout`：锁等待超时时间（默认50秒），超时后返回错误
> - `innodb_deadlock_detect`：死锁检测（默认ON），检测到死锁后自动回滚代价较小的事务

> **【技术原理】**
> - **死锁产生的四个必要条件**：1) 互斥；2) 持有并等待；3) 不可抢占；4) 循环等待。只要打破其中一个就能预防死锁
> - **InnoDB死锁检测**：InnoDB维护一个等待图(Wait-For Graph)，每当加锁时检查图中是否出现环。如果出现环，说明死锁，选择代价最小的事务回滚
> - **死锁检测的开销**：每个加锁操作都需要检查等待图，高并发场景下可能消耗大量CPU。如果确认业务不会产生死锁，可以关闭检测：`SET GLOBAL innodb_deadlock_detect = OFF;` 此时依赖锁超时来处理死锁
> - **行锁升级为表锁**：当UPDATE/DELETE的WHERE条件没有索引时，InnoDB必须扫描每一行来判断是否满足条件，每行都会加锁，等同于表锁

> **【扩展知识】**
> - 死锁排查步骤：1) 查看SHOW ENGINE INNODB STATUS的死锁信息；2) 分析两个事务的加锁顺序；3) 确认是否有索引缺失导致表锁；4) 调整业务逻辑避免循环等待
> - 生产环境建议开启 `innodb_print_all_deadlocks = ON`，将所有死锁信息记录到错误日志中

---

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

> **【执行说明】**
> - `CREATE VIEW v_user_orders AS ...`：创建视图，定义视图的SELECT语句。视图本身不存储数据，每次查询视图时执行底层SELECT
> - `CREATE OR REPLACE VIEW`：创建或替换已有视图，修改视图定义时使用
> - `SELECT * FROM v_user_orders WHERE amount > 100`：像查普通表一样查询视图，MySQL会将查询条件合并到底层SELECT中执行
> - `DROP VIEW IF EXISTS v_user_orders`：删除视图，不影响底层表数据

> **【技术原理】**
> - **视图的执行机制**：MySQL处理视图查询时采用**合并算法(MERGE)**或**临时表算法(TEMPTABLE)**：
>   - MERGE：将视图定义的SQL与查询SQL合并为一条SQL执行。例如 `SELECT * FROM v_user_orders WHERE amount > 100` 会被改写为 `SELECT u.id, u.name, o.amount, ... FROM users u INNER JOIN orders o ON u.id = o.user_id WHERE amount > 100`。性能最优
>   - TEMPTABLE：先执行视图定义的SQL，将结果存入临时表，再对临时表执行查询。当视图包含聚合函数、DISTINCT、GROUP BY、HAVING、UNION等时，必须使用TEMPTABLE算法
> - **可更新视图**：基于单表、无聚合、无GROUP BY的视图支持INSERT/UPDATE/DELETE操作。MERGE算法的视图通常可更新，TEMPTABLE算法的视图不可更新
> - **视图的性能影响**：TEMPTABLE算法的视图需要创建临时表，性能较差。嵌套视图（视图上建视图）更难优化，生产环境应避免

> **【扩展知识】**
> - **视图与权限控制**：`GRANT SELECT ON db.v_user_orders TO 'user_readonly'@'%';` 只授权用户访问视图而非底层表，实现列级权限控制
> - **视图的CHECK OPTION**：`CREATE VIEW ... WITH CHECK OPTION;` 确保通过视图插入/更新的数据满足视图的WHERE条件，防止数据"消失"
> - MySQL不支持物化视图(Materialized View)，如需预计算结果可使用触发器或定时任务维护汇总表

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

> **【执行说明】**
> - `DELIMITER //`：临时将语句分隔符改为`//`，因为触发器体中包含分号。创建完毕后用 `DELIMITER ;` 恢复
> - `AFTER INSERT ON orders`：在orders表INSERT操作之后触发
> - `FOR EACH ROW`：行级触发器，每影响一行触发一次
> - `NEW.quantity`：引用新插入行的quantity列值
> - `OLD.id`：引用被删除行的id列值
> - `SHOW TRIGGERS`：查看当前数据库所有触发器

> **【技术原理】**
> - **触发器的执行时机**：BEFORE触发器在约束检查之前执行，可以修改NEW值；AFTER触发器在约束检查之后执行，不能修改NEW值
> - **触发器与事务**：触发器在调用它的语句所在事务中执行。如果触发器执行失败，整个语句回滚
> - **触发器的性能影响**：每行操作都会触发，批量INSERT 1000行会触发1000次。高并发写入场景下，触发器可能成为性能瓶颈
> - **触发器的级联效应**：触发器中的SQL可能触发另一个表的触发器，形成级联。MySQL限制最大级联层数，防止无限递归

> **【扩展知识】**
> - **触发器的替代方案**：1) 应用层逻辑（更易测试和维护）；2) 存储过程（更灵活）；3) 消息队列+异步处理（解耦，性能更好）
> - **触发器的适用场景**：审计日志（记录数据变更）、数据同步（写入备份表）、数据校验（BEFORE触发器验证数据合法性）
> - 生产环境建议谨慎使用触发器，因为：1) 隐式执行，不易排查问题；2) 增加数据库负担；3) 调试困难

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

> **【执行说明】**
> - `IN user_id INT`：输入参数，调用时传入值
> - `OUT total INT`：输出参数，存储过程将结果写入该变量
> - `CALL get_user_by_id(1)`：调用存储过程
> - `DECLARE from_balance DECIMAL(10,2)`：声明局部变量
> - `SELECT ... INTO variable`：将查询结果赋值给变量
> - `IF ... THEN ... ELSE ... END IF`：条件判断
> - `SET @total = 0`：设置用户变量（会话级别），用@前缀

> **【技术原理】**
> - **存储过程的执行机制**：MySQL在创建存储过程时进行语法检查和预编译，执行时直接运行预编译的代码，减少了解析和优化的开销
> - **参数模式**：IN（只读传入）、OUT（只写输出）、INOUT（可读写）。OUT参数通过用户变量(@var)传递结果
> - **存储过程与事务**：存储过程中可以使用事务控制语句(START TRANSACTION/COMMIT/ROLLBACK)，实现复杂的业务逻辑原子性
> - **DECLARE的处理**：DECLARE声明的变量是局部变量，只在BEGIN...END块内有效，优先级高于同名的表列名

> **【扩展知识】**
> - **存储过程的优缺点**：
>   - 优点：减少网络传输（多条SQL一次调用）、预编译性能好、封装业务逻辑
>   - 缺点：调试困难、不可移植（不同数据库语法不同）、业务逻辑分散在数据库和应用层
> - **现代开发趋势**：互联网公司倾向于将业务逻辑放在应用层，数据库只负责存储，不使用存储过程。主要原因是：1) 应用层更容易扩展和测试；2) 存储过程难以版本管理；3) 数据库资源更昂贵
> - **游标(Cursor)**：存储过程中可使用游标逐行处理查询结果，但性能较差，应优先使用集合操作

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

> **【执行说明】**
> - `PARTITION BY RANGE (YEAR(order_date))`：按order_date的年份进行范围分区
> - `PRIMARY KEY (id, order_date)`：分区键必须包含在主键中，否则创建失败
> - `PARTITIONS 8`：HASH分区指定8个分区
> - `DROP PARTITION p2022`：删除整个分区，比DELETE快得多（直接删除文件，不逐行删除）
> - `ADD PARTITION`：添加新分区

> **【技术原理】**
> - **分区的底层实现**：每个分区在文件系统中是独立的.ibd文件。查询时，MySQL根据分区裁剪(Partition Pruning)只扫描相关分区，跳过不相关分区，减少I/O
> - **分区裁剪**：优化器根据WHERE条件自动判断需要扫描哪些分区。例如 `WHERE order_date >= '2023-01-01'` 只扫描p2023和p2024分区
> - **分区键必须在主键中**：InnoDB要求分区键是主键的子集，因为每个分区内部是一个独立的B+树，必须保证全局唯一性
> - **DROP PARTITION的性能优势**：删除分区是文件级别的操作（删除.idb文件），而DELETE需要逐行删除并写Undo Log和Redo Log，对于大数据量差距极大

> **【扩展知识】**
> - **分区的限制**：1) 最多8192个分区；2) 不支持外键；3) 分区表上的唯一索引必须包含分区键；4) 不支持QUERY CACHE
> - **分区 vs 分表**：分区对应用透明，但单机存储；分表可跨库，但需要应用层路由。数据量极大时，分库分表是更好的选择
> - **子分区(Subpartition)**：RANGE/LIST分区可再按HASH/KEY进行子分区，实现二维分区

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

> **【执行说明】**
> - `OVER (ORDER BY score DESC)`：定义窗口的排序规则
> - `OVER (PARTITION BY dept ORDER BY ...)`：按dept分组，每组内独立排序
> - `ROWS BETWEEN 2 PRECEDING AND CURRENT ROW`：窗口范围为当前行及前2行，用于计算移动平均
> - `LAG(price, 1)`：获取前一行的price值，用于计算环比变化

> **【技术原理】**
> - **窗口函数的执行流程**：
>   1. FROM/WHERE/GROUP BY/HAVING 正常执行，得到结果集
>   2. 对结果集按PARTITION BY分区
>   3. 每个分区内按ORDER BY排序
>   4. 对每行计算窗口函数值（根据ROWS/RANGE定义窗口范围）
>   5. 最后执行SELECT、DISTINCT、ORDER BY、LIMIT
>
> - **窗口函数 vs 聚合函数+GROUP BY**：
>   - GROUP BY：每组返回一行，行数减少
>   - 窗口函数：每行都返回，保留原始行数，附加聚合计算结果
>   - 窗口函数适合需要同时查看明细和聚合值的场景（如：查看每个员工信息及其部门平均工资）
>
> - **窗口帧(Frame)**：`ROWS BETWEEN ... AND ...` 定义了窗口函数的计算范围：
>   - `UNBOUNDED PRECEDING`：分区的第一行
>   - `N PRECEDING`：当前行前N行
>   - `CURRENT ROW`：当前行
>   - `N FOLLOWING`：当前行后N行
>   - `UNBOUNDED FOLLOWING`：分区的最后一行

> **【扩展知识】**
> - **常用窗口函数分类**：
>   - 排名：ROW_NUMBER, RANK, DENSE_RANK, NTILE(N)（将行分为N组）
>   - 聚合：SUM, AVG, COUNT, MAX, MIN（加OVER子句即变为窗口函数）
>   - 偏移：LAG(前N行), LEAD(后N行), FIRST_VALUE, LAST_VALUE, NTH_VALUE
> - **性能注意**：窗口函数需要对分区排序，大数据量下可能产生临时表和filesort。确保PARTITION BY和ORDER BY列有索引

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

> **【执行说明】**
> - `WITH dept_avg AS (...)`：定义非递归CTE，相当于命名的临时结果集，只在当前查询中有效
> - `WITH RECURSIVE org_tree AS (...)`：定义递归CTE，用于处理层级/树形数据
> - 递归CTE结构：锚点成员（非递归部分，`WHERE manager_id IS NULL`）+ `UNION ALL` + 递归成员（引用自身，`JOIN org_tree`）
> - 递归终止条件：递归成员不再产生新行时自动停止

> **【技术原理】**
> - **CTE vs 子查询 vs 临时表**：
>   - CTE：可读性好，可被引用多次，MySQL 8.0物化CTE（只计算一次）
>   - 子查询：嵌套层次深时难读，每次引用都重新计算
>   - 临时表：需要显式创建和删除，跨SQL可用
>
> - **递归CTE的执行过程**：
>   1. 执行锚点查询，得到初始结果集（如顶级员工）
>   2. 将锚点结果作为工作表，执行递归成员（如查找下属）
>   3. 将递归结果作为新的工作表，再次执行递归成员
>   4. 重复直到递归成员不再产生新行
>   5. 将所有结果UNION ALL合并返回
>
> - **递归深度限制**：`cte_max_recursion_depth`（默认1000），超过限制报错。可调整：`SET SESSION cte_max_recursion_depth = 10000;`

> **【扩展知识】**
> - **递归CTE的典型应用**：
>   - 组织架构树（员工-经理层级）
>   - 菜单树（无限级分类）
>   - 图的路径查找（如航班中转路线）
>   - 生成序列数据：`WITH RECURSIVE nums AS (SELECT 1 AS n UNION ALL SELECT n+1 FROM nums WHERE n < 100) SELECT * FROM nums;`
> - **CTE的可读性优势**：复杂查询中，用CTE替代嵌套子查询，逻辑更清晰，便于调试和维护

---

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

> **【执行说明】**
> - 在任何SELECT语句前加 `EXPLAIN` 即可查看执行计划
> - 重点关注 `type`（是否用了索引）和 `Extra`（是否有额外开销）
> - `key = NULL` 且 `type = ALL` 表示全表扫描，需要加索引
> - `key_len` 可判断复合索引使用了几个列：key_len / 列的字节长度 = 使用的列数

> **【技术原理】**
> - **EXPLAIN的局限性**：EXPLAIN显示的是优化器的预估执行计划，不实际执行SQL，因此rows是估算值，可能不准确。MySQL 8.0提供 `EXPLAIN ANALYZE` 实际执行SQL并返回真实的执行时间和行数
> - **优化器的工作**：优化器基于成本估算选择执行计划，考虑因素包括：1) 扫描行数（从索引统计信息估算）；2) 是否需要回表；3) 排序成本；4) JOIN顺序。成本估算依赖统计信息，`ANALYZE TABLE users;` 可更新统计信息
> - **type字段的含义详解**：
>   - `const`：通过主键或唯一索引精确匹配一行，B+树只需一次查找，是最优的访问方式
>   - `ref`：通过非唯一索引查找，可能匹配多行，需要沿B+树叶子链表扫描
>   - `range`：索引范围扫描，利用B+树有序性定位起点和终点，中间顺序扫描
>   - `index`：全索引扫描，扫描整棵B+树。比ALL好因为索引通常比数据小，但仍需扫描所有行
>   - `ALL`：全表扫描，从聚簇索引的第一个数据页扫描到最后一个，代价最大

> **【扩展知识】**
> - **EXPLAIN ANALYZE**（MySQL 8.0.18+）：实际执行查询并输出每个算子的真实耗时和行数，比普通EXPLAIN更准确，但会真正执行SQL（对DML需注意）
> - **optimizer_trace**：`SET optimizer_trace='enabled=on';` 开启优化器追踪，可查看优化器为什么选择某个执行计划，适合深度调优

### 9.2 索引优化实战

```sql
EXPLAIN SELECT * FROM users WHERE name = '张三' ORDER BY age;

CREATE INDEX idx_name_age ON users(name, age);
EXPLAIN SELECT * FROM users WHERE name = '张三' ORDER BY age;

EXPLAIN SELECT * FROM users WHERE name = '张三';

EXPLAIN SELECT id, name FROM users WHERE name = '张三';

SET optimizer_switch = 'index_condition_pushdown=on';
```

> **【执行说明】**
> - 第1条EXPLAIN：没有合适索引时，WHERE name过滤 + ORDER BY age排序，可能全表扫描+filesort
> - 第2条EXPLAIN：创建复合索引(name, age)后，name等值查找 + age已有序，Using index（覆盖索引）+ 无filesort
> - 第3条EXPLAIN：只用name条件，走索引ref访问
> - 第4条EXPLAIN：覆盖索引，Extra显示Using index，不需要回表
> - `index_condition_pushdown=on`：开启索引下推优化

> **【技术原理】**
> - **ORDER BY与索引**：当ORDER BY的列与索引列顺序一致时，可以利用B+树的有序性避免filesort。复合索引(name, age)中，name相同的数据已经按age排序，因此 `WHERE name = '张三' ORDER BY age` 不需要额外排序
> - **覆盖索引的判断**：EXPLAIN的Extra列显示 `Using index` 表示覆盖索引。此时查询的所有列都在索引中，不需要回表到聚簇索引
> - **索引下推(ICP)的优化效果**：没有ICP时，存储引擎根据索引查找行，回表后由Server层判断其他WHERE条件；有ICP时，存储引擎在索引层面就判断所有可用条件，只对满足条件的行回表，减少回表次数

> **【扩展知识】**
> - **索引优化的一般流程**：1) 用EXPLAIN分析；2) 确认type是否合理（至少ref）；3) 检查Extra是否有filesort/temporary；4) 根据查询模式设计复合索引；5) 验证覆盖索引是否生效

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

> **【执行说明】**
> - 第1条：避免 `SELECT *`，只查需要的列，减少数据传输和回表开销
> - 第2条：避免对索引列使用函数，改写为范围查询
> - 第3条：**游标分页**（深分页优化），用上一页最后一条的id代替OFFSET，避免扫描前N行再丢弃
> - 第4条：EXISTS通常比IN更高效（当子查询结果集大时）
> - 第5条：批量INSERT比逐条INSERT快得多（减少事务和SQL解析次数）
> - 第6条：大批量DELETE分批执行，避免长事务和锁争用

> **【技术原理】**
> - **深分页的性能问题**：`LIMIT 100000, 10` 需要扫描前100010行，丢弃前100000行，只返回10行。InnoDB需要先从索引中读取100010行的主键值，再回表获取100010行完整数据，代价极大
> - **游标分页的原理**：`WHERE id > 100000 ORDER BY id LIMIT 10` 直接从id=100000的位置开始扫描，利用B+树的有序性，只需扫描10行
> - **批量INSERT的优化**：一条INSERT插入多行值只需一次SQL解析、一次事务开启/提交，比多条INSERT效率高10-100倍
> - **分批DELETE的原因**：一次性删除大量行会：1) 产生大量Undo Log；2) 持有行锁时间长，阻塞其他事务；3) 删除后产生大量碎片空间

> **【扩展知识】**
> - **更多SQL优化技巧**：
>   - 用UNION ALL代替UNION（UNION需要去重排序）
>   - 避免在WHERE中使用OR（改用UNION ALL或给OR两列都加索引）
>   - 用JOIN代替子查询（MySQL 8.0优化器已自动做此改写）
>   - 大表UPDATE分批执行：`UPDATE users SET status = 1 WHERE status = 0 LIMIT 1000;`
>   - 使用FORCE INDEX强制使用索引：`SELECT * FROM users FORCE INDEX(idx_name) WHERE name = '张三';`（当优化器选错索引时使用）

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

> **【执行说明】**
> - `SET GLOBAL slow_query_log = ON`：开启慢查询日志（重启后失效，需写入my.cnf持久化）
> - `SET GLOBAL long_query_time = 1`：设置慢查询阈值为1秒（默认10秒，建议设为0.1-1秒）
> - `SHOW VARIABLES LIKE 'slow_query_log_file'`：查看慢查询日志文件路径
> - `mysqldumpslow -s t -t 10`：按总耗时排序，显示前10条慢查询。`-s t`按总时间排序，`-s c`按次数排序，`-s l`按锁定时间排序

> **【技术原理】**
> - **慢查询日志的工作机制**：SQL执行完毕后，如果执行时间超过long_query_time，将SQL文本、执行时间、锁等待时间、扫描行数等信息写入慢查询日志文件。这是事后记录，不影响SQL执行
> - **long_query_time的精度**：支持微秒精度，如 `SET GLOBAL long_query_time = 0.1;` 表示100毫秒
> - **log_queries_not_using_indexes**：`SET GLOBAL log_queries_not_using_indexes = ON;` 记录所有未使用索引的查询（即使未超过时间阈值），帮助发现潜在性能问题
> - **慢查询日志的性能开销**：开启慢查询日志对性能影响很小（约1-2%），生产环境建议始终开启

> **【扩展知识】**
> - **Performance Schema**：MySQL 8.0的Performance Schema提供更细粒度的性能监控，可替代部分慢查询日志功能。`events_statements_summary_by_digest` 表按SQL指纹聚合统计
> - **pt-query-digest**：Percona Toolkit中的慢查询分析工具，比mysqldumpslow更强大，支持生成详细的分析报告
> - **慢查询日志配置持久化**：在my.cnf中写入 `slow_query_log = 1`、`long_query_time = 1`、`slow_query_log_file = /var/log/mysql/slow.log`

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

> **【执行说明】** 此流程图展示了SQL性能优化的标准步骤：发现慢SQL → EXPLAIN分析 → 定位问题 → 实施优化。

> **【技术原理】**
> - **优化的优先级**：1) 索引优化（成本最低，效果最好）；2) SQL改写（不改架构，效果中等）；3) 架构优化（分库分表、读写分离，成本最高）
> - **type=ALL的优化路径**：加索引 → 调整查询条件 → 如果无法加索引，考虑是否可以接受全表扫描（小表全表扫描可能比索引+回表更快）
> - **filesort的优化路径**：让ORDER BY列走索引 → 使用覆盖索引 → 调整sort_buffer_size → 如果数据量极大，考虑在应用层排序
> - **temporary的优化路径**：让GROUP BY/ORDER BY使用索引 → 增大tmp_table_size → 使用SQL_BIG_RESULT提示优化器直接用磁盘临时表（避免内存临时表溢出到磁盘的额外开销）

> **【扩展知识】**
> - **优化效果验证**：每次优化后，用 `EXPLAIN` 和实际执行时间对比验证效果。避免过度优化，优先解决TOP N慢查询

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

> **【执行说明】**
> - `innodb_buffer_pool_size`：最重要的参数，建议设为物理内存的60%-80%。8.0支持在线动态调整
> - `innodb_flush_log_at_trx_commit = 1`：最安全配置，金融场景必须设为1
> - `innodb_io_capacity`：SSD设为5000-20000，HDD设为200-2000。控制后台刷脏页和刷新日志的速率
> - `innodb_flush_method = 'O_DIRECT'`：Linux下避免OS页面缓存和Buffer Pool双重缓存
> - `max_connections`：最大连接数，根据业务并发量设置，过大反而降低性能
> - `sort_buffer_size`：每个线程的排序缓冲区，4MB通常足够。过大浪费内存（每个线程独占）
> - `tmp_table_size`：内存临时表最大大小，超过则转为磁盘临时表

> **【技术原理】**
> - **双1配置**：`innodb_flush_log_at_trx_commit = 1` + `sync_binlog = 1`，保证事务提交时Redo Log和Binlog都fsync到磁盘，崩溃不丢任何数据，但性能最低
> - **O_DIRECT的原理**：默认情况下，MySQL读写数据文件经过OS页面缓存(Page Cache)，而Buffer Pool本身就是缓存，导致数据被缓存了两次(Double Buffering)。O_DIRECT绕过OS缓存，直接读写磁盘，节省内存并减少缓存一致性问题
> - **thread_cache_size**：线程缓存池大小。客户端断开连接后，服务线程不销毁而是放入缓存，新连接时复用，避免频繁创建/销毁线程的开销
> - **sort_buffer_size的影响**：如果排序数据量超过sort_buffer_size，MySQL使用磁盘临时文件进行归并排序（filesort），性能急剧下降

> **【扩展知识】**
> - **参数调优的原则**：1) 先调Buffer Pool（影响最大）；2) 再调日志相关参数（安全与性能平衡）；3) 最后调连接和缓冲区参数。不要盲目调参，每次只调一个参数并观察效果
> - **参数持久化**：MySQL 8.0支持 `SET PERSIST` 将参数写入mysqld-auto.cnf文件，重启后自动加载。如 `SET PERSIST innodb_buffer_pool_size = 4294967296;`

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

> **【执行说明】**
> - 连接池在应用层配置（如Java的HikariCP、Python的SQLAlchemy、Django的CONN_MAX_AGE）
> - `maximumPoolSize` 不是越大越好，公式 `CPU核数×2 + 磁盘数` 是经验值
> - `maxLifetime` 应小于MySQL的 `wait_timeout`，避免连接被MySQL主动断开后应用还在使用
> - `connectionTestQuery`：连接池在借出连接前执行此SQL验证连接是否有效

> **【技术原理】**
> - **连接池的核心价值**：TCP连接建立需要三次握手+MySQL认证协议，耗时约10-50ms。连接池复用已建立的连接，省去连接创建/销毁的开销
> - **连接泄漏检测**：如果应用借出连接后未归还（如忘记关闭），连接池中的可用连接会逐渐耗尽。HikariCP提供 `leakDetectionThreshold` 参数，连接借出超过此时间后记录警告
> - **连接池与MySQL max_connections的关系**：所有应用实例的连接池最大连接数之和，不应超过MySQL的 `max_connections`。例如：10个应用实例 × 每实例20个连接 = 200，MySQL的max_connections应至少设为250（留余量）

> **【扩展知识】**
> - **Django连接池**：Django默认每个请求结束后关闭连接。设置 `CONN_MAX_AGE = 60` 可保持连接60秒复用。如需真正的连接池，可使用 `django-db-connection-pool` 第三方库
> - **ProxySQL连接池**：在MySQL前端部署ProxySQL，它提供连接池和多路复用功能，多个应用连接共享少量到MySQL的真实连接

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

> **【执行说明】**
> - 分库分表是最后的手段，优先尝试：索引优化 → 读写分离 → 分区表 → 分库分表
> - 分片键的选择决定了查询效率，必须覆盖80%以上的查询场景
> - ShardingSphere-JDBC嵌入应用中，无需额外部署；ShardingSphere-Proxy独立部署，支持多语言
> - 分库分表后，DDL变更需要同时应用到所有分片

> **【技术原理】**
> - **Snowflake雪花算法**：生成64位唯一ID = 1位符号位 + 41位时间戳(约69年) + 10位机器ID(1024台) + 12位序列号(每毫秒4096个)。趋势递增，对B+树索引友好
> - **一致性哈希**：将分片键哈希值映射到0-2^32的环上，数据节点也映射到环上。每个数据归属到环上顺时针方向最近的节点。扩容时只需迁移新增节点到下一节点之间的数据，迁移量约1/N
> - **跨片事务的挑战**：分库后无法使用本地事务保证ACID。解决方案：1) 分布式事务(2PC/TCC/Seata)，保证强一致性但性能差；2) 最终一致性(消息队列+本地消息表)，性能好但有短暂不一致窗口
> - **分库分表后的JOIN**：不同分片的数据无法在数据库层面JOIN。解决方案：1) 冗余字段（订单表中冗余用户名）；2) 应用层组装（分别查询后在代码中合并）；3) 宽表设计（将关联数据合并到一张大表）

> **【扩展知识】**
> - **分库分表的替代方案**：TiDB（兼容MySQL协议的分布式数据库）、OceanBase（蚂蚁自研分布式数据库）、CockroachDB。这些数据库内置分片和分布式事务，无需应用层处理分库分表逻辑
> - **数据归档**：对于历史数据，可以先归档到归档表或冷存储，减小在线表的数据量，往往比分库分表更简单有效

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

> **【执行说明】**
> - `products`表：`idx_category`用于按分类筛选商品；`idx_status_price`复合索引覆盖"上架+按价格排序"的查询场景
> - `orders`表：主键为`(id, created_at)`，因为使用RANGE分区时分区键必须包含在主键中；`PARTITION BY RANGE`按年分区，便于按时间归档和删除历史订单
> - `order_items`表：`idx_order_id`用于查询订单明细；`idx_product_id`用于统计商品销量
> - 库存扣减：`WHERE stock >= 1`是乐观锁的核心，利用数据库WHERE条件的原子性保证不会超卖。`ROW_COUNT()`返回受影响行数，0表示库存不足扣减失败
> - 订单超时取消：先SELECT查出超时订单ID（用于业务日志），再UPDATE批量取消。生产环境通常由定时任务（如Celery）每分钟执行

> **【技术原理】**
> - **乐观锁的原理**：不实际加锁，而是在UPDATE的WHERE条件中加入业务约束（stock >= 扣减量）。InnoDB的UPDATE会对匹配行加X锁（排他锁），保证同一行不会被两个事务同时修改。如果stock不足，WHERE条件不匹配，影响行数为0，业务层据此判断扣减失败
> - **为什么不用SELECT FOR UPDATE**：先SELECT加锁再UPDATE是悲观锁方案，在高并发下所有线程串行等待，吞吐量低。乐观锁方案无等待，失败直接返回，吞吐量高。但乐观锁需要应用层重试（CAS模式）
> - **分区表在订单场景的价值**：订单表数据量增长最快，按年分区后：1) 历史年份的分区可以整个DROP（比DELETE快几个数量级）；2) 查询当年订单只扫描当年分区（分区裁剪）；3) 可以将旧分区迁移到冷存储
> - **DECIMAL(10,2)的选择**：金额必须用DECIMAL，不用FLOAT/DOUBLE。FLOAT是近似存储，`0.1 + 0.2 ≠ 0.3`，在金融场景会导致分账不平。DECIMAL是精确存储，(10,2)表示总共10位、小数2位，最大值99999999.99

> **【扩展知识】**
> - **库存扣减的高并发方案**：1) Redis预扣减（DECR原子操作）+ 异步MQ扣减数据库；2) 数据库乐观锁+重试（3次）；3) 秒杀场景可用Redis+Lua脚本保证原子性
> - **订单状态机**：0-待支付 → 1-已支付 → 2-已发货 → 3-已完成 → 4-已取消。状态流转必须单向，UPDATE时加`WHERE status = 当前状态`防止重复流转
> - **订单ID生成**：不要用自增ID（分库分表后冲突），推荐Snowflake雪花算法或美团Leaf号段模式

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

> **【执行说明】**
> - `PRIMARY KEY (id, tenant_id)`：将tenant_id纳入主键，确保每个租户的ID空间独立，同时支持按tenant_id分区
> - `INDEX idx_tenant (tenant_id)`：所有查询必须带tenant_id条件，此索引保证查询效率
> - 共享表方案：所有租户数据在同一张表，通过tenant_id字段区分。成本最低但隔离性最差
> - 独立Schema方案：每个租户一个Schema（同一MySQL实例），表结构相同。中等隔离性
> - 独立数据库方案：每个租户一个独立MySQL实例。隔离性最强，但成本和运维复杂度最高

> **【技术原理】**
> - **共享表方案的行级安全**：MySQL没有原生的行级安全策略（PostgreSQL有RLS），需要在应用层保证每个查询都带tenant_id条件。可以在中间件或ORM层自动注入tenant_id条件
> - **独立Schema的连接路由**：应用层根据租户信息切换Schema（`USE tenant_db_xxx`），或使用MySQL的schema名称前缀（`tenant_123.orders`）。连接池需要按租户维护
> - **独立数据库的连接管理**：每个租户需要独立的连接池，连接数 = 租户数 × 每租户连接数。当租户超过100个时，连接数可能成为瓶颈，此时需要连接代理（如ProxySQL）
> - **数据隔离的底层机制**：InnoDB的行格式中，每行数据属于特定页，页属于特定表空间(.ibd文件)。共享表方案中不同租户的数据在同一表空间；独立Schema/数据库方案中数据物理隔离

> **【扩展知识】**
> - **混合方案**：大客户用独立数据库，中小客户用共享表。通过路由中间件（如ShardingSphere）实现动态路由
> - **租户数据迁移**：从共享表迁移到独立库时，使用`INSERT INTO ... SELECT WHERE tenant_id = ?`，注意大租户数据量大需要分批迁移
> - **合规要求**：GDPR等法规要求数据物理隔离时，必须使用独立Schema或独立数据库方案

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

> **【执行说明】**
> - `detail JSON`：使用JSON类型存储审计详情，灵活适应不同操作的日志格式，无需为每种操作建不同的列
> - `PARTITION BY RANGE (YEAR(created_at) * 100 + MONTH(created_at))`：按月分区，比按年分区粒度更细，便于按月归档和删除
> - `ip_address VARCHAR(45)`：45个字符足够存储IPv6地址（最长39字符），兼容IPv4
> - `detail->>'$.table'`：JSON路径表达式查询，`->>`返回文本值（去掉引号），`->`返回JSON类型

> **【技术原理】**
> - **审计日志的分区策略**：日志数据只追加不修改，天然适合RANGE分区。按月分区后：1) 每月数据量可控；2) 过期月份直接DROP PARTITION，瞬间释放空间；3) 查询特定月份只扫描对应分区
> - **JSON列的索引**：MySQL的JSON列不能直接建索引，但可以通过生成列（Generated Column）+ 索引实现：`ALTER TABLE audit_logs ADD COLUMN detail_table VARCHAR(50) GENERATED ALWAYS AS (JSON_UNQUOTE(JSON_EXTRACT(detail, '$.table'))) STORED, ADD INDEX idx_detail_table(detail_table);`
> - **JSON存储格式**：MySQL 8.0使用二进制JSON（Partial In-Place Update），修改JSON中某个字段时，如果空间够用可以原地更新，不需要重写整个JSON值，性能比5.7好

> **【扩展知识】**
> - **审计日志的写入优化**：1) 批量INSERT（攒100条一次写入）；2) 异步写入（应用层MQ → 消费者写入MySQL）；3) 超大流量场景用ClickHouse替代MySQL存储日志
> - **合规要求**：金融/医疗行业的审计日志通常需要保留3-7年，分区表+归档存储是标准方案

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

> **【执行说明】**
> - `uk_follow (follower_id, following_id)`：唯一约束防止重复关注。INSERT重复记录会报唯一键冲突错误
> - `idx_following (following_id)`：用于"查谁关注了我"的查询，因为主查询模式是`WHERE follower_id = ?`（我关注了谁）和`WHERE following_id = ?`（谁关注了我），需要两个方向的索引
> - 互关查询：`a.following_id = b.follower_id AND a.follower_id = 1 AND b.following_id = 1`，即A关注B且B关注A
> - 动态流查询：`WHERE m.user_id IN (SELECT following_id ...)` 先获取关注列表，再查这些用户的动态。相关子查询统计点赞数和评论数

> **【技术原理】**
> - **邻接表模型**：user_follows表是经典的邻接表设计，每条记录表示一条边（关注关系）。查询"我关注的人"和"关注我的人"分别用两个方向的索引。互关查询需要自JOIN
> - **动态流的推拉模型**：
>   - 推模式：发动态时写入所有粉丝的收件箱（写扩散），读时直接读自己的收件箱。粉丝多时写代价极大
>   - 拉模式：读动态时实时查询关注人的动态（读扩散），写代价小但读代价大
>   - 推拉结合：大V用拉模式（粉丝太多推不动），普通用户用推模式。微博即采用此方案
> - **点赞表的设计**：`PRIMARY KEY (moment_id, user_id)` 既是主键又防止重复点赞。取消点赞用DELETE：`DELETE FROM moment_likes WHERE moment_id = ? AND user_id = ?`
> - **相关子查询的性能**：动态流SQL中的`(SELECT COUNT(*) FROM moment_likes WHERE moment_id = m.id)`是相关子查询，每行都执行一次。高并发场景应改为：1) 用Redis缓存计数；2) 在moments表冗余like_count字段

> **【扩展知识】**
> - **图数据库**：社交关系的深度查询（二度好友、好友推荐）在MySQL中需要多层JOIN，性能差。Neo4j等图数据库原生支持图遍历，查询"二度好友"只需毫秒级
> - **消息系统**：即时消息不建议用MySQL存储，推荐RabbitMQ/Kafka做消息通道，MongoDB/MySQL做消息持久化

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

> **【执行说明】**
> - `DECIMAL(18,2)`：18位总精度，2位小数，最大值9999999999999999.99，满足金融级精度要求
> - `version INT NOT NULL DEFAULT 0`：乐观锁版本号，每次更新+1，UPDATE时校验版本号是否变化
> - 悲观锁流程：START TRANSACTION → SELECT FOR UPDATE（加X锁）→ UPDATE → COMMIT。锁在COMMIT时释放
> - 乐观锁流程：START TRANSACTION → SELECT（不加锁）→ UPDATE WHERE version = ? → 检查ROW_COUNT()。如果ROW_COUNT()=0说明版本号已变，需要重试
> - 对账查询：计算每个账户的交易流水净额（收入-支出），与账户余额比对，`HAVING current_balance != calculated_balance`找出不一致的账户

> **【技术原理】**
> - **悲观锁 vs 乐观锁的选择**：
>   - 悲观锁：适合写冲突频繁的场景（如热点账户转账），直接加锁避免重试开销。缺点是并发度低，可能死锁
>   - 乐观锁：适合读多写少、冲突较少的场景。不加锁，吞吐量高。冲突时需要应用层重试（CAS）
> - **FOR UPDATE的锁机制**：`SELECT ... WHERE id = 1 FOR UPDATE`对id=1的行加X锁（排他锁），其他事务无法读取（快照读除外）或修改该行。如果id是主键且存在，加行锁；如果id无索引，可能退化为表锁
> - **对账的SQL原理**：`CASE WHEN t.to_account_id = a.id THEN t.amount ELSE 0 END`计算收入，`CASE WHEN t.from_account_id = a.id THEN t.amount ELSE 0 END`计算支出，两者之差即为流水净额。`COALESCE`处理没有交易记录的账户（LEFT JOIN结果为NULL）
> - **幂等性实现**：在transactions表加唯一索引`UNIQUE KEY uk_biz_no (biz_no)`，biz_no是业务方生成的唯一流水号。重复提交时INSERT会报唯一键冲突，事务回滚

> **【扩展知识】**
> - **TCC分布式事务**：跨服务转账（如A银行→B银行）无法用本地事务，需要TCC模式：Try（冻结金额）→ Confirm（扣减/增加）→ Cancel（解冻）。Seata框架提供TCC实现
> - **账户分片**：超大用户量的支付系统需要按user_id分库分表，但同一笔转账涉及两个账户可能在不同分片，需要分布式事务或补偿机制

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

> **【执行说明】**
> - 事件表使用UNIX_TIMESTAMP分区：因为RANGE分区要求分区表达式是整数，`UNIX_TIMESTAMP(event_time)`将时间转为整数秒值
> - `SUM(COUNT(*)) OVER (...)`：窗口函数实现累计统计。`OVER (PARTITION BY event_type ORDER BY DATE(event_time))`按事件类型分组，按日期排序，SUM从第一行累加到当前行
> - `LAG(COUNT(*), 1) OVER (...)`：获取前一行的值，用于计算日环比（当日-前日）
> - 漏斗分析：step1筛选浏览用户 → step2 JOIN step1找加购用户（加购时间>浏览时间）→ step3 JOIN step2找购买用户。每一步都是上一步的子集
> - 留存分析：`DATEDIFF(l.login_date, f.first_date)`计算回访日与首次登录日的差值，CASE WHEN匹配第1/7/30天

> **【技术原理】**
> - **窗口函数的执行机制**：窗口函数在GROUP BY之后、ORDER BY之前执行。`SUM(COUNT(*)) OVER (PARTITION BY ... ORDER BY ...)`的执行过程：1) GROUP BY聚合得到每日计数；2) 按PARTITION BY分组；3) 在每个分组内按ORDER BY排序；4) 对每行计算从分组第一行到当前行的SUM（默认帧范围RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW）
> - **漏斗分析的CTE链**：每个CTE（WITH子句）是一个临时结果集，step2引用step1，step3引用step2，形成链式过滤。MySQL 8.0的优化器会将CTE物化（存储中间结果），避免重复计算
> - **留存分析的LEFT JOIN**：`first_login LEFT JOIN login_dates`确保即使用户没有回访也会出现在结果中（回访列值为NULL，COUNT DISTINCT不计数）。这是留存率计算的关键

> **【扩展知识】**
> - **分析型查询的性能**：MySQL的OLAP能力有限，当事件数据量超过亿级时，建议迁移到ClickHouse或StarRocks。ClickHouse的列式存储和向量化引擎在聚合查询上比MySQL快100倍以上
> - **漏斗分析的时间窗口**：上述SQL没有限制步骤间的时间间隔。实际业务通常要求步骤在N天内完成，可在JOIN条件中加入：`AND e.event_time < DATE_ADD(s.step1_time, INTERVAL 7 DAY)`

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

> **【执行说明】**
> - `FULLTEXT INDEX ft_title_content (title, content)`：创建全文索引，支持中文分词（需配置ngram解析器：`WITH PARSER ngram`）
> - `MATCH ... AGAINST ... IN NATURAL LANGUAGE MODE`：自然语言模式，按相关度排序，返回relevance评分（0-1之间）
> - `MATCH ... AGAINST ... IN BOOLEAN MODE`：布尔模式，支持`+`（必须包含）、`-`（必须不包含）、`*`（通配符）等操作符
> - 递归CTE：锚点部分`WHERE parent_id IS NULL`找根分类，递归部分`JOIN category_tree t ON c.parent_id = t.id`逐层向下查找子分类。`level`记录层级，`path`拼接完整路径

> **【技术原理】**
> - **全文索引的底层实现**：MySQL全文索引基于倒排索引（Inverted Index）。创建全文索引时，MySQL对文本分词，建立"词→文档ID列表"的映射。查询时根据关键词在倒排索引中快速定位文档
> - **ngram解析器**：中文没有天然的分隔符，默认分词器对中文效果差。ngram按N个字符滑动窗口分词（默认bigram，2个字符一组），虽然不精确但覆盖所有可能的词组合
> - **递归CTE的执行机制**：1) 执行锚点部分，得到初始结果集；2) 将初始结果集作为递归部分的输入，执行递归查询；3) 将递归查询结果作为新的输入，继续递归；4) 直到递归查询返回空结果集；5) 合并所有结果
> - **CAST(name AS CHAR(500))**：递归CTE中递归列的类型由锚点部分决定。CAST确保path列有足够长度存储完整路径

> **【扩展知识】**
> - **全文索引的局限**：1) 不支持实时更新（需要批量导入后重建）；2) 中文分词不够精确；3) 相关度算法较简单。生产环境推荐Elasticsearch做全文搜索
> - **树形结构的其他方案**：1) 邻接表（parent_id，即上述方案，查询需递归）；2) 路径枚举（存储完整路径如"1/4/7"）；3) 嵌套集（左右值编码，查询快但更新慢）；4) 闭包表（单独的关系表存储所有祖先-后代关系）

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

> **【执行说明】**
> - `Threads_connected / max_connections`：当前连接数占比，超过80%需要排查是否有连接泄漏或扩容
> - `Threads_running`：同时活跃的线程数，持续超过CPU核数×2说明查询积压，需要优化慢SQL或扩容
> - `Queries`：每秒查询数（QPS），是衡量数据库负载的核心指标。通过两次采样计算差值除以时间间隔得到QPS
> - `Slow_queries`：慢查询累计数，持续增长需要分析慢查询日志
> - Buffer Pool命中率计算：`(1 - Innodb_buffer_pool_reads / Innodb_buffer_pool_read_requests) * 100`，低于95%需要增大Buffer Pool
> - `SHOW PROCESSLIST`：查看当前所有连接及其执行的SQL，`Time`列显示当前SQL执行秒数，大值可能是慢查询
> - `SHOW ENGINE INNODB STATUS`：InnoDB引擎状态报告，包含死锁信息、锁等待、Buffer Pool状态、事务列表等

> **【技术原理】**
> - **SHOW STATUS的工作机制**：MySQL维护一组全局状态变量（Global Status）和会话级状态变量（Session Status）。`SHOW STATUS`从内存中读取这些计数器值，性能开销极小。Performance Schema（MySQL 5.7+）提供更细粒度的监控数据
> - **Buffer Pool命中率的含义**：`Innodb_buffer_pool_reads`是从磁盘读取数据页的次数，`Innodb_buffer_pool_read_requests`是总读取请求次数。命中率 = 1 - 磁盘读取/总请求。高命中率意味着大部分数据在内存中，I/O压力小
> - **Threads_running vs Threads_connected**：connected是已建立连接数，running是正在执行SQL的线程数。大量连接但running少说明连接空闲（正常）；running持续高说明查询积压（异常）
> - **SHOW ENGINE INNODB STATUS的局限**：输出内容有长度限制，事务列表最多显示最近的事务。需要定期采集（每10秒）才能捕获完整信息

> **【扩展知识】**
> - **Prometheus + Grafana监控**：生产环境推荐使用mysqld_exporter采集MySQL指标，Prometheus存储时序数据，Grafana展示仪表盘。比手动SHOW STATUS更直观、可告警
> - **Performance Schema**：MySQL 8.0默认开启，提供SQL耗时分布、锁等待、内存使用、文件I/O等细粒度数据。`SELECT * FROM performance_schema.events_statements_summary_by_digest ORDER BY SUM_TIMER_WAIT DESC LIMIT 10;` 查看最耗时的SQL
> - **告警策略**：1) 连接数>80%告警；2) 慢查询数突增告警；3) Buffer Pool命中率<95%告警；4) 复制延迟>60秒告警；5) 磁盘使用>85%告警

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

> **【执行说明】**
> - `mysqldump test_db > backup.sql`：导出整个数据库的SQL语句（DDL+DML），备份文件是文本格式，可读可编辑
> - `mysqldump test_db users`：只备份users表
> - `--all-databases`：备份所有数据库
> - `--no-data`：只导出表结构（DDL），不导数据
> - `| gzip`：管道压缩，减少备份文件大小（通常压缩率70-90%）
> - XtraBackup全量备份：`--backup --target-dir`指定备份目录，备份期间数据库可正常读写
> - XtraBackup增量备份：`--incremental-basedir`基于上次全量备份的目录，只备份变化的数据页
> - XtraBackup恢复：先`--prepare`（应用Redo Log使数据文件一致），再`--copy-back`（将数据文件复制到MySQL数据目录）

> **【技术原理】**
> - **逻辑备份 vs 物理备份**：
>   - 逻辑备份（mysqldump）：将数据导出为SQL语句，可跨版本/跨平台恢复，但备份和恢复速度慢（需要执行SQL）
>   - 物理备份（XtraBackup）：直接复制数据文件（.ibd文件），备份和恢复速度快，但只能恢复到相同或兼容版本的MySQL
> - **mysqldump的--single-transaction**：InnoDB表在`--single-transaction`模式下使用一致性快照读取（MVCC），备份期间不锁表。不加此参数则使用LOCK TABLES，备份期间表只读
> - **XtraBackup的在线热备原理**：1) 启动时记录LSN（Log Sequence Number）；2) 复制数据文件（此时文件可能在变化）；3) 复制完成后，再复制备份期间产生的Redo Log；4) 恢复时用Redo Log前滚数据文件到一致状态。整个过程不需要锁表
> - **增量备份的原理**：XtraBackup检查每个数据页的LSN，只复制LSN大于上次备份的数据页。增量备份不是真正的"增量"，而是"差异"——基于上次备份后的变化页

> **【扩展知识】**
> - **备份恢复的RTO/RPO**：mysqldump恢复慢（需要执行SQL），RTO通常为小时级；XtraBackup恢复快（复制文件+前滚），RTO通常为分钟级
> - **备份验证**：定期（至少每季度）在测试环境恢复备份，验证备份的完整性和可恢复性。未经验证的备份等于没有备份
> - **MySQL Enterprise Backup**：Oracle官方的物理备份工具，功能类似XtraBackup，但需要商业许可
> - **云数据库备份**：AWS RDS/Azure MySQL/阿里云RDS提供自动备份和按时间点恢复功能，底层基于快照+Binlog

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

> **【执行说明】**
> - `SHOW VARIABLES LIKE 'log_bin%'`：检查Binlog是否开启，`log_bin = ON`表示已开启
> - `SHOW BINARY LOGS`：列出所有Binlog文件及大小
> - `SHOW BINLOG EVENTS`：查看Binlog中的事件（SQL语句或行变更），`Pos`是事件起始位置，`Event_type`是事件类型
> - `mysqlbinlog --start-datetime --stop-datetime`：解析Binlog文件，提取指定时间范围的事件，管道到mysql客户端执行实现数据恢复
> - ROW格式：记录每行的变更前和变更后数据，最安全但日志量最大。一条`UPDATE users SET status = 1`如果影响100万行，Binlog会记录100万行变更
> - STATEMENT格式：只记录SQL语句，日志量小。但`UPDATE users SET created_at = NOW()`在主从上执行时间不同，导致数据不一致

> **【技术原理】**
> - **Binlog的写入机制**：事务提交时，Binlog事件写入Binlog Cache（线程级内存缓冲），提交后写入Binlog文件。`sync_binlog`参数控制刷盘频率：1=每次提交fsync（最安全），0=OS决定（最快）
> - **Binlog与Redo Log的区别**：
>   - Binlog是Server层日志，逻辑日志，记录SQL或行变更，用于主从复制和数据恢复，追加写入
>   - Redo Log是InnoDB层日志，物理日志，记录页的物理修改，用于崩溃恢复，循环写入（固定大小，写满覆盖）
> - **两阶段提交**：事务提交时先写Redo Log（Prepare阶段），再写Binlog，再写Redo Log（Commit阶段）。保证Redo Log和Binlog的一致性。如果Binlog写入失败，事务回滚；如果Binlog写入成功但Commit阶段崩溃，恢复时根据Binlog决定提交还是回滚
> - **Binlog的时间点恢复（PITR）**：1) 先恢复全量备份；2) 用mysqlbinlog重放备份后到误操作前的Binlog；3) 跳过误操作的Binlog事件；4) 继续重放后续Binlog

> **【扩展知识】**
> - **binlog2sql闪回工具**：从Binlog中解析出误操作的逆向SQL（INSERT→DELETE，DELETE→INSERT，UPDATE→反向UPDATE），实现快速闪回。要求Binlog格式为ROW
> - **GTID（全局事务ID）**：`server_uuid:transaction_id`格式，唯一标识每个事务。开启GTID后，主从复制和故障恢复更简单，不需要手动指定Binlog位置
> - **Binlog的磁盘空间**：生产环境Binlog增长很快（高TPS系统每天几十GB），需设置`binlog_expire_logs_seconds`自动清理过期日志

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

> **【执行说明】**
> - 主库配置：`server-id = 1`（集群内唯一）、`log-bin`开启Binlog、`binlog-format = ROW`（推荐ROW格式）、`gtid-mode = ON`开启GTID
> - 创建复制用户：`REPLICATION SLAVE`权限允许用户读取主库Binlog
> - `SHOW MASTER STATUS`：查看当前Binlog文件名和位置（Position），用于配置从库的起始复制点
> - 从库配置：`server-id = 2`（不能与主库相同）、`relay-log`指定中继日志文件名
> - `CHANGE REPLICATION SOURCE TO`：配置主库连接信息。`SOURCE_AUTO_POSITION = 1`使用GTID自动定位复制点
> - `SHOW REPLICA STATUS\G`：查看复制状态，关键字段：`Replica_IO_Running`/`Replica_SQL_Running`应为Yes，`Seconds_Behind_Master`为主从延迟秒数

> **【技术原理】**
> - **三个线程的协作**：
>   - Binlog Dump线程（主库）：监听Binlog变化，将新事件发送给从库的I/O线程
>   - I/O线程（从库）：连接主库，接收Binlog事件，写入Relay Log（中继日志）
>   - SQL线程（从库）：读取Relay Log，重放SQL语句或行变更，将数据写入从库
> - **GTID的优势**：每个事务有全局唯一ID，从库自动追踪已执行的事务。主从切换时不需要手动计算Binlog位置，新从库自动从缺失的GTID位置开始复制
> - **主从延迟的原因**：1) 从库单线程重放（MySQL 5.6之前），主库并发写入但从库串行重放；2) 大事务（一个事务更新百万行，从库需要同样时间重放）；3) 从库硬件性能差；4) 网络延迟
> - **并行复制**（MySQL 5.7+）：`slave_parallel_type = LOGICAL_CLOCK`，同一组事务（在主库同一时刻提交的事务）可以在从库并行重放，大幅减少延迟

> **【扩展知识】**
> - **半同步复制**：主库提交事务后，等待至少一个从库确认收到Binlog才返回成功。比异步复制多一重保障，比全同步复制性能好。`rpl_semi_sync_master_enabled = ON` 开启
> - **多源复制**：一个从库同时复制多个主库的数据，用于数据汇聚场景。`CHANGE REPLICATION SOURCE TO FOR CHANNEL 'channel_name'`
> - **延迟从库**：`CHANGE REPLICATION SOURCE TO SOURCE_DELAY = 3600`，从库延迟1小时执行。用于防误操作——发现误操作后，在延迟从库上停止复制，从库中还有误操作前的数据

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

> **【执行说明】**
> - 读写分离的核心：写操作路由到主库，读操作路由到从库。通过代理中间件或应用层路由实现
> - ProxySQL：MySQL代理中间件，内置查询规则引擎，根据SQL类型（SELECT/INSERT/UPDATE/DELETE）自动路由。支持连接池、查询缓存、读写分离
> - ShardingSphere-Proxy：支持读写分离和分库分表，配置灵活，语言无关
> - 应用层路由：在代码中根据操作类型选择数据源。Django的`DATABASE_ROUTERS`即为此方案

> **【技术原理】**
> - **读写分离的延迟问题**：主库写入后，从库可能还有毫秒到秒级的延迟。用户刚写入的数据，立即从从库读可能读不到。解决方案：1) 关键读操作强制走主库；2) 中间件提供"写后读一致性"路由（同一会话的读请求路由到主库）
> - **代理中间件的工作机制**：1) 应用连接代理（而非直连MySQL）；2) 代理解析SQL判断类型；3) SELECT路由到从库（支持负载均衡），其他路由到主库；4) 代理转发MySQL协议，对应用透明
> - **从库负载均衡**：多个从库时，代理可以轮询（Round-Robin）或加权分配读请求。权重根据从库性能和延迟动态调整

> **【扩展知识】**
> - **ProxySQL配置要点**：1) 配置主从后端（`mysql_servers`表）；2) 配置读写分离规则（`mysql_query_rules`表）；3) 配置连接池参数；4) 配置健康检查
> - **Django读写分离**：见附录H，通过`DATABASE_ROUTERS`配置，读操作路由到replica，写操作路由到default

### 12.3 高可用方案对比

| 方案                | 架构                     | 自动故障转移 | 适用规模 |
| ----------------- | ---------------------- | ------ | ---- |
| MHA               | 主从+Manager             | ✅ 30秒内 | 中小   |
| MGR               | Paxos协议组复制             | ✅      | 中大   |
| InnoDB Cluster    | MGR+MySQL Shell+Router | ✅      | 中大   |
| MySQL NDB Cluster | 分布式存储                  | ✅      | 超大   |

> **【执行说明】**
> - MHA：最成熟的主从切换方案，适用于传统异步/半同步复制架构。需要额外部署MHA Manager节点
> - MGR：MySQL官方组复制方案，基于Paxos协议保证数据一致性。支持单主和多主模式
> - InnoDB Cluster：MGR的完整封装，包含MySQL Shell（管理工具）+ MySQL Router（路由中间件）+ MGR（复制），开箱即用
> - MySQL NDB Cluster：分布式存储引擎，数据自动分片，无单点故障。但与InnoDB不兼容，应用需要适配

> **【技术原理】**
> - **MHA的故障切换原理**：1) Manager定期ping主库；2) 检测到主库不可达后，验证从库状态；3) 选择数据最新的从库作为新主库；4) 补全其他从库的差异Relay Log；5) 将其他从库指向新主库；6) VIP漂移到新主库
> - **MGR的Paxos协议**：事务提交时，需要组内多数节点（>N/2）确认后才能提交。保证只要多数节点存活，数据不丢失。网络分区时，少数派节点无法提交事务
> - **InnoDB Cluster的组件协作**：MySQL Shell用于初始化集群和管理节点；MySQL Router作为代理，感知集群拓扑变化，自动路由到新的Primary；MGR负责数据复制和故障检测

> **【扩展知识】**
> - **方案选择建议**：新项目推荐InnoDB Cluster（官方方案，生态好）；已有主从架构推荐MHA（侵入性小）；超大规模考虑NDB Cluster或TiDB
> - **云数据库高可用**：AWS RDS Multi-AZ、阿里云RDS高可用版等，底层基于主从复制+自动故障转移，用户无需运维

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

> **【执行说明】**
> - `binlog-format = ROW`：MGR强制要求ROW格式，因为STATEMENT格式可能导致不同节点执行结果不一致
> - `binlog-checksum = NONE`：MGR当前不支持Binlog校验和，必须关闭
> - `log-slave-updates = ON`：从库将Relay Log中的事件写入自己的Binlog，MGR需要此选项
> - `group_replication_group_name`：集群UUID，所有节点必须相同
> - `group_replication_local_address`：本节点的Group Communication端口（不是MySQL端口3306），用于Paxos通信
> - `group_replication_group_seeds`：集群种子节点列表，新节点通过种子节点加入集群
> - Bootstrap引导：仅首次创建集群时在第一个节点执行，`bootstrap_group = ON`表示"我是集群的创建者"。其他节点只需`START GROUP_REPLICATION`加入已有集群
> - `REPLICATION_GROUP_MEMBERS`：查看集群成员状态，`MEMBER_STATE`应为ONLINE

> **【技术原理】**
> - **MGR的事务认证**：事务在本地执行后，通过Paxos协议广播给所有节点。每个节点独立进行认证（Conflict Detection），检查是否有并发冲突。如果认证通过，事务提交；否则回滚
> - **单主模式 vs 多主模式**：
>   - 单主模式：只有一个Primary节点可写，其他Secondary只读。Primary故障时自动选举新Primary（按server-id排序）。无冲突，性能好
>   - 多主模式：所有节点可写，但并发写同一行会冲突回滚。适合写冲突少的场景
> - **Paxos协议的工作流程**：1) Proposer（提议者）发起事务提议；2) Acceptors（接受者）多数同意后提议通过；3) 所有节点按相同顺序执行事务。保证所有节点的数据一致
> - **流量控制**：当某个节点落后时（如网络慢），MGR自动降低整个集群的写入速率，让慢节点追上。`group_replication_flow_control_mode`控制是否开启

> **【扩展知识】**
> - **MGR的网络要求**：节点间通信延迟应<5ms，建议部署在同一局域网。跨机房部署需要专线网络
> - **MGR的限制**：1) 不支持MyISAM表；2) 不支持外键（多主模式）；3) 大事务可能被拒绝（`group_replication_transaction_size_limit`）；4) 不支持SERIALIZABLE隔离级别

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

> **【执行说明】**
> - `manager_workdir`/`manager_log`：MHA Manager的工作目录和日志文件
> - `user`/`password`：MHA监控MySQL的数据库用户，需要REPLICATION CLIENT、SUPER权限
> - `repl_user`/`repl_password`：复制用户，用于重新配置主从关系
> - `ssh_user`：MHA通过SSH连接各节点执行命令，需要免密登录
> - `candidate_master=1`：标记该节点可以作为新主库的候选。优先选择数据最新的候选节点
> - `no_master=1`：标记该节点永远不会成为主库（如配置较低的从库）

> **【技术原理】**
> - **MHA的Relay Log补全**：主库崩溃时，某些从库可能还没收到最新的Binlog事件。MHA尝试从崩溃主库的SSH连接中读取最新的Binlog，补发给从库。如果主库SSH也不可达，则选择已接收最多Binlog的从库作为新主库，其他从库从这个新主库同步差异
> - **MHA的选择策略**：1) 优先选candidate_master=1的节点；2) 在候选节点中选择Binlog位置最新（延迟最小）的；3) 如果所有节点延迟相同，按配置文件顺序选择
> - **VIP漂移**：MHA切换完成后，需要将应用连接的VIP（虚拟IP）从旧主库迁移到新主库。通过`master_ip_failover_script`脚本执行`ip addr add`和`ip addr del`命令

> **【扩展知识】**
> - **MHA的局限**：1) Manager是单点，需要用Keepalived做高可用；2) 不保证数据零丢失（异步复制场景）；3) 只支持一主多从架构，不支持多主
> - **MHA vs MGR**：MHA是故障切换工具（被动），MGR是高可用方案（主动）。MHA适用于已有主从架构的升级改造，MGR适用于新项目

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

> **【执行说明】**
> - RPO：衡量数据丢失量。RPO=0表示不丢数据，RPO=1小时表示最多丢1小时数据
> - RTO：衡量服务恢复速度。RTO=30秒表示故障后30秒内恢复服务
> - 双1配置：`innodb_flush_log_at_trx_commit = 1` + `sync_binlog = 1`，保证事务提交时Redo Log和Binlog都fsync到磁盘，崩溃不丢数据
> - 延迟从库：`MASTER_DELAY = 3600`表示从库延迟3600秒（1小时）执行。误操作后，在延迟从库上`STOP SLAVE`，从库中还有1小时前的数据
> - binlog2sql闪回：`-B`参数生成逆向SQL（INSERT→DELETE，DELETE→INSERT，UPDATE→反向UPDATE），`-o`输出到文件

> **【技术原理】**
> - **RPO与架构的关系**：RPO取决于数据同步方式。异步复制：主库崩溃时从库可能没收到最新数据，RPO>0；半同步复制：至少一个从库确认收到Binlog，RPO≈0；MGR：多数节点确认后才提交，RPO=0
> - **双1配置的性能影响**：每次事务提交都需要两次fsync（Redo Log + Binlog），fsync是同步磁盘I/O操作，耗时约1-10ms。高TPS场景下，双1配置可能使TPS下降50%以上
> - **binlog2sql的原理**：解析ROW格式的Binlog，提取行变更事件。INSERT事件的逆向是DELETE（用新值定位），DELETE事件的逆向是INSERT（用旧值插入），UPDATE事件的逆向是反向UPDATE（旧值→新值变为新值→旧值）
> - **延迟从库的机制**：I/O线程正常接收Binlog写入Relay Log，但SQL线程延迟执行。`SQL_Delay`参数控制延迟秒数。延迟期间Relay Log持续积累，SQL线程按延迟时间顺序执行

> **【扩展知识】**
> - **容灾等级**：1) 同城双活（RPO=0，RTO=秒级）；2) 异地灾备（RPO=秒级，RTO=分钟级）；3) 两地三中心（最高级别，RPO=0，RTO=秒级）
> - **数据安全审计**：定期检查：1) 备份是否正常执行；2) 备份是否可恢复；3) 权限是否最小化；4) Binlog是否保留足够时间；5) 延迟从库是否正常

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

> **【学习建议】**
> - **理论与实践结合**：每个模块学完后，在本地MySQL环境中实际操作验证。尤其是索引优化（模块五/九）和事务（模块六），必须亲手执行EXPLAIN和事务实验
> - **从整体到局部**：先理解MySQL的整体架构（模块二），再深入各子系统。知道"这个组件在整体中的位置"比"这个组件的细节"更重要
> - **关注原理而非语法**：SQL语法可以查文档，但MVCC、B+树、锁机制等原理需要深入理解。面试和排查问题时，原理知识是关键
> - **循序渐进**：不要跳过基础模块直接学高级特性。模块一到七是理解后续内容的前提

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

> **【关键特性解读】**
> - **GTID复制（5.6+）**：全局事务ID使主从切换和故障恢复大幅简化，不再需要手动计算Binlog位置。新项目必须开启GTID
> - **在线DDL（5.6+）**：ALTER TABLE期间允许并发DML操作，不再锁表。但大表DDL仍可能影响性能，建议使用pt-online-schema-change或gh-ost工具
> - **ICP索引下推（5.6+）**：存储引擎在索引层面判断WHERE条件，减少回表次数。对复合索引的查询性能提升明显
> - **窗口函数和CTE（8.0+）**：大幅增强了MySQL的分析查询能力。之前需要用子查询和变量实现的排名、累计、递归查询，现在可以用标准SQL完成
> - **HASH JOIN（8.0.18+）**：优化器自动选择HASH JOIN替代NESTED LOOP JOIN，对无索引的JOIN性能提升10倍以上
> - **EXPLAIN ANALYZE（8.0.22+）**：实际执行SQL并返回真实耗时，比传统EXPLAIN的估算值更准确

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

> **【参数调优优先级】**
> 1. **innodb_buffer_pool_size**（影响最大）：设为物理内存60%-80%，这是最重要的参数。Buffer Pool越大，磁盘I/O越少
> 2. **innodb_flush_log_at_trx_commit + sync_binlog**（安全与性能平衡）：金融场景双1，一般业务1+100，日志场景2+100
> 3. **innodb_io_capacity**（SSD必调）：SSD设为5000-20000，否则InnoDB后台刷脏页太慢
> 4. **innodb_flush_method**（Linux必调）：设为O_DIRECT避免双重缓存
> 5. **连接和缓冲区参数**：根据业务并发量调整，不要盲目增大

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

> **【type判断要点】**
> - 目标：至少达到`ref`级别，JOIN至少`eq_ref`，范围查询`range`可接受
> - `index`比`ALL`好（索引比数据小），但仍需优化——检查是否可以用WHERE条件进一步过滤
> - `index_merge`表示使用了多个索引合并结果，虽然用了索引但效率不如一个复合索引，考虑优化索引设计
> - `ALL`必须优化，除非表很小（<1000行）或确实需要全表扫描

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

> **【选型建议】**
> - **MySQL**：Web应用、互联网公司、OLTP场景。生态最丰富，人才最多，运维成本最低
> - **PostgreSQL**：复杂查询、GIS（PostGIS）、JSONB场景、需要物化视图的场景。分析能力比MySQL强
> - **Oracle**：大型企业、金融核心系统。功能最全，但许可费用极高
> - **SQL Server**：Windows生态、.NET技术栈。与Azure深度集成

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

> **【运维命令分类】**
> - **诊断类**：SHOW STATUS / SHOW PROCESSLIST / SHOW ENGINE INNODB STATUS —— 排查问题时首先使用
> - **监控类**：SHOW STATUS LIKE 'Innodb%' / SHOW STATUS LIKE 'Threads%' —— 定期采集，建立基线
> - **备份类**：mysqldump / xtrabackup —— 按策略定期执行
> - **恢复类**：mysqlbinlog / mysql —— 紧急恢复时使用
> - **维护类**：mysqlcheck / ANALYZE TABLE —— 定期维护

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

> **【JSON使用要点】**
> - **`->` vs `->>`**：`->`返回JSON类型（带引号），`->>`返回文本（去引号）。WHERE条件中通常用`->>`，因为需要与字符串比较
> - **JSON_SET vs JSON_INSERT vs JSON_REPLACE**：SET最通用（存在更新+不存在新增）；INSERT只新增不更新；REPLACE只更新不新增
> - **JSON索引**：JSON列不能直接建索引，必须通过生成列（Generated Column）间接索引。`INDEX idx_profile_name ((CAST(profile->>'$.name' AS CHAR(50))))` 是MySQL 8.0的函数索引语法
> - **JSON_TABLE**：MySQL 8.0新增，将JSON数组展开为关系表，可以像普通表一样JOIN和WHERE。非常适合处理JSON数组数据

***

## 附录G：面试高频问题

**Q1：InnoDB为什么用B+树而不是B树做索引？**
B+树非叶子节点只存键值不存数据，单个页能存更多键，树更矮（3层约存2000万行），I/O次数少。叶子节点双向链表，范围查询高效。

> **【深度解析】** B树每个节点都存数据，16KB的页在B树中能存的键值数远少于B+树。假设键值+指针=12字节，数据行=1KB：B树一个页存约16个键值+数据，3层B树存16×16×16=4096行；B+树非叶子节点只存键值，一个页存约1360个键值，3层存1360×1360×N≈2000万行。差距约5000倍

**Q2：什么是回表？如何避免？**
通过二级索引查到主键值，再回聚簇索引查完整行数据叫回表。覆盖索引（查询列都在索引中）可避免回表。

> **【深度解析】** 回表的代价：每次回表都是一次随机I/O（从二级索引B+树跳到聚簇索引B+树）。如果查询100行，最坏需要100次随机I/O。覆盖索引让查询在二级索引上完成，只需顺序扫描，无需跳转

**Q3：MySQL事务隔离级别怎么选？**
生产默认RR（REPEATABLE READ），InnoDB通过MVCC+Next-Key Lock防止幻读。RC级别没有Gap Lock，并发更好但可能出现不可重复读。一般不建议用RU和Serializable。

> **【深度解析】** RC vs RR的选择：互联网公司倾向RC，因为：1) RC每次SELECT创建新Read View，数据更实时；2) 没有Gap Lock，锁冲突少，并发好；3) RC下semi-consistent read优化生效。金融场景倾向RR，因为需要可重复读保证

**Q4：MVCC怎么实现的？**
每行有隐藏列（trx\_id、roll\_ptr），修改时通过Undo Log形成版本链。SELECT时创建Read View，按可见性规则沿版本链查找可见版本。RC每次SELECT创建新Read View，RR只在首次SELECT创建。

> **【深度解析】** Read View的可见性规则：1) trx_id < min_trx_id → 可见（事务在Read View创建前已提交）；2) trx_id >= max_trx_id → 不可见（事务在Read View创建后才开始）；3) min_trx_id <= trx_id < max_trx_id → 检查trx_id是否在m_ids列表中，在则不可见，不在则可见

**Q5：Redo Log和Binlog有什么区别？**
Redo Log是InnoDB引擎层日志，循环写入，保证崩溃恢复；Binlog是Server层日志，追加写入，用于主从复制和数据恢复。两阶段提交保证两者一致性。

> **【深度解析】** 两阶段提交的必要性：如果没有两阶段提交，可能出现Redo Log已提交但Binlog未写入的情况。崩溃恢复后，主库有该事务但Binlog中没有，从库不会执行该事务，导致主从不一致

**Q6：如何优化一条慢SQL？**
1. EXPLAIN查看执行计划；2. 关注type（避免ALL）和Extra（避免filesort/temporary）；3. 添加合适索引（遵循最左前缀）；4. 避免索引失效场景；5. 覆盖索引减少回表；6. 深分页用游标方案。

> **【深度解析】** 优化步骤详解：1) `EXPLAIN`看type和Extra；2) type=ALL → 加索引；3) type=ref但rows很大 → 检查索引选择性；4) Extra有filesort → ORDER BY列加索引；5) Extra有temporary → GROUP BY列加索引；6) 深分页 → 改为游标分页；7) 还慢 → 考虑改写SQL或架构优化

**Q7：分库分表后怎么做跨片查询？**
尽量通过分片键路由到单分片。必须跨片时：聚合查询各分片汇总；JOIN用冗余字段或应用层组装；深度分页用ES辅助或禁止。

> **【深度解析】** 跨片查询是分库分表最大的痛点。最佳实践：1) 80%以上的查询走分片键路由到单分片；2) 非分片键查询用ES宽表辅助；3) 跨片JOIN用冗余字段避免；4) 跨片聚合用ShardingSphere自动合并；5) 深度分页禁止，改为滚动加载

**Q8：MySQL主从延迟怎么处理？**
原因：从库单线程重放慢、大事务、网络延迟。方案：并行复制（多线程SQL线程）、拆分大事务、半同步复制、读写分离时容忍短暂延迟（强制走主库读关键数据）。

> **【深度解析】** 并行复制参数：`slave_parallel_type = LOGICAL_CLOCK`（按组并行）、`slave_parallel_workers = 4-8`（并行线程数）。MySQL 8.0的Writeset并行复制进一步提升了并行度，几乎可以做到主库多少并发从库就多少并发

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

> **【Django+MySQL要点】**
> - **CONN_MAX_AGE = 60**：Django默认每个请求结束关闭连接，设为60秒复用连接。但要注意MySQL的`wait_timeout`（默认8小时），如果连接空闲超过此时间被MySQL断开，Django会报"MySQL server has gone away"
> - **ATOMIC_REQUESTS = True**：每个视图函数自动包裹在事务中，视图正常返回则COMMIT，抛异常则ROLLBACK
> - **select_related vs prefetch_related**：select_related用于ForeignKey/OneToOne（JOIN查询，1次SQL）；prefetch_related用于ManyToMany/反向ForeignKey（2次SQL+Python合并）
> - **only vs defer**：only指定只查的列（覆盖索引），defer指定不查的列（排除大字段）。两者都延迟加载，访问未加载列会触发额外查询
> - **bulk_create**：批量插入，`batch_size=100`每100条一次INSERT。比循环create快10-100倍
> - **F表达式**：`F("price") * 1.1`在数据库层面计算，不加载到Python内存，避免并发问题

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

> **【分库分表实战要点】**
> - **分片键选择是成败关键**：必须覆盖80%以上的查询场景。电商订单用user_id（按用户查订单），不用order_id（很少有按订单号查的场景）
> - **ShardingSphere-JDBC vs Proxy**：JDBC嵌入应用，性能好但只支持Java；Proxy独立部署，语言无关但有网络开销。Django项目推荐Proxy方案
> - **Django分库方案**：通过`DATABASES`配置多个数据库 + `using(db)`指定数据库。复杂分片逻辑建议用ShardingSphere-Proxy，Django只需连接Proxy
> - **扩容策略**：从4分片扩到8分片时，取模分片需要迁移50%的数据。一致性哈希只需迁移约25%。推荐双写方案：新数据同时写旧分片和新分片，历史数据异步迁移

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

> **【索引设计深度解析】**
> - **选择性计算**：`SELECT COUNT(DISTINCT col) / COUNT(*) FROM table;` 选择性越接近1，索引效果越好。性别列选择性约0.5，不适合单独建索引；手机号选择性接近1，适合建索引
> - **最左前缀的底层原因**：B+树按索引列顺序排列。INDEX(a,b,c)先按a排序，a相同按b排序，b相同按c排序。跳过a直接查b，B+树无法利用有序性定位
> - **覆盖索引的判断**：EXPLAIN的Extra列显示`Using index`即为覆盖索引。覆盖索引不需要回表，性能提升显著（减少50%以上的I/O）
> - **冗余索引的危害**：每个索引都需要维护（INSERT/UPDATE/DELETE时更新B+树），索引越多写性能越差。建议定期用`pt-duplicate-key-checker`检查冗余索引