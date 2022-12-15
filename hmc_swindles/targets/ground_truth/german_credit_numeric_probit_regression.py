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

r"""Ground truth values for `german_credit_numeric_probit_regression`."""

import numpy as np

PARAMS_MEAN: np.ndarray = np.array([
    -0.42897908685666664,
    0.23926350113090672,
    -0.23673147128780866,
    0.07479450724008266,
    -0.1987225548121577,
    -0.10159476965721996,
    -0.08587131672317283,
    0.002678475537235347,
    0.10167407899781508,
    -0.06651921478671127,
    -0.1341514596743671,
    0.07182084459677976,
    0.02278630491196944,
    -0.07875284566173173,
    -0.16706362838880526,
    0.15780152215110624,
    -0.18331287427579027,
    0.17095199260805277,
    0.14876390220915298,
    0.05988977283620661,
    -0.050845961524007235,
    -0.05436852813041426,
    -0.01747069034250209,
    -0.019031406873481684,
    -0.7012869237513334,
]).reshape((25,))

PARAMS_MEAN_STANDARD_ERROR: np.ndarray = np.array([
    3.420097735261459e-05,
    4.3457274305480214e-05,
    3.7050248199029315e-05,
    4.5287414911657496e-05,
    3.460243777043651e-05,
    3.5706654235736576e-05,
    3.073067940125073e-05,
    3.553446128397708e-05,
    4.46676478925998e-05,
    3.821909515323815e-05,
    2.96031241998345e-05,
    3.6813181721783774e-05,
    3.269194398059543e-05,
    3.866800481744103e-05,
    4.208834419023207e-05,
    3.1788064500883314e-05,
    4.0280505762498e-05,
    4.988099986812187e-05,
    4.644053633585101e-05,
    6.404150522685867e-05,
    6.698208314488012e-05,
    3.6241740192505915e-05,
    5.8282361896793195e-05,
    5.6985682447412076e-05,
    3.4377615085524085e-05,
]).reshape((25,))

PARAMS_STANDARD_DEVIATION: np.ndarray = np.array([
    0.05121504401925657,
    0.0602744899523069,
    0.05407578497469613,
    0.06185364110196099,
    0.05252348621651649,
    0.052740763379804066,
    0.04727278635577562,
    0.05282911243510687,
    0.060423236406815115,
    0.05513862003721909,
    0.04521408369454031,
    0.053722778839321514,
    0.049784806052081944,
    0.054973467241199524,
    0.06389872734551298,
    0.04795384296775531,
    0.059223430715316286,
    0.06803527863533278,
    0.06342154486946079,
    0.07973875380654005,
    0.08288763861667389,
    0.051490414006898265,
    0.07437621953907893,
    0.07237056620830591,
    0.050616854387434754,
]).reshape((25,))
