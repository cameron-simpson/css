# VTRC 5

## NAME

vtrc - vt configuration file format

## DESCRIPTION

The default configuration for the vt(1) command
is contained in the file `~/.vtrc`
and takes the form of a Windows style `.ini` file
with multiline clauses commencing with a clause name in square brackets
and continuing with a series of *param* = *value* lines.

## CLAUSES

### The GLOBAL section

Some overarching values for the vt(1) command
are specified in the `GLOBAL` clause:

`basedir`
  The working area where the vt system keeps its state
  such as `~/var/vt`.
  The default location of an individual Store's data
  is its clause name appended to the `basedir`.

`blockmapdir`
  The area where vt-blockmap(5) files are kept,
  an efficiency data structure to bypass the need to descend
  an indirect block's subblock tree to locate leaf blocks.
  This specifies a directory path,
  either directly such as `~/var/vt/blockmaps`
  or with reference to a store configuration clause
  either bare (`[`*clausename*`]`)
  implying `[`*clausename*`]/blockmaps`
  or explicitly naming the store subdirectory
  such as `[metadata]/blockmaps`.
  The path after the clause name specifies a subdirectory
  of that Store's top directory.

Example:

    [GLOBAL]
    # base area for Store state, one subdirectory per Store as required
    basedir = ~/var/vt
    # default location for persistent blockmaps
    # we use a common one since it is totally sharable
    blockmapdir = [metadata]/blockmaps

### Store specification sections

The other clauses specify various Stores.
See vt(1) for descriptions of the various Store types.
Each clause requires a `type` parameter specifying the Store type
and has various parameters as detailed below.

#### `type = datadir`

A datadir Store, with blocks stored in local `.vtd` files
in a `data` subdirectory.
Parameters:

`path`:
  the location of the Store,
  by default *basedir*`/`*clausename*.
`raw`:
  Default: `False`.
  If true this is a `RawDataDir` otherwise a `DataDir`.

#### `type = filecache`

A file cache Store,
with blocks stored in local `.vtd` files in a `data` subdirectory.
Parameters:

`backend`:
  a backend Store.
  Any blocks saved to the file cache are also copied to the backend.

`max_files`:
  the maximum number of `.vtd` files to keep in the cache.

`max_file_size`:
  the high water mark for cache files.
  When a file exceeds this size
  a new file is commenced
  and older files discarded if there are more than *max_files* files.
  The size can have a scale factor,
  for example `8 MiB`.

`path`:
  the location of the Store,
  by default *basedir*`/`*clausename*.

#### `type = memory`

A memory cache Store,
with blocks stored in memory in a mapping keyed on their hashcode.
Parameters:

`max_data`:
  the maximum amount of block data to keep in the cache.
  Older blocks will be forgotten to keep below this threshold.

#### `type = platonic`

A Platonic Store,
where an ordinary file tree
such as a document archive or a media server
is used as the backing store for data blocks.
Parameters:

`path`:
  the location of the Store,
  by default *basedir*`/`*clausename*.

`meta`:
  optional Store to hold the directory tree metadata.

#### `type = proxy`

A Proxy Store,
combining various other Stores.
Parameters:

`save`:
  comma separated list of Stores
  to which to save new blocks.

`read`:
  primary comma separated list of Stores from which to read blocks

`save2`:
  comma separated list of Stores to which to save blocks
  which failed to be saved to a Store from `save`

`read2`:
  secondary comma separated list of Stores from which to read blocks
  for blocks not found in `read`

`copy2`:
  comma separated list of Stores to which to save blocks
  which are obtained via `read2`

#### `type = socket`

A stream store presented via a UNIX domain socket.
Parameters:

`socket_path`:
  the path to the socket, default from the clause name;
  relative paths are resolved with respect to `basedir`

#### `type = tcp`

A stream store presented via a TCP *host*`:`*port* address.
Parameters:

`host`
  the hostname for the server, default from the clause name
`port`
  the port number

## SEE ALSO

vt(1), the vt command line tool

## AUTHOR

Cameron Simpson <cs@cskk.id.au>
