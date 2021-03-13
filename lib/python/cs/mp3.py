#!/usr/bin/python
#

''' Crude parser for MP3 data based on various web documents.

    References:
    * http://www.mp3-tech.org/programmer/frame_header.html
    * https://id3.org/mp3Frame
'''

from icontract import ensure
from cs.binary import AbstractBinary, SimpleBinary
from cs.deco import OBSOLETE
from cs.id3 import ID3V1Frame, ID3V2Frame, EnhancedTagFrame
from cs.logutils import warning

@OBSOLETE
def framesof(bfr):
  ''' OBSOLETE scanner of frames. Use `MP3Frame.scan` instead.
  '''
  return MP3Frame.scan(bfr)

class MP3Frame(AbstractBinary):
  ''' An `AbstractBinary` class whose parse method is a factory for other MP3 frames.
  '''

  def __init__(self, *_, **__):
    raise NotImplementedError("please use %s.parse" % (type(self).__name__,))

  @staticmethod
  def parse(bfr):
    ''' Parse an `MP3Frame` from the buffer.

        Supposedly all the ID3v2 tags are up the front and the ID3v1
        tags are after the audio, but we do not verify that.
    '''
    bs3 = bfr.peek(3, short_ok=True)
    if bs3 == b'TAG':
      if bfr.peek(4, short_ok=True) == b'TAG+':
        return EnhancedTagFrame.parse(bfr)
      return ID3V1Frame.parse(bfr)
    if bs3 == b'ID3':
      return ID3V2Frame.parse(bfr)
    # resync with audio data
    skipped_bss = MP3AudioFrame.scan_for_sync(bfr)
    if skipped_bss:
      warning("SKIP %d bytes: %r", sum(map(len, skipped_bss)), skipped_bss)
    return MP3AudioFrame.parse(bfr)

  def transcribe(self):
    ''' There is no transcribe method because .parse returns other class instances.
    '''
    raise NotImplementedError("no transcription")

class MP3AudioFrame(SimpleBinary):
  ''' An MP3 audio frame.
  '''

  BITRATES_BY_LAYER_KBPS = {
      1: [
          None, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416,
          448, None
      ],
      2: [
          None, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384,
          None
      ],
      3: [
          None, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320,
          None
      ],
  }

  SAMPLERATES_BY_MPEG1_HZ = [44100, 48000, 42000, None]

  SAMPLERATES_BY_MPEG2_HZ = [22050, 24000, 16000, None]

  AUDIO_MODE_IDS = [2.5, None, 2, 1]

  @staticmethod
  @ensure(lambda bfr: bfr.at_eof() or bfr.peek(2).startswith(b'\xff'))
  def scan_for_sync(bfr):
    ''' Advance the buffer `bfr`
        to the next position with the sync sequence:
        a b'\xff' followed by a byte whose top 4 bits are 0b1111 or 0b1110.
        Return a list of `bytes`-like objects skipped.
    '''
    bss = []
    found = False
    for bs in bfr:
      fpos = 0
      while fpos < len(bs):
        ff_pos = bs.find(b'\xff', fpos)
        if ff_pos < 0:
          break
        ff_pos1 = ff_pos + 1
        if ff_pos1 < len(bs):
          b1 = bs[ff_pos1]
        else:
          peek_bs = bfr.peek(1, short_ok=True)
          b1 = 0 if len(peek_bs) == 0 else peek_bs[0]
        if (b1 & 0xf0) not in (0xf0, 0xe0):
          # no match: advance and look again
          fpos = ff_pos1
          continue
        # found the sync bytes
        bfr.push(bs[ff_pos:])
        bs = bs[:ff_pos]
        found = True
        break
      if bs:
        bss.append(bs)
      if found:
        break
    return bss

  @classmethod
  ##@ensure(lambda bfr: bfr.at_eof() or bfr.peek(2) == b'\xff\xfb')
  def parse(cls, bfr):
    ''' Parse an audio frame from a MP3 stream.

        Raises `EOFError` on short data.
        Raises `ValueError` on a bad header and leaves the buffer unmoved.

        Initial code derived from the description here:
        https://id3.org/mp3Frame
    '''
    self = cls()
    offset0 = bfr.offset
    # 4 byte header - decode into bit fields
    b0, b1, b2, b3 = bfr.take(4)
    try:
      self.sync_bits = b0 << 8 | b1 & 0b11110000
      # check for SYNC: 12 set bits or 11 set bites and a cleared bit
      if self.sync_bits != 0b1111111111110000 and self.sync_bits != 0b1111111111100000:
        raise ValueError(
            "offset %d: expected SYNC 12 set bits or 11 set and 1 cleared, found %02x %02x"
            % (offset0, b0, b1)
        )
      self.id_bit = b1 & 0b00001000
      self.layer_bits = b1 & 0b00000110
      self.protection_bit = b1 & 0b00000001
      self.bitrate_bits = b2 & 0b11110000
      self.frequency_bits = b2 & 0b00001100
      self.pad_bit = b2 & 0b00000010
      self.private_bit = b2 & 0b00000001
      self.mode_bits = b3 & 0b11000000  # stereo/mono mode
      self.mode_ext_bits = b3 & 0b00110000
      self.copyright_bit = b3 & 0b00001000  # copy forbidden
      self.home_bit = b3 & 0b00000100
      self.emphasis_bits = b3 & 0b00000011
      # derived attributes
      self.is_mpeg1 = self.id_bit == 0
      self.is_mpeg2 = self.id_bit != 0
      self.layer = [None, 3, 2, 1][self.layer_bits >> 1]
      if self.layer is None:
        raise ValueError(
            "offset %d: invalid layer bits %02x" % (offset0, self.layer_bits)
        )
      if self.samplerate_hz is None:
        raise ValueError(
            "offset %d: invalid frequency bits %02x" %
            (offset0, self.frequency_bits)
        )
    except (EOFError, ValueError):
      raise
    except Exception as e:
      # transmute other errors to ValueError
      raise ValueError("invalid header: %s" % (e,)) from e
    # there is a checksum if the protection_bit is set
    if self.protection_bit:
      # this is a CRC-32 - check the audio data against it
      self.checksum_bs = bfr.take(2)
    else:
      self.checksum_bs = None
    # it appears that the header is included in the length computation
    hdr_length = bfr.offset - offset0
    assert hdr_length in (4, 6), (
        "hdr_length=%d (expected 4 or 6), offset0=%d, bfr.offset=%d" %
        (hdr_length, offset0, bfr.offset)
    )
    # TODO: on demand property to decode?
    # TODO: is the // 2 stereo dependent?
    unpadded_length = 144 * self.bitrate_kbps * 1000 // 2 // self.samplerate_hz
    n_pad_bytes = self.pad_bit >> 1
    assert n_pad_bytes in (0, 1)
    # subtrack the header length
    unpadded_length -= hdr_length
    assert unpadded_length > 0
    self.data = bfr.take(unpadded_length + n_pad_bytes)
    return self

  def transcribe(self):
    ''' Transcribe an `MP3AudioFrame`.
        Assumes the bit fields and data are all correct.
    '''
    yield bytes(
        [
            self.sync_bits >> 8,
            (self.sync_bits & 0b11110000) | self.id_bit | self.layer_bits
            | self.protection_bit,
            self.bitrate_bits | self.frequency_bits | self.pad_bit
            | self.private_bit,
            self.mode_bits | self.mode_ext_bits | self.copyright_bit
            | self.home_bit
            | self.emphasis_bits,
        ]
    )
    yield self.checksum_bs
    yield self.data

  @property
  def bitrate_kbps(self):
    ''' The audio data rate in kilobits per second.
    '''
    return self.BITRATES_BY_LAYER_KBPS[self.layer][self.bitrate_bits >> 4]

  @property
  def samplerate_hz(self):
    ''' The sample rate in Hertz.
    '''
    return (
        self.SAMPLERATES_BY_MPEG1_HZ
        if self.is_mpeg1 else self.SAMPLERATES_BY_MPEG2_HZ
    )[self.frequency_bits >> 2]

if __name__ == '__main__':
  import cs.mp3_tests
  import sys
  cs.mp3_tests.selftest(sys.argv)
