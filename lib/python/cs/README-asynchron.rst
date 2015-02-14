Asynchron and friends.
----------------------

An Asynchron is the base class for several callable subclasses which will receive values at a later point in time, and can also be used standalone without subclassing.

A call to an Asynchron will block until the value is received or the Asynchron is cancelled, which will raise an exception in the caller.
An Asynchron may be called by multiple users, before or after the value has been delivered; if the value has been delivered the caller returns with it immediately.
An Asynchron's state may be inspected (pending, running, ready, cancelled).
Callbacks can be registered via an Asychron's .notify method.

An incomplete Asynchron can be told to call a function to compute its value; the function return will be stored as the value unless the function raises an exception, in which case the exception information is recorded instead. If an exception occurred, it will be reraised for any caller of the Asynchron.

Trite example::

  A = Asynchron(name="my demo")

  Thread 1:
    value = A()
    # blocks...
    print(value)
    # prints 3 once Thread 2 (below) assigns to it

  Thread 2:
    A.result = 3

  Thread 3:
    value = A()
    # returns immediately with 3

You can also collect multiple Asynchrons in completion order using the report() function::

  As = [ ... list of Asynchrons or whatever type ... ]
  ...
  for A in report(As):
    x = A()     # collect result, will return immediately
    print(x)    # print result
