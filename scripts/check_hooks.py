"""Compare supported restricted hook behavior with the pinned clj-kondo."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HOOK = r"""
(ns hooks.compat (:refer-clojure :exclude [delay] :rename {count size})
  (:require [clj-kondo.hooks-api :as api] [clojure.string :as str :refer [join] :rename {join join-words}]))
(defn with-bound [{:keys [node]}]
  (let [[_ bindings & body] (:children node)]
    {:node (api/list-node (list* (api/token-node 'let) bindings body))}))
(defn validate [{:keys [node]}]
  (let [value (second (:children node))]
    (when-not (api/keyword-node? value)
      (api/reg-finding! (assoc (meta value) :type :example/keyword
                               :message "Expected a keyword")))))
(defn discard [_] {:node (api/token-node nil)})
(defn context [{:keys [node]}]
  (api/reg-finding! (assoc (meta node) :type :example/location
                           :message (str (:name (api/resolve {:name 'inc :call true}))
                                         "|" (vec (sort (keys (api/env))))
                                         "|" (mapv :name (api/callstack))))))
(defn increment [{:keys [node]}]
  {:node (api/list-node [(api/token-node 'inc) (second (:children node))])})
(defmacro bind [bindings & body] `(let ~bindings ~@body))
(defmacro hygienic [x] `(let [value# ~x] (inc value#)))
(def ^:dynamic *factor* 2)
(defn factor [] *factor*)
(defn pattern [{:keys [node]}]
  (let [mode (api/sexpr (second (:children node)))
        value (case mode
                :transients (persistent! (conj! (transient [1]) 2))
                :volatiles (let [v (volatile! 1)] [(vswap! v inc) (vreset! v 4) @v])
                :exception-hierarchy (try (assert false) (catch Exception _ :wrong) (catch AssertionError _ :right))
                :case-false (case false (true false) :yes :no)
                :case-missing (try (case :none :other 1) (catch Exception _ :missing))
                :defonce (do (defonce retained nil) (defonce retained 2) retained)
                :function-metadata ((fn ^String [x] x) "ok")
                :preconditions (try ((fn [x] {:pre [(pos? x)]} x) 0) (catch AssertionError _ :pre))
                :postconditions (try ((fn [x] {:post [(pos? %)]} x) 0) (catch AssertionError _ :post))
                :unicode-printer ["é" "K" "😀" "line\\nfeed"]
                :nested-quote ``(inc 1)
                :nested-unquote (let [x 3] ``(inc ~~x))
                :nested-vector (let [x 3] ``[a ~x])
                :quoted-quote `'x
                :nested-empty ``()
                :dotimes (let [out (atom [])] (dotimes [i 3] (swap! out conj i)) @out)
                :doto @(doto (atom 0) (swap! inc) (swap! inc))
                :condp (condp = 2 1 :a 2 :b :fallback)
                :condp-default (condp = 9 1 :a 2 :b :fallback)
                :condp-indirect (condp some [1 2] #{2} :>> vector :none)
                :lazy-seq (let [calls (atom 0) xs (lazy-seq (swap! calls inc) [1 2])] [(first xs) (vec xs) @calls])
                :lazy-cat (let [calls (atom 0) xs (lazy-cat [1] (do (swap! calls inc) [2]))] [(first xs) @calls (vec xs) @calls])
                :delay (let [calls (atom 0) d (clojure.core/delay (swap! calls inc))] [(realized? d) (force d) (force d) @calls (realized? d)])
                :comment [(comment missing unknown) 1]
                :dynamic-binding [(factor) (binding [*factor* 9] (factor)) (factor)]
                :with-redefs [(factor) (with-redefs [factor (fn [] 7)] (factor)) (factor)]
                :helpers [((some-fn odd? even?) 2) ((every-pred pos? even?) 2) (reduce (fn [a x] (if (> x 2) (reduced a) (+ a x))) 0 [1 2 3 4])]
                :anon-rest (#(vector % %2 %&) 1 2 3 4)
                :rest-only (#(vec %&) 1 2)
                :anon-arity (try (#(+ % %2) 1) (catch Exception _ :arity))
                :gensym-distinct (let [a `x# b `x#] (not= a b))
                :gensym-shared (let [[a b] `[x# x#]] (= a b))
                :gensym-repeated (let [f (fn [] `x#)] (= (f) (f)))
                :shadow-macro (let [when inc] (when 1))
                :qualified-macro (let [when inc] (clojure.core/when true 1))
                :shadow-cond (let [cond inc] (cond 1))
                :named-shadow ((fn when [x] (inc x)) 1)
                :renamed-core (size [1 2])
                :renamed-refer (join-words "," ["a" "b"])
                :qualify-renamed-core `size
                :qualify-renamed-refer `join-words
                :qualify-excluded `delay
                :qualify-core `inc
                :qualify-global `with-bound
                :qualify-alias `str/join
                :reader-default #?(:default :first :clj :second)
                :reader-splice [0 #?@(:clj [1 2]) 3]
                :reader-unmatched [0 #?(:cljs 1) 2]
                :reader-splice-default [0 #?@(:default [1 2]) 3]
                :local-resolve (api/resolve {:name 'inc :call true})
                :unicode (vec (re-seq #"\p{Lu}+" "ABC déf GHI"))
                :groups (re-matches #"(a)(b)?" "a")
                :flags [(boolean (re-find #"(?i)k" "K")) (boolean (re-find #"(?iu)k" "K"))]
                :quote (re-find (re-pattern "\\Q[a-z]\\E") "x[a-z]y")
                :matcher (let [m (re-matcher #"([0-9]+)" "a12b34")]
                           [(re-find m) (re-groups m) (re-find m) (re-find m)])
                :zero-width (vec (re-seq #"x*" "xy"))
                :split (str/split "a,,b," #"(,)" -1)
                :replacement (str/replace "a1 b2" #"([a-z])([0-9])" "$2-$1")
                :named-replacement (str/replace "ab" #"(?<part>a)(b)" "${part}-$2")
                :function-replacement (str/replace "a1 b2" #"([a-z])([0-9])"
                                                    (fn [[_ letter number]] (str number letter)))
                :first-replacement (str/replace-first "a1 b2" #"([a-z])([0-9])" "$2$1")
                :quoted-replacement (str/replace "a" #"a" (str/re-quote-replacement "$1\\"))
                :node-regex (api/sexpr (nth (:children node) 2)))]
    (api/reg-finding! (assoc (meta node) :type :example/location :message (pr-str value)))))

(defn inspect [{:keys [node]}]
  (when-not (api/generated-node? (second (:children node)))
    (api/reg-finding! (assoc (meta (second (:children node)))
                             :type :example/location :message "Original node"))))
"""
CONFIG = """{:hooks {:analyze-call {user/with-bound hooks.compat/with-bound
                                    user/validate hooks.compat/validate
                                    user/discard hooks.compat/discard
                                    user/increment hooks.compat/increment
                                    user/inspect hooks.compat/inspect
                                    user/context hooks.compat/context
                                    user/pattern hooks.compat/pattern}
                    :macroexpand {user/bind hooks.compat/bind user/hygienic hooks.compat/hygienic}}
             :linters {:example/keyword {:level :warning}
                       :example/location {:level :info}}
             :output {:format :json :summary false}}"""
CASES = [
    ("binding", "(with-bound [x 1] (inc x))"),
    ("unused-binding", "(with-bound [x 1] 2)"),
    ("unknown-in-body", "(with-bound [x 1] (+ x missing))"),
    ("nested-hooks", "(with-bound [x 1] (with-bound [y x] y))"),
    ("arity", "(with-bound [x 1] (inc x 2))"),
    ("valid-keyword", "(validate :ok)"),
    ("custom-finding", "(validate 1)"),
    ("original-location", "(inspect :keyword)"),
    ("discard", "(discard not-defined)"),
    ("context-api", "(with-bound [x 1] (context x))"),
    ("macroexpand-binding", "(bind [x 1] (inc x))"),
    ("macroexpand-unused", "(bind [x 1] 2)"),
    ("macroexpand-unknown", "(bind [x 1] (+ x absent))"),
]
CASES += [(f"regex-{mode}", f"(pattern :{mode})") for mode in (
    "unicode", "groups", "flags", "quote", "matcher", "zero-width", "split", "replacement",
    "named-replacement", "function-replacement", "first-replacement", "quoted-replacement")]
CASES.append(("regex-node-regex", r'(pattern :node-regex #"\p{Lu}+")'))
CASES += [(f"language-{mode}", f"(pattern :{mode})") for mode in (
    "anon-rest", "rest-only", "anon-arity", "gensym-distinct", "gensym-shared", "gensym-repeated",
    "shadow-macro", "qualified-macro", "shadow-cond", "named-shadow", "qualify-core",
    "qualify-global", "qualify-alias", "reader-default", "reader-splice", "reader-unmatched",
    "reader-splice-default", "renamed-core", "renamed-refer", "qualify-renamed-core",
    "qualify-renamed-refer", "qualify-excluded")]
CASES.append(("resolve-local", "(let [inc 1] (pattern :local-resolve) inc)"))
CASES.append(("syntaxquote-hygiene", "(let [inc (fn [] 0)] (hygienic 1))"))
CASES += [(f"extended-{mode}", f"(pattern :{mode})") for mode in ['transients', 'volatiles', 'exception-hierarchy', 'case-false', 'case-missing', 'defonce', 'function-metadata', 'preconditions', 'postconditions', 'unicode-printer', 'nested-quote', 'nested-unquote', 'nested-vector', 'quoted-quote', 'nested-empty', 'dotimes', 'doto', 'condp', 'condp-default', 'condp-indirect', 'lazy-seq', 'lazy-cat', 'comment', 'dynamic-binding', 'with-redefs', 'helpers']]
KEYS = ("type", "level", "message", "row", "col", "end-row", "end-col")


def findings(command, source):
    result = subprocess.run(command, input=source, text=True, capture_output=True, cwd=ROOT)
    try:
        output = json.loads(result.stdout)
    except ValueError as error:
        raise AssertionError(f"{command}: {result.returncode}\n{result.stdout}\n{result.stderr}") from error
    projected = [{key: finding.get(key) for key in KEYS} for finding in output["findings"]]
    for finding in projected:
        finding["message"] = finding["message"].replace("basilisp.core/", "clojure.core/")
    return projected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clj-kondo", default="clj-kondo")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="blt-hook-oracle-") as temporary:
        directory = Path(temporary)
        (directory / "hooks").mkdir()
        (directory / "hooks" / "compat.clj").write_text(HOOK)
        (directory / "config.edn").write_text(CONFIG)
        upstream = [args.clj_kondo, "--lint", "-", "--lang", "clj", "--config-dir", str(directory)]
        actual = [str(ROOT / ".venv/bin/blt"), "check", "--lint", "-", "--config-dir", str(directory), "--format", "json"]
        for name, source in CASES:
            source = "(declare with-bound validate discard increment inspect bind hygienic context pattern)" + chr(10) + source
            expected = findings(upstream, source)
            observed = findings(actual, source)
            assert observed == expected, f"{name}\nexpected={expected}\nactual={observed}"
            print(f"PASS {name}", flush=True)
    print(f"Compared {len(CASES)} hook cases with clj-kondo.")


if __name__ == "__main__":
    main()
