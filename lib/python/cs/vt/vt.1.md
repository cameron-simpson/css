# VT 1

## NAME

vt - command line interface to the vt data store

## SYNOPSIS

`vt` [*option*...] [`profile`] *subcommand* [*arg*...]

## DESCRIPTION

The vt storage system consists of two parts:
a Store, which is a collection of data blocks indexed by their hashcodes;
a file tree, which is a representation of a directory tree and its files.

Because blocks are indexed by the hashcode of their contents
repeated appearance of the same contents
do not consume additional copies of the blocks in the Store.
Repeated content may be as simple as multiple copies of the same file
but also repeated occurences of data within a file.

The vt(1) command provides convenient access to the system,
notably via the following subcommands:

* `pack` *path*:
  copy the contents of the directory *path*
  into the Store,
  record its reference in the file *path*`.vt`
  then remove *path*.
* `unpack` *path*`.vt`:
  copy the contents referenced by the file *path*`.vt`
  from the Store
  to the directory *path*.
* `mount` *path*`.vt`:
  use FUSE to mount the contents referenced by the file *path*`.vt` as *path*,
  fetching contents from the Store as needed.
  The mounted tree can be accessed or modified like any other filesystem
  and the file *path*`.vt` is updated to refer to the new contents on unmount.

These and other subcommands are detailed below.

The remaining object encountered is the "archive",
which is simply a text file
containing a record of file tree top directories.
Conventionally these files have a `.vt` suffix.
The `pack` subcommand creates or updates these,
`unpack` command` extracts these,
the `mount` subcommand mounts these
and updates the archive which changes on unmount.

Archives are referenced in two ways:

* *path*`.vt`: as a plain pathname to a file ending in `.vt`
* `[`*clause*`]`*name*: indicating an archive associated
  with a Store.
  The *name* is optional, as Stores have a default archive file,
  but otherwise should be an identifier.

The latter form accesses an archive file associated with a Store
and provide convenient access to file trees from that Store.
The *name* is optional as each Store has a default archive.

## GETTING STARTED

Run the command `vt init`;
this will create an initial `~/.vtrc` file
and empty default Stores.

## OPTIONS

`-C` *cache_store*

  Specify the Store to use as a cache.
  Specify `NONE` for no *cache_store*.
  The default cache
  comes from the environment variable `$VT_CACHE_STORE`,
  otherwise the configuration clause `[cache]`
  defines the *cache_store*.

`-S` *main_store*

  Specify the main Store to use.
  The default *main_store*
  comes from the environment variable `$VT_STORE`,
  otherwise the configuration clause `[default]`
  defines the *main_store*
  except for the `serve` subcommand
  which uses `[server]` and ignores the `$VT_STORE` environment variable.

`-f` *config*

  Configuration file.
  The default configuration file
  is specified by the `$VT_CONFIG` environment variable,
  otherwise the path `~/.vtrc` is used.

`-q`

  Quiet; not verbose.
  This is the default if the standard error output is not a tty.

`-v`

  Verbose; not quiet.
  This is the default if the standard error output is a tty.

If a *cache_store* and a *main_store* are both specified
then access is via a proxy Store set up as:

    proxy(
        read=cache_store,
        read2=main_store,
        copy2=cache_store,
        save=cache_store:main_store)

Proxy Stores are described in the STORE TYPES section below.

## SUBCOMMANDS

`config`

  Recite the configuration in .ini format.

`init`

  Initialise.
  Create the .vtrc if missing, with the default configuration.
  Create the directories for any missing local datadir Stores
  from the configuration.

`mount` [*option*...] *special* [*mountpoint* [*subpath*]]

  *Note*: the mount facility requires the `llfuse` Python module
  which is not an automatic requirement of the `cs.vt` package.

  Mount the storage specified by *special* on *mountpoint*
  presenting the directory tree from *subpath* downwards
  at the mount point.
  For some *specials* there is a default *mountpoint*,
  allowing the *mountpoint* to be omitted.

  `-a`:
  if *special* is an archive
  then all of the content references
  are presented as subdirectories
  whose names are the ISO8601 formatted timestamp
  of each reference.
  This view sets the `readonly` mount option.

  `-o` {`append_only`,`readonly`}:
  set various mount options.
  `append_only`:
  a lossless "append only mode":
  files and directories may not be removed
  and file contents may not be truncated or overwritten;
  files, for example log files, may be appended to.
  `readonly`:
  files and directories may not be modified.

  `-r`:
  This is a shorthand for `-o readonly`.

  The *special* specifies the directory contents.
  It may be *path*`.vt`:
  a vt(5) archive file - the latest entry is mounted;
  a `[`*clause*`]`*archive* archive reference;
  a content directory specification
  recognised by a leading `D{` and a trailing `}`
  (see CONTENT REFERENCES below).

  The *mountpoint* is a path to a directory
  on which to mount the content specified by *special*.
  If the directory does not exist it will be created
  and it will be removed after unmount.
  If omitted the *mountpoint* will inferred from the *special*:
  for a vt(5) archive file path, the basename of *path*;
  for a `[`*clause*`]`*archive*
  it will be *archive* unless that is empty
  in which case it will be *clause*;
  for a content directory specification
  the name of the directory.

  If the *subpath* is specified,
  that subtree of *special* will be presented on the mount point.

`pack` *path*

  Copy the contents of *path* into the Store,
  record the reference to the copy in the file *path*`.vt`,
  remove *path*.

`pullfrom` *other_store* *objects*...

  Pull blocks from the Store *other_store*
  into the default Store
  to cover the supplied *objects*.
  This ensures that the default Store
  contains all the Blocks related to each *object*.
  Each *object* may be a content reference
  such as a content directory specification,
  but may also be the pathname of a "datadir" Store
  or a `.vtd` data file;
  in this latter case the Blocks come directly
  from the Store or data file respectively
  instead of from *other_store*.

`pushto` *other_store* *objects*...

  Push blocks from the default Store
  to the Store *other_store*
  to cover the supplied *objects*.
  This ensures that *other_store*
  contains all the Blocks related to each *object*.
  Each *object* may be a content reference
  such as a content directory specification,
  but may also be the pathname of a "datadir" Store
  or a `.vtd` data file;
  in this latter case the Blocks come directly
  from the Store or data file respectively
  instead of from the default Store.

`serve` [*address*]

  Present the contents of the main Store at *address*
  for use by other vt clients.
  The default *address* is specified by the `address` field
  of the `[server]` clause from the vtrc(5) configuration file.

  The address `DEFAULT` is a syntactic placeholder
  and indicates the default address specified by the `[server]` clause.

  The address `-` presents the main Store
  via a serial protocol on the standard input and standard output.

  If the *address* contains a slash (`/`)
  it is taken to the path to a UNIX domain socket
  on which to accept connections;
  the socket will be created if necessary.
  Each connection serves the main Store via the serial protocol.

  If the *address* contains a colon (`:`)
  it is taken to be a *host*`:`*port*
  on which to accept TCP connections.
  Each connection serves the main Store via the serial protocol.

`unpack` *path*`.vt`

  Fetch the last reference from the archive file *path*`.vt`
  and copy the contents out as the directory *path*.

## STORE SPECIFICATIONS

There are several types of block Stores
including a proxy Store for combining several separate Stores.
See the STORE TYPES section for a description of each Store type.
Stores may be specified in the `~/.vtrc` configuration file
as described by the vtrc(5) manual entry
or directly as text strings as described here.

A textual Store specification
is a comma separated list of single Store specifictions,
for example:

    [trove],tcp:server:port

which indicates the Store specified by the `[trove]` configuration clause
and the Store accessed via the serial protocol over TCP
at the host `server` on port `port`.

When the list has more than one member
it it used to construct a proxy Store
where new data blocks are saved to the first Store in the list
and requested data blocks are fetched first from the first Store in the list
and secondarily from the following Stores
if the first Store does not contain the block.

`"`*text*`"`
  This syntax is for embedding *text* as a standalone string
  in a longer Store specification
  where it might otherwise be parsed directly.
  At the top level, *text* should itself be a Store specification.

`[`*clausename*`]`
  This syntax specifies a Store
  which is defined by the clause named *clausename*
  in the `~/.vtrc` configuration file.

`/`*path* or `./`*path*
  This specifies the path to local filesystem resource
  presenting a Store.
  If *path* ends in `.sock` it is taken to be a UNIX domain socket
  which serves a Store.
  Otherwise, if *path* is a directory it is taken to be a "datadir" store.
  TO DO:
  if *path* ends in `.vtd` it is taken to be a flat data block storage file
  as defined in vtd(5).

`!`*shcmd*
  This specifies a Store whose contents are served
  by the serial protocol
  as the standard input and standard output of the sh(1) command *shcmd*.

*storetype*`(`*param1*`=`*value1*[`,`*param2*`=`*value2*...]`)`
  This specifies a Store of type *storetype*
  further specified by the supplied *param*`=`*value* pairs.
  This is equivalent to a configuration clause of the form:

    [clausename]
    type = *storetype*
    *param1* = *value1*
    *param2* = *value2*
    ...

*storetype*`:`*parameters*

  This is a legacy syntax superceded by the *storetype*`(`...`)` syntax above.
  Currently it only supports the specification
  `tcp:`*host*`:`*port*
  which is equivalent to the configuration clause:

    [clausename]
    type = tcp
    address = *host*:*port*

## CONTENT REFERENCES

Content references
are used to designate the location of various types of object within a Store
and have textual transcriptions designed to be compact,
to be friendly to the human eye
and to fit on a single line.
They have a JSON-like syntax,
but are more compact (no whitespace),
have bare field names
and are not quite as regular.

The following references are understood:

### Basic Types

*unsigned_int*
  Unsigned integers are represented as decimal values.

*float*
  Floating point values are transcribed using Python's `%f` format specifier.

"*text*"
  Strings are represented using double quotes in JSON syntax.

`{`*mapping*`}`
  A Python dict is represented in compact JSON syntax
  with no whitespace around the delimiters `:` and `,`.

`H{`*hashtype*`:`*hashcode*`}`
  A block's hashcode.
  *hashtype* is the flavour of hashcode;
  presently always `sha1`.
  *hashcode* is the hexadecimal transcription of the hashcode's bytes.
  Example:

    H{sha1:2bb08bf55222fe43d154db50fb6bc7386457b12a}

### Block References

`B{hash:`*hashcode*`,span:`*length*`}`
  A direct block reference.
  This refers to a block whose data has the hashocde *hashcode*
  and whose data is of length *length*.
  Example:

    B{hash:H{sha1:471b77709dac3f7253acfba3e37050409b0c3649},span:5850}

`I{hash:`*hashcode*`,span:`*length*`}`
  An indirect block reference.
  This references to a block with hashcode *hashcode*
  whose contents is a sequence of binary serialised block references
  to subblocks referring to block data;
  each subblock may itself be an indirect block
  or another kind of direct block.
  The overall block data
  is the concatenation of the data from each leaf block (a nonindirect block)
  in order.
  The `span` field is the overall length of the leaf data.
  Example:

    I{hash:H{sha1:471b77709dac3f7253acfba3e37050409b0c3649},span:5887650}

`RLE{span:`*length*`,octet:`*value*}`
  A run length encoding block.
  The data for this block
  consists of *length* repetitions of the octet with ordinal *value*.
  Example:

    RLE{span:1234,octet:32}

`LB{*texthexified_data*}`
  A literal block.
  The data for this block
  are the decoded octets from *texthexified_data*,
  a largely hexadecimal byte transcription
  where runs of certain human readable octets
  (were they ASCII)
  are presented directly between `[` and `]` brackets;
  this is slightly more compact and also aids human inspection of the data
  where there are meaningful text fields embedded,
  such as filenames in a directory.
  Example:

    LB{471b77709d[name1]ac3f7253[name2]acfba3e37050409b0c3649}

`SubB{block:`*blockref*`,offset:`*offset*`,span:`*length*`}`
  A subblock, whose data is obtained from a subrange of another block.
  (Note: this is not a "subblock" in the sense of the subsidiary blocks
  referenced by an indirect block.)
  *blockref* is the block reference of the superblock.
  *offset* is the octet offset within the superblock.
  *span* is the length of the subblock's data within the data of the superblock.
  Example:

    SubB{block:I{hash:H{sha1:471b77709dac3f7253acfba3e37050409b0c3649},span:5887650},offset:128,span:512}

### Filesystem References

Filesystem entries follow a similar pattern to the transcriptions above.
They may have an optional leading *name* followed by a colon (`:`),
then a comma separated sequence of parameters and values.

The common parameters are as follows:

`block`
  A block reference specifying the objects contents.
  For a file this holds the file data.
  For a directory this holds a binary transcription
  of the directory entries,
  themselves filesystem object references.

`meta`
  The object's metadata,
  used to represent file permissions and ACLs, modification times
  and whatever other non file data attributes an object may have.

`prevblock`
  The object's previous state, if recorded.
  This supports per object history.
  To avoid an indefinite regress
  this is a block reference to a block
  whose content is the binary transcription
  of the previous filesystem reference.

`uuid`
  A type 4 UUID as specified by RFC4122, as a string.
  Persistent filesystem objects are identified by a GUUID.
  This is used to recognise the same entity
  when reconciling different versions of a file tree
  and in the equivalent of UNIX hard links.

The concrete transcriptions are as follows:

`D{`[*name*`:`]*param*`=`*value*`,`...`}`
  A directory whose block contents is a sequence
  of named filesystem entries comprising the directory entries.
  Example:

    D{'vt2':meta:M{a:'o:rwx-',m:1544937484.506546},block:B{hash:H{sha1:0f4ababec84c6744ffda4f20c2c2a9706a28ab2e},span:3912}}

`F{`[*name*`:`]*param*`=`*value*`,`...`}`
  A regular file whose contents are designated with a block reference.

`Indirect{`[*name*`:`]*param*`=`*value*`,`...`}`
  A reference to another filesystem object by its `uuid` value.
  Unlike all other filesystem references,
  the `uuid` parameter of an indirect filesystem reference
  identifies the target object,
  not the indirect object itself.
  This is used to implement the equivalent of UNIX hard links.

`Symlink{`[*name*`:`]*param*`=`*value*`,`...`}`
  A reference to another filesystem object using a file path.
  This is the equivalent of a UNIX symbolic link.
  The reference path is part of the metadata in the `pathref` attribute.

## STORE TYPES

### Datadir Store

A datadir store is the primary form of local Store.
It consists of a directory containing a collection of `.vtd` files
(see vtd(5))
containing data blocks inside the subdirectory `data`
and an index used to locate these blocks from their hashcodes.

The index consists of 2 parts.

There is an index of hashcodes to block locations
within the various `.vtd` data files.
Presently that is an LMDB index.
For compactness, each entry is of the form (*filenum*,*offset*).
The *filenum* refers to one of the `.vtd` files.
In order that a datadir may be shared by different clients,
each adding data to the Store,
there is one one one writing client per data file;
if multiple clients are adding data,
each has its own data file.
In order for there to be easy collision free
aadition of new data files when a client needs one
the `.vtd` files are named with UUIDs.
As each new data file is created it is allocated its own *filenum*
for use in the hashcode index.

The mapping of *filenum*s to `.vtd` filenames
is kept in an SQLite database
because that is both portable and shareable.

### Memory Cache Store

A memory cache store is a lossy in-memory mapping of hashcodes
to bytes objects, each being a data block.
It has an upper bound in size and discards older blocks as that threshold is reached
by addition of new blocks.

### File Cache Store

A file cache Store is a lossy on disc Store
very similar to a datadir Store.
It has an upper limit n the number of `.vtd` files it keeps.
When a growing `.vtd` file reaches its high water mark
and a new one is required,
enough old `.vtd` files are removed to allow the new file.

### Stream Stores

Stream Stores are accessed over an asynchronous binary protocol.
They are typically remote.
TCP stores and socket Stores are examples of this type.
The server side is normally presented by a `vt serve` command.

### Platonic Store

A Platonic Store is a local Store
whose data are contained in a regular file tree
such as a collection of manual PDFs
or a media collection on a RAID.
The name comes from
Plato's [https://en.wikipedia.org/wiki/Theory_of_forms][Theory of forms],
where the regular file trees are the ideal versions of their data,
used as backing storage for vt file trees offering that data.

Like a datadir Store
it has a `data` subdirectory pointing at the regular files
containing the data to be served.
Typically this just contains symbolic links
to whatever reference trees are to be offered.

The Store scans new files as they appear in the reference trees
and maintains an index of block hashcodes referring
to the locations in the reference files.
It also maintains an archive `.vt` file
containing a reference to the logical file tree
obtained by following the symbolic links.
Like any other archive file
it may be unpacked or mounted.

### Proxy Store

A proxy Store is used to manage access to multiple other Stores.
Its parameters are as follows:

`save`
  A sequence of Stores to which to save data blocks.
  The proxy Store is read only if this sequence is empty.

`save2`
  A sequence of Stores to which to save data blocks
  if any saves to the `save` list fail.
  This would typically be some kind of local spool Store
  which can be pushed to the normal save Stores
  at a later time as a maintenance operation.

`read`
  A sequence of Stores from which to obtain data blocks.
  These would normally be local low latency Stores.

`read2`
  A secondary sequence of Stores from which to obtain data blocks
  if they were not available from the Stores in `read`.

`copy2`
  A sequence of Stores to which to copy any data blocks
  obtained via the `read2` sequence.

`archives`
  A comma separated list of `[`*clause*`]`*ptn* items
  associating Stores with filename glob patterns *ptn*.
  Looking up a `[`*clause*`]`*name* archive reference
  via a ProxyStore
  matches *name* against the *ptn* glob
  and passes the lookup to the first Store
  whose *ptn* patches the *name*.

Example configuration file clause:

    [laptop]
    type = proxy
    save = [trove],[home_server]
    save2 = [spool]
    read = [trove],[ideal],[spool]
    read2 = [home_server]
    copy2 = [trove]
    archives = [ideal]ideal,[trove]*

This clause is for a laptop with limited storage.
Saves are stored to its `[trove]`,
an essentially permanent local Store,
and to `[home_server]`,
the network accessable vt service
with the home store of almost everything on its RAID.
You might typically arrange such access over a persistent ssh port forward.
Should a save fail,
as when there is a lack of access to the home server,
the unsaved blocks are saved to `[spool]`,
a local store of blocks needing to be spooled to the home server.
Reads are first performed against
the local `[trove]`,
the local `[ideal]` Store
and whatever is presently in the `[spool]`.
If a block is not present in any of these
it is sought from the `[home_server]` Store.
Any blocks retrieved from the home server
via the `read2` sequence are copied into the local `[trove]`
so that they are available locally in the future.

An archive loopup for the name `ideal` is obtained via the `[ideal]` Store
and all other names are obtained via the `[trove]` Store.
Note that this only controls where archive files are found;
any block lookup follows the normal flow of the ProxyStore.

## ENVIRONMENT

`$VT_CONFIG`: the path to the configuration file. Default: `$HOME/.vtrc`

`$VT_STORE`: the default Store specification.
Default from the `[default]` clause of the configuration.

`$VT_CACHE_STORE`: the default cache Store specification.
Default from the `[cache]` clause of the configuration.

## SEE ALSO

vt(5), the binary data formats in use

vtrc(5), the configuration file format used in `~/.vtrc`

## AUTHOR

Cameron Simpson <cs@cskk.id.au>
