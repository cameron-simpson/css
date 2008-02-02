#!/usr/bin/python -tt
#
# Basis for manipulating web sites through their URLs.
# See cs/flickr.py and cs/librarything.py for examples.
#       - Cameron Simpson <cs@zip.com.au> 18dec2007
#

from cs.misc import debug
import cs.www
import urllib

class SiteHack(cs.www.URL):
  def __init__(self,url,sitePrefix):
    cs.www.URL.__init__(self,url)
    if type(siteprefix) is str:
      sitePrefix=(siteprefix,)
    self.url=url
    self.type=None
    ok=False
    for pfx in sitePrefix:
      if url.startswith(pfx):
        ok=True
        tail=url[len(pfx):]
        break
    assert ok, "bad URL: %s, should start with %s" % (url, sitePrefix)
    self.words=[word for word in tail.split('/') if len(word) > 0]

  def imageURL(self,imsizes=None):
    if self.type == 'PHOTO':
      if imsizes is None:
        imsizes='olblmst'
      for imsize in imsizes:
        imurl="http://www.flickr.com/photo_zoom.gne?id=%s&size=%s" % (self.photoid, imsize)
        debug("trying imsize=%s, imurl=%s" % (imsize,imurl))
        U=urllib.urlopen(imurl)
        goturl=U.geturl()
        if goturl.endswith("&size=%s" % imsize):
          for link in self.links(U=U):
            debug("link=%s" % link)
            if link.endswith("_d.jpg"):
              return link
        debug("reject imsize=%s, goturl=%s" % (imsize, goturl))
      for imurl in self.images():
        if imurl.endswith(".jpg?v=0"):
          return imurl
      assert False, \
             "can't find image URL for %s" % self.url
    else:
      assert False, \
             "unsupported type %s for URL %s" % (self.type, self.url)

  def feed(self):
    if type == 'FAVORITES':
      return "http://api.flickr.com/services/feeds/photos_faves.gne?id=%s&lang=en-us&format=rss_200" % self.userid
    elif type == 'HOME':
      return "http://api.flickr.com/services/feeds/photos_public.gne?id=%s&lang=en-us&format=rss_200" % self.userid
    else:
      assert False, \
             "unsupported type %s for URL %s" % (self.type, self.url)
