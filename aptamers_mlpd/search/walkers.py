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

# Lint as: python3
"""Classes used for exploring around a given seed, using different algorithms.

"""

import random


import numpy as np

from ..utils import dna
from ..search import search
from ..search import search_pb2


class Error(Exception):
  pass


class Mutant:
  """Information about a mutant sequence.

  Equality is based on the sequence while sorting is based on the score.
  """

  def __init__(self, sequence, parent, seed, score):
    self.sequence = sequence
    self.parent = parent
    self.seed = seed
    self.score = score

  def __eq__(self, other):
    return self.sequence == other.sequence

  def __hash__(self):
    return hash(self.sequence)

  def __repr__(self):
    return '%s (from %s): %.3f' % (self.sequence, self.seed, self.score)


def sort_by_score(iterable):
  """Sorts a collection of objects by their score attribute, descending.

  Args:
    iterable: iterable collection of objects with a score attribute.
  Returns:
    The collection sorted by score descending.
  """
  return sorted(iterable, key=lambda x: x.score, reverse=True)


def mutate_seq(seq, num_mutations, random_state):
  """Makes num_mutation different point mutations to the input sequence.

  The mutations will all be in different positions (i.e. the returned
  sequence will be hamming distance of num_mutations from the initial sequence).

  TODO(mdimon): add functionality for indels (or write as a separate function).

  Args:
    seq: String sequence to mutate
    num_mutations: Integer number of mutations to make
    random_state: numpy RandomState object
  Returns:
     The string of the mutated sequence.
  """

  # FYI: This approach takes ~7 seconds for 10 seeds with a set of params.
  # The random_state.choice approach takes ~19 seconds for the same task:
  #   mutation_positions = random_state.choice(position_list,
  #                                            size=num_mutations,
  #                                            replace=False)
  # (The original random.sample approach takes ~7 seconds)
  #   mutation_positions = random.sample(position_list, num_mutations)
  position_list = list(range(len(seq)))
  random_state.shuffle(position_list)
  mutation_positions = position_list[:num_mutations]

  new_base_list = list(seq)
  for i in mutation_positions:
    c = new_base_list[i]
    new_base_list[i] = random_state.choice(
        [base for base in dna.DNA_BASES if base != c])
  return ''.join(new_base_list)


class AbstractWalker:
  """Defines a way to use a trained model to walk to better scoring mutants."""

  def generate_mutants(self, seeds, generation_counts):
    """Use one model and one method of 'walking' to get mutations from seeds.

    Args:
      seeds: String list of seed sequences to start the search from.
      generation_counts: Dictionary where key is the generation number
        and value is the number of sequences to pick for that generation.

    Returns:
      List of aptamer choice protos.
    """
    raise NotImplementedError


class SamplingWalker(AbstractWalker):
  """A greedy local sampler."""

  def __init__(self,
               inferer,
               model_name,
               sampling_limit,
               max_trials,
               target_molecule,
               n_parents=10,
               min_distance=1,
               max_distance=4,
               random_seed=None):
    """Walker that samples by generating many random mutations and picking best.

    Args:
      inferer: The eval_feedforward Inferer (trained model) to score mutations.
        If no inferer is passed in then sequences will be scored randomly, i.e.
        a random walk will be performed.
      model_name: String name of the model for the inferer. This is set on all
        choice protos generated by this walker for downstream analysis.
      sampling_limit: The number of mutations to sample before picking best.
      max_trials: The maximum number of times to try to make sampling_limit
        worth of unique mutations.
      target_molecule: String name of the target molecule (i.e. 'target' or
          'serum' or 'Her2').
      n_parents: For each step out, the number of parents from the previous
        generation to use as the base for the next set of mutations.
      min_distance: When generating the next generation of mutants, the
        minimum distance (number of mutations) from parent to child.
      max_distance: When generating the next generation of mutants, the
        maximum distance (number of mutations) from parent to child.
      random_seed: Optional integer to be used as the random seed during the
        sequence mutation.
    """
    self.inferer = inferer
    self.model_name = model_name
    self.target_mol = target_molecule
    self.method_type = search_pb2.METHOD_SIMPLE_SAMPLING
    self.sampling_limit = sampling_limit
    self.max_trials = max_trials
    self.n_parents = n_parents
    self.random_state = np.random.RandomState(random_seed)
    self.min_distance = min_distance
    self.max_distance = max_distance

  def generate_mutants(self, seeds, generation_counts):
    """Creates generation_counts of mutants for each seed.

    Args:
      seeds: String list of seed sequences to start the search from.
      generation_counts: Dictionary where key is the generation number
        and value is the number of sequences to pick for that generation for
        each seed sequence.
    Returns:
      A list of choice protos for the generated mutants.
    """
    mutant_protos = []

    for seed in seeds:
      mutant_protos.extend(
          self.generate_one_seed_mutants(seed, generation_counts))

    return mutant_protos

  def generate_one_seed_mutants(self, seed, generation_counts):
    """Generate the mutants for one seed.

    Args:
      seed: String sequence to mutate.
      generation_counts: Number of mutants to make at each step away.
    Returns:
      List of choice protos describing the mutants.
    """
    # dictionary tracks string sequences to avoid picking again and
    # (minus this initial seed) the sequences to return.
    # Values are the AptamerChoice protos to return if this is a mutation
    selected_seq_set = set([seed])
    mutant_protos = []

    parents = [Mutant(seed, seed, seed, None)]
    # each generation is a short walk from a number of parents. The first
    # generation walks from the seed sequence. Subsequent generations start from
    # the top parents from the previous generation.
    for i in range(max(generation_counts) + 1):
      # we want to keep the top sequences we'll need
      if i in generation_counts:
        n_sequences = generation_counts[i]
      else:
        n_sequences = 0
      n_to_keep = max(n_sequences, self.n_parents)
      mutants = self.one_generation(n_to_keep, parents, selected_seq_set)

      for mutant in mutants[:n_sequences]:
        mutant_protos.append(
            search.create_choice_pb(
                mutant.sequence,
                search_pb2.Choice.SOURCE_INVENTED,
                search_pb2.Choice.NOT_CONTROL,
                mutation_step=i+1,
                previous_sequence=mutant.parent,
                seed_sequence=mutant.seed,
                model_score=mutant.score,
                model_name=self.model_name,
                mutation_type=self.method_type))
        selected_seq_set.add(mutant.sequence)

      # new parents = top k picked sequences
      parents = mutants[:self.n_parents]

    # selected_seqs includes seed and mutants, but only return mutants
    return mutant_protos

  def one_generation(self, n_sequences, parents, to_avoid):
    """Returns n_sequences mutations from the parent sequences.

    If there is more than 1 parent, all parents will be used equally as seeds.

    The distance for each mutant child will be in the range min_distance to
    max_distance passed into the constructor.

    Args:
      n_sequences: Integer number of mutant sequences to generate
      parents: A list of string sequences to start from.
      to_avoid: A container (preferably with constant time look-ups) of
        string sequence to not return.

    Returns:
      Two lists: First, a string list of mutated sequences of length
         n_sequences, sorted by model score. Second a float list of scores.
    """

    # make mutants until we have enough to sample
    mutant_set = set()
    attempts = 0
    while (len(mutant_set) < self.sampling_limit and
           attempts < self.max_trials):
      # allow multiple starting points, make attempts at each parent equally
      attempts += 1
      parent = parents[attempts % len(parents)]
      distance = random.randint(self.min_distance, self.max_distance)
      mutant_seq = mutate_seq(parent.sequence, distance, self.random_state)
      if mutant_seq not in to_avoid:
        # create with empty score for now so we can store it with the rest
        mutant_set.add(Mutant(mutant_seq, parent.sequence, parent.seed, 100000))

    # score them all
    mutant_seqs = [x.sequence for x in mutant_set]
    if self.inferer:
      mutant_scores = self.inferer.get_affinities_for_sequences(
          mutant_seqs, self.target_mol)
    else:
      mutant_scores = self.random_state.rand(len(mutant_seqs))

    # sort the sequences based on their model scores
    for mutant in mutant_set:
      mutant.score = mutant_scores[mutant_seqs.index(mutant.sequence)]

    mutants = sort_by_score(mutant_set)
    return mutants[:n_sequences]


class GeneticWalker(AbstractWalker):
  """Finds better scoring aptamers using a genetic algorithm."""

  def __init__(self,
               inferer,
               model_name,
               n_possible_parents,
               n_parents,
               target_molecule,
               min_distance,
               max_distance,
               random_seed=None):
    """Walker that samples by generating many random mutations and picking best.

    Args:
      inferer: The eval_feedforward Inferer (trained model) to score mutations.
      model_name: String name of the model for the inferer. This is set on all
        choice protos generated by this walker for downstream analysis.
      n_possible_parents: Integer number of top sequences to use as possible
        parents. From this set, n_children sequences will be selected and each
        will have 1 child.
      n_parents: Integer number of actual parents to select from possible.
      target_molecule: String name of the target molecule (i.e. 'target' or
          'serum' or 'Her2').
      min_distance: When generating the next generation of mutants, the
        minimum distance (number of mutations) from parent to child.
      max_distance: When generating the next generation of mutants, the
        maximum distance (number of mutations) from parent to child.
      random_seed: Optional integer to be used as the random seed during the
        sequence mutation.
    """
    self.inferer = inferer
    self.model_name = model_name
    self.target_mol = target_molecule
    self.n_parents = n_parents
    self.n_possible_parents = n_possible_parents
    self.min_distance = min_distance
    self.max_distance = max_distance
    self.method_type = search_pb2.METHOD_GENETIC_ALGORITHM
    self.random_state = np.random.RandomState(random_seed)

  def generate_mutants(self, seeds, generation_counts):
    """Creates mutations from the population seeds.

    NOTE: HERE GENERATION_COUNTS IS ACROSS POPULATION while is it per-seed
    for the SamplingWalker

    Genetic algorithm: use seeds as the starting population. Each generation,
    pick some of the population to make children (weighted towards better
    sequences) then trim back to the original length of seeds.

    Recombination doesn't really make sense here because it would ruin
    secondary structure, so each child will have 1 parent instead of 2.
    Given that, we don't want to replace parent with child, but instead
    leave both in the population.

    Args:
      seeds: List of string sequences to use for the starting population.
      generation_counts: Dictionary where the key is the generation number and
        the value is the number of sequences across the whole population to
        save for that generation number.
    Returns:
      List of choice protos for the mutants.
    """

    mutant_protos = []
    if self.inferer:
      seed_scores = self.inferer.get_affinities_for_sequences(seeds,
                                                              self.target_mol)
    else:
      seed_scores = self.random_state.rand(len(seeds))

    # TODO(mdimon): think about converting to a dictionary here. Then each
    #   sequence could only exist once (have to think about whether the
    #   parent or child should be kept in those cases).
    population = []
    for (seed_seq, seed_score) in zip(seeds, seed_scores):
      population.append(Mutant(seed_seq, seed_seq, seed_seq, seed_score))
    population = sort_by_score(population)

    # per generation
    for generation_number in range(max(generation_counts) + 1):
      # let the best sequences be parents
      children = self.make_children(population[:self.n_possible_parents])

      # combine and re-sort
      population.extend(children)
      population = sort_by_score(population)

      # save the top examples
      if generation_number in generation_counts:
        for p in population[:generation_counts[generation_number]]:
          mutant_protos.append(
              search.create_choice_pb(
                  p.sequence,
                  search_pb2.Choice.SOURCE_INVENTED,
                  search_pb2.Choice.NOT_CONTROL,
                  mutation_step=generation_number,
                  previous_sequence=p.parent,
                  seed_sequence=p.seed,
                  model_score=p.score,
                  model_name=self.model_name,
                  mutation_type=self.method_type))

    return mutant_protos

  def make_children(self, possible_parents):
    """Makes one generation worth of children.

    Args:
      possible_parents: List of Mutants to possibly be selected as parents.
    Returns:
      List of Mutants to be children.
    """
    raw_scores = [x.score for x in possible_parents]
    epsilon = 1e-5
    scores = [x - min(raw_scores) + epsilon for x in raw_scores]
    score_to_probability = [(float(x) / sum(scores)) for x in scores]
    parents = self.random_state.choice(
        possible_parents,
        self.n_parents,
        replace=False,
        p=score_to_probability)

    # code is awkward, but it's much faster to batch the scoring
    child_seqs = [
        mutate_seq(p.sequence,
                   random.randint(self.min_distance, self.max_distance),
                   self.random_state) for p in parents
    ]
    if self.inferer:
      child_scores = self.inferer.get_affinities_for_sequences(child_seqs,
                                                               self.target_mol)
    else:
      child_scores = self.random_state.rand(len(child_seqs))

    children = []
    for i in range(len(parents)):
      children.append(
          Mutant(child_seqs[i], parents[i].sequence, parents[i].seed,
                 child_scores[i]))
    return children
