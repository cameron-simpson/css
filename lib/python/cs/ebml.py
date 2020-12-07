#!/usr/bin/env python3
#
# Cameron Simpson <cs@cskk.id.au>
#

''' Yet another implementation of EBML (Extensible Binary Meta Language).
'''

from cs.binary import BinarySingleValue

def get_length_encoded_bytes(bfr) -> bytes:
  ''' Read a run length encoded byte sequence
      using a similar length encoding scheme to UTF-8.
      Return the leading bitmask (`0x80`, `0x40`, etc)
      and the bytes.
  '''
  b0 = bfr.byte0()
  assert b0 != 0
  bitmask = 0b10000000
  extra_bytes = 0
  while b0:
    if b0 & bitmask:
      break
    bitmask >>= 1
    extra_bytes += 1
  if not bitmask:
    raise ValueError("invalid leading byte 0x%02x" % (b0,))
  bs = [b0]
  if extra_bytes:
    bs.extend(bfr.take(extra_bytes))
  return bitmask, bs

def get_length_encoded_value(bfr):
  ''' Read a run length encoded value
      using a similar length encoding scheme to UTF-8.
      Return the value.

      The bytes are read using `get_length_encoded_bytes`
      and interpreted as a big endian unsigned value.
  '''
  bitmask, bs = get_length_encoded_bytes(bfr)
  bvalues = list(bs)
  bvalues[0] &= ~bitmask
  value = 0
  for b in bvalues:
    value = (value << 8) | b
  return value

def transcribe_length_encoded_value(value):
  ''' Return a `bytes` encoding `value`
      as a run length encoded string.
  '''
  value0 = value
  bvalues = []
  while value > 0:
    bvalues.append(value & 0xff)
    value >>= 8
  if not bvalues:
    bvalues.append(0)
  extra_bytes = len(bvalues) - 1
  bitmask = 0b10000000 >> extra_bytes
  if bvalues[-1] >= bitmask:
    bitmask >>= 1
    bvalues.append(0)
  if not bitmask:
    raise ValueError(
        "cannot put enough leading zeroes on the first byte to encode 0x%02x" %
        (value0,)
    )
  bvalues[-1] |= bitmask
  return bytes(reversed(bvalues))

class ElementID(BinarySingleValue):
  ''' An ElementID.
  '''

  @staticmethod
  def parse_buffer(bfr):
    ''' Read and return an `ElementID` value from the buffer.
    '''
    _, element_id_bs = get_length_encoded_bytes(bfr)
    assert element_id_bs and len(element_id_bs) <= 4
    return element_id_bs

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(element_id_bs):
    ''' Return the binary transcription of an `ElementID` value.
    '''
    return transcribe_length_encoded_value(element_id_bs)

class DataSize(BinarySingleValue):
  ''' A run length encoded data size.
  '''

  @staticmethod
  def parse_buffer(bfr):
    ''' Fetch a data size.
    '''
    return get_length_encoded_value(bfr)

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(date_size):
    ''' Transcribe a data size.
    '''
    return transcribe_length_encoded_value(date_size)

def selftest():
  ''' Run some self tests.
  '''
  # pylint: disable=import-outside-toplevel
  from cs.buffer import CornuCopyBuffer
  for n in (0, 1, 2, 3, 16, 17, 127, 128, 129, 32767, 32768, 32769, 65535,
            65536, 65537):
    bs = transcribe_length_encoded_value(n)
    bfr = CornuCopyBuffer.from_bytes(bs)
    n2 = get_length_encoded_value(bfr)
    assert n == n2, "n:%s != n2:%s" % (n, n2)
    assert bfr.offset == len(
        bs
    ), "bfr.offset:%s != len(bs):%s" % (bfr.offset, len(bs))
    assert bfr.at_eof, "bfr not at EOF"
    ds, offset = DataSize.from_bytes(bs)
    assert ds.value == n
    assert offset == len(bs)
    bs2 = bytes(ds)
    assert bs == bs2
    ds2 = DataSize(n)
    bs3 = bytes(ds2)
    assert bs == bs3

if __name__ == '__main__':
  selftest()
