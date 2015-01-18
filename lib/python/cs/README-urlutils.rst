URL facilities.
===============

* NetrcHTTPPasswordMgr: a subclass of HTTPPasswordMgrWithDefaultRealm that consults the .netrc file if no overriding credentials have been stored.

* URL: factory accepting a URL string returning a str subclass instance with methods and properties to access properties of the URL

-- .NODE: fetch and parse content, return the sole node named "NODE"; example: .TITLE

-- .NODEs: fetch and parse content, return all the nodes named "NODE"; example: .Ps

-- .basename: the basename of the URL path

-- .baseurl: the base URL for this document

-- .content: the content of the document

-- .content_type: the URL Content-Type

-- .dirname: the dirname of the URL path

-- .domain: the hostname part with the first component stripped

-- .feedparsed: a parse of the content via the feedparser module

-- .find_all(): call BeautifulSoup's find_all on the parsed content

-- .flush: forget all cached content

-- .fragment: URL fragment as returned by urlparse.urlparse

-- .hostname: the hostname part

-- .hrefs(self, absolute=False): return all URLs cited as href= attributes

-- .netloc: URL netloc as returned by urlparse.urlparse

-- .page_title: the page title, possibly the empty string

-- .params: URL params as returned by urlparse.urlparse

-- .parent: parent URL, the .dirname resolved

-- .parsed: URL content parsed as HTML by BeautifulSoup

-- .parts: URL parsed into parts by urlparse.urlparse

-- .password: URL password as returned by urlparse.urlparse

-- .path: URL path as returned by urlparse.urlparse

-- .path_elements: the non-empty path components

-- .port: URL port as returned by urlparse.urlparse

-- .query: URL query as returned by urlparse.urlparse

-- .scheme: URL scheme as returned by urlparse.urlparse

-- .srcs: return all URLs cited as src= attributes

-- .username: URL username as returned by urlparse.urlparse

-- .xml: content parsed and return as an ElementTree.XML

-- .xml_find_all(self, match): convenience method to call ElementTree.XML's .findall() method

