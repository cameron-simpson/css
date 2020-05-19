#!/usr/bin/env python3

''' Convenience wrapper for youtube-dl.
    - Cameron Simpson <cs@cskk.id.au> 25apr2020
'''

from getopt import GetoptError
import logging
import sys
import time
from youtube_dl import YoutubeDL
from cs.cmdutils import BaseCommand
from cs.logutils import warning
from cs.pfx import Pfx
from cs.progress import Progress, OverProgress
from cs.result import bg as bg_result, report

DEFAULT_OUTPUT_FORMAT = 'bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best'
DEFAULT_OUTPUT_FILENAME_TEMPLATE = '%(title)s--%(uploader)s@youtube--%(upload_date)s--%(resolution)s--id=%(id)s.%(ext)s'

def main(argv=None, cmd=None):
  ''' Main command line.
  '''
  return YDLCommand().run(argv, cmd=cmd)

class YDLCommand(BaseCommand):
  ''' Command line implementation.
  '''

  USAGE_FORMAT = '''Usage: {cmd} URLs...'''

  @staticmethod
  def main(argv, options):
    ''' Command line main programme.
    '''
    if not argv:
      raise GetoptError("missing URLs")

    upd = options.loginfo.upd
    proxy0 = upd.proxy(0) if upd else None
    all_progress = OverProgress()
    nfetches = 0

    def update0():
      if nfetches == 0:
        proxy0("Idle.")
      else:
        proxy0(
            all_progress.status(
                "%d %s" % (nfetches, "fetch" if nfetches == 1 else "fetches"),
                upd.columns - 1
            )
        )

    Rs = []
    for url in argv:
      with Pfx(url):
        Y = YDL(url, upd=options.loginfo.upd, tick=update0)
        all_progress.add(Y.progress)
        Rs.append(Y.bg())
        nfetches += 1
        update0()
    for R in report(Rs):
      with Pfx(R.name):
        nfetches -= 1
        update0()
        ydl = R()
    time.sleep(4)
    warning(dir(ydl.ydl))
    warning(repr(ydl.ydl._ies))

class YDL:
  ''' Manager for a download process.
  '''

  def __init__(self, url, *, upd=None, tick=None, **kw_opts):
    ydl_opts = {
        'progress_hooks': [self.update_progress],
        'format': DEFAULT_OUTPUT_FORMAT,
        'logger': logging.getLogger(),
        'outtmpl': DEFAULT_OUTPUT_FILENAME_TEMPLATE,
        ##'skip_download': True,
        'writeinfojson': True,
        'cachedir': False,
        'process_info': [self.process_info]
    }
    if tick is None:
      tick = lambda: None
    if kw_opts:
      ydl_opts.update(kw_opts)
    self.url = url
    self.tick = tick
    self.upd = upd
    self.ydl_opts = ydl_opts
    self.ydl = YoutubeDL(ydl_opts)
    self.proxy = None
    self.proxy = upd.insert(1) if upd else None
    self.result = None
    self.progress = Progress(name=url)
    if self.proxy:
      self.proxy(url + ':')

  def bg(self):
    ''' Return the `Result` for this download,
        starting the download if necessary.
    '''
    result = self.result
    if result is None:
      result = self.result = bg_result(self.run, _name=self.url)
    return result

  def run(self):
    ''' Run the download.
    '''
    progress = self.progress
    proxy = self.proxy
    url = self.url

    if proxy:
      proxy(url + ' ...')

    with self.ydl:
      self.ydl.download([url])
    if proxy:
      proxy(
          "%s complete: %d bytes in %ds", url, progress.total,
          progress.elapsed_time
      )
    self.tick()
    return self

  def update_progress(self, ydl_progress):
    ''' Update progress hook called by youtube_dl.

        Updates the relevant status lines.
    '''
    progress = self.progress
    proxy = self.proxy
    url = self.url
    upd = self.upd
    progress.total = ydl_progress['total_bytes']
    progress.position = ydl_progress['downloaded_bytes']
    if proxy:
      proxy(
          progress.status(
              url + ': ' + ydl_progress['filename'][:24], upd.columns - 1
          )
      )
    self.tick()

  @staticmethod
  def process_info(ie_result):
    ''' Process info hook called by youtube_dl, seems uncalled :-(
    '''
    warning("PROCESS INFO: %r", ie_result)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
