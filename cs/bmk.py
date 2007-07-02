#!/usr/bin/python

import string
import os.path
import cs.treemodel
import rfc822           # basic syntax of a BMK file
import cs.sh

class BMK:
  def __init__(self,pathname):
    self.pathname=pathname
    self.title=None
    self.url=None
  def __probe(self):
    if self.title is None:
      if os.path.isdir(self.pathname):
        self.title=self.pathname
      else:
        msg=rfc822.Message(file(self.pathname))
        self.title=msg.getheader('Subject')
        self.url=msg.getheader('URL')
  def isdir(self):
    self.__probe()
    return self.url is None
  def bmk_title(self):
    self.__probe()
    if self.url is None:
      return os.path.basename(self.title)
    return self.title+" - "+self.url
  def bmk_url(self):
    self.__probe()
    return self.url

class BMKNode(cs.treemodel.PathNameNode):
  def __init__(self,pathname):
    ##print "BMK init", pathname
    self.__bmk=BMK(pathname)
    cs.treemodel.PathNameNode.__init__(self,pathname)
  def rawchildren(self):
    if not self.__bmk.isdir():
      return None
    return cs.treemodel.PathNameNode.rawchildren(self)
  def child(self,k):
    ch=self.rawchild(k)
    if ch is not None:
      ch=BMKNode(os.path.join(self.__bmk.pathname,ch))
    return ch
  def get_column(self,colidx):
    if colidx > 0:
      return None
    return self.__bmk.bmk_title()
  def selected(self,treemodel,treeview,path,coluumn):
    urlshow(self.__bmk.bmk_url())

def urlshow(url):
  env=os.environ
  if 'BROWSER' in env:
    br=os.environ['BROWSER']
  else:
    br='urlshow'

  qurl=cs.sh.quotestr(url)
  subst=string.find(br,'%s')
  if subst < 0:
    br=br+" "+qurl
  else:
    qbr=""
    lh=0
    while subst >= 0:
      qbr+=br[lh:subst]+qurl
      lh=subst+2
      subst=string.find(br,'%s',lh)
    br=qbr

  print "BR="+br
  os.system("set -x; "+br)
