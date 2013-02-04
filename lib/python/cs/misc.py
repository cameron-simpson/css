from __future__ import print_function
import os
import os.path
import errno
import sys
import logging
info = logging.info
warning = logging.warning
import string
import time
if sys.hexversion < 0x02060000: from sets import Set as set
from cs.lex import parseline, strlist

def unimplemented(func):
  ''' Decorator for stub methods that must be implemented by a stub class.
  '''
  def wrapper(self, *a, **kw):
    raise NotImplementedError("%s.%s(*%s, **%s)" % (type(self), func.__name__, a, kw))
  return wrapper

class slist(list):
  ''' A list with a shorter str().
  '''

  def __str__(self):
    return "[" + ",".join(str(e) for e in self) + "]"
