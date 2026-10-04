;; The installed Leiningen jar is pinned by the development shell's Nix lock.
;; Read literal test data, then call its actual profile machinery. No project
;; files are evaluated, and no plugins or dependencies are loaded.
(require '[leiningen.core.project :as project])

(let [[input output] *command-line-args*
      cases (binding [*read-eval* false] (read-string (slurp input)))
      results
      (mapv (fn [{:keys [name source root profiles local]}]
              (try
                (let [raw (binding [*read-eval* false] (read-string source))
                      prepared (project/make raw 'oracle "0" root)
                      definitions (merge @project/default-profiles
                                         (:profiles prepared)
                                         (when local
                                           (#'project/setup-map-of-profiles
                                            (binding [*read-eval* false]
                                              (read-string local)))))
                      value (-> prepared
                                (project/project-with-profiles definitions)
                                (project/init-profiles
                                 (or profiles [:default])))]
                  {:name name :cljfmt (:cljfmt value {})
                   :source-paths (:source-paths value)
                   :test-paths (:test-paths value)})
                (catch Exception ex
                  {:name name :error (.getMessage ex)})))
            cases)]
  (spit output
        (binding [*print-namespace-maps* false] (pr-str results))))
