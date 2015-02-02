Queue subclasses and ducktypes.
-------------------------------

Presents:

* IterableQueue and IterablePriorityQueue, Queues with the iterator protocol instead of .get(). Prospective users should also consider iter(Queue.get,sentinel), which plainly I did not.

* Channel, a zero storage message passing object.

* NullQueue, a .puttable object that discards its inputs.

* TimerQueue, a queue for submtting jobs to run at specific times without creating many Timer threads.

* PushQueue, a Queue ducktype which queues a function on .put, whose iterable result is put onto an output Queue.
