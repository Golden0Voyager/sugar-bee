#!/usr/bin/env python3
"""写服务入口（11 个工具）：只做转发，逻辑在 ``mcp_adapter/servers/writer.py``。

不在这里写业务代码，是为了让本地和服务器用同一个文件名、同一个相对位置：

  本地：``uv run python mcp_writer.py``
  服务器：``/root/.octop/venv/bin/python /root/health_tools/mcp_writer.py``

Octop connector ``sugarwrite``、Claude Desktop 的 ``mcpServers`` 都指向这个文件。
改路径时要同时改 connector 的 args，见 ``docs/mcp-architecture-review.md`` 第 6 节。
"""
from mcp_adapter.servers.writer import main

if __name__ == "__main__":
    main()
