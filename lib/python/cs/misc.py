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
