#!/usr/bin/python
#
# Utility functions and classes for configuration files.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import os.path
import sys
if sys.hexversion < 0x03000000:
  import ConfigParser as configparser
else:
  import configparser
from threading import Lock
from cs.fileutils import file_property
from cs.logutils import Pfx, info, D

def load_config(config_path, parser=None):
  ''' Load a configuration from the named `config_path`.
      If `parser` is missing or None, use configparser.SafeConfigParser.
      Return the parser.
  '''
  if parser is None:
    parser = configparser.SafeConfigParser
  CP = parser()
  with open(config_path) as fp:
    CP.readfp(fp) 
  return CP

class ConfigWatcher(object):
  ''' A monitor for a windows style .ini file.
      The current SafeConfigParser object is present in the .parser attribute.
  '''
  def __init__(self, config_path):
    self._config_path = config_path
    self._config_lock = Lock()

  @file_property
  def config(self, path):
    return load_config(path)

  @property
  def path(self):
    return self._config_path

  def as_dict(self):
    d = {}
    config = self.config
    for section in config.sections():
      d[section] = dict(config.items(section))
    return d

class ConfigSectionWatcher(object):
  ''' A class for monitoring a particular clause in a config file.
  '''

  def __init__(self, config_path, section, defaults=None):
    ''' Initialise a ConfigSectionWatcher to monitor a particular section
        of a config file.
    '''
    if not os.path.isabs(config_path):
      config_path = os.path.abspath(config_path)
    self.section = section
    self.defaults = defaults
    self.configwatcher = ConfigWatcher(config_path)

  def __str__(self):
    return "%s[%s]%r" % (self.path, self.section, self)

  def __repr__(self):
    d = {}
    for k in self.keys():
      d[k] = self[k]
    return repr(d)

  @property
  def path(self):
    ''' The pathname of the config file.
    '''
    return self.configwatcher.path

  @property
  def config(self):
    ''' The current ConfigParser.
    '''
    return self.configwatcher.config

  def keys(self):
    ''' Return the fieldnames in this config section.
    '''
    config = self.config
    section = self.section
    K = set()
    if self.defaults:
      K.update(self.defaults.keys())
    if config.has_section(section):
      K.update(config.options(section))
    return K

  def __getitem__(self, item):
    config = self.config
    section = self.section
    if config.has_section(section) and config.has_option(section, item):
      return config.get(section, item)
    if self.defaults is None:
      raise KeyError("%s: no defaults" % (item,))
    return self.defaults[item]

  def get(self, item, default):
    with Pfx("get(%s)", item):
      try:
        value = self[item]
      except KeyError:
        value = default
      else:
        if value is None:
          value = default
      return value

  def __hasitem__(self, item):
    try:
      self[item]
      return True
    except KeyError:
      return False

if __name__ == '__main__':
  import sys
  import cs.configutils_tests
  cs.configutils_tests.selftest(sys.argv)
