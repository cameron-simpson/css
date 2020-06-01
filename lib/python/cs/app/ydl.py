#!/usr/bin/env python3

''' Convenient command line and library wrapper for youtube-dl.

    The `youtube-dl` tool and associated `youtube_dl` Python module
    are very useful for downloading media from various websites.
    However, as an end user who almost never streams because of my
    soggy internet link, I find fetching several items is quite serial and
    visually noisy.

    This module provides a command line tool `ydl` which:
    - runs multiple downloads in parallel with progress bars
    - prints the downloaded filename as each completes

    Interactively, I keep this shell function:

        ydl(){
          ( set -ue
            dldir=${DL:-$HOME/dl}/v
            [ -d "$dldir" ] || set-x mkdir "$dldir"
            cd "$dldir"
            command ydl ${1+"$@"}
          )
        }

    which runs the downloader in my preferred download area
    without tedious manual `cd`ing.
'''

from getopt import GetoptError
import logging
import sys
from threading import RLock
from youtube_dl import YoutubeDL
from cs.cmdutils import BaseCommand
from cs.excutils import logexc
from cs.fstags import FSTags
from cs.logutils import warning, LogTime
from cs.pfx import Pfx, pfx_method
from cs.progress import Progress, OverProgress
from cs.result import bg as bg_result, report
from cs.tagset import Tag
from cs.upd import UpdProxy

__version__ = '20200521-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Operating System :: POSIX",
        "Operating System :: Unix",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Topic :: Internet",
        "Topic :: System :: Networking",
        "Topic :: Utilities",
    ],
    'install_requires': [
        'cs.cmdutils',
        'cs.fstags',
        'cs.logutils',
        'cs.result',
        'cs.tagset',
        'cs.upd',
        'youtube_dl',
    ],
    'entry_points': {
        'console_scripts': [
            'ydl = cs.app.ydl:main',
        ],
    },
}

DEFAULT_OUTPUT_FORMAT = 'bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best'
DEFAULT_OUTPUT_FILENAME_TEMPLATE = \
    '%(uploader)s@youtube--%(title)s--%(upload_date)s--%(resolution)s' \
    '--id=%(id)s.%(ext)s'

FSTAGS_PREFIX = 'youtube_dl'

def main(argv=None, cmd=None):
  ''' Main command line.
  '''
  return YDLCommand().run(argv, cmd=cmd)

class YDLCommand(BaseCommand):
  ''' `ydl` command line implementation.
  '''

  GETOPT_SPEC = 'f'
  USAGE_FORMAT = '''Usage: {cmd} [-f] URLs...
    -f  Force download - do not use the cache.'''

  @staticmethod
  def apply_defaults(options):
    ''' Initial defaults options.
    '''
    options.ydl_opts = dict(logger=options.loginfo.logger)

  @staticmethod
  def apply_opts(opts, options):
    ''' Command line main switches.
    '''
    for opt, val in opts:
      if opt == '-f':
        options.ydl_opts.update(cachedir=False)
      else:
        raise RuntimeError("unhandled option: %s=%s" % (opt, val))

  @staticmethod
  def main(argv, options):
    ''' Command line main programme.
    '''
    if not argv:
      raise GetoptError("missing URLs")

    upd = options.loginfo.upd
    with FSTags() as fstags:
      over_ydl = OverYDL(upd=upd, fstags=fstags, ydl_opts=options.ydl_opts)
      over_ydl.queue_iter(argv)
      for R in over_ydl.report():
        upd.nl("COMPLETED R=%s", R)

class OverYDL:
  ''' A manager for multiple `YDL` instances.
  '''

  def __init__(
      self,
      *,
      upd=None,
      fstags=None,
      all_progress=None,
      ydl_opts=None,
  ):
    if all_progress is None:
      all_progress = OverProgress()
    self.upd = upd
    self.proxy0 = upd.proxy(0) if upd else None
    self.fstags = fstags
    self.all_progress = OverProgress()
    self.ydl_opts = ydl_opts
    self.Rs = []
    self.nfetches = 0
    self._lock = RLock()

    @logexc
    def update0():
      nfetches = self.nfetches
      if nfetches == 0:
        self.proxy0("Idle.")
      else:
        self.proxy0(
            self.all_progress.status(
                "upd:%d %d %s" %
                (len(upd), nfetches, "fetch" if nfetches == 1 else "fetches"),
                upd.columns - 1
            )
        )

    self.update0 = update0

  def report(self, Rs=None):
    ''' Wrapper returning `cs.result.report(.Rs)`.
        `Rs` defaults to `list(self.Rs`, the accumulated `Result`s..
    '''
    if Rs is None:
      Rs = list(self.Rs)
    return report(Rs)

  def queue_iter(self, urls):
    ''' Queue the URLs of the iterable `urls`,
        essentially a convenience wrapper for the `queue` method.
        Returns a list of the `Result`s for each queued URL.
    '''
    return list(map(self.queue, urls))

  @pfx_method
  def queue(self, url):
    ''' Queue a fetch of `url` and return a `Result`.
      '''
    with Pfx(url):
      Y = YDL(
          url,
          fstags=self.fstags,
          upd=self.upd,
          tick=self.update0,
          over_progress=self.all_progress,
          **self.ydl_opts,
      )
      R = Y.bg()

      @logexc
      def on_completion(_):
        with self._lock:
          self.nfetches -= 1
        self.update0()

      with self._lock:
        self.Rs.append(R)
        self.nfetches += 1
      self.update0()
      R.notify(on_completion)
      return R

class YDL:
  ''' Manager for a download process.
  '''

  def __init__(
      self,
      url,
      *,
      fstags,
      upd=None,
      tick=None,
      over_progress=None,
      **kw_opts
  ):
    ''' Initialise the manager.

        Parameters:
        * `url`: the URL to download
        * `fstags`: mandatory keyword argument, a `cs.fstags.FSTags` instance
        * `upd`: optional `cs.upd.Upd` instance for progress reporting
        * `tick`: optional callback to indicate state change
        * `over_progress`: an `OverProgress` to which to add each new `Progress` instance
        * `kw_opts`: other keyword arguments are used to initialise
          the options for the underlying `YoutubeDL` instance
    '''
    if tick is None:
      tick = lambda: None
    self.url = url
    self.fstags = fstags
    self.tick = tick
    self.upd = upd
    self.proxy = None
    self.kw_opts = kw_opts
    self.ydl = None
    self.filename = None
    self.over_progress = over_progress
    self.progresses = {}
    self.result = None

  def bg(self):
    ''' Return the `Result` for this download,
        starting the download if necessary.
    '''
    result = self.result
    if result is None:
      result = self.result = bg_result(
          self.run, _name="%s.run(%r)" % (type(self).__name__, self.url)
      )
    return result

  @property
  def output_filename(self):
    ''' The target output filename.
    '''
    ydl = self.ydl
    ie_result = ydl.extract_info(self.url, download=False, process=True)
    return ydl.prepare_filename(ie_result)

  @logexc
  def run(self):
    ''' Run the download.
    '''
    url = self.url
    upd = self.upd
    proxy = self.proxy = upd.insert(1) if upd else UpdProxy(None, None)
    proxy.prefix = url + ' '

    ydl_opts = {
        'progress_hooks': [self.update_progress],
        'format': DEFAULT_OUTPUT_FORMAT,
        'logger': logging.getLogger(),
        'outtmpl': DEFAULT_OUTPUT_FILENAME_TEMPLATE,
        ##'skip_download': True,
        'writeinfojson': False,
        'updatetime': False,
        'cachedir': False,
        'process_info': [self.process_info]
    }
    if self.kw_opts:
      ydl_opts.update(self.kw_opts)
    ydl = self.ydl = YoutubeDL(ydl_opts)

    proxy('...')
    self.tick()

    with LogTime("%s.download(%r)", type(ydl).__name__, url) as LT:
      with ydl:
        ydl.download([url])
    proxy("elapsed %ds, saving metadata ...", LT.elapsed)
    self.tick()

    ie_result = ydl.extract_info(url, download=False, process=True)
    output_path = ydl.prepare_filename(ie_result)
    tagged_path = self.fstags[output_path]
    for key, value in ie_result.items():
      tag_name = FSTAGS_PREFIX + '.' + key
      tagged_path.direct_tags.add(Tag(tag_name, value))
    self.fstags.sync()
    if upd:
      upd.nl(output_path)
      proxy.delete()
    else:
      print(output_path, flush=True)
    return self

  @logexc
  def update_progress(self, ydl_progress):
    ''' Update progress hook called by youtube_dl.

        Updates the relevant status lines.
    '''
    filename = self.filename = ydl_progress['filename']
    progress = self.progresses.get(filename)
    if progress is None:
      progress = self.progresses[filename] = Progress(
          name=self.url + ':' + filename, total=ydl_progress['total_bytes']
      )
      if self.over_progress is not None:
        self.over_progress.add(progress)
    try:
      progress.position = ydl_progress['downloaded_bytes']
    except KeyError:
      pass
    status = progress.status(
        filename if len(filename) <= 24 else '...' + filename[-21:],
        self.proxy.width
    )
    self.proxy(status)
    self.tick()

  @staticmethod
  def process_info(ie_result):
    ''' Process info hook called by youtube_dl, seems uncalled :-(
    '''
    warning("PROCESS INFO: %r", ie_result)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
