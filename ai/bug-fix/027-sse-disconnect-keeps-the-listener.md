# 027 · SSE 断开后订阅者没有立刻注销：每个关掉的标签页留一个无界队列

- 状态：已修复
- 提交：`fix(events): SSE 断开时立刻注销订阅者，不再等 GC`
- 影响面：每一次浏览器断开 `/api/events`（关标签页、刷新、后端重启、断网）。
- 严重度：低到中。它不是功能故障（事件照常推给活着的前端），而是**资源泄漏 + 隐性负载**：
  泄漏期间那个订阅者仍会被 `publish()` 逐个喂消息，而队列是无界的。

## 问题

`GET /api/events` 的生成器写法是：

```python
async def event_stream():
    async for message in broker.subscribe():
        yield f"data: {message}\n\n"
```

客户端断开时，Starlette 会 `await body_iterator.aclose()`。但 **`async for` 不会关闭它的
子异步生成器**（PEP 525 的老坑）—— `broker.subscribe()` 停在 `yield await queue.get()` 上，
它的 `finally: self._listeners.discard(queue)` 迟迟不执行。

实测（新增用例里的断言，修复前）：

```text
assert broker._listeners == set()
E  assert {<Queue at 0x… maxsize=0 tasks=1>} == set()
```

## 原因

`EventBroker.subscribe()` 的注销写在 `finally` 里，这本身是对的；问题在于**没有任何代码
保证那个 `finally` 会被执行**。异步生成器只有在被 `aclose()`（或 GC 时的异步终结器）关闭时
才会走 `finally`，而 `async for` 提前退出不会替调用方做这件事。

后果分两段：
1. **立即**：断开的前端仍在订阅集合里，`publish()` 继续往一个没人读的队列里塞消息；
2. **直到 GC**：该生成器被回收时，事件循环的异步生成器终结器才会调度 `aclose()`。
   在此之前，每断开一次就多一个队列 —— 刷新十次就是十个。

因为队列无界，这段窗口里不会阻塞任何人，所以它**不会**表现为故障，只会表现为
「越用越慢/内存慢慢涨」，而且没有任何日志。

## 修复方案

把订阅对象**显式持有并在 `finally` 里关闭**：

```python
async def event_stream():
    subscription = broker.subscribe()
    try:
        async for message in subscription:
            yield f"data: {message}\n\n"
    finally:
        await subscription.aclose()
```

这样断开（正常关闭或取消）都会走 `finally`，`EventBroker` 的注销逻辑立刻执行 ——
注销的判据仍在**一处**（`subscribe()` 自己的 `finally`），路由层只负责「保证它被调用」。

### 被否决的方案

- **在路由里直接 `broker.unsubscribe(queue)`**：那要求把内部队列暴露出来，
  等于把「谁负责注销」拆到两个模块。现在的写法只多两行，职责仍收在 `EventBroker`。
- **给 `publish()` 加「队列满了就丢订阅者」**：掩盖症状而不是修原因，而且要看队列长度
  就必须给它设上限（现在是无界、不阻塞，那是有意为之，见 `test_a_slow_subscriber_does_not_block_the_others`）。
- **换成 `contextlib.aclosing`**：`aclosing` 能解决问题，但要额外 import，且在这个场景里
  `try/finally` 更直白（读代码的人一眼看到「关闭在哪里」）。

## 效果

| | 修复前 | 修复后 |
| --- | --- | --- |
| 客户端断开后 `_listeners` | 仍含那个队列（直到 GC） | 立刻为空 |
| 断开的订阅者是否继续被喂消息 | 是 | 否 |
| 连续刷新 N 次 | 最多累积 N 个无界队列 | 0 |

## 未覆盖 · 如实说明

1. **没有在真实浏览器里量内存**：判据是 `_listeners` 集合（可观测、确定），不是 RSS。
2. **取消（而不只是 `aclose`）的场景**：`GeneratorExit` 与 `CancelledError` 都走同一个
   `finally`，但只测了前者（`aclose` 是 Starlette 实际调用的那条路）。
3. **没有对 `publish()` 加背压**：慢消费者仍会积压（无界队列）。这是既有设计
   （不阻塞其他订阅者），本轮没有改动。

## 验证

**A/B**：把 `routers/events.py` 还原成 `async for broker.subscribe()` 的写法，
`test_the_sse_route_frames_each_message_as_a_data_line` 的最后一条断言失败（上面的实测输出）。
修复后 `tests/test_events.py` **5 条全绿**，其中既有「帧格式」也有「注销后集合为空」。

**覆盖**：`app/events.py` 61% → **100%**；`app/routers/events.py` 55% → **100%**。

## 风险与回滚

- **回滚**：revert 即回到「等 GC」。功能不受影响，所以回滚安全（只是泄漏回来）。
- **误伤**：`aclose()` 会被调用两次吗？`finally` 只会在生成器退出时执行一次，
  且对已关闭的异步生成器再 `aclose()` 是幂等的。用例里连续关闭 + 再发布一次也验过。

## 关联

- 同属「生命周期没有被显式结束」：015 是队列的**进程**生命周期，这里是订阅的**请求**生命周期。
  两者都不是逻辑错误，而是「以为有人会自动收拾」。
