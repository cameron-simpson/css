#!/usr/bin/python -tt
#
# Flickr convenience classes.
#       - Cameron Simpson <cs@zip.com.au> 18dec2007
#

import urllib
from cs.sitehack import SiteHack
from cs.logutils import debug

flickrPrefix=('http://www.flickr.com/', 'http://flickr.com/')

class FlickrURL(SiteHack):
  def __init__(self,url):
    SiteHack.__init__(self,url,flickrPrefix)
    words=self.words
    assert len(words) > 0, \
           "short URL: %s" % url
    if words[0] == 'photos':
      self.userid=words[1]
      debug("userid=%s" % self.userid)
      if len(words) == 2:
        self.type='HOME'
      elif words[2] == 'favorites':
        self.type='FAVORITES'
      else:
        self.type='PHOTO'
        self.photoid=words[2]
        debug("photoid=%s" % self.photoid)
    else:
      assert False, \
             "unsupported flickr URL: %s" % url

  def imageURL(self,imsizes=None):
    if self.type == 'PHOTO':
      if imsizes is None:
        imsizes='olblmst'
      for imsize in imsizes:
        imurl="http://www.flickr.com/photos/%s/%s/sizes/%s/" % (self.userid, self.photoid, imsize)
        debug("trying imsize=%s, imurl=%s" % (imsize,imurl))
        U=urllib.urlopen(imurl)
        goturl=U.geturl()
        if goturl.endswith("/sizes/%s/" % imsize):
          for link in self.links(U=U,attr="src"):
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
