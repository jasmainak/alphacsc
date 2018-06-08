import argparse
import numpy as np
import os.path as op
import matplotlib.pyplot as plt
from scipy.signal import tukey

import mne
from mne.utils import _reject_data_segments
from mne.preprocessing import ICA, create_eog_epochs
from mne import EvokedArray

from alphacsc.learn_d_z_multi import learn_d_z_multi
from alphacsc.utils import construct_X_multi, _choose_convolve
from alphacsc.utils import plot_callback, get_D
from alphacsc.utils.viz import COLORS

parser = argparse.ArgumentParser('Programme to launch experiment on multi csc')
parser.add_argument('--profile', action='store_true',
                    help='Print profiling of the function')
args = parser.parse_args()

dataset = 'sample'

n_atoms = 25
debug = False

# get X
data_path = op.join(mne.datasets.sample.data_path(), 'MEG', 'sample')
raw = mne.io.read_raw_fif(op.join(data_path,
                                  'sample_audvis_filt-0-40_raw.fif'),
                          preload=True)
raw.pick_types(meg='mag', eog=True)
raw.filter(1., 40.)
raw_data = raw[:][0]
# raw.crop(tmax=100.)  # take only 30 s of data

# ICA for comparison
picks_meg = mne.pick_types(raw.info, meg=True, eeg=False, eog=False,
                           stim=False, exclude='bads')

method = 'fastica'
decim = 3
random_state = 23

eog_inds = [4]

reject = dict(mag=5e-12)
ica = ICA(n_components=n_atoms, method=method, random_state=random_state)
ica.fit(raw, picks=picks_meg, decim=decim, reject=reject)
eog_average = create_eog_epochs(raw, reject=reject, picks=picks_meg).average()

# uncomment next two lines to automatically compute eog_inds
# eog_epochs = create_eog_epochs(raw, reject=reject)  # get single EOG trials
# eog_inds, scores = ica.find_bads_eog(eog_epochs)  # find via correlation

# Now multicsc
raw.pick_types(meg='mag')
X = raw[:][0]
X, _ = _reject_data_segments(X, reject, flat=None, decim=None,
                             info=raw.info, tstep=0.3)

# define n_channels, n_times, n_trials
n_channels, n_times = X.shape
n_times_atom = int(round(raw.info['sfreq'] * 0.8))  # 400. ms

# make windows
X = X[None, ...]
X *= tukey(n_times, alpha=0.1)[None, None, :]
X /= np.linalg.norm(X, axis=-1, keepdims=True)

plt.close('all')

if debug:
    callback = plot_callback(X, raw.info, n_atoms)
else:
    callback = None

if args.profile:
    import cProfile
    callback = None
    pr = cProfile.Profile()
    pr.enable()
pobj, times, uv_hat, Z_hat = learn_d_z_multi(
    X, n_atoms, n_times_atom, random_state=42, n_iter=200, n_jobs=1, reg=0.1,
    eps=1e-5, solver_z_kwargs={'factr': 1e12},
    D_init='chunk',
    solver_d_kwargs={'max_iter': 300}, uv_constraint='separate',
    solver_d='alternate_adaptive', callback=callback)

if args.profile:
    pr.disable()
    pr.dump_stats('.profile')

X_hat = construct_X_multi(Z_hat, uv_hat, n_channels=n_channels)

if debug:
    plt.figure("X")
    plt.plot(X.mean(axis=1)[0])
    plt.plot(X_hat.mean(axis=1)[0])
    plt.show()

    # Look at v * Z for one trial
    # (we have only one trial, so full time series)
    X_hat_k = np.zeros((n_atoms, n_times))
    for k in range(n_atoms):
        X_hat_k[k] = _choose_convolve(Z_hat[k, 0, :][None, :],
                                      uv_hat[k, n_channels:][None, :])
    ch_names = ['atom %d' % ii for ii in range(n_atoms)]
    info = mne.create_info(ch_names, sfreq=raw.info['sfreq'])
    raw_atoms = mne.io.RawArray(X_hat_k, info, first_samp=raw.first_samp)
    raw_atoms.plot(scalings=dict(misc='auto'))

# XXX: what is this 20 Hz atom? It doesn't have topomap of motor ...
atom_idx = 14
if debug:
    plt.figure('Power spectral density')
    sfreq = raw.info['sfreq']
    psd = np.abs(np.fft.rfft(uv_hat[atom_idx, :n_channels])) ** 2
    freqs = np.linspace(0, sfreq / 2.0, psd.shape[0])
    plt.plot(freqs, psd.T)
    plt.gca().set(xscale='log')

from mne.io import write_info
np.savez('examples_multicsc/multi_sample-ave.npz', Z_hat=Z_hat,
         uv_hat=uv_hat, sfreq=raw.info['sfreq'], n_channels=n_channels)
write_info('examples_multicsc/info_sample.fif', raw.info)

if debug:
    ica.plot_properties(raw, picks=eog_inds, psd_args={'fmax': 35.},
                        image_args={'sigma': 1.})
    ica.plot_sources(raw, exclude=eog_inds)  # look at source time course
