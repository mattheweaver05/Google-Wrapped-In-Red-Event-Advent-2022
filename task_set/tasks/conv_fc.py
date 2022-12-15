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
"""Convolutional neural networks with a fully connected network."""

from typing import Dict, Text, Any
import numpy as np
import sonnet as snt

from task_set import registry
from task_set.tasks import base
from task_set.tasks import utils
import tensorflow.compat.v1 as tf

ConvFCConfig = Dict[Text, Any]


@registry.task_registry.register_sampler("conv_fc_family")
def sample_conv_fc_family_cfg(seed):
  """Sample a task config for a conv net with a fully connected layer on top.

  These configs are nested python structures that provide enough information
  to create an instance of the problem.

  Args:
    seed: int Random seed to generate task from.

  Returns:
    A nested dictionary containing a configuration.
  """
  rng = np.random.RandomState(seed)
  cfg = {}
  layer_choices = [1, 2, 3, 4, 5]
  max_layer = np.max(layer_choices)
  n_layers = rng.choice(layer_choices)
  # pattern for how strides are chosen. Either all 2s, repeated 1,2
  # or repeated 2,1.
  stride_pattern = rng.choice(["all_two", "one_two", "two_one"])
  if stride_pattern == "all_two":
    cfg["strides"] = ([2] * max_layer)[0:n_layers]
  elif stride_pattern == "one_two":
    cfg["strides"] = ([1, 2] * max_layer)[0:n_layers]
  elif stride_pattern == "two_one":
    cfg["strides"] = ([2, 1] * max_layer)[0:n_layers]
  cfg["strides"] = list(zip(cfg["strides"], cfg["strides"]))
  cfg["hidden_units"] = [
      utils.sample_log_int(rng, 8, 64) for _ in range(n_layers)
  ]

  cfg["activation"] = utils.sample_activation(rng)
  cfg["w_init"] = utils.sample_initializer(rng)
  cfg["padding"] = [
      str(rng.choice([snt.SAME, snt.VALID])) for _ in range(n_layers)
  ]

  n_fc_layers = rng.choice([0, 1, 2, 3])
  cfg["fc_hidden_units"] = [
      utils.sample_log_int(rng, 32, 128) for _ in range(n_fc_layers)
  ]

  cfg["use_bias"] = bool(rng.choice([True, False]))
  cfg["dataset"] = utils.sample_image_dataset(rng)
  cfg["center_data"] = bool(rng.choice([True, False]))
  return cfg


@registry.task_registry.register_getter("conv_fc_family")
def get_conv_fc_family(cfg):
  """Get a task for the given cfg.

  Args:
    cfg: config specifying the model generated by `sample_conv_fc_family_cfg`.

  Returns:
    A task for the given config.
  """

  act_fn = utils.get_activation(cfg["activation"])
  w_init = utils.get_initializer(cfg["w_init"])
  init = {"w": w_init}
  hidden_units = cfg["hidden_units"]

  dataset = utils.get_image_dataset(cfg["dataset"])

  def _build(batch):
    """Builds the sonnet module."""
    image = utils.maybe_center(cfg["center_data"], batch["image"])

    net = snt.nets.ConvNet2D(
        hidden_units,
        kernel_shapes=[(3, 3)],
        strides=cfg["strides"],
        paddings=cfg["padding"],
        activation=act_fn,
        use_bias=cfg["use_bias"],
        initializers=init,
        activate_final=True)(
            image)

    num_classes = batch["label_onehot"].shape[1]
    fc_hidden = cfg["fc_hidden_units"] + [num_classes]
    net = snt.BatchFlatten()(net)
    logits = snt.nets.MLP(fc_hidden, initializers=init, activation=act_fn)(net)

    loss_vec = tf.nn.softmax_cross_entropy_with_logits_v2(
        labels=batch["label_onehot"], logits=logits)

    return tf.reduce_mean(loss_vec)

  return base.DatasetModelTask(lambda: snt.Module(_build), dataset)
