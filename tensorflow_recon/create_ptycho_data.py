import numpy as np

from simulation import *


# ============================================
# DO NOT ROTATE PROGRESSIVELY
# (DO NOT CONTINUE TO ROTATE AN INTERPOLATED OBJECT)
# ============================================

PI = 3.1415927

# ============================================
theta_st = 0
theta_end = 2 * PI
n_theta = 500
energy_ev = 5000
# energy_ev = 800
psize_cm = 1.e-7
# psize_cm = 0.67e-7
free_prop_cm = 1.e-4
# free_prop_cm = None
phantom_path = 'cone_256_filled_ptycho/phantom'
# phantom_path = 'adhesin_ptycho/phantom'
save_folder = 'cone_256_filled_ptycho'
# save_folder = 'adhesin_ptycho'
# fname = 'data_adhesin_64_1nm_1um.h5'
fname = 'data_adhesin_256_1nm_1um.h5'
# probe_size = [72, 72]
probe_size = [18, 18]
# probe_mag_sigma = 10
probe_mag_sigma = 10
# probe_phase_sigma = 10
probe_phase_sigma = 10
probe_phase_max = 0.5
# ============================================

# probe_mag = np.ones([img_dim, img_dim], dtype=np.float32)
# probe_phase = np.zeros([img_dim, img_dim], dtype=np.float32)
# probe_phase[int(img_dim / 2), int(img_dim / 2)] = 0.1
# probe_phase = gaussian_filter(probe_phase, 3)
# wavefront_initial = [probe_mag, probe_phase]

# probe_pos = [(y, x) for y in np.linspace(36, 220, 23) for x in np.linspace(36, 220, 23)]
# probe_pos = [(y, x) for y in np.linspace(9, 55, 23) for x in np.linspace(9, 55, 23)]
probe_pos = [(y, x) for y in np.linspace(18, 120, 52) for x in np.linspace(54, 198, 72)] + \
            [(y, x) for y in np.linspace(120, 222, 52) for x in np.linspace(22, 230, 104)]

create_ptychography_data_batch_numpy(energy_ev, psize_cm, n_theta, phantom_path, save_folder, fname, probe_pos,
                                     probe_type='gaussian', probe_size=probe_size, theta_st=theta_st, theta_end=theta_end,
                                     probe_mag_sigma=10, probe_phase_sigma=10, probe_phase_max=0.5, probe_circ_mask=None)