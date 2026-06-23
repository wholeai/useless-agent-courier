# 无用Agent 02：给快递员 Agent 装上真手——从桩工具到真打钉钉、真查路线

上一篇我把这个快递员 Agent 的最小闭环立住了。订单、事件、Agent 决策、工具调用、时间线，全在跑。

但有一个地方我心里一直清楚——

> 那 3 个工具，全是桩。

`call_customer` 一律返回 "No response yet."；`notify_dispatch` 返回 "Dispatch notified: …"；`plan_route` 直接吐字符串 "Route planned from …"。它们只做了"记录"，**没做"真做"**。

这种 demo 可以跑得很漂亮，但上不了生产。

所以这篇只干一件事：**给这个 Agent 装上真手。**

让它真打钉钉，真查路线，真给客户推消息。再加一个**跨订单记忆**——一个快递员，今天见过老客户张三，下次他再下单，Agent 应该记得他。

听起来很自然。但你看完代码会发现，这一步踩的坑比想象中多。

![自动化机器人在仓库中扫描包裹（示意图）](https://images.pexels.com/photos/8294614/pexels-photo-8294614.jpeg?auto=compress&cs=tinysrgb&h=650&w=940)

---

## 01 桩工具的边界

先把丑话说在前面：桩工具不是不能用。**它有一类很合适**的场景。

* 跑 demo 给别人看 Agent 怎么工作
* 跑单元测试，不希望依赖外网
* 跑端到端冒烟，只看流程对不对

但一旦到了这些场景，桩工具就装不下去了：

* 你想真的把异常**通知**到群里
* 你想真的**避开限行**或**绕开施工**
* 你想知道 Agent 触达用户后，**用户**做了什么
* 你想知道一个客户上次**提过什么偏好**

这些东西的共同点是：**Agent 的判断，必须有一个真实世界的反馈。**

没有这个反馈，Agent 就是在沙盒里自言自语。

那为什么我上一版还留着桩？

因为骨架要立得起来。如果一上来就接飞书、接地图、接电话，代码里会塞满 OAuth、key、配额、重试。第一篇我想先让"职业 Agent 的最小闭环"立住——接外网的事，挪到这一篇。

> **职业 Agent 的判断，必须有一个真实世界的反馈。**
> 没有这个反馈，Agent 就是在沙盒里自言自语。

这一篇的判断标准也很简单：**这个 Agent 是不是真做了事**。

---

## 02 让它真打钉钉、真查路线

我们这次的目标不是接一堆付费服务。**是把"装手"这件事的工程骨架立住。**

所以我只接 3 类工具，对应 3 个最朴素的场景：

| 工具 | 真接入 | 没有配置时 |
| --- | --- | --- |
| `notify_dispatch` | 钉钉机器人 webhook | log-only 模式 |
| `plan_route` | OSRM 公网路由（无需 key） | log-only 模式（如果端点不是经纬度） |
| `call_customer` | 通用 webhook 推送（Bark / ntfy / 企业微信 incoming 都行） | log-only 模式 |

**为什么选这三类？**

钉钉：可视化效果最直接——消息真的出现在群里，证明 Agent "动了"。
OSRM：免费、无需 key、且返回结构化数据（距离、时长），非常适合 Agent 拿来做后续判断。
通用 webhook：Bark 这种 iOS 推送、或者 ntfy 这种匿名推送，都接受 JSON POST，几乎是零成本就能跑起来。

这三类不需要任何云账号就能跑通，**门槛最低，价值最高。**

### 一个核心设计原则：log-only 是默认

我没用 `MOCK_MODE=true` 这种显式开关。**默认就是 log-only**。你不配任何环境变量，Agent 就在 log-only 模式跑。

这是 Ponytail 的一条小哲学：**让"什么都不配也能跑"成为默认行为**。

> Ponytail: `ToolBackends.from_settings()` 在拿不到 webhook URL 时直接给桩实现，业务代码感觉不到差异。

代码上长这样：

```python
# courier_agent_demo/integrations.py
class ToolBackends:
    dispatch: DispatchBackend
    routing: RoutingBackend
    customer: CustomerContactBackend

    @classmethod
    def log_only(cls) -> "ToolBackends":
        return cls(
            dispatch=DispatchBackend(webhook_url=None),
            routing=RoutingBackend(),
            customer=CustomerContactBackend(webhook_url=None),
        )

    @classmethod
    def from_settings(
        cls,
        *,
        dingtalk_webhook_url: str | None,
        customer_contact_webhook_url: str | None,
        routing_base_url: str | None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> "ToolBackends":
        return cls(
            dispatch=DispatchBackend(dingtalk_webhook_url, timeout=timeout_seconds),
            routing=RoutingBackend(
                base_url=routing_base_url or "https://router.project-osrm.org",
                timeout=timeout_seconds,
            ),
            customer=CustomerContactBackend(customer_contact_webhook_url, timeout=timeout_seconds),
        )
```

这种"零配置即跑"的代价是——**任何误用都不会被显式警告**。你必须看 tool_call 的 audit log 才能知道是 log-only 还是真发了。后续会考虑加一个 startup 横幅（"log-only mode active"），但现在先这样。

### 怎么"装"上去的：把 backends 注入 orchestrator

这里有个关键设计：**backends 不是在工具内部创建，而是从 orchestrator 透传到 agent 工具**。

如果每个工具自己去读 `os.environ`、自己 new 一个 client，会有三个问题：

1. **测试不友好**——单测里想 mock 工具，必须 monkeypatch 环境变量
2. **配置分散**——哪里配 webhook、哪里读 timeout，散在多个工具里
3. **同进程跑多份实例会很乱**——比如后面要做 A/B 测试，工具要同时连两套服务

所以我让 orchestrator 构造时拿一份 `ToolBackends`，然后塞进 `CourierDeps`，agent 工具通过 `ctx.deps.backends.dispatch.notify(reason)` 拿。

```python
# courier_agent_demo/orchestrator.py
self.backends = backends or ToolBackends.log_only()
# ...
deps = CourierDeps(
    repository=self.repository,
    order=updated_order,
    event=event,
    run_id=run_id,
    model_name=self.model_name,
    low_battery_threshold=self.low_battery_threshold,
    backends=self.backends,  # 透传到工具
)
```

agent 工具这一侧就清爽了：

```python
# courier_agent_demo/agent.py
@agent.tool
async def notify_dispatch(ctx: RunContext[CourierDeps], reason: str) -> str:
    result = ctx.deps.backends.dispatch.notify(reason)
    ctx.deps.repository.record_tool_call(
        run_id=ctx.deps.run_id,
        order_id=ctx.deps.order.order_id,
        tool_name="notify_dispatch",
        input_json={"reason": reason},
        output_json=result.to_record(),
        status="success" if result.status == "ok" else "error",
    )
    return result.as_tool_string()
```

注意最后这一行 `result.to_record()`。**任何工具的输出，都不是一个字符串，而是一个结构化结果。** 它至少告诉你：

- `mode`: 是真的发了，还是 log-only
- `status`: 成功还是失败
- `detail`: 一句话人类可读
- `endpoint`: 调了哪个端点（query 串已剥离，不存 secret）
- `error`: 如果失败了，错误是什么

这样不管 Agent 的判断对不对，你**回看 tool_call 就能复盘**——是真打了钉钉失败，还是压根没发。这是上一篇"可复盘"承诺的延续。

### 真跑一次：发真钉钉 + 查真路线

环境变量配好之后，启动服务：

```bash
export DINGTALK_WEBHOOK_URL="https://oapi.dingtalk.com/robot/send?access_token=..."
export ROUTING_BASE_URL="https://router.project-osrm.org"
uvicorn courier_agent_demo.app:app --port 8000
```

创建一个订单，然后模拟一个低电量事件——Agent 会自己判断要 notify_dispatch：

```bash
curl -X POST http://localhost:8000/api/v1/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_name":"张三","pickup_address":"Hub A","delivery_address":"Building B","package_label":"易碎","starting_battery_level":12}'
```

Agent 收到 12% 电量，**去查手册（low_battery 规则）**——然后调用 `notify_dispatch`——**这条消息真出现在你的钉钉群里**。

不是 log-only 的字符串，是真发了：

```
{"mode": "webhook_push", "status": "ok", "detail": "dispatch webhook delivered",
 "endpoint": "https://oapi.dingtalk.com/robot/send", ...}
```

路线那边也一样。如果 event 里给的是结构化 `lng,lat`（例如 `116.4,39.9`），OSRM 就真去算：

```json
{
  "mode": "http_get",
  "status": "ok",
  "detail": "route planned (10072.8m, 656.4s)",
  "endpoint": "https://router.project-osrm.org/route/v1/driving/116.4,39.9;116.5,39.9",
  "distance_m": 10072.8,
  "duration_s": 656.4
}
```

如果是 `"Building B"` 这种自然语言，就**老老实实回 log-only**，并在 note 里说明：传 `lng,lat` 才会真查。**Ponytail: 不假装。** 装 geocoder 是后面的事。

---

## 03 跨订单记忆：让一个快递员认识"老客户"

第二件真事：**让 Agent 拥有跨订单的记忆。**

上一篇的 `update_memory` 只能记"这一单"的事。`memory_key="gate_code"`、`memory_key="customer_note"`，全部带在 `order_id` 上。订单一结束，这些信息也跟着没了。

但现实中，一个快递员记得老客户的：

* 张三的小区门禁密码
* 李四喜欢把件放在楼下的丰巢
* 王五下午 3 点前不在家

这些信息**不该随着订单结束**。下一个订单开始时，Agent 应该能调出来。

### 表上加一个新成员：global_memories

我没有去动现有的 `memories` 表。`memories` 的 `order_id` 是 NOT NULL 强约束，要兼容"没有 order"的情况，最直接的方式是**新开一张表**。

```sql
CREATE TABLE IF NOT EXISTS global_memories (
    memory_key TEXT PRIMARY KEY,
    memory_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

一行一个全局知识。`memory_key` 就是它的主键，跨订单通用。

> Ponytail: **新表而不是扩老表**。多写 30 行代码，少写一段 ALTER 迁移逻辑，省心。规则没到必须抽象时，就别抽象。

`repository.py` 加 3 个方法：

```python
def record_global_memory(self, memory_key: str, memory_json: dict) -> None: ...
def get_global_memory(self, memory_key: str) -> dict | None: ...
def list_global_memory_keys(self) -> list[str]: ...
```

### 工具表面只多一个：`recall_global_memory`

`update_memory` 收一个 `scope` 参数，`"order"`（默认）或 `"global"`。这样写：

```python
@agent.tool
async def update_memory(
    ctx: RunContext[CourierDeps],
    key: str,
    value: str,
    scope: str = "order",
) -> str:
    payload = {"value": value, "event": ctx.deps.event.model_dump(), "run_id": ctx.deps.run_id}
    if scope == "global":
        ctx.deps.repository.record_global_memory(key, payload)
    else:
        ctx.deps.repository.record_memory(ctx.deps.order.order_id, key, payload)
    # ... 记录 tool_call
    return f"Stored {scope} memory for {key}."
```

读这边只需要一个工具——按 key 读，足够覆盖 90% 场景。模糊查询等真有需要再扩。

```python
@agent.tool
async def recall_global_memory(ctx: RunContext[CourierDeps], key: str) -> str:
    memory = ctx.deps.repository.get_global_memory(key)
    # ... 记录 tool_call
    return f"global memory {key} = {memory.get('value')}" if memory else f"no global memory for {key}"
```

这样 agent 的工具表面只多了一个读工具，没污染 `update_memory` 的语义。

### 一次真实使用

模拟场景：第一个订单是张三，Agent 记下了"张三的小区门禁密码是 1234"；第二个订单又是张三（不同地址），Agent 调 `recall_global_memory("zhangsan_gate_code")` 拿到密码。

```bash
# 订单 1：记录
curl -X POST http://localhost:8000/api/v1/orders/$ORDER_1/events \
  -H "Content-Type: application/json" \
  -d '{"event_type":"customer_reply","message":"门禁密码是 1234"}'
# Agent 调 update_memory(key="zhangsan_gate_code", value="1234", scope="global")

# 订单 2：读取
curl -X POST http://localhost:8000/api/v1/orders/$ORDER_2/events \
  -H "Content-Type: application/json" \
  -d '{"event_type":"customer_reply","message":"我到门口了"}'
# Agent 调 recall_global_memory("zhangsan_gate_code") 拿到 1234
```

第二条订单的 tool_call 里就能看到：

```json
{
  "tool_name": "recall_global_memory",
  "input": {"key": "zhangsan_gate_code"},
  "output": {"found": true, "key": "zhangsan_gate_code", "value": "1234"},
  "status": "success"
}
```

**这就是一个简单的"职业 Agent 跨订单记忆"。** 不需要向量数据库，不需要 RAG，SQLite 一张表就够。

> Ponytail: **不带向量召回，不做语义搜索**。MVP 阶段 key 写对就行；等 global memory 多到 key 记不过来，再上嵌入检索。

---

## 04 代码思路

这一篇改动不大，但**每一处都是结构问题**。我列一下这次改了什么、为什么这么改。

### 改动 1：新增 `integrations.py`（单文件，不拆）

我把 3 个 backend + 它们的 composition 放在**一个文件**里：

```
src/courier_agent_demo/integrations.py
```

不拆 3 个文件。理由是：

* 3 个 backend 互不依赖，拆开等于多 4 个空文件
* 它们的构造逻辑（`log_only` vs `from_settings`）天然在一处更直观
* Ponytail: 拆分的判断标准是"独立演进"，3 个 backend 暂时同步演进

每个 backend 实现都很短：

```python
class DispatchBackend:
    def __init__(self, webhook_url: str | None, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def notify(self, reason: str) -> IntegrationResult:
        if not self.webhook_url:
            return IntegrationResult(mode="log_only", status="ok", detail=f"[log-only] dispatch notified: {reason}")
        try:
            mode, body = _http_post_json(self.webhook_url, {"msgtype": "text", "text": {"content": f"[courier-agent] {reason}"}}, timeout=self.timeout)
            return IntegrationResult(mode=mode, status="ok", detail="dispatch webhook delivered", endpoint=_scrub_url(self.webhook_url), extra={"response_excerpt": body})
        except (URLError, HTTPError, TimeoutError, OSError) as error:
            return IntegrationResult(mode="webhook_push", status="error", detail="dispatch webhook failed", endpoint=_scrub_url(self.webhook_url), error=repr(error))
```

**没有 httpx，没有 aiohttp**。stdlib `urllib.request` 完全够用。同步调用——Ponytail 暂不考虑 async 高并发，等真有吞吐问题再换。

> Ponytail ceiling: 同步 stdlib HTTP，**无重试，无熔断**。当一个 bad upstream 把整个 agent 拖死时，再上 tenacity + per-host circuit breaker。

### 改动 2：orchestrator 接收 backends，agent 通过 deps 拿

`CourierOrchestrator.__init__` 多了一个 `backends` 参数，默认是 `ToolBackends.log_only()`：

```python
self.backends = backends or ToolBackends.log_only()
```

`CourierDeps` dataclass 多了一个字段：

```python
@dataclass
class CourierDeps:
    repository: object
    order: OrderRecord
    event: DeliveryEvent
    run_id: str
    model_name: str
    low_battery_threshold: int
    backends: ToolBackends
```

agent 工具通过 `ctx.deps.backends.xxx` 拿到对应后端调用。**这个套路后面要接更多外部系统时也是同一套。**

### 改动 3：repository 加 3 个 global memory 方法 + 1 张新表

```python
def record_global_memory(self, memory_key: str, memory_json: dict) -> None: ...
def get_global_memory(self, memory_key: str) -> dict | None: ...
def list_global_memory_keys(self) -> list[str]: ...
```

新表 `global_memories` 在 `_initialize_schema` 里加了一行 `CREATE TABLE IF NOT EXISTS`。**没有迁移脚本**——SQLite 本地 first，重建库的成本可以接受。

### 改动 4：测试用 `pydantic_ai.models.test.TestModel`

我跑 `pytest` 时发现一个 pre-existing 问题：原来测试用的 `model_name="fake:model"` 在新版 pydantic_ai 里被拒绝。修了：

```python
# agent.py
if provider == "test":
    from pydantic_ai.models.test import TestModel
    return TestModel()
```

> Ponytail: a recognised stub provider so unit tests can build the agent without network or a real LLM.

测试改用 `model_provider="test"`，跑起来：

```
Pytest: 23 passed
```

---

## 写在最后

这一篇没有把系统变得多复杂。

但它把两件事**做实了**：

1. **Agent 的工具是真实的**——钉钉真的响了，路线真的查了
2. **Agent 的记忆是跨订单的**——张三的门禁密码不会因为他下了第二单就丢

如果你还在用桩工具跑 Agent demo，你心里应该清楚：**它跑得通 ≠ 它能用。** 真正能把 Agent 推上生产的，永远是"它动了真实世界里的某样东西"。

下一篇，我想做一个不一样的：**回放 + 评估。**

把已经记录的 timeline 当成"剧本"，重新跑一遍——看不同模型、不同规则下，决策有没有变好。让这个 demo **从能跑，到能证明自己跑得对。**

如果你喜欢这个系列，关注《爬虫之心》公众号，让我们一起开发更多无用Agent。
