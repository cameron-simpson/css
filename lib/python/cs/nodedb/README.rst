NodeDB: a collection of native Python objects with backing store
================================================================

A NodeDB is a base class for (currently small) databases of Node objects, native Python objects identified by their .type and .name attributes, and with uppercase attributes.

Node attribute access
---------------------

As far as the backing store goes, each attribute is a sequence of values. Within Python, an attribute may be directly accessed as .FOO, which returns element 0 if the value sequence and requires the sequence to have exactly one element, or as .FOOs or .FOOes (note the lowercase plural suffix) which returns a view of the whole sequence.

The plural forms return a sequence view which itself accepts .FOO or .FOOs attributes. If the values are all Nodes, .FOOs returns a new view with all the .FOO values from each Node, so one may cascade access through a graph of Nodes, example::

  N.LIST_MEMBERs.EMAIL_ADDRESSes

which might return a sequence of email addresses from all the .LIST_MEMBER values from the root Node `N`.

The Node attributes obey both the sequence API and some of the set API: you can .append to or .extend one, or .add to or .update as with a set::

  M = MemberNode("bill")
  N.LIST_MEMBERs.add(M)

Backing Stores
--------------

A NodeDB can be backed by a CSV file (beta quality - I use it myself extensively) or SQL or a DBM file (alpha quality, both need some work). The CSV backend allows multiple clients to share the file; they update by appending to the file and monitor the updates of others.
