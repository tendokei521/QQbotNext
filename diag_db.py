# 临时诊断脚本：查询正式数据库内容
import json
import sqlite3

conn = sqlite3.connect("data/app.db")
cur = conn.cursor()

print("=== bots 表 ===")
for row in cur.execute("SELECT bot_index, ws_url, owner_id, auto_connect FROM bots ORDER BY bot_index"):
    print(row)

print("\n=== module_config 表（module, bot_id 一览）===")
for row in cur.execute("SELECT module_name, bot_id, length(config_json) FROM module_config"):
    print(row)

print("\n=== module_authority 表（module, bot_id, enabled）===")
for row in cur.execute("SELECT module_name, bot_id, authority_json FROM module_authority"):
    j = row[2] or ""
    try:
        enabled = json.loads(j).get("enabled")
    except Exception:
        enabled = "?"
    print(row[0], row[1], "enabled=", enabled)

print("\n=== agent 配置的 api_key/model ===")
for row in cur.execute("SELECT bot_id, config_json FROM module_config WHERE module_name='agent'"):
    cfg = json.loads(row[1] or "{}")
    print("bot_id=", row[0], "| api_key=", repr(cfg.get("api_key", ""))[:24], "| model=", cfg.get("model"), "| group_enable=", cfg.get("group_enable"))

conn.close()
