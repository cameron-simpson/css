Convenience facilities for managing exceptions.
-----------------------------------------------

Presents:

* return_exc_info: call supplied function with arguments, return either (function_result, None) or (None, exc_info) if an exception was raised.

* @returns_exc_info, a decorator for a function which wraps in it return_exc_info.

* @noexc, a decorator for a function whose exceptions should never escape; instead they are logged. The initial use case was inside logging functions, where I have had a failed logging action abort a program. Obviously this is a decorator which should see very little use.

* @noexc_gen, a decorator for generators with similar effect to @noexc for ordinary functions.
