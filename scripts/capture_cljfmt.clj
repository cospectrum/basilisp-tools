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
                    (distinct @captured))
        corpus-cases
        (when corpus
          (mapv (fn [file]
                  (let [source (slurp file)]
                    (try {:name (str file) :source source :options {}
                          :expected (original-reformat source)}
                         (catch Exception ex
                           {:name (str file) :source source
                            :oracle-error (.getMessage ex)}))))
                (sort-by str (filter #(and (.isFile %)
                                           (string/ends-with? (.getName %) ".lpy"))
                                     (file-seq (io/file corpus))))))
        data (walk/postwalk
              #(if (instance? java.util.regex.Pattern %)
                 (tagged-literal 're (.pattern %)) %)
              {:cases cases :corpus (or corpus-cases [])})]
    (spit destination
          (binding [*print-namespace-maps* false]
            (pr-str data)))
    (println "Captured" (count cases) "upstream cases and"
             (count corpus-cases) "corpus files.")))
