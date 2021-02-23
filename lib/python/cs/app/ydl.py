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
from os.path import splitext
import sys
from threading import RLock
from youtube_dl import YoutubeDL
from youtube_dl.utils import DownloadError
from cs.cmdutils import BaseCommand
from cs.excutils import logexc
from cs.fstags import FSTags
from cs.logutils import error, warning, LogTime
from cs.pfx import Pfx, pfx_method
from cs.progress import Progress, OverProgress
from cs.result import bg as bg_result, report
from cs.tagset import Tag
from cs.upd import Upd, print  # pylint: disable=redefined-builtin

__version__ = '20200621-post'

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
    '%(uploader)s--%(title)s--%(upload_date)s--%(resolution)s' \
    '--%(extractor_key)s--id=%(id)s.%(ext)s'

FSTAGS_PREFIX = 'youtube_dl'

def main(argv=None, cmd=None):
  ''' Main command line.
  '''
  return YDLCommand(argv).run()

class YDLCommand(BaseCommand):
  ''' `ydl` command line implementation.
  '''

  GETOPT_SPEC = 'f'
  USAGE_FORMAT = '''Usage: {cmd} [-f] {{URLs|-}}...
    -f  Force download - do not use the cache.'''

  def apply_defaults(self):
    ''' Initial defaults options.
    '''
    self.options.ydl_opts = dict(logger=self.loginfo.logger)

  def apply_opts(self, opts):
    ''' Command line main switches.
    '''
    options = self.options
    for opt, val in opts:
      if opt == '-f':
        options.ydl_opts.update(cachedir=False)
      else:
        raise RuntimeError("unhandled option: %s=%s" % (opt, val))

  def main(self, argv):
    ''' Command line main programme.
    '''
    if not argv:
      raise GetoptError("missing URLs")
    options = self.options
    with FSTags() as fstags:
      over_ydl = OverYDL(fstags=fstags, ydl_opts=options.ydl_opts)
      for url in argv:
        if url == '-':
          with Pfx('stdin'):
            for lineno, line in enumerate(sys.stdin, 1):
              with Pfx(lineno):
                url = line.rstrip()
                with Pfx("URL %r", url):
                  over_ydl.queue(url)
        else:
          over_ydl.queue(url)
      for _ in over_ydl.report():
        pass

YDLCommand.add_usage_to_docstring()

# pylint: disable=too-many-instance-attributes
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
    if upd is None:
      upd = Upd()
    if all_progress is None:
      all_progress = OverProgress()
    self.upd = upd
    self.proxy0 = upd.insert(0)
    self.fstags = fstags
    self.all_progress = all_progress
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
                "%d %s" % (nfetches, "fetch" if nfetches == 1 else "fetches"),
                upd.columns - 1
            )
        )

    self.update0 = update0
    update0()

  def report(self, Rs=None):
    ''' Wrapper returning `cs.result.report(.Rs)`.
        `Rs` defaults to `list(self.Rs`, the accumulated `Result`s.
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

# pylint: disable=too-many-instance-attributes
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
    if upd is None:
      upd = Upd()
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
    self._warned = set()

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.url)

  def bg(self):
    ''' Return the `Result` for this download,
        starting the download if necessary.
    '''
    result = self.result
    if result is None:
      result = self.result = bg_result(
          self.run,
          _name="%s.run(%r)" % (type(self).__name__, self.url),
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
    proxy = self.proxy = upd.insert(1)
    proxy.prefix = url + ' '

    with proxy:
      try:
        ydl_opts = {
            'progress_hooks': [self.update_progress],
            'format': DEFAULT_OUTPUT_FORMAT,
            'logger': logging.getLogger(),
            'outtmpl': DEFAULT_OUTPUT_FILENAME_TEMPLATE,
            ##'skip_download': True,
            'writeinfojson': False,
            'updatetime': False,
            'process_info': [self.process_info]
        }
        if self.kw_opts:
          ydl_opts.update(self.kw_opts)
        ydl = self.ydl = YoutubeDL(ydl_opts)

        proxy('extract_info...')
        self.tick()
        ie_result = ydl.extract_info(url, download=False, process=True)
        output_path = ydl.prepare_filename(ie_result)
        proxy.prefix = (ie_result.get('title') or output_path) + ' '

        proxy('download...')
        self.tick()
        with LogTime("%s.download(%r)", type(ydl).__name__, url) as LT:
          with ydl:
            ydl.download([url])
        proxy("elapsed %ds, saving metadata ...", LT.elapsed)
        self.tick()

        tagged_path = self.fstags[output_path]
        for key, value in ie_result.items():
          tag_name = FSTAGS_PREFIX + '.' + key
          tagged_path.set(tag_name, value)
        self.fstags.sync()
        print(output_path)
      except DownloadError as e:
        error("download fails: %s", e)

  @logexc
  def update_progress(self, ydl_progress):
    ''' Update progress hook called by youtube_dl.

        Updates the relevant status lines.
    '''
    filename = self.filename = ydl_progress['filename']
    progress = self.progresses.get(filename)
    if progress is None:
      total = ydl_progress.get('total_bytes'
                               ) or ydl_progress.get('total_bytes_estimate')
      if total is None:
        message = 'no total_bytes or total_bytes_estimate in ydl_progress'
        if message not in self._warned:
          warning("%s: %r", message, ydl_progress)
          self._warned.add(message)
        return
      progress = self.progresses[filename] = Progress(
          name=self.url + ':' + filename, total=total
      )
      if self.over_progress is not None:
        self.over_progress.add(progress)
    try:
      progress.position = ydl_progress['downloaded_bytes']
    except KeyError:
      pass
    _, fext = splitext(filename)
    status = progress.status(fext, self.proxy.width)
    self.proxy(status)
    self.tick()

  @staticmethod
  def process_info(ie_result):
    ''' Process info hook called by youtube_dl, seems uncalled :-(
    '''
    warning("PROCESS INFO: %r", ie_result)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
