import sqlite3
import os

# Connect to database
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "glucose.db")

def check_timestamps():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Query for records with type '运动后餐前'
    c.execute("""
        SELECT id, timestamp, value, type, notes, created_at 
        FROM records 
        WHERE type = '运动后餐前' 
        ORDER BY timestamp DESC
    """)
    
    rows = c.fetchall()
    
    print(f"{'ID':<5} | {'Event Time (Timestamp)':<20} | {'Value':<6} | {'Created At (System Time)':<20} | {'Notes'}")
    print("-" * 100)
    
    for r in rows:
        r_id, timestamp, value, r_type, notes, created_at = r
        # Handle None values for display
        created_at = created_at if created_at else "N/A"
        notes = (notes[:30] + '...') if notes and len(notes) > 30 else notes
        
        print(f"{r_id:<5} | {timestamp:<20} | {value:<6} | {created_at:<20} | {notes}")

    conn.close()

if __name__ == "__main__":
    check_timestamps()
