Compact integer ranges with a set-like API
==========================================

A Range is used to represent integer ranges, such a file offset spans.

Much of the set API is presented to modify and test Ranges, looking somewhat like sets of intergers but extended slightly to accept ranges as well as individual integers so that one may say "R.add(start, end)" and so forth.

Also provided:

* Span, a simple start:end range.

* overlap: return the overlap of two Spans

* spans: return an iterable of Spans for all contiguous sequences in the ordered integers supplied
