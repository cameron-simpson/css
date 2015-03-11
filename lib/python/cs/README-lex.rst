Lexical analysis functions, tokenisers.
=======================================

An assortment of lexcial and tokenisation functions useful for writing recursive descent parsers, of which I have several.

Generally the get_* functions accept a source string and an offset (often optional, default 0) and return a token and the new offset, raising ValueError on failed tokenisation.

* as_lines(chunks, partials=None): parse text chunks, yield complete individual lines

* get_chars(s, offset, gochars): collect adjacent characters from `gochars`

* get_delimited(s, offset, delim): collect text up to the first ocurrence of the character `delim`.

* get_envvar(s, offset=0, environ=None, default=None, specials=None): parse an environment variable reference such as $foo

* get_identifier(s, offset=0, alpha=ascii_letters, number=digits, extras='_'): parse an identifier

* get_nonwhite(s, offset=0): collect nonwhitespace characters

* get_other_chars(s, offset=0, stopchars=None): collect adjacent characters not from `stopchars`

* get_qstr(s, offset=0, q='"', environ=None, default=None, env_specials=None): collect a quoted string, honouring slosh escapes and optionally expanding environment variable references

* get_sloshed_text(s, delim, offset=0, slosh='\\', mapper=slosh_mapper, specials=None): collect some slosh escaped text with optional special tokens (such as '$' introducing '$foo')

* get_tokens(s, offset, getters): collect a sequence of tokens specified in `getters`

* match_tokens(s, offset, getters): wrapper for get_tokens which catches ValueError and returns None instead

* get_uc_identifier(s, offset=0, number=digits, extras='_'): collect an UPPERCASE identifier

* get_white(s, offset=0): collect whitespace characters

* isUC_(s): test if a string looks like an upper case identifier

* htmlify(s,nbsp=False): transcribe text in HTML safe form, using &lt; for "<", etc

* htmlquote(s): transcribe text as HTML quoted string suitable for HTML tag attribute values

* jsquote(s): transcribe text as JSON quoted string; essentially like htmlquote without its htmlify step

* parseUC_sAttr(attr): parse FOO or FOOs (or FOOes) and return (FOO, is_plural)

* slosh_mapper(c, charmap=SLOSH_CHARMAP): return a string to replace \c; the default charmap matches Python slosh escapes

* texthexify(bs, shiftin='[', shiftout=']', whitelist=None): a function like binascii.hexlify but also supporting embedded "printable text" subsequences for compactness and human readbility in the result; initial use case was for transcription of binary data with frequent text, specificly directory entry data

* untexthexify(s, shiftin='[', shiftout=']'): the inverse of texthexify()

* unctrl(s,tabsize=8): transcribe text removing control characters

* unrfc2047(s): accept RFC2047 encoded text as found in mail message headers and decode

