#!/usr/bin/env python3
"""只读分析服务入口（12 个工具）：只做转发，逻辑在 ``mcp_adapter/servers/reader.py``。

本地和服务器用同一个文件名、同一个相对位置：

  本地：``uv run python mcp_reader.py``
  服务器：``/root/.octop/venv/bin/python /root/health_tools/mcp_reader.py``

这里不写死 ``parent.parent``，而是向上找“包含 mcp_adapter/servers/reader.py 的目录”
并加入 ``sys.path``，所以无论文件放在仓库根还是部署目录根都能工作。
Octop connector ``sbhealth`` 指向这个文件。
"""
import sys
from pathlib import Path


def _find_package_root() -> Path:
    here = Path(__file__).resolve()
    for base in (here.parent, *here.parents):
        if (base / "mcp_adapter" / "servers" / "reader.py").is_file():
            return base
    raise RuntimeError(
        "找不到 mcp_adapter 包。请把 mcp_adapter/ 与 utils/ 同步到 "
        f"{here.parent} 下（保持 mcp_adapter/servers/reader.py 结构）。"
    )


_ROOT = _find_package_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp_adapter.servers.reader import main  # noqa: E402

if __name__ == "__main__":
    main()
