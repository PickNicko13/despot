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
from wcmatch import wcmatch

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
		"LOSSLESS": ['flac','alac','dsf','ape','tak'],
		"LOSSY": ['mp3','opus','aac'],
		"MIXED": ['wv','ac3', 'm4a', 'ogg', 'wma']
		}

# don't break if an image is truncated
ImageFile.LOAD_TRUNCATED_IMAGES = True
# disable zipbomb protection (which prevents large images from loading
# and is not really a concern if it's in your own music library anyway)
Image.MAX_IMAGE_PIXELS = None

# get 3 lists similar to comm utility:
# unique to list1,
# unique to list2,
# common
def comm(list1, list2):
	set1 = set(list1)
	set2 = set(list2)
	return list(set1 - set2), list(set2 - set1), list(set1 & set2)

# decibels to absolute value value
def db_gain(db: float):
	return 10**(db/20)

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

def gen_release_list(root: str) -> list[str]:
	return natsorted(set( path.dirname(file) for file in wcmatch.WcMatch(
				root,
				'|'.join(['*.'+ext for ext_list in MUSIC_EXTENSIONS.values() for ext in ext_list]),
				flags=wcmatch.RECURSIVE|wcmatch.IGNORECASE|wcmatch.SYMLINKS
			).match() ))

def scan_release(release_path: str, mtime_only: bool = False) -> dict:
	release = {} if mtime_only else {
										"tracks": {},
										"images": {},
										"files": {}
									}
	# scan each entry in the given directory
	for entry in natsorted(
				scandir(release_path),
				key=lambda x: (x.is_dir(), x.name.lower())
		):
		if entry.is_file():
			# init the object that will hold entry data representation (metadata blob)
			entry_path = path.join(release_path, entry.name)
			blob: dict[str, str|float|list|dict] = { "mtime": path.getmtime(entry_path) }
			# if scanning only for mtimes, use simplex method
			if mtime_only:
				release[entry.name] = blob
				continue
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
				release["tracks"][entry.name] = blob
			# if mutagen didn't open it as audio, try opening it as an image
			else:
				try:
					Image.open(entry_path).verify()
					release["images"][entry.name] = blob
				except Exception:
					release["files"][entry.name] = blob
	for ftype in release:
		if ftype == {}:
			release.pop(ftype)
	return release

def find_similar_release(releases: dict, release_src: dict) -> str|None:
	release = release_src
	if 'link_orig' in release.keys():
		release.pop('link_orig')
	if 'link_opus' in release.keys():
		release.pop('link_opus')
	key = [k for k, v in releases.items() if v == release]
	if len(key) == 1:
		return key[0]

# returns tuple in such form: (tree, updated_release_count)
def update_db(db: dict, trust_mtime: bool = True) -> tuple[list[str],list[str],dict]:
	# generate fresh release list and get the old one
	release_list = gen_release_list(db["root"])
	old_release_list = db["releases"].keys()
	# init new lists
	# note that "modified_releases" is more like "either modified or not modified releases"
	# this list will further be shrunk
	new_releases, deleted_releases, modified_releases = comm(release_list, old_release_list)
	# detect new and modified releases
	for release in modified_releases:
		if trust_mtime:
			files = scan_release(release, mtime_only=True)
			old_files = dict(
					(name,{"mtime":data["mtime"]})
					for filetype in ("tracks","images","files")
					for name,data in db["releases"][release][filetype].items()
			)
		else:
			files = scan_release(release)
			old_files = dict(
					(name,data)
					for filetype in ("tracks","images","files")
					for name,data in db["releases"][release][filetype].items()
			)
		# if file list and corresponding data is the same,
		# release hasn't changed, so remove it from modified
		if files == old_files:
			modified_releases.remove(release)
	new_scans = {}
	for release in new_releases:
		new_scans[release] = scan_release(release)
	del new_releases
	print(f"Deleted: {deleted_releases}\n")
	### at this point all the release data is correct and usable
	# try finding "new" releases exactly the same as a "deleted" releases
	delete = {}
	for release in deleted_releases:
		key = find_similar_release(new_scans, db["releases"][release])
		if key is not None:
			delete[key] = release
	for key,release in delete.items():
		db["releases"][key] = db["releases"].pop(release)
		deleted_releases.remove(release)
		new_scans.pop(key)
		print(f"Moved '{release}' to '{key}'")
	db["releases"].update(new_scans)
	db["statistics"] = calc_stats(db["releases"])
	return deleted_releases, modified_releases, new_scans

# find a list of tracks lacking metadata
def find_tracks_lacking_metadata(releases: dict, tag: str) -> list[str]:
	missing: list[str] = []
	for release_path, value in releases.items():
		for track_name, track in value["tracks"].items():
			if tag not in track["metadata"].keys():
				missing.append( path.join(release_path, track_name) )
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

def calc_stats(releases: dict,
				critical_metadata: list[str] = [],
				wanted_metadata: list[str] = []) -> dict:
	statistics = {
		"max_track_peak": 0.0,
		"max_album_peak": 0.0,
		"track_counts": {
			"total": 0,
			"clipping": 0,
			"uploaded_orig": 0,
			"uploaded_opus": 0,
			"extension": {},
			"depth": {},
			"rate": {},
			"lacking_metadata": {
				"critical": 0,
				"wanted": 0
			}
		}
	}

	for release in releases.values():
		# add release track count to total track count
		statistics["track_counts"]["total"] += len(release["tracks"])
		for track_name, track in release["tracks"].items():
			# init variables
			metadata = track["metadata"]
			peak = 0.0
			# get peak values, compensate them for gain and save the max value
			if ( "replaygain_track_peak" in metadata.keys()
					and "replaygain_track_gain" in metadata.keys() ):
				peak = float( metadata["replaygain_track_peak"][0] )
				db = float( metadata["replaygain_track_gain"][0].lower().
					removesuffix('db').removesuffix('lufs') )
				if db != float('inf'):
					peak *= db_gain(db)
					statistics["max_peak"]["track"] = max( peak, statistics["max_peak"]["track"] )
			if ( "replaygain_album_peak" in metadata.keys()
					and "replaygain_album_gain" in metadata.keys() ):
				peak = float( metadata["replaygain_album_peak"][0] )
				db = float( metadata["replaygain_album_gain"][0].lower().
					removesuffix('db').removesuffix('lufs') )
				if db != float('inf'):
					peak *= db_gain(db)
					statistics["max_peak"]["album"] = max( peak, statistics["max_peak"]["album"] )
			# classify track as clipping if peak over 1
			statistics["track_counts"]["clipping"] += (peak > 1.0)
			# classify track as uploaded if links exist
			if "link_orig" in track.keys():
				statistics["track_counts"]["uploaded_orig"] += 1
			if "link_opus" in track.keys():
				statistics["track_counts"]["uploaded_opus"] += 1
			# classify track by extension
			ext = path.splitext(track_name)[1].lower()
			if ext in statistics["track_counts"]["extension"].keys():
				statistics["track_counts"]["extension"][ext] += 1
			else:
				statistics["track_counts"]["extension"][ext] = 1
			# classify track by bit depth
			if track["depth"] in statistics["track_counts"]["depth"].keys():
				statistics["track_counts"]["depth"][track["depth"]] += 1
			else:
				statistics["track_counts"]["depth"][track["depth"]] = 1
			# classify track by sampling rate
			if track["rate"] in statistics["track_counts"]["rate"].keys():
				statistics["track_counts"]["rate"][track["rate"]] += 1
			else:
				statistics["track_counts"]["rate"][track["rate"]] = 1
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

		db: dict[str, list|dict|float|str] = dict()
		releases = gen_release_list(self.config['library_root'])
		db["tree"] = gen_release_list(self.config['library_root'])
		db["statistics"] = calc_stats(db["tree"])
		db["update_time"] = time.time()
		db["version"] = VERSION


	## STUBS
	def saveLibrary(self):
		return
	def closeConnections(self):
		return
