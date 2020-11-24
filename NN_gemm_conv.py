""" Python Distributed Training of Neural Networks - PyDTNN

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

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details. You
should have received a copy of the GNU General Public License along with this
program. If not, see <http://www.gnu.org/licenses/>.

"""

__author__ = "Manuel F. Dolz, Enrique S. Quintana, Sergio Barrachina, Mar Catalan, Adrian Castello"
__contact__ = "dolzm@uji.es"
__copyright__ = "Copyright 2020, Universitat Jaume I"
__credits__ = ["Manuel F. Dolz, Enrique S. Quintana", "Sergio Barrachina", "Mar Catalan", "Adrian Castello"]
__date__ = "2020/11/14"

__email__ = "dolzm@uji.es"
__license__ = "GPLv3"
__maintainer__ = "Manuel F. Dolz"
__status__ = "Testing"
__version__ = "1.1.0"

import ctypes
import os
from ctypes.util import find_library
from math import floor

import numpy as np


# Credits of this function go to 'rotglug' stackoverflow user
# https://stackoverflow.com/questions/8658813/control-memory-alignment-in-python-ctypes
# Some changes have been done to the original code to correct some bugs
def ctypes_aligned_alloc(alignment, size):
    buf_size = size + alignment
    raw_memory = bytearray(buf_size)
    ctypes_raw_type = (ctypes.c_byte * buf_size)
    ctypes_raw_memory = ctypes_raw_type.from_buffer(raw_memory)
    raw_address = ctypes.addressof(ctypes_raw_memory)
    offset = raw_address % alignment
    offset_to_aligned = alignment - offset
    ctypes_aligned_type = (ctypes.c_byte * size)
    ctypes_aligned_memory = ctypes_aligned_type.from_buffer(raw_memory, offset_to_aligned)
    return ctypes_aligned_memory


class GemmConv:
    """
    Exposes the libgemmConv functions following the PyDTNN conventions.

    Methods
    -------
    gemm_conv(filters, layers, biases, alpha, beta, vpadding, hpadding, vstride, hstride)
        Calls sgemm_conv or hgemm_conv (from libgemmConv.so) to perform a matrix
        matrix multiplication with an implicit im2col.

    Examples
    --------
    See __usage_example__() method for an example of use. This example can be
    run with: 'python NN_gemm_conv.py'

    Test
    ----
    From the current directory execute:
        python -m unittest unittests.TestGemmConv

    (see unittests/test_NN_gemm_conv.py for more instructions on testing)
    """

    # References
    # ==========
    # gemmConv.h
    # ----------
    # #define BLOCK_MC 560  // 120
    # #define BLOCK_KC 368  // 640
    # #define BLOCK_NC 3072
    # convEval.c
    # ----------
    # Ac_pack = (float*) aligned_alloc(4096,BLOCK_MC*BLOCK_KC*sizeof(float));
    # Bc_pack = (float*) aligned_alloc(4096,BLOCK_KC*BLOCK_NC*sizeof(float));

    def __init__(self, dtype=np.float32, debug=False, alignment=4096, block_mc=560, block_kc=368, block_nc=3072):
        """
        Loads the libgemmConv.so library and creates the required auxiliary matrices ac_pack and bc_pack.

        Parameters
        ----------
        dtype : data type
            The element data type being used on all the matrices.
        debug : boolean
            Whether to print debug information or not.
        alignment : int
            The alignment used when allocating memory for the auxiliary matrices ac_pack and bc_pack.
        block_mc : int
            The mc dimension of ac_pack.
        block_kc : int
            The kc dimension of ac_pack and bc_pack.
        block_nc : int
            The nc dimension of bc_pack.
        """
        path = find_library('gemmConv')
        if not path:
            for current_path in os.environ['LD_LIBRARY_PATH'].split(':'):
                if os.path.exists(os.path.join(current_path, 'libgemmConv.so')):
                    path = os.path.join(current_path, 'libgemmConv.so')
                    break
        if not path:
            raise ImportError("Library 'libgemmConv.so' could not be found. Please add its path to LD_LIBRARY_PATH "
                              "using 'export LD_LIBRARY_PATH=libgemmConv_path:$LD_LIBRARY_PATH' before calling this "
                              "application.")
        self.lib = ctypes.cdll.LoadLibrary(path)
        self.dtype = dtype
        dtype_bytes = dtype(1).nbytes
        self.ac_pack = ctypes_aligned_alloc(alignment, block_mc * block_kc * dtype_bytes)
        self.bc_pack = ctypes_aligned_alloc(alignment, block_kc * block_nc * dtype_bytes)
        self.debug = debug

    def gemm_conv(self, filters, layers, biases=None, alpha=1.0, beta=1.0,
                  vpadding=0, hpadding=0, vstride=1, hstride=1):
        """
        Calls sgemm_conv or hgemm_conv (from libgemmConv.so) to perform a matrix
        matrix multiplication with an implicit im2col.

        The matrix matrix product is in the form C = alpha * AB + beta * C, where:
            + A is the filters matrix,
            + B is the im2col(layers) matrix, and
            + C is the biases matrix.

        Parameters
        ----------
        filters : array_like
            The filters matrix (kn x c x kh x kw).
        layers : array_like
            The layers matrix (b x c x h x w).
        biases : array_like
            An optional biases matrix (kn x b*ho*wo).
        alpha : float
            The alpha factor.
        beta : float
            The beta factor.
        vpadding : int
            The vertical padding to be applied to the layers matrix.
        hpadding : int
            The horizontal padding to be applied to the layers matrix.
        vstride : int
            The vertical stride.
        hstride : int
            The horizontal stride.

        Returns
        -------
        array_like
            The result of alpha * filters * im2col(layers_padded) + beta * biases.
        """
        # Check stride parameters
        assert vstride == hstride, \
            "gemmConv does not support different vertical and horizontal strides"
        stride = vstride

        # Pad layers matrix and set vpadding and hpadding to 0
        layers_padded = np.pad(layers,
                               ((0, 0), (0, 0), (vpadding, vpadding), (hpadding, hpadding)),
                               mode='constant').astype(layers.dtype)
        vpadding = hpadding = 0

        # Get matrices dimensions (once layers matrix has been padded)
        kn, ck, kh, kw = filters.shape
        b, c, h, w = layers_padded.shape
        assert ck == c, "Number of channels in filters and layers should be the same!"

        # Compute height and weight of the output
        # Note: h and w are obtained from layers_padded (no from layers) and
        #       vpadding and hpadding are set to 0 in order to use the usual
        #       formulation
        ho = int(floor((h + 2 * vpadding - kh) / vstride + 1))
        wo = int(floor((w + 2 * hpadding - kw) / hstride + 1))

        # Create zero biases matrix if none provided. Otherwise, test its dimensions
        if biases is None:
            biases = np.zeros((kn, b * ho * wo)).astype(filters.dtype, order='C')
        else:
            assert (kn, b * ho * wo) == biases.shape, \
                "Biases matrix should be {}x{}".format(kn, b * ho * wo)

        # Check that dtype is the same on all the matrices
        assert filters.dtype == layers.dtype == biases.dtype, \
            "All the matrices must have the same type of data!"
        assert filters.dtype == self.dtype, \
            "The input matrices must have the same type of data as the one specified when " \
            "this class was instantiated!"

        # Change matrices axes to the gemmConv expected order:
        #   Where I→hi×wi×ci×b corresponds to the input tensor,
        #   F→kn×kh×kw×ci denotes the filters,
        #   and O→kn×ho×wo×b is the output tensor
        filters_gc = filters.transpose((0, 2, 3, 1)).reshape(kn, -1, order="F")
        layers_padded_gc = layers_padded.transpose((2, 3, 1, 0)).flatten(order="F")
        biases_gc = biases.astype(filters.dtype, order="F")

        # Call custom added function to libgemmConv.so to print the received parameters
        if self.debug:
            try:
                self.lib.expose_sgemm_conv(ctypes.c_uint(kh), ctypes.c_uint(kw),
                                           ctypes.c_uint(c), ctypes.c_uint(kn),
                                           ctypes.c_float(alpha), ctypes.c_void_p(filters_gc.ctypes.data),
                                           ctypes.c_uint(h), ctypes.c_uint(w),
                                           ctypes.c_uint(b), ctypes.c_uint(stride),
                                           ctypes.c_void_p(layers_padded_gc.ctypes.data), ctypes.c_float(beta),
                                           ctypes.c_void_p(biases_gc.ctypes.data),
                                           ctypes.byref(self.ac_pack), ctypes.byref(self.bc_pack))
            except AttributeError:
                print("Warning: Custom 'expose_sgemm_conv' function is not present in 'libgemmConv.so'. "
                      "You can safely ignore this message.")

        # Choose sgemm_conv or hgemm_conv function depending on the data type being used
        if filters.dtype == np.float16:
            xgemm_conv = self.lib.hgemm_conv
        elif filters.dtype == np.float32:
            xgemm_conv = self.lib.sgemm_conv
        else:
            raise ValueError("Type {} not supported by gemm_conv!".format(str(filters.dtype)))

        # Call sgemm_conv or hgemm_conv
        xgemm_conv(ctypes.c_uint(kh), ctypes.c_uint(kw),
                   ctypes.c_uint(c), ctypes.c_uint(kn),
                   ctypes.c_float(alpha), ctypes.c_void_p(filters_gc.ctypes.data),
                   ctypes.c_uint(h), ctypes.c_uint(w),
                   ctypes.c_uint(b), ctypes.c_uint(stride),
                   ctypes.c_void_p(layers_padded_gc.ctypes.data), ctypes.c_float(beta),
                   ctypes.c_void_p(biases_gc.ctypes.data),
                   ctypes.byref(self.ac_pack), ctypes.byref(self.bc_pack))

        # Change output matrix axes to the PyDTNN expected order:
        biases = biases_gc.reshape((kn, b, wo, ho)).transpose((0, 1, 3, 2)).reshape(kn, -1, order="C")
        return biases


def __usage_example__():
    # Imports for this usage example (not required otherwise)
    from timeit import timeit
    from NN_im2col_cython import im2col_cython
    # Default parameters
    b = 32  # Batch size
    c = 3  # Channels per layer
    h = 128  # Layers height
    w = 100  # Layers width
    kn = 8  # Number of filters
    kh = 16  # Filters height
    kw = 10  # Filters width
    vpadding = 1  # Vertical padding
    hpadding = 2  # Horizontal padding
    vstride = 1  # Vertical stride
    hstride = vstride  # Horizontal stride (gemmConv does not support different vertical and horizontal strides)
    # Create filters, layers, and biases matrices from previous parameters. If
    # no biases matrix is provided, a proper one filled with zeros will be
    # automatically created.
    filters = np.zeros((kn, c, kh, kw)).astype(np.float32, order='C')
    filters[0][0][0][0] = 1.89
    filters[1][1][1][1] = 3.0
    filters[2][2][2][2] = 4.0
    layers = np.ones((b, c, h, w)).astype(np.float32, order='C')
    ho = int(floor((h + 2 * vpadding - kh) / vstride + 1))
    wo = int(floor((w + 2 * hpadding - kw) / hstride + 1))
    biases = (np.ones((kn, b * ho * wo)) * 10).astype(np.float32, order='C')
    print("Using gemm_conv to compute alpha * filters * layers + beta * biases...")
    gemm_conv = GemmConv()
    gemm_conv_result = gemm_conv.gemm_conv(filters, layers, biases=biases,
                                           vpadding=vpadding, hpadding=hpadding,
                                           vstride=vstride, hstride=hstride)
    print(gemm_conv_result)
    print("Sum: ", gemm_conv_result.sum())
    sgemm_conv_t = timeit(lambda: gemm_conv.gemm_conv(filters, layers,
                                                      vpadding=vpadding, hpadding=hpadding,
                                                      vstride=vstride, hstride=hstride),
                          number=5) / 5

    print("gemm_conv time: {:.2f}".format(sgemm_conv_t))
    print()
    print("Using im2col and mm...")
    a_t = im2col_cython(layers, kh, kw, vpadding, hpadding, vstride, hstride)
    w_c = filters.reshape(kn, -1)
    im2col_mm_result = w_c @ a_t + biases
    print(im2col_mm_result)
    print("Sum: ", im2col_mm_result.sum())
    print("np.allclose: ", np.allclose(gemm_conv_result, im2col_mm_result))
    im2col_t = timeit(lambda: im2col_cython(layers, kh, kw, vpadding, hpadding, vstride, hstride), number=5) / 5
    print("im2col time: {:.2f}".format(im2col_t))
    mm_t = timeit(lambda: w_c @ a_t + biases, number=5) / 5
    print("mm time: {:.2f}".format(mm_t))


if __name__ == "__main__":
    __usage_example__()