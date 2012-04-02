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

T_HIRES = 0x01      # high resolution
T_MONOTONIC = 0x02  # never goes backwards
T_STEADY = 0x04     # never steps

def get_clock(flags, clocklist=None):
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
    return get_clock(T_MONTONIC|T_HIRES|other_flags, MONOTONIC_CLOCKS) \
        or get_clock(T_MONOTONIC|other_flags, MONOTONIC_CLOCKS)

def steady_clock(other_flags=0):
    return get_clock(T_STEADY|T_HIRES|other_flags, STEADY_CLOCKS) \
        or get_clock(T_STEADY|other_flags, STEADY_CLOCKS)

def hr_clock(other_flags=0):
    return get_clock(T_HIRES|T_STEADY|other_flags, HIRES_CLOCKS) \
        or get_clock(T_HIRES|other_flags, HIRES_CLOCKS)

ClockEntry = namedtuple('ClockEntry', 'flags factory')
ALL_CLOCKS = []

class _UNIXClock(object):
    flags = 0
    @property
    def now(self):
        return time()
_SingleUNIXClock = _UNIXClock()
UNIXClock = lambda: _SingleUNIXClock

class SyntheticMonotonic(object):
    flags = T_MONOTONIC
    def __init__(self):
        self.__last = None
    @property
    def now(self):
        last = self.__last
        t = time()
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
# in order or quality (an arbitrary measure, perhaps).
MONTONIC_CLOCKS = ALL_CLOCKS
HIRES_CLOCKS = ALL_CLOCKS
STEADY_CLOCKS = ALL_CLOCKS
