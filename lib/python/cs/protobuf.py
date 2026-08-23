#!/usr/bin/env python3

''' A simple minded protocol buffer decoder.

    I'm basing this off:
    https://protobuf.dev/programming-guides/encoding/
'''

from enum import IntEnum
from typing import Tuple

from cs.binary import BinarySingleValue
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

@Trace
@promote
def parse(bfr: CornuCopyBuffer, *, T: Trace):
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
        value.append(parse(bfr))
  elif wire_type == WireType.I32:
    value = bfr.take(4)  # yet to decode into a number
    T("I32", value)
  else:
    raise ValueError(f'unsupported {wire_type=}')
  return {"wire_type": wire_type, field_number: value}

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

if __name__ == '__main__':
  bs = b'\x08\x022\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
  zbs = b'\xb5/\xfd`\x80\x05\xb5-\x00TT\n\xfd\x0c\n\x061.22.6\x8a\x017\x1a\x14Vintage Story Server8\x80\xc0>@\x80\x02H\x80\xc0>\xa8\x01\xd0\x0f\xb0\x01\x01\xb8\x01\xd0\x0fh\xc2\xd8\x83\x9b\x05\x82\x01\x0fsurviveandbuild\x9a\x01$\n\x04game\x12\nEssentials\x1a"6(\x01\x9a\x01+\n\x08creative\x12\rCreative Mode\x1asurvival\x12\rSurvival \xa2\x01\xef\n\x05\x08gameMode\x08s\x05\x0bplayerlives\x02-1\x05\x0fstartingClimate\ttemperate\x05\x0bspawnRadius\x0250\x05\ngraceTimer\x010\x05\x0fdeathPunishment\x04drop\x05\x11droppedItemsTimer\x03600\x05\x07seasons\x07enabled\x05\x0cdaysPerMonth\x019\x05\x0charshWinters\x04true\x05\x0cblockGravity\nsandgravel\x05\x07caveIns\x03off\t\x12allowFallingBlocks\x01\t\x0fallowFireSpread\x01\t\x0elightningFires\x00\t\x17allowUndergroundFarming\x00\t\x17noLiquidSourceTransport\x00\x05\x12playerHealthPoints\x0215\x05\x16RegenSpeed\x011\x05\x11unger\x0clungCapacity\x0540000\x05\x19bodyTureResistance\x010\x05\x0fMove\x031.5\x05\x11creHostility\naggressive\x05\x10Strength\x011\x05\x11ureSwimSpeed\x012\x05\x0efoodSpoil\x011\x05\x11saplingGrowthRate\x011\x05\x0etoolDurability\x011\x05\x0ftoolMining\x17propickNodeSearchRadius\x016\x05\x13microblockChiseling\tstonewoodCoordinateHud\x01\t\x08Map\x01\t\x15colorAccurateWorldmap\x00\t\x0bloreContent\x01\x05\x11clutterObtai\nifrepaired\t\x11temporalSt\x05\x0eorms\tsometimes\x05\x14tempstormDurationMul\x011\x05\rRifts\x07visible\x05\x17GearRespawnUses\x0220\x05\x15Sleeping\x010\x05\x0cworldrealistic\x05\tlandcover\x050.975\x05\noceanscale\x015\x05\x12upheavelCommonness\x030.3\x05\x10geologicActivity\x040.05\x05\rlandformScale\x031.0\x05\nWidth\x071024000\x05\x0bworldLeng\tEdge\x0btraversable\x05\x14polarEquatorD\x06100000\x05\x1astoryStructuresDistScaling\x011\x05\x11global\x011\x05\x13PrecipitForestation\x010\x05\x16DepositSpawnRate\x011\x05\x15surfaceCoppers\x040.12\x05\x12Tins\x050.007\x05\tsnowAccum\x04true\t\x11LandClaiming\x01\t\x15classExclusiveRecipes\x01\t\x0cauctionHouse\x01\x00\xc2\x01$8f45ac0a-53b2-4d03-988cab55efd5\xca\x01\x08survival* @B\xe2\xa4\xd3\x0e\x8c$1G\xde\xd1\x91\xdd\x97c\x882\xe9\xa8\xb7\xbb\xe4\xdc"\x86s\xdd\xc9\x94Y\x95\x99\xdd\x04\xb88X~\x11\xef\x15-\xfa\x05\xac\x84\xd1IEb\xa2O\xed6\t\x12\xb8\x86\xb7y\x03\x89\xac\xdb\xd5\xf9v\xde\x82\xdbx\xa9\x00\t\x87\x93\xc7-\xd7\x03/\xfb\x9e\x07J\xdeF\xa6T\xa9\xf8t\xd9\x8a\x12Qd\x91\x1c\xcff\xdcF\x8e|r(_'
  import compression
  bs = compression.zstd.decompress(zbs[4:])
  ##import zlib
  ##bs = zlib.decompress(zbs)
  bfr = CornuCopyBuffer((zbs,))
  while not bfr.at_eof():
    try:
      with Trace("decode") as T:
        value = parse(bfr)
    finally:
      T.printt()
    print(repr(value))
