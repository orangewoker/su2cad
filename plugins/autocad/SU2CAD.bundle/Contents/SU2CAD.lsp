(vl-load-com)

(defun su2cad-safe-json (value)
  (vl-string-translate "\"" "'" (if value value ""))
)

(defun su2cad-status-directory ()
  (strcat (getenv "APPDATA") "\\SU2CAD")
)

(defun su2cad-write-status (/ directory path stream product version locale)
  (setq directory (su2cad-status-directory))
  (if (not (vl-file-directory-p directory)) (vl-mkdir directory))
  (setq path (strcat directory "\\cad_plugin_status.json"))
  (setq product (su2cad-safe-json (getvar "PRODUCT")))
  (setq version (su2cad-safe-json (getvar "ACADVER")))
  (setq locale (su2cad-safe-json (getvar "LOCALE")))
  (setq stream (open path "w"))
  (if stream
    (progn
      (write-line
        (strcat "{\"ok\":true,\"pluginVersion\":\"0.8.3\",\"product\":\"" product
                "\",\"acadVersion\":\"" version "\",\"locale\":\"" locale "\"}")
        stream)
      (close stream)
    )
  )
  path
)

(defun c:SU2CADFIXDIALOGS ()
  (setvar "FILEDIA" 1)
  (setvar "CMDDIA" 1)
  (su2cad-write-status)
  (princ "\nSU2CAD: FILEDIA and CMDDIA restored to 1.")
  (princ)
)

(defun c:SU2CADSTATUS ()
  (setq path (su2cad-write-status))
  (princ (strcat "\nSU2CAD helper ready: " (getvar "PRODUCT") " " (getvar "ACADVER")))
  (princ)
)

(defun c:SU2CADOPEN ()
  (c:SU2CADFIXDIALOGS)
  (initdia)
  (command "_.OPEN")
  (princ)
)

(setvar "FILEDIA" 1)
(setvar "CMDDIA" 1)
(su2cad-write-status)
(princ "\nSU2CAD AutoCAD Helper 0.8.3 loaded. Commands: SU2CADSTATUS, SU2CADFIXDIALOGS, SU2CADOPEN.")
(princ)
