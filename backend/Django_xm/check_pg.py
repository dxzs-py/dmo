import psycopg

conn = psycopg.connect(
    host="127.0.0.1", port=5432,
    user="daixing", password="daixing241212",
    dbname="langchain"
)
cur = conn.cursor()

cur.execute("SHOW max_connections;")
mc = cur.fetchone()[0]

cur.execute("SELECT count(*), state FROM pg_stat_activity GROUP BY state ORDER BY count DESC;")
states = cur.fetchall()

cur.execute("SELECT application_name, usename, state, count(*) FROM pg_stat_activity GROUP BY application_name, usename, state ORDER BY count DESC LIMIT 15;")
apps = cur.fetchall()

cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'idle' OR state = 'idle in transaction';")
idle = cur.fetchone()[0]

cur.execute("SELECT pid, application_name, usename, state, query_start, state_change FROM pg_stat_activity WHERE state = 'idle in transaction' ORDER BY state_change ASC LIMIT 10;")
idle_txns = cur.fetchall()

print(f"max_connections = {mc}")
print(f"idle_conns = {idle}")
print("\n=== states ===")
for s in states:
    print(f"  {s[1] or 'NULL'}: {s[0]}")
print("\n=== apps ===")
for a in apps:
    print(f"  app={a[0] or 'NULL'}, user={a[1]}, state={a[2]}, count={a[3]}")
print("\n=== idle in transaction (oldest 10) ===")
for t in idle_txns:
    print(f"  pid={t[0]}, app={t[1]}, user={t[2]}, state={t[3]}, query_start={t[4]}, state_change={t[5]}")

conn.close()
