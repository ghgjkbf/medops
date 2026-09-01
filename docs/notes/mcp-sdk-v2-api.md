# MCP Python SDK v2 API 权威笔记（实测）

实测日期：2026-09-01 · 实测版本：**mcp 2.1.1**（`uv pip install "mcp[cli]>=2,<3"`，Windows / CPython 3.14）· 验证脚本：`spikes/mcp_v2_spike.py`（运行输出 `SPIKE OK: mcp==2.1.1`，streamable-http 与 stdio 双传输往返均通过）。

## 1. Server：MCPServer 定义 tool

```python
from mcp.server import MCPServer

mcp_server = MCPServer("medops-spike")   # v1 的 FastMCP 已更名为 MCPServer

@mcp_server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""      # docstring 变成 tool 描述
    return a + b

@mcp_server.tool()
def get_device_status(device_id: str) -> dict:
    return {"device_id": device_id, "online": True, "battery": 87}
```

- `MCPServer.tool(name=None, title=None, description=None, annotations=None, icons=None, meta=None, structured_output=None)` —— 全参数可选。
- 入参 JSON Schema 由类型注解自动生成（pydantic 2.13）。`list_tools` 返回的 `Tool` 对象属性是 snake_case：**`t.input_schema`**（不是 v1/JSON 风格的 `inputSchema`，踩过坑）。

## 2. Server 启动：两种 transport

```python
# stdio（子进程模式，给 Claude Desktop 之类的宿主用）
mcp_server.run("stdio")                      # 同步阻塞；底层调 run_stdio_async()

# streamable-http（生产默认；SDK 内置 uvicorn）
await mcp_server.run_streamable_http_async(host="127.0.0.1", port=port)  # async，可放进 create_task
# 或者同步等价：mcp_server.run("streamable-http", host=..., port=...)
```

- HTTP 默认路径 **`/mcp`**（`streamable_http_path="/mcp"`，可改）。
- 需要 ASGI 挂载时用 `mcp_server.streamable_http_app(...) -> Starlette` 拿 app 交给自己的 uvicorn。
- `run_streamable_http_async` 无 `port=0` 自动分配语义——用 `socket.bind(("127.0.0.1", 0))` 先取空闲端口再传入（spike 脚本里的 `free_port()`）。

## 3. Client：统一 Client（单对象取代 v1 的 transport + ClientSession 两层）

```python
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

# streamable-http：直接传 URL 字符串（必须含 /mcp 路径）
async with Client("http://127.0.0.1:8000/mcp") as client:
    tools = await client.list_tools()          # -> ListToolsResult, .tools: list[Tool]
    r = await client.call_tool("add", {"a": 1, "b": 2})   # -> CallToolResult

# stdio：传 StdioServerParameters，SDK 拉子进程
params = StdioServerParameters(command=sys.executable, args=["server.py"])
async with Client(params) as client:
    ...

# 测试：直接传 MCPServer 实例，in-process 连接，零网络（见 §5）
async with Client(mcp_server) as client: ...
```

`Client(server)` 第一个参数接受：`str`(URL) / `StdioServerParameters` / `Transport` / `MCPServer`（in-process）。`Client` 是 async context manager（`async with`），v1 的手动 `ClientSession(read, write)` 不再需要。

## 4. call_tool 返回值（重要坑）

`CallToolResult` 字段：`content`, `structured_content`, `is_error`, `meta`。

实测 2.1.1 行为：

| tool 返回类型 | `structured_content` | `content` |
|---|---|---|
| `int`（`-> int`） | `{"result": 3}` | TextContent `"3"` |
| `dict`（`-> dict`） | **None** | TextContent，**JSON 字符串**（`json.loads(r.content[0].text)` 取回 dict） |

→ 取返回值要写成兼容式：

```python
data = r.structured_content if r.structured_content else json.loads(r.content[0].text)
```

`structured_content` 只在 tool 声明了输出 schema（或标量自动包装 `{"result": ...}`）时才填充。若 Task 9/10 需要强类型返回，给 tool 传 `structured_output=True` 或用 pydantic model 作返回注解，再实测确认。

## 5. 与 v1 的关键差异

| v1 | v2 (2.1.1) |
|---|---|
| `from mcp.server.fastmcp import FastMCP` | `from mcp.server import MCPServer`（`FastMCP` 名字废弃） |
| `stdio_client()` / `streamablehttp_client()` + 手动 `ClientSession(read, write)` | 统一 `Client(url_or_params_or_server)`，`async with` 一行连接 |
| `session.initialize()` 手动握手 | `Client.__aenter__` 自动完成 |
| `Tool.inputSchema`（camelCase 属性） | `Tool.input_schema`（pydantic snake_case） |
| SSE 为远程主推传输 | **streamable-http 为默认**（`/mcp` 单端点，POST + 可选 SSE 流）；SSE 保留为 legacy（`run_sse_async`） |
| `mcp.__version__` | **不存在** → 用 `importlib.metadata.version("mcp")` |
| httpx 1.x | 依赖树为 httpx 1.x + anyio + starlette 1.6 + uvicorn 0.52 + pydantic 2.13（本环境实测；未见 "httpx2"，该说法不成立） |

## 6. 对 Task 9/10（medops MCP server + 测试）的建议

1. **pytest 集成测试用 in-process transport**，不起端口、不起子进程，最快最稳：

```python
import pytest
from mcp.client import Client
from medops.mcp_server import mcp_server   # 你的 MCPServer 实例

@pytest.mark.asyncio
async def test_add():
    async with Client(mcp_server) as client:
        r = await client.call_tool("add", {"a": 1, "b": 2})
        assert r.structured_content["result"] == 3
```

2. **tool 返回 dict 时**：客户端侧用 `json.loads(r.content[0].text)` 兜底（§4），或定义 pydantic 返回模型并实测 `structured_output`。
3. **生产部署**：`mcp_server.run("streamable-http", host=..., port=...)` 或把 `streamable_http_app()` 挂进既有 ASGI 服务；路径保持默认 `/mcp`，Client 直接传完整 URL。
4. **stdio 模式**用于本地 agent 宿主集成：入口 `if __name__ == "__main__": mcp_server.run("stdio")`，Client 侧 `StdioServerParameters(command=..., args=[...])`。
5. 版本断言用 `importlib.metadata.version("mcp")`，不要 `mcp.__version__`。
6. **in-process 必须传裸 `MCPServer` 实例**：`Client(wrapper.mcp)` ✅，`Client(wrapper)` ❌（包装对象报 "does not support the asynchronous context manager protocol"）——Task 9 实测补充。
7. **Client 不能跨 async fixture yield**：pytest-asyncio function 级 loop 下，在 fixture 里 `async with Client(...)` 再 yield，teardown 报 "cancel scope in a different task"。解法：Client 进/出放测试体内，fixture 只给 server 实例——P1-4/9/10 全部按此写。
8. **MCP tool 回调是同步函数**：连库用同步 engine（asyncpg URL 换 psycopg2，`_sync_url()` 换算），AsyncSession 在同步回调里不可用——maintenance-db Server 范式。
9. **uv workspace 成员新增后**：`uv sync --all-packages` 才会安装新成员的可传递依赖（默认 sync 只装 root 直接依赖）。

## 7. 环境复现

```bash
uv venv .venv-spike
uv pip install --python .venv-spike/Scripts/python.exe "mcp[cli]>=2,<3"
.venv-spike/Scripts/python.exe spikes/mcp_v2_spike.py   # => SPIKE OK: mcp==2.1.1
```
