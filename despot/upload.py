from library import *
import os.path
from icu._icu_ import Transliterator
import subprocess
from enum import Enum
from shutil import copyfile
from r128gain.opusgain import \
	write_oggopus_output_gain as write_opus_gain
from r128gain import float_to_q7dot8
import mutagen.oggopus as mutagen_opus
from mutagen.flac import Picture
from math import log10
from base64 import b64encode, b64decode
from pyrogram.client import Client
import pyrogram.types

# images are saved as jpegs with quality 77
# it is because it looks like the telegram recompresses them with approximately this quality

class RG_Mode(Enum):
	NONE = 'None'					# remove existing ReplayGain tags and set header gain to 0 dB
	REPLAYGAIN = 'ReplayGain 2.0'	# save ReplayGain tags, convert R128 tags to ReplayGain, set header gain to 0 db
	R128 = 'R128'					# save R128 tags, convert ReplayGain tags to R128, set header gain to album gain

class RG_Clip(Enum):
	NONE = 'None'	# ignore clipping and let player handle it
	ALBUM = 'Album'	# lower the volume for all tracks in the release to preserve the album dynamics
	TRACK = 'Track'	# lower the volume for separate tracks

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
		'depth_rates': set(f'{track["depth"]}/{int(track["rate"])/1000}' for track in release["tracks"].values() )
	}
	formatting_data['depth_rates'] = ', '.join(formatting_data['depth_rates'])
	latin_tracklist = ""
	# stack up tracklist
	for track in release["tracks"].values():
		latin_tracklist += f'\n{track["tags"]["tracknumber"][0]}{track_separator}\
				{to_latin.transliterate(track["tags"]["title"][0])}'
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
	preferred_image = ''
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
		image_score = name_score*len(preferred_exts) + extension_score
		# Update the preferred item if the current item has a higher score
		if image_score > max_score:
			preferred_image = image_fname
			max_score = image_score
	return preferred_image

# choose the "best" artwork based on filename, but probably more efficiently
def optimized_artwork_search(
			images: list[str],
			preferred_names: list[str],
			preferred_exts: list[str]
	):
	best_ext_images = []
	# populate "best_ext_images" with artworks matching the best extension
	for ext in preferred_exts:
		for i in images:
			if i.endswith(ext):
				best_ext_images += i
		if best_ext_images != []:
			break
	# if none of the listed extensions are present, add every image, as they're equally bad
	if best_ext_images == []:
		best_ext_images = images
	# of all "best_ext_images" return the one closest to the top of the preferred names
	for name in preferred_names:
		for i in best_ext_images:
			if i[:len(name)] == name:
				return i
	# if none of the "best_ext_images" matched any of the preferred names, return the first one
	return best_ext_images[0]

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

def extract_embedded_image(src: str, out: str):
	mutafile = mutagen._file.File(src)
	if mutafile is None:
		raise Exception(f'Couldn\'t open {src} using mutagen')
	tags = mutafile.tags
	if isinstance(tags, mutagen.id3.ID3):
		apic = tags.get('APIC:')
		if apic is None:
			raise Exception(f'Track {src} contains no embedded image')
		else:
			open(out, 'wb').write(apic.data)
	elif isinstance(tags, mutagen.apev2.APEv2):
		for name, value in tags.items():
			if name.lower().startswith("cover art") and value.kind == mutagen.apev2.BINARY:
				open(out, 'wb').write( value.value.split(b'\0', 1)[1] )
				return
		raise Exception(f"Embeded image missing in: {src}")
	elif isinstance(tags, mutagen.mp4.MP4Tags):
		covr = tags.get('covr')
		if covr is None:
			raise Exception(f'Track {src} contains no embedded image')
		else:
			open(out, 'wb').write( covr[0] )
	elif isinstance(tags, mutagen._vorbis.VCommentDict):
		if ("metadata_block_picture" in tags):
			data = Picture( b64decode(tags['metadata_block_picture'][0]) ).data
		elif hasattr(mutafile, 'pictures') and len(mutafile.pictures) > 0:
			data = mutafile.pictures[0].data
		else:
			raise Exception(f"Embeded image missing in: {src}")
		open(out,'wb').write( data )
	elif isinstance(tags, mutagen.asf.ASFTags):
		pic_tag = tags.get('WM/Picture')
		if pic_tag is None:
			raise Exception(f'Track {src} contains no embedded image')
		else:
			v = pic_tag[0].value
			open(out,'wb').write( v[v.find(255):] )

def send_track(
		client: Client,
		release: dict,
		track_filename: str,
		release_path: str,
		album_thumbnail: bool,
		channel: str,
		tmp_dir: str,
		fallback_thumbnail: str,
		opus_settings: dict|None,
		callback: Callable = lambda: None
	):
	track = release['tracks'][track_filename]
	callback(operation="Preparing track thumbnail")
	# find best image
	if track['embedded_image']:
		extract_embedded_image(path.join(release_path,track_filename), path.join(tmp_dir,'track_thumbnail.jpg'))
		prepare_artwork(path.join(tmp_dir,'track_thumbnail.jpg'),path.join(tmp_dir,'track_thumbnail.jpg'))
		thumb_file = path.join(tmp_dir,'track_thumbnail.jpg')
	elif album_thumbnail:
		thumb_file = path.join(tmp_dir,'album_thumbnail.jpg')
	else:
		thumb_file = fallback_thumbnail
	track_path = path.join(release_path,track_filename)
	if isinstance(opus_settings, dict):
		callback(operation='Encoding')
		encode_opus(
				track_path,
				path.join(tmp_dir,'track.opus'),
				bitrate=opus_settings['bitrate'],
				rg_mode=opus_settings['replaygain']['mode'],
				rg_clip=opus_settings['replaygain']['clipping_policy'],
				artwork=thumb_file
		)
		track_path = path.join(tmp_dir,'track.opus')
	# send track
	msg = client.send_audio(
			channel,
			path.join(release_path,track_filename),
			duration=int(track['length']),
			performer=' | '.join(track['tags']['artist']),
			title=track['tags']['title'][0],
			thumb=thumb_file,
			progress=(lambda current,total: callback(operation='Sending', current=current, total=total))
	)
	if isinstance(msg, pyrogram.types.Message):
		if isinstance(opus_settings, dict):
			release['tracks'][track_filename]['id_opus'] = msg.id
			release['tracks'][track_filename]['link_opus'] = msg.link
		else:
			release['tracks'][track_filename]['id_orig'] = msg.id
			release['tracks'][track_filename]['link_orig'] = msg.link
	else:
		raise Exception(f'Couldn\'t upload {path.join(release_path,track_filename)}.')

def remove_release_links(release: dict, link_field: str):
	release.pop(link_field)
	for track in release['tracks']:
		track.pop(link_field)

def upload_release(
		release: dict,
		release_path: str,
		client: Client,
		channel: str,
		tmp_dir: str,
		assets_dir: str,
		release_string: str,
		preferred_names: list[str],
		preferred_exts: list[str],
		opus_settings: dict|None,
		callback: Callable
	):
	if isinstance(opus_settings, dict):
		short_type = 'opus'
	else:
		short_type = 'orig'
	id_field = 'id_'+short_type
	link_field = 'link_'+short_type
	callback(operation="Preparing release images")
	# detect best image
	if len(release['images']) > 0:
		best_image = Image.open(get_best_artwork(release['images'], preferred_names, preferred_exts))
		album_thumbnail = True
		prepare_thumbnail(best_image, path.join(tmp_dir,'album_thumbnail.jpg'))
	else:
		best_image = None
		album_thumbnail = False
	# uploading stage
	# if release message was already uploaded, proceed to upload the missing tracks
	if id_field in release.keys():
		for filename,track in dict(natsorted((k,v) for k,v in release['tracks'].items() if id_field not in v)):
			send_track(
					client,
					release,
					filename,
					release_path,
					album_thumbnail,
					channel,
					tmp_dir,
					path.join(assets_dir, 'fallback_thumbnail.jpg'),
					opus_settings,
					lambda operation, current=None, total=None:
						callback(operation=operation, track=filename, current=current, total=total),
			)
		remove_release_links(release, link_field)
	# if release message has not been uploaded yet, send the first message and upload the tracks
	else:
		# release message
		# artwork preparation
		if best_image is not None:
			prepare_artwork(best_image, path.join(tmp_dir,'album_artwork.jpg'))
		elif any( [track['embedded_image'] for track in release['tracks'].values()] ):
			for filename,track in release['tracks'].items():
				if track['embedded_image']:
					extract_embedded_image(path.join(release_path,filename), path.join(tmp_dir,'album_artwork.jpg'))
					prepare_artwork(path.join(tmp_dir,'album_artwork.jpg'), path.join(tmp_dir,'album_artwork.jpg'))
					break
		else:
			copyfile(path.join(assets_dir,'fallback_artwork.jpg'), path.join(tmp_dir,'album_artwork.jpg'))
		# send release message
		msg = client.send_photo(
				channel,
				path.join(tmp_dir,'album_artwork.jpg'),
				caption=format_release_string(release_string, release)
		)
		# if release message sent successfully, proceed to sending the tracks
		if isinstance(msg, pyrogram.types.Message):
			release[id_field] = msg.id
			release[link_field] = msg.id
			for filename in release['tracks'].keys():
				send_track(
						client,
						release,
						filename,
						release_path,
						album_thumbnail,
						channel,
						tmp_dir,
						path.join(assets_dir, 'fallback_thumbnail.jpg'),
						opus_settings,
						lambda operation, current=None, total=None:
							callback(operation=operation, track=filename, current=current, total=total),
				)
				callback(operation='Track sent successfully')
			# after finishing the full release upload, clean up the links
			remove_release_links(release, link_field)
		else:
			raise Exception(f'Couldn\'t upload {release_path}.')
