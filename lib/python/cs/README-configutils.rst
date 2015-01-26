Configuration file utility functions.
=====================================

Classes:

ConfigWatcher
-------------

A monitor for a .ini style configuration file, allowing outside users to update the file on the fly. It presents a mapping interface of section names to ConfigSectionWatcher instances, and a .config property returning the current ConfigParser instance.

ConfigSectionWatcher
--------------------

A monitor for a section of a .ini style configuration file, allowing outside users to update the file on the fly. It presents a mapping interface of section field names to values.
