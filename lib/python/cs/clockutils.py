#!/usr/bin/python
#
# Framework to present system clocks by feature, intended to avoid
# the library-as-policy pitfalls of the discussion around PEP 418.
#
# My 2c:
# http://www.gossamer-threads.com/lists/python/dev/977474#977474
# http://www.gossamer-threads.com/lists/python/dev/977495#977495
# or:
# http://www.mail-archive.com/python-dev@python.org/msg66174.html
# http://www.mail-archive.com/python-dev@python.org/msg66179.html
#       - Cameron Simpson <cs@zip.com.au> 02apr2012
#

from collections import namedtuple
from time import time

HIRES = 0x01      # high resolution
MONOTONIC = 0x02  # never goes backwards
STEADY = 0x04     # never steps

def get_clock(flags=0, clocklist=None):
    ''' Return a Clock based on the supplied `flags`.
        The returned clock shall have all the requested flags.
        If no clock matches, return None.
    '''
    if clocklist is None:
        clocklist = ALL_CLOCKS
    for clock in clocklist:
        if clock.flags & flags == flags:
            return clock.factory()
    return None

def montonic_clock(other_flags=0):
    ''' Try to return a hires monotonic clock, otherwise any monotonic
        clock.
    '''
    return get_clock(MONTONIC|HIRES|other_flags, MONOTONIC_CLOCKS) \
        or get_clock(MONOTONIC|other_flags, MONOTONIC_CLOCKS)

def steady_clock(other_flags=0):
    return get_clock(STEADY|HIRES|other_flags, STEADY_CLOCKS) \
        or get_clock(STEADY|other_flags, STEADY_CLOCKS)

def hires_clock(other_flags=0):
    return get_clock(HIRES|STEADY|other_flags, HIRES_CLOCKS) \
        or get_clock(HIRES|other_flags, HIRES_CLOCKS)

_global_monotonic = None

def monotonic():
    global _global_monotonic
    if _global_monotonic is None:
        _global_monotonic = monotonic_clock()
        if _global_monotonic is None:
            raise RunTimeError("no monotonic clock available")
    return _global_monotonic.now()

_global_hires = None

def hires():
    global _global_hires
    if _global_hires is None:
        _global_hires = hires()
        if _global_hires is None:
            raise RunTimeError("no hires clock available")
    return _global_hires.now()

_global_steady = None

def steady():
    global _global_steady
    if _global_steady is None:
        _global_steady = steady()
        if _global_steady is None:
            raise RunTimeError("no steady clock available")
    return _global_steady.now()

ClockEntry = namedtuple('ClockEntry', 'flags factory')
ALL_CLOCKS = []

class _UNIXClock(object):
    flags = 0
    def now(self):
        return time()
_SingleUNIXClock = _UNIXClock()
UNIXClock = lambda: _SingleUNIXClock

class SyntheticMonotonic(object):
    flags = MONOTONIC
    def __init__(self, base_clock=None):
        if base_clock is None:
            base_clock = UNIXClock()
        self.__last = None
        self.__base = base_clock
    def now(self):
        last = self.__last
        t = self.__base.now()
        if last is None or last < t:
            self.__last = t
        else:
            t = last
        return t

# a simple synthetic montonic clock
# may skew with respect to other instances
ALL_CLOCKS.append( ClockEntry(SyntheticMonotonic.flags, SyntheticMonotonic) )

# always provide good old time.time()
ALL_CLOCKS.append( ClockEntry(_UNIXClock.flags, UNIXClock) )

# With more clocks, these will be ALL_CLOCKS listed in order of preference
# for these types i.e. MONTONIC_CLOCKS will list only monotonic clocks
# in order of quality (an arbitrary measure, perhaps).
MONTONIC_CLOCKS = ALL_CLOCKS
HIRES_CLOCKS = ALL_CLOCKS
STEADY_CLOCKS = ALL_CLOCKS
