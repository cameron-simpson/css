#!/usr/bin/python
#
# Utility functions and classes for configuration files.
#       - Cameron Simpson <cs@zip.com.au>
#

DISTINFO = {
    'description': "utility functions for .ini style configuration files",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': [ 'cs.py3', 'cs.fileutils', 'cs.threads', 'cs.logutils' ],
}

import os
import os.path
import sys
from collections import Mapping
from threading import RLock
from cs.py3 import ConfigParser, StringTypes
from cs.fileutils import file_property
from cs.threads import locked, locked_property
from cs.logutils import Pfx, info, D, X

def load_config(config_path, parser=None):
  ''' Load a configuration from the named `config_path`.
      If `parser` is missing or None, use SafeConfigParser (just
      ConfigParser in Python 3).
      Return the parser.
  '''
  if parser is None:
    parser = ConfigParser
  CP = parser()
  with open(config_path) as fp:
    CP.readfp(fp) 
  return CP

class ConfigWatcher(Mapping):
  ''' A monitor for a windows style .ini file.
      The current SafeConfigParser object is presented as the .config property.
  '''

  def __init__(self, config_path):
    self._lock = RLock()
    if not os.path.isabs(config_path):
      config_path = os.path.abspath(config_path)
    self._config_path = config_path
    self._config_lock = self._lock
    self._watchers = {}

  def __str__(self):
    return "ConfigWatcher(%r)" % (self._config_path,)

  @file_property
  def config(self, path):
    self._mapping = None
    return load_config(path)

  @property
  def path(self):
    return self._config_path

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
    return [ name for name, value in CP.items(section) ]

  def section_value(self, section, key):
    CP = self.config
    if CP is None or not CP.has_option(section, key):
      raise KeyError(key)
    return CP.get(section, key)

  #### Mapping methods.
  @locked
  def __getitem__(self, section):
    ''' Return the ConfigWatcher for the specified section.
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
    for i in self:
      n += 1
    return n

class ConfigSectionWatcher(Mapping):
  ''' A class for monitoring a particular clause in a config file.
  '''

  def __init__(self, config, section, defaults=None):
    ''' Initialise a ConfigSectionWatcher to monitor a particular section
        of a config file.
        `config`: path of config file, or ConfigWatcher
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
    d = {}
    for k in self.keys():
      v = self[k]
      d[k] = self[k]
    return repr(d)

  @property
  def path(self):
    ''' The pathname of the config file.
    '''
    return self.config.path

  def as_dict(self):
    d = {}
    for k in self:
      d[k] = self[k]
    return d

  def keys(self):
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

if __name__ == '__main__':
  import sys
  import cs.configutils_tests
  cs.configutils_tests.selftest(sys.argv)
