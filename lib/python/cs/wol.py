#!/usr/bin/python
#
# Send wake on LAN packets.
# - Cameron Simpson <cs@cskk.id.au>
#

r'''
wol: yet another tool to send a Wake-On-LAN ethernet packet

Every WOL tool I have seen takes IP addresses and (a) infers the
outbound NIC from the routing table (directly or naively) and (b)
does not provide control for specifying the outbound NIC. I needed
a tool to wake devices on a specific NIC, which do not have IP
addresses, or do not have known IP addresses.
'''

from __future__ import print_function
from binascii import unhexlify
import socket
import sys

DISTINFO = {
    'description': "Tool for sending a wake on LAN (WOL) packet out a specific interface to a specific MAC address.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
    'entry_points': {
        'console_scripts': [
            'wol = cs.wol:main',
        ],
    },
}

USAGE = 'Usage: %s -i nic mac...'

def main(argv=None):
  if argv is None:
    argv = sys.argv
  cmd = argv.pop(0)
  usage = USAGE % (cmd,)
  badopts = False
  nic = None
  if argv and argv[0] == '-i':
    argv.pop(0)
    nic = argv.pop(0)
  if nic is None:
    print("%s: missing -i nic" % (cmd,), file=sys.stderr)
    badopts = True
  if not argv:
    print("%s: missing macs" % (cmd,), file=sys.stderr)
    badopts = True
  else:
    macs = []
    for mac in argv:
      try:
        macbytes = mac2bytes(mac)
      except ValueError:
        print("%s: invalid MAC address: %r" % (cmd, mac))
        badopts = True
      else:
        macs.append((mac, macbytes))
  if badopts:
    print(usage, file=sys.stderr)
    return 2
  s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
  s.bind((nic, 0))
  for mac, macbytes in macs:
    payload = unhexlify('ffffffffffff') + 16 * macbytes
    s.send(payload)
  return 0

def mac2bytes(mac):
  ''' Convert a textual MAC address into bytes.
  '''
  hexparts = mac.split(':')
  if len(hexparts) != 6:
    raise ValueError("expected 6 fields, found: %r" % (hexparts,))
  hexparts = [ ( '0' + hx if len(hx) == 1 else hx ) for hx in hexparts ]
  macbytes = b''.join([ unhexlify(hx) for hx in hexparts ])
  return macbytes

if __name__ == '__main__':
  sys.exit(main(sys.argv))
