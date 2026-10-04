;; Exercise the external cljfmt plugin; no upstream implementation is vendored.
(require '[cljfmt.config :as config]
         '[cljfmt.tool :as tool]
         '[clojure.edn :as edn]
         '[clojure.java.io :as io]
         '[clojure.walk :as walk])

;; The plugin only needs these two Leiningen vars. Project evaluation and profile
;; activation are intentionally outside blt's literal configuration contract.
(create-ns 'leiningen.core.main)
(intern 'leiningen.core.main (with-meta '*info* {:dynamic true}) true)
(intern 'leiningen.core.main 'abort
        (fn [& messages] (throw (ex-info (apply str messages) {}))))
(dosync (alter (var-get #'clojure.core/*loaded-libs*)
               conj 'leiningen.core.main))
(load-file "lein-cljfmt/src/leiningen/cljfmt.clj")

(def load-config config/load-config)
(def plugin (ns-resolve 'leiningen.cljfmt 'cljfmt))

(defn absolute-path [root path]
  (str (.getCanonicalFile
        (let [file (io/file path)]
          (if (.isAbsolute file) file (io/file root path))))))

(defn project-map [root source]
  ;; Defproject automatically quotes symbols and sequences in its options.
  ;; These fixtures use only literal maps: the ordinary reader yields the same
  ;; values without requiring Leiningen or evaluating a project.clj.
  (let [form (binding [*read-eval* false] (read-string source))
        options (apply hash-map (drop 3 form))]
    (-> options
        (assoc :root root)
        (update :source-paths #(mapv (partial absolute-path root) (or % ["src"])))
        (update :test-paths #(mapv (partial absolute-path root) (or % ["test"]))))))

(defn capture [{:keys [name mode root path source keys read-clj-config-files?]}]
  (try
    (let [options
          (case mode
            :lein
            (with-redefs [config/load-config (fn
                                                ([] (load-config root {}))
                                                ([path] (load-config path {}))
                                                ([path options] (load-config path options)))
                          tool/check-no-config identity]
              (plugin (project-map root source) "check"))
            :file (load-config path)
            :discover (load-config path {:read-clj-config-files? read-clj-config-files?}))
          options (cond-> (select-keys options keys)
                    (and (= mode :lein) (contains? options :paths))
                    (assoc :paths (mapv (partial absolute-path root) (:paths options))))
          printable (walk/postwalk
                     #(if (instance? java.util.regex.Pattern %)
                        (tagged-literal 're (str %)) %)
                     options)]
      {:name name :options printable})
    (catch Exception exception
      {:name name :error (.getMessage exception)})))

(let [[input output] *command-line-args*
      cases (edn/read-string (slurp input))]
  (binding [*print-namespace-maps* false]
    (spit output (pr-str (mapv capture cases)))))
