Functions and classes to work with email.
=========================================

* Maildir: a much faster subclass of mailbox.Maildir, because you really can trust the output of os.listdir to scan for changes

* Message(msgfile, headersonly=False): factory function to accept a file or filename and return an email.message.Message

* ismaildir(path): test if `path` refers to a Maildir

* ismbox(path): test if `path` refers to an mbox file

* ismhdir(path): test if `path` refers to an MH mail folder

* make_maildir(path): create a new Maildir

* message_addresses(M, header_names): generator that yields (realname, address) pairs from all the named headers

* modify_header(M, hdr, new_value, always=False): modify a Message `M` to change the value of the named header `hdr` to the new value `new_value`

* shortpath(path, environ=None): use cs.lex.shortpath to return a short refernce to a mail folder using $MAILDIR for "+" and $HOME for "~"
