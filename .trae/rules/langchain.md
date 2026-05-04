- **环境**  
  后端Python：必须先激活 conda环境 `conda activate langchain_xm`，Django最新稳定版；智能体模块使用LangChain。  
  前端：Node v20.19.5，npm管理依赖；Vue 3 + Composition API + Element Plus + Tailwind CSS。

- **后端架构**  
  Django MTV，功能模块独立app；Django REST Framework构建API，统一前缀 `/api/`，RESTful设计（GET/POST/PUT/DELETE），JSON格式交互。  
  智能体功能为核心，集成LangChain（配置智能体、模型、工具），与前端API交互实现对话。  
  中间件合理使用，CORS配置开放必要来源。配置拆分 `dev.py`（开发可改）与 `prod.py`（生产不动）。

- **前端架构**  
  Vue 3 + Composition API，组件拆分复用；Pinia全局状态，Vue Router路由；Axios请求封装，统一拦截错误处理；开发环境配置代理解决跨域。适配多设备。

- **数据与交互**  
  请求/响应JSON，统一响应结构（code、message、data），规范错误码。  
  Axios实例化，拦截器注入token、处理异常；后端Serializer校验，模型关系合理设计。

- **代码风格**  
  Python：PEP 8，类型注解，docstring；Vue：官方风格指南。变量命名有语义，关键逻辑注释。提交信息清晰。

- **安全与性能**  
  注意鉴权、SQL注入、XSS防护，查询优化、缓存策略按需，可扩展可维护。

- **参考资源**  
- 后端参考：[Django REST Framework](https://www.django-rest-framework.org/)
- 智能体参考：[Langchain](https://python.langchain.com/docs/)


