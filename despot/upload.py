from despot.library import *
import os.path
from icu import Transliterator
from PIL import Image
import subprocess
from enum import Enum
from r128gain.opusgain import \
	write_oggopus_output_gain as write_opus_gain, \
	parse_oggopus_output_gain as get_opus_gain
from r128gain import float_to_q7dot8
import mutagen.oggopus as mutagen_opus
from math import log10
from mutagen.flac import Picture
from base64 import b64encode

# don't break if an image is truncated
#ImageFile.LOAD_TRUNCATED_IMAGES = True
# disable zipbomb protection (which prevents large images from loading
# and is not really a concern if it's in your own music library anyway)
Image.MAX_IMAGE_PIXELS = None

# images are saved as jpegs with quality 77
# it is because it looks like the telegram recompresses them with approximately this quality

class RG_Mode(Enum):
	NONE = 0		# remove existing ReplayGain tags and set header gain to 0 dB
	REPLAYGAIN = 1	# save ReplayGain tags, convert R128 tags to ReplayGain, set header gain to 0 db
	R128 = 2		# save R128 tags, convert ReplayGain tags to R128, set header gain to album gain

class RG_Clip(Enum):
	NONE = 0	# ignore clipping and let player handle it
	ALBUM = 1	# lower the volume for all tracks in the release to preserve the album dynamics
	TRACK = 2	# lower the volume for separate tracks

RG_TAGLIST = (
	'replaygain_album_peak',
	'replaygain_track_peak',
	'replaygain_album_gain',
	'replaygain_track_gain',
	'replaygain_album_range',
	'replaygain_track_range',
	'replaygain_reference_loudness'
)
R128_TAGLIST = ( 'r128_track_gain', 'r128_album_gain')
MP3GAIN_TAGLIST = ( 'mp3gain_minmax', 'mp3gain_album_minmax', 'mp3gain_undo')

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

# remove tag(s) from a mutagen opus representation safely
def mutagen_safe_pop(muta: mutagen_opus.OggOpus, tags: tuple[str, ...]|str):
	keys = muta.keys()
	if isinstance(tags, list):
		for tag in tags:
			if tag in keys:
				muta.pop(tag)
	else:
		if tags in keys:
			muta.pop(tags)

# encode track as opus with given parameters
def encode_opus(
			track_path: str,
			out_path: str,
			bitrate: int		= 96,
			rg_mode: RG_Mode	= RG_Mode.NONE,
			rg_clip: RG_Clip	= RG_Clip.NONE,
			artwork: str|None	= None,
			ffmpeg_path: str	= 'ffmpeg'
	):
	command = [
			ffmpeg_path,
			'-i',	track_path,
			'-c:a',	'libopus',
			'-b:a',	f'{bitrate}k',
			out_path
	]
	if subprocess.run(command).returncode != 0:
		raise Exception(f'Ffmpeg command exited with a non-zero return code. Command: {command}.')
	# handle ReplayGain and R128
	muta = mutagen_opus.Open(out_path)
	if rg_mode == RG_Mode.NONE:
		mutagen_safe_pop( muta, (*RG_TAGLIST, *R128_TAGLIST, *MP3GAIN_TAGLIST) )
		muta.save()
	elif rg_mode == RG_Mode.REPLAYGAIN:
		if 'replaygain' not in [key[:10] for key in muta.keys()]:
			if 'r128_track_gain' in muta.keys():
				muta['replaygain_track_gain'] = [f"{muta['r128_track_gain'][0]/256 :.2f} LUFS"]
			else:
				raise Exception(f"Track gain information is missing in '{track_path}'.")
			if 'r128_album_gain' in muta.keys():
				muta['replaygain_album_gain'] = [f"{muta['r128_album_gain'][0]/256 :.2f} LUFS"]
		mutagen_safe_pop( muta, (*R128_TAGLIST, *MP3GAIN_TAGLIST) )
		# handle clipping prevention
		if rg_clip == RG_Clip.TRACK:
			compensated_peak = calc_compensated_peak(
					float(muta['replaygain_track_peak'][0]),
					muta['replaygain_track_gain'][0]
			)
			if compensated_peak > 1.0:
				absgain = db_gain(float(muta['replaygain_track_gain'][0]
							.lower().removesuffix('db').removesuffix('lufs')))
				muta['replaygain_track_gain'] =	f'{20*log10(absgain/compensated_peak):.2f} LUFS'
			# additionally compensate album gain (based on track peak)
			if 'replaygain_album_gain' in muta.keys():
				compensated_peak = calc_compensated_peak(
						float(muta['replaygain_track_peak'][0]),
						muta['replaygain_album_gain'][0]
				)
				if compensated_peak > 1.0:
					absgain = db_gain(float(muta['replaygain_album_gain'][0]
							.lower().removesuffix('db').removesuffix('lufs')))
					muta['replaygain_album_gain'] =	f'{20*log10(absgain/compensated_peak):.2f} LUFS'
		elif rg_clip == RG_Clip.ALBUM:
			compensated_peak = calc_compensated_peak(
					float(muta['replaygain_album_peak'][0]),
					muta['replaygain_album_gain'][0]
			)
			if compensated_peak > 1.0:
				# compensate track gain based on album peak
				absgain = db_gain(float(muta['replaygain_track_gain'][0]
							.lower().removesuffix('db').removesuffix('lufs')))
				muta['replaygain_track_gain'] =	f'{20*log10(absgain/compensated_peak):.2f} LUFS'
				# compensate album gain based on album peak
				absgain = db_gain(float(muta['replaygain_album_gain'][0]
							.lower().removesuffix('db').removesuffix('lufs')))
				muta['replaygain_track_gain'] =	f'{20*log10(absgain/compensated_peak):.2f} LUFS'
		muta.save()
	elif rg_mode == RG_Mode.R128:
		if 'r128_track_gain' not in muta.keys():
			rg_track_gain = muta.get('replaygain_track_gain')
			if rg_track_gain is not None:
				db = float( rg_track_gain.lower().removesuffix('db').removesuffix('lufs') )
				muta['r128_track_gain'] = float_to_q7dot8(db)
			else:
				raise Exception(f"Track gain information is missing in '{track_path}'.")
		if 'r128_album_gain' not in muta.keys():
			header_gain = 0
			rg_album_gain = muta.get('replaygain_album_gain')
			if rg_album_gain is not None:
				db = float( rg_album_gain.lower().removesuffix('db').removesuffix('lufs') )
				muta['r128_album_gain'] = 0
				muta['r128_track_gain'] = float_to_q7dot8(muta['r128_track_gain']/256 - db)
				muta.save()
				write_opus_gain(open(out_path, 'r+b'), float_to_q7dot8(db))
		mutagen_safe_pop( muta, (*RG_TAGLIST, *MP3GAIN_TAGLIST) )
	# if given artwork, embed it
	if artwork is not None:
		img = Picture()
		img.data = open(artwork, 'rb').read()
		img.type = 3	# type 3 stands for cover art
		muta['metadata_block_picture'] = b64encode(img.write()).decode('ascii')
	muta.save()

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
