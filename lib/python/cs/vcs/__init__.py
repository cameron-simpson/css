#!/usr/bin/env python3

class VCS(object):

  def release_tags(self):
    ''' Generator yielding the current release tags.
    '''
    for tag in self.tags():
      m = re_RELEASE_TAG.match(tag)
      if m:
        yield tag

  def release_prefixes(self):
    ''' Return a set of the existing release prefixes.
    '''
    tagpfxs = set()
    for tag in self.release_tags():
      tagpfx, _ = tag.split('-', 1)
      tagpfxs.add(tagpfx)
    return tagpfxs
