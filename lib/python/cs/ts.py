#!/usr/bin/env python3
#

''' My personal timesheets related automation.
'''

from getopt import GetoptError
import sys
import time

import arrow
from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.logutils import warning
from cs.pfx import Pfx

def main(argv=None):
  ''' Run the timesheets command line.
  '''
  return TSCommand(argv).run()

MAX_GAP_S = 1200
ROUND_UP = 900

class LogEntry:
  ''' A log entry.
  '''

  def __init__(self, entry, *, unixtime=None):
    if unixtime is None:
      unixtime = time.time()
    self.unixtime = unixtime
    self.entry = entry

  def __str__(self):
    return "%s:%s" % (self.unixtime, self.entry)

class LogSpan:
  ''' A collection of log entries covering a timespan.
  '''

  @typechecked
  def __init__(self, start_unixtime: float):
    self.start_unixtime = start_unixtime
    self.entries = []

  @typechecked
  def append(self, entry: LogEntry):
    ''' Append `entry` the entries for this span.
    '''
    if self.last_unixtime is not None and entry.unixtime < self.last_unixtime:
      warning(
          "LogEntry unixtime:%s < last_unixtime:%s", entry.unixtime,
          self.last_unixtime
      )
    self.entries.append(entry)

  @property
  def last_unixtime(self):
    ''' The UNIX time of the last entry in this span, or `None` if there are no entries.
        Assumes the entries are time ordered.
    '''
    entries = self.entries
    if not entries:
      return None
    return entries[-1].unixtime

  @property
  def elapsed(self):
    ''' The seconds between `self.start_unixtime` and `self.last_unixtime`,
        or `None` if the latter is `None`.
    '''
    last = self.last_unixtime
    if last is None:
      return None
    return last - self.start_unixtime

  @property
  def date(self):
    ''' The `datetime.date` of `self.start_unixtime`.
    '''
    return arrow.get(self.start_unixtime).date()

  @property
  def last_date(self):
    ''' The `datetime.date` if the last entry.
    '''
    return arrow.get(self.last_unixtime).date()

  @classmethod
  def from_entries(cls, entries, threshold=MAX_GAP_S):
    ''' Generator yielding `LogSpan`s from an iterable of `LogEntry` instances.
    '''
    span = None
    last_unixtime = None

    def in_span(entry):
      ''' Test if `entry.unixtime` should be included in the current span
          based on the last UNIX time.
      '''
      return (
          threshold(entry.unixtime, last_unixtime) if callable(threshold) else
          entry.unixtime - last_unixtime <= threshold
      )

    for entry in entries:
      with Pfx(entry):
        if last_unixtime is None:
          # new span
          if span is not None:
            yield span
          span = cls(entry.unixtime)
        else:
          if last_unixtime > entry.unixtime:
            warning("unordered entries< last_unixtime=%s", last_unixtime)
          if not in_span(entry):
            # new span, this entry is too far beyond the previous one
            if span is not None:
              yield span
            span = cls(entry.unixtime)
        span.append(entry)
        last_unixtime = entry.unixtime
    if span is not None:
      yield span

  def report(self, *, indent="", round_up=ROUND_UP, file=None):
    ''' Recite this span as a multiline report.
    '''
    start = arrow.get(self.start_unixtime)
    end = arrow.get(self.last_unixtime)
    elapsed = self.elapsed
    if elapsed > 0:
      hours = round(
          ((elapsed + round_up - 1) // round_up) * round_up / 3600, 2
      )
      print(
          indent + f'{hours} {start.format("hh:mm")}-{end.format("hh:mm")}',
          file=file
      )
    else:
      print(indent + f'{start.format("hh:mm")}-', file=file)
    for entry in self.entries:
      etime = arrow.get(entry.unixtime)
      print(
          indent + etime.format("  hh:mm"),
          "\n    ".join(entry.entry),
          file=file
      )

def scan_loglines(lines, *, start=1, drop_blanks=False):
  ''' Generator to can lines and collate into `LogEntry` instances.
  '''
  entry_unixtime = None
  entry_lines = []
  for lineno, line in enumerate(lines, start):
    with Pfx(lineno):
      line = line.rstrip()
      if drop_blanks and not line.lstrip():
        continue
      words = line.split(None, 2)
      curr = None
      if len(words) > 2:
        when_s = words[0] + ' ' + words[1]
        try:
          when = arrow.get(when_s)
        except arrow.ParserError as e:
          warning(repr(when_s), e)
        else:
          curr = when.timestamp
    # leave the Pfx, it does not play well in a generator
    if curr is None:
      entry_lines.append(line)
    else:
      if entry_lines:
        yield LogEntry(entry_lines, unixtime=entry_unixtime)
      entry_lines = []
      entry_unixtime = curr
    entry_lines.append(line)
  if entry_lines:
    yield LogEntry(entry_lines, unixtime=entry_unixtime)

class TSCommand(BaseCommand):
  ''' Timesheet command line.
  '''

  def cmd_scan(self, argv):
    ''' Usage: {cmd}
          Scan the input as log lines and produce a timesheet summary prototype report.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    with Pfx("stdin"):
      last_span = None
      for span in LogSpan.from_entries(scan_loglines(sys.stdin,
                                                     drop_blanks=True)):
        if last_span is None or last_span.date != span.date:
          print()
          print(span.date)
        span.report(indent="  ")
        last_span = span

if __name__ == '__main__':
  sys.exit(main(sys.argv))
