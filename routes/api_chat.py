from flask import Blueprint, request, jsonify, Response, stream_with_context, session
import datetime
import json
import uuid
import sqlite3

from ai_client import call_chat_stream, CHAT_AVAILABLE
import settings
from core.config import DB_NAME
from utils.auth import login_required
from utils.db import get_db

bp_chat = Blueprint('chat', __name__, url_prefix='/api/chat')
CHAT_HISTORY_LIMIT = 20

def get_current_user_id():
    return session.get('current_user_id', 1)

def build_chat_context(db, user_id):
    """构建健康助手的 system prompt"""
    days = 7
    now = datetime.datetime.now()
    c = db.cursor()
    sections: list[str] = []

    # 用户健康档案
    sections.append(settings.get_ai_system_prompt(user_id))

    # 今日详细数据 (按时间发生顺序排列)
    today_date = now.strftime('%Y-%m-%d')
    c.execute("""
        SELECT type, value, systolic_pressure, diastolic_pressure, distance, calories, notes, timestamp, weight, medication_name
        FROM records
        WHERE user_id = ? AND timestamp >= ? AND is_predicted = 0
        ORDER BY timestamp ASC
    """, (user_id, today_date))
    today_records = c.fetchall()
    
    if today_records:
        today_details: list[str] = []
        for r in today_records:
            t_type, t_val, t_sys, t_dia, t_dist, t_cal, t_notes, t_time, t_weight, t_med = r
            time_str = t_time.split(' ')[1][:5] if t_time and ' ' in t_time else (t_time or '')
            
            detail_str = f"- {time_str} {t_type or '记录'}: "
            parts: list[str] = []
            if t_val and float(t_val) > 0:
                parts.append(f"数值 {float(t_val):.1f}")
            if t_sys and float(t_sys) > 0 and t_dia and float(t_dia) > 0:
                # Use float format to be safer with type checking
                parts.append(f"血压 {int(float(t_sys))}/{int(float(t_dia))}")
            if t_dist and float(t_dist) > 0:
                parts.append(f"距离 {float(t_dist):.2f}km")
            if t_cal and float(t_cal) > 0:
                parts.append(f"热量 {int(float(t_cal))}kcal")
            if t_weight and float(t_weight) > 0:
                parts.append(f"体重 {float(t_weight):.1f}kg")
            if t_med:
                parts.append(f"用药 {t_med}")
            if t_notes:
                parts.append(f"备注: {t_notes}")
            
            if parts:
                detail_str += "，".join(parts)
                today_details.append(detail_str)
        if today_details:
            sections.append(f"今日（{today_date}）最新详细记录：\n" + "\n".join(today_details))

    # 血糖数据
    c.execute("""
        SELECT value, type, timestamp FROM records
        WHERE user_id = ? AND value > 0 AND is_predicted = 0
        AND timestamp > datetime('now', ? || ' days')
        AND type NOT IN ('跑步', '运动')
        AND type NOT LIKE '%血压%'
        AND systolic_pressure IS NULL
        ORDER BY timestamp DESC
    """, (user_id, f'-{days}'))
    glucose = c.fetchall()
    if glucose:
        vals = [r[0] for r in glucose]
        sections.append(f"近{days}天血糖均值 {sum(vals)/len(vals):.1f}")

    # 省略部分 context 构建以保持代码精简，实际中可以补全
    return "\n".join(sections)

@bp_chat.route('/stream', methods=['POST'])
@login_required
def chat_stream():
    """流式 AI 对话 (SSE)"""
    if not CHAT_AVAILABLE:
        return jsonify({'status': 'error', 'message': "健康助手未配置"}), 503

    data = request.get_json()
    message = (data or {}).get('message', '').strip()
    session_id = (data or {}).get('session_id', '')
    if not message or not session_id:
        return jsonify({'status': 'error', 'message': "缺少参数"}), 400

    user_id = get_current_user_id()
    db = get_db()

    # 构建 system prompt
    context = build_chat_context(db, user_id)
    system_prompt = f"你是健康助手。近期数据：\n{context}"

    # 加载历史消息
    c = db.cursor()
    c.execute("SELECT role, content FROM chat_messages WHERE user_id = ? AND session_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, session_id, CHAT_HISTORY_LIMIT))
    history = [{"role": r[0], "content": r[1]} for r in reversed(c.fetchall())]

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    # 保存用户消息
    c.execute("INSERT INTO chat_messages (user_id, session_id, role, content) VALUES (?, ?, 'user', ?)", (user_id, session_id, message))
    db.commit()

    def generate():
        full_reply = []
        try:
            for chunk in call_chat_stream(messages):
                full_reply.append(chunk)
                yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            if full_reply:
                reply_text = "".join(full_reply)
                try:
                    conn = sqlite3.connect(DB_NAME)
                    conn.execute("INSERT INTO chat_messages (user_id, session_id, role, content) VALUES (?, ?, 'assistant', ?)", (user_id, session_id, reply_text))
                    conn.commit()
                    conn.close()
                except Exception:
                    pass
            yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@bp_chat.route('/history', methods=['GET'])
@login_required
def chat_history():
    user_id = get_current_user_id()
    session_id = request.args.get('session_id', '')
    db = get_db()
    c = db.cursor()

    if not session_id:
        c.execute("SELECT DISTINCT session_id FROM chat_messages WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,))
        row = c.fetchone()
        session_id = row[0] if row else ''

    messages = []
    if session_id:
        c.execute("SELECT role, content, created_at FROM chat_messages WHERE user_id = ? AND session_id = ? ORDER BY created_at ASC", (user_id, session_id))
        messages = [{"role": r[0], "content": r[1], "created_at": r[2]} for r in c.fetchall()]

    c.execute("SELECT session_id, MIN(content), MIN(created_at) FROM chat_messages WHERE user_id = ? AND role = 'user' GROUP BY session_id ORDER BY MIN(created_at) DESC LIMIT 20", (user_id,))
    sessions = [{"session_id": r[0], "title": r[1][:30], "started_at": r[2]} for r in c.fetchall()]

    return jsonify({'status': 'success', 'data': {"session_id": session_id, "messages": messages, "sessions": sessions}})

@bp_chat.route('/new_session', methods=['POST'])
@login_required
def chat_new_session():
    return jsonify({'status': 'success', 'data': {"session_id": str(uuid.uuid4())}})

@bp_chat.route('/session/<session_id>', methods=['DELETE'])
@login_required
def chat_delete_session(session_id):
    user_id = get_current_user_id()
    db = get_db()
    db.execute("DELETE FROM chat_messages WHERE user_id = ? AND session_id = ?", (user_id, session_id))
    db.commit()
    return jsonify({'status': 'success', 'message': "Deleted"})
