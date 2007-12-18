#!/usr/bin/python -tt
#
# Flickr convenience classes.
#       - Cameron Simpson <cs@zip.com.au> 18dec2007
#

import cs.www
import urllib

flickrPrefix='http://www.flickr.com/'

class FlickrURL(cs.www.URL):
  def __init__(self,url):
    cs.www.URL.__init__(self,url)
    self.url=url
    self.type=None
    assert url.startswith(flickrPrefix), \
           "non-flickr URL: %s" % url
    tail=url[len(flickrPrefix):]
    words=[word for word in tail.split('/') if len(word) > 0]
    assert len(words) > 0, \
           "short flickr URL: %s" % url
    if words[0] == 'photos':
      self.userid=words[1]
      print "userid=%s" % self.userid
      if len(words) == 2:
        self.type='HOME'
      elif words[2] == 'favorites':
        self.type='FAVORITES'
      else:
        self.type='PHOTO'
        self.photoid=words[2]
        print "photoid=%s" % self.photoid
    else:
      assert False, \
             "unsupported flickr URL: %s" % url

  def imageURL(self,imsizes=None):
    if self.type == 'PHOTO':
      if imsizes is None:
        imsizes='olblmst'
      for imsize in imsizes:
        imurl="http://www.flickr.com/photo_zoom.gne?id=%s&size=%s" % (self.photoid, imsize)
        print "trying imsize=%s, imurl=%s" % (imsize,imurl)
        U=urllib.urlopen(imurl)
        goturl=U.geturl()
        if goturl.endswith("&size=%s" % imsize):
          for link in self.links(U=U):
            print "link=%s" % link
            if link.endswith("_d.jpg"):
              return link
        print "reject imsize=%s, goturl=%s" % (imsize, goturl)
      assert False, \
             "can't find image URL for %s" % self.url
      return None
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
