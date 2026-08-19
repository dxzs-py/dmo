# cq‑11 useApiTask：选择一次性接入3个视图（DeepResearchView+RagView+WorkflowView，合计31处）
> 前提：放弃“小页面试点踩坑”，直接中等规模落地。
> 风险本质：**composable一旦设计有缺陷，31处调用点全部要改，回退成本高；同时三个都是核心业务页面，容易引入loading状态错乱、漏弹窗、吞异常、组件卸载内存泄漏。**

下面分：前置约束、composable设计必须到位、迁移改造规则、编码规范、测试校验、回退方案、红线，全部可以直接写进spec。

## ⚠️前置判断：满足下面全部条件，才适合一次性接入31处
不满足就退回A方案（只接DeepResearchView试点）
1. `useApiTask` 先写单元测试，**先写composable，先测逻辑，再改业务组件，不要边写composable边改页面**。
2. 梳理完3个页面全部31处原始catch逻辑，整理一张对照表：每个调用点：loading是否开启、是否弹ElMessage、错误是否需要自定义处理、是否可取消、是否SSE、组件卸载是否要中断请求。
> ❌禁止：一边看template一边改，想到哪改到哪。
3. 确认：这31处没有大量高度特殊的业务异常分支（大量`if(error.msg.includes('xxx'))`做特殊业务逻辑）；如果存在很多特殊错误分支，一次性接入风险会陡增。

---

## 一、useApiTask composable 接口设计要点（必须全部覆盖，否则迁移必翻车）
```ts
// 理想接口参考
const { run, loading, error } = useApiTask<T>(taskFn, options)
```
options 必须支持这些开关，**不能硬编码loading、弹窗行为**
```ts
interface UseApiTaskOptions {
  /** 是否开启loading状态 */
  loading?: boolean
  /** 是否自动弹出 ElMessage 错误提示 */
  showErrorToast?: boolean
  /** 成功是否弹toast，大部分场景关闭 */
  showSuccessToast?: boolean
  /** 组件unmount时，是否自动abort任务/取消请求（非常关键，防内存泄漏、残留在后台的toast） */
  autoAbortOnUnmount?: boolean
  /** 自定义错误处理器：业务需要特殊分支时接管，绕过默认toast */
  onError?: (err: unknown) => void | Promise<void>
  /** 成功回调 */
  onSuccess?: (data: T) => void
}
```
### 必须内部实现的核心能力
1. **重复调用防护**：上一个未完成任务，新run触发时可选择abort旧任务，避免loading闪烁、多次弹窗。
2. **unmount自动清理**：组件销毁，pending请求终止，清除loading，阻止后续toast弹窗（经典bug：组件关掉了，还弹出错误提示）。
3. 区分三种错误来源：网络异常、后端业务code错误、手动abort取消；**取消请求不要弹出错误toast**（用户点取消，不应该报错）。
4. error状态对外暴露，组件依然可以访问原始error对象，做少量特殊业务逻辑。
5. 返回的`loading`是ref，可直接绑定模板。

> 坑：很多封装只做简单loading+toast，忽略**请求取消、unmount清理、取消不报错**，迁移完会出现一堆偶现bug。

## 二、迁移改造硬性规则（31处迁移时严格遵守）
1. **先做盘点表**
把31处逐个列出来：原始代码行为：loading开关、是否弹消息、有没有自定义onError、是否可取消、是否SSE。
迁移后options必须和旧行为对齐，**禁止悄悄改变业务表现**。
> 最大的坑：迁移后，有的错误不再弹窗，或者多弹出toast，用户感知异常。

2. 禁止一刀切全部打开showErrorToast
有些调用点原有逻辑是静默处理错误，不弹消息，options要设置`showErrorToast: false`，保留原有行为。

3. 原有手写catch里的特殊业务逻辑，全部迁移到`onError`回调，不要丢掉。
> 如果某个catch里面有5‑10行复杂业务判断，不要硬塞composable内部，交给onError。

4. SSE / fetch流式调用要单独处理
useApiTask适合普通Promise接口；SSE流式迭代逻辑不要强行塞进去。
> SSE部分维持原有逻辑，不要强行套useApiTask，不要为了统一而硬封装。

5. 不要删除旧的import和旧样板代码，保留做对照；提交时diff便于评审。

6. 模板中`v‑loading="xxx"`全部替换为composable返回的`loading`ref，不要混用旧loading变量。**一个组件内不要同时存在多套loading状态源**，极易状态打架。

## 三、分层测试策略（一次性大规模迁移必须做）
### 1）composable单元测试（vitest，优先写）
测试用例清单：
- run正常执行，loading正确true→false
- run抛出异常，error被赋值，showErrorToast=true时触发message
- autoAbortOnUnmount：组件unmount，pending任务终止，不会弹出toast
- 连续多次run，旧任务abort行为
- abort手动取消任务，不弹出错误toast
- showErrorToast=false，出错不弹消息

### 2）组件层面测试
- DeepResearchView / RagView / WorkflowView vitest覆盖主要调用路径
- 重点测试：组件中途销毁（切换路由），不残留弹窗、不残留pending请求。

### 3）人工回归（这个等一会测试吧，除非你D:\programming\langchain\langchain_xm\启动.md按照这个启动所有服务，通过mcp控制浏览器测试）

逐个页面验证：
1. 正常流程：loading展示消失，成功表现不变
2. 业务报错：后端返回错误，弹窗和迁移前一模一样
3. 网络异常：断网，弹窗表现和迁移前一致
4. 请求中途切换页面：**不会弹出幽灵toast**
5. 快速重复点击：loading不会错乱，不会连续弹多条错误
6. 用户手动取消任务：不弹出错误提示

## 四、风险 & 回退方案（非常关键，一次性改31处）
1. **按页面拆分PR，不要一个巨大PR塞3个页面**
> 推荐：
> PR1：实现useApiTask + composable单元测试（不含业务组件改动）
> PR2：迁移 DeepResearchView
> PR3：迁移 RagView + WorkflowView

好处：评审粒度小；出问题可以单页面回退，而不是全部回滚。
> ❌禁止一个几千行大PR一次性提交全部31处改动，review几乎不可能看全。

2. 回退预案：每个页面的迁移提交可以 revert；composable本身可以保持，页面回退回手写catch。

3. 监控注意：上线后关注前端错误埋点，看有没有被吞掉的异常。

## 五、什么信号说明不能继续，要及时止损退回试点模式
开发过程中出现下面任意一种，停止继续迁移剩下页面，退回到只保留DeepResearchView试点：
1. 发现大量调用点有高度差异化的异常处理，options配置变得极其复杂，每个调用点都要写一堆特殊参数；
2. 发现useApiTask要不断打补丁加各种特殊flag，API越来越臃肿；
3. 改造中反复出现：很难对齐原来的弹窗/loading行为。

> 信号含义：抽象封装不匹配业务现实，强行全部接入会积累技术债务。

## 六、交付产出（spec中写明）
1. 31处调用点盘点对照表
2. useApiTask API文档，每个options参数说明
3. vitest测试覆盖清单
4. 改造后样板示例
5. 风险清单+回退策略
6. 上线验证checklist
