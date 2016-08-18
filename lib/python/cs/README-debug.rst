Assorted debugging facilities.
==============================

* Lock, RLock, Thread: wrappers for threading facilties; simply import from here instead of there

* thread_dump, stack_dump: dump thread and stack state

* @DEBUG: decorator to wrap functions in timing and value debuggers

* @trace: decorator to report call and return from functions

* @trace_caller: decorator to report caller of function

* TracingObject: subclass of cs.obj.Proxy that reports attribute use
