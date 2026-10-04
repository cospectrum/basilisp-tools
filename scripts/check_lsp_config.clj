;; Load selected functions from an external, pinned clojure-lsp checkout.
;; No upstream implementation or fixtures are copied into this repository.
(require '[clojure.edn :as edn]
         '[clojure.java.io :as io]
         '[clojure.set]
         '[clojure.string]
         '[clojure.walk :as walk]
         '[medley.core])

(defn source-forms [checkout filename]
  (with-open [reader (java.io.PushbackReader.
                      (io/reader (io/file checkout "lib/src/clojure_lsp" filename)))]
    (binding [*read-eval* false]
      (loop [forms []]
        (let [form (read {:eof ::eof} reader)]
          (if (= ::eof form) forms (recur (conj forms form))))))))

(defn load-functions [checkout filename namespace names aliases]
  (let [selected (filter #(and (seq? %) (#{'defn 'defn-} (first %))
                               (contains? names (second %)))
                         (source-forms checkout filename))]
    (when-not (= names (set (map second selected)))
      (throw (ex-info "Upstream configuration functions changed" {:file filename})))
    (binding [*ns* (or (find-ns namespace) (create-ns namespace))]
      (refer 'clojure.core)
      (import 'java.io.File)
      (doseq [[local target] aliases] (alias local target))
      (doseq [form selected] (eval form)))))

(defn install-oracle [checkout]
  ;; Logging and ordinary file URI decoding are the only project stubs. All
  ;; merge, configuration discovery, EDN reading and cleaning code is upstream.
  (create-ns 'clojure-lsp.logger)
  (intern 'clojure-lsp.logger 'error (fn [& _] nil))
  (create-ns 'clojure-lsp.shared)
  (intern 'clojure-lsp.shared 'uri->filename
          (fn [uri] (.getPath (io/file (java.net.URI. uri)))))
  (load-functions checkout "shared.clj" 'clojure-lsp.shared
                  '#{deep-merge file-exists? assoc-some keywordize-first-depth}
                  '{set clojure.set})
  (load-functions checkout "config.clj" 'clojure-lsp.config
                  '#{read-edn-file get-property get-env global-config-file
                     local-project-config-file resolve-global-config
                     resolve-project-configs resolve-for-root
                     deep-merge-considering-settings with-legacy-linters-kondo-config}
                  '{shared clojure-lsp.shared logger clojure-lsp.logger
                    edn clojure.edn io clojure.java.io string clojure.string
                    medley medley.core})
  (load-functions checkout "settings.clj" 'clojure-lsp.settings
                  '#{typify-json clean-symbol-map clean-keys-map parse-source-paths
                     kwd-string parse-source-aliases clean-client-settings}
                  '{config clojure-lsp.config shared clojure-lsp.shared
                    string clojure.string walk clojure.walk medley medley.core})
  ;; Exercise the actual startup merge expression, independently of unrelated
  ;; classpath scanning, Java indexing, logging, and server initialization.
  (let [expression (first
                     (filter #(and (seq? %)
                                    (= '(medley/deep-merge encoding-settings
                                                          client-settings project-settings
                                                          force-settings) %))
                             (tree-seq coll? seq
                                       (source-forms checkout "startup.clj"))))]
    (when-not expression
      (throw (ex-info "Upstream startup settings merge changed" {})))
    (binding [*ns* (the-ns 'clojure-lsp.config)]
      (intern *ns* 'startup-merge
              (eval (list 'fn '[encoding-settings client-settings project-settings force-settings]
                          expression))))))

(defn invoke [namespace function & args]
  (apply (ns-resolve namespace function) args))

(defn capture [{:keys [name mode a b client force root home xdg]}]
  (try
    {:name name
     :settings
     (case mode
       :deep-merge (invoke 'clojure-lsp.shared 'deep-merge a b)
       :merge (invoke 'clojure-lsp.config 'deep-merge-considering-settings a b)
       :clean (invoke 'clojure-lsp.settings 'clean-client-settings client)
       :load
       (with-redefs-fn
         {(ns-resolve 'clojure-lsp.config 'get-property)
          (fn [key] (when (= key "user.home") home))
          (ns-resolve 'clojure-lsp.config 'get-env)
          (fn [key] (when (= key "XDG_CONFIG_HOME") xdg))}
         #(invoke 'clojure-lsp.config 'startup-merge {}
                  (invoke 'clojure-lsp.settings 'clean-client-settings client)
                  (invoke 'clojure-lsp.config 'resolve-for-root
                          (str (.toURI (io/file root))))
                  force)))}
    (catch Exception exception {:name name :error (.getMessage exception)})))

(let [[checkout input output] *command-line-args*]
  (install-oracle checkout)
  (let [cases (edn/read-string {:readers {'re re-pattern}} (slurp input))
        results (mapv capture cases)
        printable (walk/postwalk #(if (instance? java.util.regex.Pattern %)
                                    (tagged-literal 're (str %)) %) results)]
    (binding [*print-namespace-maps* false]
      (spit output (pr-str printable)))))
