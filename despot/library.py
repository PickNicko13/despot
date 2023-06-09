import json
import mutagen._file
import mutagen._vorbis
import mutagen.apev2
import mutagen.asf
import mutagen.easyid3
import mutagen.easymp4
import mutagen.id3
import mutagen.mp4
import zstandard
from PIL import Image
from copy import deepcopy
from datetime import datetime
from natsort import natsorted
from os import path, scandir, makedirs
from time import time
from typing import Callable
from wcmatch import wcmatch

VERSION = '0.1'
OVERRIDDEN_FILE_EXTENSIONS = ('mid','midi','mov','webp')
MUSIC_EXTENSIONS = { # TODO
		"LOSSLESS": ('flac','alac','dsf','ape','tak'),
		"LOSSY": ('mp3','opus','aac'),
		"MIXED": ('wv','ac3', 'm4a', 'ogg', 'wma')
		}

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

# a simple save function
def save_db(db: dict, db_path: str):
	makedirs(path.dirname(db_path), exist_ok=True)
	with open(db_path, 'wb') as file:
		file.write( zstandard.compress(json.dumps(db, ensure_ascii=False, indent='\t').encode()) )

# a simple load function
def load_db(db_path: str) -> dict:
	with open(db_path, 'rb') as file:
		return json.loads(zstandard.decompress(file.read()))

# a simple backup function
def backup_db(db: dict, root: str):
	makedirs(root, exist_ok=True)
	with open(path.join(root,f"{datetime.today()}.zstd"), 'wb') as file:
		file.write( zstandard.compress(json.dumps(db, ensure_ascii=False, indent='\t').encode()) )

# split tag by slash
def split_tags(tags: dict):
	if "tracknumber" in tags.keys():
		track_info = tags['tracknumber'][0].split('/')
		tags["tracknumber"] = [track_info[0]]
		if len(track_info) == 2:
			tags["totaltracks"] = [track_info[1]]
	if "discnumber" in tags.keys():
		disc_info = tags['discnumber'][0].split('/')
		tags["discnumber"] = [disc_info[0]]
		if len(disc_info) == 2:
			tags["totaldiscs"] = [disc_info[1]]

# form a blob with audio file information
def form_audio_blob(mutafile):
	info = mutafile.info
	tags = mutafile.tags
	blob = {}
	blob["depth"] = info.bits_per_sample if hasattr(info, 'bits_per_sample') else 16
	blob["rate"] = info.sample_rate if hasattr(info, 'sample_rate') else 44100
	blob["length"] = info.length
	if hasattr(info, 'total_samples'):
		blob["samples"] = info.total_samples
	else:
		blob["samples"] = int( blob["rate"]*info.length )
	# id3 is a fucking retarded mess of a standard, so it requires some
	# work to make it conform with any adequate standard
	if isinstance(tags, mutagen.id3.ID3):
		# detect embedded image
		blob["embedded_image"] = 'APIC:' in tags.keys()
		# use EasyID3 to get the tags in humanly form
		easy_tags = mutagen.easyid3.EasyID3(mutafile.filename)
		blob["tags"] = dict( easy_tags.items() )
		# then, try to split the tracknumber into tracknumber and totaltracks,
		# since ID3 spec thinks that it's a god idea to save them together
		split_tags(blob["tags"])
	# APEv2 tags contain adequate key=value pairs, but they have slightly more standardized
	# values than Vorbis comments, so we only parse text values and external links.
	# Unfurtunately, they don't support floating point values, so ReplayGain values
	# are saved as text.
	# They also have the weird slash notation for disk and track numbering.
	# Another quirk is that their keys are case-sensitive, but it is strongly advised to
	# never use keys which only differ in case.
	elif isinstance(tags, mutagen.apev2.APEv2):
		blob["embedded_image"] = any(
				k.lower().startswith("cover art") and v.kind == mutagen.apev2.BINARY
				for k,v in tags.items()
		)
		blob["tags"] = {}
		for key, item in tags.items():
			key = key.lower()
			if isinstance(item, (mutagen.apev2.APETextValue, mutagen.apev2.APEExtValue)):
				if key == "track":
					tracknumber_raw = item.value.split('/')
					blob["tags"]["tracknumber"] = [tracknumber_raw[0]]
					if len(tracknumber_raw) == 2:
						blob["tags"]["totaltracks"] = [tracknumber_raw[1]]
				elif key == "disc":
					discnumber_raw = item.value.split('/')
					blob["tags"]["discnumber"] = [discnumber_raw[0]]
					if len(discnumber_raw) == 2:
						blob["tags"]["totaldiscs"] = [discnumber_raw[1]]
				else:
					blob["tags"][key] = [str(item.value)]
	elif isinstance(tags, mutagen.mp4.MP4Tags):
		blob["embedded_image"] = "covr" in tags
		tags = mutagen.easymp4.EasyMP4(mutafile.filename).tags
		if tags is None:
			raise Exception(f"Could not open tags: '{mutafile.filename}'")
		blob["tags"] = dict( tags.items() )
		split_tags(blob["tags"])
	elif isinstance(tags, mutagen._vorbis.VCommentDict):
		blob["embedded_image"] = ("metadata_block_picture" in tags) \
					or (len(mutafile.pictures) > 0 if hasattr(mutafile, 'pictures') else False)
		blob["tags"] = dict( tags.items() )
	elif isinstance(tags, mutagen.asf.ASFTags):
		blob["embedded_image"] = "WM/Picture" in tags
		blob["tags"] = {}
		for key, item in tags.items():
			key = key.lower()
			if not isinstance(item, mutagen.asf._attrs.ASFByteArrayAttribute):
				if key == "track":
					tracknumber_raw = item.value.split('/')
					blob["tags"]["tracknumber"] = [tracknumber_raw[0]]
					if len(tracknumber_raw) == 2:
						blob["tags"]["totaltracks"] = [tracknumber_raw[1]]
				elif key == "disc":
					discnumber_raw = item.value.split('/')
					blob["tags"]["discnumber"] = [discnumber_raw[0]]
					if len(discnumber_raw) == 2:
						blob["tags"]["totaldiscs"] = [discnumber_raw[1]]
				else:
					blob["tags"][key] = [str(item.value)]
	else:
		raise Exception(f"Tag missing: {mutafile.filename}")
	blob["tags"] = dict( natsorted(blob["tags"].items()) )
	return blob

# generate a list of releases nested under some directory
def gen_release_list(root: str) -> list[str]:
	return natsorted(set( path.dirname(file) for file in wcmatch.WcMatch(
				root,
				'|'.join(['*.'+ext for ext_list in MUSIC_EXTENSIONS.values() for ext in ext_list]),
				flags=wcmatch.RECURSIVE|wcmatch.IGNORECASE|wcmatch.SYMLINKS
			).match() ))

# generate a release dict for the given directory
def scan_release(release_path: str, mtime_only: bool = False, callback: Callable = lambda **d: None) -> dict:
	release = {} if mtime_only else {
										"tracks": {},
										"images": {},
										"files": {}
									}
	# count files for callback
	total_files = sum([1 for entry in scandir(release_path) if entry.is_file(follow_symlinks=True)])
	# scan each entry in the given directory
	scanned_files = 0
	for entry in natsorted(
				scandir(release_path),
				key=lambda x: (x.is_dir(), x.name.lower())
		):
		if entry.is_file(follow_symlinks=True):
			callback(
					file =			entry.name,
					total_files =	total_files,
					scanned_files =	scanned_files
			)
			scanned_files += 1
			# init the object that will hold entry data representation (tags blob)
			entry_path = path.join(release_path, entry.name)
			blob: dict[str, str|float|list|dict] = { "mtime": path.getmtime(entry_path) }
			# if scanning only for mtimes, use simpler method
			if mtime_only:
				release[entry.name] = blob
				continue
			# do not try opening certain extensions with mutagen
			if path.splitext(entry.name)[1].lower()[1:] not in OVERRIDDEN_FILE_EXTENSIONS:
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
					blob.update( form_audio_blob(mutafile) )
					release["tracks"][entry.name] = blob
					continue
			# if mutagen was skipped or didn't open it as audio, try opening it as an image
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
	if 'id_orig' in release.keys():
		release.pop('id_orig')
	if 'id_opus' in release.keys():
		release.pop('id_opus')
	key = [k for k, v in releases.items() if v == release]
	if len(key) == 1:
		return key[0]

# returns tuple in such form: (deleted_releases, modified_releases, new_scans)
def update_db(db: dict, trust_mtime: bool = True, critical_tags: list[str] = [], wanted_tags: list[str] = [], callback: Callable = lambda **d: None) -> tuple[dict,dict]:
	# generate fresh release list and get the old one
	callback(operation="generating release list")
	release_list = gen_release_list(db["root"])
	old_release_list = db["releases"].keys()
	# init new lists
	# note that "modified_releases" is more like "either modified or not modified releases"
	# this list will further be shrunk
	potentially_new, potentially_deleted, potentially_modified = comm(release_list, old_release_list)
	modified_releases = {}
	# detect new and remove unmodified releases from "modified_releases"
	scanned_releases = 0
	for release in potentially_modified:
		scan_callback = lambda file,total_files,scanned_files: callback(
				operation =			"scanning old releases",
				release_count =		len(potentially_modified),
				scanned_releases =	scanned_releases,
				release =			release,
				file =				file,
				total_files =		total_files,
				scanned_files =		scanned_files
		)
		if trust_mtime:
			files = scan_release(release, mtime_only=True, callback=scan_callback)
			old_files = dict(
					(name,{"mtime":data["mtime"]})
					for filetype in ("tracks","images","files")
					for name,data in db["releases"][release][filetype].items()
			)
		else:
			files = scan_release(release, callback=scan_callback)
			old_files = dict(
					(name,data)
					for filetype in ("tracks","images","files")
					for name,data in db["releases"][release][filetype].items()
			)
		scanned_releases += 1
		# if file list and corresponding data is the same,
		# release hasn't changed, so remove it from modified
		if files != old_files:
			modified_releases[release] = deepcopy(db['releases'][release])
			if trust_mtime:
				db['releases'][release] = scan_release(release, callback=scan_callback)
			else:
				db['releases'][release] = files
	scanned_releases = 0
	new_scans = {}
	for release in potentially_new:
		scan_callback = lambda file,total_files,scanned_files: callback(
				operation =			"scanning new releases",
				release_count =		len(potentially_new),
				scanned_releases =	scanned_releases,
				release =			release,
				file =				file,
				total_files =		total_files,
				scanned_files =		scanned_files
		)
		new_scans[release] = scan_release(release, callback=scan_callback)
		scanned_releases += 1
	del potentially_new
	### at this point all the release data is correct and usable
	# try finding "new" releases exactly the same as a "deleted" releases
	move = {}
	deleted_releases = {}
	for release_number, release in enumerate(potentially_deleted):
		callback(
				operation="searching for moved releases",
				release_count=len(potentially_deleted),
				scanned_releases=release_number-1
		)
		key = find_similar_release(new_scans, db["releases"][release])
		if key is not None:
			move[key] = release
		else:
			deleted_releases[release] = deepcopy(db['releases'][release])
	# move releases, moved in library, to their new paths
	for key,release in move.items():
		db["releases"][key] = db["releases"].pop(release)
		potentially_deleted.remove(release)
		new_scans.pop(key)
		print(f"Moved '{release}' to '{key}'")
	db["releases"].update(new_scans)
	callback(operation="calculating statistics")
	db["statistics"] = calc_stats(db["releases"], critical_tags, wanted_tags)
	db["update_time"] = time()
	return deleted_releases, modified_releases

# find a list of tracks lacking tags
def find_tracks_lacking_tag(releases: dict, tag: str) -> dict[str,list[str]]:
	found: dict = {}
	for release_path, value in releases.items():
		for track_name, track in value["tracks"].items():
			if tag not in track["tags"].keys():
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
			if tag in track["tags"] and track["tags"][tag][0] not in different:
				different.append(track["tags"][tag][0])
				if len(different) > 1:
					found.append( release_path )
					break
	return found

# calculate compensated peak based on peak and gain tag strings
def calc_compensated_peak(peak: float, gain_db: str) -> float:
	db = float( gain_db.lower().removesuffix('db').removesuffix('lufs') )
	if db == float('inf'):
		return 0.
	else:
		return peak * db_gain(db)

# generate peak dictionary in either track or album gain mode
# returns a dict in such form:
#	track_path: peak_value
def gen_peak_dict(releases: dict, album_mode: bool = True):
	track_peaks = {}
	if album_mode:
		gain_tag = "replaygain_album_gain"
	else:
		gain_tag = "replaygain_track_gain"
	for release_path, value in releases.items():
		for track_name, track in value["tracks"].items():
			if ( "replaygain_track_peak" in track["tags"].keys()
					and gain_tag in track["tags"].keys() ):
				peak = calc_compensated_peak(
						float(track["tags"]["replaygain_album_peak"][0]),
						track["tags"][gain_tag][0]
				)
				track_peaks[path.join(release_path,track_name)] = peak
	return dict(sorted(track_peaks.items(), key=lambda x:x[1]))

# calculate statistics for the given database
def calc_stats(releases: dict,
				critical_tags: list[str] = [],
				wanted_tags: list[str] = []) -> dict:
	statistics = {
		"max_track_peak": 0.0,
		"max_album_peak": 0.0,
		"total_length": 0.0,
		"track_counts": {
			"total": 0,
			"clipping": 0,
			"uploaded_orig": 0,
			"uploaded_opus": 0,
			"extension": {},
			"depth": {},
			"rate": {},
			"lacking_tags": {
				"critical": 0,
				"wanted": 0,
				"both": 0
			},
			"artwork": {
				"embedded": 0,
				"external": 0,
				"both": 0
				# any: embedded+external-both
			}
		}
	}

	for release in releases.values():
		# add release track count to total track count
		statistics["track_counts"]["total"] += len(release["tracks"])
		for track_name, track in release["tracks"].items():
			# add track length to total length
			statistics["total_length"] += track["length"]
			# init variables
			tags = track["tags"]
			album_peak = 0.
			track_peak = 0.
			# get peak values, compensate them for gain and save the max value
			if ( "replaygain_track_peak" in tags.keys()
					and "replaygain_track_gain" in tags.keys() ):
				track_peak = calc_compensated_peak(
						float(track["tags"]["replaygain_track_peak"][0]),
						track["tags"]["replaygain_track_gain"][0]
				)
				statistics["max_track_peak"] = max( track_peak, statistics["max_track_peak"] )
			if ( "replaygain_album_peak" in tags.keys()
					and "replaygain_album_gain" in tags.keys() ):
				album_peak = calc_compensated_peak(
						float(track["tags"]["replaygain_track_peak"][0]),
						track["tags"]["replaygain_album_gain"][0]
				)
				statistics["max_album_peak"] = max( album_peak, statistics["max_album_peak"] )
			# classify track as clipping if peak over 1
			statistics["track_counts"]["clipping"] += (track_peak > 1.0) or (album_peak > 1.0)
			# classify track as uploaded if IDs exist
			if "id_orig" in track.keys():
				statistics["track_counts"]["uploaded_orig"] += 1
			if "id_opus" in track.keys():
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
			# classify track by lacking tags
			missing_critical = any( data not in tags.keys() for data in critical_tags )
			missing_wanted = any( data not in tags.keys() for data in wanted_tags )
			statistics["track_counts"]["lacking_tags"]["critical"]	+= missing_critical
			statistics["track_counts"]["lacking_tags"]["wanted"]	+= missing_wanted
			statistics["track_counts"]["lacking_tags"]["both"]		+= missing_wanted and missing_critical
			# classify tracks by the presence of artworks
			embedded = track["embedded_image"]
			external = "images" in release
			statistics["track_counts"]["artwork"]["embedded"]	+= embedded
			statistics["track_counts"]["artwork"]["external"]	+= external
			statistics["track_counts"]["artwork"]["both"]		+= embedded and external
	# convert depth and rate keys to str
	statistics["track_counts"]["depth"] = {str(k):v for k,v in statistics["track_counts"]["depth"].items()}
	statistics["track_counts"]["rate"] = {str(k):v for k,v in statistics["track_counts"]["rate"].items()}

	# at this point, "embedded" has the count of all tracks with embedded images,
	# but it would be more useful if it only counted the ones ONLY with embedded images, but not with both.
	# So, here's a simple solution
	statistics["track_counts"]["lacking_tags"]["critical"] -= statistics["track_counts"]["lacking_tags"]["both"]
	statistics["track_counts"]["lacking_tags"]["wanted"] -= statistics["track_counts"]["lacking_tags"]["both"]
	statistics["track_counts"]["artwork"]["embedded"] -= statistics["track_counts"]["artwork"]["both"]
	statistics["track_counts"]["artwork"]["external"] -= statistics["track_counts"]["artwork"]["both"]

	return statistics

# get the list of not yet uploaded releases
def get_not_uploaded_releases(releases: dict) -> dict[str,dict[str,list|str|None]]:
	# init return blob
	return_data = {
			'orig': {
				'not_uploaded': [],
				'last': None
			},
			'opus': {
				'not_uploaded': [],
				'last': None
			}
	}
	for release_name, release in releases.items():
		for channel_type in ['orig','opus']:
			if 'id_'+channel_type  not in release.keys():
				if 'link_'+channel_type in release.keys():
					if return_data[channel_type]['last'] is None:
						return_data[channel_type]['last'] = release_name
					else:
						raise Exception("Database was damaged - more than one release in progress.")
					continue
				return_data[channel_type]['not_uploaded'].append(release_name)
	return return_data

# get list of releases with embedded images
def get_releases_with_embedded_images(releases: dict):
	return [release_path
			for release_path, release in releases.items()
			if release["tracks"][0]["embedded_image"]]

# get list of releases with external images
def get_tracks_with_embedded_images(releases: dict):
	return [release_path
			for release_path, release in releases.items()
			if len(release["images"]) > 0]

# get either the tag value or the fallback if it is missing
def get_tag_fom_first_track(release: dict, tag: str, fallback: str = "METADATA MISSING"):
	if tag in [*release["tracks"].values()][0]["tags"]:
		return [*release["tracks"].values()][0]["tags"][tag][0]
	else:
		return fallback
