#!/usr/bin/python
#
# Parse ssh command line options and config files.
#   - Cameron Simpson <cs@cskk.id.au> 01oct2016
#

from __future__ import print_function

DISTINFO = {
    'description': "OpenSSH configuration parsing.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': ['cs.env', 'cs.lex', 'cs.logutils'],
    'entry_points': {
      'console_scripts': [
          'ssh-opts = cs.app.ssh:main_ssh_opts',
          ],
    },
}

import sys
from fnmatch import fnmatch
from cs.env import envsub
from cs.lex import get_identifier
from cs.logutils import setup_logging, info, warning, error
from cs.pfx import XP
from cs.pfx import Pfx

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

def main_ssh_opts(argv):
  USAGE = r'''Usage: %s [-F config-file]... [-o opt=value]... host [options...]
  -F config-file    Specific configuration file to read. These accumulate.
                    If no configuration files are specified use:
                        ''' + " ".join(DEFAULT_CONFIGS) + r'''
                    Configuration files are consulted in order and
                    the earlier matching setting of each option is
                    used.
  -o opt=value      Specify an ssh configuration option. Later -o
                    options override earlier ones. Options specified
                    by -o override options from configuration files.
  host              Host name used to match clauses in configuration files.
  options           If specified, print the value of each option
                    in order, each on its own line.
                    If not options are specified, print the value
                    of each option defined by -o or in a configuration
                    file as:
                        option-name option-value'''
  cmd = argv.pop(0)
  setup_logging(cmd)
  usage = USAGE % (cmd,)
  configs = []
  options = {}
  badopts = False
  while argv:
    opt = argv.pop(0)
    if opt == '--':
      break
    if not opt.startswith('-') or len(opt) < 2:
      argv.insert(0, opt)
      break
    with Pfx(opt):
      if opt == '-F':
        configs.append(argv.pop(0))
      elif opt == '-o':
        optarg = argv.pop(0)
        with Pfx(optarg):
          try:
            option, optvalue = parse_option(optarg)
          except ValueError as e:
            warning("invalid option: %s", e)
            badopts = True
            continue
          info("cmdline: %s = %s", option, optvalue)
          options[option] = optvalue
      else:
        warning("unrecognised option")
        badopts = True
        continue
  if not argv:
    warning("missing host")
    badopts = True
  else:
    host = argv.pop(0)
  if not configs:
    configs = [ envsub(cfg) for cfg in DEFAULT_CONFIGS ]
  if badopts:
    print(usage, file=sys.stderr)
    return 2
  xit = 0
  for config in configs:
    if not update_from_file(options, config, host):
      xit = 1
  if argv:
    for option in argv:
      print(option, options.get(option.lower(), ''))
  else:
    for option in sorted(options.keys()):
      print(option, options[option])
  return xit

if __name__ == '__main__':
  sys.exit(main_ssh_opts(sys.argv))
