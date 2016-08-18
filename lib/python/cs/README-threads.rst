Threading and communication/synchronisation conveniences.
=========================================================

Notably:

* WorkerThreadPool, a pool of worker threads to run functions.

* AdjustableSemaphore, a semaphore whose value may be tuned after instantiation.

* @locked, decorator for object methods which should hold self._lock

* @locked_property, a thread safe caching property
