from flask import jsonify

from utils.timezone import now as app_now


def api_success(data=None, message=None):
    response = {
        'status': 'success',
        'timestamp': app_now().isoformat()
    }
    if data is not None:
        response['data'] = data
    if message:
        response['message'] = message
    return jsonify(response), 200


def api_error(message, status_code=400, error_type=None, details=None):
    response = {
        'status': 'error',
        'message': message,
        'timestamp': app_now().isoformat()
    }
    if error_type:
        response['error_type'] = error_type
    if details:
        response['details'] = details
    return jsonify(response), status_code


success_res = api_success
error_res = api_error
