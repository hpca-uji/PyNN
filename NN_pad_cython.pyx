"""
Padding definition for Python Distributed Training of Neural Networks (PyDTNN)

PyDTNN is a light-weight library for distributed Deep Learning training and
inference that offers an initial starting point for interaction with distributed
training of (and inference with) deep neural networks. PyDTNN prioritizes
simplicity over efficiency, providing an amiable user interface which enables a
flat accessing curve. To perform the training and inference processes, PyDTNN
exploits distributed inter-process parallelism (via MPI) for clusters and
intra-process (via multi-threading) parallelism to leverage the presence of
multicore processors and GPUs at node level. For that, PyDTNN uses MPI4Py for
message-passing, BLAS calls via NumPy for multicore processors and
PyCUDA+cuDNN+cuBLAS for NVIDIA GPUs.

Copyright 2021 Universitat Jaume I

This file is part of PyDTNN. PyDTNN is free software: you can redistribute it
and/or modify it under the terms of the GNU General Public License as published
by the Free Software Foundation, either version 3 of the License, or (at your
option) any later version.

PyDTNN is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details. You
should have received a copy of the GNU General Public License along with this
program. If not, see <http://www.gnu.org/licenses/>.
"""

__author__ = "Manuel F. Dolz, Enrique S. Quintana, Sergio Barrachina, Mar Catalán, Adrián Castelló"
__contact__ = "dolzm@uji.es"
__copyright__ = "Copyright 2021, Universitat Jaume I"
__credits__ = ["Manuel F. Dolz, Enrique S. Quintana", "Sergio Barrachina", "Mar Catalán", "Adrián Castelló"]
__date__ = "2020/03/22"

__email__ = "dolzm@uji.es"
__license__ = "GPLv3"
__maintainer__ = "Manuel F. Dolz"
__status__ = "Production"
__version__ = "1.1.0"

import inspect

import numpy as np
cimport numpy as np
cimport cython
from libc.string cimport memcpy
from cython.parallel import prange

def _check_and_get_values(matrix_in, matrix_out):
    h = matrix_in.shape[2]
    w = matrix_in.shape[3]
    new_h = matrix_out.shape[2]
    new_w = matrix_out.shape[3]
    vpadding = (new_h - h) // 2
    hpadding = (new_w - w) // 2
    called_from = inspect.stack()[1][3]
    if new_h == h and new_w == w:
        raise ValueError("'{}' should not be called if there is nothing to pad".format(called_from))
    if h + 2 * vpadding != new_h or w + 2 * hpadding != new_w:
        raise ValueError("The given padding is not supported by '{}'".format(called_from))
    return h, w, new_h, new_w, vpadding, hpadding

def pad_cython(matrix_in, matrix_out):
    """
    DON'T USE THIS!

    Sometimes is slower than the numpy alternatives!

    Implements a parallel version of:
        matrix_out = np.pad(matrix_in,
                            ((0, 0), (0, 0),
                            (vpadding, vpadding), (hpadding, hpadding)),
                            mode='constant')
    where vpadding and hpadding are obtained from the difference of the matrix_in
    and matrix_out dimensions.
    """
    h, w, new_h, new_w, vpadding, hpadding = _check_and_get_values(matrix_in, matrix_out)
    if matrix_in.dtype == np.float32:
        pad_cython_float32(h, w, new_h, new_w, vpadding, hpadding, matrix_in, matrix_out)
    else:
        raise ValueError("Type '{}' not supported for pad_cython".format(matrix_in.dtype))

def transpose_1023_and_pad_cython(matrix_in, matrix_out):
    """
    Implements a parallel version of:
        matrix_out = np.pad(matrix_in.transpose((1, 0, 2, 3)),
                            ((0, 0), (0, 0),
                            (vpadding, vpadding), (hpadding, hpadding)),
                            mode='constant')
    where vpadding and hpadding are obtained from the difference of the matrix_in
    and matrix_out dimensions.
    """
    h, w, new_h, new_w, vpadding, hpadding = _check_and_get_values(matrix_in, matrix_out)
    if matrix_in.dtype == np.float32:
        transpose_1023_and_pad_cython_float32(h, w, new_h, new_w, vpadding, hpadding, matrix_in, matrix_out)
    else:
        raise ValueError("Type '{}' not supported for transpose_1023_and_pad__cython".format(matrix_in.dtype))

@cython.boundscheck(False)
@cython.wraparound(False)
cdef pad_cython_float32(int h, int w, int new_h, int new_w, int vpadding, int hpadding,
                        np.ndarray[np.float32_t, ndim=4] matrix_in,
                        np.ndarray[np.float32_t, ndim=4] matrix_out):
    cdef Py_ssize_t d0, d1, d2, d3, vp, hp
    cdef np.float32_t zero = 0.0
    for d0 in prange(matrix_out.shape[0], nogil=True, schedule="static"):
        for d1 in range(matrix_out.shape[1]):
            # Padded rows
            for hp in range(hpadding):
                for d3 in range(new_w):
                    matrix_out[d0, d1, hp, d3] = zero
                    matrix_out[d0, d1, new_h - hp - 1, d3] = zero
            # Regular rows
            for d2 in range(h):
                # Padded cols
                for vp in range(vpadding):
                    matrix_out[d0, d1, d2 + vpadding, vp] = zero
                    matrix_out[d0, d1, d2 + vpadding, new_w - vp - 1] = zero
                # Regular cols
                for d3 in range(w):
                    matrix_out[d0, d1, d2 + vpadding, d3 + hpadding] = matrix_in[d0, d1, d2, d3]

@cython.boundscheck(False)
@cython.wraparound(False)
cdef transpose_1023_and_pad_cython_float32(int h, int w, int new_h, int new_w, int vpadding, int hpadding,
                                           np.ndarray[np.float32_t, ndim=4] matrix_in,
                                           np.ndarray[np.float32_t, ndim=4] matrix_out):
    cdef Py_ssize_t d0, d1, d2, d3, vp, hp
    cdef np.float32_t zero = 0.0
    for d0 in prange(matrix_out.shape[0], nogil=True, schedule="static"):
        for d1 in range(matrix_out.shape[1]):
            # Padded rows
            for hp in range(hpadding):
                for d3 in range(new_w):
                    matrix_out[d0, d1, hp, d3] = zero
                    matrix_out[d0, d1, new_h - hp - 1, d3] = zero
            # Regular rows
            for d2 in range(h):
                # Padded cols
                for vp in range(vpadding):
                    matrix_out[d0, d1, d2 + vpadding, hp] = zero
                    matrix_out[d0, d1, d2 + vpadding, new_w - hp - 1] = zero
                # Regular cols
                for d3 in range(w):
                    matrix_out[d0, d1, d2 + vpadding, d3 + hpadding] = matrix_in[d1, d0, d2, d3]

def shrink_old_cython(matrix_in, matrix_out):
    """
    DON'T USE THIS!

    Most of the times is slower than the numpy alternatives!

    Implements a parallel version of the opposite of:
        matrix_out = np.pad(matrix_in,
                            ((0, 0), (0, 0),
                            (vpadding, vpadding), (hpadding, hpadding)),
                            mode='constant')
    where vpadding and hpadding are obtained from the difference of the matrix_in
    and matrix_out dimensions.
    """
    h, w, in_h, in_w, vpadding, hpadding = _check_and_get_values(matrix_out, matrix_in)
    d0w = matrix_out.shape[0]
    d1w = matrix_out.shape[1]
    matrix_in_1d = matrix_in.reshape(-1)
    matrix_out_1d = matrix_out.reshape(-1)
    if matrix_in.dtype == np.float32:
        shrink_old_cython_float32(d0w, d1w, h, w, vpadding, hpadding, matrix_in_1d, matrix_out_1d)
    else:
        raise ValueError("Type '{}' not supported for shrink_cython".format(matrix_in.dtype))

@cython.boundscheck(False)
@cython.wraparound(False)
cdef shrink_old_cython_float32(int d0w, int d1w, int h, int w, int vpadding, int hpadding,
                               np.ndarray[np.float32_t, ndim=1] matrix_in_1d,
                               np.ndarray[np.float32_t, ndim=1] matrix_out_1d):
    cdef int nm, d2, d3
    cdef int of_out, of_in, of_out_inner
    cdef int h_x_w = h * w
    cdef int nh = (h + 2 * vpadding)
    cdef int nw = (w + 2 * hpadding)
    cdef int nh_x_nw = nh * nw
    cdef int hpadding_x_2 = hpadding * 2
    cdef int vpadding_x_nw = vpadding * nw
    for nm in prange(d0w * d1w, nogil=True, schedule="static"):
        of_out = nm * h_x_w
        of_in = nm * nh_x_nw + vpadding_x_nw + hpadding
        for d2 in range(h):
            # Alternative a)
            # for d3 in range(w):
            #     matrix_out_1d[of_out] = matrix_in_1d[of_in]
            #     of_out = of_out + 1
            #     of_in = of_in + 1
            # of_in = of_in + hpadding_x_2
            # ---
            # Alternative b)
            for of_out_inner in range(of_out, of_out + w):
                matrix_out_1d[of_out_inner] = matrix_in_1d[of_in]
                of_in = of_in + 1
            of_out = of_out + w
            of_in = of_in + hpadding_x_2

def shrink_cython(matrix_in, matrix_out):
    """
    DON'T USE THIS!

    Most of the times is slower than the numpy alternatives!

    Implements a parallel version of the opposite of:
        matrix_out = np.pad(matrix_in,
                            ((0, 0), (0, 0),
                            (vpadding, vpadding), (hpadding, hpadding)),
                            mode='constant')
    where vpadding and hpadding are obtained from the difference of the matrix_in
    and matrix_out dimensions.
    """
    h, w, inh, inw, vpadding, hpadding = _check_and_get_values(matrix_out, matrix_in)
    d0w = matrix_out.shape[0]
    d1w = matrix_out.shape[1]
    matrix_in_1d = matrix_in.reshape(-1)
    matrix_out_1d = matrix_out.reshape(-1)
    if matrix_in.dtype == np.float32:
        assert (
                sizeof(np.float32_t) == 4), "shrink_cython_float32() assumes sizeof(np.float32_t) == 4 and is {}".format(
            sizeof(np.float32_t))
        shrink_cython_float32(d0w * d1w, h, w, inh, inw, vpadding, hpadding, matrix_in_1d, matrix_out_1d)
    else:
        raise ValueError("Type '{}' not supported for shrink_cython".format(matrix_in.dtype))

@cython.boundscheck(False)
@cython.wraparound(False)
cdef shrink_cython_float32(int d0w_d1w, int h, int w, int inh, int inw, int vpadding, int hpadding,
                           np.ndarray[np.float32_t, ndim=1] matrix_in_1d,
                           np.ndarray[np.float32_t, ndim=1] matrix_out_1d):
    cdef int nm, d2
    cdef int hpadding_size = hpadding << 2
    cdef int hpadding_2_size = hpadding << 3
    cdef int vpadding_inw_size = vpadding * inw << 2
    cdef int w_size = w << 2
    cdef int h_w_size = h * w << 2
    cdef int inw_size = inw << 2
    cdef int inh_inw_size = inh * inw << 2
    cdef void *out_address = &matrix_out_1d[0]
    cdef void *in_address = &matrix_in_1d[0]
    cdef void *out_offset  # out_address can not be used directly (local variable referenced before assignment error)
    cdef void *in_offset  # in_address can not be used directly (local variable referenced before assignment error)
    # Alternative a) Slower than numpy copy
    # for nm in prange(d0w_d1w, nogil=True, schedule="static"):
    #     out_offset = out_address + nm * h_w_size
    #     in_offset = in_address + nm * inh_inw_size + vpadding_inw_size + hpadding_size
    #     for d2 in range(h):
    #         memcpy(out_offset, in_offset, w_size)
    #         out_offset = out_offset + w_size
    #         in_offset = in_offset + inw_size
    # ---
    # Alternative b) Much, much slower
    # in_address = in_address + vpadding_inw_size + hpadding_size
    # for nm in range(d0w_d1w):
    #     for d2 in prange(h, nogil=True, schedule="static"):
    #         out_offset = out_address + d2 * w_size
    #         in_offset = in_address + d2 * inw_size
    #         memcpy(out_offset, in_offset, w_size)
    #     out_address = out_address + h_w_size
    #     in_address = in_address + inh_inw_size
    # ---
    # Alternative c)
    if h < 4 or h % 4 != 0:
        for nm in prange(d0w_d1w, nogil=True, schedule="static"):
            out_offset = out_address + nm * h_w_size
            in_offset = in_address + nm * inh_inw_size + vpadding_inw_size + hpadding_size
            for d2 in range(h):
                memcpy(out_offset, in_offset, w_size)
                out_offset = out_offset + w_size
                in_offset = in_offset + inw_size
        return
    for nm in prange(d0w_d1w, nogil=True, schedule="static"):
        out_offset = out_address + nm * h_w_size
        in_offset = in_address + nm * inh_inw_size + vpadding_inw_size + hpadding_size
        for d2 in range(0, h, 4):
            memcpy(out_offset, in_offset, w_size)
            memcpy(out_offset + w_size, in_offset + inw_size, w_size)
            memcpy(out_offset + w_size + w_size, in_offset + inw_size + inw_size, w_size)
            memcpy(out_offset + w_size + w_size + w_size, in_offset + inw_size + inw_size + inw_size, w_size)
            out_offset = out_offset + w_size + w_size + w_size + w_size
            in_offset = in_offset + inw_size + inw_size + inw_size + inw_size