Mailfiler: another mail filing system.
======================================

Mailfiler is my personal mail filing tool. It will monitor multiple spool mail folders and file messages which appear in them according to per-folder rules and are succinct, readable, robust and flexible.

Rule Syntax
-----------

The rule syntax is detailed in mailfiler_5_pod_, but in short::

  target,... label  condition
                    condition
                    ...

A target can be:

* a mail folder name, such as "python"

* an email address, such as the special mail address my phone consults, or that of another person who should always receive copies of specific messages

* a shell command, such as a command to log receipt of a message or to automatically process its contents; many message header details are presented in as shell environment variables for ready use without further header parsing. For example, I pass certain work related messages to this target::
  "|buglog -n -B dlog \"WORK $shortlist_from->$shortlist_to_cc_bcc: $header_subject\""

The "label", if not ".", is added to the X-Label: header.

The conditions take several forms:

a bare core email address such as bill@example.com:
  matches a message with this core address in the to/cc/bcc header

a header:address pair such as "from:joe@example.com" or "to,cc:bill@exxample.com"
  these match "joe@example.com" in the From: header or "bill@exxample.com" in the to/cc headers

a header:/regexp such as "subject:/^FAIL:"
  matches a Subject: header starting with "FAIL:"

some specialty match syntaxes

Multiple conditions may be supplied; all must match.

Notably, the core address syntax also accepts: "@example.com" to match any address from the "example.com" domain, "UPPERCASE_NAME" to match any address in the group "lowercase_name" in the maildb (see maildb_). These can be combined, such as "(@work.example.com|COLLEAGUES|joe-the-consultant@example.com)".

My Setup
--------

Like others, I run my personal email faily decoupled: I use one tool (getmail, currently) to collect email and deliver to a spool folder, another tool (this one, mailfiler) to monitor that spool and file to other mail folders, a third tool to read and dispatch email (mutt) and my local machine's mail system to actually queue and send the email.

Why not procmail?
-----------------

I used to use procmail; it is popular and does its job.
However, its rule syntax is verbose and sometimes arcane.
Even the implest rule tends to require multiple lines, and I have hundreds of rules.
This drove me to write my now defunct cats2procmailrc tool which took rules like the mailfiler rules and generated a procmailrc.

However, procmail has other problems as well:

It is sloppy.
  All the matching rules are in fact regular expressions.
  While regular expressions are flexible, they are also error prone and hard to write well and robustly.
  And for email addresses, they are awful:
  * the dots in email address are wildcards in regexps, and must be escaped for robustness
  * email addresses come in multiple forms, notably "Bill <bill@example.com>" and the uglier "(Bill) bill@example.com": to reliably match these you need two expressions with difficult address boundary positions
  By constrast, mailfiler does a proper RFC2822 parse of the address and matches against the "core address", "bill@example.com" in the example, with a direct string comparison. So there is no risk of matching "wildbill@example.com" or "bill@example.com.au", and no requirements on a particular form of the address on arrival.

It is slow:
  Procmail is invoked separately for each message to file, and it must read its rules and compile all its regular expressions every time.
  The expressions are applied as encountered, effectively reparsing each message header every time it is tested for a match.
  By contrast, mailfiler parses address headers only once, as requested, and keeps the post-parse data (core address) around for direct access in any future match. The match tests are also largely direct string comparisons, much cheaper than a regexp even discounting the regexp compile cost.

It is hard to use for ad hoc message filing:
  If you can get your message into a separte file, you can test on the command line with "procmail < message". This is not always convenient, especially from inside a mail reader.
  By contrast, to test a mailfiler ruleset I can just uddate the ruleset, copy a sample message into the spool folder using my mail reader's normal message copy function, and watch the logfile to see the actions taken. Once ocrrect, it is similarly easy to bulk refile many messages by dumping them into the spool directory.

.. _mailfiler_5_pod: https://bitbucket.org/cameron_simpson/css/src/tip/man/mailfiler.5.pod
.. _maildb: https://pypi.python.org/pypi/cs.app.maildb
