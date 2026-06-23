# 无用Agent系列：我用 PydanticAI 做了一个快递员 Agent，它能自己查规则、自己做判断

我先把结论放前面。

这个项目**一眼就不像正经产品**。不是派单系统，不是路径规划算法，也不是写死状态流的业务 demo。更像是我拿一个大模型，硬塞进"快递员"这个框架里，让它自己读规则、自己判断、自己决定要不要调工具，然后把每一次决策都记下来。

听起来有点无用。

但我越做越觉得，**这才是它适合当第一篇的原因。**

因为"职业 Agent"真正值得研究的，不是 AI 能不能替代一个人。而是一个更工程化的问题：**当一个职业被拆成规则、工具、记忆、事件和决策之后，我们能不能把它做成一个可运行、可观察、可复盘的系统？**

今天这篇，我把快递员这个最小闭环跑给你看。

![Starship 自动配送机器人行驶在校园道路上（示意图）](https://images.unsplash.com/photo-1717538855595-c2025a0755bb?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3NjYxNTR8MHwxfHNlYXJjaHwzfHxjb3VyaWVyJTIwZGVsaXZlcnklMjByb2JvdCUyMGNvbmNlcHR8ZW58MHwwfHx8MTc4MTk0NzM5NHww&ixlib=rb-4.1.0&q=80&w=1080)

---

## 01 我给 AI 的第一句话

这个项目的起点很简单。

我给 AI 的大概指令是：帮我搜一下，用 Python 做一个快递员 Agent 应该怎么规划。它能不能长期运行？能不能有自己的业务规则？

AI 一开始给出的方案，比较像传统系统设计：状态机、任务流、派单、取件、送达、异常处理。

这些东西有用，但我很快意识到，这不是我想要的。

于是我补了一句关键的话：

> 我们要使用大模型来实现 Agent，并不是传统的固定处理流。它可以自行判断使用工具，并且拥有自己的业务工作规则，处理各种情况的手册或规则。

因为很多所谓 Agent 项目，本质上还是流程引擎。它只是把"下一步做什么"包装成了模型调用，但真正的决策仍然写死在代码里。

我想要的不是这个。

我想要的是：快递员 Agent 收到一个事件——比如"客户不接电话""手机电量只剩 12%""小区门禁打不开"——它可以先去查自己的配送手册，再决定是联系客户、通知调度、重新规划路线，还是把订单标记为异常。

它不是乱想。**它要在规则里行动。**

---

## 02 方向定了，架构就清楚了

调研之后，这个 demo 不适合一上来就做很重的系统。LangGraph 适合复杂状态图，OpenAI Agents SDK 有不错的 tracing，CrewAI 偏角色协作。但这个阶段，我更想先把"一个职业 Agent 的最小闭环"跑起来。

所以选了一个更轻的组合：FastAPI 对外提供接口，PydanticAI 负责大模型调用和结构化输出，SQLite 持久化所有决策记录，再加一个 Heartbeat 机制让 Agent 定期重新判断状态。

这套设计里，我最在意三件事。

**第一，它能不能长期运行。**

不是让一次模型调用跑几个小时。而是把任务拆成事件，状态写进数据库，Heartbeat 每隔一段时间把当前情况推给 Agent，让它重新判断。这样系统就能一直活着，而不是跑一次就结束。

**第二，它是不是黑盒。**

我最怕 Agent 项目做到最后，只有一句"已处理"。所以我要求系统保存每一次决策的完整记录：做了什么判断、依据了哪条规则、调用了什么工具、工具返回了什么。

但有一个边界：我不暴露模型的原始思维链。系统返回的是结构化的决策摘要——比如"电量低于阈值，通知调度，前往充电点，10 分钟后复查"，同时标注它依据了手册里的低电量规则。

**这已经足够让团队复盘了。** 你不需要知道模型怎么一步步推理，你需要知道它为什么做了这个选择、依据了什么规则、下一步怎么安排。出了问题，才能追，才能改，才能补规则。

**第三，它能不能换模型。**

我不想写死某一家模型。这个项目默认走 OpenAI 兼容协议，你可以换成任何兼容的模型网关。

![数据分析仪表盘界面（示意图）](https://images.unsplash.com/photo-1551288049-bebda4e38f71?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3NjYxNTR8MHwxfHNlYXJjaHwxfHxldmVudCUyMHRpbWVsaW5lJTIwZGFzaGJvYXJkJTIwYW5hbHl0aWNzfGVufDB8MHx8fDE3ODE5NDczOTR8MA&ixlib=rb-4.1.0&q=80&w=1080)

---

## 03 真的跑了一遍

空讲设计没意思，直接说我这次实际跑出来的结果。

我起了服务，创建了一个订单——系统不只是把订单写进数据库，**还立刻触发了第一次 Agent 决策。** 模型看了一眼当前状态：电量 90%，天气正常，客户没有异常，于是给出判断：先去取件。

关键是，这个判断不是拍脑袋。系统标注了它依据的是配送手册里的哪条规则。你以后做复盘，不会只看到一句"先去取件"，而是能看到背后的业务依据。

接下来我模拟了一个很真实的场景：**客户回消息了，说自己 10 分钟后到家。**

系统不会写死"那我就等等"。它把客户回复、当前位置、电量水平一起交给 Agent 去判断。结果模型的判断也很合理：客户给了明确时间窗口，电量也没问题，天气正常，所以继续去取件，同时尽量对齐客户的时间窗口。

最后我查了一下时间线——这是整个系统最核心的地方。

时间线把事件、Agent 判断、工具调用全部串在一起。你能看到一个订单不是只有最终状态，而是有一整条可回放的过程：客户说了什么、Agent 做了什么判断、它调用了哪些工具、工具返回了什么结果。

**很多 Agent 项目最怕的不是"模型答错了"，而是"不知道它为什么这么答"。** 系统没有过程，只有结果，后面无论测试、修规则还是做复盘，都是在黑盒里猜。

快递员这种职业，恰恰最怕黑盒。一个订单延误了，你要能说清楚：是规则没覆盖，还是模型误判，还是工具调用失败。否则它就只是一个能聊天的 demo，不是职业 Agent。

![配送机器人在城市街道行驶（示意图）](https://images.pexels.com/photos/8566562/pexels-photo-8566562.jpeg?auto=compress&cs=tinysrgb&h=650&w=940)

---

## 04 代码思路

这个项目代码量不大，但我故意把它按"一个可演进系统"去组织，而不是按"一个能跑的脚本"去写。

最核心的地方，其实只有几层：**数据骨架、API、调度中枢、Agent、规则手册、持久化和时间线。**

我先从最不起眼、但最重要的 `schemas.py` 说起。

它定义了三样东西：订单有哪些状态、事件有哪些类型、Agent 最后必须输出哪些字段。比如订单不是随便一个字符串，而是 `assigned`（已派单）、`en_route_pickup`（前往取件）、`in_transit`（运输中）、`delivered`（已送达）、`delayed`（延误）、`escalated`（已上报）这些提前定义好的状态。事件也不是自由文本，而是 `heartbeat`（心跳）、`customer_reply`（客户回复）、`pickup_completed`（取件完成）、`delivery_completed`（送达完成）、`exception`（异常）这些固定类型。

为什么要先做这个？

因为一旦数据结构稳了，后面的规则、测试、回放、复盘才有地方挂。没有稳定结构的 Agent demo，做到后面基本都会变成一团浆糊。

接着是 `api/routes.py`。它的职责很单纯：**接收外部请求，然后把问题交给内部系统。**

比如创建订单，它不会自己判断下一步，而是先写入订单，再立刻触发第一次 Agent 决策。推送事件也是一样，`customer_reply`、`heartbeat`、`exception` 进来后，它只负责转发，不负责拍脑袋。它更像一个"前台"，真正的判断都在后面发生。

再往下，是整个系统最核心的一层：`orchestrator.py`。

你可以把它理解成**调度中枢**。它做的事情大概是这样：

1. 事件进来后，先把当前订单状态和新事件合并
2. 记录这次事件
3. 组装上下文，交给 Agent 判断
4. 拿到 Agent 返回的结构化决策
5. 把决策写回订单
6. 记录这次 Agent run
7. 返回订单、决策、run 和工具调用

核心方法结构：

```python
class CourierOrchestrator:
    def process_event(self, order_id, event) -> ProcessResult:
        # 主入口：接收事件，触发完整决策流程
        order = self.repository.get_order(order_id)
        updated_order = self._merge_event(order, event)  # 合并事件状态
        self.repository.append_event(order_id, event)    # 记录事件
        decision = self._run_agent(deps, event)          # 运行 Agent 获取决策
        finalized_order = self._apply_decision(updated_order, event, decision)  # 应用决策
        return ProcessResult(order=finalized_order, decision=decision, ...)

    def _run_agent(self, deps, event) -> CourierDecision: ...
    def _merge_event(self, order, event) -> OrderRecord: ...
    def _apply_decision(self, order, event, decision) -> OrderRecord: ...
    def _build_prompt(self, order, event) -> str: ...
```

这里最关键的一点是：**Agent 的输出不是自由文本，而是结构化决策。**

它必须回答：这次决策是什么、原因是什么、依据了哪些规则、下一步要做什么、多久之后再检查一次。这样系统拿到的才不是一句"已处理"，而是一个可记录、可审计、可复盘的结果。

然后是 `agent.py`。

它负责搭出这个快递员 Agent 的“脑子”。里面会定义角色说明、工具集、上下文注入方式。系统每次调用 Agent 时，都会把当前订单、当前事件、手册摘要一起塞进去，让它在有约束的情况下做判断。

核心方法结构：

```python
def build_courier_agent(model_name, *, provider, api_key, base_url) -> Agent:
    # 构建 Agent 实例，注入系统提示词和依赖类型
    agent = Agent(model, deps_type=CourierDeps, output_type=CourierDecision, instructions=COURIER_SYSTEM_PROMPT)
    
    # 动态注入上下文：当前时间、订单状态、电量、位置、手册摘要
    @agent.instructions
    async def add_operating_context(ctx): ...
    
    # Agent 可调用的工具集
    @agent.tool
    async def search_delivery_manual(ctx, query): ...  # 搜索配送手册
    @agent.tool
    async def call_customer(ctx, message): ...         # 联系客户
    @agent.tool
    async def notify_dispatch(ctx, reason): ...        # 通知调度
    @agent.tool
    async def plan_route(ctx, destination): ...        # 规划路线
    @agent.tool
    async def update_memory(ctx, key, value): ...      # 更新记忆
```

这里有一个细节值得注意：Agent 不是自己编规则，它能调用一个工具叫 `search_delivery_manual`。也就是说，它是“查规则”来支持判断，不是凭空拍脑袋。今天这个手册很小，但这个位置已经留出来了。后面你要接知识库、接 RAG、接更复杂的业务规则，入口就在这个地方。

`manual.py` 现在就是那份小手册，内容不多，只有低电量、门禁、客户失联、天气风险这几类。但它已经能说明一件事：**规则是可以独立维护的。**

不是把规则塞进 prompt 里就完事，而是把规则抽出来，做成可查询、可替换的模块。这样后面才好扩展。

再往下是 `repository.py`。

它负责把所有过程留下来：订单、事件、Agent run、工具调用、心跳、记忆，全都落库。每次你查 timeline，看到的不是“最终状态”，而是一条完整过程：发生了什么、Agent 做了什么判断、调用了哪些工具、工具返回了什么结果。

核心方法结构：

```python
class CourierRepository:
    def create_order(self, request) -> OrderRecord: ...        # 创建订单
    def get_order(self, order_id) -> OrderRecord: ...           # 获取订单
    def record_tool_call(self, **kwargs) -> None: ...           # 记录工具调用
    def record_heartbeat(self, order_id, status, battery_level, note) -> None: ...  # 记录心跳
    def record_memory(self, order_id, memory_key, memory_json) -> None: ...         # 记录记忆
```

这层的意义不是“用 SQLite 存点数据”，而是让系统具备**回放能力**。

很多 Agent demo 做到最后，最怕的不是答错，而是不知道为什么答错。没有 timeline，就只能猜。有了 timeline，才能追，才能改规则，才能做测试断言。

最后还有 `heartbeat.py`。

它不是一个业务逻辑模块，而是一个外部节拍器。它的作用很简单：定时把当前订单状态重新推给 Agent，让系统有机会重新判断。这样系统就不会只在"有人发事件"时才动，而是能保持一个持续运行的感觉。

这也很符合现实：快递员不是只在收到客户消息时思考，他也会在行驶途中、在电量变化、在时间推移时不断重新判断。

核心方法结构：

```python
class HeartbeatService:
    interval_seconds: int   # 心跳间隔（秒）
    running: bool = False   # 运行状态

    async def run_once(self) -> None:
        # 执行一次心跳：遍历所有活跃订单，为每个订单生成心跳事件并触发 Agent 决策
        active_orders = self.repository.list_active_orders()
        for order in active_orders:
            event = DeliveryEvent(event_type="heartbeat", message="scheduled heartbeat", ...)
            await self.orchestrator.process_event_async(order.order_id, event)

    async def run_forever(self) -> None: ...  # 持续运行，按间隔重复执行 run_once
```

所以如果让我用一句话总结这个项目的代码思路，我会说：

**它不是先写一个能跑的 demo，再补结构；而是先把数据、规则、决策、回放这四件事立住，再让 Agent 在这个骨架里工作。**

如果你喜欢这个系列，关注《爬虫之心》公众号，让我们一起开发更多无用Agent。
