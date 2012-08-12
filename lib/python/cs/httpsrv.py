from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
import cs.hier
import json
import base64
import re
import sys
try:
  from urllib.parse import unquote
except ImportError:
  from urllib import unquote

class JSONRPC(HTTPServer):
  def __init__(self,bindaddr,base=None):
    ''' Start a server bound to the supplied bindaddr, a tuple of (ip-addr, port).
        base is the DocumentRoot for requests and defaults to "/".
    '''
    if base is None: base='/'
    self.rpcBaseURL=base

    HTTPServer.__init__(self,bindaddr,JSONRPCHandler)

basic_authorization_re=re.compile(r'^\s*basic\s+(\S+)',re.I)

def testAuthPWNam(user, password):
  import pwd
  try:
    pw=pwd.getpwnam(user)
  except KeyError:
    return False

  import crypt
  pwcrypt = pw[1]
  salt = pwcrypt[:2]
  return crypt.crypt(password, salt) == pwcrypt

class RequestHandler(BaseHTTPRequestHandler):
  def __init__(self,rq,cliaddr,srv):
    BaseHTTPRequestHandler.__init__(self,rq,cliaddr,srv)

  def getAuth(self, testAuth=None):
    if testAuth is None:
      global testAuthPWNam
      testAuth=testAuthPWNam

    authhdr=self.headers.get('AUTHORIZATION')
    if authhdr is None:
      return None

    global basic_authorization_re
    m=basic_authorization_re.match(authhdr)
    if not m:
      return None

    try:
      cred=base64.b64decode(m.group(1))
    except TypeError:
      return None
    userid, password = cred.split(":",1)

    if not testAuth(userid, password):
      return None

    return userid

  def needAuth(self,realm):
    self.wfile.write("HTTP/1.0 401 Auth needed.\r\nContent-Type: text/plain\r\nWWW-Authenticate: Basic realm=\"%s\"\r\nConnection: close\r\n\r\nTesting.\r\n" % realm)

class JSONRPCHandler(RequestHandler):
  def __init__(self,rq,cliaddr,srv):
    RequestHandler.__init__(self,rq,cliaddr,srv)

  def reject(self,code,complaint):
    self.wfile.write("%03d %s\r\nContent-Type: text/plain\r\n\r\n%03d %s\r\n"
                     % (code,complaint,code,complaint))

  def do_GET(self):
    path=self.path
    root=self.server.rpcBaseURL
    if not path.startswith(root):
      self.reject(500, "path not inside root prefix: %s" % root)
      return
    path=path[len(root):]

    slndx=path.find('/')
    if slndx < 1 or not path[:slndx].isdigit():
      self.reject(500, "missing sequence number")
      return

    seq=int(path[:slndx])
    self.headers['Content-Type']="application/x-javascript\r\n"
    jsontxt=path[slndx+1:]
    jsontxt=unquote(jsontxt)
    (args,unparsed)=cs.hier.tok(jsontxt)
    rpcres=self.server.rpc(self,args)
    if rpcres is None:
      return
    cb, result = rpcres
    jscode="%s(%d,%s);\r\n" % (cb,seq,dumps(result,4));
    self.wfile.write(jscode);
