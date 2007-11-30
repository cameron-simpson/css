import os
import socket

def portnum(port,proto='tcp'):
  if isinstance(port,int): return port
  if port.isdigit(): return int(port)
  return socket.getservbyname(port,proto)

def hostaddrs(host):
  ainfo=socket.getaddrinfo(host,0)
  return [gai[4][0] for gai in ainfo]

_cache_tcp_listens=None
def tcp_listening(localport,localaddr=None,nocache=False):
  global _cache_tcp_listens
  localport=portnum(localport)
  if localaddr is not None:
    localaddr=hostaddrs(localaddr)[0]

  if nocache or _cache_tcp_listens is None:
    netstat=os.popen("netstat -nl")
    _cache_tcp_listens={}
    from cs.misc import chomp
    for line in netstat:
      line=chomp(line)
      fields=line.split()
      if len(fields) == 6 and fields[0] == "tcp" and fields[5] == "LISTEN":
        (laddr,lport)=fields[3].split(':',1)
        _cache_tcp_listens[laddr,lport]=1

  return (localport,localaddr) in _cache_tcp_listens
