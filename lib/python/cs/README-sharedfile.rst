Facilities For Shared Access To Files
=====================================

* lockfile: context manager to take a lock file around an operation, such as access to a shared file

* SharedAppendFile: a base class to share a modifiable file between multiple users

* SharedAppendLines: a subclass of SharedAppendFile which shares updates in units oftext lines
