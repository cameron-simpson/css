MailDB: NodeDB subclass for storing email information.
======================================================

A MailDB is a subclass of the NodeDB from cs.nodedb; I use it to manage my mail address database and groups (from while mail aliases are generated and which mail filing rules consult).

It comes with a script named "maildb" for editing the database and performing various routine tasks such as learning all the addresses from a mail message or emitting mail alias definitions, particularly for mutt.

A MailDB knows about an assortment of Node types and has Node subclasses for these with convenience methods for suitable tasks; creating a Node with a the type "ADDRESS", for example, instantiates an AddressNode.
