from sys import argv
from despot.despot import genTree
import json

json.dump(
		genTree(argv[1], 4),
		open('/tmp/test.json', 'w'),
		indent=4,
		ensure_ascii=False)
