# FSTAGS 1cs

## NAME

fstags - set and query filesystem based tags

## SYNOPSIS

`fstags` *subcommand* [*arg*...]

## DESCRIPTION

The `fstags` command is the command line mode of the `cs.fstags` Python module.

By storing the tags outside filesystem objects
it is possible to tag any object (a file, a directory, etc)
and it is not necessary to have knowledge of a file's internal data format.

Filesystem tags are stored in the file `.fstags`
in the directory containing the object tagged.
The tags for a path are the cumulative set of the direct tags
(stored in the `.fstags` file in the object's directory)
and the tags of each ancestor of the object
with precedence given to the `.fstags` files closer to the object.

For example, a media file for a television episode with the pathname
`/path/to/series-name/season-02/episode-name--s02e03--something.mp4`
might obtain the tags:

    series.title="Series Full Name"
    season=2
    sf
    episode=3
    episode.title="Full Episode Title"

from the following `.fstags` entries:
* tag file `/path/to/.fstags`:
  `series-name sf series.title="Series Full Name"`
* tag file `/path/to/series-name/.fstags`:
  `season-02 season=2`
* tag file `/path/to/series-name/season-02/.fstags`:
  `episode-name--s02e03--something.mp4 episode=3 episode.title="Full Episode Title"`

### Extended Attribute Support

On supported systems,
the tags in .`fstags` are synchronised with the object's extended attributes.

By default the object's direct tag set
is stored in the `user.cs.fstags` extended attribute
in the same syntax as the tag portion of its entry in the `.fstags` file.

If other extended attribute names are present
in the `[xattr]` section of the `.fstagsrc` file
then these attributes are also updated according to the tag values.
For example, this `[xattr]` section:

    [xattr]
    user.dublincore.title = title
    user.dublincore.creator = author
    user.dublincore.subject = subject
    user.dublincore.description = description
    user.dublincore.publisher = publisher
    user.xdg.comment = comment
    user.xdg.origin.url = url
    user.xdg.publisher = publisher
    user.mime_type = mime_type

maintains the extended attributes named on the left
from the tag values named on the right.

The reverse also holds.
Just as tag updates are also synchronised
to extended attributes on save as described,
the extended attributes are read when a tag set is loaded,
and merged if the tag is not already present.

A load/save sequence thus imports tag values from extended attributes
and updates them, bringing both into alignment.

Support:
presently this is limited to Linux filesystems with extended attribute support
as Python's `os.getxattr` and `os.setxattr` functions
are only defined on that platform.
I've got a patch in progress to add support on MacOS,
which also has extended attributes.

## SUBCOMMANDS

### autotag paths...

Recurse over the filesystem objects named by *paths*
and add tags to the tagsets based on rules defined
in the `~/.fstagsrc` configuration file.

Example:

    % >>media/tv-series/season-01/tv-series--s01e01--some-title.mp4
    % fstags autotag media
    autotag 'tv-series--s01e01--some-title.mp4' + title_lc="tv-series"
    autotag 'tv-series--s01e01--some-title.mp4' + season=1
    autotag 'tv-series--s01e01--some-title.mp4' + episode=1
    % cat media/tv-series/season-01/.fstags
    tv-series--s01e01--some-title.mp4 title_lc="tv-series" season=1 episode=1

### `find` [*options*...] *path* *tag_selectors*...

Recurse over the filesystem objects under *path*
reporting paths whose tags match all the specified *tag_selector*s
(see TAG SELECTOR below).

Options:
* `--direct`:
  only consider tags directly applied to a filesystem object,
  ignoring tags from its ancestors.
* `--for-rsync`:
  instead of listing the matching paths,
  emit a list of rsync(1) include/exclude patterns
  suitable for use with the `--include-from` option
  which will cause rsync(1) to only match the found files.
  This can be used to drive an rsync based process
  such as a copy or backup
  based on the tags of the filesystem objects.
* `-o` *output_format*:
  specifed the output format as a Python formatted string.
  The default is `'{filepath}'`, producing a plain file listing.
  See OUTPUT FORMAT* below.

### `ls` [*options*...] [*paths*...]

Recurse over the filesystem objects under *paths*
(default the current directory)
listing paths and their tags.

Options:
* `--direct`:
  only report tags directly applied to a filesystem object,
  ignoring tags from its ancestors.
* `-o` *output_format*:
  specifed the output format as a Python formatted string.
  The default is `'{filepath} {tags}'`.
  See OUTPUT FORMAT* below.

### `mv` *paths*... *targetdir*

Move files and their tags into *targetdir*.

### `tag` *path* *tag_modifier*...

Apply *tag_modifiers* to *path*.
See TAG MODIFIER below.

### `tagpaths` *tag_modifier* *paths*...

Apply *tag_modifier* to multiple *paths*.
See TAG MODIFIER below.

## OUTPUT FORMAT

The `find` and `ls` subcommands accept a `-o` option
to specify the output format.
This is a Python formatted string accepting the following placeholders:
* `{basename}`: the file path basename
* `{filepath}`: the full file path
* `{filepath_encoded}`: the filepath as transcribed in a `.fstags` file
* `{`*tag_name*`}`: the value of a tag

## TAG MODIFIER

A *tag_modifier* has the form:

    [`-`]*tag_name*[`=`*tag_value*]

A leading minus ('`-`') means the modifier removes the tag;
if a *tag_value* is provided
then the tag will only be removed if its value matches *tag_value*.

Without a leading minus the modifier added the tag.

## TAG SELECTOR

A *tag_selector* has the form:

    [`-`]*tag_name*[`=`*tag_value*]

A leading minus ('`-`') means the selector rejects objects with the tag,
otherwise the object must possess that tag.
The *tag_name* is a dotted identifer accepting dashes,
such as `backup` or `mime_type` or `authorship.primary-author`.
If there is no `=`*tag_value* specified
the selector tests for the presence if the tag
otherwise it tests for the presence of the tag with the pecified *tag_value*.

Example:

    % >>media/tv-series/season-01/tv-series--s01e01--some-title.mp4
    % >>media/tv-series/season-01/tv-series--s01e02--some-other-title.mp4
    % fstags find media season=1 episode=2
    media/tv-series/season-01/tv-series--s01e02--some-title.mp4

## CONFIGURATION: ~/.fstagrsrc

Default operation may be altered with the `~/.fstagsrc` file,
which is a `.ini` formatted file with the following sections:
* `[general]`: general settings
* `[xattr]`: extended attribute settings
* `[autotag]`: rules for autotagging files using regular expressions

See fstagsrc(5cs) for details.

## EXAMPLES

## SEE ALSO

fstags(5cs), the format of the `.fstags` file

fstagsrc(5cs), the format of the `.fstagsrc` configuration file

tagged-backup(1cs), a multivolume histbackup based backup script
using tags to identify the volume which should store particular files

## AUTHOR

Cameron Simpson <cs@cskk.id.au>
