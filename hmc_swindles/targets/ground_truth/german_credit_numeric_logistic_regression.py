# coding=utf-8
# Copyright 2022 The Google Research Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# python3
r"""Ground truth values for `german_credit_numeric_logistic_regression`."""

import numpy as np

PARAMS_MEAN: np.ndarray = np.array([
    -0.7352827054226667,
    0.41866870568141934,
    -0.41408340082973866,
    0.12674651532219475,
    -0.36449841940347355,
    -0.1787095587256448,
    -0.15284503374361397,
    0.012945745597343381,
    0.18079313937606414,
    -0.11069993193855845,
    -0.22427831572334683,
    0.12245964652829924,
    0.02876605764799352,
    -0.13613726719351119,
    -0.2923712178606636,
    0.27844278973319087,
    -0.2996302141552852,
    0.3037762840794942,
    0.2703879269081164,
    0.12259872057122918,
    -0.06292353155882136,
    -0.09266207767564091,
    -0.025138438006849718,
    -0.02285481618031211,
    -1.203318299172,
]).reshape((25,))

PARAMS_MEAN_STANDARD_ERROR: np.ndarray = np.array([
    6.1012991980311924e-05,
    7.463384273085632e-05,
    6.52812613496821e-05,
    7.798883054702806e-05,
    6.307380522552345e-05,
    6.227027739589214e-05,
    5.354034805526289e-05,
    6.04404398473962e-05,
    7.66186990247283e-05,
    6.73392017057976e-05,
    5.201062614735653e-05,
    6.490682131449691e-05,
    5.6650124345737464e-05,
    6.643508804279057e-05,
    8.200884520063484e-05,
    5.524013290291545e-05,
    6.973889767910411e-05,
    9.029073728789311e-05,
    8.226418110554051e-05,
    0.000110733130183622,
    0.00011528686630808109,
    6.438037651980422e-05,
    9.988535605139446e-05,
    9.778501139563837e-05,
    6.462079645004537e-05,
]).reshape((25,))

PARAMS_STANDARD_DEVIATION: np.ndarray = np.array([
    0.08995878946524997,
    0.1041838230985769,
    0.09487855366414591,
    0.10811436614307905,
    0.09466519622990757,
    0.09214885632781586,
    0.08192803881220141,
    0.09090391655123663,
    0.10439113353338751,
    0.09704044004595577,
    0.07885561998985224,
    0.09402741588602435,
    0.0856098631374047,
    0.09445691924296881,
    0.11827858477818105,
    0.08273520521468354,
    0.10341222307519475,
    0.1210186486237161,
    0.11117699079089349,
    0.1374658764845949,
    0.14311583566612943,
    0.09026830544894694,
    0.12746535439081513,
    0.12475249415294769,
    0.09194566052845303,
]).reshape((25,))
