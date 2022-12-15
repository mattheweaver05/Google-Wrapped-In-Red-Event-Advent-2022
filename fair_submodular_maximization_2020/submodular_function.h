// Copyright 2020 The Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//
// Submodular Function
//
// Inherit from this to define your own submodular function
// the function should keep a current set S as its state.

#ifndef FAIR_SUBMODULAR_MAXIMIZATION_2020_SUBMODULAR_FUNCTION_H_
#define FAIR_SUBMODULAR_MAXIMIZATION_2020_SUBMODULAR_FUNCTION_H_

#include <cstdint>
#include <limits>
#include <memory>
#include <string>
#include <vector>

#include "utilities.h"

class SubmodularFunction {
 public:
  static int64_t oracle_calls_;

  virtual ~SubmodularFunction() {}

  // Sets S = empty set.
  virtual void Reset() = 0;

  // Initiation function for this submodular function.
  virtual std::vector<std::pair<int, int>> Init(std::string expriment_name = {}) = 0;

  // Return the objective value of a set 'S' and also increases oracle_calls.
  double ObjectiveAndIncreaseOracleCall(
      const std::vector<std::pair<int, int>>& elements);

  // Adds element 'e' to the function using Add(e) and also increases
  // oracle_calls.
  void AddAndIncreaseOracleCall(std::pair<int, int> element);

  // Returns the delta by adding this element using Delta(e) and also increases
  // oracle_calls.
  double DeltaAndIncreaseOracleCall(std::pair<int, int> element);

  // Add element if and only if its contribution is >= thre and also increases
  // oracle_calls. Return the contribution increase.
  double AddAndIncreaseOracleCall(std::pair<int, int> element, double thre);

  // Returns the universe of the utility function. The first is the name of the
  // element and the second is its color.
  virtual const std::vector<std::pair<int, int>>& GetUniverse() const = 0;

  // Get name of utility function.
  virtual std::string GetName() const = 0;

  // Clone the object (see e.g. GraphUtility for an example).
  virtual std::unique_ptr<SubmodularFunction> Clone() const = 0;

  // Returns the guess of the optimum solution.
  std::vector<double> GetOptEstimates(int cardinality_k);

 protected:
  // Adds a new element to set S.
  virtual void Add(std::pair<int, int> element) = 0;

  // Computes f(S u {e}) - f(S).
  virtual double Delta(std::pair<int, int> element) = 0;

  // Computes f(S).
  virtual double Objective(
      const std::vector<std::pair<int, int>>& elements) const = 0;
};

#endif  // FAIR_SUBMODULAR_MAXIMIZATION_2020_SUBMODULAR_FUNCTION_H_
