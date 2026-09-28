import sqlite3

conn = sqlite3.connect('supportnova.db')
c = conn.cursor()
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(f"Total tables: {len(tables)}")
for t in sorted(tables):
    try:
        cnt = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {cnt} rows")
    except Exception as e:
        print(f"  {t}: {e}")
conn.close()
