{{/*
ProxySQL component name
*/}}
{{- define "mariadb-cluster.proxysql.name" -}}
{{ include "mariadb-cluster.fullname" . }}-proxysql
{{- end }}

{{/*
ProxySQL labels
*/}}
{{- define "mariadb-cluster.proxysql.labels" -}}
{{ include "mariadb-cluster.labels" . }}
app.kubernetes.io/component: proxysql
{{- end }}

{{/*
ProxySQL selector labels
*/}}
{{- define "mariadb-cluster.proxysql.selectorLabels" -}}
{{ include "mariadb-cluster.selectorLabels" . }}
app.kubernetes.io/component: proxysql
{{- end }}

{{/*
Get ProxySQL admin password
Requires explicit password or existingSecret
*/}}
{{- define "mariadb-cluster.proxysql.adminPassword" -}}
{{- if .Values.proxysql.admin.password -}}
{{- .Values.proxysql.admin.password -}}
{{- else -}}
{{- fail "ProxySQL admin password must be set via proxysql.admin.password" -}}
{{- end -}}
{{- end -}}

{{/*
Get ProxySQL cluster password
Requires explicit password or existingSecret
*/}}
{{- define "mariadb-cluster.proxysql.clusterPassword" -}}
{{- if .Values.proxysql.admin.clusterPassword -}}
{{- .Values.proxysql.admin.clusterPassword -}}
{{- else -}}
{{- fail "ProxySQL cluster password must be set via proxysql.admin.clusterPassword" -}}
{{- end -}}
{{- end -}}

{{/*
Get ProxySQL monitor password
Requires explicit password or existingSecret
*/}}
{{- define "mariadb-cluster.proxysql.monitorPassword" -}}
{{- if .Values.proxysql.monitor.password -}}
{{- .Values.proxysql.monitor.password -}}
{{- else -}}
{{- fail "ProxySQL monitor password must be set via proxysql.monitor.password" -}}
{{- end -}}
{{- end -}}

{{/*
Get ProxySQL user password from secretKeyRef or direct value
Context should be: dict "user" $user "root" $
*/}}
{{- define "mariadb-cluster.proxysql.userPassword" -}}
{{- $user := .user }}
{{- $root := .root }}
{{- if $user.passwordSecretKeyRef }}
{{- $secretName := tpl $user.passwordSecretKeyRef.name $root }}
{{- $secretKey := $user.passwordSecretKeyRef.key }}
{{- printf "%%{%s:%s}" $secretName $secretKey }}
{{- else if $user.password }}
{{- $user.password }}
{{- else }}
{{- fail (printf "User %s must have either passwordSecretKeyRef or password" $user.username) }}
{{- end }}
{{- end }}

{{/*
ProxySQL service name
*/}}
{{- define "mariadb-cluster.proxysql.serviceName" -}}
{{ include "mariadb-cluster.proxysql.name" . }}
{{- end }}

{{/*
ProxySQL headless service name
*/}}
{{- define "mariadb-cluster.proxysql.headlessServiceName" -}}
{{ include "mariadb-cluster.proxysql.name" . }}-cluster
{{- end }}

{{/*
ProxySQL config name
*/}}
{{- define "mariadb-cluster.proxysql.configName" -}}
{{ include "mariadb-cluster.proxysql.name" . }}-config
{{- end }}

{{/*
ProxySQL monitor secret name
*/}}
{{- define "mariadb-cluster.proxysql.monitorSecretName" -}}
{{- if .Values.proxysql.monitor.existingSecret -}}
{{- .Values.proxysql.monitor.existingSecret -}}
{{- else -}}
{{- include "mariadb-cluster.proxysql.name" . }}-monitor
{{- end -}}
{{- end -}}

{{/*
ProxySQL monitor user name
*/}}
{{- define "mariadb-cluster.proxysql.monitorUserName" -}}
{{ include "mariadb-cluster.proxysql.name" . }}-monitor
{{- end }}
