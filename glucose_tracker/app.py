from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file
import sqlite3
import pandas as pd
import datetime
import os
from parser import parse_glucose_input

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "glucose.db")

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS records
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  value REAL, 
                  unit TEXT, 
                  type TEXT, 
                  notes TEXT, 
                  timestamp DATETIME,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM records ORDER BY timestamp DESC")
    records = c.fetchall()
    conn.close()
    return render_template('index.html', records=records)

@app.route('/add', methods=['POST'])
def add_record():
    value = request.form.get('value')
    unit = request.form.get('unit')
    r_type = request.form.get('type')
    notes = request.form.get('notes')
    timestamp = request.form.get('timestamp')
    
    # Handle empty timestamp (default to now)
    if not timestamp:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # If HTML5 datetime-local input is used, it comes as 'YYYY-MM-DDTHH:MM', needs simple cleanup if we want consistent format, but let's store as is or standardized.
    # Let's standardize to YYYY-MM-DD HH:MM:SS for SQLite sortability
    if 'T' in timestamp:
        timestamp = timestamp.replace('T', ' ')
        if len(timestamp) == 16: # Missing seconds
            timestamp += ':00'

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO records (value, unit, type, notes, timestamp) VALUES (?, ?, ?, ?, ?)",
              (value, unit, r_type, notes, timestamp))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/parse_ai', methods=['POST'])
def parse_ai():
    text = request.json.get('text')
    if not text:
        return jsonify([])
    
    results = parse_glucose_input(text)
    return jsonify(results)

@app.route('/batch_add', methods=['POST'])
def batch_add():
    data = request.json.get('records')
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    for r in data:
        c.execute("INSERT INTO records (value, unit, type, notes, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (r['value'], r['unit'], r['type'], r['notes'], r['datetime']))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/delete/<int:id>')
def delete(id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM records WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/export')
def export():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM records ORDER BY timestamp DESC", conn)
    conn.close()
    
    export_path = "glucose_records.csv"
    df.to_csv(export_path, index=False, encoding='utf-8-sig')
    return send_file(export_path, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5001)
