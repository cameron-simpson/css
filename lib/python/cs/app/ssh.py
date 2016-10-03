#!/usr/bin/python
#
# Parse command line options and ssh config files.
#   - Cameron Simpson <cs@zip.com.au> 01oct2016
#

from __future__ import print_function
import sys
from fnmatch import fnmatch
from cs.lex import get_identifier
from cs.logutils import Pfx, warning, error

DEFAULT_CONFIGS = ('$HOME/.ssh/config', '/etc/ssh/ssh_config')

def parse_option(opttext):
  ''' Parse an option string into an option name and value.
  '''
  option, offset = get_identifier(opttext)
  if len(option) == 0:
    raise ValueError('missing option name')
  option = option.lower()
  opttext = opttext[offset:]
  if not opttext:
    raise ValueError("missing option value")
  if opttext.startswith('='):
    opttext = aptarg[1:]
  elif opttext[0].isspace():
    opttext = opttext.strip()
  else:
    raise ValueError("invalid text after option: %r", opttext)
  return option, opttext

def update_from_file(options, config, host):
  ''' Read options from an ssh_config file and update `options`; return true on successful parse.
      `options`: a mapping of existing option values keyed on option.lower().
      `config`: configuration file to read.
      `host`: host used to select Host clauses to honour.
  '''
  host_lc = host.lower()
  ok = True
  with Pfx(config):
    with open(config) as fp:
      use_host = False
      for lineno, line in enumerate(fp, 1):
        with Pfx(lineno):
          if not line.endswith('\n'):
            warning("missing newline")
          line = line.strip()
          if not line or line.startswith('#'):
            continue
          words = line.split(None, 1)
          if words[0].lower() == 'host':
            use_host = False
            if len(words) == 1:
              warning("no host patterns")
            else:
              for hostptn in words[1].split():
                if fnmatch(host_lc, hostptn.lower()):
                  use_host = True
                  break
          elif use_host:
            try:
              option, optvalue = parse_option(line)
            except ValueError as e:
              error("invalid option: %s", e)
              ok = False
            else:
              if option not in options:
                options[option] = optvalue
  return ok
