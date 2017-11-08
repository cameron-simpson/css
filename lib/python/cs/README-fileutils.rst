Convenience functions and classes for files and filenames/pathnames.
====================================================================

* Pathname: subclass of str with convenience properties useful for pathnames

* BackedFile: a RawIOBase implementation that uses a backing file for initial data and writes new data to a front file

* FileState: a signature object for a file state derived from os.stat or os.lstat or os.fstat; has .mtime, .size, .dev and .ino attributes

* Tee: an output file-like object for copying data to multiple output files

* abspath_from_file: restore relative path with respect to another path, as for an include filename

* read_from: generator yielding text or data from an open file until EOF

* lines_of: generator yielding lines of text from an open file until EOF

* compare: compare two filenames or file-like objects for content equality

* @file_property: decorator for a caching property whose value is recomputed if the file changes

* make_file_property: constructor for variants on @file_property

* @files_property: decorator for a caching property whose value is recomputed if any of a set of files changes

* make_files_property: constructor for variants on @files_property

* shortpath: return `path` with the first matching leading prefix replaced with short form such as "~/" or "$LOGDIR/" etc

* longpath: the inverse of shortpath

* mkdirn: create a new directory named path+sep+n, where `n` exceeds any name already present

* poll_file: watch a file for modification by polling its state as obtained by FileState

* rewrite: rewrite the content of a file if changed; with an assortment of modes

* rewrite_cmgr: a context manager made from rewrite()
