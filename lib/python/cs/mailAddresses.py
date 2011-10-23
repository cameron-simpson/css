import sys
if sys.hexversion < 0x02060000: from sets import Set as set
from collections import namedtuple
from email.utils import parseaddr, getaddresses, formataddr
from cs.logutils import Pfx, error, warning, info

AddressInfo = namedtuple('AddressInfo', 'key address categories')

def addressKey(addr):
  ''' Return the key value for an RFC2822 address.
  '''
  name, core = parseaddr(addr)
  if len(name) == 0 and len(core) == 0:
    return None
  return core.lower()

def addrsRegexp(addrkeys):
  ''' Return the text of a regular expression to match the supplied
      address keys `addrkeys`.
  '''
  addrkeys = list(addrkeys)
  addrkeys.sort()
  retext = '|'.join( addrkey.replace('.', '\\.').replace('+','\\+')
                     for addrkey in addrkeys )
  return retext

def loadAddresses(addresses, catmap=None, addrmap=None):
  ''' Load an address list file.
      Return return ok (True/False) and maps by category and address key.
      Existing category and address key maps may be supplied.
  '''
  if catmap is None:
    catmap={}
  if addrmap is None:
    addrmap={}
  ok = True
  with Pfx(addresses):
    lineno=0
    with open(addresses) as addrfp:
      for line in addrfp:
        lineno+=1
        if not line.endswith('\n'):
          error("line %d: missing newline (unexpected EOF)", lineno)
          ok = False
          break

        line=line.strip()
        if len(line) == 0 or line[0] == '#':
          continue

        try:
          cats, addr = line.split(None,1)
        except ValueError:
          warning("line %d: bad syntax: %s", lineno, line)
          ok = False
          continue

        if addr.startswith('mailto:'):
          addr=addr[7:]
        cats=cats.split(',')
        addrkey=addressKey(addr)
        if addrkey is None:
          warning("line %d: can't parse address \"%s\"", lineno, addr)
          ok = False
          continue

        if "@" not in addrkey:
          warning("line %d: no \"@\" in \"%s\"", lineno, addrkey)

        if addrkey in addrmap:
          info = addrmap[addrkey]
        else:
          info = addrmap[addrkey] = AddressInfo(addrkey, addr, set())

        info.categories.update(cats)
        for cat in cats:
          catmap.setdefault(cat,{})[addrkey]=info

  return ok, catmap, addrmap
