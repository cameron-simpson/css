#!/usr/bin/python
#
# Utility functions and classes for configuration files.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Utility functions and classes for .ini style configuration files.
'''

import os
import os.path
import sys
from collections.abc import Mapping
from threading import RLock
from cs.deco import cachedmethod
from cs.fileutils import file_property
from cs.py3 import ConfigParser, StringTypes
from cs.tagset import TagSet
from cs.threads import locked

__version__ = '20220430-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.fileutils',
        'cs.py3',
        'cs.tagset',
        'cs.threads',
    ],
}

def load_config(config_path, parser=None):
  ''' Load a configuration from the named `config_path`.

      If `parser` is missing or `None`, use `SafeConfigParser` (just
      `ConfigParser` in Python 3).
      Return the parser.
  '''
  if parser is None:
    parser = ConfigParser
  CP = parser()
  CP.read([config_path])
  return CP

class ConfigWatcher(Mapping):
  ''' A monitor for a windows style `.ini` file.
      The current `SafeConfigParser` object is presented as the `.config` property.
  '''

  def __init__(self, config_path):
    self._lock = RLock()
    if not os.path.isabs(config_path):
      config_path = os.path.abspath(config_path)
    self._config__filename = config_path
    self._config_lock = self._lock
    self._mapping = None
    self._watchers = {}

  def __str__(self):
    return "ConfigWatcher(%r)" % (self._config__filename,)

  @file_property
  def config(self, filename):
    ''' Live configuration.
    '''
    self._mapping = None
    return load_config(filename)

  @property
  def path(self):
    ''' The path to the config file.
    '''
    return self._config__filename

  def as_dict(self):
    ''' Construct and return a dictionary containing an entry for each section
        whose value is a dictionary of section items and values.
    '''
    d = {}
    config = self.config
    if config is not None:
      # file exists and was read successfully
      for section in config.sections():
        d[section] = self[section].as_dict()
    return d

  def section_keys(self, section):
    ''' Return the field names for the specified section.
    '''
    CP = self.config
    if CP is None or (section != 'DEFAULT' and not CP.has_section(section)):
      return []
    return [name for name, value in CP.items(section)]

  def section_value(self, section, key):
    ''' Return the value of [section]key.
    '''
    CP = self.config
    if CP is None or not CP.has_option(section, key):
      raise KeyError(key)
    return CP.get(section, key)

  #### Mapping methods.
  @locked
  def __getitem__(self, section):
    ''' Return the `ConfigSectionWatcher` for the specified section.
    '''
    watchers = self._watchers
    if section not in watchers:
      watchers[section] = ConfigSectionWatcher(self, section)
    return watchers[section]

  def __iter__(self):
    CP = self.config
    if CP is None:
      return iter(())
    return iter(CP.sections())

  def __len__(self):
    n = 0
    for _ in self:
      n += 1
    return n

class ConfigSectionWatcher(Mapping):
  ''' A class for monitoring a particular clause in a config file.
  '''

  def __init__(self, config, section, defaults=None):
    ''' Initialise a `ConfigSectionWatcher` to monitor a particular section
        of a config file.
        `config`: path of config file or `ConfigWatcher`
        `section`: the section to watch
        `defaults`: the defaults section to use, default 'DEFAULT'
    '''
    if isinstance(config, StringTypes):
      config_path = config
      config = ConfigWatcher(config_path)
    if defaults is None:
      defaults = 'DEFAULT'
    self.config = config
    self.section = section
    self.defaults = defaults

  def __str__(self):
    return "%s[%s]" % (self.path, self.section)

  def __repr__(self):
    return repr(self.as_dict())

  @property
  def path(self):
    ''' The pathname of the config file.
    '''
    return self.config.path

  def as_dict(self):
    ''' Return the config section as a `dict`.
    '''
    d = {}
    for k in self:
      d[k] = self[k]
    return d

  def keys(self):
    ''' Return the keys of the config section.
    '''
    ks = set(self.config.section_keys(self.section))
    if self.section != self.defaults:
      ks.update(set(self.config.section_keys(self.defaults)))
    return list(ks)

  #### Mapping methods.
  def __getitem__(self, key):
    v = self.config.section_value(self.section, key)
    return v

  def __iter__(self):
    return iter(self.keys())

  def __len__(self):
    return len(self.keys())

class HasConfigIni:
  ''' Class for objects with a `config.ini` file.
      A section of the config is designated "our" configuration
      and its fields parsed into a `TagSet`;
      in particular the field values use the `TagSet` transcription syntax.

      The default implementation is expected to be mixed into a
      class with a `.pathto(rpath)` method, such as one which
      inherits from `HasFSPath`.

      The mixin provides the following attributes:
      * `config`: an on demand property which is a `TagSet` made
        from the configuration file section
      * `config_ini`: the relative path to the configuration file
      * `configpath`: the full pathname of the configuration file
      * `config_flush()`: update the configuration file if the tags
        have been modified
  '''

  # the default configuration file relative pathname
  CONFIG_INI = 'config.ini'

  def __init__(self, section, config_ini=None):
    ''' Initialise the configuration.
    '''
    if config_ini is None:
      config_ini = self.CONFIG_INI
    self.config_ini = config_ini
    self.__section = section
    self._config = None

  @property
  def configpath(self):
    ''' The path to the `config.ini` file.
    '''
    return self.pathto(self.config_ini)  # pylint: disable=no-member

  @property
  @cachedmethod
  def config(self):
    ''' The configuration as a `TagSet`.
    '''
    return TagSet.from_ini(self.configpath, self.__section, missing_ok=True)

  def config_flush(self):
    ''' Save the current configuration to the `config.ini` file if `self.__modified`.
    '''
    config = self._config
    if config is not None and config.modified:
      config.save_as_ini(self.configpath, self.__section)
      config.modified = False

if __name__ == '__main__':
  import cs.configutils_tests
  cs.configutils_tests.selftest(sys.argv)
