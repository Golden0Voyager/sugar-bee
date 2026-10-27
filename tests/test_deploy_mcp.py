"""MCP 独立发布清单的回归测试。"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_writer_bundle_includes_indirect_branding_dependency():
    """writer 经 utils.db 导入 branding，预检、语法检查和同步都不能漏掉它。"""
    script = (REPO_ROOT / 'scripts/deploy_mcp.sh').read_text(encoding='utf-8')

    assert '[ "$ONLY" != "reader" ] && NEED+=(settings.py branding.py)' in script
    assert "'settings.py','branding.py'" in script
    assert 'for f in settings.py branding.py; do' in script
