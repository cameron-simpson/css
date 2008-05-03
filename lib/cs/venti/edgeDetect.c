#include <Python.h>
#include <stdlib.h>
#include <assert.h>

#define RHASH_LEN 4

/*
 * Struct to hold a vocabulary word.
 * When we recognise the hash for the tail of the word, if the preceeding
 * buffer content matches the word it is considered a hit.
 */
typedef struct vocab {
  char     *word;          /* match string */
  int      offset;         /* offset of edge point from start of word */
  int      hash;           /* hash code for tail of string */
} vocab;

/*
 * Structure for a rolling hash state.
 * It is a PyObject because we need to hand it back to the outer
 * Python program.
 * It MUST NOT be realloced() !!
 */
typedef struct rhash {
  PyObject_HEAD
  int  hash;
  int  offset;
  int  bufsize;
  char *buf;
  vocab **words;         /* vocabulary */
  char _buf[RHASH_LEN];
} rolling_hash;
staticforward PyTypeObject rhash_type;

static void rhash_disposevocab(vocab *vwords[]);

/*
 * Create a new rolling hash struct.
 * Release with rhash_dispose().
 */
static rolling_hash *
rhash_create()
{
  rolling_hash *rhp
    = PyObject_NEW( rolling_hash, &rhash_type );
  /* FIXME: check for out of memory, raise exception? */

  rhp->hash=0;
  rhp->offset=0;
  rhp->buf=rhp->_buf;
  rhp->bufsize=sizeof(rhp->_buf);
  rhp->words=(vocab **)NULL;

  return rhp;
}

/*
 * Release the storage associated with a rolling hash.
 */
static void
rhash_dispose(rolling_hash *rhp)
{
  if (rhp->buf != rhp->_buf) {
    free(rhp->buf);
  }
  if (rhp->words) {
    rhash_disposevocab(rhp->words);
  }
  PyObject_DEL(rhp);
}

/*
 * Reset the state of the rolling hash.
 */
static void
rhash_reset(rolling_hash *rhp)
{
  rhp->offset=0;
  rhp->hash=0;
  int bufsize=RHASH_LEN;
  if (rhp->words) {
    vocab **vpp;
    for (vpp=rhp->words; *vpp; vpp++) {
      int wlen = strlen((*vpp)->word);
      if (wlen > bufsize) {
        bufsize=wlen;
      }
    }
  }
  rhp->bufsize=bufsize;
  if (bufsize == RHASH_LEN) {
    rhp->buf=rhp->_buf;
  } else {
    if (rhp->buf != rhp->_buf) {
      free(rhp->buf);
    }
    rhp->buf = calloc(1, bufsize);
  }
  /* FIXME: check calloc() return */
}

/*
 * Dispose the current voabculary, if any.
 * FIXME: shouldn't dispose here, but where else?
 * Set the new vocabulary.
 * Reset the hash state.
 */
static void
rhash_setvocab(rolling_hash *rhp, vocab *vwords[])
{
  if (rhp->words) {
    rhash_disposevocab(rhp->words);
  }
  rhp->words=vwords;
  rhash_reset(rhp);
}

int
rhash_checktail(rolling_hash *rhp, char *s)
{
  int len=strlen(s);
  assert(len <= rhp->bufsize);

  int bufsize=rhp->bufsize;
  int off=rhp->offset-len;
  if (off < 0)
    off+=bufsize;

  while (*s) {
    if (*s != rhp->buf[off])
      return 0;
    s++;
    off++;
    if (off >= bufsize)
      off-=bufsize;
  }
  return 0;
}

/*
 * Take a Python vocabulary tuple and make a (vocab *) array.
 */
static vocab **
rhash_makevocab(PyObject *args)
{
  PyObject *vseq;
  if (!PyArg_ParseTuple(args,"O",&vseq)) {
    /* FIXME: report parsing failure */
    return (vocab **)NULL;
  }
  vseq=PySequence_Fast(vseq, "vocabulary argument must be iterable");
  if (!vseq)
    return;

  int nwords = PySequence_Fast_GET_SIZE(vseq);
  vocab **vwords = malloc((nwords+1) * sizeof(vocab));
  if (!vwords) {
    Py_DECREF(vseq);
    return (vocab **)NULL;
  }

  int i;
  vocab **vpp=vwords;
  for (i=0; i<nwords; i++) {
    vocab *vp = *vpp++;
    PyObject *vwordTuple = PySequence_Fast_GET_ITEM(vseq, i);
    if (!PyArg_ParseTuple(vwordTuple, "si", &vp->word, &vp->offset)) {
      /* FIXME: complain about errors, raise exception? */
      vpp--;
      continue;
    }
    if (vp->subvocab) {
      /* we'll be keeping this */
      Py_INCREF(vp->subvocab);
    }
  }
  *vpp=(vocab *)NULL;

  return vwords;
}

/*
 * Release the storage associated with a vocabulary.
 */
static void
rhash_disposevocab(vocab *vwords[])
{
  vocab **vpp;
  for (vpp=vwords; *vpp; vpp++) {
    vocab *vp = *vpp;
    free(vp->word);     /* FIXME: is this correct disposal? */
    if (vp->subvocab) {
      Py_DECREF(vp->subvocab);
    }
  }
  free(vwords);
}

statichere PyTypeObject rhash_type = {
  PyObject_HEAD_INIT(0)
  0,                    /* ob_size */
  "rolling_hash",       /* tp_name */
  sizeof(rolling_hash), /* tp_basicsize */
  0,                    /* tp_itemsize */
  /* methods */
  (destructor)rhash_dispose, /* tp_dealloc */
  NULL, /* end of methods */
};

/*
 * Add a character to the rolling hash state, return the new hash value.
 */
static int
rollingHash(rolling_hash *rhp, const char *cp)
{
  int offset = rhp->offset;
  int rh = rhp->hash;
  char *buf = rhp->buf;
  
  int ch;
  ch = buf[offset];
  rh -= ((ch&0x0f)<<4) + ((ch&0xf0)>>4);
  ch = *cp;
  rh += ((ch&0x0f)<<4) + ((ch&0xf0)>>4);

  rhp->hash=rh;
  buf[offset++]=ch;
  if (offset >= RHASH_LEN) {
    offset=0;
  }
  rhp->offset=offset;

  return rh;
}


static PyObject *
rhash(PyObject *self)
{
  return (PyObject *)rhash_create();
}

static int
findEdgeNative(rolling_hash *rhp, const char *s, int offset, int pendlen, int minblock, int maxblock)
{
  assert(offset >= 0);
  assert(offset <= strlen(s));
  assert(minblock > 0);
  assert(minblock < maxblock);

  int len=pendlen+offset;
  const char *cp;
  int h;
  for (cp=s+offset; *cp; cp++) {
    h=rollingHash(rhp,cp);
    len++;
    offset++;
    if (len < minblock)
      /* too early */
      continue;
    if (len >= maxblock)
      /* too big - crop now */
      return offset;
    if (h == 511 && offset%8 == 0)
      /* normal naive cut point */
      return offset;
    if (!rhp->words)
      /* no special vocab */
      continue;
    vocab **vpp;
    for (vpp=rhp->words; *vpp; vpp++) {
      if (h == (*vpp)->hash && rhash_checktail(rhp, (*vpp)->word))
        /* adjust offset to match desired point in matched word */
        offset += (*vpp)->offset-strlen((*vpp)->word);
        return offset;
    }
  }
}

/*
 * Locate next edge in the supplied (rhp, s, offset, pendlen, minblock, maxblock).
 * rhp: rolling hash context object.
 * s: new str to consider.
 * offset: start point for consideration.
 * pendlen: length of buffer preceeding s.
 * minblock: minimum value for pendlen+edge.
 * maxblock: maximum value for pendlen+edge.
 * Return edge offset, or 0.
 */
static PyObject *
findEdge(PyObject *self, PyObject *args)
{
  rolling_hash *rhp;
  const char   *s;
  int          offset;
  int          pendlen;
  int          minblock;
  int          maxblock;
  int          edge;

  if (!PyArg_ParseTuple(args, "Osiiii", &rhp, &s, &offset, &pendlen, &minblock, &maxblock)) {
    /* FIXME: bad args? */
    return 0;
  }
  
  edge = findEdgeNative(rhp, s, offset, pendlen, minblock, maxblock);

  return PyInt_FromLong((long)edge);
}
