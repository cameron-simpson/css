#!/usr/bin/env python3

from collections import defaultdict
import os
from os.path import (
    abspath,
    basename,
    dirname,
    exists as existspath,
    expanduser,
    isabs as isabspath,
    isdir as isdirpath,
    join as joinpath,
    samefile,
)
from tempfile import NamedTemporaryFile

from cs.fstags import FSTags
from cs.logutils import info, warning, error
from cs.pfx import Pfx, pfx, pfx_call, prefix
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
    self._file_by_mappings = {}

  def auto_name(self, srcpath, dstdirpath, tags):
    ''' Generate a filename computed from `srcpath`, `dstdirpath` and `tags`.
    '''
    name = basename(srcpath)
    X("autoname(%s) => %r", tags, name)
    return name

  @pfx
  def file_by_tags(self, path: str, prune_inherited=False, no_link=False):
    ''' Examine a file's tags.
        Where those tags imply a location, link the file to that location.
        Return the list of links made.

        Parameters:
        * `path`: the source path to file
        * `prune_inherited`: optional, default `False`:
          prune the inherited tags from the direct tags on the target
        * `no_link`: optional, fault `False`;
          do not actually make the hard link, just report the target

        Note: if `path` is already linked to an implied location
        that location is also included in the returned list.
    '''
    fstags = self.fstags
    # start the queue with the resolved `path`
    srcpath0 = fstags[path].filepath
    q = [srcpath0]
    linked_to = []
    filed_from = set()
    while q:
      srcpath = q.pop(0)
      print("file_by_tags:", srcpath)
      with Pfx(srcpath):
        srcdirpath = dirname(srcpath)
        # loop detection
        if srcdirpath in filed_from:
          continue
        filed_from.add(srcdirpath)
        tagged = fstags[srcpath]
        tags = tagged.all_tags
        # places to redirect this file
        refile_to = set()
        mapping = self.file_by_mapping(srcdirpath)
        interesting_tag_names = {tag.name for tag in mapping.keys()}
        for tag_name in sorted(interesting_tag_names):
          with Pfx("tag_name %r", tag_name):
            if tag_name not in tags:
              continue
            bare_tag = Tag(tag_name, tags[tag_name])
            try:
              target_dirs = mapping.get(bare_tag, ())
            except TypeError as e:
              warning("  %s not mapped, skipping", bare_tag)
              continue
            if not target_dirs:
              continue
            # collect other filing locations
            refile_to.update(target_dirs)
        # ... but remove locations we have already considered
        refile_to.difference_update(filed_from)
        # now collate new filing locations
        dstpaths = []
        for dstdirpath in refile_to:
          with Pfx("dst %r", dstdirpath):
            if not isdirpath(dstdirpath):
              warning("not a directory, ignoring")
              continue
            dstbase = self.auto_name(srcpath, dstdirpath, tags)
            with Pfx(dstbase):
              dstpath = joinpath(dstdirpath, dstbase)
              if existspath(dstpath):
                warning("already exists, skipping")
                continue
              dstpaths.append(dstpath)
        if dstpaths:
          # queue further locations, do not file here
          q.extend(dstpaths)
        else:
          # file here
          dstbase = self.auto_name(srcpath, srcdirpath, tags)
          with Pfx(dstbase):
            dstpath = joinpath(srcdirpath, dstbase)
            if existspath(dstpath):
              warning("already exists, skipping")
              continue
            if not no_link:
              pfx_call(os.link, srcpath0, dstpath)
              fstags[dstpath].update(tags)
            linked_to.append(dstpath)
    return linked_to

  @pfx
  def auto_file_map(self, dirpath: str, tag_names):
    ''' Walk the file tree at `dirpath`
        looking for directories whose direct tags contain tags
        whose name is in `tag_names`.
        Return a mapping of `Tag->[dirpaths...]`
        mapping specific tag values to the directory paths where they occur.

        Parameters:
        * `dirpath`: the path to the directory to walk
        * `tag_names`: an iterable of Tag names of interest

        The intent here is to derive filing locations
        from the tree layout.
    '''
    dirpath = abspath(dirpath)
    cache_key = dirpath, tuple(sorted(tag_names))
    try:
      mapping = self._file_by_mappings[cache_key]
    except KeyError:
      mapping = self._file_by_mappings[cache_key] = defaultdict(list)
      fstags = self.fstags
      if mapping is None:
        mapping = defaultdict(list)
      tag_names = set(tag_names)
      assert all(isinstance(tag_name, str) for tag_name in tag_names)
      for path, dirnames, _ in os.walk(dirpath):
        with Pfx(path):
          # orderthe descent
          dirnames[:] = sorted(
              dirname for dirname in dirnames
              if dirname and not dirname.startswith('.')
          )
          tagged = fstags[path]
          if 'tagger.skip' in tagged:
            # prune this directory from the mapping
            dirnames[:] = []
          else:
            # look for the tags of interest
            for tag in tagged:
              if tag.name in tag_names:
                bare_tag = Tag(tag.name, tag.value)
                mapping[bare_tag].append(tagged.filepath)
    return mapping

  @pfx
  def file_by_mapping(self, srcdirpath):
    ''' Examine the `tagger.file_by` tag for `srcdirpath`.
        Return a mapping of specific tag values to filing locations
        derived via `auto_file_map`.
        The file locations may be a list or a string (for convenient single locations.

        For example, I might tag my downloads directory with:

            tagger.file_by={"abn":"~/them/me"}

        indicating that files with an `abn` tag should be filed in the `~/them/me` directory.
        That directory is then walked looking for the tag `abn`,
        and wherever some tag `abn=`*value*` is found on a subdirectory
        a mapping entry for `abn=`*value*=>*subdirectory* is added.

        This results in a direct mapping of specific tag values to filing locations,
        such as:

            { Tag('abn','***********') => ['/path/to/them/me/abn-**-***-***-***'] }

        because the target subdirectory has been tagged with `abn="***********"`.
    '''
    fstags = self.fstags
    mapping = defaultdict(set)
    file_by = fstags[srcdirpath].get('tagger.file_by') or {}
    # group the tags by file_by target path
    grouped = defaultdict(set)
    for tag_name, file_to in file_by.items():
      if isinstance(file_to, str):
        file_to = file_to,
      for file_to_path in file_to:
        if not isabspath(file_to_path):
          if file_to_path.startswith('~'):
            file_to_path = expanduser(file_to_path)
          else:
            file_to_path = joinpath(srcdirpath, file_to_path)
        file_to_path = abspath(file_to_path)
        grouped[file_to_path].add(tag_name)
    # walk each path for its tag_names of interest
    for file_to_path, tag_names in sorted(grouped.items()):
      with Pfx("%r:%r", file_to_path, tag_names):
        # accrue destination paths by tag values
        for bare_key, dstpaths in self.auto_file_map(file_to_path,
                                                     tag_names).items():
          mapping[bare_key].update(dstpaths)
    return mapping
