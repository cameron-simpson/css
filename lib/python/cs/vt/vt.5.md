# VT 5

## NAME

vt - vt data formats

## DESCRIPTION

This manual entries describes the various data formats
used to implement the vt Store.

## ARCHIVE FILES (`.vt`)

The vt data storage system has 2 distinct components:
the block Store,
where variable sized blocks are stored
and indexed by their cryptographic hash codes,
and the file tree,
which is a directory and file tree structure whose content is also stored
in the block Store.

Because the tree is stored in the block Store,
provided one has access to the Store
an entire and arbitrary file tree
can be referenced simply
with a hashcode reference to its top Block
and a little annotation to indicate how to interpret the resulting data block
(direct or indirect, as a file or a directory).
Such a reference is a filesystem object reference
as described in vt(1), such as
`D{'vt2':meta:M{a:'o:rwx-',m:1544937484.506546},block:B{hash:H{sha1:0f4ababec84c6744ffda4f20c2c2a9706a28ab2e},span:3912}}`.

File trees stored in the vt(1) storage system
have their top entries recorded in *archive*`.vt` files
(so called because these files are also
those updated by the `vt pack` archiving command).
Whenever such a tree is updated
a new line is appended to the archive file
with a timestamped filesystem object reference.

Each line in the archive has the form:

    *iso-timestamp* *unixtime* *filesystem-object-reference* [*comment*]

The *iso-timestamp* is an ISO8601 human friendly timestamp
such as `2018-12-16T16:18:04.820552+11:00`.

The *unixtime* is a normal UNIX timestamp,
a period in seconds since the epoch midnight 1 January 1970 GMT.

The *filesystem-object-reference*
is a filesystem object reference as described in vt(1), such as
`D{'vt2':meta:M{a:'o:rwx-',m:1544937484.506546},block:B{hash:H{sha1:0f4ababec84c6744ffda4f20c2c2a9706a28ab2e},span:3912}}`.

## BINARY FORMATS

### Basic Types

The block Stores and file tree
are intended to be future proof
and as such largely do not employ fixed width data formats
such as 32 bit integers
as these have inherent upper bounds.
Instead,
unsigned integer values are stored as extensible byte sequences
and blocks of binary data are generally stored
as length prefixed data.

`bsuint`: byte serialised unsigned integer:
  a big endian byte encoding where continuation octets
  have their high bit set. The bits contributing to the value
  are in the low order 7 bits.

`bsdata`: length prefixed data:
  a data block prefixed by its length as a `bsuint`.

`bsstring`: length prefixed `utf-8` text data:
  a `bsdata` whose data block is the `utf-8` encoding of a string value.

`bssfloat`: textual floating point value:
  a `bsstring` containing the textual representation of a floaing point value.

### Block Files (.vtd)

Block files with the `.vtd` file extension
are simple sequences of binary data blocks stored as:

    block_record {
      flags: bsuint;
      zdata: bsdata;
    }

The `flags` field holds a bit array of flags
sufficient to define the flags which are set.
Presently there is just one flag:

`0x01` "COMPRESSED":
  if set then the `zdata` field
  contains the zlib compressed form of the raw data;
  otherwise it contains the raw data directly.

### Hashcodes

The primary index of the data blocks
is that of their cryptographic hashcode.
For future changes of hash function,
hashcode data includes the hash function type
so hashcodes are stored as:

    hashcode_record {
      hashtype: bsuint;
      hashdata: byte[length];
    }

The `hashtype` is a `bsuint` indicating the hash function in use.
Presently the only hash function is SHA1, whose `hashtype` value is `0`.

The `hashdata` is a direct binary dump of the hash value
whose length is defined by the hash function.
For a SHA1 hash value the `hashdata` is 20 bytes long.

### Block References

A block reference comes in several forms as outlined in vt(1).
The binary transcriptions of these forms are described below.

Every block reference is a `bsdata` length prefixed record:

    blockref_record {
      length: bsuint;
      blockref_data {
        flags: bsuint;
        span: bsuint;
        type: bsuint;       // if required; default type==HASHCODE
        type_flags: bsuint; // if required; default flags==0
        switch type {
          HASHCODE:
            hashcode: hashcode_record;
          RLE:
            octet: byte;
          LITERAL:
            block_data: bsdata;
          SUBBLOCK:
            offset: bsuint;
            super_block: blockref_record;
        }
      }
    }

`flags` is a bit field holding the following flags:

`0x01` "INDIRECT":
  if set, the block data consist of a sequence of `blockref_record`s
  instead of the actual block data;
  the actual block data are comprised from the concatenation
  of the block data of the subblocks

`0x02` "TYPED":
  if set, the `type` value is present to specify the block type;
  otherwise the `type` is the default value `HASHCODE`.

`0x04` "TYPE\_FLAGS":
  if set, the `type_flags` value is present;
  otherwise the `type_flags` value is `0`.

The `type` value has the following values:

`0` "HASHCODE":
  a hashcode block, where a hashcode is provided
  which specifies the block data.
  The tail of the `blockref_data` contains the `hashcode_record`.

`1` "RLE":
  a run length encoded block.
  The tail of the `blockref_data` is a single `octet`,
  which is repeated `span` times to construct the block data.

`2` "LITERAL":
  a literal block;
  this is usually used where a hashcode record
  would exceed the size of the block data.
  The tail of the `blockref_data` is the block data.

`3` "SUBBLOCK":
  this block's data are a subspan of another block's data
  (this block's "superblock").
  The tail of the `blockref_data` contains the `offset`
  of the block data within the superblock
  and a `blockref_record` specifying the superblock.

### Blockmap Files

Performing random access to the data within an indirect block
can be expensive as an arbitrarily deep tree of indirection
may be required.
If it is expected that a block will be accessed in such a way
it is possible to make a persistent index of the leaf block locations.

These indices are flat files containing records
consisting of `(offset,hashcode)` fields,
being the offset of the leaf block within the top block
and the hashcode of the leaf block in the index.
The length of the leaf block can be computed from the leaf offset
and the offset of the next leaf.
Once the appropriate leaf block is found
the following leaf blocks are immediately known
from the following records
making sequential file access from an arbitrary point
very efficient.

Because these files are bisected on the `offset` to locate the relevant leaf
the records need to be fixed length.
Since the top block may be of arbitrary size
the index is broken into distinct files each covering a range of offsets.
The `offset` within the index record is the modulus of the real offset
with respect to the span covered by an index file
plus the base offset of the span covered by the file.

Bcause the hashcode of the top indirect block
completely dictates its content,
a blockmap index need only ever be constructed once per top block
and may be kept persistently as a collection of blockmap files.

Blockmaps are therefore parameterised on 2 values:
the size of the block spans covered by an individual blockmap file
and the hashcode of the top block.
As such,
the blockmap files are kept in a directory tree whose internal stucture
is composed of blockmap files whose pathnames have the form:

  `mapsize:`*spansize*`/`*hashcodehex*`.`*hashtype*`/`*spanindex*`.blockmap`

The use of such a structure allows vt systems
using different blockmap span sizes and different hashcode functions
to be accomodated side by side without conflict.

For example, this path:

  `mapsize:4294967296/06a86c1bb238cf58b34d8fe140b44818fd68728d.sha1/0.blockmap`

is a blockmap file for the first blockmap index
of the top indirect block
with SHA1 hashcode `06a86c1bb238cf58b34d8fe140b44818fd68728d`
using index spans of `4294967296`
(2**32, expressable with an unsigned 4 byte value in an index record).

If the top block spans more than 4294967296 bytes
there will also be a `.../1.blockmap` file
and so on as required.

The current vt blockmap system uses blockmap records of the form:

    blockmap_record {
      offset: uint32be;
      hashcode: hashdata;
    }

where `uint32be` indicates a bigendian 32 bit unsigned integer
and `hashdata` is the raw hashcode binary dump,
so 20 bytes for a SHA1 hashcode.

### DataDir Stores

The DataDir Store is the usual local block Store type.
The on disc structure is as follow:

    ...store/
      data/
        uuid1.vtd
        uuid2.vtd
        ...
      index-hashtype-state.sqlite
      index-hashtype.indextype

thus:

`data`:
  a subdirectory containing an arbitrary number of `.vtd` block files
  which are usually named with UUIDs
  to arrange conflict free new file creation.

`index-`*hashtype*`-state.sqlite`
  an SQLite3 database containing the mapping of the block data filenames
  to numeric `filenum` values and a scan offset indicating how much of each file
  has been scanned and thus has its content recorded in the block index file.
  *hashtype* indicates the hashcode function,
  presently `sha1` for SHA1 hashcodes.

`index-`*hashtype*`.`*indextype*
  a fast binary mapping of raw hashcodes
  (such as the 20 byte SHA1 dump)
  to binary records of `(offset,filenum)`
  both of which are `bsuint` encoded.
  The corresponding `block_record`
  is located at `offset` within the block data file numbered `filenum`.
  Presently *hashtype* is `sha1`
  and the *indextype* is one of `lmdb`, `gdbm` and `kyoto`
  depending on the availability of the LBDM, GDBM
  or KyotoCabinet libraries.

The block data files and the SQLite3 indices are portable.
The `hashcode` -> `(offset,filenum)` indices are not portable.
However, on a given machine
multiple vt storage instances may share these files concurrently;
steps are taken to avoid multiple instances trying to append
to the same block data file;
each will have its own at any given time.

### Platonic Stores

A Platonic Store does not have data blocks added to it.
Instead, it is an index to an ordinary directory tree
with stable (or growing) data files within it
such as a document archive, scientific dataset or media server tree.
This is traversed regularly to maintain the index.
As with a DataDir Store, each data file is assigned a distinct `filenum`
which is used in the compact binary index.

This kind of Store is structured just like a DataDir Store
except as follows:

The `data` directory does not contain block data `.vtd` files
but instead contains a symlink following directory tree
or ordinary files
whose contents are scanned for block boundary locations
and each block indexed.
Typically the `data` directory just contains a few symbolic links
to preexisting external directory trees.

The `index-`*hashtype*`.`*indextype* index file
maps to records of `(offset,filenum,length)`
all of which are `bsuint` encoded.
The corresponding raw block data with span `length`
are located at `offset` within the ordinary file numbered `filenum`.

### Serial Protocol: General Structure

The serial protocol used to communicate between clients and Stores
is built on the `cs.packetstream` binary protocol,
which uses variable sized packets,
is bidirectional, asynchronous, and supports multiple logical channels.
Each packet is either a request or a response to a request
and each outstanding request has a distinct `tag` value
which is unique per channel.

All packets are `bsdata` encoded and have the form:

    packet_record {
      length: bsuint;
      packet_data {
        tag: bsuint;
        flags: bsuint;
        channel: bsuint;  // optional field, default value `0`
        rq_type: bsuint;  // only present in request packets
        payload: byte[];  // the remainder of the packet_data
      }
    }

The `flags` field is a bitmap with the following predefined flags:

`0x01` "HAS\_CHANNEL":
  if set, the `channel` field is present to provide the channel number;
  otherwise the channel number is `0`.

`0x02` "IS\_REQUEST":
  if set, the packet is a request packet
  and the `rq_type` field is present to specify the request type;
  otherwise the packet is a response packet
  to an outstanding request.

After stripping off these 2 bit values,
the remaining value is right shifted 2 bits
and preserved as the protocol level flags field.

The payload field is arbirary protocol specific binary data.
It may be empty.

However, the `cs.packetstream.PacketConnection` class
automatically handles the return values of the packet handler function
in use by the protocol.
When doing this it adds an additional `flags` bit
indicating success or failure
to response packets as the low order bit,
shifting the response-provided flags up an additional position.
So in responses there is an additional flag:

`0x04` "IS\_OK":
  if set the the request was correctly handled;
  otherwise there was some kind of server side failure.

As with the primary two flags
this is also stripped off
and the `flags` value shifted down one further bit position.
Thus the return from a client side request call is `(ok,flags,payload)`
where `ok` is a Boolean,
`flags` is any remaining flag values
and `payload` is the packet's trailing bytes payload field.

On the server side
`cs.packetstream.PacketConnection` request handlers thus have 5 modes
of return value at the Python level
which translate to response packets as follows:

`None`:
  the response will be `IS_OK` with no additional `flags`
  and an empty `payload`

`int`:
  the response will be `IS_OK` with additional `flags`
  provided by the `int` return value
  and an empty `payload`

`bytes`:
  the response will be `IS_OK` with no additional `flags`
  and the `bytes` as the `payload`

`str`:
  the response will be `IS_OK` with no additional `flags`
  and the `payload` will be the `str` return value
  encoded as `utf-8`.

`(int,bytes)`:
  the response will be `IS_OK` with additional `flags`
  provided by the `int` return value
  and the `bytes` as the `payload`

If a handler raises an exception
a failure packet will be returned,
with no `IS_OK` flag, no additional `flags` and an empty `payload`.

### Serial Protocol: Store Specific Protocol

The `cs.vt.stream` serial protocol
is the `cs.packetstream.PacketConnection` protocol
with the following `rq_type` values and associated values:

`0` "ADD":
  add the `payload` bytes as a data block.
  The response `payload` is a `hashcode_record`
  containing the hashcode which indexes the data block.

`1` "GET":
  fetch the data block whose hashcode is stored in the `payload`
  as a `hashcode_record`.
  The response `payload` is the data block bytes.

`2` "CONTAINS":
  test whether the data block whose hashcode is stored in the `payload`
  as a `hashcode_record` is present in the Store.
  The response will be ok if the block is present.

`3` "FLUSH":
  flush the Store to its backend substrates.

`4` "HASHCODES":
  request the available data block hashcodes
  from a starting hashcode value:

    hashcodes_request {
      hashname: bsstring
      start_hashcode: hashcode_record;  // present if "has_start_hashcode"
      length: bsuint;
    }

  The request `flags` are
  `0x01` "reverse" to return hashcodes in reverse,
  `0x02` "after" to return hashcodes `>start_hashcode`
  instead of `>=start_hashcode`
  (or `<start_hashcode` instead of `<=start_hashcode`
  if the "reverse" flag is provided)
  and `0x04` "has\_start\_hashcode" to indicate that a `start_hashcode`
  is provided, otherwise the hashcodes start from the lowest hashcode in the Store
  (or the highest hashcode if the "reverse" flag is provided).
  The `length` is the number of hashcodes to return;
  the response may be short of there are insufficient hashcodes in the Store.

  The response payload is the concatenation of the requested `hashcode_record` records.

`5` "HASHCODES_HASH":
  request the hashcode of the available data block hashcodes
  from a starting hashcode value:

    hashcodes_request {
      hashname: bsstring
      start_hashcode: hashcode_record;  // present if "has_start_hashcode"
      length: bsuint;
    }

  The request `flags` are
  `0x01` "reverse" to return hashcodes in reverse,
  `0x02` "after" to return hashcodes `>start_hashcode`
  instead of `>=start_hashcode`
  (or `<start_hashcode` instead of `<=start_hashcode`
  if the "reverse" flag is provided)
  and `0x04` "has\_start\_hashcode" to indicate that a `start_hashcode`
  is provided, otherwise the hashcodes start from the lowest hashcode in the Store
  (or the highest hashcode if the "reverse" flag is provided).
  The `length` is the number of hashcodes to return;
  the response may be short of there are insufficient hashcodes in the Store.

  The response payload is a `hashcode_record`
  containing the hashcode
  of the concatenation of the requested `hashcode_record` records.

`6` "ARCHIVE_LAST":
  request the last archive entry of a named archive:

    archive_last_request {
      archive_name: bsstring
    }

  The response will have `flags==0` if there are no entries
  or `flags==1` if there are entries.
  In the latter case the payload will consist of the entry timestamp
  as a `bssfloat` with the UNIX time of the entry
  and the binary transcription of the directory.

`7` "ARCHIVE_LIST":
  request all the archive entries of a named archive:

    archive_list_request {
      archive_name: bsstring
    }

  The payload will be the concatenation of the archive entries
  transcribed as for "ARCHIVE_LAST".

`8` "ARCHIVE_UPDATE":
  submit a new archive entry to append to a named archive:

    archive_update_request {
      archive_name: bsstring
      when: bssfloat
      dirent: binary_directory
    }

## SEE ALSO

vt(1), the vt command line tool

## AUTHOR

Cameron Simpson <cs@cskk.id.au>
