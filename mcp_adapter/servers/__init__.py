"""两个 MCP server 入口。

- ``writer``：写入 / 核对（11 个工具），与 Flask 同机，走 API + 内联批量写库
- ``reader``：只读分析（12 个工具），以 ``mode=ro`` 打开数据库，可独立部署

共享逻辑在 ``mcp_adapter.core``；本包只放「入口 + 工具定义」。
"""
