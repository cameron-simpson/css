#define PY_SSIZE_T_CLEAN
#include <Python.h>

static char module_docstring[] =
    "Buffer scanning code.";

static char scanbuf_docstring[] =
    "Scan buffer with rolling hash, return offsets and new hash.";

static PyObject *scan_scanbuf(PyObject *self, PyObject *args);
static PyObject *scan_scanbuf2(PyObject *self, PyObject *args);

static PyMethodDef module_methods[] = {
    {"scanbuf", scan_scanbuf, METH_VARARGS, scanbuf_docstring},
    {"scanbuf2", scan_scanbuf2, METH_VARARGS, scanbuf_docstring},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module_defn = {
    PyModuleDef_HEAD_INIT,
    "_scan",    /* name of module */
    module_docstring,
    -1,          /* size of per-interpreter state of the module, or -1 if the module keeps state in global variables. */
    module_methods,
};

PyMODINIT_FUNC PyInit__scan(void)
{
    return PyModule_Create(&module_defn);
}

// rolling hash advance function
// shift left 7 bits, append 7 bit value from byte
#define INCREMENT_ROLLING_HASH(hash_value, b) \
    ( ( ( (hash_value) & 0x001fffff ) << 7 ) \
    | ( ( (b) & 0x7f )^( ((b) & 0x80)>>7 ) ) \
    )

// a hash value falling on this expression indicates a boundary
#define IS_MAGIC_HASH_VALUE(hash_value) \
    ( (hash_value) % 4093 == 4091) )

static PyObject *scan_scanbuf(PyObject *self, PyObject *args) {
    unsigned long   hash_value;
    unsigned char   *buf;
#ifdef PY_SSIZE_T_CLEAN
    Py_ssize_t      buflen;
#else
    int             buflen;
#endif

    if (!PyArg_ParseTuple(args, "ky#", &hash_value, &buf, &buflen)) {
        return NULL;
    }

    unsigned long   *offsets = NULL;
    int             noffsets = 0;

    if (buflen > 0) {
        offsets = malloc(buflen * sizeof(unsigned long));
        if (offsets == NULL) {
            return PyErr_NoMemory();
        }
        Py_BEGIN_ALLOW_THREADS
        unsigned long   offset = 0;
        unsigned char   *cp = buf;
        for (; buflen; cp++, buflen--, offset++) {
            unsigned char b = *cp;
            hash_value = INCREMENT_ROLLING_HASH(hash_value, b);
            if (IS_MAGIC_HASH_VALUE(hash_value) {
                offsets[noffsets++] = offset;
            }
        }
        Py_END_ALLOW_THREADS
    }

    /* compose a Python list containing the offsets */
    PyObject        *offset_list = PyList_New(noffsets);
    if (offset_list == NULL) {
        if (offsets != NULL) {
            free(offsets);
        }
        return NULL;
    }
    for (int offset_ndx=0; offset_ndx < noffsets; offset_ndx++) {
        PyObject *py_offset = PyLong_FromUnsignedLong(offsets[offset_ndx]);
        if (py_offset == NULL) {
            Py_DECREF(offset_list);
            if (offsets != NULL) {
                free(offsets);
            }
            return NULL;
        }
        PyList_SET_ITEM(offset_list, offset_ndx, py_offset);
    }

    if (offsets != NULL) {
        free(offsets);
    }

    PyObject    *ret_list = PyList_New(2);
    if (ret_list == NULL) {
        Py_DECREF(offset_list);
        return NULL;
    }
    PyObject    *hash_value_long = PyLong_FromUnsignedLong(hash_value);
    if (hash_value_long == NULL) {
        Py_DECREF(offset_list);
        Py_DECREF(ret_list);
        return NULL;
    }
    PyList_SET_ITEM(ret_list, 0, hash_value_long);
    PyList_SET_ITEM(ret_list, 1, offset_list);

    return ret_list;
}

static PyObject *scan_scanbuf2(PyObject *self, PyObject *args) {
    const unsigned char   *buf;
#ifdef PY_SSIZE_T_CLEAN
    Py_ssize_t      buflen;
#else
    int             buflen;
#endif
    unsigned long   hash_value;
    unsigned long   sofar;
    unsigned long   min_block;
    unsigned long   max_block;

    // Arguments:
    // buf, buflen: a buffer of unsigned char to scan
    // hash_value: unsigned long initial hash value
    // sofar: the length of the initial partial block scanned prior
    //        to this buffer
    // min_block: unsigned long minimum distance between offsets
    // max_block: unsigned long maximum distance between offsets
    if (!PyArg_ParseTuple(args, "y#kkkk",
            &buf, &buflen, &hash_value, &sofar, &min_block, &max_block)
    ) {
        return NULL;
    }

    unsigned long   *offsets = NULL;
    int             noffsets = 0;

    if (buflen > 0) {
        int max_offsets = buflen / min_block + 2;
        offsets = malloc(max_offsets * sizeof(unsigned long));
        if (offsets == NULL) {
            return PyErr_NoMemory();
        }

        Py_BEGIN_ALLOW_THREADS
        unsigned long       block_size = sofar;
        const unsigned char *cp;
        for (cp=buf; buflen; cp++, buflen--, block_size++) {
            unsigned char b = *cp;
            hash_value = INCREMENT_ROLLING_HASH(hash_value, b);
            if (block_size >= min_block) {
                if (block_size >= max_block || IS_MAGIC_HASH_VALUE(hash_value) {
                    offsets[noffsets++] = cp - buf;
                    block_size = 0;
                }
            }
        }
        Py_END_ALLOW_THREADS
    }

    /* compose a Python list containing the offsets */
    PyObject        *offset_list = PyList_New(noffsets);
    if (offset_list == NULL) {
        if (offsets != NULL) {
            free(offsets);
        }
        return NULL;
    }
    for (int offset_ndx=0; offset_ndx < noffsets; offset_ndx++) {
        PyObject *py_offset = PyLong_FromUnsignedLong(offsets[offset_ndx]);
        if (py_offset == NULL) {
            Py_DECREF(offset_list);
            if (offsets != NULL) {
                free(offsets);
            }
            return NULL;
        }
        PyList_SET_ITEM(offset_list, offset_ndx, py_offset);
    }

    if (offsets != NULL) {
        free(offsets);
    }

    PyObject    *ret_list = PyList_New(2);
    if (ret_list == NULL) {
        Py_DECREF(offset_list);
        return NULL;
    }
    PyObject    *hash_value_long = PyLong_FromUnsignedLong(hash_value);
    if (hash_value_long == NULL) {
        Py_DECREF(offset_list);
        Py_DECREF(ret_list);
        return NULL;
    }
    PyList_SET_ITEM(ret_list, 0, hash_value_long);
    PyList_SET_ITEM(ret_list, 1, offset_list);

    return ret_list;
}
