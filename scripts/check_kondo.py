"""Compare shared-language diagnostics with the pinned clj-kondo executable.

Run with: uv run python scripts/check_kondo.py
The compatibility Nix shell supplies clj-kondo. These fixtures are original;
Python interoperability has separate tests because clj-kondo does not analyze it.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

KONDO_VERSION = "2026.08.04"
FIELDS = ("type", "level", "message", "row", "col", "end-row", "end-col")
CASES = [
    ("unresolved-value", "missing\n"),
    ("unicode-column", '(do "😀" missing)\n'),
    ("unicode-multiline-column", '(do "😀\n😀" missing)\n'),
    ("unresolved-call", "(missing 1)\n"),
    ("unresolved-namespace", "(absent/value 1)\n"),
    ("core-zero-arity", "(inc)\n"),
    ("core-extra-argument", "(inc 1 2)\n"),
    ("user-function-arity", "(defn take-one [value] value)\n(take-one)\n"),
    ("unused-let-binding", "(let [unused 1] :ok)\n"),
    ("unused-function-argument", "(defn run [unused] :ok)\n"),
    ("unused-vector-binding", "(let [[used spare] [1 2]] used)\n"),
    ("unused-map-binding", "(let [{:keys [used spare]} {:used 1 :spare 2}] used)\n"),
    ("redefined-var", "(def value 1)\n(def value 2)\n"),
    ("unresolved-binding-initializer", "(let [value value] value)\n"),
    ("quoted-symbols", "'(absent/value missing)\n"),
    ("discarded-code", "#_(missing 1)\n42\n"),
    ("ignored-binding", "(let [_ignored 1] :ok)\n"),
    ("sequential-bindings", "(let [value 1 result (inc value)] result)\n"),
    ("captured-binding", "(let [value 1] ((fn [] value)))\n"),
    ("variadic-function", "(defn prepend [head & tail] (cons head tail))\n(prepend 1 2)\n"),
    ("local-function", "(let [f (fn [value] value)] (f 1))\n"),
    ("multi-arity-function", "(defn value ([] 0) ([x] x))\n(value)\n(value 1)\n"),
]

CONFIG_CASES = [('exclude-symbol', 'missing kept', '{:linters {:unresolved-symbol {:exclude [missing]}}}'),
 ('exclude-symbol-regex',
  '?query kept',
  '{:linters {:unresolved-symbol {:exclude-patterns ["^\\\\?"]}}}'),
 ('duplicate-default', 'missing missing', '{}'),
 ('duplicate-report',
  'missing missing',
  '{:linters {:unresolved-symbol {:report-duplicates true}}}'),
 ('duplicate-namespace', '(absent/a) (absent/b)', '{}'),
 ('unused-binding-regex',
  '(let [this-that 1 kept 2] :ok)',
  '{:linters {:unused-binding {:exclude-patterns ["^this"]}}}'),
 ('unused-private-exclude',
  '(defn- hidden [] :ok) (defn- kept [] :ok)',
  '{:linters {:unused-private-var {:exclude [sample/hidden]}}}'),
 ('unused-ns-exclude',
  '(ns sample (:require [unused.lib :as u]))',
  '{:linters {:unused-namespace {:exclude [unused.lib]}}}'),
 ('unused-ns-regex',
  '(ns sample (:require [unused.lib :as u]))',
  '{:linters {:unused-namespace {:exclude [".*lib$"]}}}'),
 ('unused-refer',
  '(ns sample (:require [unused.lib :refer [a b]]))',
  '{:linters {:unused-referred-var {:exclude {unused.lib [a]}} :unused-namespace {:level :off}}}'),
 ('ns-groups',
  'missing',
  '{:ns-groups [{:pattern "^sam" :name samples}] :config-in-ns {samples {:ignore '
  '[:unresolved-symbol]}}}'),
 ('ns-groups-filename',
  'missing',
  '{:ns-groups [{:filename-pattern "sample\\\\.clj$" :name samples}] :config-in-ns {samples '
  '{:ignore [:unresolved-symbol]}}}'),
 ('ns-groups-specific',
  'missing',
  '{:ns-groups [{:pattern "^sam" :name samples}] :config-in-ns {samples {:ignore '
  '[:unresolved-symbol]} sample {:linters {:unresolved-symbol {:level :warning}}}}}'),
 ('ns-deep-merge',
  'missing kept',
  '{:linters {:unresolved-symbol {:exclude [missing]}} :config-in-ns {sample {:linters '
  '{:unresolved-symbol {:exclude [kept]}}}}}'),
 ('ns-replace',
  'missing kept',
  '{:linters {:unresolved-symbol {:exclude [missing]}} :config-in-ns {sample {:linters '
  '{:unresolved-symbol {:exclude ^:replace [kept]}}}}}'),
 ('ns-metadata',
  "(ns sample {:clj-kondo/config '{:linters {:unresolved-symbol {:level :off}}}}) missing",
  '{}'),
 ('ns-metadata-ignore', '(ns sample {:clj-kondo/ignore [:unresolved-symbol]}) missing', '{}'),
 ('ns-name-metadata-ignore', '(ns ^{:clj-kondo/ignore true} sample) missing', '{}'),
 ('call-config',
  '(defn wrap [x] x) (wrap missing) kept',
  '{:config-in-call {sample/wrap {:ignore [:unresolved-symbol]}}}'),
 ('call-config-scope-unused',
  '(identity (let [x 1] :ok)) (let [y 1] :ok)',
  '{:config-in-call {clojure.core/identity {:ignore [:unused-binding]}}}'),
 ('call-config-alias',
  '(ns sample (:require [clojure.core :as c]))\n(c/identity missing) kept',
  '{:config-in-call {clojure.core/identity {:ignore [:unresolved-symbol]}}}'),
 ('call-exclude',
  '(identity missing) missing',
  '{:linters {:unresolved-symbol {:exclude [(clojure.core/identity [missing])]}}}'),
 ('call-exclude-all',
  '(identity [missing other]) kept',
  '{:linters {:unresolved-symbol {:exclude [(clojure.core/identity)]}}}'),
 ('skip-args-linter',
  '(identity (inc)) (inc)',
  '{:linters {:invalid-arity {:skip-args [clojure.core/identity]}}}'),
 ('skip-args', '(identity (inc missing other)) (inc)', '{:skip-args [clojure.core/identity]}'),
 ('skip-comments', '(comment missing (inc)) kept', '{:skip-comments true}'),
 ('tag-default', '#data [missing (inc)]', '{}'),
 ('tag-config',
  '#data [missing (inc)]',
  '{:config-in-tag {data {:linters {:unresolved-symbol {:level :error}}}}}'),
 ('discard-ignore', '#_:clj-kondo/ignore (inc) (inc)', '{}'),
 ('discard-ignore-specific', '#_{:clj-kondo/ignore [:invalid-arity]} (inc missing 2)', '{}'),
 ('fn-destructure-keys',
  '(fn [{:keys [x]}] :ok) (let [{:keys [y]} {}] :ok)',
  '{:linters {:unused-binding {:exclude-destructured-keys-in-fn-args true}}}'),
 ('destructure-as',
  '(let [{:keys [x] :as m} {} [y :as all] []] [x y])',
  '{:linters {:unused-binding {:exclude-destructured-as true}}}'),
 ('lint-as',
  '(defmacro my-let [& args] args) (my-let [x 1] x)',
  '{:lint-as {sample/my-let clojure.core/let}}'),
 ('lint-as-local-shadow',
  '(defmacro my-let [& args] args) (let [my-let (fn [x] x)] (my-let 1))',
  '{:lint-as {sample/my-let clojure.core/let}}'),
 ('inline-macro',
  "(defmacro with-x {:clj-kondo/config '{:ignore [:unresolved-symbol]}} [& args] args) (with-x "
  'missing)',
  '{}'),
 ('nested-destructure',
  '(fn [{:keys [x]}] (let [{:keys [nested]} {}] :ok))',
  '{:linters {:unused-binding {:exclude-destructured-keys-in-fn-args true}}}'),
 ('discard-binding', '(let [#_:clj-kondo/ignore unused 1] :ok)', '{}'),
 ('call-macro-report',
  '(defmacro wrap [& args] args) (wrap missing)',
  '{:config-in-call {sample/wrap {:linters {:unresolved-symbol {:level :warning}}}}}'),
 ('unresolved-ns-group',
  '(absent/value)',
  '{:ns-groups [{:pattern "^abs" :name optional}] :linters {:unresolved-namespace {:exclude '
  '[optional]}}}'),
 ('unresolved-var-exclusion',
  '(sample/missing)',
  '{:linters {:unresolved-var {:exclude [sample]}}}'),
 ('macro-inline-lint-as',
  "(defmacro my-let {:clj-kondo/lint-as 'clojure.core/let} [& args] args) (my-let [x 1] x)",
  '{}'),
 ('fn-explicit-map-destructure',
  '(fn [{x :x}] :ok)',
  '{:linters {:unused-binding {:exclude-destructured-keys-in-fn-args true}}}'),
 ('defmulti-args',
  '(defmulti f (fn [x y] x))',
  '{:linters {:unused-binding {:exclude-defmulti-args true}}}'),
 ('defmulti-nested-fn',
  '(defmulti f (fn [x y] (fn [z] :ok)))',
  '{:linters {:unused-binding {:exclude-defmulti-args true}}}'),
 ('thread-call-config',
  '(-> 1 (+ missing))',
  '{:config-in-call {clojure.core/+ {:ignore [:unresolved-symbol]}}}'),
 ('cond-thread-call-config',
  '(cond-> 1 true (inc 2))',
  '{:config-in-call {clojure.core/inc {:ignore [:invalid-arity]}}}'),
 ('thread-call-arity',
  '(-> 1 (identity missing))',
  '{:config-in-call {clojure.core/identity {:ignore true}}}')]


CONFIG_CASES.extend(
[('redundant-do-empty', '(do)', '{}'),
 ('redundant-do-one', '(do (inc 1))', '{}'),
 ('redundant-do-body', '(defn f [x] (do (println x) x))', '{}'),
 ('redundant-do-binding', '(let [x (do (println 1) 2)] x)', '{}'),
 ('redundant-do-fn-star', '(fn* [] (do 1))', '{}'),
 ('redundant-let-empty', '(let [] 1)', '{}'),
 ('redundant-let-nested', '(let [x 1] (let [y 2] (+ x y)))', '{}'),
 ('let-separate-bodies', '(let [x 1] (let [y 2] (+ x y)) x)', '{}'),
 ('cond-truthy', '(cond true 1 false 2)', '{}'),
 ('cond-keyword', '(cond :x 1 :else 2 :y 3)', '{}'),
 ('cond-false-nil', '(cond false 1 nil 2 :else 3)', '{}'),
 ('cond-missing-result', '(cond :else)', '{}'),
 ('cond-number', '(cond 1 :x)', '{}'),
 ('shadowed-fn-param', '(fn [x x] x)', '{}'),
 ('single-key-default', '(get-in {} [:a])', '{}'),
 ('single-key-enabled',
  '(get-in {} [:a]) (assoc-in {} [:a] 1) (update-in {} [:a] inc)',
  '{:linters {:single-key-in {:level :warning}}}'),
 ('redundant-let-binding',
  '(let [x 1 x x] x)',
  '{:linters {:redundant-let-binding {:level :warning}}}'),
 ('literal-number-call', '(1 2)', '{}'),
 ('literal-string-call', '("s" 1)', '{}'),
 ('literal-boolean-call', '(true 1)', '{}'),
 ('redundancy-config-off',
  '(do (let [] 1))',
  '{:linters {:redundant-do {:level :off} :redundant-let {:level :off}}}'),
 ('shadowed-cond-name', '(let [cond (fn [x] x)] (cond 1))', '{}')]
)


CONFIG_CASES.extend(
    [('one-logical', '(and true)'),
     ('one-comparison', '(= 1)'),
     ('try-without-handler', '(try 1)'),
     ('when-without-body', '(when true)'),
     ('earmuffed-nondynamic', '(def *value* 1)'),
     ('dynamic-not-earmuffed',
      '(def ^:dynamic value 1)',
      '{:linters {:dynamic-var-not-earmuffed {:level :warning}}}'),
     ('uninitialized', '(def value)'),
     ('conflicting-fixed-arity', '(fn ([x] x) ([y] y))'),
     ('case-duplicate', '(case 1 1 :a 1 :b)'),
     ('case-quoted', "(case 1 'x :a)"),
     ('case-symbol', '(case 1 x :a)', '{:linters {:case-symbol-test {:level :warning}}}'),
     ('blank-docstring', '(defn f "" [] 1)'),
     ('misplaced-docstring', '(defn f [] "wrong place" 1)'),
     ('used-underscore', '(let [_x 1] _x)', '{:linters {:used-underscored-binding {:level :warning}}}'),
     ('inline-def', '(defn f [] (def x 1))'),
     ('redundant-string', '(str "x")'),
     ('redundant-nested', '(and true (and false true))'),
     ('seq-rest', '(seq (rest [1]))', '{:linters {:seq-rest {:level :warning}}}'),
     ('not-nil', '(not (nil? 1))', '{:linters {:not-nil? {:level :warning}}}'),
     ('equals-true', '(= true 1)', '{:linters {:equals-true {:level :warning}}}'),
     ('equals-false', '(= false 1)', '{:linters {:equals-false {:level :warning}}}'),
     ('equals-nil', '(= nil 1)', '{:linters {:equals-nil {:level :warning}}}'),
     ('plus-one', '(+ 1 2)', '{:linters {:plus-one {:level :warning}}}'),
     ('minus-one', '(- 2 1)', '{:linters {:minus-one {:level :warning}}}'),
     ('equals-float', '(= 1.0 1)', '{:linters {:equals-float {:level :warning}}}'),
     ('unknown-ns-option', '(ns sample (:wat foo))'),
     ('ns-underscore', '(ns sample_name)'),
     ('redundant-declare', '(def x 1) (declare x)'),
     ('numeric-type-error', '(inc "x")'),
     ('variadic-type-error', '(+ 1 :x)'),
     ('seqable-type-error', '(first 1)'),
     ('collection-type-error', '(conj 1 2)'),
     ('string-type-error', '(subs 1 0)'),
     ('function-type-error', '(map 1 [])'),
     ('local-call-type-error', '(let [x 1] (x 2))'),
     ('nil-call-type-error', '(let [x nil] (x 2))'),
     ('inferred-parameter-type', '(defn f [x] (inc x)) (f "x")'),
     ('type-flow-binding', '(let [x "x"] (inc x))'),
     ('not-empty-sequence', '(not (empty? (list 1)))')]
)

CONFIG_CASES.extend(
    [('loop-no-recur', '(loop [x 1] x)'),
     ('destructuring-default-unbound', '(let [{:keys [x] :or {y 1}} {}] x)'),
     ('deprecated-value', '(def ^:deprecated old 1) old'),
     ('deprecated-call', '(defn ^:deprecated old [] 1) (old)'),
     ('deprecated-since', '(def ^{:deprecated "1.0"} old 1) old'),
     ('deprecated-exclusion',
      '(def ^:deprecated old 1) old',
      '{:linters {:deprecated-var {:exclude {sample/old {:namespaces [sample]}}}}}'),
     ('missing-docstring', '(defn f [] 1)', '{:linters {:missing-docstring {:level :warning}}}'),
     ('docstring-summary',
      '(defn f "No period" [] 1)',
      '{:linters {:docstring-no-summary {:level :warning}}}'),
     ('docstring-whitespace',
      '(defn f " extra " [] 1)',
      '{:linters {:docstring-leading-trailing-whitespace {:level :warning}}}'),
     ('def-of-fn', '(def f (fn [x] x))', '{:linters {:def-fn {:level :warning}}}'),
     ('shadowed-core-var', '(let [map 1] map)', '{:linters {:shadowed-var {:level :warning}}}'),
     ('core-conflicting-alias', '(ns sample (:require [clojure.core :as c] [other :as c]))'),
     ('unknown-require-option', '(ns sample (:require [clojure.core :wat []]))'),
     ('self-require', '(ns sample (:require [sample :as self]))'),
     ('duplicate-refer', '(ns sample (:require [clojure.core :refer [inc inc]]))'),
     ('consistent-alias',
      '(ns sample (:require [clojure.core :as core]))',
      '{:linters {:consistent-alias {:aliases {basilisp.core c clojure.core c}}}}'),
     ('refer-all-warning', '(ns sample (:require [clojure.core :refer :all]))'),
     ('refer-warning',
      '(ns sample (:require [clojure.core :refer [inc]]))',
      '{:linters {:refer {:level :warning}}}'),
     ('unused-alias',
      '(ns sample (:require [clojure.core :as c])) (clojure.core/inc 1)',
      '{:linters {:unused-alias {:level :warning}}}'),
     ('unused-core-exclusion', '(ns sample (:refer-clojure :exclude [map]))'),
     ('unknown-core-exclusion', '(ns sample (:refer-clojure :exclude [not-a-real-core-var]))'),
     ('unsorted-requires',
      '(ns sample (:require [z :as z] [a :as a]))',
      '{:linters {:unsorted-required-namespaces {:level :warning}}}'),
     ('protocol-missing-method',
      '(defprotocol P (m [this]) (n [this])) (deftype T [] P (m [this] nil))'),
     ('protocol-arity', '(defprotocol P (m [this])) (deftype T [] P (m [this x] x))'),
     ('protocol-unknown-method', '(defprotocol P (m [this])) (deftype T [] P (unknown [this] nil))'),
     ('protocol-varargs', '(defprotocol P (m [this & xs]))'),
     ('fn-wrapper', '(fn [x] (println x))', '{:linters {:redundant-fn-wrapper {:level :warning}}}'),
     ('anonymous-wrapper', '#(println %)', '{:linters {:redundant-fn-wrapper {:level :warning}}}'),
     ('fn-wrapper-reordered',
      '(fn [x y] (println y x))',
      '{:linters {:redundant-fn-wrapper {:level :warning}}}'),
     ('format-missing-argument', '(format "%s %s" "one")'),
     ('format-extra-argument', '(format "%s" "one" "two")'),
     ('redundant-format', '(format "plain")'),
     ('redundant-boolean', '(boolean true)'),
     ('missing-test-assertion',
      '(ns sample (:require [clojure.test :refer [deftest]])) (deftest t (= 1 2))'),
     ('assertion-message',
      '(ns sample (:require [clojure.test :refer [deftest is]])) (deftest t (is (= 1 2) :bad))')]
)

CONFIG_CASES.extend(
    [('duplicate-field', '(deftype Item [value value])'),
     ('duplicate-assoc-key', '(assoc {} :a 1 :a 2 :a 3)'),
     ('duplicate-dissoc-key', '(dissoc {} :a :a :a)'),
     ('duplicate-symbol-assoc-key', '(let [key :a] (assoc {} key 1 key 2))'),
     ('alias-only-var', '(ns sample (:require [clojure.core :as-alias c])) (c/inc 1)'),
     ('case-name-style',
      '(defn Submit [] 1) (defn submit [] 2)',
      '{:linters {:var-same-name-except-case {:level :warning}}}'),
     ('configured-map-missing',
      '(declare f) (f {})',
      '{:linters {:type-mismatch {:namespaces {sample {f {:arities {1 {:args [{:op :keys :req {:a '
      ':number}}]}}}}}}}}'),
     ('configured-map-wrong-value',
      '(declare f) (f {:a "bad"})',
      '{:linters {:type-mismatch {:namespaces {sample {f {:arities {1 {:args [{:op :keys :req {:a '
      ':number}}]}}}}}}}}'),
     ('configured-map-correct',
      '(declare f) (f {:a 1})',
      '{:linters {:type-mismatch {:namespaces {sample {f {:arities {1 {:args [{:op :keys :req {:a '
      ':number}}]}}}}}}}}'),
     ('configured-rest-pairs',
      '(declare f) (f :a 1 :b "bad")',
      '{:linters {:type-mismatch {:namespaces {sample {f {:arities {:varargs {:min-arity 0 :args [{:op '
      ':rest :spec [:keyword :number]}]}}}}}}}}')]
)

CONFIG_CASES.extend(
    [('conditional-basic',
      '(let [m {} m (if true (assoc m :a 1) m) m (if false (assoc m :b 2) m)] m)',
      '{:linters {:conditional-build-up {:level :warning}}}'),
     ('conditional-nonliteral-base',
      '(let [m (hash-map) m (if true (assoc m :a 1) m) m (if true (assoc m :b 2) m)] m)',
      '{:linters {:conditional-build-up {:level :warning}}}'),
     ('conditional-single',
      '(let [m {} m (if true (assoc m :a 1) m)] m)',
      '{:linters {:conditional-build-up {:level :warning}}}'),
     ('conditional-predicate-use',
      '(let [m {} m (if (:x m) (assoc m :a 1) m) m (if (:y m) (assoc m :b 2) m)] m)',
      '{:linters {:conditional-build-up {:level :warning}}}'),
     ('conditional-value-use',
      '(let [m {} m (if true (assoc m :a (:x m)) m) m (if true (assoc m :b 2) m)] m)',
      '{:linters {:conditional-build-up {:level :warning}}}'),
     ('conditional-shadowed-predicate',
      '(let [m {} m (if (let [m true] m) (assoc m :a 1) m) m (if (let [m true] m) (assoc m :b 2) m)] '
      'm)',
      '{:linters {:conditional-build-up {:level :warning}}}'),
     ('conditional-shadowed-assoc',
      '(let [assoc (fn [_ _ _] {}) m {} m (if true (assoc m :a 1) m) m (if true (assoc m :b 2) m)] m)',
      '{:linters {:conditional-build-up {:level :warning}}}'),
     ('conditional-break',
      '(let [m {} m (if true (assoc m :a 1) m) m (assoc m :stop 1) m (if true (assoc m :b 2) m)] m)',
      '{:linters {:conditional-build-up {:level :warning}}}'),
     ('default-same-map',
      '(let [{:keys [a b] :or {b a}} {}] [a b])',
      '{:linters {:destructured-or-binding-of-same-map {:level :warning}}}'),
     ('default-as-map',
      '(fn [{value :value :as row :or {value row}}] value)',
      '{:linters {:destructured-or-binding-of-same-map {:level :warning}}}'),
     ('default-own-fallback',
      '(let [a 1 {:keys [a] :or {a a}} {}] a)',
      '{:linters {:destructured-or-binding-of-same-map {:level :warning}}}'),
     ('default-quoted',
      "(let [{:keys [a b] :or {b 'a}} {}] [a b])",
      '{:linters {:destructured-or-binding-of-same-map {:level :warning}}}')]
)

CONFIG_CASES.extend(
    [('redundant-ignore-all', '#_:clj-kondo/ignore (inc 1)'),
     ('redundant-ignore-selective', '#_{:clj-kondo/ignore [:unused-binding]} (inc 1)'),
     ('used-ignore', '#_{:clj-kondo/ignore [:unresolved-symbol]} missing'),
     ('ignored-ignore',
      '#_{:clj-kondo/ignore [:unused-binding]} (inc 1)',
      '{:linters {:redundant-ignore {:exclude [:unused-binding]}}}'),
     ('constant-true-inferred', '(let [x (str 1)] (if x 1 2))'),
     ('constant-false-inferred', '(if (:x {}) 1 2)'),
     ('constant-nil-local', '(let [x nil] (if x 1 2))'),
     ('constant-boolean-intentional', '(if false 1 2)'),
     ('template-empty-args',
      '(ns sample (:require [clojure.template :refer [do-template]])) (do-template [] (inc 1) 2)'),
     ('template-empty-values',
      '(ns sample (:require [clojure.template :refer [do-template]])) (do-template [x] (inc x))'),
     ('template-uneven-values',
      '(ns sample (:require [clojure.template :refer [do-template]])) (do-template [x y] (+ x y) 1 2 '
      '3)'),
     ('template-valid-expanded',
      '(ns sample (:require [clojure.template :refer [do-template]])) (do-template [x] (inc x) 1 2)'),
     ('template-expanded-type',
      '(ns sample (:require [clojure.template :refer [do-template]])) (do-template [x] (inc x) "bad")'),
     ('template-expanded-unresolved',
      '(ns sample (:require [clojure.template :refer [do-template]])) (do-template [x] (println x) '
      'missing)'),
     ('template-data-replacement',
      '(ns sample (:require [clojure.template :refer [do-template]])) (do-template [:slot] (inc :slot) '
      '1)')]
)

CONFIG_CASES.extend(
    [('shared-string-result', '(ns sample (:require [clojure.string :as s])) (inc (s/join [1 2]))'),
     ('shared-set-result', '(ns sample (:require [clojure.set :as s])) (inc (s/rename [] {}))'),
     ('configured-rest-last',
      '(declare f) (f 1 2 "end")',
      '{:linters {:type-mismatch {:namespaces {sample {f {:arities {:varargs {:args [{:op :rest :spec '
      ':number :last :string}]}}}}}}}}'),
     ('configured-rest-incomplete',
      '(declare f) (f :a)',
      '{:linters {:type-mismatch {:namespaces {sample {f {:arities {:varargs {:args [{:op :rest :spec '
      '[:keyword :number]}]}}}}}}}}'),
     ('configured-number-widening',
      '(declare f) (f 1)',
      '{:linters {:type-mismatch {:namespaces {sample {f {:arities {1 {:args [:double]}}}}}}}}'),
     ('configured-key-ret',
      '(declare f) (inc (:a (f)))',
      '{:linters {:type-mismatch {:namespaces {sample {f {:arities {0 {:ret {:op :keys :req {:a '
      ':string}}}}}}}}}}'),
     ('repeated-ignored-param', '(fn [_ _ _] 1)')]
)

CONFIG_CASES.extend([
    ("aliased-namespace-symbol", '(ns sample (:require [clojure.string :as s])) (clojure.string/join ["a"])',
     '{:linters {:aliased-namespace-symbol {:level :warning}}}'),
    ("aliased-namespace-symbol-excluded", '(ns sample (:require [clojure.string :as s])) (clojure.string/join ["a"])',
     '{:linters {:aliased-namespace-symbol {:level :warning :exclude [clojure.string basilisp.string]}}}'),
    ("syntax-quoted-alias", '(ns sample (:require [clojure.string :as s])) `s/join'),
    ("syntax-quoted-referred", '(ns sample (:require [clojure.string :refer [join]])) `join'),
])


def normalize(findings, source=None):
    # Normalize equivalent standard-library namespace names and their source widths.
    result = []
    for finding in findings:
        item = {field: finding.get(field) for field in FIELDS}
        item["message"] = item["message"].replace("clojure.core", "basilisp.core").replace("clojure.test", "basilisp.test").replace("clojure.template", "basilisp.template").replace("clojure.string", "basilisp.string").replace("clojure.set", "basilisp.set")
        if source is not None:
            # Replacing the language namespace changes its width by one column.
            lines = source.splitlines()
            for row_key, column_key in (("row", "col"), ("end-row", "end-col")):
                row, column = item[row_key], item[column_key]
                if row and column and row <= len(lines):
                    line = lines[row - 1]
                    delta = sum(1 for match in re.finditer(r"clojure\.(?:core|test|template|string|set)", line)
                                if len(line[:match.end()].encode("utf-16-le")) // 2 < column)
                    item[column_key] += delta
        result.append(item)
    return sorted(result, key=lambda item: (
        item["row"] or 0, item["col"] or 0, item["type"], item["message"]
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clj-kondo", default="clj-kondo", help="Path to the pinned executable.")
    parser.add_argument("--report", type=Path, help="Write all comparisons as JSON.")
    args = parser.parse_args()
    version = subprocess.check_output([args.clj_kondo, "--version"], text=True).strip()
    if version != f"clj-kondo v{KONDO_VERSION}":
        parser.error(f"Expected clj-kondo v{KONDO_VERSION}, found {version}")

    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap

    checker = importlib.import_module("basilisp_tools.check")

    def plain_findings(result):
        return [
            {str(key).removeprefix(":"): (
                str(value).removeprefix(":") if str(key) in (":type", ":level") else value
            ) for key, value in finding.items()}
            for finding in result.val_at(kw("findings"))
        ]

    comparisons = []
    with tempfile.TemporaryDirectory(prefix="blt-kondo-") as temporary:
        config_directory = Path(temporary) / ".clj-kondo"
        config_directory.mkdir()
        (config_directory / "config.edn").write_text(
            "{:config-paths ^:replace []}", encoding="utf-8"
        )
        environment = dict(os.environ)
        environment.pop("CLJ_KONDO_EXTRA_CONFIG_DIR", None)
        for case in [*CASES, *CONFIG_CASES]:
            name, body, *configs = case
            config = configs[0] if configs else "{}"
            source = body if body.startswith("(ns ") else "(ns sample)\n" + body
            filename = "sample.clj"
            oracle = subprocess.run([
                args.clj_kondo, "--lint", "-", "--lang", "clj", "--filename", filename,
                "--cache", "false", "--repro", "--config-dir", str(config_directory),
                "--config", config, "--config", "{:output {:format :json}}",
            ], input=source, text=True, capture_output=True, env=environment, cwd=temporary)
            if oracle.returncode not in (0, 2, 3):
                raise RuntimeError(f"clj-kondo failed for {name}: {oracle.stderr}")
            expected = normalize(json.loads(oracle.stdout)["findings"], source)
            try:
                with patch.dict(os.environ, environment, clear=True):
                    result = checker.run(lmap({
                        kw("stdin"): source.replace("clojure.core", "basilisp.core").replace("clojure.test", "basilisp.test").replace("clojure.template", "basilisp.template").replace("clojure.string", "basilisp.string").replace("clojure.set", "basilisp.set"),
                        kw("filename"): filename, kw("config"): checker.read_config(config),
                        kw("config-dir"): str(config_directory), kw("repro"): True,
                        kw("python-inspection?"): False,
                    }))
                actual = normalize(plain_findings(result))
                status = checker.exit_status(result)
                comparison = {
                    "name": name, "source": source, "config": config, "expected": expected, "actual": actual,
                    "expected_exit": oracle.returncode, "actual_exit": status,
                    "match": expected == actual and oracle.returncode == status,
                }
            except Exception as error:
                comparison = {
                    "name": name, "source": source, "expected": expected,
                    "exception": repr(error), "match": False,
                }
            comparisons.append(comparison)
    failed = [case for case in comparisons if not case["match"]]
    if args.report:
        args.report.write_text(json.dumps({
            "clj_kondo_version": KONDO_VERSION, "cases": comparisons,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for case in failed:
        print(json.dumps(case, ensure_ascii=False, sort_keys=True))
    print(f"clj-kondo diagnostics: {len(comparisons) - len(failed)}/{len(comparisons)} matched.")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
