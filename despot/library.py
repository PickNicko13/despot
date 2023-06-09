import mutagen._file
import mutagen._vorbis
import mutagen.apev2
import mutagen.asf
import mutagen.easyid3
import mutagen.easymp4
import mutagen.id3
import mutagen.mp4
from PIL import Image
from natsort import natsorted
from os import path, scandir, makedirs
import json
from wcmatch import wcmatch
import zstandard
from datetime import datetime

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
def form_audio_blob(mutafile, entry_path):
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
		easy_tags = mutagen.easyid3.EasyID3(entry_path)
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
		blob["embedded_image"] = "cover art (front)" in tags
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
		tags = mutagen.easymp4.EasyMP4(entry_path).tags
		if tags is None:
			raise Exception(f"Could not open tags: '{entry_path}'")
		blob["tags"] = dict( tags.items() )
		split_tags(blob["tags"])
	elif isinstance(tags, mutagen._vorbis.VCommentDict):
		blob["embedded_image"] = "tags_block_picture" in tags
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
	elif tags is not None:
		blob["embedded_image"] = len(mutafile.pictures) > 0 if hasattr(mutafile, 'pictures') else False
		blob["tags"] = dict( tags.items() )
	else:
		raise Exception(f"Tag missing: {entry_path}")
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
			# init the object that will hold entry data representation (tags blob)
			entry_path = path.join(release_path, entry.name)
			blob: dict[str, str|float|list|dict] = { "mtime": path.getmtime(entry_path) }
			# if scanning only for mtimes, use simplex method
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
					blob.update( form_audio_blob(mutafile, entry_path) )
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
	if db != float('inf'):
		return 0.
	else:
		return peak * db_gain(db)

# find tracks which clip in either track or album gain mode
# returns two dicts in such form:
#	track_path: peak_value
# first dict is for track compensated peaks, second is for the album compensated track peaks
def find_clipping_tracks(releases: dict) -> tuple[dict[str,float], dict[str,float]]:
	clip_album: dict = {}
	clip_track: dict = {}
	for release_path, value in releases.items():
		for track_name, track in value["tracks"].items():
			if ( "replaygain_track_peak" in track["tags"].keys()
					and "replaygain_album_gain" in track["tags"].keys() ):
				peak = calc_compensated_peak(
						float(track["tags"]["replaygain_album_peak"][0]),
						track["tags"]["replaygain_album_gain"][0]
				)
				if peak > 1:
					clip_album[path.join(release_path,track_name)] = peak
			if ( "replaygain_track_peak" in track["tags"].keys()
					and "replaygain_track_gain" in track["tags"].keys() ):
				peak = calc_compensated_peak(
						float(track["tags"]["replaygain_track_peak"][0]),
						track["tags"]["replaygain_track_gain"][0]
				)
				if peak > 1:
					clip_track[path.join(release_path,track_name)] = peak
	return ( dict(sorted(clip_track.items(), key=lambda x:x[1])),
				dict(sorted(clip_album.items(), key=lambda x:x[1])) )

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
				"wanted": 0
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
			peak = 0.0
			# get peak values, compensate them for gain and save the max value
			if ( "replaygain_track_peak" in tags.keys()
					and "replaygain_track_gain" in tags.keys() ):
				peak = float( tags["replaygain_track_peak"][0] )
				db = float( tags["replaygain_track_gain"][0].lower().
					removesuffix('db').removesuffix('lufs') )
				if db != float('inf'):
					peak *= db_gain(db)
					statistics["max_track_peak"] = max( peak, statistics["max_track_peak"] )
			if ( "replaygain_album_peak" in tags.keys()
					and "replaygain_album_gain" in tags.keys() ):
				peak = float( tags["replaygain_album_peak"][0] )
				db = float( tags["replaygain_album_gain"][0].lower().
					removesuffix('db').removesuffix('lufs') )
				if db != float('inf'):
					peak *= db_gain(db)
					statistics["max_album_peak"] = max( peak, statistics["max_album_peak"] )
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
			# classify track by lacking tags
			if any( data not in tags.keys() for data in critical_tags ):
				statistics["track_counts"]["lacking_tags"]["critical"] += 1
			if any( data not in tags.keys() for data in wanted_tags ):
				statistics["track_counts"]["lacking_tags"]["wanted"] += 1
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
		if 'link_orig' not in release.keys():
			return_data['orig']['not_uploaded'].append(release_name)
			continue
		for track in release['tracks']:
			if 'link_orig' not in track.keys():
				if return_data['orig']['last'] is not None:
					raise Exception("Database was damaged - more than one release in progress.")
				return_data['orig']['last'] = release_name
				continue
		if 'link_opus' not in release.keys():
			return_data['opus']['not_uploaded'].append(release_name)
			continue
		for track in release['tracks']:
			if 'link_opus' not in track.keys():
				if return_data['opus']['last'] is not None:
					raise Exception("Database was damaged - more than one release in progress.")
				return_data['opus']['last'] = release_name
				continue
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
	if tag in release["tracks"][0]["tags"]:
		return release["tracks"][0]["tags"][tag][0]
	else:
		return fallback
