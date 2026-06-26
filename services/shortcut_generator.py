"""iOS 捷径 (.shortcut) 生成器。

使用 Python 标准库 plistlib 生成未签名的 binary plist,可被 iOS「快捷指令」App
识别并导入。

当前版本(v1)生成"首次绑定"捷径:运行后读取剪贴板中的 6 位绑定码,调用
/api/v1/health-sync/bind_from_shortcut 完成设备绑定,并显示返回的
device_id/device_token。

后续可在此文件扩展完整的"绑定+同步"捷径。
"""
from __future__ import annotations

import io
import plistlib
import uuid


def _uuid() -> str:
    """生成 Shortcuts 使用的大写 UUID 字符串。"""
    return str(uuid.uuid4()).upper()


def _text_token(text: str, attachments: dict | None = None) -> dict:
    """构造 Shortcuts 文本令牌(WFTextTokenString)。

    attachments 用于把"￼"字符替换为前面某个动作的 Magic Variable。
    key 格式为 "{startIndex, length}",value 为 ActionOutput 描述。
    """
    value: dict = {"string": text}
    if attachments:
        value["attachmentsByRange"] = attachments
    return {"Value": value, "WFSerializationType": "WFTextTokenString"}


def _headers_value(headers: dict[str, str]) -> dict:
    """构造 Shortcuts 的 HTTP headers 字段值。"""
    return {
        "Value": {"headers": headers},
        "WFSerializationType": "WFDictionaryFieldValue",
    }


def _action(identifier: str, params: dict | None = None) -> dict:
    """构造单个 Shortcuts 动作字典。"""
    action: dict = {"WFWorkflowActionIdentifier": identifier}
    if params:
        action["WFWorkflowActionParameters"] = params
    return action


def _get_clipboard_action(action_uuid: str) -> dict:
    """获取剪贴板动作。"""
    return _action("is.workflow.actions.getclipboard", {"UUID": action_uuid})


def _get_url_action(
    action_uuid: str,
    url: str,
    method: str,
    body_token: dict,
    headers: dict[str, str],
) -> dict:
    """构造"获取 URL 内容"动作(HTTP 请求)。"""
    return _action(
        "is.workflow.actions.geturl",
        {
            "UUID": action_uuid,
            "WFURL": _text_token(url),
            "WFHTTPMethod": method,
            "WFHTTPBody": body_token,
            "WFHTTPHeaders": _headers_value(headers),
        },
    )


def _show_result_action(text: str, attachments: dict | None = None) -> dict:
    """构造"显示结果"动作(在屏幕顶部弹出提示)。"""
    return _action(
        "is.workflow.actions.showresult",
        {"Text": _text_token(text, attachments)},
    )


def _workflow(actions: list[dict], name: str = "Sugar Bee 同步") -> dict:
    """构造完整的 WFWorkflow 根字典。"""
    return {
        "WFWorkflow": {
            "WFWorkflowClientRelease": "18.0",
            "WFWorkflowClientVersion": "1302.1.3",
            "WFWorkflowIcon": {
                "WFWorkflowIconStartColor": 4282601983,
                "WFWorkflowIconGlyphNumber": 61440,
            },
            "WFWorkflowImportQuestions": [],
            "WFWorkflowInputContentItemClasses": [
                "WFURLContentItem",
                "WFTextContentItem",
            ],
            "WFWorkflowMinimumClientVersion": 1300,
            "WFWorkflowMinimumClientVersionString": "1300",
            "WFWorkflowName": name,
            "WFWorkflowOutputContentItemClasses": [],
            "WFWorkflowTypes": ["ActionExtension", "MenuBar"],
            "WFWorkflowActions": actions,
        }
    }


def generate_binding_shortcut(base_url: str) -> bytes:
    """生成"Sugar Bee 绑定"捷径文件(v1)。

    运行流程:
    1. 读取剪贴板(用户需先在 Sugar Bee 网页复制 6 位绑定码)
    2. POST /api/v1/health-sync/bind_from_shortcut
       body: {"code": "<剪贴板>", "device_name": "iPhone"}
    3. 显示服务器返回的 JSON(包含 device_id 与 device_token)

    Args:
        base_url: Sugar Bee 服务根 URL,例如 https://example.com/
    """
    base_url = base_url.rstrip("/")
    bind_url = f"{base_url}/api/v1/health-sync/bind_from_shortcut"

    clipboard_uuid = _uuid()
    post_uuid = _uuid()

    # 把剪贴板内容嵌入 JSON body。文本中的 "￼"(U+FFFC)会被替换为剪贴板变量。
    body_text = '{"code": "￼", "device_name": "iPhone"}'
    body_attachments = {
        "{10, 1}": {
            "OutputName": "Clipboard",
            "OutputUUID": clipboard_uuid,
            "Type": "ActionOutput",
        }
    }

    actions = [
        _get_clipboard_action(clipboard_uuid),
        _get_url_action(
            action_uuid=post_uuid,
            url=bind_url,
            method="POST",
            body_token=_text_token(body_text, body_attachments),
            headers={"Content-Type": "application/json"},
        ),
        _show_result_action(
            "绑定结果：￼\n\n请把 device_id 和 device_token 保存好,后续同步需要用到。",
            attachments={
                "{6, 1}": {
                    "OutputName": "Contents of URL",
                    "OutputUUID": post_uuid,
                    "Type": "ActionOutput",
                }
            },
        ),
    ]

    workflow = _workflow(actions, name="Sugar Bee 绑定")
    buffer = io.BytesIO()
    plistlib.dump(workflow, buffer, fmt=plistlib.FMT_BINARY)
    return buffer.getvalue()


def generate_sync_shortcut(
    base_url: str,
    device_id: str,
    device_token: str,
) -> bytes:
    """生成"Sugar Bee 同步"捷径文件(v1,最小可用)。

    当前为占位实现:生成的捷径会提示用户"请手动创建同步捷径"。
    原因:在 plist 中正确表达"循环遍历健康样本+构造 JSON 数组+POST"的 Magic
    Variable 引用较为复杂,需要先在真实 iOS 设备上验证基础动作导入后再扩展。

    Args:
        base_url: Sugar Bee 服务根 URL。
        device_id: 已绑定设备的 device_id。
        device_token: 已绑定设备的 device_token。
    """
    del base_url, device_id, device_token  # v1 占位,暂不嵌入真实 token
    actions = [
        _show_result_action(
            " Sugar Bee 同步捷径 v1\n\n"
            "目前请先在 iPhone 上按页面说明手动创建同步捷径。\n"
            "后续版本将支持一键导入完整同步捷径。"
        ),
    ]
    workflow = _workflow(actions, name="Sugar Bee 同步")
    buffer = io.BytesIO()
    plistlib.dump(workflow, buffer, fmt=plistlib.FMT_BINARY)
    return buffer.getvalue()
