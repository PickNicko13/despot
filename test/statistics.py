from sys import argv
from despot.library import tree_stats
import json
from pprint import pprint
import time

t = time.time()

tree = json.load(open('/tmp/test.json', 'r'))

print(f"json loading time: {time.time()-t}")
t = time.time()

if len(argv) > 1:
	print(f"Critical tag: {argv[1]}")
	pprint( tree_stats(tree, [argv[1]], []) )
else:
	pprint( tree_stats(tree, [], []) )

print(f"stat time: {time.time()-t}")
