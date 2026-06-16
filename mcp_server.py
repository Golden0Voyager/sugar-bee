#!/usr/bin/env python3
"""兼容入口：保留根目录的 mcp_server.py，实际逻辑已迁移到 mcp/server.py。

原 Claude Desktop 配置和命令无需修改：
  uv run python mcp_server.py
"""
from mcp_adapter.server import main

if __name__ == "__main__":
    main()
