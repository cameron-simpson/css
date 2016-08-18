Support for ID3 tags.
=====================

This module is mostly a convenience wrapper for Doug Zongker's pyid3lib. Note that pyid3lib is not on PyPI and must be fetched independently.

I'm utilising it via cs.id3 in my id3ise script (https://bitbucket.org/cameron_simpson/css/src/tip/bin-cs/id3ise), yet another MP3 tagger/cleaner script. See that script for how to use the ID3 class.

The ID3 class has a much wider suite of convenience attribute names and several convenience facilities.

References:
===========

Doug Zongker's pyid3lib:
    http://pyid3lib.sourceforge.net/

My id3ise script:
    https://bitbucket.org/cameron_simpson/css/src/tip/bin-cs/id3ise

id3 2.3.0 spec, frames:
    http://id3.org/id3v2.3.0#Text_information_frames

id3 2.4.0 spec, frames:
    http://id3.org/id3v2.4.0-frames
