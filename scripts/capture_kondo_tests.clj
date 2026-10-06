;; Read upstream tests as data; never evaluate their forms or source snippets.
(require '[clojure.java.io :as io]
         '[clojure.string :as str])

(defn read-forms [file]
  (with-open [reader (clojure.lang.LineNumberingPushbackReader. (io/reader file))]
    (binding [*read-eval* false]
      (loop [forms []]
        (let [form (read {:eof ::eof :read-cond :allow :features #{:clj}} reader)]
          (if (= ::eof form) forms (recur (conj forms form))))))))

(defn literal [form]
  (cond
    (and (seq? form) (= 'quote (first form))) (second form)
    (or (nil? form) (string? form) (number? form) (keyword? form)
        (boolean? form) (char? form) (instance? java.util.regex.Pattern form)) form
    (map? form) (into (empty form) (map (fn [[k v]] [(literal k) (literal v)])) form)
    (vector? form) (with-meta (mapv literal form) (meta form))
    (set? form) (with-meta (set (map literal form)) (meta form))
    :else (throw (ex-info "dynamic argument" {:form form}))))

(defn json [value]
  (cond
    (nil? value) "null"
    (or (true? value) (false? value) (number? value)) (str value)
    (string? value)
    (str "\"" (apply str (map #(case %
                                 \" "\\\"" \\ "\\\\" \newline "\\n" \return "\\r" \tab "\\t"
                                 (if (< (int %) 32) (format "\\u%04x" (int %)) (str %))) value)) "\"")
    (keyword? value) (json (name value))
    (map? value) (str "{" (str/join "," (map (fn [[k v]] (str (json k) ":" (json v))) value)) "}")
    (sequential? value) (str "[" (str/join "," (map json value)) "]")))

(let [[checkout output] *command-line-args*
      test-root (io/file checkout "test" "clj_kondo")
      utils (read-forms (io/file test-root "test_utils.clj"))
      base-config (->> utils
                       (filter #(and (seq? %) (= 'def (first %)) (= 'base-config (second %))))
                       first last literal)
      occurrences (atom {})
      cases (atom [])
      skipped (atom [])]
  (when-not (map? base-config)
    (throw (ex-info "Missing literal upstream base-config" {})))
  (doseq [file (sort (filter #(str/ends-with? (.getName %) "_test.clj") (file-seq test-root)))
          form (read-forms file)
          :when (and (seq? form) (symbol? (first form)) (= "deftest" (name (first form))))
          call (tree-seq coll? seq form)
          :when (and (seq? call) (symbol? (first call)) (= "lint!" (name (first call))))]
    (let [relative (str (.relativize (.toPath (io/file checkout)) (.toPath file)))
          info {:file relative :test (str (second form)) :line (:line (meta call))}
          location (str relative ":" (:line info) ":" (:column (meta call)))
          occurrence (get (swap! occurrences update location (fnil inc 0)) location)
          id (str location ":" occurrence)]
      (try
        (let [[source & arguments] (map literal (rest call))
              [config arguments] (if (map? (first arguments))
                                   [(first arguments) (rest arguments)] [{} arguments])
              options (apply hash-map arguments)]
          (cond
            (not (string? source)) (swap! skipped conj (assoc info :id id :reason "non-string source"))
            (or (contains? config :hooks) (str/includes? source ":hooks"))
            (swap! skipped conj (assoc info :id id :reason "executable hooks require upstream harness"))
            (or (contains? config :config-paths) (contains? config :config-dir))
            (swap! skipped conj (assoc info :id id :reason "external configuration fixtures"))
            (not-every? #{"--lang" "--filename"} (keys options))
            (swap! skipped conj (assoc info :id id :reason "external CLI options"))
            (not= "clj" (get options "--lang" "clj"))
            (swap! skipped conj (assoc info :id id :reason "ClojureScript or reader-conditional dialect"))
            (some #(str/ends-with? (get options "--filename" "upstream.clj") %)
                  [".cljs" ".cljc" ".edn"])
            (swap! skipped conj (assoc info :id id :reason "non-Clojure filename dialect"))
            :else (swap! cases conj
                         (assoc info :id id :source source
                                :config (binding [*print-meta* true] (pr-str config))
                                :filename (get options "--filename" "upstream.clj")))))
        (catch Exception _
          (swap! skipped conj (assoc info :id id :reason "dynamic source, config, or arguments"))))))
  (spit output (json {:base-config (binding [*print-meta* true] (pr-str base-config))
                     :cases @cases :skipped @skipped})))
