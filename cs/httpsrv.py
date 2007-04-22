from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
import cs.hier
import cs.json
import cs.www
from cs.misc import cmderr

class JSONRPC(HTTPServer):
  def __init__(self):
    HTTPServer.__init__(self,('127.0.0.1',8089),JSONRPCHandler)

class JSONRPCHandler(BaseHTTPRequestHandler):
  def __init__(self,rq,cliaddr,srv):
    BaseHTTPRequestHandler.__init__(self,rq,cliaddr,srv)

  def reject(self,code,complaint):
    self.wfile.write("%03d %s\r\nContent-Type: text/plain\r\n\r\n%03d %s\r\n"
                   % (code,complaint,code,complaint))

  def do_GET(self):
    qndx=self.path.find('?')
    if qndx < 0:
      self.reject(500,"No QUERY_STRING part.")
      return
      
    self.wfile.write("200 OK\r\nContent-Type: text/plain\r\n\r\n")
    jsontxt=self.path[qndx+1:]
    jsontxt=cs.www.unhexify(jsontxt)
    (args,unparsed)=cs.hier.tok(jsontxt)
    (cb,result)=self.server.rpc(args)
    self.wfile.write(cb)
    self.wfile.write("(")
    self.wfile.write(cs.json.json(result))
    self.wfile.write(");\r\n")
