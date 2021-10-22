#!/usr/bin/python
#
# Access iPhoto data.
#       - Cameron Simpson <cs@cskk.id.au> 04apr2013
#

from __future__ import print_function
import sys
import os
import os.path
from os.path import isabs as isabspath
from collections import defaultdict, namedtuple
from fnmatch import fnmatch
from functools import partial
import pprint
from getopt import GetoptError
import re
import shlex
import sqlite3
from threading import RLock
from types import SimpleNamespace as NS
from uuid import uuid4
from PIL import Image
Image.warnings.simplefilter('error', Image.DecompressionBombWarning)
from cs.dbutils import TableSpace, Table, Row
from cs.edit import edit_strings
from cs.env import envsub
from cs.fstags import FSTags
from cs.lex import get_identifier
from cs.logutils import debug, info, warning, error, setup_logging
from cs.mediainfo import EpisodeInfo
from cs.pfx import Pfx, XP
from cs.py.func import prop
from cs.seq import the
from cs.tagset import Tag
from cs.threads import locked, locked_property
from cs.upd import Upd
from cs.x import X
from .plist import ingest_plist

DEFAULT_LIBRARY = '$HOME/Pictures/iPhoto Library.photolibrary'

RE_SCENE = r's0*(\d+)[a-z]?'
RE_SCENE_PART = r's0(\d+)p0(\d+)[a-z]?'
RE_EPISODE = r'e0*(\d+)[a-z]?'
RE_EPISODE_PART = r'e0(\d+)p0(\d+)[a-z]?'
RE_EPISODE_SCENE = r'e0*(\d+)s0*(\d+)[a-z]?'
RE_SERIES_EPISODE = r's0*(\d+)e0*(\d+)[a-z]?'
RE_SERIES_EPISODE_SCENE = r's0*(\d+)e0*(\d+)s0*(\d+)[a-z]?'
RE_PART = r'p0(\d+)[a-z]?'

USAGE = '''Usage: %s [/path/to/iphoto-library-path] op [op-args...]
    -                   Read ops from standard input and execute.
    info masters        List info about masters.
    ls {0,1,2,3,4,5}    List master pathnames with specific rating.
    ls albums           List album names.
    ls [event]          List events names (default mode).
    ls folders          List folder names (includes events).
    ls {keywords|tags}  List keywords/tags.
    ls masters          List master pathnames.
    ls {people|faces}   List person/face names.
    rename {events|keywords/tags|people/faces} {/regexp|name}...
                        Rename entities.
    select criteria...  List masters with all specified criteria.
    tag criteria... [--] {+tag|-tag}...
                        Add or remove tags from selected images.
    autotag [/regexp] Autotag images from their event name.
    test [args...]      Whatever I'm testing at the moment...

Criteria:
  [!]/regexp            Filename matches regexp.
  [!]kw:[keyword]       Latest version has keyword.
                        Empty keyword means "has a keyword".
  [!]face:[person_name] Latest version has named person.
                        Empty person_name means "has a face".
                        May also be writtens "who:...".
  [!]attr{<,<=,=,>=,>}value
                        Test image attribute eg "width>=1920".
  Because "!" is often used for shell history expansion, a dash "-"
  is also accepted to invert the selector.
'''

def main(argv=None):
  ''' Main program associated with the cs.app.osx.iphoto module.
  '''
  if argv is None:
    argv = ['cs.app.osx.iphoto']
  cmd0 = argv.pop(0)
  cmd = os.path.basename(cmd0)
  usage = USAGE % (cmd,)
  setup_logging(cmd)
  with Pfx(cmd):
    if argv and argv[0].startswith('/'):
      library_path = argv.pop(0)
    else:
      library_path = os.environ.get(
          'IPHOTO_LIBRARY_PATH', envsub(DEFAULT_LIBRARY)
      )
    I = iPhoto(library_path)
    try:
      return main_iphoto(I, argv)
    except GetoptError as e:
      warning("warning: %s", e)
      print(usage, file=sys.stderr)
      return 2

def main_iphoto(I, argv):
  xit = 0
  if not argv:
    raise GetoptError("missing op")
  op = argv.pop(0)
  with Pfx(op):
    if op == '-':
      xit = cmd_(I, argv)
    elif op == 'fstags_export':
      xit = cmd_fstags_export(I, argv)
    elif op == 'info':
      xit = cmd_info(I, argv)
    elif op == 'ls':
      xit = cmd_ls(I, argv)
    elif op == 'rename':
      xit = cmd_rename(I, argv)
    elif op == 'select':
      xit = cmd_select(I, argv)
    elif op == "tag":
      xit = cmd_tag(I, argv)
    elif op == "autotag":
      xit = cmd_autotag(I, argv)
    elif op == "test":
      xit = cmd_test(I, argv)
    else:
      raise GetoptError("unrecognised op")
  return xit

def cmd_(I, argv):
  xit = 0
  badopts = False
  if argv:
    raise GetoptError("extra arguments: %s", ' '.join(argv))
  for lineno, line in enumerate(sys.stdin, 1):
    with Pfx("stdin:%d", lineno):
      line = line.strip()
      if not line or line.startswith('#'):
        continue
      print(line)
      sub_argv = shlex.split(line, comments=True)
      try:
        sub_xit = main_iphoto(I, sub_argv)
      except GetoptError as e:
        warning("%s", e)
        badopts = True
      else:
        if sub_xit != 0 and xit == 0:
          xit = sub_xit
  if badopts and xit == 0:
    xit = 2
  return xit

def cmd_fstags_export(I, argv):
  ''' Usage: fstags_export masters
  '''
  xit = 0
  if not argv:
    raise GetoptError("missing masters")
  fstags = FSTags()
  with Upd(sys.stderr) as U:
    with fstags:
      obclass = argv.pop(0)
      with Pfx(obclass):
        if obclass == 'masters':
          for master in sorted(I.masters, key=lambda m: m.pathname):
            U.out(master.pathname)
            with Pfx(master.pathname):
              tags = fstags[master.pathname]
              for tag in master.tags():
                export_tag = Tag('iphoto.' + tag.name, tag.value)
                if export_tag not in tags:
                  tags.set(export_tag, verbose=True)
        else:
          raise GetoptError("unknown class: %r" % (obclass,))
  return xit

def cmd_info(I, argv):
  ''' Usage: info masters...
  '''
  xit = 0
  if not argv:
    raise GetoptError("missing masters")
  obclass = argv.pop(0)
  with Pfx(obclass):
    if obclass == 'masters':
      for master in sorted(I.masters, key=lambda m: m.pathname):
        with Pfx(master.pathname):
          iminfo = master.image_info
          if iminfo is None:
            error("no info")
            xit = 1
          else:
            print(
                master.pathname, iminfo.dx, iminfo.dy, iminfo.format,
                *['kw:' + kwname for kwname in master.keyword_names]
            )
    else:
      raise GetoptError("unknown class: %r" % (obclass,))
  return xit

def cmd_ls(I, argv):
  xit = 0
  if not argv:
    obclass = 'events'
  else:
    obclass = argv.pop(0)
  with Pfx(obclass):
    if obclass.isdigit():
      rating = int(obclass)
      rows = I.versions_table.rows_by_value('rating', rating)
    elif obclass == 'events':
      rows = I.events
    elif obclass == 'folders':
      rows = I.folders
    elif obclass in ('keywords', 'tags'):
      rows = I.keywords
    elif obclass == 'masters':
      rows = I.masters
    elif obclass in ('faces', 'people'):
      rows = I.persons
    else:
      raise GetoptError("unknown class")
    for row in sorted(rows):
      name = row.name
      with Pfx(name):
        if argv and name not in argv:
          continue
        if not name:
          X("SKIP EMPTY NAME")
          continue
        print(name)
        if argv:
          for column_name in sorted(row.column_names):
            if column_name.endswith('Data'):
              obj = ingest_plist(row[column_name], recurse=True, resolve=True)
              print(' ', column_name + ':')
              pprint.pprint(obj.value, width=32)
            else:
              print(' ', column_name + ':', row[column_name])
        if obclass == 'albums':
          print("apalbumpath =", row.apalbum_path)
          apalbum = row.apalbum
          if apalbum is None:
            error("NO ALBUM DATA?")
          else:
            print("filter:")
            apalbum.dump()
        if obclass in ('folders', 'events'):
          for master in row.masters:
            print('   ', master.pathname)
  return xit

def cmd_rename(I, argv):
  ''' Usage: rename {events|keywords/tags|people/faces} {/regexp|name}...
  '''
  xit = 0
  if not argv:
    raise GetoptError("missing events")
  obclass = argv.pop(0)
  with Pfx(obclass):
    if obclass == 'events':
      table = I.folder_table
    elif obclass in ('keywords', 'tags'):
      table = I.keyword_table
    elif obclass in ('people', 'faces'):
      table = I.person_table
    else:
      raise GetoptError("unknown class %r" % (obclass,))
    items = list(table)
    all_names = set(item.name for item in items)
    X("%d items: %r", len(items), all_names)
    if argv:
      edit_lines = set()
      for arg in argv:
        with Pfx(arg):
          # TODO: select by regexp if /blah
          if arg.startswith('/'):
            regexp = re.compile(arg[1:-1] if arg.endswith('/') else arg[1:])
            edit_lines.update(
                item.edit_string for item in items if regexp.search(item.name)
            )
          elif '*' in arg or '?' in arg:
            edit_lines.update(
                item.edit_string for item in items if fnmatch(item.name, arg)
            )
          elif arg in all_names:
            for item in items:
              if item.name == arg:
                edit_lines.add(item.edit_string)
          else:
            raise GetoptError("unknown item name")
    else:
      edit_lines = set(item.edit_string for item in items)
    changes = edit_strings(
        sorted(edit_lines, key=lambda _: _.split(':', 1)[1]),
        errors=lambda msg: warning(msg + ', discarded')
    )
    for old_string, new_string in changes:
      with Pfx("%s => %s", old_string, new_string):
        old_modelId, old_name = old_string.split(':', 1)
        old_modelId = int(old_modelId)
        try:
          new_modelId, new_name = new_string.split(':', 1)
          new_modelId = int(new_modelId)
        except ValueError as e:
          error("invalid edited string: %s", e)
          xit = 1
        else:
          if old_modelId != new_modelId:
            error("modelId changed")
            xit = 1
          elif new_name in all_names:
            if obclass in ('keywords', 'tags'):
              # TODO: merge keywords
              print("%d: merge %s => %s" % (old_modelId, old_name, new_name))
              otherModelId = the(
                  item.modelId for item in items if item.name == new_name
              )
              I.replace_keywords(old_modelId, otherModelId)
              I.expunge_keyword(old_modelId)
            else:
              error("new name already in use: %r", new_name)
              xit = 1
          else:
            print("%d: %s => %s" % (old_modelId, old_name, new_name))
            table[old_modelId].name = new_name
  return xit

def cmd_select(I, argv):
  xit = 0
  badopts = False
  if not argv:
    raise GetoptError("missing selectors")
  selectors = []
  for selection in argv:
    with Pfx(selection):
      try:
        selector = I.parse_selector(selection)
      except ValueError as e:
        warning("invalid selector: %s", e)
        badopts = True
      else:
        selectors.append(selector)
  if badopts:
    raise GetoptError("invalid arguments")
  masters = None
  for selector in selectors:
    masters = selector.select(masters)
  for master in masters:
    print(master.pathname)
  return xit

def cmd_tag(I, argv):
  ''' Add or remove tags from selected images.
      Usage: tag criteria... [--] {+tag|-tag}...
  '''
  xit = 0
  badopts = False
  if not argv:
    raise GetoptError("missing selector")
  # collect criteria
  selectors = []
  unknown = False
  while argv:
    selection = argv.pop(0)
    if selection == '--':
      break
    if selection.startswith('+'):
      argv.insert(0, selection)
      break
    with Pfx(selection):
      try:
        selector = I.parse_selector(selection)
      except KeyError as e:
        warning(e)
        unknown = True
      except ValueError as e:
        warning("invalid selector: %s", e)
        badopts = True
      else:
        selectors.append(selector)
  if unknown:
    return 1
  if not argv:
    raise GetoptError("missing tags")
  # collect tag changes
  tagging = []
  for arg in argv:
    try:
      with Pfx(arg):
        if not arg:
          raise GetoptError("invalid empty tag")
        kw_op = arg[0]
        if kw_op not in ('+', '-'):
          raise GetoptError(
              "invalid tag op, requires leading '+' or '-': %r" % (kw_op,)
          )
        kw_name = arg[1:]
        try:
          kw = I.keyword(kw_name)
        except KeyError:
          warning("unknown tag, CREATE")
          kw = I.create_keyword(kw_name)
        except ValueError:
          warning("ambiguous tag")
          continue
        tagging.append((kw_op == '+', kw))
    except GetoptError as e:
      warning(e)
      badopts = True
  if badopts:
    raise GetoptError("invalid arguments")
  if not tagging:
    warning("no tags to apply, skipping")
    return 0
  masters = None
  for selector in selectors:
    masters = selector.select(masters)
  for master in masters:
    with Pfx(master.basename):
      XP("examine %s", master.basename)
      V = master.latest_version
      for add, tag in tagging:
        with Pfx("%s%s", "+" if add else "-", tag.name):
          kws = V.keywords
          if add:
            if tag not in kws:
              V.add_keyword(tag)
              info('OK')
          else:
            if tag in kws:
              V.del_keyword(tag)
              info('OK')
  return xit

def cmd_autotag(I, argv):
  ''' Apply tags to images based on the text of their event names.
  '''
  xit = 0
  if argv and argv[0].startswith('/'):
    ptn = argv.pop(0)[1:]
    ptn_re = re.compile(ptn, re.I)
  else:
    ptn = None
  if argv:
    raise GetoptError('extra arguments: %s' % (' '.join(argv),))
  events = I.events
  if ptn:
    X("winnow %d events", len(events))
    events = [E for E in events if ptn_re.search(E.name)]
  warned_faces = set()
  warned_kws = set()
  for event in sorted(events):
    with Pfx("event %r", event.name):
      kws = set()
      for part in event.name.split('--'):
        part = part.strip('-')
        if not part:
          continue
        with Pfx(part):
          # look for series/episode/scene/part markers
          fields, offset = EpisodeInfo.parse_filename_part(part)
          if offset == len(part):
            kwnames = []
            for field, value in fields.items():
              kwnames.append('%s-%02d' % (field, value))
            for kwname in kwnames:
              try:
                kw = I.keyword(kwname)
              except KeyError:
                kw = I.create_keyword(kwname)
              kws.add(kw)
            continue
          # look for person+person...
          if '+' in part:
            for name0 in part.split('+'):
              if name0 in warned_faces:
                continue
              name = name0.replace('-', ' ')
              face = I.person_table.get(name)
              if not face:
                warning("unknown face reference %r", name0)
                warned_faces.add(name0)
                continue
              debug("expecting face %s", face.name)
          else:
            kw = I.get_keyword(part)
            if kw is None:
              face = I.person_table.get(part.replace('-', ' '))
              if face is None:
                if False and '-' in part:
                  for subpart in part.split('-'):
                    if subpart in warned_kws:
                      continue
                    with Pfx(subpart):
                      kw = I.get_keyword(subpart)
                      if kw:
                        kws.add(kw)
                      elif subpart in warned_kws:
                        pass
                      else:
                        warning("unknown keyword")
                        warned_kws.add(subpart)
                elif part in warned_kws:
                  pass
                else:
                  warning("unknown keyword")
                  warned_kws.add(part)
              else:
                debug("expecting face for %s", part)
            else:
              kws.add(kw)
      if kws:
        for V in event.versions:
          vkws = V.keywords
          for kw in sorted(kws):
            if kw not in vkws:
              with Pfx("%s + %s", V.name, kw.name):
                V.add_keyword(kw)
                info("OK")
      else:
        ##warning("no recognised keywords, no tagging")
        pass
  return xit

def cmd_test(I, argv):
  AD = I.load_albumdata()
  print('AlbumData.xml:')
  print(pprint.pformat(AD._as_dict(), indent=2, width=32))
  sys.exit(1)
  ##for folder in I.read_folders():
  ##  print('uuid =', folder.uuid, 'folderType =', folder.folderType, 'name =', folder.name)
  ##for keyword in I.read_keywords():
  ##  print('uuid =', keyword.uuid, 'name =', keyword.name)
  ##for master in I.read_masters():
  ##  print('uuid =', master.uuid, 'name =', master.name, 'originalFileName =', master.originalFileName, 'imagePath =', master.imagePath, 'pathname =', master.pathname)
  ##for v in I.read_versions():
  ##  print('uuid =', v.uuid, 'masterUuid =', v.masterUuid, 'versionNumber =', v.versionNumber)
  ##for kwv in I.keywords_to_versions():
  ##  print('keywordId =', kwv.keywordId, 'versionId =', kwv.versionId)
  ##for album in I.album_table.read_rows():
  ##  print('uuid =', album.uuid, 'albumType =', album.albumType, 'name =', album.name)
  ##for kwname in sorted(I.keyword_names()):
  ##  kw = I.keyword_by_name.get(kwname)
  ##  print(kw.name, [ master.name for master in kw.masters() ])
  for vface in I.vfaces():
    print(vface)
    P = vface.person()
    print("Person", P)
    master = vface.master
    print("Master", master)
    print(" ", master.pathname)
    for vface2 in master.vfaces:
      print("  VFace", vface)
    FI = vface.Image()
    filebase, ext = os.path.splitext(os.path.basename(master.pathname))
    filename = '%s-FACE%s' % (filebase, ext)
    print("save to %r" % (filename,))
    FI.save(filename)
    FI.close()
    break
  return 0

Image_Info = namedtuple('Image_Info', 'dx dy format')

class iPhoto(NS):
  ''' Access an iPhoto library.
      This contains multiple sqlite3 databases.
  '''

  def __init__(self, libpath=None):
    ''' Open the iPhoto library stored at `libpath`.
        If `libpath` is not supplied, use DEFAULT_LIBRARY.
    '''
    if libpath is None:
      libpath = envsub(DEFAULT_LIBRARY)
    if not os.path.isdir(libpath):
      raise ValueError("not a directory: %r" % (libpath,))
    self.path = libpath
    self.table_by_nickname = {}
    self._lock = RLock()
    self.dbs = iPhotoDBs(self)
    self.dbs.load_all()
    # set up intertable links now that the Tables are defined
    # direct relations
    for nickname, table in sorted(self.table_by_nickname.items()):
      for rel_name, rel_defn in sorted(table.schema.get('link_to',
                                                        {}).items()):
        with Pfx("%s.link_to[%r]:%r", nickname, rel_name, rel_defn):
          info("LINK TO: %s.to_%ss => %r", nickname, rel_name, rel_defn)
          local_column, other_table, other_column = rel_defn
          if isinstance(other_table, str):
            other_table = self.table_by_nickname[other_table]
          table.link_to(
              other_table,
              local_column=local_column,
              other_column=other_column,
              rel_name=rel_name
          )
    # relations via mapping tables
    for nickname, table in sorted(self.table_by_nickname.items()):
      for rel_name, rel_defn in sorted(table.schema.get('link_via',
                                                        {}).items()):
        with Pfx("%s.link_via[%r]:%r", nickname, rel_name, rel_defn):
          info("LINK VIA: %s.to_%ss => %r", nickname, rel_name, rel_defn)
          local_column, \
            via_table, via_left_column, via_right_column, \
            other_table, other_column = rel_defn
          if isinstance(other_table, str):
            other_table = self.table_by_nickname[other_table]
          if isinstance(via_table, str):
            via_table = self.table_by_nickname[via_table]
          table.link_via(
              via_table,
              via_left_column,
              via_right_column,
              other_table,
              right_column=other_column,
              rel_name=rel_name
          )

  def pathto(self, rpath):
    if rpath.startswith('/'):
      raise ValueError('rpath may not start with a slash: %r' % (rpath,))
    return os.path.join(self.path, rpath)

  def db_names(self):
    return self.dbs.db_names()

  def dbpath(self, db_name):
    return self.dbs.pathto(db_name)

  def __getattr__(self, attr):
    with Pfx('.' + attr):
      try:
        if not attr.startswith('_'):
          if attr.endswith('s'):
            # {nickname}s => iter(table-nickname)
            if '_' not in attr:
              # *s ==> iterable of * (obtained from *_by_id)
              nickname = attr[:-1]
              try:
                T = self.table_by_nickname[nickname]
              except KeyError:
                raise RuntimeError("no table with nickname %r" % (nickname,))
              return iter(T)
            # *_rows ==> iterator of rows from table "*"
            if attr.endswith('_rows'):
              nickname = attr[:-5]
              return iter(self.table_by_nickname[nickname])
          if attr.startswith('select_by_'):
            criterion_words = attr[10:].split('_')
            class_name = 'SelectBy' + '_'.join(
                word.title() for word in criterion_words
            )
            return partial(globals()[class_name], self)
          if attr.endswith('_table'):
            # *_table ==> table "*"
            nickname = attr[:-6]
            if nickname in self.table_by_nickname:
              return self.table_by_nickname[nickname]
            else:
              X(
                  "no table with nickname %r: nicknames=%r", nickname,
                  sorted(self.table_by_nickname.keys())
              )
      except AttributeError as e:
        msg = "__getattr__ got internal AttributeError: %s" % (e,)
        raise RuntimeError(msg)
      msg = "iPhoto.__getattr__: nothing named %r" % (attr,)
      raise AttributeError(msg)

  def album(self, album_id):
    return self.albums_table[album_id]

  @property
  def albumdata_path(self):
    ''' Pathname of the AlbumData.xml file, saved when iPhoto quits.
    '''
    return self.pathto('AlbumData.xml')

  def load_albumdata(self):
    return ingest_plist(self.albumdata_path, recurse=True, resolve=True)

  @locked_property
  def albumdata_xml_plist(self):
    ''' Ingest and cache the AlbumData.xml file.
    '''
    return self.load_albumdata()

  def folder(self, folder_id):
    return self.folders_table[folder_id]

  @prop
  def folders_simple(self):
    return [folder for folder in self.folders if folder.is_simple_folder]

  def event(self, event_id):
    folder = self.folder(event_id)
    if not folder.is_event:
      return None
    return folder

  @prop
  def events(self):
    return [folder for folder in self.folders if folder.is_event]

  def match_people(self, person_name):
    ''' User convenience: match string against all person names, return Person rows.
    '''
    lc_person_name = person_name.lower()
    people = self.persons
    matches = set()
    # try exact match, ignoring case
    for P in people:
      if lc_person_name == P.name.lower():
        matches.add(P)
    if not matches:
      # try "who aka who-else" and "formerly" and "was"
      for split_word in 'aka', 'formerly', 'was':
        split_word = ' ' + split_word + ' '
        for P in people:
          aliases = P.name.lower().split(split_word)
          if lc_person_name in aliases:
            matches.add(P)
    if not matches:
      # try "who (where)"
      for P in people:
        if P.name.endswith(')'):
          try:
            left, etc = P.name.split(' (', 1)
          except ValueError:
            continue
          if left.lower().strip() == lc_person_name:
            matches.add(P)
    if not matches:
      # try by word: all words but in any order
      lc_person_words = lc_person_name.split()
      for P in people:
        lc_words = P.name.lower().split()
        match_count = 0
        for lc_person_word in lc_person_words:
          if lc_person_word in lc_words:
            match_count += 1
        if match_count == len(lc_person_words):
          matches.add(P)
    if not matches:
      # try substrings
      for P in people:
        if lc_person_name in P.name.lower():
          matches.add(P)
    return matches

  def match_one_person(self, person_name):
    matches = self.match_people(person_name)
    if not matches:
      raise ValueError("unknown person")
    if len(matches) > 1:
      raise ValueError("matches multiple people, rejected: %r" % (matches,))
    return matches.pop()

  def master(self, master_id):
    return self.masters_table[master_id]

  def master_pathnames(self):
    return [master.pathname for master in self.masters_table]

  def keyword_names(self):
    return frozenset(kw.name for kw in self.keywords())

  def match_keywords(self, kwname):
    ''' User convenience: match string against all keywords, return matches.
    '''
    kw = self.keyword_table.get(kwname)
    if kw:
      return kw,
    if not isinstance(kwname, str):
      return ()
    lc_kwname = kwname.lower()
    kw = self.keyword_table.get(lc_kwname)
    if kw:
      return kw,
    for sep in None, '/', '.':
      kws = []
      for kw in self.keywords:
        words = kw.name.split(sep)
        if words and lc_kwname == words[0].lower():
          kws.append(kw)
      if kws:
        return kws
    return ()

  def keyword(self, kwname):
    ''' Try to match a single keyword.
    '''
    with Pfx("I.keyword(%r)", kwname):
      kws = self.match_keywords(kwname)
      # no match
      if not kws:
        raise KeyError("unknown keyword")
      if len(kws) == 1:
        # exact match
        return kws[0]
      pfxkws = []
      for kw in kws:
        for suffix in ' (', '/':
          if kw.name.startswith(kwname + suffix):
            pfxkws.append(kw)
            break
      if len(pfxkws) == 1:
        return pfxkws[0]
      # multiple inexact matches
      raise ValueError(
          "matches multiple keywords, rejected: %r" %
          ([kw.name for kw in kws],)
      )

  def get_keyword(self, kwname, default=None):
    try:
      return self.keyword(kwname)
    except KeyError:
      return default
    except ValueError as e:
      warning("invalid kwname: %s", e)
      return None

  def versions_by_keyword(self, kwname):
    return self.keyword(kwname).versions

  def masters_by_keyword(self, kwname):
    return self.keyword(kwname).masters

  def replace_keywords(self, old_keyword_id, new_keyword_id):
    ''' Update image tags to replace one keyword with another.
    '''
    self \
      .table_by_nickname['keywordForVersion'] \
      . update_by_column('keywordId', new_keyword_id,
                         'keywordId', old_keyword_id)

  def expunge_keyword(self, keyword_id):
    ''' Remove the specified keyword.
    '''
    # remove keyword from versions
    self \
      .table_by_nickname['keywordForVersion'] \
      .delete_by_column('keywordId', keyword_id)
    # remove keyword definition
    self \
      .table_by_nickname['keyword'] \
      .delete_by_column('modelId', keyword_id)

  def create_keyword(self, kw_name):
    # create new keyword definition
    info("CREATE new keyword %r", kw_name)
    self \
      .table_by_nickname['keyword'] \
      .insert( ('uuid', 'name'),
               ( (str(uuid4()), kw_name), )
             )
    return self.keyword(kw_name)

  def parse_selector(self, selection):
    ''' Parse a single image selection criterion.
        A leading "!" or "-" inverts the test.
        /regexp                 Compare image filename against regexp.
        {kw,keyword,tag}:       Image has at least one keywords.
        {kw,keyword,tag}:kwname Image has keyword "kwname".
        {who,face}:             Image has at least one face.
        {who,face}:name         Imagine contains the named face.
        attr{<,<=,=,>=,>}value  Test image attribute eg "width>=1920".
    '''
    with Pfx(selection):
      selection0 = selection
      selector = None
      invert = False
      if selection.startswith('!') or selection.startswith('-'):
        invert = True
        selection = selection[1:]
      if selection.startswith('/'):
        re_text = selection[1:]
        selector = SelectByFilenameRE(self, re_text, invert)
      else:
        sel_type, offset = get_identifier(selection)
        if not sel_type:
          raise ValueError("expected identifier at %r" % (selection,))
        if offset == len(selection):
          raise ValueError("expected delimiter after %r" % (sel_type,))
        selection = selection[offset:]
        if selection.startswith(':'):
          selection = selection[1:]
          if sel_type in ('keyword', 'kw', 'tag'):
            kwname = selection
            if not kwname:
              selector = SelectByFunction(
                  self, lambda master: len(master.keywords) > 0, invert
              )
            else:
              try:
                kw = self.keyword(kwname)
              except KeyError:
                warning(
                    "no match for keyword %r, using dummy selector", kwname
                )
                selector = SelectByKeyword_Name(self, None, invert)
              except ValueError as e:
                raise ValueError("invalid keyword: %s" % (e,))
              else:
                if kw.name != kwname:
                  debug("%r ==> %r", kwname, kw.name)
                selector = SelectByKeyword_Name(self, kw.name, invert)
          elif sel_type in ('face', 'who'):
            person_name = selection
            if not person_name:
              selector = SelectByFunction(
                  self, lambda master: len(master.faces) > 0, invert
              )
            else:
              operson_name = person_name
              try:
                P = self.match_one_person(person_name)
              except ValueError as e:
                raise KeyError("rejected face name: %r: %s" % (person_name, e))
              if P.name != operson_name:
                info("%r ==> %r", operson_name, P.name)
              selector = SelectByPerson_Name(self, P.name, invert)
          else:
            raise ValueError("unknown selector type %r" % (sel_type,))
        elif selection[0] in '<=>':
          cmpop = selection[0]
          selection = selection[1:]
          if selection.startswith('='):
            cmpop += '='
            selection = selection[1:]
          left = sel_type
          right = selection
          selector = SelectByComparison(self, left, cmpop, right, invert)
        else:
          raise ValueError("unrecognised delimiter after %r" % (sel_type,))
      if selector is None:
        raise RuntimeError(
            "parse_selector(%r) did not set selector" % (selection0,)
        )
      return selector

class iPhotoDBs(object):

  def __init__(self, iphoto):
    self.iphoto = iphoto
    self.dbmap = {}
    self.named_tables = {}
    self._lock = iphoto._lock

  def load_all(self):
    for db_name in 'Library', 'Faces':
      self._load_db(db_name)

  @prop
  def dbdirpath(self):
    return self.iphoto.pathto('Database/apdb')

  def db_names(self):
    for basename in os.listdir(self.dbdirpath):
      if basename.endswith('.apdb'):
        yield basename[:-5]

  def pathto(self, db_name):
    ''' Compute pathname of named database file.
    '''
    if db_name == 'Faces':
      return os.path.join(self.dbdirpath, db_name + '.db')
    return os.path.join(self.dbdirpath, db_name + '.apdb')

  def _load_db(self, db_name):
    db = self.dbmap[db_name] = iPhotoDB(self.iphoto, db_name)
    return db

  @locked
  def __getattr__(self, db_name):
    dbmap = self.dbmap
    if db_name in dbmap:
      return dbmap[db_name]
    dbpath = self.pathto(db_name)
    if os.path.exists(dbpath):
      return self._load_db(db_name)
    raise AttributeError(db_name)

class iPhotoDB(TableSpace):

  def __init__(self, iphoto, db_name):
    TableSpace.__init__(self, iPhotoTable, iphoto._lock, db_name=db_name)
    global SCHEMAE
    self.iphoto = iphoto
    self.dbpath = iphoto.dbpath(db_name)
    self.conn = sqlite3.connect(self.dbpath)
    self.param_style = '?'
    self.schema = SCHEMAE[db_name]
    for nickname, schema in self.schema.items():
      self.iphoto.table_by_nickname[nickname] = iPhotoTable(
          self, nickname, schema
      )

class iPhotoTable(Table):

  def __init__(self, db, nickname, schema):
    table_name = schema['table_name']
    column_names = schema['columns']
    row_class = schema.get('mixin', iPhotoRow)
    name_column = schema.get('name')
    if name_column is None and 'name' in column_names:
      name_column = 'name'
    Table.__init__(
        self,
        db,
        table_name,
        column_names=column_names,
        id_column='modelId',
        name_column=name_column,
        row_class=row_class
    )
    self.nickname = nickname
    self.schema = schema

  @prop
  def iphoto(self):
    return self.db.iphoto

  @prop
  def conn(self):
    return self.db.conn

  def update_by_column(
      self, upd_column, upd_value, sel_column, sel_value, sel_op='='
  ):
    return self.update_columns(
        (upd_column,), (upd_value,), '%s %s ?' % (sel_column, sel_op),
        sel_value
    )

  def delete_by_column(self, sel_column, sel_value, sel_op='='):
    return self.delete('%s %s ?' % (sel_column, sel_op), sel_value)

class iPhotoRow(Row):

  @prop
  def iphoto(self):
    return self._table.iphoto

  @prop
  def edit_string(self):
    return "%d:%s" % (self.modelId, self.name)

  def __eq__(self, other):
    return self.modelId == other.modelId

  def __hash__(self):
    return self.modelId

class Album_Mixin(iPhotoRow):

  @prop
  def apalbum_path(self):
    return self.iphoto.pathto(
        os.path.join('Database/Albums', self.uuid + '.apalbum')
    )

  @locked_property
  def apalbum(self):
    try:
      return AlbumPList(self.apalbum_path)
    except FileNotFoundError as e:
      error("apalbum: %r: %s", self.apalbum_path, e)
      return None

class AlbumPList(object):

  def __init__(self, plistpath):
    self.path = plistpath
    self.plist = ingest_plist(plistpath, recurse=True, resolve=True)

  def __str__(self):
    return "<AlbumPList:%r>" % (self.plist,)

  def __repr__(self):
    return "<AlbumPList %r:%r>" % (self.path, self.plist)

  @prop
  def filter(self):
    return self.plist['FilterInfo']

  def dump(self):
    Q = self.get_query()
    XP("QUERY = %s", Q)
    XP("RUN = %s", Q.run())

  def get_query(self):
    return FilterQuery(self.filter)

def FilterQuery(ifilter):
  classname = ifilter['queryClassName']
  if classname == 'RKSingleItemQuery':
    return SingleItemQuery(ifilter)
  if classname == 'RKMultiItemQuery':
    return MultiItemQuery(ifilter)
  raise ValueError("unsupported filter query class name %r", classname)

class BaseFilterQuery(NS):

  def __init__(self, ifilter):
    ##XP("BaseFilterQuery: ifilter=%r", ifilter)
    super().__init__(**ifilter)
    self._ifilter = ifilter

class SingleItemQuery(BaseFilterQuery):

  def __str__(self):
    return "SingleItemQuery(%r)" % (self._ifilter,)

  __repr__ = __str__

  def run(self):
    invert = self.queryIsEnabled
    return ()

class MultiItemQuery(BaseFilterQuery):

  def __init__(self, ifilter):
    BaseFilterQuery.__init__(self, ifilter)
    self.querySubqueries = [FilterQuery(f) for f in self.querySubqueries]

  def __str__(self):
    return "MultiItemQuery(%s)" \
           % ( ",".join(str(q) for q in self.querySubqueries), )

  def run(self):
    invert = self.queryIsEnabled
    conjunction = self.queryMatchType == 1
    return ()

class Master_Mixin(iPhotoRow):

  @prop
  def basename(self):
    return os.path.basename(self.imagePath)

  @prop
  def pathname(self):
    imagepath = self.imagePath
    if not isabspath(imagepath):
      if imagepath[:4].isdigit():
        imagepath = os.path.join(self.iphoto.pathto('Masters'), imagepath)
      else:
        imagepath = os.path.join('/', imagepath)
    return imagepath

  @locked_property
  def versions(self):
    return self.to_versions

  @locked_property
  def latest_version(self):
    vs = self.versions
    if not vs:
      warning("no versions for master %d: %r", self.modelId, self.pathname)
      return None
    if len(vs) == 1:
      return vs[0]
    return max(*vs, key=lambda v: v.versionNumber)

  @prop
  def width(self):
    return self.latest_version and self.latest_version.processedWidth

  @prop
  def height(self):
    return self.latest_version and self.latest_version.processedHeight

  @prop
  def detected_faces(self):
    return self.latest_version and self.latest_version.detected_faces

  @prop
  def faces(self):
    return self.latest_version and self.latest_version.faces

  @prop
  def keywords(self):
    return self.latest_version and self.latest_version.keywords

  @prop
  def keyword_names(self):
    return self.latest_version and self.latest_version.keyword_names

  def Image(self):
    ''' Obtain an open Image of this master.
        Caller must close.
    '''
    return Image.open(self.pathname)

  @locked_property
  def image_info(self):
    pathname = self.pathname
    with Pfx("Image.open(%r)", pathname):
      try:
        image = self.Image()
      except OSError as e:
        error("cannot load image: %s", e)
        return None
      dx, dy = image.size
      image_info = Image_Info(dx, dy, image.format)
      image.close()
    return image_info

  @prop
  def dx(self):
    return self.image_info.dx

  @prop
  def dy(self):
    return self.image_info.dy

  @prop
  def format(self):
    return self.image_info.format

  def tags(self):
    yield Tag('imagepath', self.imagePath)
    yield Tag('dx', self.width)
    yield Tag('dy', self.height)
    faces = self.faces
    if faces:
      face_names = sorted(face.name for face in self.faces)
      if face_names:
        yield Tag('faces', face_names)
    kwnames = self.keyword_names
    if kwnames:
      kwmap = defaultdict(list)
      for kwname in sorted(kwnames):
        m = re.match(r'(?P<field>[a-z]+)-0*(?P<value>\d+)$', kwname)
        if m:
          kwmap[m.group('field')].append(int(m.group('value')))
          continue
        while True:
          m = re.match(
              r'\s*(?P<prefix>.*\S)\s+\(\s*(?P<category>.*\S)\s*\)\s*$', kwname
          )
          if m:
            kwname = m.group('category') + '.' + m.group('prefix')
            continue
          break
        kwname = kwname.lower().replace(' ', '-')
        try:
          kwname, kwvalue = kwname.rsplit(':', 1)
        except ValueError:
          kwvalue = None
        kwmap[kwname].append(kwvalue)
      for kw, values in sorted(kwmap.items()):
        if not values:
          continue
        if len(values) == 1:
          yield Tag('kw.' + kw, values[0])
        else:
          yield Tag('kw.' + kw, values)

class Version_Mixin(iPhotoRow):

  @locked_property
  def master(self):
    master = self.iphoto.master_table[self.masterId]
    if master is None:
      raise ValueError(
          "version %d masterId %d matches no master" %
          (self.modelId, self.masterId)
      )
    return master

  @prop
  def keywords(self):
    return self.to_keywords

  @prop
  def keyword_names(self):
    return [kw.name for kw in self.keywords]

  def add_keyword(self, kw):
    # add keyword to version
    if isinstance(kw, str):
      kw = self.iphoto.keyword(kw)
    self.keywords += kw.modelId

  def del_keyword(self, kw):
    # remove keyword from version
    if isinstance(kw, str):
      kw = self.iphoto.keyword(kw)
    self.keywords -= kw.modelId

  @prop
  def detected_faces(self):
    ''' Return face detections for this image version.
    '''
    return self.to_detected_faces

  @prop
  def faces(self):
    return self.to_faces

class Folder_Mixin(Album_Mixin):

  @prop
  def masters(self):
    ''' Return the masters from this album.
    '''
    return self.to_masters

  @prop
  def versions(self):
    return [M.latest_version for M in self.masters]

  @property
  def is_event(self):
    return self.sortKeyPath == 'custom.kind'

  @property
  def is_simple_folder(self):
    return self.sortKeyPath == 'custom.default'

class Keyword_Mixin(iPhotoRow):

  @prop
  def versions(self):
    ''' Return the versions with this keyword.
    '''
    return self.to_versions

  @prop
  def masters(self):
    ''' Return the masters with this keyword.
    '''
    ms = set()
    for version in self.versions:
      ms.add(version.master)
    return ms

  @prop
  def latest_versions(self):
    ''' Return the latest version of all masters with this keyword.
    '''
    return set(master.latest_version for master in self.masters)

class Person_Mixin(iPhotoRow):

  @locked_property
  def vfaces(self):
    return set()

# association of masters/versions/faces
class VFace_Mixin(iPhotoRow):

  @prop
  def master(self):
    ''' The master for this row.
    '''
    return self.iphoto.master(self.masterId)

  def person(self):
    if not self.isNamed:
      return None
    return self.iphoto.person(self.faceKey)

  def Image(self, padfactor=1.0):
    ''' Return an Image of this face.
    '''
    MI = self.master.Image()
    mdx, mdy = MI.size
    # convert face box into centre and radii
    rx = self.faceRectWidth / 2 * padfactor
    ry = self.faceRectHeight / 2 * padfactor
    cx = self.faceRectLeft + rx
    cy = self.faceRectTop + ry
    X("RX = %s, RY = %s, CX = %s, CY = %s", rx, ry, cx, cy)
    # Image y-ordinates are inverse of iPhoto coordinates
    ##cx = mdx - cx
    cy = 1.0 - cy
    face_box = (
        int(mdx * (cx - rx)),
        int(mdy * (cy - ry)),
        int(mdx * (cx + rx)),
        int(mdy * (cy + ry)),
    )
    XP("MI.size = %r, face_box = %r", MI.size, face_box)
    face_Image = MI.crop(face_box)
    face_Image.load()
    MI.close()
    return face_Image

class _SelectMasters(object):
  ''' Select masters base class.
  '''

  def select(self, masters=None):
    if masters is None:
      return self.select_from_all()
    else:
      return self.select_masters(masters)

  def select_from_all(self):
    return self.select_masters(self.iphoto.masters)

class SelectByFunction(_SelectMasters):
  ''' Select by arbitrary function on a master.
  '''

  def __init__(self, iphoto, func, invert=False):
    self.iphoto = iphoto
    self.func = func
    self.invert = invert

  def select_masters(self, masters):
    func = self.func
    invert = self.invert
    for master in masters:
      if func(master):
        if not invert:
          yield master
      elif invert:
        yield master

COMPARATORS = {
    '<': lambda left, right: left < right,
    '<=': lambda left, right: left <= right,
    '==': lambda left, right: left == right,
    '!=': lambda left, right: left != right,
    '>=': lambda left, right: left >= right,
    '>': lambda left, right: left > right,
}

class SelectByComparison(_SelectMasters):

  def __init__(self, iphoto, left, cmpop, right, invert):
    try:
      cmpfunc = COMPARATORS[cmpop]
    except KeyError:
      raise ValueError('unknown comparison operator %r', cmpop)
    self.iphoto = iphoto
    self.left = left
    self.cmpfunc = cmpfunc
    self.right = right
    self.cmpfunc
    self.invert = invert

  def select_masters(self, masters):
    invert = self.invert
    left = self.left
    right = self.right
    cmpfunc = self.cmpfunc
    for master in masters:
      try:
        left_val = float(left)
      except ValueError:
        try:
          left_val = int(left)
        except ValueError:
          left_val = getattr(master, left)
      try:
        right_val = float(right)
      except ValueError:
        try:
          right_val = int(right)
        except ValueError:
          right_val = getattr(master, right)
      if cmpfunc(left_val, right_val):
        if not invert:
          yield master
      else:
        if invert:
          yield master

class SelectByPerson_Name(_SelectMasters):
  ''' Select masters by person name.
  '''

  def __init__(self, iphoto, person_name, invert=False):
    self.iphoto = iphoto
    self.person_name = person_name
    self.person = iphoto.match_one_person(person_name)
    self.invert = invert

  def select_masters(self, masters):
    person = self.person
    if self.invert:
      for master in masters:
        if person not in master.faces:
          yield master
    else:
      for master in masters:
        fs = master.faces
        if person in fs:
          yield master

class SelectByFilenameRE(_SelectMasters):
  ''' Select masters by regular expression.
  '''

  def __init__(self, iphoto, re_text, invert=False):
    self.iphoto = iphoto
    self.re_text = re_text
    self.invert = invert
    self.re = re.compile(re_text)

  def select_masters(self, masters):
    re = self.re
    if self.invert:
      for master in masters:
        if not re.search(master.latest_version.fileName):
          yield master
    else:
      for master in masters:
        if re.search(master.latest_version.fileName):
          yield master

class SelectByKeyword_Name(_SelectMasters):
  ''' Select masters by keyword name.
  '''

  def __init__(self, iphoto, kwname, invert=False):
    self.iphoto = iphoto
    self.kwname = kwname
    self.invert = invert

  def select(self, masters=None):
    if self.kwname is None:
      if self.invert:
        if masters is None:
          masters = self.iphoto.masters
        return masters
      return ()
    return super().select(masters)

  def select_from_all(self):
    ''' Yield all matching masters.
    '''
    if self.invert:
      # invert requires more work
      return self.select_masters(self.iphoto.masters)
    else:
      # no invert can use a faster method
      return self.iphoto.masters_by_keyword(self.kwname)

  def select_masters(self, masters):
    ''' Yield from `masters` matching `self.kwname`.
    '''
    kwname = self.kwname
    if self.invert:
      for master in masters:
        if kwname not in master.keyword_names:
          yield master
    else:
      for master in masters:
        if kwname in master.keyword_names:
          yield master

SCHEMAE = {'Faces':
            { # person definition: their name, etc
              'person':
                { 'table_name': 'RKFaceName',
                  'mixin': Person_Mixin,
                  'columns':
                    ( 'modelId', 'uuid', 'faceKey', 'keyVersionUuid',
                      'name', 'fullName', 'email', 'similarFacesCached',
                      'similarFacesOpen', 'manualOrder', 'lastUsedDate',
                      'attrs',
                    ),
                },
              # detected faces in master images
              'face':
                { 'table_name': 'RKDetectedFace',
                  'columns':
                    ( 'modelId', 'uuid', 'masterUuid', 'altMasterUuid',
                      'faceKey', 'correlatedFaceKey', 'ownerServiceKey',
                      'faceIndex', 'width', 'height', 'topLeftX',
                      'topLeftY', 'topRightX', 'topRightY', 'bottomLeftX',
                      'bottomLeftY', 'bottomRightX', 'bottomRightY',
                      'confidence', 'sharpness', 'exposureValue',
                      'rejected', 'faceCount', 'ignore', 'faceFlags',
                      'hasFaceTile', 'tileFacePosition', 'faceAngle',
                      'faceDirectionAngle', 'faceSkinScore',
                      'skippedInUnnamedFaces',
                    ),
                },
            },
           'Library':
            {
              # master images
              'master':
                { 'table_name': 'RKMaster',
                  'mixin': Master_Mixin,
                  'link_to': {
                    'version': ('modelId', 'version', 'masterId'),
                  },
                  'columns':
                    ( 'modelId', 'uuid', 'name', 'projectUuid', 'importGroupUuid',
                      'fileVolumeUuid', 'alternateMasterUuid', 'originalVersionUuid',
                      'originalVersionName', 'fileName', 'type', 'subtype',
                      'fileIsReference', 'isExternallyEditable', 'isTrulyRaw',
                      'isMissing', 'hasAttachments', 'hasNotes', 'hasFocusPoints',
                      'imagePath', 'fileSize', 'pixelFormat',
                      'duration', 'imageDate', 'fileCreationDate', 'fileModificationDate',
                      'imageHash', 'originalFileName', 'originalFileSize', 'imageFormat',
                      'importedBy', 'createDate', 'isInTrash', 'faceDetectionState',
                      'colorSpaceName', 'colorSpaceDefinition', 'fileAliasData',
                      'streamAssetId', 'streamSourceUuid', 'burstUuid',
                    ),
                },
              # faces in image versions
              'vface':
                { 'table_name': 'RKVersionFaceContent',
                  'mixin': VFace_Mixin,
                  'columns':
                    ( 'modelId', 'versionId', 'masterId', 'isNamed', 'faceKey', 'faceIndex', 'faceRectLeft', 'faceRectTop', 'faceRectWidth', 'faceRectHeight',
                    ),
                },
              # events
              'folder':
                { 'table_name': 'RKFolder',
                  'mixin': Folder_Mixin,
                  'link_to': {
                    'master': ('uuid', 'master', 'projectUuid'),
                  },
                  'columns':
                    ( 'modelId', 'uuid', 'folderType', 'name', 'parentFolderUuid',
                      'implicitAlbumUuid', 'posterVersionUuid',
                      'automaticallyGenerateFullSizePreviews', 'versionCount',
                      'minImageTimeZoneName', 'maxImageTimeZoneName', 'minImageDate',
                      'maxImageDate', 'folderPath', 'createDate', 'isExpanded',
                      'isHidden', 'isHiddenWhenEmpty', 'isFavorite', 'isInTrash',
                      'isMagic', 'colorLabelIndex', 'sortAscending', 'sortKeyPath',
                    ),
                },
              # keyword definitions
              # TODO: keywords form a tree? always presented as flat in GUI
              'keyword':
                { 'table_name': 'RKKeyword',
                  'mixin': Keyword_Mixin,
                  'link_via': {
                    'version': (
                        'modelId',
                        'keywordForVersion', 'keywordId', 'versionId',
                        'version', 'modelId'),
                  },
                  'columns':
                    ( 'modelId', 'uuid', 'name', 'searchName', 'parentId',
                      'hasChildren', 'shortcut',
                    ),
                },
              # albums
              'album':
                { 'table_name': 'RKAlbum',
                  'mixin': Album_Mixin,
                  'columns':
                    ( 'modelId', 'uuid', 'albumType', 'albumSubclass', 'serviceName',
                      'serviceAccountName', 'serviceFullName', 'name', 'folderUuid',
                      'queryFolderUuid', 'posterVersionUuid', 'selectedTrackPathUuid',
                      'sortKeyPath', 'sortAscending', 'customSortAvailable',
                      'versionCount', 'createDate', 'isFavorite', 'isInTrash', 'isHidden',
                      'isMagic', 'publishSyncNeeded', 'colorLabelIndex',
                      'faceSortKeyPath', 'recentUserChangeDate', 'filterData',
                      'queryData', 'viewData', 'selectedVersionIds',
                    ),
                },
              # image versions
              'version':
                { 'table_name': 'RKVersion',
                  'mixin': Version_Mixin,
                  'link_via': {
                    'face': (
                        'modelId',
                        'vface', 'versionId', 'faceKey',
                        'person', 'faceKey'),
                    'keyword': (
                        'modelId',
                        'keywordForVersion', 'versionId', 'keywordId',
                        'keyword', 'modelId'),
                    'detected_face': (
                        'modelId',
                        'vface', 'versionId', 'faceKey',
                        'face', 'faceKey'),
                  },
                  'columns':
                    ( 'modelId', 'uuid', 'name', 'fileName',
                      'versionNumber', 'stackUuid', 'masterUuid',
                      'masterId', 'rawMasterUuid', 'nonRawMasterUuid',
                      'projectUuid', 'imageTimeZoneName', 'imageDate',
                      'mainRating', 'isHidden', 'isFlagged', 'isOriginal',
                      'isEditable', 'colorLabelIndex', 'masterHeight',
                      'masterWidth', 'processedHeight', 'processedWidth',
                      'rotation', 'hasAdjustments', 'hasEnabledAdjustments',
                      'hasNotes', 'createDate', 'exportImageChangeDate',
                      'exportMetadataChangeDate', 'isInTrash',
                      'thumbnailGroup', 'overridePlaceId', 'exifLatitude',
                      'exifLongitude', 'renderVersion', 'adjSeqNum',
                      'supportedStatus', 'videoInPoint', 'videoOutPoint',
                      'videoPosterFramePoint', 'showInLibrary',
                      'editState', 'contentVersion', 'propertiesVersion',
                      'rawVersion', 'faceDetectionIsFromPreview',
                      'faceDetectionRotationFromMaster', 'editListData',
                      'hasKeywords',
                    ),
                },
              # presence of image versions in albums
              'albumForVersion':
                { 'table_name': 'RKAlbumVersion',
                  'columns':
                    ( 'modelId', 'versionId', 'albumId'),
                },
              # association of keywords with image versions
              'keywordForVersion':
                { 'table_name': 'RKKeywordForVersion',
                  'columns':
                    ( 'modelId', 'versionId', 'keywordId'),
                },
            }
          }

if __name__ == '__main__':
  sys.exit(main(sys.argv))
