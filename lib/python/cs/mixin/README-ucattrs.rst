A mixin and a dict subclass where .FOO means ['FOO']
----------------------------------------------------

Presents:

* WithUC_Attrs, a mixin for other classes which provides .__getattr__ and .__setattr__ which intercept .FOO where FOO would match the rexgep ``^[A-Z][_A-Z0-9]*$`` and maps them to ['FOO'].

* UCdict, a subclass of dict using the WithUC_Attrs mixin.
