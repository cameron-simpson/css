#!/usr/bin/python
#
# Crude parser for MP3 data based on:
#   http://www.mp3-tech.org/programmer/frame_header.html
# - Cameron Simpson <cs@zip.com.au>
#

from cs.buffer import extend

_mp3_audio_ids = [ 2.5, None, 2, 1 ]
_mp3_layer     = [ None, 3, 2, 1 ]
_mp3_crc       = [ True, False ]
_mp3_br_v1_l1  = [ None, 32, 64, 96, 128, 160, 192, 224,
                   256, 288, 320, 352, 384, 416, 448, None ]
_mp3_br_v1_l2  = [ None, 32, 48, 56, 64, 80, 96, 112,
                   128, 160, 192, 224, 256, 320, 384, None ]
_mp3_br_v1_l3  = [ None, 32, 40, 48, 56, 64, 80, 96,
                   112, 128, 160, 192, 224, 256, 320, None ]
_mp3_br_v2_l1  = [ None, 32, 48, 56, 64, 80, 96, 112,
                   128, 144, 160, 176, 192, 224, 256, None ]
_mp3_br_v2_l23 = [ None, 8, 16, 24, 32, 40, 48, 56,
                   64, 80, 96, 112, 128, 144, 160, None ]
_mp3_sr_m1     = [ 44100, 48000, 32000, None ]
_mp3_sr_m2     = [ 22050, 24000, 16000, None ]
_mp3_sr_m25    = [ 11025, 12000, 8000, None ]

def parse_mp3(chunks):
  ''' Read MP3 data from `fp` and yield frame data chunks.
  '''
  chunks = iter(chunks)
  chunk = b''
  def accrue(min_size):
    nonlocal chunk
    new_chunks = []
    chunk = extend(chunk, min_size, chunks, new_chunks.append)
    yield from iter(new_chunks)
  offset = 0
  while True:
    advance_by = None
    yield from accrue(3)
    if len(chunk) < 3:
      break
    if chunk[:3] == b'TAG':
      yield from accrue(128)
      yield offset + 128
      advance_by = 128
    elif chunk[:3] == b'ID3':
      # TODO: suck up a few more bytes and compute length
      raise RuntimeError("ID3 not implemented")
    else:
      # 4 byte header
      yield from accrue(4)
      b0, b1, b2, b3 = chunk[:4].tolist()
      if b0 != 255:
        raise ValueError("offset %d: expected 0xff, found 0x%02x" % (offset, b0,))
      if (b1 & 224) != 224:
        raise ValueError("offset %d: expected b&224 == 224, found 0x%02x" % (offset+1, b1))
      audio_vid = _mp3_audio_ids[ (b1&24)>>3 ]
      layer = _mp3_layer[ (b1&6)>>1 ]
      has_crc = not _mp3_crc[ b1&1 ]
      bri = (b2&240)>>4
      if audio_vid == 1:
        if layer == 1:
          bitrate = _mp3_br_v1_l1[bri]
        elif layer == 2:
          bitrate = _mp3_br_v1_l2[bri]
        elif layer == 3:
          bitrate = _mp3_br_v1_l3[bri]
        else:
          raise ValueError("offset %d: bogus layer %s" % (offset, layer))
      elif audio_vid == 2 or audio_vid == 2.5:
        if layer == 1:
          bitrate = _mp3_br_v2_l1[bri]
        elif layer == 2 or layer == 3:
          bitrate = _mp3_br_v2_l23[bri]
        else:
          raise ValueError("offset %d: bogus layer %s" % (offset, layer))
      else:
        raise ValueError("offset %d: bogus audio_vid %s" % (offset, audio_vid))
      sri = (b2&12) >> 2
      if audio_vid == 1:
        samplingrate = _mp3_sr_m1[sri]
      elif audio_vid == 2:
        samplingrate = _mp3_sr_m2[sri]
      elif audio_vid == 2.5:
        samplingrate = _mp3_sr_m25[sri]
      else:
        raise ValueError("offset %d: unsupported audio_vid %s" % (offset, audio_vid))
      padding = (b2&2) >> 1
      # TODO: surely this is wrong? seems to include header in audio sample
      if layer == 1:
        data_len = (12 * bitrate * 1000 // samplingrate + padding) * 4
      elif layer == 2 or layer == 3:
        data_len = 144 * bitrate * 1000 // samplingrate + padding
      else:
        raise ValueError("offset %d: cannot compute data_len for layer=%s" % (offset, data_len))
      frame_len = data_len
      if has_crc:
        frame_len += 2
      ##print("vid =", audio_vid, "layer =", layer, "has_crc =", has_crc, "frame_len =", frame_len, "bitrate =", bitrate, "samplingrate =", samplingrate, "padding =", padding, file=sys.stderr)
      yield from accrue(frame_len)
      yield offset + frame_len
      advance_by = frame_len
    assert advance_by > 0
    chunkage[0] = chunk[advance_by:]
    offset += advance_by
  if chunk:
    yield chunk
