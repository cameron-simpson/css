#!/usr/bin/python
#
# Understanding of the Sydney Morning Herald website.
#       - Cameron Simpson <cs@zip.com.au> 27dec2013
#

DOMAIN = 'www.smh.com.au'

def grok_story( (P, U) ):
  return {
      'story_title': U.title,
    }
