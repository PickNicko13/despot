from despot.library import *
import os.path
from icu import Transliterator
from PIL import Image

# don't break if an image is truncated
#ImageFile.LOAD_TRUNCATED_IMAGES = True
# disable zipbomb protection (which prevents large images from loading
# and is not really a concern if it's in your own music library anyway)
Image.MAX_IMAGE_PIXELS = None

# images are saved as jpegs with quality 77
# it is because it looks like the telegram recompresses them with approximately this quality

# format the user-defined release format string with the appropriate data
def format_release_string(release_string: str, release: dict, track_separator: str = '. ') -> str:
	# init transliterator
	to_latin = Transliterator.createInstance("Any-Latin")
	# init other formatting data (subject to expansion)
	formatting_data = {
		'album': get_tag_fom_first_track(release, 'album'),
		'albumartist': get_tag_fom_first_track(release, 'albumartist'),
		'date': get_tag_fom_first_track(release, 'date'),
		'totaltracks': get_tag_fom_first_track(release, 'totaltracks'),
		'depth_rates': set(f'{track["depth"]}/{track["rate"]}' for track in release["tracks"].values() )
	}
	latin_tracklist = ""
	# stack up tracklist
	for track in release["tracks"].values():
		latin_tracklist += f'\n{track["tracknumber"][0]}{track_separator}\
				{to_latin.transliterate(track["title"][0])}'
	return release_string.format(**formatting_data, latin_tracklist=latin_tracklist)

# prepare the release artwork according to the telegram rules:
# height and width <= 2560px
# size <= 10M
# either PNG or JPEG
def prepare_artwork(img, path):
	if( os.path.getsize(img.filename)/1024/1024 > 10 or
			img.height > 2560 or img.width > 2560 or
			img.format not in ('PNG', 'JPEG') ):
		divider = img.width/2560 if img.width < img.height else img.height/2560
		img = img.resize(( int(img.width//divider), int(img.height//divider) ))
	img = img.convert('RGB')
	img.save(path, "jpeg", quality=77)

# prepare the thumbnail for the file according to the telegram rules:
# height and width <= 320px
# size <= 200K
def prepare_thumbnail(img, path):
	divider = img.width/320 if img.width < img.height else img.height/320
	img = img.resize(( int(img.width//divider), int(img.height//divider) ))
	img = img.convert('RGB')
	img.save(path, "jpeg", quality=77)

# choose the "best" artwork based on filename
def get_best_artwork(
			images: list[str],
			preferred_names: list[str],
			preferred_exts: list[str]
	):
	preferred_image = None
	max_score = float('-inf')
	# loop through all images and find the highest score
	for image_fname in images:
		name_score = 0
		extension_score = 0
		name, ext = path.splitext(image_fname)
		# Score based on name preferences
		for index, preference in enumerate(preferred_names):
			if preference == name.lower():
				name_score = len(preferred_names) - index
				break
		# Score based on extension preferences
		for index, preference in enumerate(preferred_exts):
			if preference == ext.lower():
				extension_score = len(preferred_exts) - index
				break
		image_score = name_score*1000 + extension_score
		# Update the preferred item if the current item has a higher score
		if image_score > max_score:
			preferred_image = image_fname
			max_score = image_score
	return preferred_image

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
