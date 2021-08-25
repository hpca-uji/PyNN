"""
PyDTNN convWinograd module
"""

#
#  This file is part of Python Distributed Training of Neural Networks (PyDTNN)
#
#  Copyright (C) 2021 Universitat Jaume I
#
#  PyDTNN is free software: you can redistribute it and/or modify it under the
#  terms of the GNU General Public License as published by the Free Software
#  Foundation, either version 3 of the License, or (at your option) any later
#  version.
#
#  This program is distributed in the hope that it will be useful, but WITHOUT
#  ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
#  or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public
#  License for more details.
#
#  You should have received a copy of the GNU General Public License along
#  with this program. If not, see <https://www.gnu.org/licenses/>.
#

import ctypes
import math
import platform
import weakref
from contextlib import suppress

import numpy as np

# from pydtnn.tracers import PYDTNN_OPS_EVENT, PYDTNN_OPS_EVENTS, ...
from pydtnn.utils import load_library
from pydtnn.utils.best_pad import best_pad
from pydtnn.utils.memory_cache import MemoryCache


@property
def is_conv_winograd_available():
    try:
        load_library("convWinograd")
    except ImportError:
        return False
    return True


class ConvWinograd:
    """
    Exposes the libconvWinograd functions following the PyDTNN conventions.

    Methods
    -------
    winograd(weights, x, biases, vpadding, hpadding, 
             vstride, hstride, vdilation, hdilation)
        calls the appropriate winograd function from libconvWinograd.so to perform
        the Toom-Cook based convolution.

    Examples
    --------
    See __usage_example__() method for an example of use. This example can be
    run with: 'python conv_winograd.py'

    Tests
    -----
    To perform the tests, run the following command from the current directory:
        python -m unittest tests.convWinogradTestcase

    (see tests/winograd.py for more instructions on testing)
    """

    lib_cw = None  # will link to the libconvWinograd.so library

    def __init__(self, dtype=np.float32, debug=False, parent_layer=None):
        """
        Loads the libconvWinograd.so library.

        Parameters
        ----------
        dtype : data type
            The element data type being used on all the matrices.
        debug : boolean
            Whether to print debug information or not.
        parent_layer: layer
            The layer that is using it (for tracing purposes).
        """
        if isinstance(dtype, type):
            self.dtype = dtype
        else:
            try:
                self.dtype = {'float32': np.float32, 'float64': np.float64}[dtype]
            except coeyError:
                raise AttributeError("dtype '{}' not recognized".format(dtype)) from None

        if ConvWinograd.lib_cw is None:
            ConvWinograd.lib_cw = load_library("convWinograd")

        # F(2x2, 3x3)
        self.bt_2x2_3x3 = np.array([[   1,   0,  -1  ,0 ], 
                                    [   0,   1,   1,  0 ], 
                                    [   0,  -1,   1,  0 ], 
                                    [   0,   1,   0, -1 ]], dtype=self.dtype, order="C")  # Transpose of B
        self.g_2x2_3x3  = np.array([[   1,   0,   0 ], 
                                    [ 0.5, 0.5, 0.5 ], 
                                    [ 0.5,-0.5, 0.5 ], 
                                    [   0,   0,   1 ]], dtype=self.dtype, order="C")
        self.at_2x2_3x3 = np.array([[   1,   1,   1,  0 ], 
                                    [   0,   1,  -1, -1 ]], dtype=self.dtype, order="C")  # Transpose of A

        # The x_padded and output matrices are also cached, but only on the current instance
        self.x_padded_cache = MemoryCache(lambda shape: np.zeros(shape, self.dtype, order="C"))
        self.out_cw_cache = MemoryCache(lambda shape: np.empty(shape, self.dtype, order="C"))
        self.y_cache = MemoryCache(lambda shape: np.zeros(shape, self.dtype, order="C"))
        self.u_cache = MemoryCache(lambda shape: np.zeros(shape, self.dtype, order="C"))
        self.v_cache = MemoryCache(lambda shape: np.zeros(shape, self.dtype, order="C"))
        self.m_cache = MemoryCache(lambda shape: np.zeros(shape, self.dtype, order="C"))
        self.d_cache = MemoryCache(lambda shape: np.zeros(shape, self.dtype, order="C"))

        # Debug
        self.debug = debug
        # Parent layer
        if parent_layer is not None:
            self.get_parent_layer = weakref.ref(parent_layer)
        # choose the appropriate convWinograd function depending on the architecture and the data type being used
        if platform.machine() == 'aarch64':
            if self.dtype == np.float32:
                try:
                    self.x_winograd_nchw = self.lib_cw.sconvWinograd2x2_3x3_nchw
                except AttributeError:
                    print("Winograd sconvWinograd2x2_3x3_nchw routine not found. Fallback to numpy version!".format(str(self.dtype)))
                    self.x_winograd_nchw = self.lib_cw.sconvWinograd2x2_3x3_numpy_nchw
            else:
                raise ValueError("Type {} not supported by this version of libconvWinograd!".format(str(self.dtype)))
        elif platform.machine() == 'x86_64':
            if self.dtype == np.float32:
                try:
                    self.x_winograd_nchw = self.lib_cw.sconvWinograd2x2_3x3_nchw
                except AttributeError:
                    print("Winograd sconvWinograd2x2_3x3_nchw routine not found. Fallback to numpy version!".format(str(self.dtype)))
                    self.x_winograd_nchw = self.lib_cw.sconvWinograd2x2_3x3_numpy_nchw

                # try:
                #     self.x_winograd_nhwc = self.lib_cw.sconvWinograd2x2_3x3_nhwc
                # except AttributeError:
                #     pass # do not complain about missing symbols
            else:
                raise ValueError("Type {} not supported by this version of libconvWinograd!".format(str(self.dtype)))
        else:
            raise ValueError("Platform '{}' not yet supported")

    def conv_winograd_2x2_3x3_numpy_nchw(self, weights, x, biases=None, vpadding=0, hpadding=0, 
                                         vstride=1, hstride=1, vdilation=1, hdilation=1):
        n, ci, hi, wi = x.shape
        co, ci, kh, kw = weights.shape

        m, r = 2, 3    # Winograd output tile (m x m) and filter (r x r) sizes
        s = r - 1      # Winograd sliding window stride
        t = m + r - 1  # Winograd sliding window size t x t

        if (kh, kw) != (r, r):
            raise ValueError("Kernel size {} supported by this version of Winograd, kernel size should be (3x3)!".format(str((kh, kw))))

        if (vstride, hstride) != (1, 1):
            raise ValueError("Stride {} supported by this version of Winograd, stride should be (1,1)!".format(str((vstride, hstride))))

        if (vdilation, hdilation) != (1, 1):
            raise ValueError("Dilation {} supported by this version of Winograd, dilation should be (1,1)!".format(str((vdilation, hdilation))))

        ho = (hi + 2 * vpadding - vdilation * (kh - 1) - 1) // vstride + 1
        wo = (wi + 2 * hpadding - hdilation * (kw - 1) - 1) // hstride + 1

        tile_h = math.ceil((hi + 2 * vpadding - kh) / m) + 1
        tile_w = math.ceil((wi + 2 * hpadding - kw) / m) + 1

        y = self.y_cache[(n, co, ho, wo)]    # Output
        u = self.u_cache[(t, t, co, ci)]     # Workspace for G * g * G^T
        v = self.v_cache[(t, t, ci, (n * tile_h * tile_w))]
        m_= self.m_cache[(t, t, co, (n * tile_h * tile_w))]
        d = self.d_cache[(t, t)]

        for k in range(co):
            for c in range(ci):
                # U = G  * g * G^T
                u[..., k, c] = (self.g_2x2_3x3 @ weights[k, c, ...]) @ self.g_2x2_3x3.T    

        # 1.1) First alternative: padding first
        # x_padded = best_pad(x, vpadding, hpadding)
        # _, _, hi, wi = x_padded.shape
        #
        # for c in range(ci):
        #     for b in range(n):
        #         for h in range(tile_h):
        #             for w in range(tile_w):
        #                 hh, ww = h * s, w * s
        #                 th, tw = min(hi-hh,t), min(wi-ww,t)
        #                 d[:th, :tw] = x_padded[b, c, hh:hh+th, ww:ww+tw]
        #                 v[..., c, b * tile_h * tile_w + h * tile_w + w] = (self.bt_2x2_3x3 @ d) @ self.bt_2x2_3x3.T

        # 1.2) Second alternative: avoid padding
        for c in range(ci):
            for b in range(n):
                for h in range(tile_h):
                    hh_= min(hi, h * s - vpadding)
                    hh, fh = (hh_, 0) if hh_ > 0 else (0, min(-hh_, t))
                    oh = max(min(t, hi - hh) - min(t, fh), 0)

                    for w in range(tile_w):
                        ww_= min(wi, w * s - hpadding)
                        ww, fw = (ww_, 0) if ww_ > 0 else (0, min(-ww_, t))
                        ow = max(min(t, wi - ww) - min(t, fw), 0)

                        if hh < hh+oh and ww < ww+ow:
                            d[fh:fh+oh, fw:fw+ow] = x[b, c, hh:hh+oh, ww:ww+ow]

                        #   0  0  0
                        #   X  X  X
                        #   X  X  X
                        if 0 <= fh:
                            d[:fh, ...] = 0

                        #   0  0  0
                        #   X  X  X
                        #   0  0  0
                        if fh+oh < t:
                            d[fh+oh:, ...] = 0

                        #   0  0  0
                        #   0  X  X
                        #   0  0  0
                        if 0 <= fw:
                            d[fh:fh+oh, :fw] = 0

                        #   0  0  0
                        #   0  X  0
                        #   0  0  0
                        if fw+ow < t:
                            d[fh:fh+oh, fw+ow:] = 0

                        v[..., c, b * tile_h * tile_w + h * tile_w + w] = (self.bt_2x2_3x3 @ d) @ self.bt_2x2_3x3.T

        # 2.1) Firt alternative: np.einsum
        m_ = np.einsum('... i j, ... j k -> ... i k', u, v)

        # 2.2) Second alternative: matmul
        # for i in range(t):
        #     for j in range(t):
        #         m_[i, j] = u[i, j] @ v[i, j]
        
        for k in range(co):
            for b in range(n):
                for h in range(tile_h):
                    for w in range(tile_w): 
                        z = (self.at_2x2_3x3 @ m_[..., k, b * tile_h * tile_w + h * tile_w + w]) @ self.at_2x2_3x3.T
                        hh, ww = h * s, w * s
                        y[b, k, hh:hh+m, ww:ww+m] = z[:min(m, ho-hh), :min(m, wo-ww)]

            if biases is not None:
                y[:, k, ...] += biases[k]

        return y

    def conv_winograd_2x2_3x3_nchw(self, weights, x, biases=None, vpadding=0, hpadding=0, 
                                   vstride=1, hstride=1, vdilation=1, hdilation=1):

        n, ci, hi, wi = x.shape
        co, ci, kh, kw = weights.shape

        m, r = 2, 3    # Winograd output tile (m x m) and filter (r x r) sizes
        s = r - 1      # Winograd sliding window stride
        t = m + r - 1  # Winograd sliding window size t x t

        if (kh, kw) != (r, r):
            raise ValueError("Kernel size {} supported by this version of Winograd, kernel size should be (3x3)!".format(str((kh, kw))))

        if (vstride, hstride) != (1, 1):
            raise ValueError("Stride {} supported by this version of Winograd, stride should be (1,1)!".format(str((vstride, hstride))))

        if (vdilation, hdilation) != (1, 1):
            raise ValueError("Dilation {} supported by this version of Winograd, dilation should be (1,1)!".format(str((vdilation, hdilation))))

        ho = (hi + 2 * vpadding - vdilation * (kh - 1) - 1) // vstride + 1
        wo = (wi + 2 * hpadding - hdilation * (kw - 1) - 1) // hstride + 1

        tile_h = (hi + 2 * vpadding - kh) // m + 1
        tile_w = (wi + 2 * hpadding - kw) // m + 1

        y = self.y_cache[(n, co, ho, wo)]    # Output
        u = self.u_cache[(t, t, co, ci)]     # Workspace for G * g * G^T
        v = self.v_cache[(t, t, ci, (n * tile_h * tile_w))]
        m1= self.m_cache[(t, t, co, (n * tile_h * tile_w))]
        m2= self.m_cache[(co, (n * tile_h * tile_w))]
        d = self.d_cache[(t, t)]

        ldD1, ldD2, ldD3 = ci * hi * wi, hi * wi, wi
        ldF1, ldF2, ldF3 = ci * kh * kw, kh * kw, kw
        ldY1, ldY2, ldY3 = co * ho * wo, ho * wo, wo

        self.x_winograd_nchw(ctypes.c_uint(n), ctypes.c_uint(co), ctypes.c_uint(ci), 
                             ctypes.c_uint(hi), ctypes.c_uint(wi),
                             ctypes.c_uint(kh), ctypes.c_uint(kw),
                             ctypes.c_uint(vpadding), ctypes.c_uint(hpadding), 
                             ctypes.c_void_p(x.ctypes.data), ctypes.c_uint(ldD1), ctypes.c_uint(ldD2), ctypes.c_uint(ldD3),
                             ctypes.c_void_p(weights.ctypes.data),ctypes.c_uint(ldF1), ctypes.c_uint(ldF2), ctypes.c_uint(ldF3),
                             ctypes.c_void_p(y.ctypes.data), ctypes.c_uint(ldY1), ctypes.c_uint(ldY2), ctypes.c_uint(ldY3),
                             ctypes.c_void_p(0 if biases is None else biases.ctypes.data),
                             ctypes.c_void_p(self.bt_2x2_3x3.ctypes.data),
                             ctypes.c_void_p(self.g_2x2_3x3.ctypes.data),
                             ctypes.c_void_p(self.at_2x2_3x3.ctypes.data),
                             ctypes.c_void_p(u.ctypes.data), ctypes.c_void_p(v.ctypes.data),
                             ctypes.c_void_p(m1.ctypes.data), ctypes.c_void_p(m2.ctypes.data))
        return y


def __usage_example__():
    # Imports for this usage example (not required otherwise)
    from timeit import timeit
    from pydtnn.cython_modules import im2col_nchw_cython
    # Default parameters (1st layer AlexNet for Cifar10)
    b = 64  # Batch size
    c = 3  # Channels per layer
    h = 32  # Layers height
    w = 32  # Layers width
    kn = 64  # Number of filters
    kh = 3  # Filters weights height
    kw = 3  # Filters weights width
    vpadding = 3  # Vertical padding
    hpadding = 3  # Horizontal padding
    vstride = 1  # Vertical stride
    hstride = 1  # Horizontal stride
    vdilation = 1  # Vertical dilation
    hdilation = 1  # Horizontal dilation
    # Create weights, x, and biases matrices from previous parameters. If no biases
    # matrix is provided, a proper one filled with zeros will be automatically
    # created.
    weights = np.zeros((kn, c, kh, kw)).astype(np.float32, order='C')
    weights[0][0][0][0] = 1.89
    weights[1][1][1][1] = 3.0
    weights[2][2][2][2] = 4.0
    x = np.ones((b, c, h, w)).astype(np.float32, order='C')
    ho = (h + 2 * vpadding - vdilation * (kh - 1) - 1) // vstride + 1
    wo = (w + 2 * hpadding - hdilation * (kw - 1) - 1) // hstride + 1
    biases = (np.ones((kn, b * ho * wo)) * 10).astype(np.float32, order='C')
    biases_wg = (np.ones((kn)) * 10).astype(np.float32, order='C')
    print("Using conv_winograd to compute weights * x + biases...")
    conv_winograd = ConvWinograd(debug=False)
    conv_winograd_result = conv_winograd.conv_winograd_2x2_3x3_nchw(weights, x, biases_wg,
                                         vpadding=vpadding, hpadding=hpadding,
                                         vstride=vstride, hstride=hstride,
                                         vdilation=vdilation, hdilation=hdilation)
    # print(conv_winograd_result)
    print("Sum: ", conv_winograd_result.sum())
    conv_winograd_t = timeit(lambda: conv_winograd.conv_winograd_2x2_3x3_nchw(weights, x, biases_wg,
                                        vpadding=vpadding, hpadding=hpadding,
                                        vstride=vstride, hstride=hstride,
                                        vdilation=vdilation, hdilation=hdilation),
                                        number=10) / 10
    print("conv_winograd time: {:.4f}".format(conv_winograd_t))
    print()
    print("Using im2col and mm...")
    x_c = im2col_nchw_cython(x, kh, kw, vpadding, hpadding, vstride, hstride, vdilation, hdilation)
    w_c = weights.reshape(kn, -1)
    im2col_mm_result = w_c @ x_c + biases
    im2col_mm_result = im2col_mm_result.reshape(kn, -1, ho, wo).transpose(1, 0, 2, 3)
    # print(im2col_mm_result)
    print("Sum: ", im2col_mm_result.sum())
    print("np.allclose: ", np.allclose(conv_winograd_result, im2col_mm_result))
    # im2col_t = timeit(lambda: im2col_nchw_cython(x, kh, kw, vpadding, hpadding, vstride, hstride,
    #                                              vdilation, hdilation), number=10) / 10
    # print("im2col time: {:.4f}".format(im2col_t))
    mm_t = timeit(lambda: w_c @ im2col_nchw_cython(x, kh, kw, vpadding, hpadding, vstride, hstride, vdilation, hdilation) \
                       + biases, number=10) / 10
    print("mm time: {:.4f}".format(mm_t))

    # """
    n = 64
    c = k = 64
    h = w = 32
    vpadd = hpadd = 6
    for nn in range(16, n, 16):
        for cc in range(16, c, 16):
            for kk in range(16, k, 16):
                for hh in range(8, h, 4):
                    for ww in range(8, w, 4):
                        for vpadding in range(1, vpadd):
                            for hpadding in range(1, hpadd):
                                print(nn, cc, kk, hh, ww, vpadding, hpadding, end="")
                                weights = np.random.rand(kk, cc, kh, kw).astype(np.float32, order='C')
                                x = np.random.rand(nn, cc, hh, ww).astype(np.float32, order='C')
                                ho = (hh + 2 * vpadding - vdilation * (kh - 1) - 1) // vstride + 1
                                wo = (ww + 2 * hpadding - hdilation * (kw - 1) - 1) // hstride + 1
                                biases = (np.ones((kk, nn * ho * wo)) * 10).astype(np.float32, order='C')
                                biases_wg = (np.ones((kk)) * 10).astype(np.float32, order='C')
                                conv_winograd = ConvWinograd(debug=False)
                                conv_winograd_t = timeit(lambda: conv_winograd.conv_winograd_2x2_3x3_nchw(weights, x, biases_wg,
                                                                    vpadding=vpadding, hpadding=hpadding,
                                                                    vstride=vstride, hstride=hstride,
                                                                    vdilation=vdilation, hdilation=hdilation),
							    number=10) / 10
                                print(" conv_winograd time: {:.4f} ".format(conv_winograd_t), end="")
                                w_c = weights.reshape(kk, -1)
                                im2col_t = timeit(lambda: w_c @ im2col_nchw_cython(x, kh, kw, vpadding, hpadding, vstride, hstride, vdilation, hdilation) \
                                                   + biases, number=10) / 10
                                print("mm time: {:.4f}".format(im2col_t), end="")
                                print((" WINOGR", " IM2COL")[conv_winograd_t > im2col_t], im2col_t/conv_winograd_t)
    # """

if __name__ == "__main__":
    __usage_example__()
