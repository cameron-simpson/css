# MAILFILER 5

## NAME

mailfiler - rules file format

## DESCRIPTION

The mailfiler(1cs) command's monitor watches Maildirs for newly
arrived messages and files them according to a sequence of rules.

## SYNTAX

### Comments

Blank lines and lines commencing with an octothorpe ('#') are ignored.

### Include Files

A line commencing with a less-than symbol ('<') is an "include" directive.
The following word is the name of a file whose contents are read as though they
appeared at this point in the rules file.
If the filename is relative, it is resolved with respect to the
directory part of the filename of the current rules file.

### Filing Rules

Other lines are mail filing rules, of the general form:

    [=][!]targets tag [!]condition

A line commencing with whitespace is considered to be an additional
condition to be ANDed with the preceeding line's rule conditions:

    [=][!]targets tag condition1
                      condition2
                      condition3

#### Delivery Flags

The following indicators may preceed, in order, the targets:

`=`: an equals symbol ('=') indicates that rule processing should
cease at this rule if it matches.
Normally all matching rules are applied.

`!`: an exclaimation symbol ('!') indicates that an alert should be
emitted at the end of filing if this rule was matched.

#### Targets

The *targets* is a comma separated list of actions to perform with a message.
Syntacticly, a target takes the form of a sequence of nonwhitespace non-comma
characters, or a string value in double quotes.
Note that a shell command in quotes will need any backslashes doubled,
as the quoted string parse strips one level of backslash escaping.

Targets come in two flavours, deliverable targets and action targets.
Deliverable targets are those which deliver a message to some location
such as a mail folder, email address or a shell pipeline.
Action targets affect the filer status or the message itself,
such as a variable assignment or a message header modification.

The action targets are applied as they are encountered in matching rules.
The deliverable targets are accrued for delivery at the end of the rules.

If no deliverable targets have been accrued then the default targets,
specified by the `$DEFAULT` environment variable, are used.
There is no default for `$DEFAULT`;
if it is not specified the message is not considered to be successfully delivered.
Most rules files commence with a definition of the default delivery target, for example:

    DEFAULT=unmatched

For folders whose rules are exceptions
i.e. they specify messages to be filed elsewhere,
while nonmatching messages remain in the folder, use:

    DEFAULT=.

The message must be successfully handed to all targets of all matching
rules for the filter run to be considered successfully filtered.
If all delivery attempts succeed, the original message is removed from the Maildir.
Otherwise the message remains in the Maildir and is ignored on subsequent polls.

The variable target types are listed below in parse order.

##### Variable Assignment

A target of the form:

    VARIABLE=value

or

    VARIABLE="quoted-value"

assigns the value to the named environment variable.
If unquoted, *value* consists of nonwhitespace characters excluding the comma.
If quoted, *quoted-value* is a slosh escaped string.
Note that the value is subject to parameter substitution and the
associated slosh escaping and therefore sloshes needed for that
latter phase will need doubling in the `"`*quoted-value*`"` form.

Some environment variables have special meaning
as described in the ENVIRONMENT section below.

##### Header Substitution

A target of the form:

    [header[,header...]:]s/this/that/

performs a regular expression match on the specified headers
(by default the "Subject:" header)
and replaces the match with the second string, subject to parameter substitution.
The available parameter names consist of:

The existing header values:
The parameter name is the header name in lowercase
with dash ('-') replaced by underscore ('_'); example: "message_id".
The parameter value is the last header value
with CR and LF stripped per RFC2822 part 2.2.3.

Named subexpressions from the regular expression:
These names and values override any values from the existing header values.

Numeric subexpressions:
`$0` expands to the whole matched expression,
and `$1` and so on to bracketed subexpressions from left to right.

##### Functions on Headers

A target of the form:

    header[,header...]:function[(arg,...)]

applies the named *function* to each instance of the named headers.
If there are no *arg*s then the parentheses may be omitted.

The *function* may be either a dotted name
of the form *module_name*`.`*function_name*
or a simple *function_name*.

With the former form, the Python module named *module_name*
is imported and the name *function_name* obtained from it.

For convenience, each *arg* may take the following forms:

*NAME*:
An uppercase identifier such as a group name, passed in as a string.

`@`*domain*:
An at symbol ("@") followed by a domain name, passed in as a string.

*number*:
A nonnegative integer, passed in as an integer.

`"`*quoted-value*`"`:
A quoted value subject to parameter substitution.
The value after substitution is passed in as a string.

The function obtained is called as follows:

    function_name(filer, header_names, *args)

where *filer* is the internal MessageFiler instance
associated with this message,
*header_names* and *args* the list of headers
and the *args* supplied with the target respectively.

The following simple function names are supported:

##### Message Flags

A target consisting a single capital letter specifies a flag to be
applied to the message.

Supported flags are:
`D`: Draft,
`F`: Flagged,
`P`: Passed,
`R`: Replied,
`S`: Seen,
`T`: Trashed.

##### Deliver to Program

A target commencing with a pipe symbol ('|') is considered a command
to run with the message text on its standard input.
If the command exits with a zero exit status is it considered to have run successfully.

##### Deliver to Email Address

Otherwise, a target containing an "at" symbol ('@') is considered
an email address to which to send a copy of the message.
If the mail system accepts the message it is considered to have been dispatched successfully.

To avoid avoid blowback to the original author or source of the message
the `Sender:`, `Return-Path:` and `Errors-To:` headers
are set to the value of the environment variable `$EMAIL`,
which is expected to be a bare *localpart*`@`*domain* address.
If `$EMAIL` is not set then the message is not sent
and delivery is not considered successful.

To avoid spurious delivery loop detection, the `Delivered-To:` header is cleared.

##### Deliver to Mail Folder

Otherwise, a target is considered to indicate a Maildir or a UNIX mbox
into which the message should be placed.

#### Conditions

A condition may be preceeded by an exclaimation symbol ('!') to invert its meaning.
Example:

    !from:cs@cskk.id.au

matches a message not from "cs@cskk.id.au".

Conditions are tested against specific message headers.
Unless specified, these headers are the 'To:', 'CC:' and 'BCC:' headers.
Example:

    cs@cskk.id.au

matches a message addressed to "cs@cskk.id.au".

A condition may be preceeded by a comma separated list of headers.
Example:

    to,from:cs@cskk.id.au

matches a message from "cs@cskk.id.au" or addressed to "cs@cskk.id.au" in the 'To:' header.

A header may be matched against any of multiple addresses by grouping the alternatives in brackets, example:

    to,cc:(python-list@python.org|@example.com|ME|THEM)

which tests all the addresses in the headers 'To:' or 'CC:' against:
the address `python-list@python.org`,
the mail domain `@example.com`,
either of the mail address groups `me` or `them`.
The mail address groups come from the maildb(1cs), if any.

A header may be tested against a regular expression instead of by email address by preceding it with a slash ('/'), example:

    subject:/icewm-Bugs

matches a message with the string `icewm-Bugs` in the `Subject:` header.

A header may be tested against a variety of special purpose functions
accepting a double quoted string.
Example:

    list-id.contains("<squid-users.squid-cache.org>")

matches a message with the string `<squid-users.squid-cache.org>`
in the `List-ID:` header.

The list of available functions is as follows: `contains`. (More to come.)

## ENVIRONMENT

`ALERT`, the executable path of the command to issue alerts, by default `alert`.

`ALERT_FORMAT`, the format of alert strings.
This is a Python format string, by default:
`MAILFILER: `*short_from*`->`*short_recipients*`: `*subject*.

`ALERT_TARGETS`, additional implied targets to be used if an alert was issued.

`DEFAULT`, default delivery targets
if no rule with a delivery target has been matched.
See the Targets section above.
Note that this is considered *after* any `$ALERT_TARGETS` have been added.

## CAVEATS

### No Loop Detection

There is no loop detection for folders.
It is possible to file from folder A to folder B
and have a rule for folder B which in turn files to A;
this will process the message indefinitely (once per pass).

However, a message which fails to file is noted and never reprocessed.
This allows the user to fix the rules and then refile the message by hand,
for example by using mutt to save the offending message to the same folder.
The refiled message will be seen as new and processed anew.

Also, it is possible to leave messages in the source folder by specifying a target of `.`
to indicate the current folder;
in this case the message is not considered on subsequent passes, avoiding a loop.
The conventional arrange for this it to use:

    DEFAULT=.

at the start of the rules for such a folder.

### Assignments Are Rule Targets

Do not forget that assignments are *targets*.
Even the author has been bitten by writing:

    ALERT_TARGETS=F,spool-to-phone

in his environment rules.
This is *two* targets: "ALTER_TARGETS=F" and "spool-to-phone".
As a consequence, all the rule files had an unconditional filing to the "spool-to-phone" folder, including that folder filing to itself.
The correct incantation is:

    ALERT_TARGETS="F,spool-to-phone"

For completeness the author also added:

    ALERT_TARGETS=""

after the environment load in his "spool-to-phone" folder rules, which now read:

    < env
    ALERT_TARGETS=""
    $PHONE . .

## SEE ALSO

mailfiler(1cs)

## AUTHOR

Cameron Simpson <cs@cskk.id.au>

