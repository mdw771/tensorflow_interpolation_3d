import numpy as np
import tensorflow as tf

def biliniear_interpolation_3d(data, warp):
    """
    Interpolate a 3D array (monochannel).
    :param data: 3D tensor.
    :param warp: a list of 3D coordinates to interpolate. 2D tensor with shape (n_points, 3).
    """
    n_pts = warp.shape[0]
    # Pad data around to avoid indexing overflow
    data = tf.pad(data, [[1, 1], [1, 1], [1, 1]], mode='SYMMETRIC')
    warp = warp + tf.constant([1, 1, 1], dtype='float32')
    i000 = tf.cast(tf.floor(warp), dtype=tf.int32)
    i100 = i000 + tf.constant([1, 0, 0])
    i010 = i000 + tf.constant([0, 1, 0])
    i001 = i000 + tf.constant([0, 0, 1])
    i110 = i000 + tf.constant([1, 1, 0])
    i101 = i000 + tf.constant([1, 0, 1])
    i011 = i000 + tf.constant([0, 1, 1])
    i111 = i000 + tf.constant([1, 1, 1])
    c000 = tf.gather_nd(data, i000)
    c100 = tf.gather_nd(data, i100)
    c010 = tf.gather_nd(data, i010)
    c001 = tf.gather_nd(data, i001)
    c110 = tf.gather_nd(data, i110)
    c101 = tf.gather_nd(data, i101)
    c011 = tf.gather_nd(data, i011)
    c111 = tf.gather_nd(data, i111)
    # build matrix
    h00 = tf.ones(n_pts)
    x0 = tf.cast(i000[:, 0], dtype=tf.float32)
    y0 = tf.cast(i000[:, 1], dtype=tf.float32)
    z0 = tf.cast(i000[:, 2], dtype=tf.float32)
    x1 = tf.cast(i111[:, 0], dtype=tf.float32)
    y1 = tf.cast(i111[:, 1], dtype=tf.float32)
    z1 = tf.cast(i111[:, 2], dtype=tf.float32)
    h1 = tf.stack([h00, x0, y0, z0, x0 * y0, x0 * z0, y0 * z0, x0 * y0 * z0])
    h2 = tf.stack([h00, x1, y0, z0, x1 * y0, x1 * z0, y0 * z0, x1 * y0 * z0])
    h3 = tf.stack([h00, x0, y1, z0, x0 * y1, x0 * z0, y1 * z0, x0 * y1 * z0])
    h4 = tf.stack([h00, x1, y1, z0, x1 * y1, x1 * z0, y1 * z0, x1 * y1 * z0])
    h5 = tf.stack([h00, x0, y0, z1, x0 * y0, x0 * z1, y0 * z1, x0 * y0 * z1])
    h6 = tf.stack([h00, x1, y0, z1, x1 * y0, x1 * z1, y0 * z1, x1 * y0 * z1])
    h7 = tf.stack([h00, x0, y1, z1, x0 * y1, x0 * z1, y1 * z1, x0 * y1 * z1])
    h8 = tf.stack([h00, x1, y1, z1, x1 * y1, x1 * z1, y1 * z1, x1 * y1 * z1])
    h = tf.stack([h1, h2, h3, h4, h5, h6, h7, h8])
    h = tf.transpose(h, perm=[2, 0, 1])
    c = tf.transpose(tf.stack([c000, c100, c010, c110, c001, c101, c011, c111]))
    c = tf.expand_dims(c, -1)
    a = tf.matmul(tf.matrix_inverse(h), c)[:, :, 0]
    x = warp[:, 0]
    y = warp[:, 1]
    z = warp[:, 2]

    f = a[:, 0] + a[:, 1] * x + a[:, 2] * y + a[:, 3] * z + \
        a[:, 4] * x * y + a[:, 5] * x * z + a[:, 6] * y * z + a[:, 7] * x * y * z
    return f
