import tomopy
import tensorflow as tf
import numpy as np
import dxchange
import h5py
import matplotlib.pyplot as plt
import matplotlib
from pyfftw.interfaces.numpy_fft import fft2, ifft2
from pyfftw.interfaces.numpy_fft import fftshift as np_fftshift
from pyfftw.interfaces.numpy_fft import ifftshift as np_ifftshift
import warnings
try:
    from constants import *
    import sys
    from scipy.ndimage import gaussian_filter
    from scipy.ndimage import fourier_shift
except:
    warnings.warn('Some dependencies are screwed up.')
import os
import pickle
import glob


class Simulator(object):
    """Optical simulation based on multislice propagation.

    Attributes
    ----------
    grid : numpy.ndarray or list of numpy.ndarray
        Descretized grid for the phantom object. If type == 'refractive_index',
        it takes a list of [delta_grid, beta_grid].
    energy : float
        Beam energy in eV. Should match the energy used for creating the grids.
    psize : list
        Pixel size in cm.
    type : str
        Value type of input grid.
    """

    def __init__(self, energy, grid=None, psize=None, type='refractive_index'):

        if type == 'refractive_index':
            if grid is not None:
                self.grid_delta, self.grid_beta = grid
            else:
                self.grid_delta = self.grid_beta = None
            self.energy_kev = energy * 1.e-3
            self.voxel_nm = np.array(psize) * 1.e7
            self.mean_voxel_nm = np.prod(self.voxel_nm)**(1. / 3)
            self._ndim = 3
            self.size_nm = np.array(self.grid_delta.get_shape().as_list()) * self.voxel_nm
            self.shape = self.grid_delta.get_shape().as_list()
            self.lmbda_nm = 1.24 / self.energy_kev
            self.mesh = []
            temp = []
            for i in range(self._ndim):
                temp.append(np.arange(self.shape[i]) * self.voxel_nm[i])
            self.mesh = np.meshgrid(*temp, indexing='xy')

            # wavefront in x-y plane or x edge
            self.wavefront = np.zeros(self.grid_delta.shape[:-1], dtype=np.complex64)
        else:
            raise ValueError('Currently only delta and beta grids are supported.')

    def save_grid(self, save_path='data/sav/grid'):
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        np.save(os.path.join(save_path, 'grid_delta'), self.grid_delta)
        np.save(os.path.join(save_path, 'grid_beta'), self.grid_beta)
        grid_pars = [self.shape, self.voxel_nm, self.energy_kev * 1.e3]
        np.save(os.path.join(save_path, 'grid_pars'), grid_pars)

    def read_grid(self, save_path='data/sav/grid'):
        try:
            self.grid_delta = np.load(os.path.join(save_path, 'grid_delta.npy'))
            self.grid_beta = np.load(os.path.join(save_path, 'grid_beta.npy'))
        except:
            raise ValueError('Failed to read grid.')

    def save_slice_images(self, save_path='data/sav/slices'):
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        dxchange.write_tiff_stack(self.grid_delta, os.path.join(save_path, 'delta'),
                                  overwrite=True, dtype=np.float32)
        dxchange.write_tiff_stack(self.grid_beta, os.path.join(save_path, 'beta'),
                                  overwrite=True, dtype=np.float32)

    def show_grid(self, part='delta'):
        import tifffile
        if part == 'delta':
            tifffile.imshow(self.grid_delta)
        elif part == 'beta':
            tifffile.imshow(self.grid_beta)
        else:
            warnings.warn('Wrong part specified for show_grid.')

    def initialize_wavefront(self, type, **kwargs):
        """Initialize wavefront.

        Parameters:
        -----------
        type : str
            Type of wavefront to be initialized. Valid options:
            'plane', 'spot', 'point_projection_lens', 'spherical'
        kwargs :
            Options specific to the selection of type.
            'plane': no option
            'spot': 'width'
            'point_projection_lens': 'focal_length', 'lens_sample_dist'
            'spherical': 'dist_to_source'
        """
        wave_shape = np.asarray(self.wavefront.shape)
        if type == 'plane':
            self.wavefront[...] = 1.
        elif type == 'spot':
            wid = kwargs['width']
            radius = int(wid / 2)
            if self._ndim == 2:
                center = int(wave_shape[0] / 2)
                self.wavefront[center-radius:center-radius+wid] = 1.
            elif self._ndim == 3:
                center = np.array(wave_shape / 2, dtype=int)
                self.wavefront[center[0]-radius:center[0]-radius+wid, center[1]-radius:center[1]-radius+wid] = 1.
        elif type == 'spherical':
            z = kwargs['dist_to_source']
            xx = self.mesh[0][:, :, 0]
            yy = self.mesh[1][:, :, 0]
            xx -= xx[0, -1] / 2
            yy -= yy[-1, 0] / 2
            print(xx, yy, z)
            r = np.sqrt(xx ** 2 + yy ** 2 + z ** 2)
            self.wavefront = np.exp(-1j * 2 * np.pi * r / self.lmbda_nm)
        elif type == 'point_projection_lens':
            f = kwargs['focal_length']
            s = kwargs['lens_sample_dist']
            xx = self.mesh[0][:, :, 0]
            yy = self.mesh[1][:, :, 0]
            xx -= xx[0, -1] / 2
            yy -= yy[-1, 0] / 2
            r = np.sqrt(xx ** 2 + yy ** 2)
            theta = np.arctan(r / (s - f))
            path = np.mod(s / np.cos(theta), self.lmbda_nm)
            phase = path * 2 * PI
            wavefront = np.ones(wave_shape).astype('complex64')
            wavefront = wavefront + 1j * np.tan(phase)
            self.wavefront = wavefront / np.abs(wavefront)


def gen_mesh(max, shape):
    """Generate mesh grid.
    """
    yy = np.linspace(-max[0], max[0], shape[0])
    xx = np.linspace(-max[1], max[1], shape[1])
    res = np.meshgrid(xx, yy)
    return res


def get_kernel(dist_nm, lmbda_nm, voxel_nm, grid_shape):
    """Get Fresnel propagation kernel for TF algorithm.

    Parameters:
    -----------
    simulator : :class:`acquisition.Simulator`
        The Simulator object.
    dist : float
        Propagation distance in cm.
    """
    # k = 2 * PI / lmbda_nm
    u_max = 1. / (2. * voxel_nm[0])
    v_max = 1. / (2. * voxel_nm[1])
    u, v = gen_mesh([v_max, u_max], grid_shape[0:2])
    # H = np.exp(1j * k * dist_nm * np.sqrt(1 - lmbda_nm**2 * (u**2 + v**2)))
    H = np.exp(-1j * PI * lmbda_nm * dist_nm * (u**2 + v**2))

    return H


def get_kernel_ir(dist_nm, lmbda_nm, voxel_nm, grid_shape):

    """
    Get Fresnel propagation kernel for IR algorithm.

    Parameters:
    -----------
    simulator : :class:`acquisition.Simulator`
        The Simulator object.
    dist : float
        Propagation distance in cm.
    """
    size_nm = np.array(voxel_nm) * np.array(grid_shape)
    k = 2 * PI / lmbda_nm
    ymin, xmin = np.array(size_nm)[:2] / -2.
    dy, dx = voxel_nm[0:2]
    x = np.arange(xmin, xmin + size_nm[1], dx)
    y = np.arange(ymin, ymin + size_nm[0], dy)
    x, y = np.meshgrid(x, y)
    h = np.exp(1j * k * dist_nm) / (1j * lmbda_nm * dist_nm) * np.exp(1j * k / (2 * dist_nm) * (x**2 + y**2))
    h = tf.convert_to_tensor(h, dtype='complex64')
    H = tf.fft2d(fftshift(h)) * voxel_nm[0] * voxel_nm[1]

    return H


def preprocess(dat, blur=None, normalize_bg=False):

    dat[np.abs(dat) < 2e-3] = 2e-3
    dat[dat > 1] = 1
    if normalize_bg:
        dat = tomopy.normalize_bg(dat)
    dat = -np.log(dat)
    dat[np.where(np.isnan(dat) == True)] = 0
    if blur is not None:
        dat = gaussian_filter(dat, blur)

    return dat


def realign_image(arr, shift):
    """
    Translate and rotate image via Fourier

    Parameters
    ----------
    arr : ndarray
        Image array.

    shift: tuple
        Mininum and maximum values to rescale data.

    angle: float, optional
        Mininum and maximum values to rescale data.

    Returns
    -------
    ndarray
        Output array.
    """
    # if both shifts are integers, do circular shift; otherwise perform Fourier shift.
    if np.count_nonzero(np.abs(np.array(shift) - np.round(shift)) < 0.01) == 2:
        temp = np.roll(arr, int(shift[0]), axis=0)
        temp = np.roll(temp, int(shift[1]), axis=1)
        temp = temp.astype('float32')
    else:
        temp = fourier_shift(np.fft.fftn(arr), shift)
        temp = np.fft.ifftn(temp)
        temp = np.abs(temp).astype('float32')
    return temp


def fftshift(tensor):
    ndim = len(tensor.shape)
    for i in range(ndim):
        n = tensor.shape[i].value
        p2 = (n+1) // 2
        begin1 = [0] * ndim
        begin1[i] = p2
        size1 = tensor.shape.as_list()
        size1[i] = size1[i] - p2
        begin2 = [0] * ndim
        size2 = tensor.shape.as_list()
        size2[i] = p2
        t1 = tf.slice(tensor, begin1, size1)
        t2 = tf.slice(tensor, begin2, size2)
        tensor = tf.concat([t1, t2], axis=i)
    return tensor


def ifftshift(tensor):
    ndim = len(tensor.shape)
    for i in range(ndim):
        n = tensor.shape[i].value
        p2 = n - (n + 1) // 2
        begin1 = [0] * ndim
        begin1[i] = p2
        size1 = tensor.shape.as_list()
        size1[i] = size1[i] - p2
        begin2 = [0] * ndim
        size2 = tensor.shape.as_list()
        size2[i] = p2
        t1 = tf.slice(tensor, begin1, size1)
        t2 = tf.slice(tensor, begin2, size2)
        tensor = tf.concat([t1, t2], axis=i)
    return tensor


def multislice_propagate(grid_delta, grid_beta, probe_real, probe_imag, energy_ev, psize_cm, h=None, free_prop_cm=None, pad=None, obj_shape=None):

    if pad is not None:
        grid_delta = tf.pad(grid_delta, pad, 'CONSTANT')
        grid_beta = tf.pad(grid_beta, pad, 'CONSTANT')

    voxel_nm = np.array([psize_cm] * 3) * 1.e7
    wavefront = np.zeros([obj_shape[0], obj_shape[1]])
    # wavefront = tf.convert_to_tensor(wavefront, dtype=tf.complex64, name='wavefront')
    wavefront = tf.constant(wavefront, dtype='complex64')
    wavefront = wavefront + tf.cast(probe_real, tf.complex64) + 1j * tf.cast(probe_imag, tf.complex64)
    lmbda_nm = 1240. / energy_ev
    mean_voxel_nm = np.prod(voxel_nm) ** (1. / 3)
    size_nm = np.array(obj_shape) * voxel_nm
    # wavefront = tf.reshape(wavefront, [1, wavefront.shape[0].value, wavefront.shape[1].value, 1])

    n_slice = grid_delta.shape[-1]
    delta_nm = voxel_nm[-1]

    grid_delta = tf.cast(grid_delta, tf.complex64)
    grid_beta = tf.cast(grid_beta, tf.complex64)

    if h is None:
        kernel = get_kernel(delta_nm, lmbda_nm, voxel_nm, obj_shape)
    k = 2. * PI * delta_nm / lmbda_nm

    def modulate_and_propagate(i, wavefront):
        delta_slice = grid_delta[:, :, i]
        # delta_slice = tf.cast(delta_slice, dtype=tf.complex64)
        beta_slice = grid_beta[:, :, i]
        # beta_slice = tf.cast(beta_slice, dtype=tf.complex64)
        c = tf.exp(1j * k * delta_slice) * tf.exp(-k * beta_slice)
        wavefront = wavefront * c
        dist_nm = delta_nm
        l = np.prod(size_nm)**(1. / 3)
        crit_samp = lmbda_nm * dist_nm / l

        if mean_voxel_nm > crit_samp:
            # wavefront = tf.nn.conv2d(wavefront, h, (1, 1, 1, 1), 'SAME')
            wavefront = tf.ifft2d(ifftshift(fftshift(tf.fft2d(wavefront)) * h))
        else:
            wavefront = tf.fft2d(fftshift(wavefront))
            wavefront = ifftshift(tf.ifft2d(wavefront * h))
        i = i + 1
        return i, wavefront

    i = tf.constant(0)
    c = lambda i, wavefront: tf.less(i, n_slice)
    _, wavefront = tf.while_loop(c, modulate_and_propagate, [i, wavefront])

    if free_prop_cm is not None:
        dist_nm = free_prop_cm * 1e7
        l = np.prod(size_nm)**(1. / 3)
        crit_samp = lmbda_nm * dist_nm / l
        algorithm = 'TF' if mean_voxel_nm > crit_samp else 'IR'
        if algorithm == 'TF':
            h = get_kernel(dist_nm, lmbda_nm, voxel_nm, obj_shape)
        else:
            h = get_kernel_ir(dist_nm, lmbda_nm, voxel_nm, obj_shape)
        wavefront = fftshift(tf.fft2d(wavefront)) * h
        wavefront = tf.ifft2d(ifftshift(wavefront))

    # for i_slice in range(n_slice):
    #     # print('Slice: {:d}'.format(i_slice))
    #     # sys.stdout.flush()
    #     delta_slice = grid_delta[:, :, i_slice]
    #     delta_slice = tf.cast(delta_slice, dtype=tf.complex64)
    #     beta_slice = grid_beta[:, :, i_slice]
    #     beta_slice = tf.cast(beta_slice, dtype=tf.complex64)
    #     c = tf.exp(1j * k * delta_slice) * tf.exp(-k * beta_slice)
    #     # c = tf.reshape(c, wavefront.shape)
    #     wavefront = wavefront * c
    #
    #     dist_nm = delta_nm
    #     l = np.prod(size_nm)**(1. / 3)
    #     crit_samp = lmbda_nm * dist_nm / l
    #
    #     if mean_voxel_nm > crit_samp:
    #         # wavefront = tf.nn.conv2d(wavefront, h, (1, 1, 1, 1), 'SAME')
    #         wavefront = tf.ifft2d(ifftshift(fftshift(tf.fft2d(wavefront)) * h))
    #     else:
    #         wavefront = tf.fft2d(fftshift(wavefront))
    #         wavefront = ifftshift(tf.ifft2d(wavefront * h))

    return wavefront


def multislice_propagate_batch(grid_delta_batch, grid_beta_batch, probe_real, probe_imag, energy_ev, psize_cm, free_prop_cm=None, obj_batch_shape=None):

    minibatch_size = obj_batch_shape[0]
    grid_shape = obj_batch_shape[1:]
    voxel_nm = np.array([psize_cm] * 3) * 1.e7
    # wavefront = tf.convert_to_tensor(wavefront, dtype=tf.complex64, name='wavefront')
    wavefront = tf.zeros([minibatch_size, obj_batch_shape[1], obj_batch_shape[2]], dtype='complex64')
    wavefront = wavefront + tf.cast(probe_real, tf.complex64) + 1j * tf.cast(probe_imag, tf.complex64)
    lmbda_nm = 1240. / energy_ev
    mean_voxel_nm = np.prod(voxel_nm) ** (1. / 3)
    size_nm = np.array(grid_shape) * voxel_nm
    # wavefront = tf.reshape(wavefront, [1, wavefront.shape[0].value, wavefront.shape[1].value, 1])

    n_slice = obj_batch_shape.shape[-1]
    delta_nm = voxel_nm[-1]

    kernel = get_kernel(delta_nm, lmbda_nm, voxel_nm, grid_shape)
    h = tf.convert_to_tensor(kernel, dtype=tf.complex64, name='kernel')
    h = fftshift(h)
    # h = tf.reshape(h, [h.shape[0].value, h.shape[1].value, 1, 1])
    k = 2. * PI * delta_nm / lmbda_nm

    def modulate_and_propagate(i, wavefront):
        delta_slice = grid_delta_batch[:, :, :, i]
        delta_slice = tf.cast(delta_slice, dtype=tf.complex64)
        beta_slice = grid_beta_batch[:, :, :, i]
        beta_slice = tf.cast(beta_slice, dtype=tf.complex64)
        c = tf.exp(1j * k * delta_slice) * tf.exp(-k * beta_slice)
        # c = tf.reshape(c, wavefront.shape)
        wavefront = wavefront * c
        wavefront = tf.ifft2d(tf.fft2d(wavefront) * h)
        i = i + 1
        return (i, wavefront)

    i = tf.constant(0)
    c = lambda i, wavefront: tf.less(i, n_slice)
    _, wavefront = tf.while_loop(c, modulate_and_propagate, [i, wavefront])

    # for i in range(n_slice):
    #     delta_slice = grid_delta_batch[:, :, :, i]
    #     delta_slice = tf.cast(delta_slice, dtype=tf.complex64)
    #     beta_slice = grid_beta_batch[:, :, :, i]
    #     beta_slice = tf.cast(beta_slice, dtype=tf.complex64)
    #     c = tf.exp(1j * k * delta_slice) * tf.exp(-k * beta_slice)
    #     # c = tf.reshape(c, wavefront.shape)
    #     wavefront = wavefront * c
    #     wavefront = tf.ifft2d(tf.fft2d(wavefront) * h)

    if free_prop_cm is not None:
        dist_nm = free_prop_cm * 1e7
        l = np.prod(size_nm)**(1. / 3)
        crit_samp = lmbda_nm * dist_nm / l
        algorithm = 'TF' if mean_voxel_nm > crit_samp else 'IR'
        if algorithm == 'TF':
            h = get_kernel(dist_nm, lmbda_nm, voxel_nm, grid_shape)
        else:
            h = get_kernel_ir(dist_nm, lmbda_nm, voxel_nm, grid_shape)
        wavefront = tf.ifft2d(ifftshift(fftshift(tf.fft2d(wavefront)) * h))

    return wavefront


def multislice_propagate_batch_numpy(grid_delta_batch, grid_beta_batch, probe_real, probe_imag, energy_ev, psize_cm, free_prop_cm=None, obj_batch_shape=None):

    minibatch_size = obj_batch_shape[0]
    grid_shape = obj_batch_shape[1:]
    voxel_nm = np.array([psize_cm] * 3) * 1.e7
    wavefront = np.zeros([minibatch_size, obj_batch_shape[1], obj_batch_shape[2]], dtype='complex64')
    wavefront += (probe_real + 1j * probe_imag)

    lmbda_nm = 1240. / energy_ev
    mean_voxel_nm = np.prod(voxel_nm) ** (1. / 3)
    size_nm = np.array(grid_shape) * voxel_nm

    n_slice = obj_batch_shape.shape[-1]
    delta_nm = voxel_nm[-1]

    h = get_kernel(delta_nm, lmbda_nm, voxel_nm, grid_shape)
    h = fftshift(h)
    # h = tf.reshape(h, [h.shape[0].value, h.shape[1].value, 1, 1])
    k = 2. * PI * delta_nm / lmbda_nm

    for i in range(n_slice):
        delta_slice = grid_delta_batch[:, :, :, i]
        beta_slice = grid_beta_batch[:, :, :, i]
        c = np.exp(1j * k * delta_slice) * np.exp(-k * beta_slice)
        wavefront = wavefront * c
        wavefront = ifft2(fft2(wavefront) * h)

    if free_prop_cm is not None:
        dist_nm = free_prop_cm * 1e7
        l = np.prod(size_nm)**(1. / 3)
        crit_samp = lmbda_nm * dist_nm / l
        algorithm = 'TF' if mean_voxel_nm > crit_samp else 'IR'
        if algorithm == 'TF':
            h = get_kernel(dist_nm, lmbda_nm, voxel_nm, grid_shape)
        else:
            h = get_kernel_ir(dist_nm, lmbda_nm, voxel_nm, grid_shape)
        wavefront = ifft2(np_ifftshift(np_fftshift(fft2(wavefront)) * h))

    return wavefront


def create_batches(arr, batch_size):

    arr_len = len(arr)
    i = 0
    batches = []
    while i < arr_len:
        batches.append(arr[i:min(i+batch_size, arr_len)])
        i += batch_size
    return batches


def save_rotation_lookup(array_size, n_theta, dest_folder=None):

    image_center = [np.floor(x / 2) for x in array_size]

    coord0 = np.arange(array_size[0])
    coord1 = np.arange(array_size[1])
    coord2 = np.arange(array_size[2])

    coord2_vec = np.tile(coord2, array_size[1])

    coord1_vec = np.tile(coord1, array_size[2])
    coord1_vec = np.reshape(coord1_vec, [array_size[1], array_size[2]])
    coord1_vec = np.reshape(np.transpose(coord1_vec), [-1])

    coord0_vec = np.tile(coord0, [array_size[1] * array_size[2]])
    coord0_vec = np.reshape(coord0_vec, [array_size[1] * array_size[2], array_size[0]])
    coord0_vec = np.reshape(np.transpose(coord0_vec), [-1])

    # move origin to image center
    coord1_vec = coord1_vec - image_center[1]
    coord2_vec = coord2_vec - image_center[2]

    # create matrix of coordinates
    coord_new = np.stack([coord1_vec, coord2_vec]).astype(np.float32)

    # create rotation matrix
    theta_ls = np.linspace(0, 2 * np.pi, n_theta)
    coord_old_ls = []
    for theta in theta_ls:
        m_rot = np.array([[np.cos(theta),  -np.sin(theta)],
                          [np.sin(theta), np.cos(theta)]])
        coord_old = np.matmul(m_rot, coord_new)
        coord1_old = np.round(coord_old[0, :] + image_center[1]).astype(np.int)
        coord2_old = np.round(coord_old[1, :] + image_center[2]).astype(np.int)
        # clip coordinates
        coord1_old = np.clip(coord1_old, 0, array_size[1]-1)
        coord2_old = np.clip(coord2_old, 0, array_size[2]-1)
        coord_old = np.stack([coord1_old, coord2_old], axis=1)
        coord_old_ls.append(coord_old)
    if dest_folder is None:
        dest_folder = 'arrsize_{}_{}_{}_ntheta_{}'.format(array_size[0], array_size[1], array_size[2], n_theta)
    if not os.path.exists(dest_folder):
        os.mkdir(dest_folder)
    for i, arr in enumerate(coord_old_ls):
        f = open(os.path.join(dest_folder, '{:04}'.format(i)), 'wb')
        pickle.dump(arr, f)
        f.close()

    coord1_vec = coord1_vec + image_center[1]
    coord1_vec = np.tile(coord1_vec, array_size[0])
    coord2_vec = coord2_vec + image_center[2]
    coord2_vec = np.tile(coord2_vec, array_size[0])
    for i, coord in enumerate([coord0_vec, coord1_vec, coord2_vec]):
        f = open(os.path.join(dest_folder, 'coord{}_vec'.format(i)), 'wb')
        pickle.dump(coord, f)
        f.close()

    return coord_old_ls


def read_origin_coords(src_folder, index):

    f = open(os.path.join(src_folder, '{:04}'.format(index)), 'r')
    coords = pickle.load(f)
    coords = tf.convert_to_tensor(coords)
    return coords


def read_all_origin_coords(src_folder, n_theta):

    coord_ls = []
    for i in range(n_theta):
        coord_ls.append(read_origin_coords(src_folder, i))
    coord_ls = tf.convert_to_tensor(coord_ls)
    return coord_ls


def apply_rotation(obj, coord_old, src_folder):

    coord_vec_ls = []
    for i in range(3):
        f = open(os.path.join(src_folder, 'coord{}_vec'.format(i)))
        coord_vec_ls.append(tf.convert_to_tensor(pickle.load(f), dtype=tf.int32))
    s = obj.get_shape().as_list()
    coord0_vec, coord1_vec, coord2_vec = coord_vec_ls

    # sess = tf.Session()

    coord_new = tf.cast(tf.stack([coord0_vec, coord1_vec, coord2_vec], axis=1), tf.int32)

    coord_old = tf.cast(tf.tile(coord_old, [s[0], 1]), tf.int32)
    coord1_old = coord_old[:, 0]
    coord2_old = coord_old[:, 1]
    coord_old = tf.stack([coord0_vec, coord1_old, coord2_old], axis=1)
    # print(sess.run(coord_old))


    obj_channel_ls = tf.split(obj, s[3], 3)
    obj_rot_channel_ls = []
    for channel in obj_channel_ls:
        obj_chan_new_val = tf.gather_nd(tf.squeeze(channel), coord_old)
        obj_rot_channel_ls.append(tf.sparse_to_dense(coord_new, [s[0], s[1], s[2]],
                                                     obj_chan_new_val, 0, validate_indices=False))
    obj_rot = tf.stack(obj_rot_channel_ls, axis=3)
    # print(sess.run(obj_rot))
    return obj_rot



def rotate_image_tensor(image, angle, mode='black'):
    """
    Rotates a 3D tensor (HWD), which represents an image by given radian angle.

    New image has the same size as the input image.

    mode controls what happens to border pixels.
    mode = 'black' results in black bars (value 0 in unknown areas)
    mode = 'white' results in value 255 in unknown areas
    mode = 'ones' results in value 1 in unknown areas
    mode = 'repeat' keeps repeating the closest pixel known
    """
    s = image.get_shape().as_list()
    assert len(s) == 3, "Input needs to be 3D."
    assert (mode == 'repeat') or (mode == 'black') or (mode == 'white') or (mode == 'ones'), "Unknown boundary mode."
    image_center = [np.floor(x/2) for x in s]

    # Coordinates of new image
    coord1 = tf.range(s[0])
    coord2 = tf.range(s[1])

    # Create vectors of those coordinates in order to vectorize the image
    coord1_vec = tf.tile(coord1, [s[1]])

    coord2_vec_unordered = tf.tile(coord2, [s[0]])
    coord2_vec_unordered = tf.reshape(coord2_vec_unordered, [s[0], s[1]])
    coord2_vec = tf.reshape(tf.transpose(coord2_vec_unordered, [1, 0]), [-1])

    # center coordinates since rotation center is supposed to be in the image center
    coord1_vec_centered = coord1_vec - image_center[0]
    coord2_vec_centered = coord2_vec - image_center[1]

    coord_new_centered = tf.cast(tf.pack([coord1_vec_centered, coord2_vec_centered]), tf.float32)

    # Perform backward transformation of the image coordinates
    rot_mat_inv = tf.dynamic_stitch([[0], [1], [2], [3]], [tf.cos(angle), tf.sin(angle), -tf.sin(angle), tf.cos(angle)])
    rot_mat_inv = tf.reshape(rot_mat_inv, shape=[2, 2])
    coord_old_centered = tf.matmul(rot_mat_inv, coord_new_centered)

    # Find nearest neighbor in old image
    coord1_old_nn = tf.cast(tf.round(coord_old_centered[0, :] + image_center[0]), tf.int32)
    coord2_old_nn = tf.cast(tf.round(coord_old_centered[1, :] + image_center[1]), tf.int32)

    # Clip values to stay inside image coordinates
    if mode == 'repeat':
        coord_old1_clipped = tf.minimum(tf.maximum(coord1_old_nn, 0), s[0]-1)
        coord_old2_clipped = tf.minimum(tf.maximum(coord2_old_nn, 0), s[1]-1)
    else:
        outside_ind1 = tf.logical_or(tf.greater(coord1_old_nn, s[0]-1), tf.less(coord1_old_nn, 0))
        outside_ind2 = tf.logical_or(tf.greater(coord2_old_nn, s[1]-1), tf.less(coord2_old_nn, 0))
        outside_ind = tf.logical_or(outside_ind1, outside_ind2)

        coord_old1_clipped = tf.boolean_mask(coord1_old_nn, tf.logical_not(outside_ind))
        coord_old2_clipped = tf.boolean_mask(coord2_old_nn, tf.logical_not(outside_ind))

        coord1_vec = tf.boolean_mask(coord1_vec, tf.logical_not(outside_ind))
        coord2_vec = tf.boolean_mask(coord2_vec, tf.logical_not(outside_ind))

    coord_old_clipped = tf.cast(tf.transpose(tf.pack([coord_old1_clipped, coord_old2_clipped]), [1, 0]), tf.int32)

    # Coordinates of the new image
    coord_new = tf.transpose(tf.cast(tf.pack([coord1_vec, coord2_vec]), tf.int32), [1, 0])

    image_channel_list = tf.split(2, s[2], image)

    image_rotated_channel_list = list()
    for image_channel in image_channel_list:
        image_chan_new_values = tf.gather_nd(tf.squeeze(image_channel), coord_old_clipped)

        if (mode == 'black') or (mode == 'repeat'):
            background_color = 0
        elif mode == 'ones':
            background_color = 1
        elif mode == 'white':
            background_color = 255

        image_rotated_channel_list.append(tf.sparse_to_dense(coord_new, [s[0], s[1]], image_chan_new_values,
                                                             background_color, validate_indices=False))

    image_rotated = tf.transpose(tf.pack(image_rotated_channel_list), [1, 2, 0])

    return image_rotated


def total_variation_3d(arr):

    # res = tf.pow(tf.manip.roll(arr, 1, 0) - arr, 2)
    # res += tf.pow(tf.manip.roll(arr, 1, 1) - arr, 2)
    # res += tf.pow(tf.manip.roll(arr, 1, 2) - arr, 2)
    # res = tf.sqrt(res)
    # res = tf.reduce_sum(tf.boolean_mask(res, tf.is_finite(res)))
    res = tf.reduce_sum(tf.image.total_variation(arr))
    res += tf.reduce_sum(tf.image.total_variation(tf.transpose(arr, perm=[2, 0, 1, 3])))
    res += tf.reduce_sum(tf.image.total_variation(tf.transpose(arr, perm=[1, 2, 0, 3])))
    return res


def generate_sphere(shape, radius, anti_aliasing=5):

    shape = np.array(shape)
    radius = int(radius)
    x = np.linspace(-radius, radius, (radius * 2 + 1) * anti_aliasing)
    y = np.linspace(-radius, radius, (radius * 2 + 1) * anti_aliasing)
    z = np.linspace(-radius, radius, (radius * 2 + 1) * anti_aliasing)
    xx, yy, zz = np.meshgrid(x, y, z)
    a = (xx**2 + yy**2 + zz**2 <= radius**2).astype('float')
    res = np.zeros(shape * anti_aliasing)
    center_res = (np.array(res.shape) / 2).astype('int')
    res[center_res[0] - int(a.shape[0] / 2):center_res[0] + int(a.shape[0] / 2),
        center_res[1] - int(a.shape[0] / 2):center_res[1] + int(a.shape[0] / 2),
        center_res[2] - int(a.shape[0] / 2):center_res[2] + int(a.shape[0] / 2)] = a
    res = gaussian_filter(res, 0.5 * anti_aliasing)
    res = res[::anti_aliasing, ::anti_aliasing, ::anti_aliasing]
    return res


def generate_shell(shape, radius, anti_aliasing=5):

    sphere1 = generate_sphere(shape, radius + 0.5, anti_aliasing=anti_aliasing)
    sphere2 = generate_sphere(shape, radius - 0.5, anti_aliasing=anti_aliasing)
    return sphere1 - sphere2


def fourier_shell_correlation(obj, ref, step_size=1, save_path='fsc', save_mask=True):

    if not os.path.exists(save_path):
        os.makedirs(save_path)

    radius_max = int(min(obj.shape) / 2)
    f_obj = np_fftshift(fftn(obj))
    f_ref = np_fftshift(fftn(ref))
    f_prod = f_obj * np.conjugate(f_ref)
    f_obj_2 = np.real(f_obj * np.conjugate(f_obj))
    f_ref_2 = np.real(f_ref * np.conjugate(f_ref))
    radius_ls = np.arange(1, radius_max, step_size)
    fsc_ls = []
    np.save(os.path.join(save_path, 'radii.npy'), radius_ls)

    for rad in radius_ls:
        print(rad)
        if os.path.exists(os.path.join(save_path, 'mask_rad_{:04d}.tiff'.format(int(rad)))):
            mask = dxchange.read_tiff(os.path.join(save_path, 'mask_rad_{:04d}.tiff'.format(int(rad))))
        else:
            mask = generate_shell(obj.shape, rad, anti_aliasing=2)
            if save_mask:
                dxchange.write_tiff(mask, os.path.join(save_path, 'mask_rad_{:04d}.tiff'.format(int(rad))),
                                    dtype='float32', overwrite=True)
        fsc = abs(np.sum(f_prod * mask))
        fsc /= np.sqrt(np.sum(f_obj_2 * mask) * np.sum(f_ref_2 * mask))
        fsc_ls.append(fsc)
        np.save(os.path.join(save_path, 'fsc.npy'), fsc_ls)

    matplotlib.rcParams['pdf.fonttype'] = 'truetype'
    fontProperties = {'family': 'serif', 'serif': ['Times New Roman'], 'weight': 'normal', 'size': 12}
    plt.rc('font', **fontProperties)
    plt.plot(radius_ls.astype(float) / radius_ls[-1], fsc_ls)
    plt.xlabel('Spatial frequency (1 / Nyquist)')
    plt.ylabel('FSC')
    plt.savefig(os.path.join(save_path, 'fsc.pdf'), format='pdf')


def upsample_2x(arr):

    if arr.ndim == 4:
        out_arr = np.zeros([arr.shape[0] * 2, arr.shape[1] * 2, arr.shape[2] * 2, arr.shape[3]])
        for i in range(arr.shape[3]):
            out_arr[:, :, :, i] = upsample_2x(arr[:, :, :, i])
    else:
        out_arr = np.zeros([arr.shape[0] * 2, arr.shape[1] * 2, arr.shape[2] * 2])
        out_arr[::2, ::2, ::2] = arr[:, :, :]
        out_arr = gaussian_filter(out_arr, 1)
    return out_arr


def print_flush(a):
    print(a)
    sys.stdout.flush()


def real_imag_to_mag_phase(realpart, imagpart):

    a = realpart + 1j * imagpart
    return np.abs(a), np.angle(a)


def mag_phase_to_real_imag(mag, phase):

    a = mag * np.exp(1j * phase)
    return a.real, a.imag


def create_probe_initial_guess(data_fname, dist_nm, energy_ev, psize_nm):

    f = h5py.File(data_fname, 'r')
    dat = f['exchange/data'][...]
    # NOTE: this is for toy model
    wavefront = np.mean(np.abs(dat), axis=0)
    lmbda_nm = 1.24 / energy_ev
    h = get_kernel(-dist_nm, lmbda_nm, [psize_nm, psize_nm], wavefront.shape)
    wavefront = np.fft.fftshift(np.fft.fft2(wavefront)) * h
    wavefront = np.fft.ifft2(np.fft.ifftshift(wavefront))
    return wavefront
