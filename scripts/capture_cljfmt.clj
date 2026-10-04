;; Run inside the pinned cljfmt checkout. Its tests remain external to this repo.
(require '[cljfmt.core :as fmt]
         '[clojure.java.io :as io]
         '[clojure.string :as string]
         '[clojure.test :as test]
         '[clojure.walk :as walk]
         '[rewrite-clj.node :as node]
         '[rewrite-clj.parser :as parser])
(require 'cljfmt.core-test)

(def original-reformat fmt/reformat-string)
(def captured (atom []))
(def ^:dynamic *capturing* false)

(defn fragments [source options]
  (let [forms (remove #(#{:whitespace :newline :comment} (node/tag %))
                      (node/children (parser/parse-string-all source)))]
    (when (> (count forms) 1)
      (mapv (fn [form]
              (let [source (node/string form)]
                {:source source :expected (original-reformat source options)}))
            forms))))

;; Original regression inputs supplement the external suite, notably options
;; accepted by its public API but not exercised by its own formatting tests.
(def compatibility-cases
  (concat
   (for [value [true false nil 0 "" [] {} :enabled]]
     {:name (str "option-truthiness-" (pr-str value))
      :source "(defn compute []\n        (run))" :options {:indentation? value}})
   [{:name "unknown-option-keys"
     :source "(defn compute []\n(run))"
     :options {:future-feature? true :integration/settings {:enabled? nil} "editor" "test"}}
    {:name "nil-aliases-and-refers"
     :source "(custom/run first\nsecond)"
     :options {:alias-map nil :refer-map nil}}
    {:name "deprecated-binding-alignment-is-ignored"
     :source "(let [a 1\nlonger 2]\n(+ a longer))"
     :options {:align-binding-columns? true}}
    {:name "deprecated-binding-alignment-does-not-override-disabled-form-alignment"
     :source "(let [a 1\nlonger 2]\n(+ a longer))"
     :options {:align-binding-columns? true :align-form-columns? false}}
    {:name "deprecated-binding-alignment-does-not-disable-form-alignment"
     :source "(let [a 1\nlonger 2]\n(+ a longer))"
     :options {:align-binding-columns? false :align-form-columns? true}}]))

(let [[destination corpus] *command-line-args*
      result
      (with-redefs [fmt/reformat-string
                    (fn [& args]
                      (if *capturing*
                        (apply original-reformat args)
                        (binding [*capturing* true]
                          (let [output (apply original-reformat args)
                                [source options] args]
                            (swap! captured conj
                                   {:name (str (:name (meta (first test/*testing-vars*))))
                                    :source source :options (or options {})
                                    :expected output})
                            output))))]
        (test/run-tests 'cljfmt.core-test))]
  (when (pos? (+ (:fail result) (:error result)))
    (throw (ex-info "The pinned upstream test suite failed" result)))
  (let [cases (mapv #(assoc % :fragments (fragments (:source %) (:options %)))
                    (concat (distinct @captured)
                            (map #(assoc % :expected (original-reformat (:source %) (:options %)))
                                 compatibility-cases)))
        corpus-cases
        (when corpus
          (mapv (fn [index file]
                  (let [source (slurp file)
                        expected (io/file (.getParentFile (io/file destination))
                                          (str "corpus-" index ".lpy"))]
                    (try
                      (spit expected (original-reformat source))
                      {:name (str file) :source-file (str file) :options {}
                       :expected-file (str expected)}
                      (catch Exception ex
                        {:name (str file) :source-file (str file)
                         :oracle-error (.getMessage ex)}))))
                (range)
                (sort-by str (filter #(and (.isFile %)
                                           (string/ends-with? (.getName %) ".lpy"))
                                     (file-seq (io/file corpus))))))
        data (walk/postwalk
              #(if (instance? java.util.regex.Pattern %)
                 (tagged-literal 're (.pattern %)) %)
              {:cases cases :upstream-count (count (distinct @captured))
               :regression-count (count compatibility-cases)
               :corpus (or corpus-cases [])})]
    (spit destination
          (binding [*print-namespace-maps* false]
            (pr-str data)))
    (println "Captured" (count cases) "formatting cases and"
             (count corpus-cases) "corpus files.")))
