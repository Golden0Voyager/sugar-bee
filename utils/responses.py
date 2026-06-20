import datetime

from flask import jsonify


def api_success(data=None, message=None):
    """
    生成成功的 API 响应

    Args:
        data: 响应数据
        message: 可选的消息

    Returns:
        tuple: (JSON响应, HTTP状态码)
    """
    response = {
        'status': 'success',
        'timestamp': datetime.datetime.now().isoformat()
    }
    if data is not None:
        response['data'] = data
    if message:
        response['message'] = message
    return jsonify(response), 200


def api_error(message, status_code=400, error_type=None, details=None):
    """
    生成错误的 API 响应

    Args:
        message: 错误消息
        status_code: HTTP 状态码 (默认 400)
        error_type: 错误类型标识
        details: 错误详情

    Returns:
        tuple: (JSON响应, HTTP状态码)
    """
    response = {
        'status': 'error',
        'message': message,
        'timestamp': datetime.datetime.now().isoformat()
    }
    if error_type:
        response['error_type'] = error_type
    if details:
        response['details'] = details
    return jsonify(response), status_code

# 兼容性别名
success_res = api_success
error_res = api_error
