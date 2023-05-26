from despot.library import *
from icu import Transliterator

def format_release_string(release_string: str, release: dict, track_separator: str = '. ') -> str:
	to_latin = Transliterator.createInstance("Any-Latin")

	formatting_data = {
			'album': get_tag_fom_first_track(release, 'album'),
			'albumartist': get_tag_fom_first_track(release, 'albumartist'),
			'date': get_tag_fom_first_track(release, 'date'),
			'totaltracks': get_tag_fom_first_track(release, 'totaltracks'),
			'depth_rates': set(f'{track["depth"]}/{track["rate"]}' for track in release["tracks"].values() )
			}
	latin_tracklist = ""
	for track in release["tracks"].values():
		latin_tracklist += f'\n{track["tracknumber"][0]}{track_separator}\
				{to_latin.transliterate(track["title"][0])}'
	return release_string.format(**formatting_data, latin_tracklist=latin_tracklist)

# class Despot:
# 	def __init__( self, api_id: str|None = None, api_hash: str|None = None ) -> None:
# 		if path.isfile(CONFIG_PATH):
# 			self.loadConfig()
# 		elif api_id is not None or api_hash is not None:
# 			self.initConfig(api_id, api_hash)
# 		else:
# 			raise Exception("Existing config not found and API data not given.")
#
# 	def __del__(self):
# 		self.closeConnections()
# 		self.saveConfig()
# 		self.saveLibrary()
#
# 	def loadConfig(self) -> None:
# 		self.config = json.load( open(CONFIG_PATH, 'r') )
#
# 	def saveConfig(self) -> None:
# 		json.dump(self.config, open(CONFIG_PATH, 'w'), indent=4, ensure_ascii=False)
# 	
# 	def initConfig(self, api_id, api_hash) -> None:
# 		self.config: dict = json.load( open(DEFAULT_CONFIG_PATH, 'r') )
# 		self.config['api_id'] = api_id
# 		self.config['api_hash'] = api_hash
# 		self.saveConfig()
#
# 	def findMissingConf(self) -> list[str]:
# 		missing: list[str] = []
# 		for value in 'api_id', 'api_hash', 'library_root':
# 			if self.config[value] is None:
# 				missing.append(value)
# 		return missing
#
# 	def isClientInitiated(self) -> bool:
# 		return isinstance(self.client, Client)
#
# 	def initClient(self, db_path: str):
# 		self.client = Client(db_path)
#
# 	def initDB(self):
# 		if isinstance(self.config['library_root'], str):
# 			raise Exception("Library root not set.")
# 		if not path.isdir(self.config['library_root']):
# 			raise Exception("The specified library root is not a directory.")
#
# 		db: dict[str, list|dict|float|str] = dict()
# 		releases = gen_release_list(self.config['library_root'])
# 		db["tree"] = gen_release_list(self.config['library_root'])
# 		db["statistics"] = calc_stats(db["tree"])
# 		db["update_time"] = time.time()
# 		db["version"] = VERSION
#
#
# 	## STUBS
# 	def saveLibrary(self):
# 		return
# 	def closeConnections(self):
# 		return
