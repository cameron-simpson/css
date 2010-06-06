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
from cs.logutils import Pfx, info

class ConfigWatcher(object):
  ''' A monitor for a windows style .ini file.
      The current SafeConfigParser object is present in the .parser attribute.
  '''
  def __init__(self, cfgpath):
    self.cfgpath = cfgpath
    self.mtime = None
    self.parser = configparser.SafeConfigParser()
    self.poll()

  def poll(self):
    ''' Poll the config file for changes.
        If the file is present and newer and doesn't change after the read
        then return a fresh SafeConfigParser loaded from the file.
        Otherwise return None, indicating no new changes available.
        If there are new changes, the new parser is saved as the .parser
        attribute.
    '''
    cfgpath = self.cfgpath
    result = None
    ##with NoExceptions(None):
    if os.path.isfile(cfgpath):
      s = os.stat(cfgpath)
      if self.mtime is None or s.st_mtime > self.mtime:
        CP = configparser.SafeConfigParser()
        with open(cfgpath) as fp:
          CP.readfp(fp)
        s2 = os.stat(cfgpath)
        if s2.st_mtime == s.st_mtime and s2.st_size == s.st_size:
           self.mtime = s.st_mtime
           info("new config loaded from %s" % (cfgpath,))
           result = self.parser = CP
    return result

class ConfigSectionWatcher(object):
  ''' A class for monitoring a particular clause in a config file
      and self updating if needed when poll() is called.
  '''
  def __init__(self, cfgpath, section, defaults=None):
    ''' Initialise a ConfigSectionWatcher to monitor a particular section
        of a config file.
    '''
    if not os.path.isabs(cfgpath):
      cfgpath = os.path.abspath(cfgpath)
    self.cfgpath = cfgpath
    self.section = section
    self.defaults = defaults
    self.configwatcher = ConfigWatcher(cfgpath)

  def poll(self):
    self.configwatcher.poll()

  def __str__(self):
    return "%s[%s]%s" % (self.cfgpath, self.section, repr(self))

  def __repr__(self):
    d = {}
    for k in self.keys():
      d[k] = self[k]
    return repr(d)

  def keys(self):
    CP = self.configwatcher.parser
    section = self.section
    K = set()
    if self.defaults:
      K.update(self.defaults.keys())
    if CP.has_section(section):
      K.update(CP.options(section))
    return list(K)
    
  def __getitem__(self, item):
    CP = self.configwatcher.parser
    section = self.section
    if CP.has_section(section) and CP.has_option(section, item):
      return CP.get(section, item)
    if self.defaults is None:
      raise IndexError, "__getitem__(%s): no defaults" % (item,)
    return self.defaults[item]

  def get(self, item, default):
    with Pfx("get(%s)" % item):
      try:
        value = self[item]
      except IndexError:
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
