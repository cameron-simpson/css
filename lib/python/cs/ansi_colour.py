#!/usr/bin/python
#
# ANSI terminal sequences.
#       - Cameron Simpson <cs@zip.com.au> 16nov2010
#

DISTINFO = {
    'description': "Convenience functions for ANSI terminal colour sequences.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Development Status :: 6 - Mature",
        "Environment :: Console",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "Topic :: Terminals",
    ],
}

COLOURS = {
    'normal': '\033[0m',
    'reverse': '\033[7m',
    'underline': '\033[4m',
    'bold': '\033[1m',
    'black': '\033[30m',
    'red': '\033[31m',
    'green': '\033[32m',
    'yellow': '\033[33m',
    'blue': '\033[34m',
    'magenta': '\033[35m',
    'cyan': '\033[36m',
    'white': '\033[37m',
}

def colourise(s, colour, uncolour='normal'):
  ''' Return a string enclosed in colour-on and colour-off ANSI sequences.
      `colour` names the desired ANSI colour.
      `uncolour` may be used to specify the colour-off colour;
      the default is 'normal'.
  '''
  return COLOURS[colour] + s + COLOURS[uncolour]
