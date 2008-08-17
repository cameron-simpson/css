from email.utils import parseaddr, getaddresses, formataddr
from cs.misc import cmderr, verbose

def addressKey(addr):
  ''' Return the key value for an RFC2822 address.
  '''
  name, core = parseaddr(addr)
  if len(name) == 0 and len(core) == 0:
    return None
  return core.lower()

def addrsRegexp(addrkeys):
  addrkeys=list(addrkeys)
  addrkeys.sort()
  retext='|'.join(addrkey.replace('.', '\\.').replace('+','\\+')
                  for addrkey in addrkeys)
  return retext

def loadAddresses(addresses,catmap=None,addrmap=None):
  ''' Load an address list file, return maps by category and address key.
      Existing category and address key maps may be supplied.
  '''
  if catmap is None:
    catmap={}
  if addrmap is None:
    addrmap={}

  lineno=0
  for line in open(addresses):
    lineno+=1
    if line[-1] != '\n':
      cmderr("%s, line %d: missing newline (unexpected EOF)"
             % (addresses, lineno))
      xit=1
      break

    line=line.strip()
    if len(line) == 0 or line[0] == '#':
      continue

    cats, addr = line.split(None,1)
    if addr.startswith('mailto:'):
      addr=addr[7:]
    cats=cats.split(',')
    addrkey=addressKey(addr)
    if addrkey is None:
      verbose("%s, line %d: can't parse address \"%s\""
             % (addresses, lineno, addr))
      xit=1
      continue

    if addrkey in addrmap:
      verbose("%s, line %d: repeated address \"%s\" (%s)"
              % (addresses, lineno, addr, addrkey))
      continue

    assert addrkey.find("@") > 0, "%s, line %d: no \"@\" in \"%s\"" % (addresses, lineno, addrkey)
    cats.sort()
    addrmap[addrkey]=(addr,addrkey,cats)

    for cat in cats:
      catmap.setdefault(cat,{})[addrkey]=addr

  return catmap, addrmap
