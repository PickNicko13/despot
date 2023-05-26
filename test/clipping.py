from despot.library import find_clipping_tracks
import json
import time

t = time.time()
db = json.load(open('/tmp/db.json','r'))
print(f"load: {time.time()-t}")
t = time.time()

t = time.time()
clipping = {}
clipping["track"], clipping["album"] = find_clipping_tracks(db["releases"])
print(f"Clipping track count (using album gain):\
	{len(clipping['album'])} ({len(clipping['album'])/db['statistics']['total']:1.3})")
print(f"Clipping track count (using track gain):\
	{len(clipping['track'])} ({len(clipping['track'])/db['statistics']['total']:1.3})")
print(f"clipping detection: {time.time()-t}")
t = time.time()

db["update_time"] = t
json.dump(
		clipping,
		open('/tmp/clipping.json', 'w'),
		indent='\t',
		ensure_ascii=False)
print(f"dump: {time.time()-t}")
