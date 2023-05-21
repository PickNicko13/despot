import mutagen._file
import mutagen._vorbis
import mutagen.apev2
import mutagen.asf
import mutagen.easyid3
import mutagen.easymp4
import mutagen.id3
import mutagen.mp4
from PIL import Image, ImageFile
from natsort import natsorted
from os import path, scandir, makedirs
import json
from wcmatch import wcmatch
import zstandard
from datetime import datetime
import time

from io import BytesIO
from pyrogram.client import Client
import re
import sys

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

# a simple backup function
def backup_db(db: dict, root: str):
	makedirs(root, exist_ok=True)
	with open(path.join(root,f"{datetime.today()}.zstd"), 'wb') as file:
		file.write( zstandard.compress(json.dumps(db, ensure_ascii=False).encode()) )

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

# form a blob with audio file information
def form_audio_blob(mutafile, entry_path):
	info = mutafile.info
	tags = mutafile.tags
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
		# detect embedded image
		blob["embedded_image"] = 'APIC:' in tags.keys()
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
		blob["embedded_image"] = "cover art (front)" in tags
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
		blob["embedded_image"] = "covr" in tags
		tags = mutagen.easymp4.EasyMP4(entry_path).tags
		if tags is None:
			raise Exception(f"Could not open tags: '{entry_path}'")
		blob["metadata"] = dict( tags.items() )
		split_tags(blob["metadata"])
	elif isinstance(tags, mutagen._vorbis.VCommentDict):
		blob["embedded_image"] = "metadata_block_picture" in tags
		blob["metadata"] = dict( tags.items() )
	elif isinstance(tags, mutagen.asf.ASFTags):
		blob["embedded_image"] = "WM/Picture" in tags
		blob["metadata"] = {}
		for key, item in tags.items():
			key = key.lower()
			if not isinstance(item, mutagen.asf._attrs.ASFByteArrayAttribute):
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
	elif tags is not None:
		blob["embedded_image"] = len(mutafile.pictures) > 0 if hasattr(mutafile, 'pictures') else False
		blob["metadata"] = dict( tags.items() )
	blob["metadata"] = dict( natsorted(blob["metadata"].items()) )
	return blob

# generate a list of releases nested under some directory
def gen_release_list(root: str) -> list[str]:
	return natsorted(set( path.dirname(file) for file in wcmatch.WcMatch(
				root,
				'|'.join(['*.'+ext for ext_list in MUSIC_EXTENSIONS.values() for ext in ext_list]),
				flags=wcmatch.RECURSIVE|wcmatch.IGNORECASE|wcmatch.SYMLINKS
			).match() ))

# generate a release dict for the given directory
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
				mutafile = mutagen._file.File(entry_path)
			except Exception:
				mutafile = None
				# it should return None if the file is not an audio file,
				# so this is is highly unlikely, but, well, it's I/O
				print(f"Mutagen error on {entry_path}.")
			# if mutagen opened the file, treat it as music
			if mutafile is not None:
				blob.update( form_audio_blob(mutafile, entry_path) )
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

# returns tuple in such form: (deleted_releases, modified_releases, new_scans)
def update_db(db: dict, trust_mtime: bool = True) -> tuple[list[str],list[str],dict]:
	# generate fresh release list and get the old one
	release_list = gen_release_list(db["root"])
	old_release_list = db["releases"].keys()
	# init new lists
	# note that "modified_releases" is more like "either modified or not modified releases"
	# this list will further be shrunk
	new_releases, deleted_releases, modified_releases = comm(release_list, old_release_list)
	unmodified_releases = []
	# detect new and remove unmodified releases from "modified_releases"
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
			unmodified_releases.append(release)
	for release in unmodified_releases:
		modified_releases.remove(release)
	del unmodified_releases
	new_scans = {}
	for release in new_releases:
		new_scans[release] = scan_release(release)
	del new_releases
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
def find_tracks_lacking_tag(releases: dict, tag: str) -> dict[str,list[str]]:
	found: dict = {}
	for release_path, value in releases.items():
		for track_name, track in value["tracks"].items():
			if tag not in track["metadata"].keys():
				if release_path not in found.keys():
					found[release_path] = []
				found[release_path].append( path.join(release_path, track_name) )
	return found

# find releases with differring extensions in a single directory
def find_multi_ext_releases(releases: dict) -> list[str]:
	found: list[str] = []
	for release_path, value in releases.items():
		extensions = []
		for track_name in value["tracks"].keys():
			ext = path.splitext(track_name)[1]
			if ext not in extensions:
				extensions.append(ext)
				if len(extensions) > 1:
					found.append( release_path )
					break
	return found

# find releases with differring tag in a single directory
def find_multi_tag_releases(releases: dict, tag: str) -> list[str]:
	found: list[str] = []
	for release_path, value in releases.items():
		different = []
		for track_name, track in value["tracks"].items():
			if tag in track["metadata"] and track["metadata"][tag][0] not in different:
				different.append(track["metadata"][tag][0])
				if len(different) > 1:
					found.append( release_path )
					break
	return found

# find tracks which clip in either track or album gain mode
# returns two dicts in such form:
#	track_path: peak_value
# first dict is for track compensated peaks, second is for the album compensated track peaks
def find_clipping_tracks(releases: dict) -> tuple[dict[str,float], dict[str,float]]:
	clip_album: dict = {}
	clip_track: dict = {}
	for release_path, value in releases.items():
		for track_name, track in value["tracks"].items():
			if ( "replaygain_track_peak" in track["metadata"].keys()
					and "replaygain_album_gain" in track["metadata"].keys() ):
				peak = float( track["metadata"]["replaygain_album_peak"][0] )
				db = float( track["metadata"]["replaygain_album_gain"][0].lower().
					removesuffix('db').removesuffix('lufs') )
				if db != float('inf'):
					peak *= db_gain(db)
					if peak > 1:
						clip_album[path.join(release_path,track_name)] = peak
			if ( "replaygain_track_peak" in track["metadata"].keys()
					and "replaygain_track_gain" in track["metadata"].keys() ):
				peak = float( track["metadata"]["replaygain_track_peak"][0] )
				db = float( track["metadata"]["replaygain_track_gain"][0].lower().
					removesuffix('db').removesuffix('lufs') )
				if db != float('inf'):
					peak *= db_gain(db)
					if peak > 1:
						clip_track[path.join(release_path,track_name)] = peak
	return ( dict(sorted(clip_track.items(), key=lambda x:x[1])),
				dict(sorted(clip_album.items(), key=lambda x:x[1])) )

# calculate statistics for the given database
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
