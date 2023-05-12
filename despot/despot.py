import mutagen.id3
import mutagen.easyid3
import mutagen.apev2
import mutagen._file
import mutagen.mp4
import mutagen.easymp4
from PIL import Image, ImageFile
from natsort import natsorted
from os import path, scandir
import json

from datetime import datetime
from io import BytesIO
from pyrogram.client import Client
import re
import sys
import time

DEFAULT_CONFIG_PATH = path.join(path.realpath(__file__),'assets/default.json')
DB_ROOT = path.expanduser('~/.local/share/despot')
CONFIG_PATH = path.expanduser('~/.config/despot/config.json')
VERSION = '0.1'
MUSIC_EXTENSIONS = { # TODO
		"LOSSLESS": ['.flac','.alac','.dsf','.ape','.tak'],
		"LOSSY": ['.mp3','.opus','.aac'],
		"MIXED": ['.wv','.ac3', '.m4a', '.ogg', '.wma']
		}

# don't break if an image is truncated
ImageFile.LOAD_TRUNCATED_IMAGES = True
# disable zipbomb protection (which prevents large images from loading
# and is not really a concern if it's in your own music library anyway)
Image.MAX_IMAGE_PIXELS = None

# split tag by slash
def split_tags(metadata: dict):
	if "tracknumber" in metadata.keys():
		track_info = metadata['tracknumber'][0].split('/')
		metadata["tracknumber"] = [track_info[0]]
		if len(track_info) == 2:
			metadata["totaltracks"] = [track_info[1]]
	if "discnumber" in metadata.keys():
		disc_info = metadata['discnumber'][0].split('/')
		metadata["discnumber"] = [disc_info[0]]
		if len(disc_info) == 2:
			metadata["totaldiscs"] = [disc_info[1]]

def form_audio_blob(info, tags, entry_path):
	blob = {}
	blob["type"] = "music"
	blob["depth"] = info.bits_per_sample if hasattr(info, 'bits_per_sample') else 16
	blob["rate"] = info.sample_rate if hasattr(info, 'sample_rate') else 44100
	if hasattr(info, 'total_samples'):
		blob["samples"] = info.total_samples
	else:
		blob["samples"] = int( blob["rate"]*info.length )
	# id3 is a fucking retarded mess of a standard, so it requires some
	# work to make it conform with any adequate standard
	if isinstance(tags, mutagen.id3.ID3):
		# use EasyID3 to get the metadata in humanly form
		easy_tags = mutagen.easyid3.EasyID3(entry_path)
		blob["metadata"] = dict( easy_tags.items() )
		# then, try to split the tracknumber into tracknumber and totaltracks,
		# since ID3 spec thinks that it's a god idea to save them together
		split_tags(blob["metadata"])
	# APEv2 tags contain adequate key=value pairs, but they have slightly more standardized
	# values than Vorbis comments, so we only parse text values and external links.
	# Unfurtunately, they don't support floating point values, so ReplayGain values
	# are saved as text.
	# They also have the weird slash notation for disk and track numbering.
	# Another quirk is that their keys are case-sensitive, but it is strongly advised to
	# never use keys which only differ in case.
	elif isinstance(tags, mutagen.apev2.APEv2):
		blob["metadata"] = {}
		for key, item in tags.items():
			key = key.lower()
			if isinstance(item, (mutagen.apev2.APETextValue, mutagen.apev2.APEExtValue)):
				if key == "track":
					tracknumber_raw = item.value.split('/')
					blob["metadata"]["tracknumber"] = [tracknumber_raw[0]]
					if len(tracknumber_raw) == 2:
						blob["metadata"]["totaltracks"] = [tracknumber_raw[1]]
				elif key == "disc":
					discnumber_raw = item.value.split('/')
					blob["metadata"]["discnumber"] = [discnumber_raw[0]]
					if len(discnumber_raw) == 2:
						blob["metadata"]["totaldiscs"] = [discnumber_raw[1]]
				else:
					blob["metadata"][key] = [str(item.value)]
	elif isinstance(tags, mutagen.mp4.MP4Tags):
		tags = mutagen.easymp4.EasyMP4(entry_path).tags
		if tags is None:
			raise Exception(f"Could not open tags: '{entry_path}'")
		blob["metadata"] = dict( tags.items() )
		split_tags(blob["metadata"])
	else:
		blob["metadata"] = dict( tags.items() )
	blob["metadata"] = dict( natsorted(blob["metadata"].items()) )
	return blob


def gen_tree(root: str, print_depth: int = 0) -> dict:
	# initialize tree list
	tree: dict = {}
	# scan each entry in the given directory
	for entry in natsorted( scandir(root), key=lambda x: (x.is_dir(), x.name.lower()) ):
		# init the object that will hold entry data representation (metadata blob)
		entry_path = path.join(root, entry.name)
		blob: dict[str, str|float|list|dict] = { "mtime": path.getmtime(entry_path) }
		if entry.is_file():
			# try opening the file as audio
			try:
				file = mutagen._file.File(entry_path)
				if file is not None:
					tags = file.tags
					info = file.info
				else:
					tags = None
					info = None
				del file
			except Exception:
				# it should return None if the file is not an audio file,
				# so this is is highly unlikely, but, well, it's I/O
				print(f"Mutagen error on {entry_path}.")
				tags = None
				info = None
			# if mutagen opened the file, treat it as music
			if tags is not None and info is not None:
				blob.update( form_audio_blob(info, tags, entry_path) )
			# if mutagen didn't open it as audio, try opening it as an image
			else:
				try:
					Image.open(entry_path).verify()
					blob["type"] = "image"
				except Exception:
					blob["type"] = "file"
		elif entry.is_dir():
			if entry_path.count("/") == print_depth:
				print(f"Working in '{entry_path}'")
			blob["type"] = "dir"
			blob["children"] = gen_tree(entry_path, print_depth)
		else:
			raise Exception(f"Encountered a non-file and non-directory entry while scanning: \
					'{path.join(root, entry.name)}'.")
		tree[entry.name] = blob
	return dict(  natsorted( tree.items(), key=lambda x: (x[1]["type"], x[0]) )  )

# returns tuple in such form: (tree, updated_release_count)
def update_db(tree: dict, root: str, trust_mtime: bool = True) -> tuple[dict, int]:
	# scan each entry in the given directory
	for entry in natsorted( scandir(root), key=lambda x: (x.is_dir(), x.name.lower()) ):
		modified = False
		entry_path = path.join(root, entry.name)
		blob: dict[str, str|float|list|dict] = { "mtime": path.getmtime(entry_path) }
		if (	entry.name in tree.keys() and
				blob["mtime"] == tree[entry.name]["mtime"]
		):
			# if mtimes can be trusted, this is enough to prove that the file is the same
			if trust_mtime:
				continue
			# else, proceed with more thorough verification
			if entry.is_file():
				# try opening the file as audio
				try:
					file = mutagen._file.File(entry_path)
					if file is not None:
						tags = file.tags
						info = file.info
					else:
						tags = None
						info = None
					del file
				except Exception:
					# it should return None if the file is not an audio file,
					# so this is is highly unlikely, but, well, it's I/O
					print(f"Mutagen error on {entry_path}.")
					tags = None
					info = None
				# if mutagen opened the file, treat it as music
				if tags is not None and info is not None:
					blob.update( form_audio_blob(info, tags, entry_path) )
					# if formed blob is the same as the old one, file is the same
					if blob == tree[entry.name]:
						continue
					# else, set modified flag
					modified = True
				# if mutagen didn't open it as audio, always trust mtime
				else:
					continue
			elif entry.is_dir():
				blob["type"] = "dir"
				blob["children"] = update_db(tree, entry_path, trust_mtime)
			else:
				raise Exception(f"Encountered a non-file and non-directory entry while scanning: \
						'{path.join(root, entry.name)}'.")
		tree.append(blob)
	return natsorted(tree, key=lambda x: (x["type"], x["name"].lower()))


# find a list of tracks lacking metadata
def find_tracks_lacking_metadata(root: list[dict], tag: str) -> list[str]:
	missing: list[str] = []
	for entry in root:
		if entry["type"] == "dir":
			for new_missing in find_tracks_lacking_metadata(entry["children"], tag):
				missing.extend( path.join(entry["name"], new_missing) )
		elif entry["type"] == "music":
			if tag not in entry["metadata"].keys():
				missing.append( entry["name"] )
	return missing

def sum_track_counts(main: dict, new: dict[str|int, int|dict]):
	for key, value in new.items():
		if isinstance(value, dict):
			sum_track_counts(main[key],value)
		else:
			if key in main.keys():
				main[key] += value
			else:
				main[key] = value

def tree_stats(root: list[dict],
				critical_metadata: list[str] = [],
				wanted_metadata: list[str] = []) -> dict:
	statistics = {
		"max_peak": {
			"track": 0.0,
			"album": 0.0
		},
		"track_counts": {
			"total": 0,
			"clipping": 0,
			"uploaded": {
				"normal": 0,
				"opus": 0
			},
			"extension": {},
			"depth": {},
			"rate": {},
			"lacking_metadata": {
				"critical": 0,
				"wanted": 0
			}
		}
	}

	for entry in root:
		# if directory, recurse and max/add the values
		if entry["type"] == "dir":
			child_stats = tree_stats(entry["children"], critical_metadata, wanted_metadata)
			statistics["max_peak"]["track"] = max(
					child_stats["max_peak"]["track"],
					statistics["max_peak"]["track"])
			statistics["max_peak"]["album"] = max(
					child_stats["max_peak"]["album"],
					statistics["max_peak"]["album"])
			sum_track_counts(statistics["track_counts"], child_stats["track_counts"])
		# if music, work on it
		elif entry["type"] == "music":
			# init variables
			metadata = entry["metadata"]
			peak = 0.0
			# get peak values, compensate them for gain and save the max value
			if ( "replaygain_track_peak" in metadata.keys()
					and "replaygain_track_gain" in metadata.keys() ):
				peak = float( metadata["replaygain_track_peak"][0] )
				db_gain = float( metadata["replaygain_track_gain"][0].lower().
					removesuffix('db').removesuffix('lufs') )
				if db_gain != float('inf'):
					peak *= 10**(db_gain/20)
					statistics["max_peak"]["track"] = max( peak, statistics["max_peak"]["track"] )
			if ( "replaygain_album_peak" in metadata.keys()
					and "replaygain_album_gain" in metadata.keys() ):
				peak = float( metadata["replaygain_album_peak"][0] )
				db_gain = float( metadata["replaygain_album_gain"][0].lower().
					removesuffix('db').removesuffix('lufs') )
				if db_gain != float('inf'):
					peak *= 10**(db_gain/20)
					statistics["max_peak"]["album"] = max( peak, statistics["max_peak"]["album"] )
			# increment track count
			statistics["track_counts"]["total"] += 1
			# classify track as clipping if peak over 1
			statistics["track_counts"]["clipping"] += (peak > 1.0)
			# classify track as uploaded if links exist
			if "links" in entry.keys():
				statistics["track_counts"]["clipping"] += ("normal" in entry["links"])
				statistics["track_counts"]["clipping"] += ("opus" in entry["links"])
			# classify track by extension
			ext = path.splitext(entry["name"])[1].lower()
			if ext in statistics["track_counts"]["extension"].keys():
				statistics["track_counts"]["extension"][ext] += 1
			else:
				statistics["track_counts"]["extension"][ext] = 1
			del ext
			# classify track by bit depth
			if entry["depth"] in statistics["track_counts"]["depth"].keys():
				statistics["track_counts"]["depth"][entry["depth"]] += 1
			else:
				statistics["track_counts"]["depth"][entry["depth"]] = 1
			# classify track by sampling rate
			if entry["rate"] in statistics["track_counts"]["rate"].keys():
				statistics["track_counts"]["rate"][entry["rate"]] += 1
			else:
				statistics["track_counts"]["rate"][entry["rate"]] = 1
			# classify track by lacking metadata
			if any( data not in metadata.keys() for data in critical_metadata ):
				statistics["track_counts"]["lacking_metadata"]["critical"] += 1
			if any( data not in metadata.keys() for data in wanted_metadata ):
				statistics["track_counts"]["lacking_metadata"]["wanted"] += 1
	return statistics

class Despot:
	def __init__( self, api_id: str|None = None, api_hash: str|None = None ) -> None:
		if path.isfile(CONFIG_PATH):
			self.loadConfig()
		elif api_id is not None or api_hash is not None:
			self.initConfig(api_id, api_hash)
		else:
			raise Exception("Existing config not found and API data not given.")

	def __del__(self):
		self.closeConnections()
		self.saveConfig()
		self.saveLibrary()

	def loadConfig(self) -> None:
		self.config = json.load( open(CONFIG_PATH, 'r') )

	def saveConfig(self) -> None:
		json.dump(self.config, open(CONFIG_PATH, 'w'), indent=4, ensure_ascii=False)
	
	def initConfig(self, api_id, api_hash) -> None:
		self.config: dict = json.load( open(DEFAULT_CONFIG_PATH, 'r') )
		self.config['api_id'] = api_id
		self.config['api_hash'] = api_hash
		self.saveConfig()

	def findMissingConf(self) -> list[str]:
		missing: list[str] = []
		for value in 'api_id', 'api_hash', 'library_root':
			if self.config[value] is None:
				missing.append(value)
		return missing

	def isClientInitiated(self) -> bool:
		return isinstance(self.client, Client)

	def initClient(self, db_path: str):
		self.client = Client(db_path)

	def initDB(self):
		if isinstance(self.config['library_root'], str):
			raise Exception("Library root not set.")
		if not path.isdir(self.config['library_root']):
			raise Exception("The specified library root is not a directory.")

		db: dict[str, list|dict|float] = dict()
		db["tree"] = gen_tree(self.config['library_root'])
		db["statistics"] = tree_stats(db["tree"])
		db["update_time"] = time.time()
		db["version"] = VERSION


	## STUBS
	def saveLibrary(self):
		return
	def closeConnections(self):
		return
