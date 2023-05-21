from despot.despot import update_db
import json
import time

t = time.time()
db = json.load(open('/tmp/db.json','r'))
print(f"load: {time.time()-t}")
t = time.time()

t = time.time()
deleted_releases, modified_releases, new_scans = update_db(db)
print(f"update_db: {time.time()-t}")
t = time.time()

db["update_time"] = t
json.dump(
		db,
		open('/tmp/db_updated.json', 'w'),
		indent='\t',
		ensure_ascii=False)
print(f"dump: {time.time()-t}")

print(f"Deleted: {deleted_releases}")
print(f"Modified: {modified_releases}")
print(f"New: {new_scans.keys()}")
