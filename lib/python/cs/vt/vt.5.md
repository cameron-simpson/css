# VT 5

## NAME

vt - vt archive format

## DESCRIPTION

File trees stored in the vt(1) storage system
have their top entries recorded in *archive*`.vt` files.
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

## SEE ALSO

vt(1), the vt command line tool

## AUTHOR

Cameron Simpson <cs@cskk.id.au>
