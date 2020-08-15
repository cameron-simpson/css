#!/usr/bin'python
#
# Functions for working with the Internet Protocol (IP).
#   - Cameron Simpson <cs@cskk.id.au> 27jan2017
#

from collections import namedtuple, OrderedDict
from cs.logutils import error, warning
from cs.pfx import Pfx
from cs.x import X

ETC_SERVICES = '/etc/services'

_PortInfo = namedtuple('PortInfo', 'portnum proto name aliases comment')
class PortInfo(_PortInfo):
  def line(self):
    line = "%-15s %d/%s" % (self.name if self.name else '',
                            self.portnum,
                            self.proto)
    if self.aliases:
      X("line=%s, aliases=%s", line, self.aliases)
      line += '    ' + ' '.join(self.aliases)
    if self.comment:
      line += '  # ' + self.comment
    return line

def read_services(fp, start_lineno=1):
  ''' Parse the services(5) format, yield (prelines, PortInfo(portnum, proto, name, aliases)).
  '''
  textlines = []
  for lineno, line in enumerate(fp, start_lineno):
    with Pfx("%s:%d", fp, lineno):
      if not line.endswith('\n'):
        raise ValueError("missing terminating newline")
      line0 = line[:-1]
      line = line0.rstrip()
      comment_pos = line.find('#')
      if comment_pos >= 0:
        line = line[:comment_pos].rstrip()
        comment = line[comment_pos+1:].strip()
      else:
        comment = ''
      words = line.split()
      if not words:
        textlines.append(line0)
        continue
      if line[0].isspace():
        name = None
      else:
        name = words.pop(0)
      with Pfx(name):
        try:
          portspec = words.pop(0)
        except IndexError:
          raise ValueError("missing portnum/proto")
        with Pfx(portspec):
          try:
            portnum, proto = portspec.split('/')
            portnum = int(portnum)
            proto = proto.lower()
          except ValueError as e:
            raise ValueError("invalid portspec: %s" % (e,))
          yield textlines, PortInfo(portnum, proto, name, words, comment)
          textlines = []
  if textlines:
    yield textlines, None

def merge_services(fp, portmap=None, namemap=None):
  if portmap is None:
    portmap = OrderedDict() # mapping from (portnum, proto) to PortInfo
  if namemap is None:
    namemap = {}            # mapping from name or alias to PortInfo
  for prelines, PI in read_services(fp):
    ##X("PI=%s, %d prelines", PI, len(prelines))
    prelines = list(prelines)
    ##for line in prelines:
    ##  print(line)
    if PI is None:
      continue
    ##print(PI.line())
    key = PI.portnum, PI.name
    if key not in portmap:
      portmap[key] = (PI, prelines)
    else:
      PI0, prelines = portmap[key]
      if PI.name is not None:
        PI0.aliases.append(PI.name)
      PI0.aliases.extend(PI.aliases)
      if PI.comment:
        PI0.comments.append("; "+PI.comment)
      prelines.extend(prelines)
    names = []
    if PI.name is not None:
      names.append(PI.name)
    names.extend(PI.aliases)
    for name in set(names):
      key = name + '/' + PI.proto
      if key not in namemap:
        namemap[key] = PI
  return portmap, namemap
