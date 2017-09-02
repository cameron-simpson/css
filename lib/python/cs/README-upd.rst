Single line status updates.
===========================

* Upd: a class accepting update strings which emits minimal text to update a progress line.

-- out(s): update the line to show the string `s`

-- nl(s): flush the output line, write `s` with a newline, restore the status line

-- without(func,...): flush the output line, call func, restore the status line

This is available as an output mode in cs.logutils.
