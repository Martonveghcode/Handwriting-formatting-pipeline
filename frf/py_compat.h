#ifndef FRF_PY_COMPAT_H
#define FRF_PY_COMPAT_H

#if PY_MAJOR_VERSION >= 3
#define PyString_Check PyUnicode_Check
#define PyString_AsString PyUnicode_AsUTF8
#define PyString_FromString PyUnicode_FromString
#define PyString_FromFormat PyUnicode_FromFormat
#define PyString_ConcatAndDel PyUnicode_AppendAndDel
#endif

#endif
