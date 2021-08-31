#!/usr/bin/env python3

from collections import defaultdict
import os
from os.path import (
    basename,
    exists as existspath,
    isdir as isdirpath,
    join as joinpath,
    samefile,
)
from tempfile import NamedTemporaryFile

from cs.fstags import FSTags
from cs.logutils import info, warning
from cs.pfx import Pfx, pfx, pfx_call
from cs.tagset import Tag

from cs.x import X

class Tagger:
  ''' The core logic of a tagger.
  '''

  def __init__(self, fstags=None):
    ''' Initialise the `Tagger`.

        Parameters:
        * `fstags`: optional `FSTags` instance
    '''
    if fstags is None:
      fstags = FSTags()
    self.fstags = fstags
    # mapping of bare tags to [dirpath]
    self.tag_filepaths = defaultdict(list)
    self.tag_filepaths[Tag('vendor',
                           'crt')] = ['/Users/cameron/hg/css-tagger/for_crt']

  def auto_name(self, tags):
    ''' Generate a filename computed from `tags`.
    '''
    name = basename(tags.srcpath)
    X("autoname(%s) => %r", tags, name)
    return name

  @pfx
  def file_by_tags(self, path, prune_inherited=False):
    ''' Examine a file's tags.
        Where those tags imply a location, link the file to that location.
        Return the list of links made.

        Note: if `path` is already linked to an implied location
        that location is also included in the returned list.
    '''
    linked_to = []
    tagged = self.fstags[path]
    srcpath = tagged.filepath
    for tag in tagged.all_tags:
      with Pfx(tag):
        bare_tag = Tag(tag.name, tag.value)
        for target_dir in self.tag_filepaths.get(bare_tag, ()):
          with Pfx("=> %r", target_dir):
            if not isdirpath(target_dir):
              warning("not a directory")
              continue
            with NamedTemporaryFile(dir=target_dir) as T:
              tmppath = T.name
              tmp = self.fstags[tmppath]
              tmp.update(tagged.all_tags)
              if prune_inherited:
                tmp.prune_inherited()
              tmp.add('srcpath', srcpath)
              link_as = self.auto_name(tmp)
              assert link_as == basename(link_as)
              linkpath = joinpath(target_dir, link_as)
              if existspath(linkpath):
                if samefile(srcpath, linkpath):
                  info("source %r already linked to %r", srcpath, linkpath)
                  linked_to.append(linkpath)
                else:
                  warning("link path %r already exists", linkpath)
              else:
                try:
                  pfx_call(os.link, srcpath, linkpath)
                except OSError as e:
                  error("link fails: %s", e)
                else:
                  self.fstags[linkpath].update(tmp)
                  linked_to.append(linkpath)
    return linked_to
