# https://stackoverflow.com/a/68245346
# handle padding with double-size chars
import unicodedata
WIDTHS = { 'F': 2, 'H': 1, 'W': 2, 'N': 1, 'A': 1, 'Na': 1 }
def wc_normalize_length(text, width):
	text_width = 0
	for n, ch in enumerate(text):
		width_class = unicodedata.east_asian_width(ch)
		text_width += WIDTHS[width_class]
		if text_width > width:
			return text[:n]
	return text + ' ' * (width - text_width)
