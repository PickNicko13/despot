from despot.library import gen_release_list, scan_release, calc_stats
from despot.whatever import wc_normalize_length
import json
import time
from os import path, get_terminal_size
from sys import argv

db = {}
db["root"] = argv[1] if len(argv) > 1 else path.join(path.dirname(path.realpath(__file__)),'library')

t = time.time()
release_list = gen_release_list(db["root"])
print(f"release_list: {time.time()-t}")
t = time.time()

db["releases"] = {}
for c, release in enumerate(release_list):
	print(
			wc_normalize_length(
				f"[{c+1}/{len(release_list)}] '{release}'",
				get_terminal_size().columns
			),
			end='\r'
	)
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
