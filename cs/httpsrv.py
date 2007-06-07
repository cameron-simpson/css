from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
import cs.hier
import cs.json
import cs.www
from cs.misc import cmderr

class JSONRPC(HTTPServer):
  def __init__(self,bindaddr=None,base=None):
    if bindaddr is None: bindaddr=('127.0.0.1',8089)
    if base is None: base='/'
    self.rpcBaseURL=base

    HTTPServer.__init__(self,bindaddr,JSONRPCHandler)

class JSONRPCHandler(BaseHTTPRequestHandler):
  def __init__(self,rq,cliaddr,srv):
    BaseHTTPRequestHandler.__init__(self,rq,cliaddr,srv)

  def reject(self,code,complaint):
    self.wfile.write("%03d %s\r\nContent-Type: text/plain\r\n\r\n%03d %s\r\n"
                   % (code,complaint,code,complaint))

  def do_GET(self):
    cmderr("path =", `self.path`)
    path=self.path
    root=self.server.rpcBaseURL
    if path[:len(root)] != root:
      self.reject(500,"path not inside root prefix: "+root)
      return

    path=path[len(root):]
    slndx=path.find('/')
    if slndx < 1 or not path[:slndx].isdigit():
      self.reject(500,"missing sequence")
      return

    seq=int(path[:slndx])
    self.headers['Content-Type']="application/x-javascript\r\n"
    jsontxt=path[slndx+1:]
    cmderr("jsontxt0 =", `jsontxt`)
    jsontxt=cs.www.unhexify(jsontxt)
    cmderr("jsontxt1 =", `jsontxt`)
    (args,unparsed)=cs.hier.tok(jsontxt)
    cmderr("args =", `args`, "unparsed =", `unparsed`)
    (cb,result)=self.server.rpc(args)
    self.wfile.write("%s(%d,%s);\r\n" % (cb,seq,cs.json.json(result)))
