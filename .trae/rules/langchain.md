# 项目规则

## 1. 开发环境配置

### 后端环境
- Python环境：使用conda创建并激活名为`Django_xm`的环境
  ```bash
  conda activate Django_xm
  ```
- Django版本：建议使用最新稳定版
- 依赖管理：使用pip和requirements.txt管理依赖

### 前端环境
- NVM版本：1.2.2
- Node.js版本：v20.19.5
- npm版本：10.8.2
- 包管理：使用npm管理前端依赖

### 环境验证
在进行项目开发前，应验证所有环境版本是否符合要求：
```bash
# 验证后端环境
python --version
conda info --envs

# 验证前端环境
nvm --version
node --version
npm --version
```

## 2. 项目架构最佳实践

### 后端架构
- 采用Django的MTV架构（Model-Template-View）
- 应用模块化：将功能划分为独立的app
- RESTful API设计：使用Django REST Framework构建API
- 中间件使用：合理使用中间件处理跨域、认证等
- 数据库设计：遵循数据库设计规范，使用模型关系

### 前端架构
- Vue 3 + Composition API
- 组件化开发：将UI拆分为可复用组件
- 状态管理：使用Pinia管理全局状态
- 路由管理：使用Vue Router管理页面路由
- 响应式设计：适配不同设备尺寸

## 3. 前后端交互规范

### API设计
- 统一API前缀：如`/api/v1/`
- HTTP方法使用：GET（获取）、POST（创建）、PUT（更新）、DELETE（删除）
- 响应格式统一：
  ```json
  {
    "code": 200,
    "message": "success",
    "data": {...}
  }
  ```
- 错误处理：统一错误码和错误信息

### 数据传输
- 使用JSON格式传输数据
- 前端使用Axios进行HTTP请求
- 处理请求超时和网络错误
- 实现请求拦截器和响应拦截器

### 跨域处理
- 后端配置CORS（Cross-Origin Resource Sharing）
- 前端配置代理（在开发环境）

## 4. 代码规范

### Python代码规范
- 遵循PEP 8规范
- 使用类型注解
- 函数和类的文档字符串
- 模块化设计，避免重复代码

### Vue代码规范
- 遵循Vue官方风格指南
- 组件命名规范：使用PascalCase或kebab-case
- 代码缩进：2个空格
- 合理使用注释

### 通用规范
- 变量命名：使用有意义的名称
- 代码注释：关键逻辑必须有注释
- 代码格式化：使用prettier（前端）和black（后端）
- 版本控制：遵循Git规范，提交信息清晰

## 5. 项目资料管理

### 文档
- README.md：项目概述、安装说明、使用方法
- API文档：使用Swagger或DRF自动生成
- 架构文档：系统架构、模块关系

### 测试
- 后端：使用Django测试框架
- 前端：使用Vue Test Utils和Jest
- 集成测试：测试前后端交互

### 部署
- 后端：使用Gunicorn + Nginx
- 前端：构建后部署到静态文件服务器
- 环境配置：区分开发、测试、生产环境

## 6. 参考优秀企业项目

### 后端参考
- Django Rest Framework官方示例
- 大型企业级Django项目结构
- GitHub上的优秀Django开源项目

### 前端参考
- Vue官方示例和最佳实践
- 大型Vue 3项目结构
- Element Plus官方示例

## 7. 开发流程

1. 环境搭建：配置后端和前端环境
2. 需求分析：明确功能需求和技术实现
3. 架构设计：设计系统架构和模块划分
4. 代码实现：按照规范编写代码
5. 测试：进行单元测试和集成测试
6. 部署：部署到测试和生产环境
7. 维护：定期更新和优化

## 8. 注意事项

- 安全性：注意SQL注入、XSS攻击等安全问题
- 性能：优化数据库查询、前端渲染性能
- 可扩展性：设计可扩展的架构
- 可维护性：代码结构清晰，易于维护
- 文档：及时更新文档，保持代码和文档同步