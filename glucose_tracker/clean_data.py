import sqlite3
import os
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "glucose.db")

def clean_premeal_data():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # 1. Select all '运动后' records
    c.execute("SELECT id, timestamp FROM records WHERE type = '运动后'")
    rows = c.fetchall()
    
    print(f"Found {len(rows)} records to clean.")
    print("-" * 60)
    print(f"{'ID':<5} | {'Original Time':<20} | {'New Time'}")
    print("-" * 60)
    
    count = 0
    for r in rows:
        r_id = r[0]
        original_ts = r[1]
        
        try:
            # Parse the original timestamp string
            # Format usually: 'YYYY-MM-DD HH:MM:SS'
            dt = datetime.datetime.strptime(original_ts, "%Y-%m-%d %H:%M:%S")
            
            # Create new datetime with same date but time set to 08:45:00
            new_dt = dt.replace(hour=8, minute=45, second=0)
            new_ts = new_dt.strftime("%Y-%m-%d %H:%M:%S")
            
            # Update only if different
            if new_ts != original_ts:
                c.execute("UPDATE records SET timestamp = ? WHERE id = ?", (new_ts, r_id))
                print(f"{r_id:<5} | {original_ts:<20} | {new_ts}")
                count += 1
                
        except ValueError:
            print(f"Skipping ID {r_id}: Invalid timestamp format '{original_ts}'")
            
    conn.commit()
    conn.close()
    
    print("-" * 60)
    print(f"Successfully updated {count} records to 08:45:00.")

if __name__ == "__main__":
    clean_premeal_data()
