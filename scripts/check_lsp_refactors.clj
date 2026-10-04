;; Load the refactor implementation from an external pinned checkout.
;; No upstream implementation or fixtures are copied into this repository.
(require '[clojure.edn :as edn]
         '[clojure.java.io :as io]
         '[clojure.set]
         '[clojure.string]
         '[rewrite-clj.node]
         '[rewrite-clj.zip :as z])

(defn source-forms [checkout filename stop]
  (with-open [reader (java.io.PushbackReader.
                      (io/reader (io/file checkout "lib/src/clojure_lsp" filename)))]
    (binding [*read-eval* false]
      (loop [forms []]
        (let [form (read {:eof ::eof} reader)]
          (cond
            (= ::eof form) forms
            (and (seq? form) (= stop (second form))) (conj forms form)
            :else (recur (conj forms form))))))))

(defn load-forms [checkout filename namespace names aliases refer-fast? stop]
  (let [selected (filter #(and (seq? %) (#{'def 'defn 'defn- 'defmacro} (first %))
                               (or (nil? names) (contains? names (second %))))
                         (source-forms checkout filename stop))]
    (when (and names (not= names (set (map second selected))))
      (throw (ex-info "Upstream refactor functions changed" {:file filename})))
    (binding [*ns* (or (find-ns namespace) (create-ns namespace))]
      (refer 'clojure.core)
      (when refer-fast? (refer 'clojure-lsp.shared :only '[fast=]))
      (doseq [[local target] aliases] (alias local target))
      (doseq [form selected] (eval form)))))

(defn install-oracle [checkout]
  (load-forms checkout "shared.clj" 'clojure-lsp.shared '#{fast=} {} false nil)
  ;; Refactor tests provide a fixed settings snapshot. Configuration refresh and
  ;; merging are independently exercised by check_lsp_config.py; the only stub
  ;; here is settings/all. The actual upstream settings/get is loaded below.
  (create-ns 'clojure-lsp.settings)
  (intern 'clojure-lsp.settings 'all :settings)
  (load-forms checkout "settings.clj" 'clojure-lsp.settings '#{get} {} false nil)
  (load-forms checkout "refactor/edit.clj" 'clojure-lsp.refactor.edit nil
              '{set clojure.set n rewrite-clj.node z rewrite-clj.zip} true nil)
  ;; The prefix contains the entire collection/threading/unwinding implementation.
  ;; Later functions depend on unrelated server, classpath, and analyzer services.
  (load-forms checkout "refactor/transform.clj" 'clojure-lsp.refactor.transform nil
              '{edit clojure-lsp.refactor.edit settings clojure-lsp.settings
                set clojure.set string clojure.string n rewrite-clj.node z rewrite-clj.zip}
              true 'unwind-all)
  (load-forms checkout "feature/thread_get.clj" 'clojure-lsp.feature.thread-get nil
              '{edit clojure-lsp.refactor.edit n rewrite-clj.node z rewrite-clj.zip} true nil))

(defn position [source offset]
  (let [before (subs source 0 offset)
        lines (clojure.string/split before #"\n" -1)]
    [(count lines) (inc (count (last lines)))]))

(defn offset-at [source row col]
  (+ (reduce + (map #(inc (count %))
                    (take (dec row) (clojure.string/split source #"\n" -1))))
     (dec col)))

(defn apply-edits [source edits]
  (reduce (fn [text {:keys [range loc]}]
            (let [{:keys [row col end-row end-col]} range
                  start (offset-at source row col)
                  end (offset-at source end-row end-col)]
              (str (subs text 0 start) (when loc (z/string loc)) (subs text end))))
          source
          (sort-by #(let [{:keys [row col]} (:range %)] [(- row) (- col)]) edits)))

(defn capture [{:keys [name source command offset args settings]}]
  (try
    (let [[row col] (position source offset)
          loc ((ns-resolve 'clojure-lsp.refactor.edit 'find-at-pos) (z/of-string source) row col)
          namespace (if (clojure.string/starts-with? command "get-in-")
                      'clojure-lsp.feature.thread-get 'clojure-lsp.refactor.transform)
          function (ns-resolve namespace (symbol command))
          edits (cond
                  (#{"thread-first" "thread-last" "thread-first-all" "thread-last-all"} command)
                  (function loc {:settings settings})
                  (= "change-coll" command) (function loc (first args))
                  :else (function loc))]
      {:name name :source (apply-edits source edits)})
    (catch Exception exception {:name name :error (.getMessage exception)})))

(let [[checkout input output] *command-line-args*]
  (install-oracle checkout)
  (let [cases (edn/read-string (slurp input))]
    (binding [*print-namespace-maps* false]
      (spit output (pr-str (mapv capture cases))))))
