#define PY_SSIZE_T_CLEAN
#include <Python.h>

static char module_docstring[] =
    "Buffer scanning code.";

static char scanbuf_docstring[] =
    "Scan buffer with rolling hash, return offsets and new hash.";

static PyObject *scan_scanbuf(PyObject *self, PyObject *args);

static PyMethodDef module_methods[] = {
    {"scanbuf", scan_scanbuf, METH_VARARGS, scanbuf_docstring},
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
            hash_value = ( ( ( hash_value & 0x001fffff ) << 7
                           )
                         | ( ( b & 0x7f )^( (b & 0x80)>>7 )
                           )
                         );
            if (hash_value % 4093 == 4091) {
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
