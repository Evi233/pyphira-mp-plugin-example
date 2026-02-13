# pyphira-mp 插件系统文档（事件驱动 / 热重载）

本文档描述本仓库内置的插件系统：如何编写、加载、热重载、以及可用事件。

> 适用范围：本仓库当前实现（`utils/eventbus.py` + `utils/plugin_manager.py`）。

---

## 1. 设计目标

插件系统的核心目标：

1. **零门槛加载**：用户把插件 `.py` 文件丢进 `./plugins/` 即可加载。
2. **事件驱动**：服务端核心逻辑通过 `EventBus` 抛出事件，插件通过订阅事件实现扩展。
3. **热重载**：服务运行中修改/新增/删除插件文件，无需重启即可生效。
4. **安全隔离**：插件异常不会影响主服务器逻辑（异常会被捕获并记录日志）。

---

## 2. 快速开始

### 2.1 目录结构

插件目录固定为：

```
pyphira-mp/
  plugins/
    auth_test.py
    my_plugin.py
```

### 2.2 最小插件示例

创建文件 `plugins/my_plugin.py`：

```py
PLUGIN_INFO = {
    "name": "my_plugin",
    "version": "0.0.1",
}


def setup(ctx):
    ctx.logger.info("my_plugin loaded")

    def on_auth_success(connection=None, user_info=None, **_):
        ctx.logger.info("auth ok: %s", getattr(user_info, "name", None))

    ctx.on("auth.success", on_auth_success)

    def teardown():
        ctx.logger.info("my_plugin unloaded")

    return teardown
```

保存后，服务端会在下一次轮询（默认 1 秒）检测到变化并热重载。

---

## 3. 插件生命周期

插件以“文件”为单位被管理（`plugins/*.py`）。生命周期如下：

### 3.1 启动加载（load）

服务启动时：

1. `PluginManager.start()` 扫描 `plugins/*.py`
2. 对每个插件文件：
   - 通过 `importlib` 从路径加载为模块（module name: `pyphira_plugin_<文件名stem>`）
   - 若模块存在 `setup(ctx)` 函数：调用它
   - 若 `setup` 返回一个可调用对象：作为 `teardown` 保存

### 3.2 热重载（reload）

当插件文件的 `mtime` 变化（例如编辑保存）时：

1. `unload(old)`：
   - 调用旧插件的 `teardown()`（若存在）
   - 从 EventBus 中移除该插件注册的所有事件订阅（自动清理）
   - 从 `sys.modules` 清掉旧 module
2. `load(new)`：重新按启动加载流程加载

### 3.3 删除卸载（unload）

当插件文件被删除：

1. `unload` 同上，清理事件订阅与模块缓存。

---

## 4. 热重载机制细节

本仓库使用**纯标准库**实现热重载：

- 轮询方式：后台 `asyncio` task 周期性扫描 `plugins/*.py`
- 变更判断：比较 `path.stat().st_mtime`
- 默认轮询间隔：`poll_interval=1.0` 秒

日志关键字：

- `Hot-reload watcher started...`
- `Detected change, reloading: xxx.py`
- `Unloaded plugin xxx`
- `Loaded plugin xxx`

> 注意：轮询并非文件系统事件监听（watchdog）。在极端高频变更场景下可能存在 0~poll_interval 秒延迟。

---

## 5. 插件 API 参考

插件入口：

```py
def setup(ctx):
    ...
    return teardown  # 可选
```

### 5.1 `PLUGIN_INFO`（可选）

插件可提供 `PLUGIN_INFO` 字典用于描述自身：

```py
PLUGIN_INFO = {
  "name": "my_plugin",
  "version": "0.0.1",
  "description": "...",
}
```

当前管理器不会强制读取该信息（主要用于自描述/未来扩展）。

### 5.2 `ctx`（PluginContext）

`setup(ctx)` 的 `ctx` 对象提供以下能力：

#### `ctx.on(event: str, callback)`

订阅事件。

- `callback` 可以是普通函数，也可以是 `async def`
- 回调的参数通过关键字传入（`**payload`），因此推荐声明形如：

```py
def handler(connection=None, user_info=None, **_):
    ...
```

#### `ctx.once(event: str, callback)`

订阅一次性事件：第一次触发后自动取消订阅。

#### `ctx.emit(event: str, **payload)`

触发事件。一般用于**插件间通信**或插件内部事件分发。

#### `ctx.logger`

插件专属 logger（名称形如 `plugin.<plugin_name>`），可用于记录日志：

```py
ctx.logger.info("hello")
ctx.logger.exception("something failed")
```

### 5.3 回调异常与异步回调

EventBus 的行为：

- sync callback 抛异常：捕获并记录日志，不影响主逻辑
- async callback：会被 `asyncio.create_task()` 调度执行；异常同样捕获记录

这意味着：

- 插件可以写异步逻辑（例如发起 HTTP 请求），但要注意并发与资源释放
- 插件回调的异常不会阻断服务端包处理流程

---

## 6. 内置事件列表

### 6.1 `auth.success`

触发时机：

- 玩家鉴权成功（`MainHandler.handleAuthenticate` 成功路径）

payload：

- `connection`: `utils.connection.Connection`
- `user_info`: API 获取到的用户信息对象（具体字段取决于 `utils/phiraapi.py` 返回结构）
- `handler`: 当前连接对应的 `MainHandler`

示例：在鉴权后向玩家发送一条系统聊天：

```py
from rymc.phira.protocol.data.message import ChatMessage
from rymc.phira.protocol.packet.clientbound import ClientBoundMessagePacket


def setup(ctx):
    def on_auth_success(connection=None, **_):
        connection.send(ClientBoundMessagePacket(ChatMessage(-1, "hello from plugin")))

    ctx.on("auth.success", on_auth_success)
```

### 6.2 通用事件：所有 handler 自动事件（before/after）

为了避免为每个 `handleXXX` 手动埋点，本仓库在 `MainHandler` 初始化时会自动“包装”所有 `handle*` 方法，统一抛出两类事件：

- `handler.<method>.before`
- `handler.<method>.after`

其中 `<method>` 是方法名（例如：`handleJoinRoom`），那么事件名就是：

- `handler.handleJoinRoom.before`
- `handler.handleJoinRoom.after`

payload（before/after 通用字段）：

- `connection`: `utils.connection.Connection`
- `handler`: 当前连接的 `MainHandler`
- `packet`: 传入的包对象（通常是 ServerBoundXXXPacket；某些 handler 可能无参时为 None）
- `args`: 位置参数 tuple（除去 self）
- `kwargs`: 关键字参数 dict

after 事件额外包含：

- `result`: handler 返回值（大多数 handler 返回 None）

示例：监听“玩家加入房间” handler 调用前后：

```py
def setup(ctx):
    def before_join(packet=None, **_):
        ctx.logger.info("before join room, packet=%r", packet)

    def after_join(packet=None, **_):
        ctx.logger.info("after join room, packet=%r", packet)

    ctx.on("handler.handleJoinRoom.before", before_join)
    ctx.on("handler.handleJoinRoom.after", after_join)
```

> 提醒：这些是“通用埋点事件”，并不代表业务成功/失败语义。业务判断仍需插件根据 packet / 服务器响应逻辑自行判断。

### 6.3 通用事件：包接收事件（packet.received）

当服务端收到任意 packet 时，会先抛出：

- `packet.received`
- `packet.<PacketClassName>.received`（按具体类名区分，例如 `packet.ServerBoundAuthenticatePacket.received`）

payload：

- `connection`
- `handler`
- `packet`

示例：监听所有聊天包：

```py
def setup(ctx):
    def on_chat(packet=None, **_):
        # packet 是 ServerBoundChatPacket
        ctx.logger.info("chat packet: %r", packet)

    ctx.on("packet.ServerBoundChatPacket.received", on_chat)
```

---

## 7. 编写插件的最佳实践

1. **回调签名用宽松参数**：推荐 `def cb(**payload)` 或 `def cb(connection=None, **_)`，避免服务端未来新增字段导致参数不匹配。
2. **避免阻塞**：不要在回调里做长时间 CPU/IO 阻塞（例如 `time.sleep`）。需要等待请用 `async def` + `await asyncio.sleep(...)`。
3. **资源清理放到 teardown**：
   - 创建的后台任务
   - 打开的文件/Socket
   - 连接池等
4. **日志清晰**：使用 `ctx.logger`，便于定位问题。
5. **兼容热重载**：不要把“必须唯一”的全局状态写死；热重载时模块会重新 import。

---

## 8. 常见问题（FAQ / Troubleshooting）

### 8.1 看不到热重载日志？

确认：

- 服务端是否使用同一个终端窗口运行
- `poll_interval` 默认 1 秒，改动后等待 1~2 秒
- 插件文件是否位于 `./plugins/` 且后缀为 `.py`

### 8.2 插件报错会不会把服务器搞崩？

不会。

- EventBus 对回调异常做了捕获并记录日志
- PluginManager 在 load/unload/reload 过程中也做了异常捕获

但插件可以做“坏事”（例如 `os.remove`），因此：

> **强烈建议只加载你信任的插件代码。**

### 8.3 插件里能 import 项目其他模块吗？

可以。插件运行在同一 Python 进程中，能 `import utils.xxx` 或 `rymc.xxx`。

---

## 9. 安全提示

插件是**任意 Python 代码执行**，具备与服务器进程等同权限：

- 可读写文件
- 可访问网络
- 可执行任意 Python 操作

因此不要加载来源不明的插件。
