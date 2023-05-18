from sys import argv
from despot.despot import gen_release_list, scan_release, calc_stats
import json
import time

db = {}
db["root"] = '/home/pn13/repos/despot/test/library'

t = time.time()
release_list = gen_release_list(db["root"])
print(f"release_list: {time.time()-t}")
t = time.time()


db["releases"] = {}
for release in release_list:
	print(f"scanning: '{release}'")
	db["releases"][release] = scan_release(release)
print(f"db['releases']: {time.time()-t}")
t = time.time()

db["statistics"] = calc_stats(db["releases"])
print(f"db['statistics']: {time.time()-t}")
t = time.time()

db["update_time"] = t
json.dump(
		db,
		open('/tmp/db.json', 'w'),
		indent='\t',
		ensure_ascii=False)
print(f"dump: {time.time()-t}")
