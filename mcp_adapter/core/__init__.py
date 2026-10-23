"""MCP 层共享核心：领域常量、时间口径、纯计算函数。

设计约束：
- 只依赖标准库（domain/analytics）与 utils.timezone（timefmt），
  不 import Flask、不 import settings，保证只读 server 可以独立部署。
- 与 settings.py 的一致性由 tests/test_core_parity.py 守护。
"""
