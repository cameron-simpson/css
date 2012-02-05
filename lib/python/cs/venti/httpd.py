#!/usr/bin/python
#
# Serve store contents via HTTP.
#       - Cameron Simpson <cs@zip.com.au> 23jun2010
#

import cherrypy
from cs.html import tok2s

class DirView(object):
  ''' An object exposing a Dir as an HTML tree.
  '''

  def __init__(self, D, path=None):
    self.D = D
    self.path = path

  @cherrypy.expose
  def index(self):
    html = []

    if self.path:
      html.append(['H1', self.path])

    namelist = []
    for name in sorted(self.D.keys()):
      E = self.D[name]
      if E.isdir:
        namelist.append([ 'LI', [ 'A', { 'HREF': name+'/', }, name+'/' ] ])
      else:
        namelist.append([ 'LI', [ 'A', { 'HREF': name, }, name ] ])
    html.append(['UL'] + namelist)

    return tok2s(*html)

if __name__ == '__main__':
  cherrypy.quickstart(DirView())
