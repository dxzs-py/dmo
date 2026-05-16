# 零基础到进阶的MySQL系统学习手册

## 前言

本手册旨在帮助零基础用户系统学习MySQL数据库，从基础操作到高级特性，循序渐进地掌握MySQL的核心知识点。手册中的所有实战案例均基于MySQL 8.0版本，可直接复制运行。

## 准备工作

在开始学习前，请确保你已经安装了MySQL 8.0。可以通过以下命令检查MySQL版本：

```sql
SELECT VERSION();
```

## 模块一：基础操作

### 1.1 连接MySQL

**功能说明**：连接到MySQL服务器。

**实战案例**：

```bash
# 命令行连接
mysql -u root -p

# 输入密码后即可连接
```

### 1.2 数据库操作

**功能说明**：创建、查看、删除数据库。

**实战案例**：

```sql
-- 创建数据库
CREATE DATABASE test_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 查看所有数据库
SHOW DATABASES;

-- 使用数据库
USE test_db;

-- 删除数据库（谨慎操作）
DROP DATABASE IF EXISTS test_db;
```

### 1.3 表操作

**功能说明**：创建、查看、修改、删除表。

**实战案例**：

```sql
-- 创建表
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    age INT,
    email VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 查看所有表
SHOW TABLES;

-- 查看表结构
DESCRIBE users;

-- 修改表（添加字段）
ALTER TABLE users ADD COLUMN phone VARCHAR(20);

-- 删除表
DROP TABLE IF EXISTS users;
```

## 模块二：SQL语法

### 2.1 插入数据

**功能说明**：向表中插入数据。

**实战案例**：

```sql
-- 插入单条数据
INSERT INTO users (name, age, email) VALUES ('张三', 25, 'zhangsan@example.com');

-- 插入多条数据
INSERT INTO users (name, age, email) VALUES 
('李四', 30, 'lisi@example.com'),
('王五', 28, 'wangwu@example.com');
```

### 2.2 查询数据

**功能说明**：从表中查询数据。

**实战案例**：

```sql
-- 查询所有数据
SELECT * FROM users;

-- 查询指定字段
SELECT name, age FROM users;

-- 带条件查询
SELECT * FROM users WHERE age > 25;

-- 排序查询
SELECT * FROM users ORDER BY age DESC;

-- 分页查询
SELECT * FROM users LIMIT 2 OFFSET 1;
```

### 2.3 更新数据

**功能说明**：更新表中的数据。

**实战案例**：

```sql
-- 更新单条数据
UPDATE users SET age = 26 WHERE name = '张三';

-- 更新多条数据
UPDATE users SET age = age + 1 WHERE age > 25;
```

### 2.4 删除数据

**功能说明**：删除表中的数据。

**实战案例**：

```sql
-- 删除指定数据
DELETE FROM users WHERE name = '张三';

-- 删除所有数据（谨慎操作）
DELETE FROM users;
```

### 2.5 聚合函数

**功能说明**：对数据进行聚合计算。

**实战案例**：

```sql
-- 计算平均年龄
SELECT AVG(age) AS avg_age FROM users;

-- 计算最大年龄
SELECT MAX(age) AS max_age FROM users;

-- 计算总人数
SELECT COUNT(*) AS total_users FROM users;
```

### 2.6 分组查询

**功能说明**：按指定字段分组查询。

**实战案例**：

```sql
-- 按年龄分组，计算每组人数
SELECT age, COUNT(*) AS count FROM users GROUP BY age;

-- 按年龄分组，只显示人数大于1的组
SELECT age, COUNT(*) AS count FROM users GROUP BY age HAVING count > 1;
```

### 2.7 连接查询

**功能说明**：连接多个表进行查询。

**实战案例**：

```sql
-- 创建订单表
CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    amount DECIMAL(10,2),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 插入订单数据
INSERT INTO orders (user_id, amount) VALUES (1, 100.00), (2, 200.00);

-- 内连接查询
SELECT users.name, orders.amount FROM users INNER JOIN orders ON users.id = orders.user_id;

-- 左连接查询
SELECT users.name, orders.amount FROM users LEFT JOIN orders ON users.id = orders.user_id;
```

## 模块三：约束

### 3.1 主键约束

**功能说明**：确保表中每行数据的唯一性。

**实战案例**：

```sql
-- 创建带主键的表
CREATE TABLE products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    price DECIMAL(10,2)
);
```

### 3.2 唯一约束

**功能说明**：确保指定字段的值唯一。

**实战案例**：

```sql
-- 创建带唯一约束的表
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    email VARCHAR(100) UNIQUE,
    age INT
);

-- 尝试插入重复邮箱（会失败）
INSERT INTO users (name, email, age) VALUES ('张三', 'zhangsan@example.com', 25);
INSERT INTO users (name, email, age) VALUES ('李四', 'zhangsan@example.com', 30);
```

### 3.3 非空约束

**功能说明**：确保指定字段不能为空。

**实战案例**：

```sql
-- 创建带非空约束的表
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    age INT
);

-- 尝试插入空名字（会失败）
INSERT INTO users (age) VALUES (25);
```

### 3.4 外键约束

**功能说明**：确保两个表之间的关联关系。

**实战案例**：

```sql
-- 创建部门表
CREATE TABLE departments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL
);

-- 创建员工表，带外键约束
CREATE TABLE employees (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    dept_id INT,
    FOREIGN KEY (dept_id) REFERENCES departments(id)
);

-- 插入部门数据
INSERT INTO departments (name) VALUES ('技术部'), ('市场部');

-- 插入员工数据
INSERT INTO employees (name, dept_id) VALUES ('张三', 1), ('李四', 2);
```

### 3.5 检查约束

**功能说明**：确保字段值满足指定条件。

**实战案例**：

```sql
-- 创建带检查约束的表
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    age INT CHECK (age >= 18)
);

-- 尝试插入年龄小于18的记录（会失败）
INSERT INTO users (name, age) VALUES ('张三', 17);
```

## 模块四：索引

### 4.1 创建索引

**功能说明**：提高查询速度。

**实战案例**：

```sql
-- 创建普通索引
CREATE INDEX idx_name ON users(name);

-- 创建唯一索引
CREATE UNIQUE INDEX idx_email ON users(email);

-- 创建复合索引
CREATE INDEX idx_name_age ON users(name, age);
```

### 4.2 查看索引

**功能说明**：查看表中的索引。

**实战案例**：

```sql
-- 查看表的索引
SHOW INDEX FROM users;
```

### 4.3 删除索引

**功能说明**：删除不需要的索引。

**实战案例**：

```sql
-- 删除索引
DROP INDEX idx_name ON users;
```

### 4.4 索引使用场景

**功能说明**：了解何时使用索引。

**实战案例**：

```sql
-- 为经常用于查询条件的字段创建索引
CREATE INDEX idx_age ON users(age);

-- 为经常用于排序的字段创建索引
CREATE INDEX idx_created_at ON users(created_at);
```

## 模块五：事务

### 5.1 事务基础

**功能说明**：确保一组操作要么全部成功，要么全部失败。

**实战案例**：

```sql
-- 开始事务
START TRANSACTION;

-- 执行操作
UPDATE users SET age = 26 WHERE id = 1;
INSERT INTO orders (user_id, amount) VALUES (1, 150.00);

-- 提交事务
COMMIT;

-- 或者回滚事务
-- ROLLBACK;
```

### 5.2 事务隔离级别

**功能说明**：控制事务之间的隔离程度。

**实战案例**：

```sql
-- 查看当前隔离级别
SELECT @@transaction_isolation;

```

```sql
-- 结果
+-------------------------+
| @@transaction_isolation |
+-------------------------+
| REPEATABLE-READ         |
+-------------------------+
1 row in set (0.00 sec)
```

| 隔离级别                    | 脏读 | 不可重复读 | 幻读                    | 说明                                                 |
| --------------------------- | ---- | ---------- | ----------------------- | ---------------------------------------------------- |
| READ UNCOMMITTED (未提交读) | 可能 | 可能       | 可能                    | 级别最低，几乎不使用，数据最不安全。                 |
| READ COMMITTED (提交读)     | 安全 | 可能       | 可能                    | Oracle/SQL Server 的默认级别。只能读到已提交的数据。 |
| REPEATABLE READ (可重复读)  | 安全 | 安全       | 可能(但在MySQL中已优化) | MySQL 默认级别。兼顾了数据一致性和性能。             |
| SERIALIZABLE (串行化)       | 安全 | 安全       | 安全                    | 级别最高，所有事务排队执行，效率最低。               |



```sql
-- 设置隔离级别
SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;
```



### 5.3 事务并发问题

**功能说明**：了解事务并发可能导致的问题。

**实战案例**：

```sql
-- 脏读示例
-- 会话1
START TRANSACTION;
UPDATE users SET age = 30 WHERE id = 1;

-- 会话2（未提交时读取）
SELECT age FROM users WHERE id = 1;

-- 会话1
ROLLBACK;

-- 会话2再次读取
SELECT age FROM users WHERE id = 1;
```

## 模块六：存储过程

### 6.1 创建存储过程

**功能说明**：封装可重复使用的SQL代码。

**实战案例**：

```sql
-- 创建存储过程
DELIMITER //
CREATE PROCEDURE get_user_by_id(IN user_id INT)
BEGIN
    SELECT * FROM users WHERE id = user_id;
END //
DELIMITER ;
```

这段 SQL 代码的主要功能是**创建一个名为 `get_user_by_id` 的存储过程**。

简单来说，它就像是在数据库里写好了一个“函数”或“脚本”。以后你只需要告诉数据库“我要查 ID 为 5 的用户”，数据库就会自动执行里面写好的 `SELECT` 语句，而不需要你每次都重新输入一遍查询代码。

下面是逐行详细的解释：

1. `DELIMITER //`

- **含义**：告诉 MySQL，“请把语句的结束符号暂时从默认的分号 `;` 改成双斜杠 `//`”。
- **为什么要这样做？**
    因为存储过程内部包含 SQL 语句（比如 `SELECT ...;`），这些语句本身是以分号结尾的。如果不改结束符，MySQL 读到内部的分号就会以为整个命令结束了，从而报错。改成 `//` 后，MySQL 就会一直读取，直到遇到 `//` 才认为整个存储过程的定义结束了。

2. `CREATE PROCEDURE get_user_by_id(IN user_id INT)`

- **`CREATE PROCEDURE`**：声明要创建一个存储过程。
- **`get_user_by_id`**：这是你给这个存储过程起的名字。
- **`(IN user_id INT)`**：这是**参数列表**。
    - `IN`：表示这是一个**输入参数**（调用时传进去的值）。
    - `user_id`：参数的名字。
    - `INT`：参数的数据类型是整数。

3. `BEGIN ... END`

- 这是存储过程的主体部分，包裹着实际要执行的逻辑代码。

4. `SELECT * FROM users WHERE id = user_id;`

- 这是具体的业务逻辑。
- 它会在 `users` 表中查找数据。
- **注意**：这里的 `user_id` 使用的是你传入的参数值，而不是表里的列名（虽然列名通常也叫 id）。

5. `DELIMITER ;`

- **含义**：存储过程定义完毕后，把结束符号**恢复**为默认的分号 `;`。这很重要，否则你以后执行普通 SQL 语句时如果忘记加 `//` 就会报错。

---

**🚀 如何使用这个存储过程？**

创建好之后，你可以这样调用它（例如查询 ID 为 101 的用户）：

```sql
CALL get_user_by_id(101);
```

**这样做的好处是：**

1.  **复用性**：写一次，到处调用。
2.  **安全性**：可以限制用户只通过存储过程访问数据，而不直接操作表。
3.  **性能**：存储过程是预编译的，执行效率通常比单独发送 SQL 语句稍高。

### 6.2 调用存储过程

**功能说明**：执行存储过程。

**实战案例**：

```sql
-- 调用存储过程
CALL get_user_by_id(1);
```

### 6.3 删除存储过程

**功能说明**：删除不需要的存储过程。

**实战案例**：

```sql
-- 删除存储过程
DROP PROCEDURE IF EXISTS get_user_by_id;
```

### 6.4 存储过程参数

**功能说明**：使用输入输出参数。

**实战案例**：

```sql
-- 创建带输出参数的存储过程
DELIMITER //
CREATE PROCEDURE get_user_count(OUT total INT)
BEGIN
    SELECT COUNT(*) INTO total FROM users;
END //
DELIMITER ;

-- 调用带输出参数的存储过程
SET @total = 0;
CALL get_user_count(@total);
SELECT @total;
```

## 模块七：SQL优化

### 7.1 分析查询

**功能说明**：分析SQL查询的执行计划。

**实战案例**：

```sql
-- 分析查询执行计划
EXPLAIN SELECT * FROM users WHERE age > 25;
```

### 7.2 优化查询

**功能说明**：优化SQL查询语句。

**实战案例**：

```sql
-- 优化前：全表扫描
SELECT * FROM users WHERE age > 25;

-- 优化后：使用索引
CREATE INDEX idx_age ON users(age);
SELECT * FROM users WHERE age > 25;
```

### 7.3 避免全表扫描

**功能说明**：避免查询时扫描整个表。

**实战案例**：

```sql
-- 避免使用SELECT *
SELECT name, age FROM users WHERE age > 25;

-- 避免在WHERE子句中使用函数
-- 优化前
SELECT * FROM users WHERE YEAR(created_at) = 2023;
-- 优化后
SELECT * FROM users WHERE created_at BETWEEN '2023-01-01' AND '2023-12-31';
```

### 7.4 分页查询优化

**功能说明**：优化分页查询性能。

**实战案例**：

```sql
-- 优化前
SELECT * FROM users ORDER BY id LIMIT 100000, 10;

-- 优化后
SELECT * FROM users WHERE id > 100000 ORDER BY id LIMIT 10;
```

## 模块八：锁

### 8.1 行级锁

**功能说明**：只锁定单行数据。

**实战案例**：

```sql
-- 开始事务
START TRANSACTION;

-- 锁定单行数据
SELECT * FROM users WHERE id = 1 FOR UPDATE;

-- 执行更新
UPDATE users SET age = 26 WHERE id = 1;

-- 提交事务
COMMIT;
```

### 8.2 表级锁

**功能说明**：锁定整个表。

**实战案例**：

```sql
-- 锁定表
LOCK TABLES users WRITE;

-- 执行操作
INSERT INTO users (name, age) VALUES ('赵六', 30);

-- 解锁表
UNLOCK TABLES;
```

### 8.3 死锁

**功能说明**：了解死锁产生的原因。

**实战案例**：

```sql
-- 会话1
START TRANSACTION;
SELECT * FROM users WHERE id = 1 FOR UPDATE;

-- 会话2
START TRANSACTION;
SELECT * FROM users WHERE id = 2 FOR UPDATE;

-- 会话1
UPDATE users SET age = 26 WHERE id = 2;

-- 会话2
UPDATE users SET age = 27 WHERE id = 1;

-- 此时会产生死锁
```

## 模块九：备份

### 9.1 逻辑备份

**功能说明**：使用mysqldump工具备份数据。

**实战案例**：

```bash
# 备份整个数据库
mysqldump -u root -p test_db > test_db_backup.sql

# 备份单个表
mysqldump -u root -p test_db users > users_backup.sql
```

### 9.2 恢复数据

**功能说明**：从备份文件恢复数据。

**实战案例**：

```bash
# 恢复数据库
mysql -u root -p test_db < test_db_backup.sql
```

### 9.3 物理备份

**功能说明**：直接复制数据文件。

**实战案例**：

```bash
# 停止MySQL服务
net stop mysql

# 复制数据目录
xcopy "C:\ProgramData\MySQL\MySQL Server 8.0\Data" "D:\backup\mysql_data" /E /I

# 启动MySQL服务
net start mysql
```

## 模块十：主从复制

### 10.1 配置主服务器

**功能说明**：配置MySQL主服务器。

**实战案例**：

```ini
# 修改my.cnf配置文件
[mysqld]
server-id = 1
log-bin = mysql-bin
binlog-format = ROW
```

### 10.2 创建复制用户

**功能说明**：为从服务器创建复制用户。

**实战案例**：

```sql
-- 创建复制用户
CREATE USER 'repl'@'%' IDENTIFIED BY 'password';
GRANT REPLICATION SLAVE ON *.* TO 'repl'@'%';
FLUSH PRIVILEGES;

-- 查看主服务器状态
SHOW MASTER STATUS;
```

### 10.3 配置从服务器

**功能说明**：配置MySQL从服务器。

**实战案例**：

```ini
# 修改my.cnf配置文件
[mysqld]
server-id = 2
relay-log = mysql-relay-bin
```

### 10.4 启动复制

**功能说明**：启动主从复制。

**实战案例**：

```sql
-- 在从服务器上执行
CHANGE MASTER TO
MASTER_HOST = '主服务器IP',
MASTER_USER = 'repl',
MASTER_PASSWORD = 'password',
MASTER_LOG_FILE = 'mysql-bin.000001',
MASTER_LOG_POS = 154;

-- 启动从服务器
START SLAVE;

-- 查看从服务器状态
SHOW SLAVE STATUS\G;
```

## 总结

本手册涵盖了MySQL从基础到进阶的所有核心知识点，每个知识点都提供了可直接运行的实战案例。通过系统学习本手册，你将能够掌握MySQL的基本操作、SQL语法、约束、索引、事务、存储过程、SQL优化、锁、备份和主从复制等知识点，为你的数据库学习和应用打下坚实的基础。

## 附录：常用命令

### 服务管理

```bash
# 启动MySQL服务
net start mysql

# 停止MySQL服务
net stop mysql

# 重启MySQL服务
net restart mysql
```

### 用户管理

```sql
-- 创建用户
CREATE USER 'username'@'localhost' IDENTIFIED BY 'password';

-- 授予权限
GRANT ALL PRIVILEGES ON *.* TO 'username'@'localhost';

-- 撤销权限
REVOKE ALL PRIVILEGES ON *.* FROM 'username'@'localhost';

-- 删除用户
DROP USER 'username'@'localhost';
```

### 性能监控

```sql
-- 查看当前连接
SHOW PROCESSLIST;

-- 查看慢查询日志
SHOW VARIABLES LIKE 'slow_query%';

-- 查看数据库大小
SELECT table_schema AS '数据库', 
ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS '大小(MB)'
FROM information_schema.TABLES
GROUP BY table_schema;
```