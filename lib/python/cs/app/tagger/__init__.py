#!/usr/bin/env python3

''' Tagger class and `tagger` command line tool for filing files by tags.
'''

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
    realpath,
    samefile,
)

from cs.deco import fmtdoc
from cs.fstags import FSTags
from cs.lex import FormatAsError
from cs.logutils import warning
from cs.pfx import Pfx, pfx, pfx_call
from cs.queues import ListQueue
from cs.seq import unrepeated
from cs.tagset import Tag, TagSet, RegexpTagRule

from typeguard import typechecked

# the subtags containing Tagger releated values
TAGGER_TAG_PREFIX_DEFAULT = 'tagger'

class Tagger:
  ''' The core logic of a tagger.
  '''

  TAG_PREFIX = TAGGER_TAG_PREFIX_DEFAULT

  def __init__(self, fstags=None):
    ''' Initialise the `Tagger`.

        Parameters:
        * `fstags`: optional `FSTags` instance
    '''
    if fstags is None:
      fstags = FSTags()
    self.fstags = fstags
    self._file_by_mappings = {}

  @classmethod
  @typechecked
  def conf_tags(cls, tags: TagSet):
    ''' The `Tagger` related subtags from `tags`, a `TagSet`.
    '''
    return tags.subtags(cls.TAG_PREFIX)

  @classmethod
  @typechecked
  def conf_tag(cls, tags: TagSet, conf: str, default=None):
    ''' Return the `Tagger` related subtag value for `conf`, or `default` (default `None).
    '''
    return cls.conf_tags(tags).get(conf, default)

  @pfx
  def auto_name(self, srcpath, dstdirpath, tags):
    ''' Generate a filename computed from `srcpath`, `dstdirpath` and `tags`.
    '''
    tagged = self.fstags[dstdirpath]
    formats = self.conf_tag(tagged.merged_tags(), 'auto_name', ())
    if isinstance(formats, str):
      formats = [formats]
    if formats:
      if not isinstance(tags, TagSet):
        tags = TagSet()
        for tag in tags:
          tags.add(tag)
      for fmt in formats:
        with Pfx(repr(fmt)):
          try:
            formatted = pfx_call(tags.format_as, fmt, strict=True)
            if formatted.endswith('/'):
              formatted += basename(srcpath)
            return formatted
          except FormatAsError:
            ##warning("%s", e)
            ##print("auto_name(%r): %r: %s", srcpath, fmt, e)
            continue
    return basename(srcpath)

  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
  @pfx
  @fmtdoc
  def file_by_tags(
      self, path: str, prune_inherited=False, no_link=False, do_remove=False
  ):
    ''' Examine a file's tags.
        Where those tags imply a location, link the file to that location.
        Return the list of links made.

        Parameters:
        * `path`: the source path to file
        * `prune_inherited`: optional, default `False`:
          prune the inherited tags from the direct tags on the target
        * `no_link`: optional, default `False`;
          do not actually make the hard link, just report the target
        * `do_remove`: optional, default `False`;
          remove source files if successfully linked

        Note: if `path` is already linked to an implied location
        that location is also included in the returned list.

        The filing process is as follows:
        - for each target directory, initially `dirname(path)`,
          look for a filing map on tag `file_by_mapping`
        - for each directory in that mapping which matches a tag from `path`,
          queue it as an additional target directory
        - if there were no matching directories, file `path` at the current
          target directory under the filename
          returned by `{TAGGER_TAG_PREFIX_DEFAULT}.auto_name`
    '''
    if do_remove and no_link:
      raise ValueError("do_remove and no_link may not both be true")
    fstags = self.fstags
    # start the queue with the resolved `path`
    tagged = fstags[path]
    srcpath = tagged.filepath
    tags = tagged.all_tags
    # a queue of reference directories
    q = ListQueue((dirname(srcpath),))
    linked_to = []
    for refdirpath in unrepeated(q, signature=abspath):
      with Pfx(refdirpath):
        # places to redirect this file
        mapping = self.file_by_mapping(refdirpath)
        interesting_tag_names = {tag.name for tag in mapping.keys()}
        # locate specific filing locations in the refdirpath
        refile_to = set()
        for tag_name in sorted(interesting_tag_names):
          with Pfx("tag_name %r", tag_name):
            if tag_name not in tags:
              print("  tag %r not present, skipping" % (tag_name,))
              continue
            bare_tag = Tag(tag_name, tags[tag_name])
            try:
              target_dirs = mapping.get(bare_tag, ())
            except TypeError as e:
              warning("  %s not mapped (%s), skipping", bare_tag, e)
              continue
            if not target_dirs:
              continue
            # collect other filing locations
            refile_to.update(target_dirs)
        if refile_to:
          # redistribute from here
          q.extend(refile_to)
          continue
        # file locally
        dstbase = self.auto_name(srcpath, refdirpath, tags)
        with Pfx("%s => %s", refdirpath, dstbase):
          dstpath = dstbase if isabspath(dstbase
                                         ) else joinpath(refdirpath, dstbase)
          if existspath(dstpath):
            if not samefile(srcpath, dstpath):
              warning("already exists, skipping")
            continue
          if no_link:
            linked_to.append(dstpath)
          else:
            linkto_dirpath = dirname(dstpath)
            if not isdirpath(linkto_dirpath):
              pfx_call(os.mkdir, linkto_dirpath)
            try:
              pfx_call(os.link, srcpath, dstpath)
            except OSError as e:
              warning("cannot link to %r: %s", dstpath, e)
            else:
              linked_to.append(dstpath)
              fstags[dstpath].update(tags)
              if prune_inherited:
                fstags[dstpath].prune_inherited()
    if linked_to and do_remove:
      S = os.stat(srcpath)
      if S.st_nlink < 2:
        warning(
            "not removing %r, unsufficient hard links (%s)", srcpath,
            S.st_nlink
        )
      else:
        pfx_call(os.remove, srcpath)
    return linked_to

  @pfx
  @fmtdoc
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

        We automatically skip subdirectories whose names commence with `'.'`.
        We also skip subdirectories tagged with `{TAGGER_TAG_PREFIX_DEFAULT}.skip`.
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
      for path, dirnames, _ in os.walk(realpath(dirpath)):
        with Pfx(path):
          # order the descent
          dirnames[:] = sorted(
              dname for dname in dirnames
              if dname and not dname.startswith('.')
          )
          tagged = fstags[path]
          if self.conf_tag(tagged, 'skip'):
            # tagger.skip => prune this directory tree from the mapping
            dirnames[:] = []
          else:
            # look for the tags of interest
            for tag in tagged:
              if tag.name in tag_names:
                bare_tag = Tag(tag.name, tag.value)
                mapping[bare_tag].append(tagged.filepath)
    return mapping

  @pfx
  @fmtdoc
  def file_by_mapping(self, srcdirpath):
    ''' Examine the `{TAGGER_TAG_PREFIX_DEFAULT}.file_by` tag for `srcdirpath`.
        Return a mapping of specific tag values to filing locations
        derived via `auto_file_map`.
        The file locations may be a list or a string (for convenient single locations.

        For example, I might tag my downloads directory with:

            {TAGGER_TAG_PREFIX_DEFAULT}.file_by={{"abn":"~/them/me"}}

        indicating that files with an `abn` tag may be filed in the `~/them/me` directory.
        That directory is then walked looking for the tag `abn`,
        and wherever some tag `abn=`*value*` is found on a subdirectory
        a mapping entry for `abn=`*value*=>*subdirectory* is added.

        This results in a direct mapping of specific tag values to filing locations,
        such as:

            {{ Tag('abn','***********') => ['/path/to/them/me/abn-**-***-***-***'] }}

        because the target subdirectory has been tagged with `abn="***********"`.
    '''
    fstags = self.fstags
    mapping = defaultdict(set)
    file_by = self.conf_tag(fstags[srcdirpath], 'file_by', {})
    # group the tags by file_by target path
    grouped = defaultdict(set)
    for tag_name, file_to in file_by.items():
      if isinstance(file_to, str):
        file_to = (file_to,)
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

  def suggested_tags(self, path):
    ''' Return a mapping of `tag_name=>set(tag_values)`
        representing suggested tags
        obtained from the autofiling configurations.
    '''
    tagged = self.fstags[path]
    suggestions = defaultdict(set)
    q = ListQueue([dirname(tagged.filepath)])
    for dirpath in unrepeated(q, signature=abspath):
      mapping = self.file_by_mapping(dirpath)
      for bare_tag, dstpaths in mapping.items():
        suggestions[bare_tag.name].add(bare_tag.value)
        # following the filing chain if tagged has this particular tag
        if bare_tag in tagged:
          q.extend(dstpaths)
    return suggestions

  @pfx
  @fmtdoc
  def inference_mapping(self, dirpath):
    r'''Scan `path`'s `{TAGGER_TAG_PREFIX_DEFAULT}.filename_inference` tag,
        a mapping of prefix=>rule specifications.
        Return a mapping of prefix=>inference_function
        where `inference_function(pathname)` returns a list of inferred `Tag`s.

        The prefix is a tag prefix. Example:

            {TAGGER_TAG_PREFIX_DEFAULT}.filename_inference={{
                'cs.tv_episode':
                    '/^(?P<series_title_lc>([^-]|-[^-])+)--s0*(?P<season_n>\d+)e0*(?P<episode_n>\d+)--(?P<episode_title_lc>([^-]|-[^-])+)--',
            }}

        which would try to match a filename against my habitual naming convention,
        and if so return a `TagSet` with tags named `series_title_lc`,
        `season_n`, `episode_n`, `episode_title_lc`.
    '''
    filename_inference = self.conf_tag(
        self.fstags[dirpath], 'filename_inference', {}
    )
    mapping = defaultdict(list)
    with Pfx("filename_inference=%r", filename_inference):
      for prefix, rule_spec in filename_inference.items():
        with Pfx("%r: %r", prefix, rule_spec):
          if isinstance(rule_spec, str):
            if rule_spec.startswith('/'):
              rule = RegexpTagRule(rule_spec[1:])
              mapping[prefix] = lambda path, rule=rule: rule.infer_tags(
                  basename(path)
              )
            else:
              warning("skipping unrecognised pattern")
          else:
            warning("skipping unhandled type")
    return mapping

  @pfx
  @fmtdoc
  def infer(self, path, apply=False):
    ''' Compare the `{TAGGER_TAG_PREFIX_DEFAULT}.filename_inference` rules to `path`,
        producing a mapping of prefix=>[Tag] for each rule which infers tags.
        Return the mapping.

        If `apply` is true,
        also apply all the inferred tags to `path`
        with each `Tag` *name*=*value* applied as *prefix*.*name*=*value*.
    '''
    tagged = self.fstags[path]
    srcdirpath = dirname(tagged.filepath)
    inference_mapping = self.inference_mapping(srcdirpath)
    inferences = {}
    for prefix, infer_func in inference_mapping:
      with Pfx(prefix):
        tag_list = infer_func(tagged.filepath)
        if tag_list:
          inferences[prefix] = tag_list
    if apply:
      with Pfx("apply"):
        for prefix, tag_list in inferences.items():
          for tag in tag_list:
            tag_name = prefix + '.' + tag.name
            tagged[tag_name] = tag.value
    return inferences
