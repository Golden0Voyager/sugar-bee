#!/usr/bin/env python3
"""生成 Sugar Bee iOS 捷径文件。

用法:
    python scripts/generate_shortcut.py [--host HOST] [--port PORT]

默认生成到 static/shortcuts/SugarBeeBind.shortcut
自动调用 shortcuts sign 签名（需要 macOS + Shortcuts App）
"""
import argparse
import os
import plistlib
import subprocess
import sys
import tempfile

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def generate_bind_shortcut(host: str = '192.168.1.72', port: int = 5001) -> dict:
    """生成绑定码捷径的 plist 字典。

    捷径流程:
    1. 获取剪贴板（绑定码）
    2. 创建词典 {"code": 剪贴板内容}
    3. POST 请求到服务器
    4. 显示结果
    """
    url = f'http://{host}:{port}/api/v1/health-sync/bind_from_shortcut'

    actions = [
        # 1. 获取剪贴板
        {
            'WFWorkflowActionIdentifier': 'is.workflow.actions.getclipboard',
            'WFWorkflowActionParameters': {},
        },
        # 2. 设置变量 "BindCode"
        {
            'WFWorkflowActionIdentifier': 'is.workflow.actions.setvariable',
            'WFWorkflowActionParameters': {
                'WFVariableName': 'BindCode',
            },
        },
        # 3. 获取变量 "BindCode"（用于后续引用）
        {
            'WFWorkflowActionIdentifier': 'is.workflow.actions.getvariable',
            'WFWorkflowActionParameters': {
                'WFVariable': {
                    'Value': {'Type': 'Variable', 'VariableName': 'BindCode'},
                    'WFSerializationType': 'WFTextTokenAttachment',
                },
            },
        },
        # 4. 文本 - JSON 模板（使用变量）
        {
            'WFWorkflowActionIdentifier': 'is.workflow.actions.gettext',
            'WFWorkflowActionParameters': {
                'WFTextActionText': {
                    'Value': {
                        'attachmentsByRange': {
                            '{8, 1}': {
                                'Type': 'Variable',
                                'VariableName': 'BindCode',
                            },
                        },
                        'string': '{"code": "\ufffc"}',
                    },
                    'WFSerializationType': 'WFTextTokenString',
                },
            },
        },
        # 5. URL
        {
            'WFWorkflowActionIdentifier': 'is.workflow.actions.url',
            'WFWorkflowActionParameters': {
                'WFURLActionURL': url,
            },
        },
        # 6. 获取 URL 内容 (POST)
        {
            'WFWorkflowActionIdentifier': 'is.workflow.actions.geturlcontent',
            'WFWorkflowActionParameters': {
                'WFHTTPMethod': 'POST',
                'WFHTTPBodyType': 'JSON',
                'WFGetDictionaryValueType': 'Dictionary',
                'WFHTTPHeaders': [],
                'WFFormValues': {
                    'Value': {
                        'attachmentsByRange': {
                            '{0, 1}': {
                                'Type': 'Variable',
                                'VariableName': 'BindCode',
                            },
                        },
                        'string': '\ufffc',
                    },
                    'WFSerializationType': 'WFTextTokenString',
                },
            },
        },
        # 7. 显示结果
        {
            'WFWorkflowActionIdentifier': 'is.workflow.actions.showresult',
            'WFWorkflowActionParameters': {},
        },
    ]

    return {
        'WFWorkflowMinimumClientVersion': 900,
        'WFWorkflowMinimumClientVersionString': '900',
        'WFWorkflowClientVersion': '2612.0.4',
        'WFWorkflowHasShortcutInputVariables': False,
        'WFWorkflowIcon': {
            'WFWorkflowIconStartColor': 463140863,
            'WFWorkflowIconGlyphNumber': 59771,
        },
        'WFWorkflowImportQuestions': [],
        'WFWorkflowInputContentItemClasses': [],
        'WFWorkflowTypes': ['NCWidget', 'WatchKit', 'ActionExtension'],
        'WFWorkflowActions': actions,
        'WFWorkflowHasOutputFallback': False,
        'WFWorkflowName': 'Sugar Bee 绑定',
    }


def save_shortcut(shortcut_dict: dict, path: str) -> None:
    """保存为二进制 plist 文件。"""
    with open(path, 'wb') as f:
        plistlib.dump(shortcut_dict, f, fmt=plistlib.FMT_BINARY)


def sign_shortcut(input_path: str, output_path: str) -> bool:
    """使用 macOS shortcuts CLI 签名捷径文件。

    Returns:
        True if signing succeeded, False otherwise.
    """
    try:
        result = subprocess.run(
            ['shortcuts', 'sign', '--mode', 'anyone', '--input', input_path, '--output', output_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True
        print(f'⚠️  签名失败: {result.stderr}', file=sys.stderr)
        return False
    except FileNotFoundError:
        print('⚠️  未找到 shortcuts 命令（需要 macOS + Shortcuts App）', file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print('⚠️  签名超时', file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description='生成 Sugar Bee iOS 捷径文件')
    parser.add_argument('--host', default='192.168.1.72', help='服务器 IP 地址')
    parser.add_argument('--port', type=int, default=5001, help='服务器端口')
    parser.add_argument('--output', default=None, help='输出文件路径')
    parser.add_argument('--no-sign', action='store_true', help='跳过签名')
    args = parser.parse_args()

    shortcut = generate_bind_shortcut(args.host, args.port)

    output_path = args.output or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'static', 'shortcuts', 'SugarBeeBind.shortcut'
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if args.no_sign:
        save_shortcut(shortcut, output_path)
        print(f'✅ 捷径文件已生成（未签名）: {output_path}')
    else:
        # 先保存到临时文件，签名后输出到目标路径
        with tempfile.NamedTemporaryFile(suffix='.shortcut', delete=False) as tmp:
            tmp_path = tmp.name
        try:
            save_shortcut(shortcut, tmp_path)
            if sign_shortcut(tmp_path, output_path):
                print(f'✅ 捷径文件已生成并签名: {output_path}')
            else:
                # 签名失败，保存未签名版本
                save_shortcut(shortcut, output_path)
                print(f'⚠️  签名失败，已保存未签名版本: {output_path}')
                print('   提示: 未签名的捷径可能无法在 iOS 15+ 上导入')
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    print(f'   服务器地址: http://{args.host}:{args.port}')


if __name__ == '__main__':
    main()
