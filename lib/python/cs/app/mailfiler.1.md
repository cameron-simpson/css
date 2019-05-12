# MAILFILER 1

## NAME

mailfiler - a Maildir monitor for filing email messages

## SYNOPSIS

mailfiler monitor [-1] [-d delay] [-n] [-N] [-R rules_pattern] maildirs...

## DESCRIPTION

The mailfiler(1cs) command files email messages in Maildirs
according to simple text rules as described in mailfiler(5cs).

## MODES

### monitor

In the `monitor` mode `mailfiler` watches the specified *maildirs*
for email messages
and files each according to a sequence of rules in the matching rules file
(see mailfiler(5cs)).

The matching rule file is named:

    $HOME/.mailfiler/`*maildir.basename*

where *maildir.basename* is the basename of the named Maildir.

Each Maildir is consulted in turn, so filings which deliver messages
into Maildirs named later on the command line will cause the filed
message to be processed in this run when that later Maildir is
reached.

The following options are available in monitor mode:

`-1`: file at most 1 message per Maildir.

`-d` *delay*:
  delay *delay* seconds between runs.
  The default is to make only one pass over the Maildirs.

`-n`: no remove.
  File messages but do not remove successfully filed messages
  from the source Maildir.

`-R` *rules_pattern*
  Specify the rules file pattern used to specify rules files from Maildir names.
  Default: `$HOME/.mailfiler/`*maildir.basename*

## EXAMPLE

My standard invocation of mailfiler is this:

    mailfiler monitor -d 1 ~/mail/spool ~/mail/spool-in ~/mail/spool-out ~/mail/spool-xref

My mail collection delivers email to my `spool` folder.
The rules there divert probable spam and then pass what remains to `spool-in`
where my main rules live, which can presume a message is not spam.

My mailer's message send facility takes a copy to `spool-out`, whose rules say:

    < env
    out,me,$PHONE,spool-xref,"| cs-aliases-add-email sent" . .

This copies the message to `out` as a record,
to my primary inbox `me` for later reference,
to `$PHONE` (an email address defined in `env`) which is consulted by my phone,
and pipes a copy to my `cs-aliases-add-email`
to learn the address for whitelisting purposes.

## SEE ALSO

mailfiler(5cs), procmail(1)

## AUTHOR

Cameron Simpson <cs@cskk.id.au>

