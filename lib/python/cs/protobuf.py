#!/usr/bin/env python3

''' A simple minded protocol buffer encoder/decoder.

    I'm basing this off:
    https://protobuf.dev/programming-guides/encoding/
'''

from enum import IntEnum
from typing import Tuple

from cs.binary import AbstractBinary, BinarySingleValue
from cs.buffer import CornuCopyBuffer
from cs.deco import promote
from cs.trace import Trace

class WireType(IntEnum):
  VARINT = 0
  I64 = 1
  LEN = 2
  SGROUP = 3
  EGROUP = 4
  I32 = 5

class VarInt(BinarySingleValue, value_type=int):

  @staticmethod
  @Trace
  @promote
  def parse_value(bfr: CornuCopyBuffer, *, T: Trace) -> int:
    ''' Parse a protobuf VARINT from a buffer.
    '''
    T("n", 0)
    n = 0
    shift = 0
    while True:
      b = bfr.byte0()
      T("byte", hex(b))
      n |= (b & 0x7f) << shift
      T("n", n)
      if b & 0x80 == 0:
        break
      shift += 7
    return n

  @staticmethod
  def decode_bytes(data: bytes, offset=0) -> Tuple[int, int]:
    r'''Decode an extensible byte serialised unsigned `int` from `data` at `offset`.
    '''
    n = 0
    shift = 0
    while True:
      b = data[offset]
      offset += 1
      n |= (b & 0x7f) << shift
      if b & 0x80 == 0:
        break
      shift += 7
    return n, offset

  # pylint: disable=arguments-renamed
  @staticmethod
  def transcribe_value(n):
    ''' Encode an unsigned int as an entensible byte serialised octet
        sequence for decode. Return the bytes object.
    '''
    assert n >= 0
    bns = []
    while True:
      b = n & 0x7f
      n >>= 7
      if n > 0:
        b |= 0x80
      bns.append(b)
      if n == 0:
        break
    return bytes(bns)

class Protobuf(AbstractBinary):
  ''' A Google Protobuf.

      See: https://protobuf.dev/programming-guides/encoding/
  '''

  def __init__(self, wire_type: int, field_number: int, value):
    if field_number < 0:
      raise ValueError(f'{field_number=} must be >= 0')
    self.wire_type = WireType(wire_type)
    self.field_number = field_number
    self.value = value

  @classmethod
  @Trace
  @promote
  def parse(cls, bfr: CornuCopyBuffer, *, T: Trace):
    record_hdr = VarInt.parse_value(bfr)
    T("record_hdr", hex(record_hdr))
    wire_type = record_hdr & 0x07
    T("wire_type", wire_type)
    field_number = record_hdr >> 3
    T("field_number", field_number)
    if wire_type == WireType.VARINT:
      value = VarInt.parse_value(bfr)
      T("VARINT", value)
    elif wire_type == WireType.I64:
      value = bfr.take(8)  # yet to decode into a number
      T("I64", value)
    elif wire_type == WireType.LEN:
      length = VarInt.parse_value(bfr)
      value = []
      with Trace(f'LEN {length} records') as T2:
        for i in range(length):
          value.append(cls.parse(bfr))
    elif wire_type == WireType.I32:
      value = bfr.take(4)  # yet to decode into a number
      T("I32", value)
    else:
      raise ValueError(f'unsupported {wire_type=}')
    return cls(wire_type, field_number, value)

  def transcribe(self):
    wire_type = self.wire_type
    record_hdr = (self.field_number << 3) | wire_type
    yield VarInt.transcribe_value(record_hdr)
    if wire_type == WireType.VARINT:
      yield VarInt.transcribe_value(self.value)
    elif wire_type == WireType.I64:
      yield self.value  # should b 8 bytes
    elif wire_type == WireType.LEN:
      length = len(self.value)
      yield VarInt.transcribe_value(length)
      for subvalue in self.value:
        yield subvalue.transcribe()
    elif wire_type == WireType.I32:
      yield self.value  # should be 4 bytes
    else:
      raise ValueError(f'unsupported {wire_type=}')

if __name__ == '__main__':
  bs = b'\x08\x022\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
  ##import compression
  ##bs = compression.zstd.decompress(zbs[4:])
  ##import zlib
  ##bs = zlib.decompress(zbs)
  bfr = CornuCopyBuffer((bs,))
  while not bfr.at_eof():
    try:
      with Trace("decode") as T:
        pbuf = Protobuf.parse(bfr)
    finally:
      T.printt()
    print(repr(pbuf))
