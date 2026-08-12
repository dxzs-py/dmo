# Vue.js 响应式系统简要研究：计划与结果

## 一、研究计划

| 步骤 | 内容 | 状态 |
| --- | --- | --- |
| 1 | 规划研究步骤（write_todos） | ✅ 完成 |
| 2 | 搜索并收集 Vue 2/3 响应式系统资料（官方文档 + 技术文章） | ✅ 完成 |
| 3 | 将研究结果与计划写入 /plans/research_plan.md | ✅ 完成 |
| 4 | 用 read_file 读取本文件确认内容 | ⏳ 进行中 |

**研究问题**：Vue.js 的响应式系统如何工作？Vue 2 与 Vue 3 的实现有何差异？

---

## 二、研究结果摘要

### 1. 响应式（Reactivity）的核心概念

响应性是一种声明式处理变化的编程范式。在 Vue 中：
- **副作用（effect）**：会更改程序状态的函数，例如 `update()` 或组件的渲染函数。
- **依赖（dependency）**：被副作用读取的数据（如 `A0`、`A1`）。
- **订阅者（subscriber）**：依赖变化时需要重新执行的副作用。

Vue 无法直接追踪普通局部变量的读写，因此通过**劫持对象属性的访问**实现追踪，核心机制可概括为三步：
1. **读取时追踪（track / 依赖收集）**：属性被读取时，将当前正在运行的副作用记为订阅者。
2. **写入时触发（trigger / 派发更新）**：属性被修改时，通知所有订阅者重新执行。
3. **副作用管理（effect）**：包装需响应式执行的函数，运行期间标记自身为"当前活跃副作用"（activeEffect），供 track 定位。

### 2. Vue 2：基于 `Object.defineProperty`

- 在初始化 data 时**递归遍历对象所有属性**，用 `Object.defineProperty` 将每个属性转为 getter/setter（数据劫持）。
- getter 中执行**依赖收集**，setter 中执行**派发更新**。
- 每个属性对应一个 **Dep（订阅器）**，管理一组 **Watcher（观察者）**；每个组件实例对应一个渲染 Watcher，在渲染过程中把访问过的属性记录为依赖。
- 数据变化时，Dep 调用 `notify()` 通知所有 Watcher 重新渲染。

**Vue 2 的局限性**：
- 无法检测对象属性的**新增与删除**（需 `Vue.set` / `Vue.delete`）。
- 数组需要**重写 7 个变异方法**（push/pop/splice 等），且无法检测"按索引修改元素"和"修改 length"。
- 初始化时递归遍历整个对象，深嵌套/大对象性能开销大。
- getter/setter 是 ES5 特性且无法 shim，因此不支持 IE8 及以下。

### 3. Vue 3：基于 `Proxy` + `Reflect`

- 用 `Proxy` **代理整个对象**，拦截 get/set/has/deleteProperty 等操作；`ref` 仍使用 getter/setter。
- 依赖存储于全局 **WeakMap\<target, Map\<key, Set\<effect\>\>\>** 结构中。
- 核心 API：`reactive()`、`ref()`、`effect`（内部 `ReactiveEffect`）、`track()`、`trigger()`、`computed()`、`watchEffect()`。
- `computed` 内部使用响应式副作用管理失效与重新计算；每个组件实例创建一个响应式副作用来渲染和更新 DOM。

**Vue 3 的优势**：
- 可直接检测属性的新增与删除。
- 数组操作天然被拦截，无需重写方法。
- **惰性响应式**：仅在访问属性时才收集依赖，减少初始化开销、内存占用更小。
- 依赖追踪粒度更细，支持 computed 的缓存与懒执行，减少不必要的更新。

### 4. Vue 2 与 Vue 3 对比一览

| 维度 | Vue 2 | Vue 3 |
| --- | --- | --- |
| 劫持方式 | Object.defineProperty（属性级） | Proxy + Reflect（对象级） |
| 新增/删除属性 | 不支持（需 Vue.set/delete） | 自动支持 |
| 数组响应式 | 重写 7 个变异方法，有边界缺陷 | 原生拦截，无需特殊处理 |
| 初始化开销 | 递归遍历全部属性，开销大 | 惰性代理，按需收集 |
| 依赖管理 | Dep + Watcher | WeakMap → Map → Set + effect |
| 更新粒度 | 组件级（较粗） | 更细粒度追踪 |
| 浏览器兼容 | IE8 不支持（ES5 无法 shim） | 需支持 Proxy 的环境 |

### 5. 异步更新队列与 nextTick

- Vue 更新 DOM 是**异步**的：同一事件循环内的多次数据变更被缓冲进队列，同一 watcher 只入队一次（去重），在下一个 tick 统一刷新，避免重复计算和 DOM 操作。
- Vue 2 内部按优先级尝试 `Promise.then` → `MutationObserver` → `setImmediate` → `setTimeout(fn, 0)`。
- 可通过 `Vue.nextTick()` / `vm.$nextTick()`（Vue 3 中为 `nextTick()`，返回 Promise）在 DOM 更新完成后执行回调。

### 6. 调试与扩展

- 调试钩子：`onRenderTracked` / `onRenderTriggered`（组件渲染）、computed 与 watch 的 `onTrack` / `onTrigger`（仅开发模式）。
- 与外部状态集成：用 `shallowRef` 包裹外部状态（如 Immer、XState、RxJS）。
- 与信号（signal）的联系：Solid / Angular / Preact 的 signal 本质与 Vue 的 `ref` 相同（读时追踪、写时触发）。

---

## 三、结论

Vue 响应式系统的本质是**"数据劫持 + 依赖收集 + 派发更新"**的发布-订阅模式：读取时记录依赖，写入时通知更新。Vue 2 受限于 `Object.defineProperty` 的属性级劫持能力，存在新增属性、数组、初始化性能等缺陷；Vue 3 用 `Proxy` 从"属性级"跃迁到"对象级"代理，天然覆盖新增/删除属性与数组操作，并通过惰性代理与更细粒度的依赖追踪显著提升性能。理解这一机制有助于规避响应式陷阱（如解构丢失响应性）并正确使用 `ref`/`reactive`/`computed`/`watchEffect` 等 API。

---

## 四、参考文献

1. Vue.js 官方文档（Vue 3）：深入响应式系统 — https://cn.vuejs.org/guide/extras/reactivity-in-depth.html
2. Vue.js 官方文档（Vue 2）：深入响应式原理 — https://v2.cn.vuejs.org/v2/guide/reactivity.html
3. SitePoint：Understanding the New Reactivity System in Vue 3 — https://www.sitepoint.com/vue-3-reactivity-system
4. 博客园《Vue 响应式原理、2和3的区别》— https://www.cnblogs.com/crispyChicken/p/18719542
5. GitHub ljianshu/Blog《深入浅出Vue响应式原理》— https://github.com/ljianshu/Blog/issues/70
6. Medium：How Vue 3's Reactivity Works Under the Hood（With Proxies Explained Simply）
